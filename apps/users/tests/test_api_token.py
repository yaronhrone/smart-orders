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


class ApiTokenManagementTests(APITestCase):

    URL = reverse("api-tokens")

    def setUp(self):
        self.user = User.objects.create_user(email="m@test.com", password="pass1234")
        Profile.objects.create(user=self.user)
        self.other = User.objects.create_user(email="o@test.com", password="pass1234")

    def test_create_returns_secret_once_and_it_works(self):
        self.client.force_authenticate(self.user)
        res = self.client.post(self.URL, {"name": "laptop"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        raw = res.data["token"]
        self.assertTrue(raw.startswith("so_"))
        self.assertIsNotNone(res.data["expires_at"])
        listed = self.client.get(self.URL)
        self.assertNotIn("token", listed.data["results"][0])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(OPEN_URL, HTTP_AUTHORIZATION=f"Bearer {raw}").status_code, 200)

    def test_limit_of_active_tokens(self):
        self.client.force_authenticate(self.user)
        for _ in range(3):
            self.assertEqual(self.client.post(self.URL, {}).status_code, 201)
        self.assertEqual(self.client.post(self.URL, {}).status_code, 409)

    def test_revoke_own_token_only(self):
        mine, raw = ApiToken.issue(self.user)
        theirs, _ = ApiToken.issue(self.other)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.delete(reverse("api-token-revoke", args=[theirs.id])).status_code, 404)
        self.assertEqual(self.client.delete(reverse("api-token-revoke", args=[mine.id])).status_code, 204)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(OPEN_URL, HTTP_AUTHORIZATION=f"Bearer {raw}").status_code, 401)

    def test_expired_token_rejected(self):
        from datetime import timedelta
        from django.utils import timezone
        _, raw = ApiToken.issue(self.user, expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.client.get(OPEN_URL, HTTP_AUTHORIZATION=f"Bearer {raw}").status_code, 401)

    def test_api_token_cannot_mint_tokens(self):
        _, raw = ApiToken.issue(self.user)
        res = self.client.post(self.URL, {}, HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(res.status_code, 401)

    def test_my_prices_only_own_region_and_not_blocked(self):
        from datetime import timedelta
        from decimal import Decimal
        from django.utils import timezone
        from apps.catalog.models import Product, Supplier, SupplierProduct
        self.user.profile.region = "center"
        self.user.profile.save()
        product = Product.objects.create(name="עגבנייה-טסט", unit="kg")
        for i, (name, region, blocked) in enumerate([("קרוב", "center", None), ("רחוק", "north", None),
                                                     ("חסום", "center", timezone.now() + timedelta(days=1))]):
            s = Supplier.objects.create(name=name, phone=f"050000000{i}", whatsapp_number=f"+97250000000{i}", region=region, blocked_until=blocked)
            SupplierProduct.objects.create(supplier=s, product=product, price_per_unit=Decimal("5"))
        self.client.force_authenticate(self.user)
        res = self.client.get(reverse("my-prices"), {"product": "עגבנייה-טסט"})
        self.assertEqual(res.status_code, 200)
        offers = [o["supplier"] for g in res.data["prices"] for o in g["offers"]]
        self.assertEqual(offers, ["קרוב"])
