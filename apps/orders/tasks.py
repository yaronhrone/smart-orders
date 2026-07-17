import json
import logging

from celery import shared_task

from apps.orders.whatsapp import send_whatsapp_message

logger = logging.getLogger(__name__)


@shared_task
def handle_fallback_timeout(phone: str, order_request_id: int):
    """
    Fires FALLBACK_TTL seconds after presenting a fallback offer to the customer.
    If the customer still hasn't responded, auto-remove the unconfirmed items.
    Edge case 4: cache expiry → mark as unconfirmed and remove items.
    """
    from django.core.cache import cache
    from apps.orders.models import OrderRequestProduct
    from apps.orders.whatsapp import _recalculate_order_total, DecimalEncoder, FALLBACK_TTL

    raw = cache.get(f"whatsapp_fallback:{phone}")
    if not raw:
        # Customer already responded — nothing to do
        return

    import json
    state = json.loads(raw)
    cache.delete(f"whatsapp_fallback:{phone}")

    removed = []
    for r in state.get("redirects", []):
        if r.get("type") == "partial":
            # Original ORP was already reduced to confirmed_qty; just skip the new ORP creation
            removed.append(f"{r['product_name']} (כמות חלקית — {r['quantity']} {r.get('unit', '')} לא הוזמנה)")
        else:
            try:
                orp = OrderRequestProduct.objects.get(id=r["orp_id"])
                orp.delete()
                removed.append(f"{r['product_name']} x{r['quantity']} {r.get('unit', '')}")
            except OrderRequestProduct.DoesNotExist:
                pass

    _recalculate_order_total(order_request_id)

    if removed:
        lines = ["⏰ פג הזמן לאישור הספק החלופי. הפריטים הבאים הוסרו מההזמנה:"]
        for item in removed:
            lines.append(f"  • {item}")
        lines.append("\nצור קשר עם המערכת אם ברצונך להוסיפם מחדש.")
        send_whatsapp_message(phone, "\n".join(lines))
