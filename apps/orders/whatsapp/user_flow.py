import json
import logging
from datetime import time as dtime
from decimal import Decimal

from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone

from .cache import save_pending_order, DecimalEncoder
from .delivery_flow import _handle_delivery_flow
from .fallback_flow import _handle_fallback_approval
from . import validators

logger = logging.getLogger(__name__)


def _format_scenario(label, s):
    lines = [f"*{label}*"]
    for p in s["products"]:
        lines.append(
            f"  • {p['product_name']} x{p['quantity']} {p.get('unit', '')} "
            f"— {p['supplier_name']} — {p['subtotal']}₪"
        )
    lines.append(f'סה"כ: {s["total_price"]}₪')
    return "\n".join(lines)


def _format_minimum_warning(issues: list) -> str:
    lines = ["⛔ מינימום הזמנה לא עומד:"]
    for issue in issues:
        lines.append(
            f"  • {issue['supplier_name']}: נדרש ₪{issue['minimum_required']}, "
            f"חסר ₪{Decimal(str(issue['missing_amount'])):.2f}"
        )
    return "\n".join(lines)


def _build_and_send_confirmed_order(data: dict, scenario: str) -> bool:
    """Build order in DB and send WhatsApp to each supplier. Returns True on success."""
    from django.contrib.auth import get_user_model
    from apps.catalog.models import Product
    from apps.orders.services import build_order
    from .supplier_flow import notify_suppliers_for_order

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
        validators.send_whatsapp_message(phone, "מספר הטלפון שלך לא רשום במערכת. פנה למנהל.")
        return HttpResponse(status=200)

    user = profile.user
    all_products = list(Product.objects.all())
    product_names = [p.name for p in all_products]

    try:
        parsed_items = parse_customer_order(body, product_names)
    except ValueError:
        validators.send_whatsapp_message(
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
        validators.send_whatsapp_message(
            phone,
            f"לא זיהיתי מוצרים ידועים בהזמנה.\nלא זוהה: {', '.join(unrecognized)}",
        )
        return HttpResponse(status=200)

    try:
        result = suggest_order(user=user, region=profile.region, products=products)
    except ValueError as exc:
        validators.send_whatsapp_message(phone, f"שגיאה בעיבוד ההזמנה: {exc}")
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

    validators.send_whatsapp_message(phone, msg)
    return HttpResponse(status=200)


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
                    validators.send_whatsapp_message(
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
            validators.send_whatsapp_message(existing_orp.supplier.whatsapp_number, "\n".join(msg_lines))
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
            validators.send_whatsapp_message(sp.supplier.whatsapp_number, "\n".join(msg_lines))
            changes_made.append(
                f"נוסף: {product.name} x{item['quantity']} {product.get_unit_display()}"
            )

    if not changes_made and not errors:
        validators.send_whatsapp_message(phone, "לא הצלחתי לזהות שינוי בהזמנה. נסה שוב.")
        return HttpResponse(status=200)

    reply_lines = []
    if changes_made:
        reply_lines.append(f"✅ השינויים נשלחו לספקים:")
        reply_lines += [f"  • {c}" for c in changes_made]
    if errors:
        reply_lines.append(f"⚠️ לא נמצאו: {', '.join(errors)}")

    validators.send_whatsapp_message(phone, "\n".join(reply_lines))
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
        validators.send_whatsapp_message(phone, "אנא ענה *א* או *ב* כדי לבחור.")
        return HttpResponse(status=200)

    minimum_issues = data.get("minimum_issues", {})
    scenario_issues = minimum_issues.get(scenario, [])
    if scenario_issues:
        msg = _format_minimum_warning(scenario_issues)
        msg += "\n\nשלח הזמנה מחודשת עם כמויות גדולות יותר כדי לעמוד במינימום."
        validators.send_whatsapp_message(phone, msg)
        return HttpResponse(status=200)

    cache.delete(key)
    success = _build_and_send_confirmed_order(data, scenario)

    if success:
        confirm = _format_scenario(f"✅ אושר! {label}", chosen)
        confirm += "\n\nההזמנה נשלחה לספקים."
    else:
        confirm = "❌ אירעה שגיאה בעיבוד ההזמנה. אנא נסה שנית או פנה לתמיכה."
    validators.send_whatsapp_message(phone, confirm)

    return HttpResponse(status=200)
