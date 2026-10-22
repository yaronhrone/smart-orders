import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.users.authentication import CookieJWTAuthentication
from apps.users.models import ApiToken

from .models import OAuthClient, OAuthCode, OAuthRefreshToken, digest, new_secret

CODE_LIFETIME = timedelta(minutes=10)
ACCESS_LIFETIME = timedelta(days=1)
REFRESH_LIFETIME = timedelta(days=90)
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
MAX_REDIRECT_URIS = 5


def _error(code, description, status=400):
    return Response({"error": code, "error_description": description}, status=status)


def _valid_redirect_uri(uri: str) -> bool:
    if not isinstance(uri, str) or len(uri) > 500:
        return False
    parsed = urlparse(uri)
    if parsed.fragment or not parsed.hostname:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS


def authorization_server_metadata() -> dict:
    issuer = settings.OAUTH_ISSUER.rstrip("/")
    return {
        "issuer": issuer,
        "authorization_endpoint": settings.OAUTH_AUTHORIZE_URL,
        "token_endpoint": f"{issuer}/api/oauth/token/",
        "registration_endpoint": f"{issuer}/api/oauth/register/",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


class MetadataView(APIView):
    """GET /.well-known/oauth-authorization-server (RFC 8414)."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response(authorization_server_metadata())


class RegisterView(APIView):
    """POST /api/oauth/register/ — dynamic client registration (RFC 7591), public clients only."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "oauth_register"

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        uris = data.get("redirect_uris")
        if not isinstance(uris, list) or not uris or len(uris) > MAX_REDIRECT_URIS:
            return _error("invalid_redirect_uri", "redirect_uris is required")
        if not all(_valid_redirect_uri(u) for u in uris):
            return _error("invalid_redirect_uri", "redirect URIs must be https, or http on localhost")
        name = str(data.get("client_name") or "MCP client").strip()[:100] or "MCP client"
        client = OAuthClient.objects.create(client_id=secrets.token_urlsafe(24), name=name, redirect_uris=uris)
        return Response({
            "client_id": client.client_id,
            "client_name": client.name,
            "redirect_uris": client.redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }, status=201)


class _AuthorizeSerializer(serializers.Serializer):
    client_id = serializers.CharField()
    redirect_uri = serializers.CharField()
    code_challenge = serializers.CharField(max_length=128)
    code_challenge_method = serializers.ChoiceField(choices=["S256"])
    state = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class AuthorizeView(APIView):
    """
    The consent step, called by the website's /oauth/authorize page with the customer's
    login session (an API token cannot approve anything).

    GET  ?client_id=&redirect_uri= — what to show: the app's name, after checking the redirect URI.
    POST — the customer approved: returns where to send the browser, with a one-time code.
    """

    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _client(client_id, redirect_uri):
        client = OAuthClient.objects.filter(client_id=client_id).first()
        if client is None or redirect_uri not in client.redirect_uris:
            return None
        return client

    def get(self, request):
        client = self._client(request.query_params.get("client_id", ""), request.query_params.get("redirect_uri", ""))
        if client is None:
            return _error("invalid_request", "אפליקציה או כתובת חזרה לא מוכרות")
        return Response({"client_name": client.name})

    def post(self, request):
        serializer = _AuthorizeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        client = self._client(d["client_id"], d["redirect_uri"])
        if client is None:
            return _error("invalid_request", "אפליקציה או כתובת חזרה לא מוכרות")
        code = new_secret()
        OAuthCode.objects.create(
            code_hash=digest(code), client=client, user=request.user, redirect_uri=d["redirect_uri"],
            code_challenge=d["code_challenge"], expires_at=timezone.now() + CODE_LIFETIME,
        )
        query = {"code": code}
        if d["state"]:
            query["state"] = d["state"]
        sep = "&" if "?" in d["redirect_uri"] else "?"
        return Response({"redirect_to": f"{d['redirect_uri']}{sep}{urlencode(query)}"})


def _pkce_ok(verifier: str, challenge: str) -> bool:
    computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return secrets.compare_digest(computed, challenge)


def _issue_tokens(client, user):
    now = timezone.now()
    api_token, access = ApiToken.issue(user, f"Claude ({client.name})", expires_at=now + ACCESS_LIFETIME)
    api_token.via_oauth = True
    api_token.save(update_fields=["via_oauth"])
    refresh = new_secret()
    OAuthRefreshToken.objects.create(
        token_hash=digest(refresh), client=client, user=user, api_token=api_token, expires_at=now + REFRESH_LIFETIME,
    )
    return Response({
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_LIFETIME.total_seconds()),
        "refresh_token": refresh,
    })


class TokenView(APIView):
    """POST /api/oauth/token/ — authorization_code (with PKCE) and refresh_token grants."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        data = request.data
        grant = data.get("grant_type")
        client = OAuthClient.objects.filter(client_id=data.get("client_id", "")).first()
        if client is None:
            return _error("invalid_client", "unknown client", 401)
        if grant == "authorization_code":
            return self._code(data, client)
        if grant == "refresh_token":
            return self._refresh(data, client)
        return _error("unsupported_grant_type", "grant_type must be authorization_code or refresh_token")

    @transaction.atomic
    def _code(self, data, client):
        row = (
            OAuthCode.objects.select_for_update(of=("self",))
            .filter(code_hash=digest(str(data.get("code", ""))), client=client).select_related("user").first()
        )
        if row is None or row.used or row.expires_at <= timezone.now():
            return _error("invalid_grant", "code is invalid or expired")
        row.used = True
        row.save(update_fields=["used"])
        if data.get("redirect_uri") != row.redirect_uri:
            return _error("invalid_grant", "redirect_uri does not match")
        verifier = str(data.get("code_verifier", ""))
        if not 43 <= len(verifier) <= 128 or not _pkce_ok(verifier, row.code_challenge):
            return _error("invalid_grant", "PKCE verification failed")
        if not row.user.is_active:
            return _error("invalid_grant", "user is inactive")
        return _issue_tokens(client, row.user)

    @transaction.atomic
    def _refresh(self, data, client):
        row = (
            OAuthRefreshToken.objects.select_for_update(of=("self",))
            .filter(token_hash=digest(str(data.get("refresh_token", ""))), client=client)
            .select_related("user", "api_token").first()
        )
        if row is None or row.used or row.expires_at <= timezone.now():
            return _error("invalid_grant", "refresh token is invalid or expired")
        if row.api_token is None or row.api_token.revoked or not row.user.is_active:
            return _error("invalid_grant", "the connection was revoked")
        row.used = True
        row.save(update_fields=["used"])
        row.api_token.revoked = True
        row.api_token.save(update_fields=["revoked"])
        return _issue_tokens(client, row.user)
