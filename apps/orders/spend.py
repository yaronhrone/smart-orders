from django.db.models import DecimalField, ExpressionWrapper, F

from apps.orders.models import OrderRequest, OrderRequestProduct

# Pending (never sent to a supplier) and cancelled orders were never spent.
SPEND_STATUSES = (
    OrderRequest.Status.SENT,
    OrderRequest.Status.APPROVED,
    OrderRequest.Status.SHIPPED,
    OrderRequest.Status.DELIVERED,
)

LINE_TOTAL = ExpressionWrapper(
    F("quantity") * F("unit_price"),
    output_field=DecimalField(max_digits=14, decimal_places=2),
)


def spend_lines(user=None):
    """Order lines that count as spend. user=None means every customer (admin)."""
    lines = OrderRequestProduct.objects.filter(order_request__status__in=SPEND_STATUSES)
    if user is not None:
        lines = lines.filter(order_request__user=user)
    return lines
