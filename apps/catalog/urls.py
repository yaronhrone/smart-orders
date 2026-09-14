from django.urls import path
from .views import (
    ProductListCreateView, ProductDestroyView, ProductBulkCreateView,
    ProductAliasListCreateView, ProductAliasDestroyView,
    SupplierListCreateView, SupplierUpdateDestroyView,
    SupplierPriceUpdateView, SupplierPricesListView,
    ProductCatalogView,
)

urlpatterns = [
    path("products/", ProductListCreateView.as_view(), name="catalog-products"),
    path("products/bulk/", ProductBulkCreateView.as_view(), name="catalog-products-bulk"),
    path("products/<int:pk>/", ProductDestroyView.as_view(), name="catalog-product-delete"),
    path("product-aliases/", ProductAliasListCreateView.as_view(), name="catalog-product-aliases"),
    path("product-aliases/<int:pk>/", ProductAliasDestroyView.as_view(), name="catalog-product-alias-delete"),
    path("suppliers/", SupplierListCreateView.as_view(), name="catalog-suppliers"),
    path("suppliers/<int:pk>/", SupplierUpdateDestroyView.as_view(), name="catalog-supplier-detail"),
    path("suppliers/prices/", SupplierPriceUpdateView.as_view(), name="supplier-prices"),
    path("suppliers/prices/all/", SupplierPricesListView.as_view()),
    path("product-prices/", ProductCatalogView.as_view(), name="product-prices"),
]
