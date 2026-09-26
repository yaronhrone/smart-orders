from decimal import Decimal
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers
from apps.catalog.models import Product
from apps.orders.models import OrderRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class HebrewProductField(serializers.SlugRelatedField):
    """SlugRelatedField that returns a Hebrew error when a product is not found."""

    def to_internal_value(self, data):
        try:
            return self.get_queryset().get(**{self.slug_field: data})
        except ObjectDoesNotExist:
            raise serializers.ValidationError(f'המוצר {data} לא נמצא במערכת')
        except (TypeError, ValueError):
            raise serializers.ValidationError(f'ערך לא תקין: {data}')


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

class OrderItemInputSerializer(serializers.Serializer):
    product_name = HebrewProductField(
        queryset=Product.objects.all(),
        slug_field="name",
        source="product",
    )
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
class SuggestOrderInputSerializer(serializers.Serializer):

    products = OrderItemInputSerializer(many=True, min_length=1)
class PlaceOrderInputSerializer(serializers.Serializer):
    scenario = serializers.ChoiceField(
        choices=["cheapest", "fewest_suppliers"],
        default="cheapest",
    )
    products = OrderItemInputSerializer(many=True, min_length=1)
# ---------------------------------------------------------------------------
# Output — scenario
# ---------------------------------------------------------------------------

class ScenarioItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    unit = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2)
    supplier_id = serializers.IntegerField()
    supplier_name = serializers.CharField()
class ScenarioSerializer(serializers.Serializer):
    scenario = serializers.CharField()
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    supplier_count = serializers.IntegerField()
    products = ScenarioItemSerializer(many=True)
# ---------------------------------------------------------------------------
# Output — suggest endpoint
# ---------------------------------------------------------------------------
class MinimumIssueSerializer(serializers.Serializer):
    supplier_id = serializers.IntegerField()
    supplier_name = serializers.CharField()
    current_total = serializers.DecimalField(max_digits=10, decimal_places=2)
    minimum_required = serializers.DecimalField(max_digits=10, decimal_places=2)
    missing_amount = serializers.DecimalField(max_digits=10, decimal_places=2)


class MinimumIssuesByScenarioSerializer(serializers.Serializer):
    cheapest = MinimumIssueSerializer(many=True)
    fewest_suppliers = MinimumIssueSerializer(many=True)


class SuggestOrderResponseSerializer(serializers.Serializer):
    cheapest = ScenarioSerializer()
    fewest_suppliers = ScenarioSerializer()
    minimum_issues = MinimumIssuesByScenarioSerializer()
    # The single scenario to offer ("cheapest" | "fewest_suppliers"), or
    # null when none clears every supplier's minimum.
    recommended = serializers.CharField(allow_null=True)

# ---------------------------------------------------------------------------
# Output — place endpoint
# ---------------------------------------------------------------------------

class WhatsAppLinkSerializer(serializers.Serializer):
    supplier_id = serializers.IntegerField()
    supplier_name = serializers.CharField()
    phone = serializers.CharField()
    whatsapp_url = serializers.CharField()


class PlacedOrderSerializer(serializers.Serializer):
    order_id = serializers.IntegerField(source="id")
    supplier_id = serializers.IntegerField(source="supplier.id")
    supplier_name = serializers.CharField(source="supplier.name")
    status = serializers.CharField()
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)


class PlaceOrderResponseSerializer(serializers.Serializer):
    """One checkout = one batch = one order per supplier."""
    batch_id = serializers.IntegerField()
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    scenario = serializers.CharField()
    orders = PlacedOrderSerializer(many=True)
    whatsapp_links = WhatsAppLinkSerializer(many=True)

# ---------------------------------------------------------------------------
# Output — order list / detail
# ---------------------------------------------------------------------------

class OrderItemDetailSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(source="product.id")
    product_name = serializers.CharField(source="product.name")
    supplier_id = serializers.IntegerField(source="supplier.id")
    supplier_name = serializers.CharField(source="supplier.name")
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)
    unit_display = serializers.SerializerMethodField()
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2)

    def get_unit_display(self, obj):
        return obj.product.get_unit_display()


class OrderDetailSerializer(serializers.Serializer):
    """One order = one supplier. `batch_id`/`batch_created_at` link it back to
    the checkout it was placed in, alongside its sibling orders."""
    id = serializers.IntegerField()
    status = serializers.CharField()
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    created_at = serializers.DateTimeField()
    supplier_id = serializers.IntegerField()
    supplier_name = serializers.CharField()
    batch_id = serializers.IntegerField()
    batch_created_at = serializers.DateTimeField()
    products = OrderItemDetailSerializer(many=True)


# ---------------------------------------------------------------------------
# Output — checkout batches (dashboard lists)
# ---------------------------------------------------------------------------

# How far along an order is — a batch's overall status is its LEAST advanced
# live order ("the checkout is only as done as its slowest supplier").
STATUS_PROGRESS = [
    OrderRequest.Status.PENDING,
    OrderRequest.Status.SENT,
    OrderRequest.Status.APPROVED,
    OrderRequest.Status.SHIPPED,
    OrderRequest.Status.DELIVERED,
]


def batch_overall_status(orders):
    live = [o.status for o in orders if o.status != OrderRequest.Status.CANCELLED]
    if not live:
        return OrderRequest.Status.CANCELLED
    return min(live, key=STATUS_PROGRESS.index)


class BatchOrderSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    supplier_id = serializers.IntegerField(source="supplier.id")
    supplier_name = serializers.CharField(source="supplier.name")
    status = serializers.CharField()
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    product_count = serializers.IntegerField()


class OrderBatchSerializer(serializers.Serializer):
    """
    A checkout for the dashboard list: one row per batch, expanding into its
    per-supplier orders. Expects `orders` prefetched with `supplier` and an
    annotated `product_count` (see views._batches_queryset).
    """
    id = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    total_price = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    orders = BatchOrderSummarySerializer(many=True)

    def get_total_price(self, batch):
        total = sum(
            (o.total_price for o in batch.orders.all() if o.status != OrderRequest.Status.CANCELLED),
            Decimal(0),
        )
        return f"{total:.2f}"

    def get_status(self, batch):
        return batch_overall_status(batch.orders.all())


class AdminOrderBatchSerializer(OrderBatchSerializer):
    """Same as OrderBatchSerializer plus who placed it — admin sees every customer."""
    customer_email = serializers.EmailField(source="user.email")
    company_name = serializers.SerializerMethodField()

    def get_company_name(self, batch):
        profile = getattr(batch.user, "profile", None)
        return profile.company_name if profile else ""

# ---------------------------------------------------------------------------
# Input — status update
# ---------------------------------------------------------------------------

class OrderStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=OrderRequest.Status.choices)


# ---------------------------------------------------------------------------
# Output — stats endpoint
# ---------------------------------------------------------------------------

class SupplierSpendingSerializer(serializers.Serializer):
    supplier_id = serializers.IntegerField()
    supplier_name = serializers.CharField()
    total_spent = serializers.DecimalField(max_digits=12, decimal_places=2)
    order_count = serializers.IntegerField()


class OrderStatsSerializer(serializers.Serializer):
    total_spent = serializers.DecimalField(max_digits=12, decimal_places=2)
    order_count = serializers.IntegerField()
    by_supplier = SupplierSpendingSerializer(many=True)
