from django.contrib import admin
from .models import ShoppingList, ShoppingListItem, OrderRequest, OrderRequestItem


class ShoppingListItemInline(admin.TabularInline):
    model = ShoppingListItem
    extra = 1


@admin.register(ShoppingList)
class ShoppingListAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "name", "created_at")
    search_fields = ("name", "user__email")
    inlines = [ShoppingListItemInline]


class OrderRequestItemInline(admin.TabularInline):
    model = OrderRequestItem
    extra = 0
    readonly_fields = ("subtotal",)


@admin.register(OrderRequest)
class OrderRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "total_price", "created_at")
    list_filter = ("status",)
    search_fields = ("user__email",)
    readonly_fields = ("total_price", "created_at")
    inlines = [OrderRequestItemInline]
