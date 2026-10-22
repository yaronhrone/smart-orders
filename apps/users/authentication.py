from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import ApiToken


class CookieJWTAuthentication(JWTAuthentication):
    """
    Same as JWTAuthentication, but falls back to the HttpOnly access-token
    cookie when there's no Authorization header.

    The browser frontend never sends Authorization — it relies entirely on
    the cookie set by HebrewLoginView/TokenRefreshCookieView. API clients
    (Postman, scripts, CI) keep working exactly as before via the header.
    """

    def authenticate(self, request):
        header = self.get_header(request)
        if header is not None:
            return super().authenticate(request)

        raw_token = request.COOKIES.get(settings.JWT_ACCESS_COOKIE)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        return self.get_user(validated_token), validated_token


class ApiTokenAuthentication(BaseAuthentication):
    """`Authorization: Bearer so_...` — a personal API token (see ApiToken).
    Anything else is left to the next authenticator (JWT)."""

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if len(header) != 2 or header[0].lower() != b"bearer":
            return None
        try:
            raw = header[1].decode()
        except UnicodeError:
            return None
        if not raw.startswith(ApiToken.PREFIX):
            return None

        token = ApiToken.objects.select_related("user").filter(token_hash=ApiToken.hash_token(raw)).first()
        if token is None or not token.is_usable or not token.user.is_active:
            raise AuthenticationFailed("טוקן לא תקין")
        ApiToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
        return token.user, token

    def authenticate_header(self, request):
        return "Bearer"
