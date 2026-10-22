import hashlib
import secrets

from django.conf import settings
from django.db import models


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_secret() -> str:
    return secrets.token_urlsafe(32)


class OAuthClient(models.Model):
    """An app that registered itself (RFC 7591), e.g. Claude. Public client: no secret, PKCE instead."""

    client_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    redirect_uris = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class OAuthCode(models.Model):
    """A one-time authorization code, valid for minutes, bound to the client, redirect URI and PKCE challenge."""

    code_hash = models.CharField(max_length=64, unique=True)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    redirect_uri = models.CharField(max_length=500)
    code_challenge = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)


class OAuthRefreshToken(models.Model):
    """Rotating refresh token. `api_token` is the access token issued with it, so revoking that
    connection on the website also ends the refresh."""

    token_hash = models.CharField(max_length=64, unique=True)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    api_token = models.ForeignKey("users.ApiToken", null=True, on_delete=models.SET_NULL)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
