"""
Public customer sign-up. The request is saved as pending and sent to the admin (email +
WhatsApp) with the customer's details and the suppliers they work with. The admin calls the
suppliers, then approves it in the customers screen: that creates the customer with a
temporary password and sends them a confirmation (email + WhatsApp) with it.
"""
import logging
import secrets
import string

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.catalog.models import Region
from apps.users.models import Profile, SignupRequest, User
from core.pagination import LoadMorePagination10
from core.phone import normalize_il_phone

logger = logging.getLogger(__name__)

MAX_SUPPLIERS = 10


class SignupSupplierSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    phone = serializers.CharField(max_length=20)

    def validate_phone(self, value):
        phone = normalize_il_phone(value)
        if len(phone) < 9:
            raise serializers.ValidationError("מספר טלפון לא תקין")
        return phone


class SignupSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    email = serializers.EmailField(max_length=254)
    phone = serializers.CharField(max_length=20)
    company_name = serializers.CharField(max_length=150)
    company_address = serializers.CharField(max_length=250, allow_blank=True, required=False, default="")
    region = serializers.ChoiceField(choices=Region.choices)
    suppliers = SignupSupplierSerializer(many=True, allow_empty=True, max_length=MAX_SUPPLIERS, required=False, default=list)
    # Honeypot: hidden in the form, so only a bot fills it.
    website = serializers.CharField(allow_blank=True, required=False, default="")

    def validate_phone(self, value):
        phone = normalize_il_phone(value)
        if len(phone) < 9:
            raise serializers.ValidationError("מספר טלפון לא תקין")
        return phone


def build_admin_message(data: dict) -> str:
    lines = [
        "בקשת הרשמה חדשה",
        f"שם: {data['first_name']} {data['last_name']}",
        f"עסק: {data['company_name']}",
        f"טלפון: {data['phone']}",
        f"אימייל: {data['email']}",
        f"כתובת: {data.get('company_address') or '-'}",
        f"אזור: {Region(data['region']).label}",
    ]
    suppliers = data.get("suppliers") or []
    if suppliers:
        lines.append("ספקים:")
        lines += [f"  • {s['name']} — {s['phone']}" for s in suppliers]
    else:
        lines.append("ספקים: לא צוינו")
    return "\n".join(lines)


def notify_admin(message: str) -> None:
    """Email + WhatsApp to the admin. A failure of one channel must not lose the request."""
    admin_email = getattr(settings, "ADMIN_EMAIL", "")
    if admin_email:
        try:
            send_mail("בקשת הרשמה חדשה — Smart Orders", message, None, [admin_email])
        except Exception:
            logger.exception("Signup: email to admin failed")
    else:
        logger.warning("ADMIN_EMAIL לא מוגדר — לא נשלח מייל על בקשת הרשמה")

    admin_number = getattr(settings, "ADMIN_WHATSAPP_NUMBER", "")
    if admin_number:
        try:
            from apps.orders.whatsapp import send_whatsapp_message
            send_whatsapp_message(admin_number, message)
        except Exception:
            logger.exception("Signup: WhatsApp to admin failed")
    else:
        logger.warning("ADMIN_WHATSAPP_NUMBER לא מוגדר — לא נשלחה הודעה על בקשת הרשמה")


class SignupRequestView(APIView):
    """POST /api/users/signup/ — public, rate limited, stores nothing."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "signup"

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data.get("website"):
            # A bot: answer like a success so it learns nothing, send nothing.
            return Response({"detail": "ok"}, status=201)
        email = data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError({"email": "כתובת האימייל כבר רשומה במערכת."})
        # The same person pressing send twice (or retrying) is one request, not two.
        if SignupRequest.objects.filter(email__iexact=email, status=SignupRequest.Status.PENDING).exists():
            return Response({"detail": "ok"}, status=201)
        SignupRequest.objects.create(
            email=email, first_name=data["first_name"], last_name=data["last_name"], phone=data["phone"],
            company_name=data["company_name"], company_address=data.get("company_address", ""),
            region=data["region"], suppliers=[dict(x) for x in data.get("suppliers", [])],
        )
        notify_admin(build_admin_message(data))
        return Response({"detail": "ok"}, status=201)


def send_customer_confirmation(user, phone: str, password: str = "") -> None:
    """Told the customer their account is open. Best effort, never fails the creation."""
    login_url = getattr(settings, "SITE_URL", "").rstrip("/") + "/login"
    text = (
        f"שלום {user.first_name or ''}".strip() + ",\n"
        "ההרשמה שלך ל-Smart Orders אושרה והחשבון נפתח.\n"
        f"כניסה: {login_url}\n"
        f"אימייל להתחברות: {user.email}"
    )
    if password:
        text += f"\nסיסמה זמנית: {password}\nמומלץ להחליף אותה מיד באזור הפרופיל באתר."
    try:
        send_mail("ההרשמה שלך ל-Smart Orders אושרה", text, None, [user.email])
    except Exception:
        logger.exception("Signup: confirmation email to %s failed", user.email)
    if phone and getattr(settings, "SIGNUP_CUSTOMER_WHATSAPP", True):
        try:
            from apps.orders.whatsapp import send_whatsapp_message
            from apps.orders.whatsapp.validators import _local_to_e164
            send_whatsapp_message(_local_to_e164(phone), text)
        except Exception:
            logger.exception("Signup: confirmation WhatsApp to %s failed", phone)


TEMP_PASSWORD_LENGTH = 12


def make_temp_password() -> str:
    """Random, and always passes validate_password_hebrew (8+ chars, an upper case letter, a symbol)."""
    alphabet = string.ascii_letters + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(TEMP_PASSWORD_LENGTH - 2))
    return body + secrets.choice(string.ascii_uppercase) + secrets.choice("!@#$%")


class SignupRequestAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = SignupRequest
        fields = ("id", "email", "first_name", "last_name", "phone", "company_name", "company_address",
                  "region", "suppliers", "status", "created_at")


class AdminSignupListView(generics.ListAPIView):
    """GET /api/users/admin/signups/?status=pending (default pending)."""

    serializer_class = SignupRequestAdminSerializer
    permission_classes = [permissions.IsAdminUser]
    pagination_class = LoadMorePagination10

    def get_queryset(self):
        status = self.request.query_params.get("status", SignupRequest.Status.PENDING)
        return SignupRequest.objects.filter(status=status)


class AdminSignupApproveView(APIView):
    """POST /api/users/admin/signups/<id>/approve/ — creates the customer and sends the confirmation."""

    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        with transaction.atomic():
            signup = get_object_or_404(SignupRequest.objects.select_for_update(), pk=pk)
            if signup.status != SignupRequest.Status.PENDING:
                raise ValidationError({"detail": "הבקשה כבר טופלה"})
            if User.objects.filter(email__iexact=signup.email).exists():
                raise ValidationError({"detail": "כבר קיים משתמש עם האימייל הזה"})
            password = make_temp_password()
            user = User.objects.create_user(
                email=signup.email, password=password,
                first_name=signup.first_name, last_name=signup.last_name, must_change_password=True,
            )
            Profile.objects.create(
                user=user, company_name=signup.company_name, company_address=signup.company_address,
                phone=signup.phone, region=signup.region,
            )
            signup.status = SignupRequest.Status.APPROVED
            signup.reviewed_at = timezone.now()
            signup.save(update_fields=["status", "reviewed_at"])
        send_customer_confirmation(user, signup.phone, password)
        return Response({"detail": "אושר", "user_id": user.id})


class AdminSignupRejectView(APIView):
    """POST /api/users/admin/signups/<id>/reject/ — nothing is sent to the customer."""

    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        signup = get_object_or_404(SignupRequest, pk=pk)
        if signup.status != SignupRequest.Status.PENDING:
            raise ValidationError({"detail": "הבקשה כבר טופלה"})
        signup.status = SignupRequest.Status.REJECTED
        signup.reviewed_at = timezone.now()
        signup.save(update_fields=["status", "reviewed_at"])
        return Response({"detail": "נדחה"})
