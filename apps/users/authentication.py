from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication


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
