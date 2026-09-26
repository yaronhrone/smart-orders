import json
import logging
import re
from collections import defaultdict
from datetime import time as dtime, timedelta
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone

from apps.catalog.product_matcher import MISSING_KEYWORDS, resolve_alias
from .cache import (
    clear_eta_request, get_eta_request, save_supplier_pending_order, CUTOFF_TTL,
)
from .fallback_flow import _handle_missing_items, _recalculate_order_total
from . import validators

logger = logging.getLogger(__name__)


SUPPLIER_REPLY_INSTRUCTIONS = (
    "\nענה:\n• *אישור* — לאישור הכל\n• *חסר [שם מוצר]* — אם פריט לא זמין\n• *ביטול* — לביטול ההזמנה"
)


def _company_details(order):
    profile = getattr(order.user, "profile", None)
    if not profile:
        return "", "", ""
    return profile.company_name, profile.company_address, profile.company_phone


def _save_pending_for_order(order) -> None:
    """
    Register everything in `order` the supplier hasn't confirmed yet as
    pending — not just the items in the latest message. An order can gain
    items after the first message (a redirect, a customer addition); if the
    pending state only held the newest ones, the supplier's "אישור" would
    leave the earlier ones unconfirmed forever and the order stuck on SENT.
    """
    from apps.orders.models import SupplierConfirmation

    confirmed_ids = set(
        SupplierConfirmation.objects
        .filter(order_request_product__order_request=order)
        .values_list("order_request_product_id", flat=True)
    )
    save_supplier_pending_order(
        supplier_phone=order.supplier.whatsapp_number,
        order_request_id=order.id,
        products=[
            {
                "orp_id": item.id,
                "product_name": item.product.name,
                "quantity": str(item.quantity),
                "unit": item.product.get_unit_display(),
            }
            for item in order.products.select_related("product").all()
            if item.id not in confirmed_ids
        ],
    )


def notify_suppliers_for_order(order) -> None:
    """Send the order to its supplier, save pending state, and mark it SENT.

    One order = one supplier (a checkout with several suppliers is several
    orders in one OrderBatch — see notify_suppliers_for_batch).
    """
    from apps.orders.models import OrderRequest
    from apps.orders.tasks import send_supplier_order_notification_task

    company_name, company_address, company_phone = _company_details(order)

    lines = [f"שלום, *{company_name}* מבקש להזמין (הזמנה #{order.id}):"]
    for item in order.products.select_related("product").all():
        lines.append(f"- {item.product.name} x{item.quantity} {item.product.get_unit_display()}")
    if company_address:
        lines.append(f"\n📍 *כתובת למשלוח:* {company_address}")
    if company_phone:
        lines.append(f"📞 {company_phone}")
    lines.append(SUPPLIER_REPLY_INSTRUCTIONS)
    send_supplier_order_notification_task.delay(order.supplier.whatsapp_number, "\n".join(lines))

    _save_pending_for_order(order)
    order.transition_to(OrderRequest.Status.SENT)


def notify_suppliers_for_batch(orders) -> None:
    for order in orders:
        notify_suppliers_for_order(order)


def notify_supplier_of_items(order, items, *, created: bool) -> None:
    """
    Tell `order`'s supplier about items that just landed in it (a reroute, a
    redirect, a customer addition) and re-register the order's pending state.
    `created` — the order itself is new (first message to this supplier for
    it) vs. items added to an order they already have open.
    """
    company, address, company_phone = _company_details(order)
    if created:
        msg_lines = [f"שלום, *{company}* מבקש להזמין (הזמנה #{order.id}):"]
    else:
        msg_lines = [f"שלום, *{company}* מבקש להוסיף להזמנה #{order.id}:"]
    for item in items:
        msg_lines.append(f"- {item.product.name} x{item.quantity} {item.product.get_unit_display()}")
    if address:
        msg_lines.append(f"\n📍 *כתובת למשלוח:* {address}")
    if company_phone:
        msg_lines.append(f"📞 {company_phone}")
    msg_lines.append(SUPPLIER_REPLY_INSTRUCTIONS)
    validators.send_whatsapp_message(order.supplier.whatsapp_number, "\n".join(msg_lines))
    _save_pending_for_order(order)


def _dispatch_reroute_assignments(
    order_request_id: int, assignments: list, unavailable: list,
    customer_phone: str | None, intro_lines: list,
) -> None:
    """
    Commit a validated (every resulting supplier clears their minimum)
    reroute of a cancelled supplier's order: move each item into its new
    supplier's order in the same batch (an open sibling, or a new order),
    notify each of those suppliers once, cancel the source order, and send
    the customer one consolidated message covering every item that moved.
    Shared by the immediate happy path and by a top-up that just cleared a
    minimum that was previously blocking dispatch.
    """
    from apps.orders.models import OrderRequest
    from apps.orders.services import move_item_to_supplier, refresh_order_after_changes

    touched = {}  # target order id -> {"order", "items", "created"}
    for a in assignments:
        target = move_item_to_supplier(a["orp"], a["supplier"], a["unit_price"])
        if target.id not in touched:
            touched[target.id] = {
                "order": target,
                "items": [],
                "created": not target.products.exclude(id=a["orp"].id).exists(),
            }
        touched[target.id]["items"].append(a["orp"])

    for entry in touched.values():
        refresh_order_after_changes(entry["order"].id)
        notify_supplier_of_items(entry["order"], entry["items"], created=entry["created"])

    source = refresh_order_after_changes(order_request_id)
    if source and source.status not in (OrderRequest.Status.CANCELLED, OrderRequest.Status.DELIVERED):
        source.transition_to(OrderRequest.Status.CANCELLED)

    if customer_phone:
        lines = list(intro_lines)
        for entry in touched.values():
            order = entry["order"]
            lines.append(f"\n*{order.supplier.name}* (הזמנה #{order.id}):")
            for item in entry["items"]:
                lines.append(f"  • {item.product.name} x{item.quantity} — {item.unit_price}₪")
        if unavailable:
            lines.append(f"\n❌ לא נמצא ספק חלופי עבור: {', '.join(unavailable)}")
        validators.send_whatsapp_message(customer_phone, "\n".join(lines))


def _notify_admin_supplier_cancelled(supplier, order_request_id: int) -> None:
    """Alert the admin that a supplier cancelled an order, so someone can
    find out why — routine reroute already ran by the time this fires,
    this is just so a pattern (or something unusual) doesn't go unnoticed."""
    from django.conf import settings
    admin_number = getattr(settings, "ADMIN_WHATSAPP_NUMBER", "")
    if not admin_number:
        logger.warning("ADMIN_WHATSAPP_NUMBER לא מוגדר — לא נשלחה התראה על ביטול ספק")
        return
    try:
        validators.send_whatsapp_message(
            admin_number,
            f"⚠️ *{supplier.name}* ביטל את הזמנה #{order_request_id}.\n"
            f"📞 {supplier.phone}\n"
            "הספק חסום מהזמנות חדשות ל-10 ימים. כדאי לברר למה — משהו חריג?",
        )
    except Exception as exc:
        logger.error("שגיאה בשליחת התראה לאדמין על ביטול ספק %s: %s", supplier.id, exc)


def send_order_to_supplier(supplier, assignments: list) -> str:
    lines = ["שלום, ברצוני להזמין:"]
    for a in assignments:
        lines.append(
            f"- {a['product'].name} x{a['quantity']} {a['product'].get_unit_display()}"
        )
    lines.append("תודה!")
    body = "\n".join(lines)
    return validators.send_whatsapp_message(supplier.whatsapp_number, body)


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


# Requires an explicit arrival verb, never bare "עד HH:MM" — that phrasing
# already means something else entirely in _parse_supplier_cutoff ("changes
# accepted until HH:MM"), and a supplier confirming with both in one message
# ("אישור, ניתן לשנות עד 14:00") must not have the cutoff time misread as a
# delivery ETA.
_ETA_ABSOLUTE_RE = re.compile(r"(?:יגיע|יגיעו|מגיע|תגיע|הגעה|משלוח)\D{0,15}?(\d{1,2}:\d{2})")
_ETA_RELATIVE_RE = re.compile(r"תוך\s+(שעה|שעתיים|\d+\s*שעות)")
_ETA_RELATIVE_HOURS = {"שעה": 1, "שעתיים": 2}


def _parse_delivery_eta(body: str):
    """
    Best-effort extraction of a delivery ETA from a supplier's own
    confirmation message — e.g. "אישור, יגיע עד 14:00" or "אישור, תוך שעתיים".
    Optional: most confirmations won't mention one at all. Returns a
    datetime.time (today, local) or None.
    """
    m = _ETA_ABSOLUTE_RE.search(body)
    if m:
        try:
            h, mi = m.group(1).split(":")
            return dtime(int(h), int(mi))
        except ValueError:
            return None

    m = _ETA_RELATIVE_RE.search(body)
    if m:
        token = m.group(1)
        hours = _ETA_RELATIVE_HOURS.get(token) or int(re.search(r"\d+", token).group())
        return (timezone.localtime() + timedelta(hours=hours)).time()

    return None


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
    _HEB = r"[֐-׿]+"
    missing_mentions: set[str] = set()
    for kw in MISSING_KEYWORDS:
        for m in re.finditer(rf"{re.escape(kw)}\s+({_HEB}(?:\s+{_HEB})?)", body_lower):
            missing_mentions.add(m.group(1).strip())
        for m in re.finditer(rf"({_HEB})\s+{re.escape(kw)}", body_lower):
            missing_mentions.add(m.group(1).strip())

    # Resolve mentions through the alias dictionary first — it already maps the
    # plural/spelling variants a supplier actually types ("עגבניות" → "עגבנייה").
    # The prefix heuristic below is only a fallback: dropping one character does
    # not bridge a two-letter suffix ("עגבנייה"[:-1] never matches "עגבניות"),
    # which silently confirmed items the supplier had just declared missing.
    catalog_names = {p["product_name"] for p in products}
    resolved_missing = {
        resolve_alias(mention, catalog_names) for mention in missing_mentions
    } - {None}

    missing = []
    remaining = []
    for p in products:
        name_lower = p["product_name"].lower()
        # Prefix to handle Hebrew pluralization: עגבניה → עגבני (matches עגבניות)
        match_name = name_lower[:-1] if len(name_lower) > 4 else name_lower
        is_missing = p["product_name"] in resolved_missing or any(
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
        validators.send_whatsapp_message(phone, f"שגיאה בעיבוד המחירים: {exc}")
        return HttpResponse(status=200)

    updated = result["updated"]
    removed = result["removed"]
    skipped = result["skipped"]

    if not updated and not removed and not skipped:
        validators.send_whatsapp_message(phone, "לא זיהיתי מחירים בהודעה. נסה לשלוח כגון:\nעגבניות 3.50, מלפפון 2.00")
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

    if removed:
        lines.append("\n🚫 סומנו כלא זמינים (לא יוצעו בהזמנות חדשות עד עדכון מחיר):")
        for r in removed:
            lines.append(f"  • {r['product_name']}")

    if skipped:
        lines.append("\n⚠️ לא זוהה:")
        for s in skipped:
            lines.append(f"  • {s['product_name']} — {s['reason']}")

    total = len(existing) + len(new_products) + len(removed)
    parts = []
    if existing:
        parts.append(f"{len(existing)} עודכנו")
    if new_products:
        parts.append(f"{len(new_products)} חדשים נוספו")
    if removed:
        parts.append(f"{len(removed)} סומנו לא זמינים")
    if skipped:
        parts.append(f"{len(skipped)} לא זוהו")
    lines.append(f"\nסה\"כ: {', '.join(parts)} ({total} מוצרים)")

    validators.send_whatsapp_message(phone, "\n".join(lines))
    return HttpResponse(status=200)


def _handle_supplier_flow(phone: str, supplier, body: str) -> HttpResponse:
    processing_key = f"whatsapp_supplier_processing:{phone}"
    if not cache.add(processing_key, 1, timeout=30):
        return HttpResponse(status=200)

    try:
        return _handle_supplier_flow_inner(phone, supplier, body)
    finally:
        cache.delete(processing_key)


# Words a supplier uses to say goods are on their way — checked only when
# they have no order awaiting confirmation, so it can never be confused with
# a reply to a pending "please confirm" message.
SHIPPING_KEYWORDS = ["יצא למשלוח", "יצאה למשלוח", "בדרך אליכם", "נשלח"]


def _handle_mark_shipped(phone: str, supplier, order, body: str = "") -> HttpResponse:
    """Supplier reports an already-approved order is out for delivery."""
    from apps.orders.models import OrderRequest

    try:
        order.transition_to(OrderRequest.Status.SHIPPED)
    except ValueError as exc:
        logger.error("Failed to mark order %s shipped: %s", order.id, exc)
        return HttpResponse(status=200)

    eta_time = _parse_delivery_eta(body)
    eta_suffix = f" צפויה להגיע עד {eta_time.strftime('%H:%M')}." if eta_time else ""
    validators.send_whatsapp_message(
        phone, f"✅ עדכנו שהזמנה #{order.id} יצאה למשלוח.{eta_suffix}"
    )

    customer_profile = getattr(order.user, "profile", None)
    customer_phone = (
        validators._local_to_e164(customer_profile.phone)
        if customer_profile and customer_profile.phone else None
    )
    if customer_phone:
        msg = f"📦 ההזמנה שלך #{order.id} יצאה למשלוח מ-{supplier.name}!"
        if eta_time:
            msg += f"\n🕐 צפויה להגיע עד השעה {eta_time.strftime('%H:%M')}"
        validators.send_whatsapp_message(customer_phone, msg)
    return HttpResponse(status=200)


def _handle_eta_update_reply(phone: str, supplier, body: str, eta_request: dict) -> HttpResponse:
    """
    Supplier's reply to a "customer says it hasn't arrived, when will it?"
    prompt (see delivery_flow._notify_supplier_not_arrived). Takes priority
    over everything else this supplier could send — same tradeoff the
    existing whatsapp_supplier_pending confirmation state already makes,
    and for the same reason: a delivery problem is urgent, and a supplier
    who wants out can still let ETA_REQUEST_TTL lapse.
    """
    order_id = eta_request["order_id"]
    eta_time = _parse_delivery_eta(body)
    if not eta_time:
        validators.send_whatsapp_message(
            phone,
            f"מחכים לעדכון שעת הגעה להזמנה #{order_id}. "
            "השב עם שעה, לדוגמה: \"עד 16:00\" או \"תוך שעה\".",
        )
        return HttpResponse(status=200)

    clear_eta_request(phone)
    eta_str = eta_time.strftime("%H:%M")
    validators.send_whatsapp_message(
        phone, f"✅ תודה, עדכנו את הלקוח שהזמנה #{order_id} צפויה להגיע עד {eta_str}."
    )
    validators.send_whatsapp_message(
        eta_request["customer_phone"],
        f"🕐 עדכון מ-{supplier.name}: הזמנה #{order_id} צפויה להגיע עד {eta_str}.",
    )
    return HttpResponse(status=200)


def _handle_supplier_flow_inner(phone: str, supplier, body: str) -> HttpResponse:
    from apps.orders.models import OrderRequestProduct, SupplierConfirmation

    eta_request = get_eta_request(phone)
    if eta_request is not None:
        return _handle_eta_update_reply(phone, supplier, body, eta_request)

    key = f"whatsapp_supplier_pending:{phone}"
    raw = cache.get(key)

    # No pending order → either a shipping notice for something already
    # approved, or (the common case) a price update.
    if not raw:
        if any(kw in body for kw in SHIPPING_KEYWORDS):
            from apps.orders.models import OrderRequest
            # One order per supplier, so this supplier's latest APPROVED
            # order is exactly what they're reporting — no shared status
            # with other suppliers to trip over.
            approved_order = (
                OrderRequest.objects
                .filter(supplier=supplier, status=OrderRequest.Status.APPROVED)
                .select_related("user__profile")
                .order_by("-created_at")
                .first()
            )
            if approved_order:
                return _handle_mark_shipped(phone, supplier, approved_order, body)
        return _handle_supplier_price_update(phone, supplier, body)

    data = json.loads(raw)
    products = data["products"]
    order_request_id = data["order_request_id"]

    confirmed, missing = _parse_supplier_reply(body, products)
    eta_time = _parse_delivery_eta(body)

    CANCEL_KEYWORDS = ["ביטול", "לא מאשר", "מבטל", "cancel", "לא רוצה"]
    if any(kw in body.strip().lower() for kw in CANCEL_KEYWORDS):
        cache.delete(key)
        validators.send_whatsapp_message(phone, "✅ ביטול ההזמנה התקבל.")

        try:
            from apps.orders.models import OrderRequest, OrderRequestProduct
            from apps.orders.services import _check_missing_minimum, find_reroute_for_cancelled_supplier
            from .cache import _save_reroute_grace_state

            # Block this supplier from any new assignment for 10 days, and
            # tell the admin so someone can find out why — before anything
            # else, so it happens even if the reroute below hits an error.
            supplier.blocked_until = timezone.now() + timedelta(days=10)
            supplier.save(update_fields=["blocked_until"])
            _notify_admin_supplier_cancelled(supplier, order_request_id)

            cancelled_order = OrderRequest.objects.select_related("user__profile").get(id=order_request_id)
            p = getattr(cancelled_order.user, "profile", None)
            customer_phone = validators._local_to_e164(p.phone) if p and p.phone else None

            reroute = find_reroute_for_cancelled_supplier(
                order_request_id=order_request_id,
                failing_supplier_id=supplier.id,
            )
            assignments = reroute["assignments"]
            unavailable = reroute["unavailable"]
            minimum_problems = (
                _check_missing_minimum(assignments, reroute["existing_totals"]) if assignments else []
            )

            if assignments and not minimum_problems:
                _dispatch_reroute_assignments(
                    order_request_id, assignments, unavailable, customer_phone,
                    intro_lines=[
                        f"⚠️ *{supplier.name}* ביטל את הזמנה #{order_request_id}.",
                        "✅ העברנו אוטומטית:",
                    ],
                )
            elif assignments and minimum_problems:
                # A valid full-coverage split exists, but it would leave a
                # resulting supplier under their own minimum — the user's
                # explicit rule: a pricier-but-valid split beats a cheaper
                # one, but nothing under minimum gets dispatched. Hold it
                # open instead of committing anything, and let the customer
                # top up (see _handle_reroute_grace_topup).
                if customer_phone:
                    _save_reroute_grace_state(customer_phone, order_request_id, supplier.id)
                    hours = 7200 // 3600
                    lines = [
                        f"⚠️ *{supplier.name}* ביטל את הזמנה #{order_request_id}.",
                        "מצאנו לאן להעביר את כל הפריטים, אבל זה לא עומד במינימום ההזמנה:",
                    ]
                    for problem in minimum_problems:
                        lines.append(
                            f"  • {problem['supplier_name']}: חסר {problem['missing_amount']:.2f}₪ "
                            f"(סה\"כ נוכחי {problem['current_total']:.2f}₪, מינימום {problem['minimum_required']:.2f}₪)"
                        )
                    lines.append(
                        f"\nיש לך {hours} שעות להוסיף עוד מוצרים/כמות (שלח הודעה רגילה) כדי להשלים "
                        "למינימום. אם לא, ההזמנה תבוטל אוטומטית."
                    )
                    validators.send_whatsapp_message(customer_phone, "\n".join(lines))
                else:
                    # No way to reach the customer to ask for a top-up —
                    # can't hold this open indefinitely, so fall back to
                    # cancelling rather than silently dispatching under-minimum.
                    OrderRequest.objects.get(id=order_request_id).transition_to(OrderRequest.Status.CANCELLED)
            else:
                # Nothing could be rerouted at all — cancel the order.
                OrderRequest.objects.get(id=order_request_id).transition_to(OrderRequest.Status.CANCELLED)
                if customer_phone:
                    validators.send_whatsapp_message(
                        customer_phone,
                        f"❌ *{supplier.name}* ביטל את הזמנה #{order_request_id}.\n"
                        "לא נמצא ספק חלופי. ניתן ליצור הזמנה חדשה דרך המערכת.",
                    )
        except Exception as exc:
            logger.error("Failed to handle cancellation for order %s: %s", order_request_id, exc)

        return HttpResponse(status=200)

    if not confirmed and not missing:
        validators.send_whatsapp_message(
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
                OrderRequest.objects.get(id=order_request_id).transition_to(OrderRequest.Status.APPROVED)
        except Exception as exc:
            logger.error("Failed to update order status after supplier confirmation: %s", exc)

    # A product neither confirmed nor reported missing - e.g. a shortage
    # report with no confirm word in the same message ("אין תירס ואבוקדו",
    # no "שאר אישור") - used to just vanish: has_general_confirm was False,
    # so it was never in `confirmed`, and the pending cache got cleared
    # unconditionally below regardless, leaving no way to ever confirm it
    # afterward. Found live: an order stuck on SENT forever with most of its
    # items silently unaddressed, no error, no sign anything was wrong.
    missing_ids = {p["orp_id"] for p in missing}
    unaddressed = [p for p in products if p["orp_id"] not in confirmed and p["orp_id"] not in missing_ids]
    if unaddressed:
        # Keep the pending state alive - trimmed to just what's left - so a
        # follow-up reply only needs to address the remainder, not repeat
        # confirming what's already done.
        save_supplier_pending_order(
            supplier_phone=phone, order_request_id=order_request_id, products=unaddressed,
        )
    else:
        cache.delete(key)

    # Parse optional cutoff time
    cutoff_time = _parse_supplier_cutoff(body)
    if cutoff_time:
        cutoff_str = cutoff_time.strftime("%H:%M")
        cache.set(f"supplier_cutoff:{phone}:{order_request_id}", cutoff_str, timeout=CUTOFF_TTL)

    # Acknowledge supplier. Used to open with "תודה! קיבלתי:", which read as
    # if the goods had arrived and didn't say which order was being confirmed.
    ack_lines = [f"✅ אישור הזמנה #{order_request_id} נקלט:"]
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
        else:
            # Neither confirmed nor reported missing — used to just vanish
            # from the ack entirely, with no way to confirm it afterward
            # (the pending cache was always cleared below regardless). See
            # the `unaddressed` handling above.
            ack_lines.append(f"  ⏳ {p['product_name']} — טרם אושר")
    if missing:
        ack_lines.append(
            "\n🚫 סימנו את המוצרים החסרים כלא זמינים אצלך — הם לא יוצעו בהזמנות חדשות. "
            "שלח לנו מחיר מעודכן כשהם חוזרים למלאי."
        )
    if unaddressed:
        unaddressed_names = ", ".join(p["product_name"] for p in unaddressed)
        ack_lines.append(
            f"\n❓ עדיין לא ענית לגבי: {unaddressed_names}. שלח *אישור* לאישור שלהם, "
            f"או *חסר {unaddressed[0]['product_name']}* אם משהו מהם לא זמין."
        )
    if cutoff_time:
        ack_lines.append(f"\n⏰ שינויים מתקבלים עד {cutoff_time.strftime('%H:%M')}")
    if eta_time:
        ack_lines.append(f"\n🚚 עדכנו את הלקוח שההזמנה צפויה להגיע עד {eta_time.strftime('%H:%M')}")
    if not missing and not unaddressed and not partial_products:
        # Nothing left to do on this order right now — let the supplier know
        # the "shipped" update exists at all; SHIPPING_KEYWORDS is otherwise
        # a completely undocumented feature nobody would discover on their own.
        ack_lines.append('\n📦 כשההזמנה יוצאת אליכם לדרך, אפשר לשלוח "יצא למשלוח" ונעדכן את הלקוח.')
    validators.send_whatsapp_message(phone, "\n".join(ack_lines))

    # Notify customer about confirmed items
    try:
        orp = OrderRequestProduct.objects.select_related(
            "order_request__user__profile"
        ).filter(order_request_id=order_request_id).first()

        if orp and confirmed:
            customer_profile = getattr(orp.order_request.user, "profile", None)
            customer_phone = (
                validators._local_to_e164(customer_profile.phone)
                if customer_profile and customer_profile.phone else None
            )
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
                if eta_time:
                    customer_lines.append(f"\n🕐 צפוי להגיע עד השעה {eta_time.strftime('%H:%M')}")
                customer_lines.append(f"\nמספר הזמנה: #{order_request_id}")
                validators.send_whatsapp_message(customer_phone, "\n".join(customer_lines))
    except Exception as exc:
        logger.error("Failed to notify customer after supplier confirmation: %s", exc)

    # Find fallback suppliers for missing/partial items and notify customer
    if missing or partial_products:
        _handle_missing_items(supplier, missing, order_request_id, partial_products=partial_products)

    return HttpResponse(status=200)
