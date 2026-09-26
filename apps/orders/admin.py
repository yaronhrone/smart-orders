from django.contrib import admin
from .models import OrderBatch, OrderRequest, OrderRequestProduct, SupplierConfirmation


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
    list_display = ("id", "batch", "user", "supplier", "status", "total_price", "created_at")
    list_filter = ("status", "supplier")
    search_fields = ("user__email", "supplier__name")
    readonly_fields = ("total_price", "created_at")
    inlines = [OrderRequestItemInline]


class OrderRequestInline(admin.TabularInline):
    model = OrderRequest
    extra = 0
    fields = ("id", "supplier", "status", "total_price")
    readonly_fields = ("id", "supplier", "status", "total_price")
    show_change_link = True


@admin.register(OrderBatch)
class OrderBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "created_at")
    search_fields = ("user__email",)
    readonly_fields = ("created_at",)
    inlines = [OrderRequestInline]
