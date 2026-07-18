from django.urls import path
from .views import (
    ProductListCreateView, ProductDestroyView, ProductBulkCreateView,
    SupplierListCreateView, SupplierUpdateDestroyView,
    SupplierPriceUpdateView, SupplierPricesListView, MarketPriceListView, MarketPriceRawScrapeView,
    ProductCatalogView,
)

urlpatterns = [
    path("products/", ProductListCreateView.as_view(), name="catalog-products"),
    path("products/bulk/", ProductBulkCreateView.as_view(), name="catalog-products-bulk"),
    path("products/<int:pk>/", ProductDestroyView.as_view(), name="catalog-product-delete"),
    path("suppliers/", SupplierListCreateView.as_view(), name="catalog-suppliers"),
    path("suppliers/<int:pk>/", SupplierUpdateDestroyView.as_view(), name="catalog-supplier-detail"),
    path("suppliers/prices/", SupplierPriceUpdateView.as_view(), name="supplier-prices"),
    path("suppliers/prices/all/", SupplierPricesListView.as_view()),
    path("market-prices/", MarketPriceListView.as_view(), name="market-prices"),
    path("market-prices/raw/", MarketPriceRawScrapeView.as_view(), name="market-prices-raw"),
    path("product-prices/", ProductCatalogView.as_view(), name="product-prices"),
]
