from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import RegisterView, MeView, AdminUserListView, AdminUserDetailView, ProfileUpdateView, AdminUserProfileUpdateView, HebrewLoginView

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", HebrewLoginView.as_view(), name="login"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("me/", MeView.as_view(), name="me"),
    path("me/profile/", ProfileUpdateView.as_view(), name="me-profile-update"),
    path("admin/users/", AdminUserListView.as_view(), name="admin-user-list"),
    path("admin/users/<int:pk>/", AdminUserDetailView.as_view(), name="admin-user-detail"),
    path("admin/users/<int:pk>/profile/", AdminUserProfileUpdateView.as_view(), name="admin-user-profile-update"),
]
