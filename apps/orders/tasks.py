import json
import logging
from collections import defaultdict

from celery import shared_task

from apps.orders.whatsapp import send_whatsapp_message, save_supplier_pending_order

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def send_daily_supplier_orders_task(self):
    """
    Runs daily: for each user's primary shopping list, builds the cheapest order
    (quantities x2), sends WhatsApp to each supplier, and caches the order so
    suppliers can confirm via the webhook.
    """
    from apps.orders.models import ShoppingList
    from apps.orders.services import build_order

    sent_count = 0
    error_count = 0

    primary_lists = (
        ShoppingList.objects
        .filter(is_primary=True)
        .select_related("user__profile")
        .prefetch_related("products__product")
    )

    for shopping_list in primary_lists:
        user = shopping_list.user
        if not hasattr(user, "profile"):
            logger.warning("User %s has no profile, skipping daily order", user.id)
            continue

        products = [
            {"product": slp.product, "quantity": slp.default_quantity * 2}
            for slp in shopping_list.products.all()
        ]
        if not products:
            continue

        try:
            order, _ = build_order(
                user=user,
                region=user.profile.region,
                products=products,
                scenario="cheapest",
            )

            # Group order items by supplier and send one message per supplier
            by_supplier = defaultdict(list)
            for orp in order.products.select_related("product", "supplier").all():
                by_supplier[orp.supplier].append(orp)

            for supplier, items in by_supplier.items():
                lines = ["שלום, ברצוני להזמין:"]
                for item in items:
                    lines.append(
                        f"- {item.product.name} x{item.quantity} {item.product.get_unit_display()}"
                    )
                lines.append("\nאנא ענה *אישור* לאישור הכל, או שלח כמויות מעודכנות.")
                send_whatsapp_message(supplier.whatsapp_number, "\n".join(lines))

                # Cache so the supplier's reply can be matched
                save_supplier_pending_order(
                    supplier_phone=supplier.whatsapp_number,
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
                sent_count += 1

        except Exception as exc:
            error_count += 1
            logger.error(
                "Daily order failed for user %s (list %s): %s",
                user.id, shopping_list.id, exc,
            )

    logger.info("send_daily_supplier_orders_task done: %d sent, %d errors", sent_count, error_count)
    return {"sent": sent_count, "errors": error_count}


@shared_task
def handle_fallback_timeout(phone: str, order_request_id: int):
    """
    Fires FALLBACK_TTL seconds after presenting a fallback offer to the customer.
    If the customer still hasn't responded, auto-remove the unconfirmed items.
    Edge case 4: cache expiry → mark as unconfirmed and remove items.
    """
    from django.core.cache import cache
    from apps.orders.models import OrderRequestProduct
    from apps.orders.whatsapp_webhook import _recalculate_order_total, DecimalEncoder, FALLBACK_TTL

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
