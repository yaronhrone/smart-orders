from django.urls import path
from .views import RegisterView, MeView, AdminUserListView, AdminUserDetailView, ProfileUpdateView, AdminUserProfileUpdateView, HebrewLoginView, TokenRefreshCookieView, LogoutView, ChangePasswordView
from .token_views import ApiTokenListCreateView, ApiTokenRevokeView

from .signup import SignupRequestView, AdminSignupListView, AdminSignupApproveView, AdminSignupRejectView

urlpatterns = [
    path("signup/", SignupRequestView.as_view(), name="signup"),
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", HebrewLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("token/refresh/", TokenRefreshCookieView.as_view(), name="token_refresh"),
    path("api-tokens/", ApiTokenListCreateView.as_view(), name="api-tokens"),
    path("api-tokens/<int:pk>/", ApiTokenRevokeView.as_view(), name="api-token-revoke"),
    path("me/change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("admin/signups/", AdminSignupListView.as_view(), name="admin-signup-list"),
    path("admin/signups/<int:pk>/approve/", AdminSignupApproveView.as_view(), name="admin-signup-approve"),
    path("admin/signups/<int:pk>/reject/", AdminSignupRejectView.as_view(), name="admin-signup-reject"),
    path("me/", MeView.as_view(), name="me"),
    path("me/profile/", ProfileUpdateView.as_view(), name="me-profile-update"),
    path("admin/users/", AdminUserListView.as_view(), name="admin-user-list"),
    path("admin/users/<int:pk>/", AdminUserDetailView.as_view(), name="admin-user-detail"),
    path("admin/users/<int:pk>/profile/", AdminUserProfileUpdateView.as_view(), name="admin-user-profile-update"),
]
