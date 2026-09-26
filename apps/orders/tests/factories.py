from apps.orders.models import OrderBatch, OrderRequest


def make_order(user, supplier, batch=None, **fields):
    """
    One order = one supplier, always inside a checkout batch. Pass `batch`
    to add a sibling order (another supplier) to the same checkout.
    """
    if batch is None:
        batch = OrderBatch.objects.create(user=user)
    return OrderRequest.objects.create(user=user, batch=batch, supplier=supplier, **fields)
