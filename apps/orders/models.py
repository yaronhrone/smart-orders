from django.db import models
from django.conf import settings
from apps.catalog.models import Product, Supplier


class ShoppingList(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shopping_lists")
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} - {self.name}"


class ShoppingListProduct(models.Model):
    shopping_list = models.ForeignKey(ShoppingList, on_delete=models.CASCADE, related_name="products")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    default_quantity = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ("shopping_list", "product")

    def __str__(self):
        return f"{self.product.name} x{self.default_quantity}"


class OrderRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "ממתין לאישור"
        APPROVED = "approved", "אושר"
        SENT = "sent", "נשלח לספקים"
        CANCELLED = "cancelled", "בוטל"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"הזמנה #{self.id} - {self.user.email} ({self.get_status_display()})"


class OrderRequestProduct(models.Model):
    order_request = models.ForeignKey(OrderRequest, on_delete=models.CASCADE, related_name="products")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def subtotal(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return f"{self.product.name} x{self.quantity} מ-{self.supplier.name}"
