from django.conf import settings
from rest_framework import generics, permissions
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from django.shortcuts import get_object_or_404
from .serializers import RegisterSerializer, UserSerializer, AdminUserSerializer, UserWithProfileSerializer, ProfileSerializer, HebrewTokenObtainPairSerializer
from django.contrib.auth import get_user_model
from rest_framework.response import Response
from core.pagination import LoadMorePagination10

User = get_user_model()


def _set_auth_cookies(response, access, refresh=None):
    """Write the access token (and optionally a rotated refresh token) as
    HttpOnly cookies. The refresh cookie is scoped to /api/users/ only —
    it never needs to travel on regular API requests, just refresh/logout."""
    response.set_cookie(
        settings.JWT_ACCESS_COOKIE,
        str(access),
        max_age=int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()),
        httponly=True,
        secure=settings.JWT_COOKIE_SECURE,
        samesite=settings.JWT_COOKIE_SAMESITE,
        path="/",
    )
    if refresh is not None:
        response.set_cookie(
            settings.JWT_REFRESH_COOKIE,
            str(refresh),
            max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
            httponly=True,
            secure=settings.JWT_COOKIE_SECURE,
            samesite=settings.JWT_COOKIE_SAMESITE,
            path="/api/users/",
        )


def _clear_auth_cookies(response):
    response.delete_cookie(settings.JWT_ACCESS_COOKIE, path="/")
    response.delete_cookie(settings.JWT_REFRESH_COOKIE, path="/api/users/")


class HebrewLoginView(TokenObtainPairView):
    serializer_class = HebrewTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        access = serializer.validated_data["access"]
        refresh = serializer.validated_data["refresh"]

        response = Response({"detail": "התחברת בהצלחה"})
        _set_auth_cookies(response, access, refresh)
        return response


class TokenRefreshCookieView(APIView):
    """POST /api/users/token/refresh/ — reads the refresh token from its
    HttpOnly cookie (never from the request body, so JS never handles it),
    issues a new access token, and — since ROTATE_REFRESH_TOKENS is on —
    a new refresh token too, overwriting both cookies."""
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        raw_refresh = request.COOKIES.get(settings.JWT_REFRESH_COOKIE)
        if not raw_refresh:
            return Response({"detail": "לא מחובר"}, status=401)

        serializer = TokenRefreshSerializer(data={"refresh": raw_refresh})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError:
            response = Response({"detail": "פג תוקף ההתחברות, יש להתחבר מחדש"}, status=401)
            _clear_auth_cookies(response)
            return response

        access = serializer.validated_data["access"]
        new_refresh = serializer.validated_data.get("refresh")

        response = Response({"detail": "רוענן"})
        _set_auth_cookies(response, access, new_refresh)
        return response


class LogoutView(APIView):
    """POST /api/users/logout/ — blacklists the refresh token so it can't
    be replayed even if it leaked before logout, and clears both cookies."""
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        raw_refresh = request.COOKIES.get(settings.JWT_REFRESH_COOKIE)
        if raw_refresh:
            try:
                RefreshToken(raw_refresh).blacklist()
            except TokenError:
                pass  # already invalid/expired — nothing to blacklist

        response = Response({"detail": "התנתקת"})
        _clear_auth_cookies(response)
        return response


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.save()

        return Response(UserWithProfileSerializer(user).data)


class MeView(generics.RetrieveAPIView):
    serializer_class = UserWithProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class AdminUserListView(generics.ListAPIView):
    queryset = User.objects.select_related("profile").all().order_by("-date_joined")
    serializer_class = UserWithProfileSerializer
    permission_classes = [permissions.IsAdminUser]
    pagination_class = LoadMorePagination10


class AdminUserDetailView(generics.RetrieveDestroyAPIView):
    queryset = User.objects.select_related("profile").all()
    serializer_class = UserWithProfileSerializer
    permission_classes = [permissions.IsAdminUser]


class AdminUserProfileUpdateView(generics.UpdateAPIView):
    """PATCH /api/users/admin/users/<id>/profile/ — admin edits any user's profile."""
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAdminUser]

    def get_object(self):
        user = get_object_or_404(User, pk=self.kwargs["pk"])
        return user.profile

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


class ProfileUpdateView(generics.UpdateAPIView):
    """PATCH /api/users/me/profile/ — update the current user's company/personal profile."""
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user.profile

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)
