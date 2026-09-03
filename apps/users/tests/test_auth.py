from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status
from django.conf import settings

User = get_user_model()

LOGIN_URL = reverse("login")
LOGOUT_URL = reverse("logout")
REFRESH_URL = reverse("token_refresh")
ME_URL = reverse("me")

ACCESS_COOKIE = settings.JWT_ACCESS_COOKIE
REFRESH_COOKIE = settings.JWT_REFRESH_COOKIE


class LoginTests(APITestCase):

    def setUp(self):
        self.email = "testuser@example.com"
        self.password = "StrongPass123!"
        self.user = User.objects.create_user(email=self.email, password=self.password)

    def test_login_success(self):
        """Valid credentials set HttpOnly access/refresh cookies, not a token in the body."""
        res = self.client.post(LOGIN_URL, {"email": self.email, "password": self.password})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotIn("access", res.data)
        self.assertNotIn("refresh", res.data)
        self.assertIn(ACCESS_COOKIE, res.cookies)
        self.assertIn(REFRESH_COOKIE, res.cookies)
        self.assertTrue(res.cookies[ACCESS_COOKIE]["httponly"])
        self.assertTrue(res.cookies[REFRESH_COOKIE]["httponly"])

    def test_login_wrong_password(self):
        """Wrong password returns 401 and sets no cookies"""
        res = self.client.post(LOGIN_URL, {"email": self.email, "password": "wrongpassword"})

        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn(ACCESS_COOKIE, res.cookies)

    def test_login_wrong_email(self):
        """Non-existent email returns 401"""
        res = self.client.post(LOGIN_URL, {"email": "nobody@example.com", "password": self.password})

        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn(ACCESS_COOKIE, res.cookies)

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
        self.assertNotIn(ACCESS_COOKIE, res.cookies)

    def test_access_token_can_authenticate_via_header(self):
        """Access cookie's value also works as a bare Authorization header (API clients)."""
        res = self.client.post(LOGIN_URL, {"email": self.email, "password": self.password})
        access_token = res.cookies[ACCESS_COOKIE].value

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        res = self.client.get(ME_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["email"], self.email)

    def test_access_cookie_can_authenticate_without_header(self):
        """The browser flow: no Authorization header, just the cookie the test client
        keeps from the login response."""
        self.client.post(LOGIN_URL, {"email": self.email, "password": self.password})

        res = self.client.get(ME_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["email"], self.email)

    def test_unauthenticated_request_rejected(self):
        res = self.client.get(ME_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class RefreshTests(APITestCase):

    def setUp(self):
        self.email = "testuser@example.com"
        self.password = "StrongPass123!"
        self.user = User.objects.create_user(email=self.email, password=self.password)
        self.client.post(LOGIN_URL, {"email": self.email, "password": self.password})

    def test_refresh_rotates_both_cookies(self):
        old_access = self.client.cookies[ACCESS_COOKIE].value
        old_refresh = self.client.cookies[REFRESH_COOKIE].value

        res = self.client.post(REFRESH_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotEqual(res.cookies[ACCESS_COOKIE].value, old_access)
        self.assertNotEqual(res.cookies[REFRESH_COOKIE].value, old_refresh)

    def test_refresh_without_cookie_returns_401(self):
        self.client.cookies.pop(REFRESH_COOKIE, None)

        res = self.client.post(REFRESH_URL)

        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_rotated_out_refresh_token_is_blacklisted(self):
        """Once rotated, the old refresh token can't be replayed."""
        old_refresh = self.client.cookies[REFRESH_COOKIE].value
        self.client.post(REFRESH_URL)  # rotates — old_refresh is now blacklisted

        self.client.cookies[REFRESH_COOKIE] = old_refresh
        res = self.client.post(REFRESH_URL)

        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class LogoutTests(APITestCase):

    def setUp(self):
        self.email = "testuser@example.com"
        self.password = "StrongPass123!"
        self.user = User.objects.create_user(email=self.email, password=self.password)
        self.client.post(LOGIN_URL, {"email": self.email, "password": self.password})

    def test_logout_clears_cookies(self):
        res = self.client.post(LOGOUT_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.cookies[ACCESS_COOKIE].value, "")
        self.assertEqual(res.cookies[REFRESH_COOKIE].value, "")

    def test_logout_blacklists_refresh_token(self):
        refresh_token = self.client.cookies[REFRESH_COOKIE].value

        self.client.post(LOGOUT_URL)

        self.client.cookies[REFRESH_COOKIE] = refresh_token
        res = self.client.post(REFRESH_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
