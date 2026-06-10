from rest_framework import serializers
from .models import Product, Supplier, SupplierProduct, MarketPrice, Unit



class ProductSerializer(serializers.ModelSerializer):
    unit_display = serializers.CharField(source="get_unit_display", read_only=True)

    class Meta:
        model = Product
        fields = ("id", "name", "unit", "unit_display")


class SupplierProductSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source="product.id", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    unit = serializers.CharField(source="product.get_unit_display", read_only=True)

    class Meta:
        model = SupplierProduct
        fields = ("product_id", "product_name", "unit","price_per_unit", "updated_at")


class SupplierProductWriteSerializer(serializers.Serializer):
    """Used when creating a supplier with initial prices."""
    product_name = serializers.CharField()
    price_per_unit = serializers.DecimalField(max_digits=10, decimal_places=2)
    unit = serializers.ChoiceField(choices=Unit.choices, default=Unit.KG)


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ("id", "name", "phone", "whatsapp_number", "region", "minimum_order")
        extra_kwargs = {
            "name": {"error_messages": {"unique": "ספק עם שם זה כבר קיים במערכת."}},
            "phone": {"error_messages": {"unique": "מספר הטלפון כבר קיים במערכת."}},
            "whatsapp_number": {"error_messages": {"unique": "מספר הוואטסאפ כבר קיים במערכת."}},
        }

    def validate_phone(self, value):
        return "".join(filter(str.isdigit, value))


class PriceMessageSerializer(serializers.Serializer):
    """Input for updating supplier prices from a free-text message."""
    phone = serializers.CharField()
    message = serializers.CharField(
        help_text='Example: "עגבנייה 3.50, מלפפון 2.00, גזר 1.80"'
    )


class PriceUpdateResultSerializer(serializers.Serializer):
    updated = serializers.ListField(child=serializers.DictField())
    skipped = serializers.ListField(child=serializers.DictField())


class SupplierCreateSerializer(serializers.ModelSerializer):
    """Write serializer — owner is set automatically from the request user."""
    prices = SupplierProductWriteSerializer(many=True, required=False, write_only=True)

    class Meta:
        model = Supplier
        fields = ("id", "name", "phone", "whatsapp_number", "region", "minimum_order", "prices")
        extra_kwargs = {
            "name": {"error_messages": {"unique": "ספק עם שם זה כבר קיים במערכת."}},
            "phone": {"error_messages": {"unique": "מספר הטלפון כבר קיים במערכת."}},
            "whatsapp_number": {"error_messages": {"unique": "מספר הוואטסאפ כבר קיים במערכת."}},
        }

    from .models import Product, SupplierProduct


    def create(self, validated_data):
        prices = validated_data.pop("prices", [])
        supplier = Supplier.objects.create(**validated_data)

        for item in prices:
            product_name = item["product_name"].lower().strip()

            unit = item["unit"]

            product, created = Product.objects.get_or_create(
                name=product_name,
                defaults={"unit": unit}
            )

            if not created and product.unit != unit:
                product.unit = unit
                product.save()

            SupplierProduct.objects.update_or_create(
                supplier=supplier,
                product=product,
                defaults={
                    "price_per_unit": item["price_per_unit"]
                }
            )

        return supplier

class SupplierPriceUpdateSerializer(serializers.Serializer):
    phone = serializers.CharField()
    prices = SupplierProductWriteSerializer(many=True)




    def validate_phone(self, value):
        return "".join(filter(str.isdigit, value))

class MarketPriceSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="product.name", read_only=True)
    unit = serializers.CharField(source="product.unit", read_only=True)
    unit_display = serializers.CharField(source="product.get_unit_display", read_only=True)

    class Meta:
        model = MarketPrice
        fields = ("name", "unit", "unit_display", "price_grade_a", "price_premium", "market_date", "updated_at")


class SupplierWithProductsSerializer(serializers.ModelSerializer):
    products = SupplierProductSerializer(many=True, read_only=True)

    class Meta:
        model = Supplier
        fields = (
            "id",
            "name",
            "phone",
            "whatsapp_number",
            "region",
            "minimum_order",
            "products",
        )


# ─────────────────────────── Market Agent push ───────────────────────────────

class MarketPriceItemSerializer(serializers.Serializer):
    """Single product entry in a market-agent bulk push."""
    product_name = serializers.CharField(max_length=100)
    price_grade_a = serializers.DecimalField(
        max_digits=10, decimal_places=2, allow_null=True, required=False
    )
    price_premium = serializers.DecimalField(
        max_digits=10, decimal_places=2, allow_null=True, required=False
    )
    market_date = serializers.DateField(allow_null=True, required=False)


class MarketPricesPushSerializer(serializers.Serializer):
    """Top-level payload for POST /api/catalog/market-prices/push/."""
    prices = MarketPriceItemSerializer(many=True)