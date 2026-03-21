from django.contrib import admin
from .models import Product, Supplier, SupplierProduct, MarketPrice


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "unit")
    search_fields = ("name",)


class SupplierProductInline(admin.TabularInline):
    model = SupplierProduct
    extra = 1


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "region", "minimum_order", "owner", "is_global")
    list_filter = ("region", "owner")
    search_fields = ("name",)
    inlines = [SupplierProductInline]


@admin.register(SupplierProduct)
class SupplierProductAdmin(admin.ModelAdmin):
    list_display = ("supplier", "product", "price_per_unit", "updated_at")
    list_filter = ("supplier", "product")


@admin.register(MarketPrice)
class MarketPriceAdmin(admin.ModelAdmin):
    list_display = ("product", "price_per_unit", "source", "updated_at")
    search_fields = ("product__name",)
