from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.contrib.auth.models import BaseUserManager

from apps.catalog.models import Region


class UserManager(BaseUserManager):

    def create_user(self, email, password=None, **extra_fields):

        if not email:
            raise ValueError("חובה להזין כתובת אימייל")

        email = self.normalize_email(email).lower()

        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)

        return user


    def create_superuser(self, email, password=None, **extra_fields):

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        return self.create_user(email, password, **extra_fields)
class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()


class Profile(models.Model):

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile"
    )

    # company info
    company_name = models.CharField(max_length=255 , default="Default Company")
    company_phone = models.CharField(max_length=20, blank=True)
    company_address = models.CharField(max_length=255, blank=True)

    # personal info
    phone = models.CharField(max_length=20, blank=True)
    position = models.CharField(max_length=255, blank=True)

    region = models.CharField(max_length=50, choices=Region.choices, default=Region.JERUSALEM)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.company_name

class ApiToken(models.Model):
    """
    A long-lived personal token for API clients that cannot refresh a 60 minute
    JWT (the MCP server). Only the SHA-256 hash is stored; the raw token is
    shown once, when it is issued.
    """

    PREFIX = "so_"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField(max_length=100, blank=True)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)
    # Null = never expires (tokens issued from Django admin); the website issues 90 day tokens.
    expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} {self.name}".strip()

    @property
    def is_usable(self) -> bool:
        from django.utils import timezone
        return not self.revoked and (self.expires_at is None or self.expires_at > timezone.now())

    @staticmethod
    def hash_token(raw: str) -> str:
        import hashlib
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, user, name="", expires_at=None):
        """Create a token and return (instance, raw_token)."""
        import secrets
        raw = cls.PREFIX + secrets.token_urlsafe(32)
        return cls.objects.create(user=user, name=name, expires_at=expires_at, token_hash=cls.hash_token(raw)), raw
