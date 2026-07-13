from django.urls import path
from .views import (
    SuggestOrderView,
    PlaceOrderView,
    OrderListView,
    OrderDetailView,
    OrderStatusUpdateView,
    OrderStatsView,
)
urlpatterns = [
    path("suggest/", SuggestOrderView.as_view(), name="orders-suggest"),
    path("place/", PlaceOrderView.as_view(), name="orders-place"),
    path("", OrderListView.as_view(), name="orders-list"),
    path("<int:pk>/", OrderDetailView.as_view(), name="orders-detail"),
    path("<int:pk>/status/", OrderStatusUpdateView.as_view(), name="orders-status"),
    path("stats/", OrderStatsView.as_view(), name="orders-stats"),
]
