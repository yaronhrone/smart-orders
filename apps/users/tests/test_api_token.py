from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.users.models import ApiToken, Profile

User = get_user_model()

OPEN_URL = reverse("orders-open")


class ApiTokenAuthTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(email="c@test.com", password="pass1234")
        Profile.objects.create(user=self.user)

    def _get(self, raw):
        return self.client.get(OPEN_URL, HTTP_AUTHORIZATION=f"Bearer {raw}")

    def test_issue_stores_only_the_hash(self):
        token, raw = ApiToken.issue(self.user, "mcp")
        self.assertTrue(raw.startswith("so_"))
        self.assertNotIn(raw, token.token_hash)
        self.assertEqual(token.token_hash, ApiToken.hash_token(raw))

    def test_valid_token_authenticates_and_marks_last_used(self):
        token, raw = ApiToken.issue(self.user)
        res = self._get(raw)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        token.refresh_from_db()
        self.assertIsNotNone(token.last_used_at)

    def test_unknown_token_rejected(self):
        self.assertEqual(self._get("so_nope").status_code, status.HTTP_401_UNAUTHORIZED)

    def test_revoked_token_rejected(self):
        token, raw = ApiToken.issue(self.user)
        token.revoked = True
        token.save()
        self.assertEqual(self._get(raw).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_user_rejected(self):
        _, raw = ApiToken.issue(self.user)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self._get(raw).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_no_header_is_unauthenticated(self):
        self.assertEqual(self.client.get(OPEN_URL).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_jwt_still_works(self):
        from rest_framework_simplejwt.tokens import AccessToken
        jwt = str(AccessToken.for_user(self.user))
        self.assertEqual(self._get(jwt).status_code, status.HTTP_200_OK)
