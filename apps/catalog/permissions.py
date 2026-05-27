import secrets

from django.conf import settings
from rest_framework.permissions import BasePermission


class IsMarketAgent(BasePermission):
    """
    Allows access only to requests carrying the pre-shared market-agent key.

    Expected header:
        Authorization: Api-Key <MARKET_AGENT_SECRET>

    If MARKET_AGENT_SECRET is empty (not configured), all requests are denied.
    Uses secrets.compare_digest to resist timing attacks.
    """

    message = "Invalid or missing market agent key."

    def has_permission(self, request, view):
        secret = getattr(settings, "MARKET_AGENT_SECRET", "")
        if not secret:
            return False
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Api-Key "):
            return False
        provided = auth[len("Api-Key "):]
        return secrets.compare_digest(provided, secret)
