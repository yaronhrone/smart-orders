"""
"Forgot password": a one-time link by email. The link carries the user id and Django's
password-reset token, which stops working once the password changes or after
PASSWORD_RESET_TIMEOUT, so nothing is stored.
"""
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.users.models import User

from .serializers import validate_password_hebrew

logger = logging.getLogger(__name__)

SENT = {"detail": "אם האימייל רשום במערכת, נשלח אליו קישור לאיפוס סיסמה."}


class PasswordResetRequestView(APIView):
    """POST /api/users/password-reset/ {email}. The answer is the same whether or not the email
    exists, so the form can't be used to find out who is a customer."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        email = str(request.data.get("email", "")).strip().lower()
        user = User.objects.filter(email__iexact=email, is_active=True).first() if email else None
        if user:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            link = f"{settings.SITE_URL.rstrip('/')}/reset-password?{urlencode({'uid': uid, 'token': token})}"
            text = (
                "קיבלנו בקשה לאיפוס הסיסמה שלך ב-Smart Orders.\n"
                f"לבחירת סיסמה חדשה: {link}\n"
                "הקישור תקף לשעה. אם לא ביקשת, אפשר להתעלם מההודעה."
            )
            try:
                send_mail("איפוס סיסמה — Smart Orders", text, None, [user.email])
            except Exception:
                logger.exception("Password reset email to %s failed", user.email)
        return Response(SENT)


class PasswordResetConfirmView(APIView):
    """POST /api/users/password-reset/confirm/ {uid, token, new_password, new_password2}."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_confirm"

    def post(self, request):
        bad_link = Response({"detail": "הקישור לא תקף או שפג תוקפו. אפשר לבקש קישור חדש."}, status=400)
        try:
            user = User.objects.get(pk=force_str(urlsafe_base64_decode(str(request.data.get("uid", "")))))
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            return bad_link
        if not user.is_active or not default_token_generator.check_token(user, str(request.data.get("token", ""))):
            return bad_link

        new = request.data.get("new_password", "")
        if new != request.data.get("new_password2", ""):
            return Response({"new_password": "הסיסמאות אינן תואמות"}, status=400)
        try:
            validate_password_hebrew(new)
        except serializers.ValidationError as exc:
            return Response({"new_password": exc.detail[0]}, status=400)
        user.set_password(new)
        user.save(update_fields=["password"])
        return Response({"detail": "הסיסמה הוחלפה, אפשר להתחבר."})
