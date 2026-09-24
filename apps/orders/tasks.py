import json
import logging

from celery import shared_task

from apps.orders.whatsapp import send_whatsapp_message

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_supplier_order_notification_task(self, phone: str, message: str):
    """Send a supplier its order-notification WhatsApp message, retrying on failure."""
    try:
        send_whatsapp_message(phone, message)
    except Exception as exc:
        logger.error(
            "Failed to notify supplier %s of new order (attempt %s/%s): %s",
            phone, self.request.retries + 1, self.max_retries, exc,
        )
        raise self.retry(exc=exc)


@shared_task
def dispatch_draft_order_task(phone: str, generation: int, is_grace_retry: bool = False):
    """
    Fires DRAFT_DEBOUNCE_SECONDS after a customer's order-building message —
    or MINIMUM_GRACE_SECONDS later, with is_grace_retry=True, when the last
    attempt didn't clear a supplier's minimum and was held open for a top-up
    instead of dropped (see _suggest_and_respond's minimum-issues branch).
    If a newer message already bumped the generation, this run is stale —
    the newer message scheduled its own task with a fresh countdown, so
    doing nothing here is correct, not a missed dispatch.
    """
    from apps.orders.whatsapp.cache import get_draft_order, clear_draft_order
    from apps.orders.whatsapp.user_flow import (
        _handle_ambiguous_products, _resolve_profile, _suggest_and_respond,
    )

    draft = get_draft_order(phone)
    if not draft or draft["generation"] != generation:
        return

    clear_draft_order(phone)

    if draft.get("ambiguous"):
        # A message inside the debounce window named a product family
        # ("בצל", "תפוח אדמה") without a variant — held in the draft instead
        # of interrupting it (see save_draft_order). Ask about it now, once
        # the window has closed, against the fully-merged basket.
        _handle_ambiguous_products(phone, draft["ambiguous"], draft["items"])
        return

    profile = _resolve_profile(phone)
    if not profile:
        return
    _suggest_and_respond(phone, profile.user, profile, draft["items"], is_grace_retry=is_grace_retry)


@shared_task
def handle_reroute_grace_timeout(phone: str, order_request_id: int):
    """
    Fires MINIMUM_GRACE_SECONDS after a post-cancellation reroute was held
    open because it would've left a supplier under their minimum. If the
    customer never topped up enough to clear it, give up: cancel the order
    (the items are still sitting on the now-blocked cancelling supplier —
    nothing to move back, just drop the order like the no-fallback-at-all
    case) and clear the grace state.
    """
    import json as _json
    from django.core.cache import cache
    from apps.orders.models import OrderRequest
    from apps.orders.whatsapp.cache import _get_reroute_grace_state, _clear_reroute_grace_state

    raw = _get_reroute_grace_state(phone)
    if not raw:
        return  # already resolved (topped up, or a newer grace state took over)
    state = _json.loads(raw)
    if state.get("order_request_id") != order_request_id:
        return  # stale — a newer cancellation on a different order replaced this one

    _clear_reroute_grace_state(phone)
    try:
        order = OrderRequest.objects.get(id=order_request_id)
        order.transition_to(OrderRequest.Status.CANCELLED)
    except OrderRequest.DoesNotExist:
        return

    send_whatsapp_message(
        phone,
        f"⏰ פג הזמן להשלמת המינימום להזמנה #{order_request_id}. ההזמנה בוטלה.\n"
        "ניתן ליצור הזמנה חדשה דרך המערכת.",
    )


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
