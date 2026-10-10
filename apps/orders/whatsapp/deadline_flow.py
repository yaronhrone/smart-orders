import logging
from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from apps.orders.services import IL_TZ
from . import validators
from .cache import clear_supplier_pending_for_order

logger = logging.getLogger(__name__)

CONFIRMATION_DEADLINE = time(18, 0)
MIN_CONFIRMATION_WINDOW = timedelta(hours=2)

_OUTCOME_TEXT = {
    "dispatched": "הועברה לספק אחר",
    "grace": "מחכה שהלקוח ישלים מינימום אצל ספק חלופי",
    "cancelled": "בוטלה (אין ספק חלופי)",
}


def confirmation_deadline(sent_at):
    """18:00 Israel time on the day it was sent, but never less than 2 hours after sending."""
    local = sent_at.astimezone(IL_TZ)
    at_cutoff = datetime.combine(local.date(), CONFIRMATION_DEADLINE, tzinfo=IL_TZ)
    return max(at_cutoff, local + MIN_CONFIRMATION_WINDOW)


def overdue_orders(now=None):
    """
    SENT orders past their deadline that the job hasn't handled yet. Only
    orders sent today or yesterday (Israel time): older stuck orders are
    left alone instead of all being rerouted at once.
    """
    from apps.orders.models import OrderRequest

    now = now or timezone.now()
    yesterday = now.astimezone(IL_TZ).date() - timedelta(days=1)
    candidates = (
        OrderRequest.objects
        .filter(
            status=OrderRequest.Status.SENT,
            deadline_handled_at__isnull=True,
            created_at__gte=datetime.combine(yesterday, time.min, tzinfo=IL_TZ),
        )
        .select_related("supplier", "user__profile")
    )
    return [order for order in candidates if confirmation_deadline(order.created_at) <= now]


def handle_overdue_orders(now=None) -> int:
    """Reroute every overdue order (see handle_overdue_order). Returns how many were handled."""
    handled = 0
    for order in overdue_orders(now):
        try:
            if handle_overdue_order(order, now):
                handled += 1
        except Exception:
            logger.exception("Failed to handle overdue order %s", order.id)
    return handled


def handle_overdue_order(order, now=None) -> bool:
    """
    The supplier never confirmed in time: treat it like a cancellation
    (process_reroute picks the next supplier, holds it for a minimum top-up,
    or tells the customer there's no supplier), without the 10-day block a
    real cancellation gets. An order the supplier partly confirmed was
    answered, so it's only flagged to the admin. Returns False if another
    run already took this order.
    """
    from apps.orders.models import OrderRequest, SupplierConfirmation
    from .supplier_flow import process_reroute

    now = now or timezone.now()
    claimed = OrderRequest.objects.filter(pk=order.pk, deadline_handled_at__isnull=True).update(
        deadline_handled_at=now,
    )
    if not claimed:
        return False

    supplier = order.supplier
    deadline = confirmation_deadline(order.created_at).strftime("%H:%M")
    unconfirmed = list(
        order.products.filter(confirmations__isnull=True).select_related("product").distinct()
    )
    if SupplierConfirmation.objects.filter(order_request_product__order_request=order).exists():
        names = ", ".join(item.product.name for item in unconfirmed)
        _notify_admin(
            f"⚠️ *{supplier.name}* אישר רק חלק מהזמנה #{order.id} עד {deadline}.\n"
            f"לא אושרו: {names}\nההזמנה לא הועברה אוטומטית. כדאי לבדוק מולו."
        )
        return True

    clear_supplier_pending_for_order(supplier.whatsapp_number, order.id)
    validators.send_whatsapp_message(
        supplier.whatsapp_number,
        f"⏰ הזמנה #{order.id} בוטלה כי לא אושרה עד {deadline}. אין צורך לספק אותה.",
    )

    profile = getattr(order.user, "profile", None)
    customer_phone = validators._local_to_e164(profile.phone) if profile and profile.phone else None
    outcome = process_reroute(
        order.id, customer_phone,
        header_lines=[f"⏰ *{supplier.name}* לא אישר את הזמנה #{order.id} עד {deadline}, אז העברנו אותה."],
    )
    _notify_admin(
        f"⏰ *{supplier.name}* לא אישר את הזמנה #{order.id} עד {deadline}.\n"
        f"📞 {supplier.phone}\n"
        f"ההזמנה {_OUTCOME_TEXT.get(outcome, outcome)}. הספק לא נחסם."
    )
    return True


def _notify_admin(text: str) -> None:
    admin_number = getattr(settings, "ADMIN_WHATSAPP_NUMBER", "")
    if not admin_number:
        logger.warning("ADMIN_WHATSAPP_NUMBER is not set; skipped a supplier-deadline alert")
        return
    try:
        validators.send_whatsapp_message(admin_number, text)
    except Exception as exc:
        logger.error("Failed to send supplier-deadline alert to admin: %s", exc)
