from django.urls import path
from .views import (
    SuggestOrderView,
    PlaceOrderView,
    OrderBatchListView,
    OrderDetailView,
    OrderStatusUpdateView,
    OrderStatsView,
    AdminOrderBatchListView,
    OpenBatchView,
    OpenOrderChangeView,
    OpenOrderCancelView,
)
urlpatterns = [
    path("suggest/", SuggestOrderView.as_view(), name="orders-suggest"),
    path("place/", PlaceOrderView.as_view(), name="orders-place"),
    path("open/change/", OpenOrderChangeView.as_view(), name="orders-open-change"),
    path("open/cancel/", OpenOrderCancelView.as_view(), name="orders-open-cancel"),
    path("open/", OpenBatchView.as_view(), name="orders-open"),
    path("batches/", OrderBatchListView.as_view(), name="orders-batches"),
    path("admin/batches/", AdminOrderBatchListView.as_view(), name="orders-admin-batches"),
    path("<int:pk>/", OrderDetailView.as_view(), name="orders-detail"),
    path("<int:pk>/status/", OrderStatusUpdateView.as_view(), name="orders-status"),
    path("stats/", OrderStatsView.as_view(), name="orders-stats"),
]
