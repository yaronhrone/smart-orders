from django.urls import reverse
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework import status

NO_THROTTLE = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {},
}

User = get_user_model()

LOGIN_URL = reverse("login")



class LoginTests(APITestCase):

    def setUp(self):
        self.email = "testuser@example.com"
        self.password = "StrongPass123!"
        self.user = User.objects.create_user(email=self.email, password=self.password)

    def test_login_success(self):
        """Valid credentials return access and refresh tokens"""
        res = self.client.post(LOGIN_URL, {"email": self.email, "password": self.password})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("access", res.data)
        self.assertIn("refresh", res.data)

    def test_login_wrong_password(self):
        """Wrong password returns 401"""
        res = self.client.post(LOGIN_URL, {"email": self.email, "password": "wrongpassword"})

        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", res.data)

    def test_login_wrong_email(self):
        """Non-existent email returns 401"""
        res = self.client.post(LOGIN_URL, {"email": "nobody@example.com", "password": self.password})

        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", res.data)

    def test_login_missing_fields(self):
        """Missing email or password returns 400"""
        res = self.client.post(LOGIN_URL, {"email": self.email})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        res = self.client.post(LOGIN_URL, {"password": self.password})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_inactive_user(self):
        """Inactive user cannot login"""
        self.user.is_active = False
        self.user.save()

        res = self.client.post(LOGIN_URL, {"email": self.email, "password": self.password})

        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", res.data)

    def test_access_token_can_authenticate(self):
        """Access token returned from login works for authenticated requests"""
        res = self.client.post(LOGIN_URL, {"email": self.email, "password": self.password})
        access_token = res.data["access"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        me_url = reverse("me")
        res = self.client.get(me_url)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["email"], self.email)
