from django.contrib import admin
from .models import ShoppingList, ShoppingListProduct, OrderRequest, OrderRequestProduct, SupplierConfirmation


class ShoppingListItemInline(admin.TabularInline):
    model = ShoppingListProduct
    extra = 1


@admin.register(ShoppingList)
class ShoppingListAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "name", "is_primary", "created_at")
    list_filter = ("is_primary",)
    search_fields = ("name", "user__email")
    inlines = [ShoppingListItemInline]


@admin.register(SupplierConfirmation)
class SupplierConfirmationAdmin(admin.ModelAdmin):
    list_display = ("id", "order_request_product", "confirmed_quantity", "confirmed_at")
    readonly_fields = ("confirmed_at",)


class OrderRequestItemInline(admin.TabularInline):
    model = OrderRequestProduct
    extra = 0
    readonly_fields = ("subtotal",)


@admin.register(OrderRequest)
class OrderRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "total_price", "created_at")
    list_filter = ("status",)
    search_fields = ("user__email",)
    readonly_fields = ("total_price", "created_at")
    inlines = [OrderRequestItemInline]
