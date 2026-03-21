from django.db import models
from django.conf import settings


class Region(models.TextChoices):
    TEL_AVIV = "tel_aviv", "תל אביב"
    JERUSALEM = "jerusalem", "ירושלים"
    HAIFA = "haifa", "חיפה"
    SOUTH = "south", "דרום"
    NORTH = "north", "צפון"
    CENTER = "center", "מרכז"


class Unit(models.TextChoices):
    KG = "kg", "ק\"ג"
    UNIT = "unit", "יחידה"
    BOX = "box", "ארגז"


class Product(models.Model):
    name = models.CharField(max_length=100, unique=True)
    unit = models.CharField(max_length=10, choices=Unit.choices, default=Unit.KG)

    def __str__(self):
        return f"{self.name} ({self.get_unit_display()})"


class Supplier(models.Model):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20)
    whatsapp_number = models.CharField(max_length=20)
    region = models.CharField(max_length=50, choices=Region.choices)
    minimum_order = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # None = global (admin), set = private supplier of a user
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="private_suppliers",
    )

    def __str__(self):
        return self.name

    @property
    def is_global(self):
        return self.owner is None


class SupplierProduct(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="products")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="supplier_prices")
    price_per_unit = models.DecimalField(max_digits=10, decimal_places=2)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("supplier", "product")

    def __str__(self):
        return f"{self.supplier.name} - {self.product.name}: ₪{self.price_per_unit}"


class MarketPrice(models.Model):
    """מחיר עזר מרשות חקלאית — שורה אחת לכל מוצר."""
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="market_price",
    )
    price_per_unit = models.DecimalField(max_digits=10, decimal_places=2)
    source = models.CharField(max_length=255, default="רשות חקלאית")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.product.name}: ₪{self.price_per_unit} ({self.source})"
