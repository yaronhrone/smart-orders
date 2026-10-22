"""
Changing or cancelling the customer's open order from the website or the MCP server.

The rules are the ones the WhatsApp flow already applies (apps.orders.whatsapp.user_flow):
what a supplier may still be asked, minimums, approved lines, the supplier messages. This module
only adds the entry point that has no phone number in the loop, plus a dry run that shows what
would happen and leaves no trace: the real logic runs inside a transaction that is rolled back.
"""
from django.db import transaction

from .models import OrderRequest
from .services import checkout_lock, get_open_batch, todays_batch

CANCELLABLE = (OrderRequest.Status.PENDING, OrderRequest.Status.SENT, OrderRequest.Status.APPROVED)


class NoOpenOrder(Exception):
    """The customer has no order that can be changed (or cancelled) right now."""


def change_open_order(user, items: list, *, dry_run: bool) -> dict:
    """
    items: [{"product_name": exact catalog name, "quantity": Decimal, "intent": "add" | "reduce" | "update"}].
    Returns what was (or, in a dry run, would be) done. Raises NoOpenOrder; any exception while telling a
    supplier rolls the whole change back (same contract as WhatsApp).
    """
    from .whatsapp.user_flow import _apply_items, _dispatch_modification_batches

    changes_made, errors, blocked, order_changes = [], [], [], {}
    with checkout_lock(user):
        batch = get_open_batch(user)
        if batch is None:
            raise NoOpenOrder()
        profile = getattr(user, "profile", None)
        region = profile.region if profile else "center"
        company = profile.company_name if profile else ""
        address = profile.company_address if profile else ""
        with transaction.atomic():
            _apply_items(None, batch, region, items, "add", order_changes, changes_made, errors, blocked)
            if dry_run:
                transaction.set_rollback(True)
            elif changes_made or errors or blocked:
                _dispatch_modification_batches(order_changes, company, address)
    return {
        "batch_id": batch.id,
        "changes": changes_made,
        "not_found": errors,
        "blocked": blocked,
        "applied": bool(changes_made) and not dry_run,
        "needs_supplier_answer": any(e["needs_confirmation"] for e in order_changes.values()),
    }


def cancel_open_order(user, *, dry_run: bool) -> dict:
    """Cancel every order of today's checkout that has not shipped, and tell the suppliers who had it."""
    from .whatsapp import validators
    from .whatsapp.user_flow import cancel_batch_orders

    with checkout_lock(user):
        batch = todays_batch(user)
        if batch is None:
            raise NoOpenOrder()
        orders = list(batch.orders.exclude(status=OrderRequest.Status.CANCELLED).select_related("supplier").order_by("id"))
        cancellable = [o for o in orders if o.status in CANCELLABLE]
        kept = [o for o in orders if o.status not in CANCELLABLE]

        def row(order):
            return {
                "order_id": order.id, "supplier": order.supplier.name, "status": order.status,
                "total_price": f"{order.total_price:.2f}",
            }

        result = {
            "batch_id": batch.id,
            "orders": [row(o) for o in cancellable],
            "already_shipped": [row(o) for o in kept],
            "cancelled": False,
        }
        if dry_run or not cancellable:
            return result

        profile = getattr(user, "profile", None)
        phone = validators._local_to_e164(profile.phone) if profile and profile.phone else None
        cancelled, told = cancel_batch_orders(batch, phone)
        result["cancelled"] = True
        result["suppliers_notified"] = [o.supplier.name for o in told]
        return result
