from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class Region(models.TextChoices):
    TEL_AVIV = "tel_aviv", "תל אביב"
    JERUSALEM = "jerusalem", "ירושלים"
    HAIFA = "haifa", "חיפה"
    SOUTH = "south", "דרום"
    NORTH = "north", "צפון"
    CENTER = "center", "מרכז"


class Unit(models.TextChoices):
    KG = "kg", "ק\"ג"
    GRAM = "gram", "גרם"
    UNIT = "unit", "יחידה"
    BOX = "box", "ארגז"
    BUNDLE = "bundle", "אגודה"
    PACK = "pack", "חבילה"


class Product(models.Model):
    name = models.CharField(max_length=100, unique=True)
    unit = models.CharField(max_length=10, choices=Unit.choices, default=Unit.KG)

    def __str__(self):
        return f"{self.name} ({self.get_unit_display()})"


class Supplier(models.Model):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, unique=True)
    whatsapp_number = models.CharField(max_length=20, unique=True)
    region = models.CharField(max_length=50, choices=Region.choices)
    minimum_order = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )

    def __str__(self):
        return self.name


class SupplierProduct(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="products")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="supplier_prices")
    price_per_unit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("supplier", "product")

    def __str__(self):
        return f"{self.supplier.name} - {self.product.name}: ₪{self.price_per_unit}"
