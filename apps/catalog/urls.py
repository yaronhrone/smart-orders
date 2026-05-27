from django.urls import path
from .views import (
    ProductListCreateView, ProductDestroyView, SupplierListCreateView, SupplierPriceUpdateView,
    SupplierPricesListView, MarketPriceListView, MarketPricesPushView, ProductCatalogView,
)

urlpatterns = [
    path("products/", ProductListCreateView.as_view(), name="catalog-products"),
    path("products/<int:pk>/", ProductDestroyView.as_view(), name="catalog-product-delete"),
    path("suppliers/", SupplierListCreateView.as_view(), name="catalog-suppliers"),
    path("suppliers/prices/", SupplierPriceUpdateView.as_view(), name="supplier-prices"),
    path("suppliers/prices/all/", SupplierPricesListView.as_view()),
    path("market-prices/", MarketPriceListView.as_view(), name="market-prices"),
    path("market-prices/push/", MarketPricesPushView.as_view(), name="market-prices-push"),
    path("product-prices/", ProductCatalogView.as_view(), name="product-prices"),
]
