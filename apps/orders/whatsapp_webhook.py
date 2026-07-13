import json
import logging
import re
from collections import defaultdict
from datetime import datetime, time as dtime
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

from django.conf import settings
from apps.orders.whatsapp import (
    send_whatsapp_message,
    save_supplier_pending_order,
    notify_suppliers_for_order,
    DecimalEncoder,
)

logger = logging.getLogger(__name__)

SESSION_TTL = 3600
CUTOFF_TTL = 86400
FALLBACK_TTL = 3600


def _normalize_phone(phone: str) -> str:
    """Ensure phone is in +XXXXXXXXXXX format."""
    if phone.startswith("972") and not phone.startswith("+"):
        return "+" + phone
    return phone


def _validate_twilio_signature(request) -> bool:
    """Return True if the request came from Twilio (or DEBUG is on)."""
    if settings.DEBUG:
        return True
    try:
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
        signature = request.META.get("HTTP_X_TWILIO_SIGNATURE", "")
        url = request.build_absolute_uri()
        return validator.validate(url, request.POST, signature)
    except Exception as exc:
        logger.error("Twilio signature validation error: %s", exc)
        return False


# ─────────────────────── User flow helpers ───────────────────────

def save_pending_order(phone: str, cheapest: dict, fewest: dict,
                       products: list = None,
                       user_id: int = None,
                       region: str = None,
                       minimum_issues: dict = None):
    """
    Cache the suggested order options for a user.
    `products` (list of {product_id, quantity}), `user_id`, and `region`
    are optional but required for actually building the order on confirmation.
    """
    key = f"whatsapp_order:{phone}"
    payload = {"cheapest": cheapest, "fewest": fewest}
    if products is not None:
        payload["products"] = products
    if user_id is not None:
        payload["user_id"] = user_id
    if region is not None:
        payload["region"] = region
    if minimum_issues is not None:
        payload["minimum_issues"] = minimum_issues
    cache.set(key, json.dumps(payload, cls=DecimalEncoder), timeout=SESSION_TTL)


def _format_scenario(label, s):
    lines = [f"*{label}*"]
    for p in s["products"]:
        lines.append(
            f"  • {p['product_name']} x{p['quantity']} {p.get('unit', '')} "
            f"— {p['supplier_name']} — {p['subtotal']}₪"
        )
    lines.append(f'סה"כ: {s["total_price"]}₪')
    return "\n".join(lines)


def _build_and_send_confirmed_order(data: dict, scenario: str) -> bool:
    """Build order in DB and send WhatsApp to each supplier. Returns True on success."""
    from django.contrib.auth import get_user_model
    from apps.catalog.models import Product
    from apps.orders.services import build_order

    user_id = data.get("user_id")
    raw_products = data.get("products")
    region = data.get("region")

    if not user_id or not raw_products or not region:
        return False

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
        products = [
            {
                "product": Product.objects.get(id=p["product_id"]),
                "quantity": Decimal(p["quantity"]),
            }
            for p in raw_products
        ]
        order, _ = build_order(user, region, products, scenario=scenario)
        notify_suppliers_for_order(order)
        return True
    except Exception as exc:
        logger.error("Failed to build/send confirmed order for user %s: %s", user_id, exc)
        return False


def _handle_new_order(phone: str, body: str) -> HttpResponse:
    """Handle an incoming message that has no pending order — treat as a new customer order."""
    from apps.catalog.models import Product
    from apps.users.models import Profile
    from apps.orders.services import suggest_order
    from apps.orders.order_parser import parse_customer_order

    # Look up user by phone; try +972XXXXXXXXX and 0XXXXXXXXX
    profile = Profile.objects.filter(phone=phone).select_related("user").first()
    if not profile and phone.startswith("+972"):
        profile = Profile.objects.filter(phone="0" + phone[4:]).select_related("user").first()

    if not profile:
        send_whatsapp_message(phone, "מספר הטלפון שלך לא רשום במערכת. פנה למנהל.")
        return HttpResponse(status=200)

    user = profile.user
    all_products = list(Product.objects.all())
    product_names = [p.name for p in all_products]

    try:
        parsed_items = parse_customer_order(body, product_names)
    except ValueError:
        send_whatsapp_message(
            phone,
            "לא הצלחתי להבין את ההזמנה.\nנסה לשלוח כגון: 5 קילו עגבניות, 10 קילו גזר",
        )
        return HttpResponse(status=200)

    all_products_map = {p.name: p for p in all_products}
    products = []
    unrecognized = []
    for item in parsed_items:
        product = all_products_map.get(item["product_name"])
        if product:
            products.append({"product": product, "quantity": item["quantity"]})
        else:
            unrecognized.append(item["product_name"])

    if not products:
        send_whatsapp_message(
            phone,
            f"לא זיהיתי מוצרים ידועים בהזמנה.\nלא זוהה: {', '.join(unrecognized)}",
        )
        return HttpResponse(status=200)

    try:
        result = suggest_order(user=user, region=profile.region, products=products)
    except ValueError as exc:
        send_whatsapp_message(phone, f"שגיאה בעיבוד ההזמנה: {exc}")
        return HttpResponse(status=200)

    cheapest = result["cheapest"]
    fewest = result["fewest_suppliers"]
    minimum_issues = result.get("minimum_issues", {})

    save_pending_order(
        phone,
        cheapest,
        fewest,
        products=[{"product_id": p["product"].id, "quantity": str(p["quantity"])} for p in products],
        user_id=user.id,
        region=profile.region,
        minimum_issues=minimum_issues,
    )

    same = cheapest["total_price"] == fewest["total_price"]
    if same:
        msg = _format_scenario("ההזמנה שלך", cheapest)
        cheapest_issues = minimum_issues.get("cheapest", [])
        if cheapest_issues:
            msg += "\n\n" + _format_minimum_warning(cheapest_issues)
            msg += "\n\nשלח הזמנה מחודשת עם כמויות גדולות יותר."
        else:
            msg += "\n\nענה *אישור* לאישור."
    else:
        cheapest_issues = minimum_issues.get("cheapest", [])
        fewest_issues = minimum_issues.get("fewest_suppliers", [])
        cheapest_label = "אפשרות א׳ — הזול ביותר"
        fewest_label = "אפשרות ב׳ — הכי פחות ספקים"
        if cheapest_issues:
            cheapest_label += " ⚠️"
        if fewest_issues:
            fewest_label += " ⚠️"
        msg = (
            _format_scenario(cheapest_label, cheapest)
            + "\n\n"
            + _format_scenario(fewest_label, fewest)
            + "\n\nענה *א* לאפשרות הזולה יותר, *ב* לאפשרות עם פחות ספקים."
        )
        if cheapest_issues or fewest_issues:
            msg += "\n\n⚠️ — אפשרות זו אינה עומדת במינימום הזמנה של ספק"

    if unrecognized:
        msg += f"\n\n⚠️ לא זוהה: {', '.join(unrecognized)}"

    send_whatsapp_message(phone, msg)
    return HttpResponse(status=200)


def _format_minimum_warning(issues: list) -> str:
    lines = ["⛔ מינימום הזמנה לא עומד:"]
    for issue in issues:
        lines.append(
            f"  • {issue['supplier_name']}: נדרש ₪{issue['minimum_required']}, "
            f"חסר ₪{Decimal(str(issue['missing_amount'])):.2f}"
        )
    return "\n".join(lines)


def _parse_supplier_cutoff(body: str):
    """
    Detect cutoff time from supplier message, e.g. 'ניתן לשנות עד 10:00'.
    Returns a time object or None.
    """
    pattern = re.compile(
        r"(?:ניתן|אפשר|בסדר|עד)\s+(?:לשנות\s+)?עד\s+(\d{1,2}:\d{2})",
        re.IGNORECASE,
    )
    m = pattern.search(body)
    if not m:
        return None
    try:
        h, mi = m.group(1).split(":")
        return dtime(int(h), int(mi))
    except (ValueError, AttributeError):
        return None


def _handle_order_modification(phone: str, body: str, user, order) -> HttpResponse:
    """Handle ADD or UPDATE modification to a SENT order."""
    from apps.catalog.models import Product, SupplierProduct
    from apps.orders.models import OrderRequestProduct
    from apps.orders.order_parser import parse_modification_intent

    product_names = list(Product.objects.values_list("name", flat=True))
    parsed = parse_modification_intent(body, product_names)
    intent = parsed["intent"]
    items = parsed["items"]

    if intent == "none" or not items:
        return _handle_new_order(phone, body)

    profile = getattr(user, "profile", None)
    region = profile.region if profile else "center"

    changes_made = []
    errors = []

    for item in items:
        product = Product.objects.filter(name=item["product_name"]).first()
        if not product:
            errors.append(item["product_name"])
            continue

        # Check cutoff for the supplier handling this product
        existing_orp = OrderRequestProduct.objects.filter(
            order_request=order, product=product
        ).select_related("supplier").first()

        if existing_orp:
            cutoff_key = f"supplier_cutoff:{existing_orp.supplier.whatsapp_number}:{order.id}"
            cutoff = cache.get(cutoff_key)
            if cutoff:
                now = timezone.localtime().time()
                cutoff_time = dtime(*map(int, cutoff.split(":")))
                if now > cutoff_time:
                    send_whatsapp_message(
                        phone,
                        f"⛔ לא ניתן לשנות את {product.name} — "
                        f"{existing_orp.supplier.name} קבע שעת הגבלה עד {cutoff}.",
                    )
                    continue

        if intent == "update" and existing_orp:
            old_qty = existing_orp.quantity
            existing_orp.quantity = item["quantity"]
            existing_orp.save(update_fields=["quantity"])
            order.total_price = sum(
                p.quantity * p.unit_price
                for p in order.products.all()
            )
            order.save(update_fields=["total_price"])

            msg_lines = [f"📝 *{user.profile.company_name if profile else ''}* עדכן הזמנה:"]
            msg_lines.append(
                f"- {product.name}: {old_qty} → {item['quantity']} {product.get_unit_display()}"
            )
            msg_lines.append("\nאנא ענה *אישור* לאישור השינוי.")
            send_whatsapp_message(existing_orp.supplier.whatsapp_number, "\n".join(msg_lines))
            changes_made.append(
                f"עודכן: {product.name} {old_qty}→{item['quantity']} {product.get_unit_display()}"
            )

        elif intent == "add":
            from django.db.models import Q as _Q
            sp = (
                SupplierProduct.objects
                .filter(product=product, supplier__region=region)
                .filter(_Q(supplier__owner__isnull=True) | _Q(supplier__owner=user))
                .select_related("supplier")
                .order_by("price_per_unit")
                .first()
            )
            if not sp:
                errors.append(product.name)
                continue

            orp, created = OrderRequestProduct.objects.get_or_create(
                order_request=order,
                product=product,
                supplier=sp.supplier,
                defaults={"quantity": item["quantity"], "unit_price": sp.price_per_unit},
            )
            if not created:
                orp.quantity += item["quantity"]
                orp.save(update_fields=["quantity"])

            order.total_price = sum(
                p.quantity * p.unit_price
                for p in order.products.all()
            )
            order.save(update_fields=["total_price"])

            company = profile.company_name if profile else ""
            address = profile.company_address if profile else ""
            msg_lines = [f"📝 *{company}* הוסיף להזמנה:"]
            msg_lines.append(f"- {product.name} x{item['quantity']} {product.get_unit_display()}")
            if address:
                msg_lines.append(f"📍 {address}")
            msg_lines.append("\nאנא ענה *אישור* לאישור השינוי.")
            send_whatsapp_message(sp.supplier.whatsapp_number, "\n".join(msg_lines))
            changes_made.append(
                f"נוסף: {product.name} x{item['quantity']} {product.get_unit_display()}"
            )

    if not changes_made and not errors:
        send_whatsapp_message(phone, "לא הצלחתי לזהות שינוי בהזמנה. נסה שוב.")
        return HttpResponse(status=200)

    reply_lines = []
    if changes_made:
        reply_lines.append(f"✅ השינויים נשלחו לספקים:")
        reply_lines += [f"  • {c}" for c in changes_made]
    if errors:
        reply_lines.append(f"⚠️ לא נמצאו: {', '.join(errors)}")

    send_whatsapp_message(phone, "\n".join(reply_lines))
    return HttpResponse(status=200)


def _handle_user_flow(phone: str, body: str) -> HttpResponse:
    from apps.users.models import Profile
    from apps.orders.models import OrderRequest

    delivery_response = _handle_delivery_flow(phone, body)
    if delivery_response is not None:
        return delivery_response

    fallback_response = _handle_fallback_approval(phone, body)
    if fallback_response is not None:
        return fallback_response

    key = f"whatsapp_order:{phone}"
    raw = cache.get(key)

    if not raw:
        # Check if user has a SENT order (awaiting supplier confirmation) → offer modification
        profile = Profile.objects.filter(phone=phone).select_related("user").first()
        if not profile and phone.startswith("+972"):
            profile = Profile.objects.filter(phone="0" + phone[4:]).select_related("user").first()

        if profile:
            sent_order = (
                OrderRequest.objects
                .filter(user=profile.user, status=OrderRequest.Status.SENT)
                .order_by("-created_at")
                .first()
            )
            if sent_order:
                return _handle_order_modification(phone, body, profile.user, sent_order)

        return _handle_new_order(phone, body)

    data = json.loads(raw)
    cheapest = data["cheapest"]
    fewest = data["fewest"]

    same = cheapest["total_price"] == fewest["total_price"]

    if same or body in ("א", "1"):
        chosen = cheapest
        label = "אפשרות א׳ — הזול ביותר" if not same else "ההזמנה"
        scenario = "cheapest"
    elif body in ("ב", "2"):
        chosen = fewest
        label = "אפשרות ב׳ — הכי פחות ספקים"
        scenario = "fewest_suppliers"
    else:
        send_whatsapp_message(phone, "אנא ענה *א* או *ב* כדי לבחור.")
        return HttpResponse(status=200)

    minimum_issues = data.get("minimum_issues", {})
    scenario_issues = minimum_issues.get(scenario, [])
    if scenario_issues:
        msg = _format_minimum_warning(scenario_issues)
        msg += "\n\nשלח הזמנה מחודשת עם כמויות גדולות יותר כדי לעמוד במינימום."
        send_whatsapp_message(phone, msg)
        return HttpResponse(status=200)

    cache.delete(key)
    success = _build_and_send_confirmed_order(data, scenario)

    if success:
        confirm = _format_scenario(f"✅ אושר! {label}", chosen)
        confirm += "\n\nההזמנה נשלחה לספקים."
    else:
        confirm = "❌ אירעה שגיאה בעיבוד ההזמנה. אנא נסה שנית או פנה לתמיכה."
    send_whatsapp_message(phone, confirm)

    return HttpResponse(status=200)


# ─────────────────────── Delivery confirmation helpers ───────────────────────

DELIVERY_TTL = 86400  # 24 שעות


def _get_delivery_state(phone: str):
    return cache.get(f"whatsapp_delivery:{phone}")


def _save_delivery_state(phone: str, state: dict):
    cache.set(f"whatsapp_delivery:{phone}", json.dumps(state, cls=DecimalEncoder), timeout=DELIVERY_TTL)


def _clear_delivery_state(phone: str):
    cache.delete(f"whatsapp_delivery:{phone}")


def _handle_delivery_flow(phone: str, body: str) -> HttpResponse | None:
    """
    Handles delivery confirmation per supplier.
    Returns an HttpResponse if handled, None if the message is unrelated.
    """
    from apps.orders.models import OrderRequest

    ARRIVAL_WORDS = ["הגיע", "הגיעה", "נמסר", "נמסרה", "arrived", "received"]
    ALL_WORDS = ["הכל", "כולם", "הכל הגיע", "הכל נמסר", "all", "כן"]

    raw = _get_delivery_state(phone)

    # Start delivery flow
    if raw is None:
        if not any(w in body for w in ARRIVAL_WORDS):
            return None

        from apps.users.models import Profile
        profile = Profile.objects.filter(phone=phone).select_related("user").first()
        if not profile and phone.startswith("+972"):
            profile = Profile.objects.filter(phone="0" + phone[4:]).select_related("user").first()
        if not profile:
            return None

        order = (
            OrderRequest.objects
            .filter(user=profile.user, status=OrderRequest.Status.SENT)
            .order_by("-created_at")
            .first()
        )
        if not order:
            send_whatsapp_message(phone, "לא נמצאה הזמנה פתוחה שממתינה לאישור מסירה.")
            return HttpResponse(status=200)

        suppliers = list(
            order.products.select_related("supplier")
            .values("supplier__id", "supplier__name")
            .distinct()
        )
        supplier_list = [
            {"id": s["supplier__id"], "name": s["supplier__name"], "delivered": False}
            for s in suppliers
        ]

        if len(supplier_list) == 1:
            order.status = OrderRequest.Status.DELIVERED
            order.save(update_fields=["status"])
            send_whatsapp_message(
                phone,
                f"✅ הזמנה #{order.id} מ-{supplier_list[0]['name']} אושרה כנמסרה. תודה!"
            )
            return HttpResponse(status=200)

        _save_delivery_state(phone, {"order_id": order.id, "suppliers": supplier_list})
        lines = [f"מה הגיע מהזמנה #{order.id}? ענה עם מספר:"]
        for i, s in enumerate(supplier_list, 1):
            lines.append(f"{i}. {s['name']}")
        lines.append('\nאו ענה *הכל* אם כל הספקים הגיעו.')
        send_whatsapp_message(phone, "\n".join(lines))
        return HttpResponse(status=200)

    # Continue delivery flow
    state = json.loads(raw)
    order_id = state["order_id"]
    suppliers = state["suppliers"]

    if any(w in body for w in ALL_WORDS):
        for s in suppliers:
            s["delivered"] = True
    else:
        nums = re.findall(r"\d+", body)
        matched = False
        for n in nums:
            idx = int(n) - 1
            if 0 <= idx < len(suppliers):
                suppliers[idx]["delivered"] = True
                matched = True
        if not matched:
            lines = ["לא הבנתי. ענה עם מספר הספק:"]
            for i, s in enumerate(suppliers, 1):
                status = "✅" if s["delivered"] else "⏳"
                lines.append(f"{i}. {s['name']} {status}")
            lines.append('\nאו ענה *הכל* לאישור כולם.')
            send_whatsapp_message(phone, "\n".join(lines))
            return HttpResponse(status=200)

    pending = [s for s in suppliers if not s["delivered"]]
    delivered = [s for s in suppliers if s["delivered"]]

    reply_lines = ["✅ אושר:"]
    for s in delivered:
        reply_lines.append(f"  • {s['name']}")

    if pending:
        _save_delivery_state(phone, {"order_id": order_id, "suppliers": suppliers})
        reply_lines.append("\n⏳ עדיין ממתין:")
        for i, s in enumerate(suppliers, 1):
            if not s["delivered"]:
                reply_lines.append(f"  {i}. {s['name']}")
        reply_lines.append('\nענה מספר ספק שהגיע, או *הכל*.')
    else:
        _clear_delivery_state(phone)
        try:
            order = OrderRequest.objects.get(id=order_id)
            order.status = OrderRequest.Status.DELIVERED
            order.save(update_fields=["status"])
        except OrderRequest.DoesNotExist:
            pass
        reply_lines.append(f"\n✅ כל הזמנה #{order_id} אושרה כנמסרה. תודה!")

    send_whatsapp_message(phone, "\n".join(reply_lines))
    return HttpResponse(status=200)


# ─────────────────────── Fallback state helpers ───────────────────────

def _save_fallback_state(phone: str, state: dict):
    cache.set(f"whatsapp_fallback:{phone}", json.dumps(state, cls=DecimalEncoder), timeout=FALLBACK_TTL)
    try:
        from apps.orders.tasks import handle_fallback_timeout
        handle_fallback_timeout.apply_async(
            args=[phone, state.get("order_request_id")],
            countdown=FALLBACK_TTL,
        )
    except Exception as exc:
        logger.warning("Could not schedule fallback timeout task: %s", exc)


def _get_fallback_state(phone: str):
    return cache.get(f"whatsapp_fallback:{phone}")


def _clear_fallback_state(phone: str):
    cache.delete(f"whatsapp_fallback:{phone}")


def _recalculate_order_total(order_request_id: int):
    """Recalculate and persist order.total_price from its remaining ORPs."""
    from apps.orders.models import OrderRequest, OrderRequestProduct
    try:
        total = sum(
            (orp.quantity * orp.unit_price
             for orp in OrderRequestProduct.objects.filter(order_request_id=order_request_id)),
            Decimal(0),
        )
        OrderRequest.objects.filter(id=order_request_id).update(total_price=total)
    except Exception as exc:
        logger.error("_recalculate_order_total(%s): %s", order_request_id, exc)


# ─────────────────────── Supplier reply parsing ───────────────────────

MISSING_KEYWORDS = ["חסר", "אין", "נגמר", "אזל", "לא קיים"]


def _parse_supplier_reply(body: str, products: list) -> tuple[dict, list]:
    """
    Returns (confirmed: {orp_id: Decimal}, missing: [product_dict]).

    Handles:
    - "אישור" → all products confirmed
    - "חסר עגבניות, שאר אישור" → tomatoes missing, rest confirmed
    - "עגבניות 40, מלפפון 25" → explicit quantities
    - "עגבניות 40, מלפפון חסר" → mixed
    """
    body_lower = body.strip().lower()

    # Step 1: detect which products the supplier flagged as missing
    # Extract the words that DIRECTLY follow each missing keyword (e.g. "חסר עגבניות" → "עגבניות")
    # Using Hebrew Unicode range so we don't over-match across commas or conjunctions.
    _HEB = r"[֐-׿]+"
    missing_mentions: set[str] = set()
    for kw in MISSING_KEYWORDS:
        for m in re.finditer(rf"{re.escape(kw)}\s+({_HEB}(?:\s+{_HEB})?)", body_lower):
            missing_mentions.add(m.group(1).strip())
        for m in re.finditer(rf"({_HEB})\s+{re.escape(kw)}", body_lower):
            missing_mentions.add(m.group(1).strip())

    missing = []
    remaining = []
    for p in products:
        name_lower = p["product_name"].lower()
        # Prefix to handle Hebrew pluralization: עגבניה → עגבני (matches עגבניות)
        match_name = name_lower[:-1] if len(name_lower) > 4 else name_lower
        is_missing = any(
            mention.startswith(match_name) or match_name in mention
            for mention in missing_mentions
        )
        if is_missing:
            missing.append(p)
        else:
            remaining.append(p)

    # Step 2: parse confirmed quantities for non-missing products
    confirm_words = ["אישור", "אוקי", "כן", "ok", "yes", "בסדר", "מאושר", "✅", "👍", "שאר", "הכל"]
    has_general_confirm = any(w in body_lower for w in confirm_words)

    confirmed = {}
    if has_general_confirm:
        for p in remaining:
            confirmed[p["orp_id"]] = Decimal(str(p["quantity"]))
    else:
        for p in remaining:
            pattern = re.compile(re.escape(p["product_name"]) + r"[:\s]+(\d+(?:\.\d+)?)", re.IGNORECASE)
            m = pattern.search(body)
            if m:
                try:
                    confirmed[p["orp_id"]] = Decimal(m.group(1))
                except InvalidOperation:
                    pass

        if not confirmed and len(remaining) == 1:
            numbers = re.findall(r"\d+(?:\.\d+)?", body)
            if numbers:
                try:
                    confirmed[remaining[0]["orp_id"]] = Decimal(numbers[0])
                except InvalidOperation:
                    pass

    return confirmed, missing


def _handle_supplier_price_update(phone: str, supplier, body: str) -> HttpResponse:
    """Supplier sent a price list (no pending order). Parse and update DB."""
    from apps.catalog.price_parser import update_prices_from_message

    try:
        result = update_prices_from_message(supplier, body)
    except ValueError as exc:
        send_whatsapp_message(phone, f"שגיאה בעיבוד המחירים: {exc}")
        return HttpResponse(status=200)

    updated = result["updated"]
    skipped = result["skipped"]

    if not updated and not skipped:
        send_whatsapp_message(phone, "לא זיהיתי מחירים בהודעה. נסה לשלוח כגון:\nעגבניות 3.50, מלפפון 2.00")
        return HttpResponse(status=200)

    existing = [u for u in updated if not u.get("is_new")]
    new_products = [u for u in updated if u.get("is_new")]

    lines = []
    if existing:
        lines.append("✅ מחירים עודכנו:")
        for u in existing:
            unit = u.get("unit", 'ק"ג')
            lines.append(f"  • {u['product_name']}: {u['price']}₪/{unit}")

    if new_products:
        lines.append("\n🆕 מוצרים חדשים נוספו לקטלוג:")
        for u in new_products:
            unit = u.get("unit", 'ק"ג')
            lines.append(f"  • {u['product_name']}: {u['price']}₪/{unit}")

    if skipped:
        lines.append("\n⚠️ לא זוהה:")
        for s in skipped:
            lines.append(f"  • {s['product_name']} — {s['reason']}")

    total = len(existing) + len(new_products)
    parts = []
    if existing:
        parts.append(f"{len(existing)} עודכנו")
    if new_products:
        parts.append(f"{len(new_products)} חדשים נוספו")
    if skipped:
        parts.append(f"{len(skipped)} לא זוהו")
    lines.append(f"\nסה\"כ: {', '.join(parts)} ({total} מוצרים)")

    send_whatsapp_message(phone, "\n".join(lines))
    return HttpResponse(status=200)


def _handle_supplier_flow(phone: str, supplier, body: str) -> HttpResponse:
    from apps.orders.models import OrderRequestProduct, SupplierConfirmation

    processing_key = f"whatsapp_supplier_processing:{phone}"
    if not cache.add(processing_key, 1, timeout=30):
        return HttpResponse(status=200)

    try:
        return _handle_supplier_flow_inner(phone, supplier, body)
    finally:
        cache.delete(processing_key)


def _handle_supplier_flow_inner(phone: str, supplier, body: str) -> HttpResponse:
    from apps.orders.models import OrderRequestProduct, SupplierConfirmation

    key = f"whatsapp_supplier_pending:{phone}"
    raw = cache.get(key)

    # No pending order → treat message as a price update
    if not raw:
        return _handle_supplier_price_update(phone, supplier, body)

    data = json.loads(raw)
    products = data["products"]
    order_request_id = data["order_request_id"]

    confirmed, missing = _parse_supplier_reply(body, products)

    CANCEL_KEYWORDS = ["ביטול", "לא מאשר", "מבטל", "cancel", "לא רוצה"]
    if any(kw in body.strip().lower() for kw in CANCEL_KEYWORDS):
        cache.delete(key)
        send_whatsapp_message(phone, "✅ ביטול ההזמנה התקבל.")

        try:
            from apps.orders.models import OrderRequest, OrderRequestProduct
            from apps.orders.services import find_full_coverage_fallback

            # Get customer phone
            first_orp = OrderRequestProduct.objects.select_related(
                "order_request__user__profile"
            ).filter(order_request_id=order_request_id).first()
            customer_phone = None
            if first_orp:
                p = getattr(first_orp.order_request.user, "profile", None)
                customer_phone = p.phone if p else None

            fallback = find_full_coverage_fallback(
                order_request_id=order_request_id,
                failing_supplier_id=supplier.id,
            )

            if fallback:
                new_supplier = fallback["supplier"]

                # Transfer all items to new supplier
                for item in fallback["items"]:
                    orp = item["orp"]
                    orp.supplier = new_supplier
                    orp.unit_price = item["new_price"]
                    orp.save(update_fields=["supplier", "unit_price"])
                _recalculate_order_total(order_request_id)

                # Send order to new supplier
                order_obj = OrderRequest.objects.select_related("user__profile").get(id=order_request_id)
                prof = getattr(order_obj.user, "profile", None)
                company = prof.company_name if prof else ""
                address = prof.company_address if prof else ""
                cp = prof.company_phone if prof else ""

                msg_lines = [f"שלום, *{company}* מבקש להזמין:"]
                for item in fallback["items"]:
                    orp = item["orp"]
                    msg_lines.append(f"- {orp.product.name} x{orp.quantity} {orp.product.get_unit_display()}")
                if address:
                    msg_lines.append(f"\n📍 *כתובת למשלוח:* {address}")
                if cp:
                    msg_lines.append(f"📞 {cp}")
                msg_lines.append("\nענה:\n• *אישור* — לאישור הכל\n• *חסר [שם מוצר]* — אם פריט לא זמין\n• *ביטול* — לביטול ההזמנה")
                send_whatsapp_message(new_supplier.whatsapp_number, "\n".join(msg_lines))

                save_supplier_pending_order(
                    supplier_phone=new_supplier.whatsapp_number,
                    order_request_id=order_request_id,
                    products=[
                        {
                            "orp_id": item["orp"].id,
                            "product_name": item["orp"].product.name,
                            "quantity": str(item["orp"].quantity),
                            "unit": item["orp"].product.get_unit_display(),
                        }
                        for item in fallback["items"]
                    ],
                )

                # Notify customer of auto-transfer
                if customer_phone:
                    lines = [
                        f"⚠️ *{supplier.name}* ביטל את הזמנה #{order_request_id}.",
                        f"✅ העברנו אוטומטית ל-*{new_supplier.name}*:",
                    ]
                    for item in fallback["items"]:
                        orp = item["orp"]
                        lines.append(f"  • {orp.product.name} x{orp.quantity} — {item['new_price']}₪")
                    lines.append(f'\nסה"כ חדש: {fallback["redirect_total"]:.2f}₪')
                    if not fallback["minimum_met"]:
                        lines.append(f"⚠️ חסר {fallback['missing_amount']:.2f}₪ למינימום {new_supplier.name}")
                    send_whatsapp_message(customer_phone, "\n".join(lines))
            else:
                # No fallback — cancel the order
                OrderRequest.objects.filter(id=order_request_id).update(status=OrderRequest.Status.CANCELLED)
                if customer_phone:
                    send_whatsapp_message(
                        customer_phone,
                        f"❌ *{supplier.name}* ביטל את הזמנה #{order_request_id}.\n"
                        "לא נמצא ספק חלופי. ניתן ליצור הזמנה חדשה דרך המערכת.",
                    )
        except Exception as exc:
            logger.error("Failed to handle cancellation for order %s: %s", order_request_id, exc)

        return HttpResponse(status=200)

    if not confirmed and not missing:
        send_whatsapp_message(
            phone,
            "לא הצלחתי להבין.\nשלח *אישור* לאישור הכל, כמויות כגון:\nעגבניות 40, מלפפונים 25\nאו *חסר עגבניות* לדיווח על מוצר חסר.",
        )
        return HttpResponse(status=200)

    # Save confirmations
    for orp_id, qty in confirmed.items():
        try:
            orp = OrderRequestProduct.objects.get(id=int(orp_id))
            SupplierConfirmation.objects.update_or_create(
                order_request_product=orp,
                defaults={"confirmed_quantity": qty},
            )
        except (OrderRequestProduct.DoesNotExist, ValueError):
            pass

    # Detect partial confirmations (supplier can supply less than the requested qty)
    # Edge case 2: split ORP — reduce original to confirmed_qty, find fallback for remainder
    partial_products = []
    for p in products:
        orp_id = p["orp_id"]
        if orp_id not in confirmed:
            continue
        requested_qty = Decimal(str(p["quantity"]))
        confirmed_qty = confirmed[orp_id]
        if confirmed_qty >= requested_qty:
            continue
        remaining_qty = requested_qty - confirmed_qty
        try:
            orp = OrderRequestProduct.objects.get(id=int(orp_id))
            orp.quantity = confirmed_qty
            orp.save(update_fields=["quantity"])
            partial_products.append({
                "orp_id": orp_id,
                "product_name": p["product_name"],
                "quantity": str(remaining_qty),
                "unit": p["unit"],
            })
        except OrderRequestProduct.DoesNotExist:
            pass

    # Mark order APPROVED only if all items confirmed, none missing, none partial
    if not missing and not partial_products:
        try:
            from apps.orders.models import OrderRequest
            total_orps = OrderRequestProduct.objects.filter(order_request_id=order_request_id).count()
            confirmed_orps = SupplierConfirmation.objects.filter(
                order_request_product__order_request_id=order_request_id
            ).count()
            if total_orps > 0 and confirmed_orps >= total_orps:
                OrderRequest.objects.filter(id=order_request_id).update(status=OrderRequest.Status.APPROVED)
        except Exception as exc:
            logger.error("Failed to update order status after supplier confirmation: %s", exc)

    cache.delete(key)

    # Parse optional cutoff time
    cutoff_time = _parse_supplier_cutoff(body)
    if cutoff_time:
        cutoff_str = cutoff_time.strftime("%H:%M")
        cache.set(f"supplier_cutoff:{phone}:{order_request_id}", cutoff_str, timeout=CUTOFF_TTL)

    # Acknowledge supplier
    ack_lines = ["✅ תודה! קיבלתי:"]
    for p in products:
        orp_id = p["orp_id"]
        if orp_id in confirmed:
            c_qty = confirmed[orp_id]
            r_qty = Decimal(str(p["quantity"]))
            if c_qty < r_qty:
                ack_lines.append(f"  ⚠️ {p['product_name']} {c_qty}/{r_qty} {p['unit']} (חלקי)")
            else:
                ack_lines.append(f"  ✅ {p['product_name']} x{c_qty} {p['unit']}")
        elif p in missing:
            ack_lines.append(f"  ❌ {p['product_name']} — חסר")
    if cutoff_time:
        ack_lines.append(f"\n⏰ שינויים מתקבלים עד {cutoff_time.strftime('%H:%M')}")
    send_whatsapp_message(phone, "\n".join(ack_lines))

    # Notify customer about confirmed items
    try:
        orp = OrderRequestProduct.objects.select_related(
            "order_request__user__profile"
        ).filter(order_request_id=order_request_id).first()

        if orp and confirmed:
            customer_profile = getattr(orp.order_request.user, "profile", None)
            customer_phone = customer_profile.phone if customer_profile else None
            if customer_phone:
                customer_lines = [f"✅ *{supplier.name}* אישר:"]
                for p in products:
                    orp_id_p = p["orp_id"]
                    if orp_id_p in confirmed:
                        c_qty = confirmed[orp_id_p]
                        r_qty = Decimal(str(p["quantity"]))
                        if c_qty < r_qty:
                            customer_lines.append(f"  • {p['product_name']} {c_qty}/{r_qty} {p['unit']} (חלקי)")
                        else:
                            customer_lines.append(f"  • {p['product_name']} x{c_qty} {p['unit']}")
                customer_lines.append(f"\nמספר הזמנה: #{order_request_id}")
                send_whatsapp_message(customer_phone, "\n".join(customer_lines))
    except Exception as exc:
        logger.error("Failed to notify customer after supplier confirmation: %s", exc)

    # Find fallback suppliers for missing/partial items and notify customer
    if missing or partial_products:
        _handle_missing_items(supplier, missing, order_request_id, partial_products=partial_products)

    return HttpResponse(status=200)


def _handle_missing_items(original_supplier, missing_products: list, order_request_id: int, partial_products: list = None):
    """
    Find fallback suppliers for items the supplier can't fulfill and notify the customer.
    missing_products: fully missing (entire ORP needs redirect).
    partial_products: supplier confirmed partial qty; remaining qty needs fallback (ORP already reduced).
    Edge case 5: if no fallback exists for a product, auto-remove it from the order.
    """
    from apps.catalog.models import Product
    from apps.orders.models import OrderRequest, OrderRequestProduct
    from apps.orders.services import find_fallback_for_product

    partial_products = partial_products or []

    order = (
        OrderRequest.objects
        .select_related("user__profile")
        .filter(id=order_request_id)
        .first()
    )
    if not order:
        return

    customer_profile = getattr(order.user, "profile", None)
    customer_phone = customer_profile.phone if customer_profile else None
    if not customer_phone:
        return

    redirects = []
    no_fallback = []  # list of {"product_name", "quantity", "unit", "partial"}
    auto_removed = False

    # ── Process fully missing items ──
    for mp in missing_products:
        product = Product.objects.filter(name=mp["product_name"]).first()
        if not product:
            no_fallback.append({"product_name": mp["product_name"], "quantity": mp.get("quantity", ""), "unit": mp.get("unit", "")})
            continue

        existing_orp = OrderRequestProduct.objects.filter(
            order_request_id=order_request_id, product=product
        ).first()
        original_price = str(existing_orp.unit_price) if existing_orp else "?"

        fallback = find_fallback_for_product(
            product=product,
            excluded_supplier_id=original_supplier.id,
            order_request_id=order_request_id,
            quantity=Decimal(str(mp["quantity"])),
        )

        if not fallback:
            # Edge case 5: no supplier at all — auto-remove from order immediately
            if existing_orp:
                existing_orp.delete()
                auto_removed = True
            no_fallback.append({"product_name": mp["product_name"], "quantity": mp.get("quantity", ""), "unit": mp.get("unit", "")})
            continue

        redirects.append({
            "type": "missing",
            "orp_id": mp["orp_id"],
            "product_name": mp["product_name"],
            "quantity": mp["quantity"],
            "unit": mp.get("unit", ""),
            "original_supplier_id": original_supplier.id,
            "original_supplier_name": original_supplier.name,
            "original_price": original_price,
            "fallback_supplier_id": fallback["supplier"].id,
            "fallback_supplier_name": fallback["supplier"].name,
            "fallback_supplier_whatsapp": fallback["supplier"].whatsapp_number,
            "fallback_price": str(fallback["price"]),
            "minimum_met": fallback["minimum_met"],
            "missing_amount": str(fallback["missing_amount"]),
        })

    # ── Process partially confirmed items (edge case 2) ──
    for pp in partial_products:
        try:
            orp = OrderRequestProduct.objects.select_related("product").get(id=int(pp["orp_id"]))
        except OrderRequestProduct.DoesNotExist:
            continue
        product = orp.product

        fallback = find_fallback_for_product(
            product=product,
            excluded_supplier_id=original_supplier.id,
            order_request_id=order_request_id,
            quantity=Decimal(str(pp["quantity"])),
        )

        if not fallback:
            no_fallback.append({
                "product_name": pp["product_name"],
                "quantity": pp["quantity"],
                "unit": pp.get("unit", ""),
                "partial": True,
            })
            continue

        redirects.append({
            "type": "partial",
            "orp_id": pp["orp_id"],  # original ORP already reduced to confirmed_qty
            "product_name": pp["product_name"],
            "quantity": pp["quantity"],  # remaining (unconfirmed) qty
            "unit": pp.get("unit", ""),
            "original_supplier_id": original_supplier.id,
            "original_supplier_name": original_supplier.name,
            "original_price": str(orp.unit_price),
            "fallback_supplier_id": fallback["supplier"].id,
            "fallback_supplier_name": fallback["supplier"].name,
            "fallback_supplier_whatsapp": fallback["supplier"].whatsapp_number,
            "fallback_price": str(fallback["price"]),
            "minimum_met": fallback["minimum_met"],
            "missing_amount": str(fallback["missing_amount"]),
        })

    # Edge case 1: recalculate total after any auto-removals
    if auto_removed:
        _recalculate_order_total(order_request_id)

    # ── Build customer message ──
    lines = [f"⚠️ *{original_supplier.name}* דיווח:"]
    for r in redirects:
        if r["type"] == "partial":
            lines.append(
                f"\n• *{r['product_name']}* — ספק אישר רק חלק מהכמות"
                f"\n  נשארו {r['quantity']} {r['unit']} שטרם סופקו"
                f"\n  ✅ {r['fallback_supplier_name']} יכול לספק את הנותר ב-{r['fallback_price']}₪"
            )
        else:
            lines.append(
                f"\n• *{r['product_name']}* x{r['quantity']} {r['unit']}"
                f"\n  ❌ {r['original_supplier_name']} — חסר"
                f"\n  ✅ {r['fallback_supplier_name']} — {r['fallback_price']}₪ (במקום {r['original_price']}₪)"
            )
        if not r["minimum_met"]:
            lines.append(f"  ⚠️ חסר עוד {Decimal(r['missing_amount']):.2f}₪ למינימום ספק זה")

    for nf in no_fallback:
        if nf.get("partial"):
            lines.append(
                f"\n• *{nf['product_name']}* {nf['quantity']} {nf['unit']} — "
                "לא נמצא ספק חלופי לכמות הנותרת"
            )
        else:
            lines.append(
                f"\n• *{nf['product_name']}* x{nf['quantity']} {nf['unit']} — "
                "חסר המוצר הזה במלאי לכל הספקים, הוסר מההזמנה אוטומטית"
            )

    if redirects:
        lines.append("\nענה *כן* להעברה לספק חלופי, *לא* לביטול.")
        _save_fallback_state(customer_phone, {
            "order_request_id": order_request_id,
            "original_supplier_name": original_supplier.name,
            "redirects": redirects,
        })
    elif not lines[1:]:
        # Only auto-removed items with no fallback — nothing to confirm
        pass

    send_whatsapp_message(customer_phone, "\n".join(lines))


def _handle_fallback_approval(phone: str, body: str) -> HttpResponse | None:
    """Handle customer's yes/no to a fallback supplier suggestion. Returns None if no fallback pending."""
    raw = _get_fallback_state(phone)
    if not raw:
        return None

    state = json.loads(raw)
    body_lower = body.strip().lower()

    yes_words = ["כן", "yes", "אישור", "אוקי", "ok", "בסדר", "1"]
    no_words = ["לא", "no", "ביטול", "cancel", "2"]

    if any(w in body_lower for w in yes_words):
        return _execute_fallback_redirect(phone, state)
    elif any(w in body_lower for w in no_words):
        return _remove_missing_items(phone, state)
    else:
        supplier_name = state.get("original_supplier_name", "הספק")
        send_whatsapp_message(
            phone,
            f"⏳ ממתין לתשובתך: *{supplier_name}* דיווח על פריטים חסרים.\n"
            "ענה *כן* להעברה לספק חלופי, *לא* לביטול.",
        )
        return HttpResponse(status=200)


def _remove_missing_items(phone: str, state: dict) -> HttpResponse:
    """Customer declined fallback — remove the missing products and check remaining minimums."""
    from apps.catalog.models import Supplier
    from apps.orders.models import OrderRequestProduct

    _clear_fallback_state(phone)

    order_request_id = state["order_request_id"]
    redirects = state["redirects"]

    removed_lines = []
    affected_supplier_ids = set()

    for r in redirects:
        if r.get("type") == "partial":
            # Original ORP was already reduced to confirmed_qty; just skip creating new ORP
            removed_lines.append(
                f"  • {r['product_name']} {r['quantity']} {r.get('unit', '')} (כמות חלקית — לא תוזמן)"
            )
        else:
            try:
                orp = OrderRequestProduct.objects.get(id=r["orp_id"])
                affected_supplier_ids.add(orp.supplier_id)
                orp.delete()
                removed_lines.append(f"  • {r['product_name']} x{r['quantity']} {r.get('unit', '')} הוסר")
            except OrderRequestProduct.DoesNotExist:
                pass

    # Edge case 1: update total_price after removals
    _recalculate_order_total(order_request_id)

    lines = ["🗑️ הבנתי — הפריטים הבאים הוסרו מההזמנה:"]
    lines += removed_lines

    # Check if remaining suppliers still meet their minimum
    remaining_orps = list(
        OrderRequestProduct.objects.filter(order_request_id=order_request_id)
        .select_related("supplier")
    )

    from collections import defaultdict as _defaultdict
    from decimal import Decimal as _Decimal
    supplier_totals = _defaultdict(_Decimal)
    supplier_obj = {}
    for orp in remaining_orps:
        supplier_totals[orp.supplier_id] += orp.quantity * orp.unit_price
        supplier_obj[orp.supplier_id] = orp.supplier

    for sid in affected_supplier_ids:
        if sid not in supplier_obj:
            continue
        supplier = supplier_obj[sid]
        total = supplier_totals.get(sid, _Decimal(0))
        if total >= supplier.minimum_order:
            continue

        missing_amount = supplier.minimum_order - total
        lines.append(
            f"\n⚠️ {supplier.name} נפל ל-{total:.2f}₪ (מינימום {supplier.minimum_order}₪, חסר {missing_amount:.2f}₪)."
        )
        lines.append("מחפש ספק חלופי לשאר המוצרים...")

        _auto_transfer_remaining(
            phone=phone,
            order_request_id=order_request_id,
            failing_supplier=supplier,
            lines=lines,
        )

    send_whatsapp_message(phone, "\n".join(lines))
    return HttpResponse(status=200)


def _auto_transfer_remaining(phone: str, order_request_id: int, failing_supplier, lines: list):
    """Move all remaining items from failing_supplier to the best available fallback."""
    from apps.orders.models import OrderRequest, OrderRequestProduct
    from apps.orders.services import find_full_coverage_fallback

    result = find_full_coverage_fallback(
        order_request_id=order_request_id,
        failing_supplier_id=failing_supplier.id,
    )

    if not result:
        lines.append(f"❌ לא נמצא ספק שיכול לכסות את כל המוצרים של {failing_supplier.name}.")
        return

    new_supplier = result["supplier"]

    if not result["minimum_met"]:
        lines.append(
            f"⛔ {new_supplier.name} יכול לכסות הכל אך גם לא עומד במינימום "
            f"(חסר {result['missing_amount']:.2f}₪). הוסף מוצרים נוספים."
        )
        return

    # Execute the transfer
    for item in result["items"]:
        orp = item["orp"]
        orp.supplier = new_supplier
        orp.unit_price = item["new_price"]
        orp.save(update_fields=["supplier", "unit_price"])
        lines.append(f"  ↪ {orp.product.name} x{orp.quantity} → {new_supplier.name} ({item['new_price']}₪)")

    lines.append(f"✅ כל המוצרים של {failing_supplier.name} הועברו ל-{new_supplier.name}.")

    # Edge case 1: recalculate total after price changes
    _recalculate_order_total(order_request_id)

    # Notify the new supplier
    try:
        order = OrderRequest.objects.select_related("user__profile").get(id=order_request_id)
        profile = getattr(order.user, "profile", None)
        company = profile.company_name if profile else ""
        address = profile.company_address if profile else ""
        company_phone_str = profile.company_phone if profile else ""
    except OrderRequest.DoesNotExist:
        company = address = company_phone_str = ""

    msg_lines = [f"שלום, *{company}* מבקש להוסיף להזמנה:"]
    for item in result["items"]:
        orp = item["orp"]
        msg_lines.append(f"- {orp.product.name} x{orp.quantity} {orp.product.get_unit_display()}")
    if address:
        msg_lines.append(f"\n📍 *כתובת למשלוח:* {address}")
    if company_phone_str:
        msg_lines.append(f"📞 {company_phone_str}")
    msg_lines.append("\nאנא ענה *אישור* לאישור.")

    send_whatsapp_message(new_supplier.whatsapp_number, "\n".join(msg_lines))
    save_supplier_pending_order(
        supplier_phone=new_supplier.whatsapp_number,
        order_request_id=order_request_id,
        products=[
            {
                "orp_id": item["orp"].id,
                "product_name": item["orp"].product.name,
                "quantity": str(item["orp"].quantity),
                "unit": item["orp"].product.get_unit_display(),
            }
            for item in result["items"]
        ],
    )


def _execute_fallback_redirect(phone: str, state: dict) -> HttpResponse:
    """Execute approved fallback: update order items, send WhatsApp to new supplier."""
    from apps.catalog.models import Supplier
    from apps.orders.models import OrderRequest, OrderRequestProduct

    _clear_fallback_state(phone)

    order_request_id = state["order_request_id"]
    redirects = state["redirects"]

    by_supplier = defaultdict(list)
    for r in redirects:
        by_supplier[r["fallback_supplier_id"]].append(r)

    success_lines = ["✅ ההעברה בוצעה:"]
    below_minimum_msgs = []

    for supplier_id, items in by_supplier.items():
        try:
            supplier = Supplier.objects.get(id=supplier_id)
        except Supplier.DoesNotExist:
            continue

        # Check minimum with existing + redirected items BEFORE updating DB
        existing_total = sum(
            orp.quantity * orp.unit_price
            for orp in OrderRequestProduct.objects.filter(
                order_request_id=order_request_id, supplier=supplier
            )
        )
        redirect_total = sum(
            Decimal(r["quantity"]) * Decimal(r["fallback_price"]) for r in items
        )
        new_total = existing_total + redirect_total

        if new_total < supplier.minimum_order:
            missing_amount = supplier.minimum_order - new_total
            below_minimum_msgs.append(
                f"⛔ {supplier.name}: סה\"כ {new_total:.2f}₪, חסר {missing_amount:.2f}₪ למינימום ({supplier.minimum_order}₪)\n"
                f"   הוסף מוצרים נוספים מ-{supplier.name} כדי לעמוד במינימום."
            )
            continue  # Skip this supplier — don't update DB or send message

        # Update DB and collect items for supplier message
        supplier_items_for_msg = []
        for r in items:
            if r.get("type") == "partial":
                # Edge case 2: create NEW ORP for remaining qty — original ORP already reduced
                try:
                    original_orp = OrderRequestProduct.objects.select_related("product").get(id=r["orp_id"])
                    new_orp = OrderRequestProduct.objects.create(
                        order_request_id=order_request_id,
                        product=original_orp.product,
                        supplier=supplier,
                        quantity=Decimal(r["quantity"]),
                        unit_price=Decimal(r["fallback_price"]),
                    )
                    success_lines.append(
                        f"  • {r['product_name']} {r['quantity']} {r.get('unit', '')} (חלקי) → {supplier.name}"
                    )
                    # Build redirect entry for supplier pending cache using new ORP id
                    supplier_items_for_msg.append({**r, "orp_id": new_orp.id, "quantity": r["quantity"]})
                except OrderRequestProduct.DoesNotExist:
                    pass
            else:
                # Full redirect: change existing ORP to new supplier
                try:
                    orp = OrderRequestProduct.objects.get(id=r["orp_id"])
                    orp.supplier = supplier
                    orp.unit_price = Decimal(r["fallback_price"])
                    orp.save(update_fields=["supplier", "unit_price"])
                    success_lines.append(f"  • {r['product_name']} x{r['quantity']} {r.get('unit', '')} → {supplier.name}")
                    supplier_items_for_msg.append(r)
                except OrderRequestProduct.DoesNotExist:
                    pass

        if not supplier_items_for_msg:
            continue

        # Build supplier WhatsApp message
        try:
            order = OrderRequest.objects.select_related("user__profile").get(id=order_request_id)
            profile = getattr(order.user, "profile", None)
            company = profile.company_name if profile else ""
            address = profile.company_address if profile else ""
            company_phone_str = profile.company_phone if profile else ""
        except OrderRequest.DoesNotExist:
            company = address = company_phone_str = ""

        msg_lines = [f"שלום, *{company}* מבקש להוסיף להזמנה:"]
        for r in supplier_items_for_msg:
            msg_lines.append(f"- {r['product_name']} x{r['quantity']} {r.get('unit', '')}")
        if address:
            msg_lines.append(f"\n📍 *כתובת למשלוח:* {address}")
        if company_phone_str:
            msg_lines.append(f"📞 {company_phone_str}")
        msg_lines.append("\nאנא ענה *אישור* לאישור.")

        send_whatsapp_message(supplier.whatsapp_number, "\n".join(msg_lines))
        save_supplier_pending_order(
            supplier_phone=supplier.whatsapp_number,
            order_request_id=order_request_id,
            products=[
                {
                    "orp_id": r["orp_id"],
                    "product_name": r["product_name"],
                    "quantity": r["quantity"],
                    "unit": r.get("unit", ""),
                }
                for r in supplier_items_for_msg
            ],
        )

    # Edge case 1: recalculate total after all redirect changes
    _recalculate_order_total(order_request_id)

    reply = "\n".join(success_lines)
    if below_minimum_msgs:
        reply += "\n\n" + "\n".join(below_minimum_msgs)
    send_whatsapp_message(phone, reply)
    return HttpResponse(status=200)


# ─────────────────────── Main webhook ───────────────────────

@csrf_exempt
def whatsapp_webhook(request):
    if request.method != "POST":
        return HttpResponse(status=405)

    if not _validate_twilio_signature(request):
        logger.warning("Rejected request with invalid Twilio signature")
        return HttpResponse(status=403)

    message_sid = request.POST.get("MessageSid", "")
    if message_sid:
        dedup_key = f"twilio_msg:{message_sid}"
        if not cache.add(dedup_key, 1, timeout=3600):
            return HttpResponse(status=200)

    body = request.POST.get("Body", "").strip()
    from_raw = request.POST.get("From", "")
    phone = _normalize_phone(from_raw.replace("whatsapp:", ""))

    from apps.catalog.models import Supplier
    supplier = Supplier.objects.filter(whatsapp_number=phone).first()
    if supplier:
        return _handle_supplier_flow(phone, supplier, body)

    return _handle_user_flow(phone, body)
