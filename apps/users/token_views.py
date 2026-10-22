from datetime import timedelta

from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import CookieJWTAuthentication
from .models import ApiToken

TOKEN_LIFETIME = timedelta(days=90)
MAX_ACTIVE_TOKENS = 3


def _row(token: ApiToken) -> dict:
    return {
        "id": token.id,
        "name": token.name,
        "created_at": token.created_at,
        "last_used_at": token.last_used_at,
        "expires_at": token.expires_at,
        "via_oauth": token.via_oauth,
    }


class _CreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")


class ApiTokenListCreateView(APIView):
    """
    GET  /api/users/api-tokens/ — the caller's active personal tokens (never the secret).
    POST /api/users/api-tokens/ — issue one; the secret is in this response only.

    Signing in with the website session is required: an API token cannot mint
    more tokens, so a leaked one cannot outlive its revocation.
    """

    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def _active(self, user):
        now = timezone.now()
        return ApiToken.objects.filter(user=user, revoked=False).exclude(expires_at__lte=now).order_by("-created_at")

    def _manual_count(self, user):
        return self._active(user).filter(via_oauth=False).count()

    def get(self, request):
        return Response({"results": [_row(t) for t in self._active(request.user)]})

    def post(self, request):
        serializer = _CreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if self._manual_count(request.user) >= MAX_ACTIVE_TOKENS:
            return Response(
                {"detail": f"אפשר להחזיק עד {MAX_ACTIVE_TOKENS} חיבורים פעילים. בטל חיבור קיים כדי ליצור חדש."},
                status=status.HTTP_409_CONFLICT,
            )
        token, raw = ApiToken.issue(
            request.user, serializer.validated_data["name"], expires_at=timezone.now() + TOKEN_LIFETIME,
        )
        return Response({**_row(token), "token": raw}, status=status.HTTP_201_CREATED)


class ApiTokenRevokeView(APIView):
    """DELETE /api/users/api-tokens/<id>/ — revoke one of the caller's own tokens."""

    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, pk):
        updated = ApiToken.objects.filter(pk=pk, user=request.user).update(revoked=True)
        if not updated:
            return Response({"detail": "החיבור לא נמצא"}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
