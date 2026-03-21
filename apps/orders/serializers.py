from decimal import Decimal
from rest_framework import serializers
from apps.catalog.models import Product, Region
from apps.orders.models import OrderRequest, ShoppingList, ShoppingListItem


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

class OrderItemInputSerializer(serializers.Serializer):
    product_name = serializers.SlugRelatedField(
        queryset=Product.objects.all(),
        slug_field="name",
        source="product",
    )
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))


class SuggestOrderInputSerializer(serializers.Serializer):

    items = OrderItemInputSerializer(many=True, min_length=1)


class PlaceOrderInputSerializer(serializers.Serializer):
    region = serializers.ChoiceField(choices=Region.choices)
    scenario = serializers.ChoiceField(
        choices=["cheapest", "fewest_suppliers"],
        default="cheapest",
    )
    items = OrderItemInputSerializer(many=True, min_length=1)


# ---------------------------------------------------------------------------
# Output — scenario
# ---------------------------------------------------------------------------

class ScenarioItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2)
    supplier_id = serializers.IntegerField()
    supplier_name = serializers.CharField()


class ScenarioSerializer(serializers.Serializer):
    scenario = serializers.CharField()
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    supplier_count = serializers.IntegerField()
    items = ScenarioItemSerializer(many=True)


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
    items = MarketComparisonItemSerializer(many=True)
    our_total = serializers.DecimalField(max_digits=10, decimal_places=2)
    market_total = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    total_savings = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)


# ---------------------------------------------------------------------------
# Output — suggest endpoint
# ---------------------------------------------------------------------------

class SuggestOrderResponseSerializer(serializers.Serializer):
    cheapest = ScenarioSerializer()
    fewest_suppliers = ScenarioSerializer()
    market_comparison = MarketComparisonSerializer()


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
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2)


class OrderDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    created_at = serializers.DateTimeField()
    items = OrderItemDetailSerializer(many=True)


class OrderListSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    created_at = serializers.DateTimeField()
    item_count = serializers.IntegerField()


# ---------------------------------------------------------------------------
# Input — status update
# ---------------------------------------------------------------------------

class OrderStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=OrderRequest.Status.choices)


# ---------------------------------------------------------------------------
# Shopping List
# ---------------------------------------------------------------------------

class ShoppingListItemSerializer(serializers.ModelSerializer):
    product_name = serializers.SlugRelatedField(
        queryset=Product.objects.all(),
        slug_field="name",
        source="product",
    )

    class Meta:
        model = ShoppingListItem
        fields = ("id", "product_name", "default_quantity")


class ShoppingListSerializer(serializers.ModelSerializer):
    items = ShoppingListItemSerializer(many=True)

    class Meta:
        model = ShoppingList
        fields = ("id", "name", "created_at", "items")
        read_only_fields = ("id", "created_at")

    def create(self, validated_data):
        items_data = validated_data.pop("items")
        shopping_list = ShoppingList.objects.create(**validated_data)
        ShoppingListItem.objects.bulk_create([
            ShoppingListItem(
                shopping_list=shopping_list,
                product=item["product"],
                default_quantity=item["default_quantity"],
            )
            for item in items_data
        ])
        return shopping_list

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        instance.name = validated_data.get("name", instance.name)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            ShoppingListItem.objects.bulk_create([
                ShoppingListItem(
                    shopping_list=instance,
                    product=item["product"],
                    default_quantity=item["default_quantity"],
                )
                for item in items_data
            ])
        return instance


class ShoppingListSuggestSerializer(serializers.Serializer):
    """Input for suggesting an order directly from a shopping list."""
    region = serializers.ChoiceField(choices=Region.choices)
