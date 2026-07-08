import json
import logging
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from twilio.rest import Client

logger = logging.getLogger(__name__)

SUPPLIER_SESSION_TTL = 86400  # 24 שעות


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def send_whatsapp_message(to_number: str, body: str) -> str:
    """
    Send a WhatsApp message via Twilio Sandbox.
    to_number: Israeli format, e.g. "+972501234567"
    Returns the Twilio message SID.
    If WHATSAPP_OVERRIDE_NUMBER is set, all messages are redirected there (for testing).
    """
    override = getattr(settings, "WHATSAPP_OVERRIDE_NUMBER", None)
    actual_to = override if override else to_number
    if override and override != to_number:
        body = f"[בדיקה — מיועד ל: {to_number}]\n\n{body}"

    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    from_wa = f"whatsapp:{settings.TWILIO_WHATSAPP_NUMBER}"
    to_wa = f"whatsapp:{actual_to}"
    message = client.messages.create(body=body, from_=from_wa, to=to_wa)
    return message.sid


def save_supplier_pending_order(supplier_phone: str, order_request_id: int, products: list):
    """
    Cache a pending supplier order so the webhook can match the reply.
    products: list of dicts with keys: orp_id, product_name, quantity, unit
    """
    key = f"whatsapp_supplier_pending:{supplier_phone}"
    data = {"order_request_id": order_request_id, "products": products}
    cache.set(key, json.dumps(data, cls=DecimalEncoder), timeout=SUPPLIER_SESSION_TTL)


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
