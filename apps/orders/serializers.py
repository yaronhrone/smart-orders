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
# Output — market comparison
# ---------------------------------------------------------------------------
class MarketComparisonItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)
    our_unit_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    market_unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    our_subtotal = serializers.DecimalField(max_digits=10, decimal_places=2)
    market_subtotal = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    savings = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
class MarketComparisonSerializer(serializers.Serializer):
    products = MarketComparisonItemSerializer(many=True)
    our_total = serializers.DecimalField(max_digits=10, decimal_places=2)
    market_total = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    total_savings = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
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
    market_comparison = MarketComparisonSerializer()
    minimum_issues = MinimumIssuesByScenarioSerializer()

# ---------------------------------------------------------------------------
# Output — place endpoint
# ---------------------------------------------------------------------------

class WhatsAppLinkSerializer(serializers.Serializer):
    supplier_id = serializers.IntegerField()
    supplier_name = serializers.CharField()
    phone = serializers.CharField()
    whatsapp_url = serializers.CharField()


class PlaceOrderResponseSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    status = serializers.CharField()
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    scenario = serializers.CharField()
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
    id = serializers.IntegerField()
    status = serializers.CharField()
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    created_at = serializers.DateTimeField()
    products = OrderItemDetailSerializer(many=True)


class OrderListSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    created_at = serializers.DateTimeField()
    product_count = serializers.IntegerField()

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
