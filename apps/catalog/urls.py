from django.urls import path
from .views import (
    ProductListCreateView, SupplierListCreateView, SupplierPriceUpdateView,
    SupplierPricesListView, MarketPriceListView,
)

urlpatterns = [
    path("products/", ProductListCreateView.as_view(), name="catalog-products"),
    path("suppliers/", SupplierListCreateView.as_view(), name="catalog-suppliers"),
    path("suppliers/prices/", SupplierPriceUpdateView.as_view(), name="supplier-prices"),
    path("suppliers/prices/all/", SupplierPricesListView.as_view()),
    path("market-prices/", MarketPriceListView.as_view(), name="market-prices"),
]
