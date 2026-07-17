import json
import logging
import re
from collections import defaultdict
from datetime import time as dtime
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone

from .cache import save_supplier_pending_order, CUTOFF_TTL
from .fallback_flow import _handle_missing_items, _recalculate_order_total
from .validators import send_whatsapp_message

logger = logging.getLogger(__name__)

MISSING_KEYWORDS = ["חסר", "אין", "נגמר", "אזל", "לא קיים"]


def notify_suppliers_for_order(order) -> None:
    """Send WhatsApp to every supplier in an order, save pending state, and mark order SENT.

    Groups by phone number so suppliers sharing a number (e.g. in testing) get one combined
    message instead of multiple separate ones.
    """
    from apps.orders.models import OrderRequest

    profile = getattr(order.user, "profile", None)
    company_name = profile.company_name if profile else ""
    company_address = profile.company_address if profile else ""
    company_phone = profile.company_phone if profile else ""

    by_phone = defaultdict(list)
    for orp in order.products.select_related("product", "supplier").all():
        by_phone[orp.supplier.whatsapp_number].append(orp)

    for phone, items in by_phone.items():
        lines = [f"שלום, *{company_name}* מבקש להזמין:"]
        for item in items:
            lines.append(f"- {item.product.name} x{item.quantity} {item.product.get_unit_display()}")
        if company_address:
            lines.append(f"\n📍 *כתובת למשלוח:* {company_address}")
        if company_phone:
            lines.append(f"📞 {company_phone}")
        lines.append("\nענה:\n• *אישור* — לאישור הכל\n• *חסר [שם מוצר]* — אם פריט לא זמין\n• *ביטול* — לביטול ההזמנה")
        send_whatsapp_message(phone, "\n".join(lines))

        save_supplier_pending_order(
            supplier_phone=phone,
            order_request_id=order.id,
            products=[
                {
                    "orp_id": item.id,
                    "product_name": item.product.name,
                    "quantity": str(item.quantity),
                    "unit": item.product.get_unit_display(),
                }
                for item in items
            ],
        )

    order.status = OrderRequest.Status.SENT
    order.save(update_fields=["status"])


def send_order_to_supplier(supplier, assignments: list) -> str:
    lines = ["שלום, ברצוני להזמין:"]
    for a in assignments:
        lines.append(
            f"- {a['product'].name} x{a['quantity']} {a['product'].get_unit_display()}"
        )
    lines.append("תודה!")
    body = "\n".join(lines)
    return send_whatsapp_message(supplier.whatsapp_number, body)


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
