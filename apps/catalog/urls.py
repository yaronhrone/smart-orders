from django.urls import path
from .views import ProductListCreateView, SupplierListCreateView, SupplierUpdatePricesView

urlpatterns = [
    path("products/", ProductListCreateView.as_view(), name="catalog-products"),
    path("suppliers/", SupplierListCreateView.as_view(), name="catalog-suppliers"),
    path("suppliers/<int:pk>/update-prices/", SupplierUpdatePricesView.as_view(), name="supplier-update-prices"),
]
