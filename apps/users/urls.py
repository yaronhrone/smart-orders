from django.urls import path
from .views import RegisterView, MeView, AdminUserListView, AdminUserDetailView, ProfileUpdateView, AdminUserProfileUpdateView, HebrewLoginView, TokenRefreshCookieView, LogoutView
from .token_views import ApiTokenListCreateView, ApiTokenRevokeView

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", HebrewLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("token/refresh/", TokenRefreshCookieView.as_view(), name="token_refresh"),
    path("api-tokens/", ApiTokenListCreateView.as_view(), name="api-tokens"),
    path("api-tokens/<int:pk>/", ApiTokenRevokeView.as_view(), name="api-token-revoke"),
    path("me/", MeView.as_view(), name="me"),
    path("me/profile/", ProfileUpdateView.as_view(), name="me-profile-update"),
    path("admin/users/", AdminUserListView.as_view(), name="admin-user-list"),
    path("admin/users/<int:pk>/", AdminUserDetailView.as_view(), name="admin-user-detail"),
    path("admin/users/<int:pk>/profile/", AdminUserProfileUpdateView.as_view(), name="admin-user-profile-update"),
]
