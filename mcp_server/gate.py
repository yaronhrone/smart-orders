"""Sign-in gate in front of the MCP app.

Without a valid token the answer is 401 with a pointer to the resource metadata,
which is what makes a client such as Claude start the OAuth sign-in. Valid tokens
are remembered for a short while, together with whose they are (the daily order
quota is per customer, not per token, so a refreshed token does not reset it)."""
import hashlib
import json
import os
import time

import httpx

import api

PUBLIC_BASE = os.environ.get("MCP_PUBLIC_BASE", "http://localhost:8765").rstrip("/")
OAUTH_ISSUER = os.environ.get("OAUTH_ISSUER", "http://localhost:8000").rstrip("/")
CACHE_SECONDS = 30

_cache: dict = {}


def token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def customer_of(token: str):
    """Who a token that already passed the gate belongs to; None if it has not."""
    entry = _cache.get(token_key(token))
    return entry[0] if entry else None


async def validate(token: str):
    """The customer's email if the token is good, else None."""
    key = token_key(token)
    entry = _cache.get(key)
    if entry and entry[1] > time.time():
        return entry[0]
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(f"{api.API_BASE}/api/users/me/", headers={"Authorization": f"Bearer {token}"})
    if res.status_code != 200:
        _cache.pop(key, None)
        return None
    who = str(res.json().get("email") or key).lower()
    _cache[key] = (who, time.time() + CACHE_SECONDS)
    return who


def resource_metadata() -> dict:
    return {
        "resource": f"{PUBLIC_BASE}/mcp",
        "authorization_servers": [OAUTH_ISSUER],
        "bearer_methods_supported": ["header"],
    }


async def _send_json(send, status, body, headers=()):
    payload = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()), *headers]})
    await send({"type": "http.response.body", "body": payload})


class AuthGate:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope["path"]
        if path.startswith("/.well-known/oauth-protected-resource"):
            return await _send_json(send, 200, resource_metadata())
        if path == "/health":
            return await _send_json(send, 200, {"status": "ok"})
        if scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)

        headers = dict(scope["headers"])
        scheme, _, token = headers.get(b"authorization", b"").decode().partition(" ")
        token = token.strip() if scheme.lower() == "bearer" else ""
        challenge = f'Bearer resource_metadata="{PUBLIC_BASE}/.well-known/oauth-protected-resource"'
        if not token:
            return await _send_json(send, 401, {"error": "unauthorized"}, [(b"www-authenticate", challenge.encode())])
        try:
            who = await validate(token)
        except httpx.HTTPError:
            return await _send_json(send, 503, {"error": "השרת לא זמין, נסה שוב"})
        if who is None:
            invalid = challenge + ', error="invalid_token"'
            return await _send_json(send, 401, {"error": "invalid_token"}, [(b"www-authenticate", invalid.encode())])
        return await self.app(scope, receive, send)
