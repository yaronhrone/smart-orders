from django.urls import path
from .views import (
    SuggestOrderView,
    PlaceOrderView,
    OrderListView,
    OrderDetailView,
    OrderStatusUpdateView,
    OrderStatsView,
    ShoppingListView,
    ShoppingListDetailView,
    ShoppingListSuggestView,
)
urlpatterns = [
    path("suggest/", SuggestOrderView.as_view(), name="orders-suggest"),
    path("place/", PlaceOrderView.as_view(), name="orders-place"),
    path("", OrderListView.as_view(), name="orders-list"),
    path("<int:pk>/", OrderDetailView.as_view(), name="orders-detail"),
    path("<int:pk>/status/", OrderStatusUpdateView.as_view(), name="orders-status"),
    path("stats/", OrderStatsView.as_view(), name="orders-stats"),
    path("shopping-lists/", ShoppingListView.as_view(), name="shopping-lists"),
    path("shopping-lists/<int:pk>/", ShoppingListDetailView.as_view(), name="shopping-lists-detail"),
    path("shopping-lists/<int:pk>/suggest/", ShoppingListSuggestView.as_view(), name="shopping-lists-suggest"),
]
