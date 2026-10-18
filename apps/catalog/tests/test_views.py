from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from apps.catalog.models import Product, ProductAlias, Unit

User = get_user_model()

PRODUCTS_URL = reverse("catalog-products")
PRODUCT_PRICES_URL = reverse("product-prices")
PRODUCT_ALIASES_URL = reverse("catalog-product-aliases")


class ProductListPaginationTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(email="user@test.com", password="pass1234")
        self.client.force_authenticate(user=self.user)

    def test_default_page_shape(self):
        res = self.client.get(PRODUCTS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("results", res.data)
        self.assertIn("has_more", res.data)

    def test_limit_caps_results_and_sets_has_more(self):
        for i in range(3):
            Product.objects.create(name=f"מוצר {i}", unit=Unit.KG)

        res = self.client.get(PRODUCTS_URL, {"limit": 2})

        self.assertEqual(len(res.data["results"]), 2)
        self.assertTrue(res.data["has_more"])

    def test_no_more_when_under_limit(self):
        Product.objects.create(name="מוצר יחיד", unit=Unit.KG)

        res = self.client.get(PRODUCTS_URL, {"limit": 20})

        self.assertEqual(len(res.data["results"]), 1)
        self.assertFalse(res.data["has_more"])

    def test_all_param_bypasses_pagination(self):
        for i in range(25):
            Product.objects.create(name=f"מוצר {i:02d}", unit=Unit.KG)

        res = self.client.get(PRODUCTS_URL, {"all": "1"})

        self.assertEqual(len(res.data["results"]), 25)
        self.assertFalse(res.data["has_more"])

    def test_search_finds_product_beyond_first_page(self):
        """A product far past the default page size must still be found by search."""
        for i in range(250):
            Product.objects.create(name=f"מוצר {i:03d}", unit=Unit.KG)
        Product.objects.create(name="חסה", unit=Unit.KG)

        res = self.client.get(PRODUCTS_URL, {"search": "חסה"})

        names = [p["name"] for p in res.data["results"]]
        self.assertIn("חסה", names)
        self.assertFalse(res.data["has_more"])

    def test_search_is_paginated_too(self):
        for i in range(5):
            Product.objects.create(name=f"עגבנייה {i}", unit=Unit.KG)
        Product.objects.create(name="מלפפון", unit=Unit.KG)

        res = self.client.get(PRODUCTS_URL, {"search": "עגבנייה", "limit": 2})

        self.assertEqual(len(res.data["results"]), 2)
        self.assertTrue(res.data["has_more"])
        for p in res.data["results"]:
            self.assertIn("עגבנייה", p["name"])


class ProductCatalogPaginationTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(email="user2@test.com", password="pass1234")
        self.client.force_authenticate(user=self.user)

    def test_limit_caps_results_and_sets_has_more(self):
        for i in range(3):
            Product.objects.create(name=f"מוצר {i}", unit=Unit.KG)

        res = self.client.get(PRODUCT_PRICES_URL, {"limit": 2})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data["results"]), 2)


class ProductAliasViewTests(APITestCase):
    """Admin-managed synonym dictionary (e.g. "בצל לבן" -> "בצל יבש")."""

    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="admin123")
        self.user = User.objects.create_user(email="user@test.com", password="pass1234")
        self.onion = Product.objects.create(name="בצל יבש", unit=Unit.KG)

    def test_non_admin_cannot_create_alias(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.post(PRODUCT_ALIASES_URL, {"product": self.onion.id, "alias": "בצל לבן"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(ProductAlias.objects.count(), 0)

    def test_admin_can_create_alias(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(PRODUCT_ALIASES_URL, {"product": self.onion.id, "alias": "בצל לבן"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ProductAlias.objects.get().alias, "בצל לבן")

    def test_duplicate_alias_text_rejected_with_hebrew_message(self):
        self.client.force_authenticate(user=self.admin)
        ProductAlias.objects.create(product=self.onion, alias="בצל לבן")
        res = self.client.post(PRODUCT_ALIASES_URL, {"product": self.onion.id, "alias": "בצל לבן"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("כבר קיים", str(res.data))

    def test_list_filters_by_product(self):
        other = Product.objects.create(name="בצל סגול", unit=Unit.KG)
        ProductAlias.objects.create(product=self.onion, alias="בצל לבן")
        ProductAlias.objects.create(product=other, alias="בצל אדום")
        self.client.force_authenticate(user=self.admin)

        res = self.client.get(PRODUCT_ALIASES_URL, {"product": self.onion.id})

        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["alias"], "בצל לבן")

    def test_admin_can_delete_alias(self):
        alias = ProductAlias.objects.create(product=self.onion, alias="בצל לבן")
        self.client.force_authenticate(user=self.admin)

        res = self.client.delete(f"{PRODUCT_ALIASES_URL}{alias.id}/")

        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(ProductAlias.objects.count(), 0)

    def test_product_list_nests_its_aliases(self):
        ProductAlias.objects.create(product=self.onion, alias="בצל לבן")
        self.client.force_authenticate(user=self.user)

        res = self.client.get(PRODUCTS_URL)

        onion_row = next(p for p in res.data["results"] if p["id"] == self.onion.id)
        self.assertEqual([a["alias"] for a in onion_row["aliases"]], ["בצל לבן"])



class SupplierBlockingPermissionTests(APITestCase):
    """Blocking a supplier is an admin decision: nobody else can set or lift it."""

    def setUp(self):
        from django.utils import timezone
        from apps.catalog.models import Supplier

        self.timezone = timezone
        self.supplier = Supplier.objects.create(
            name="ספק בדיקה", phone="0500000001", whatsapp_number="+972500000001",
            region="center", minimum_order=0,
        )
        self.url = reverse("catalog-supplier-detail", args=[self.supplier.id])
        self.admin = User.objects.create_user(email="admin@test.com", password="pass1234", is_staff=True)
        self.customer = User.objects.create_user(email="customer@test.com", password="pass1234")
        self.until = (timezone.now() + timezone.timedelta(days=10)).isoformat()

    def test_a_customer_cannot_block_a_supplier(self):
        self.client.force_authenticate(user=self.customer)

        res = self.client.patch(self.url, {"blocked_until": self.until}, format="json")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.supplier.refresh_from_db()
        self.assertIsNone(self.supplier.blocked_until)

    def test_an_anonymous_visitor_cannot_block_a_supplier(self):
        res = self.client.patch(self.url, {"blocked_until": self.until}, format="json")

        self.assertIn(res.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.supplier.refresh_from_db()
        self.assertIsNone(self.supplier.blocked_until)

    def test_a_customer_cannot_lift_a_block_either(self):
        self.supplier.blocked_until = self.timezone.now() + self.timezone.timedelta(days=10)
        self.supplier.save(update_fields=["blocked_until"])
        self.client.force_authenticate(user=self.customer)

        res = self.client.patch(self.url, {"blocked_until": None}, format="json")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.supplier.refresh_from_db()
        self.assertIsNotNone(self.supplier.blocked_until)

    def test_an_admin_can_block_and_lift(self):
        self.client.force_authenticate(user=self.admin)

        res = self.client.patch(self.url, {"blocked_until": self.until}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.supplier.refresh_from_db()
        self.assertIsNotNone(self.supplier.blocked_until)

        res = self.client.patch(self.url, {"blocked_until": None}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.supplier.refresh_from_db()
        self.assertIsNone(self.supplier.blocked_until)
