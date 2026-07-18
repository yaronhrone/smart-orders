from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from apps.catalog.models import Product, Unit

User = get_user_model()

PRODUCTS_URL = reverse("catalog-products")
PRODUCT_PRICES_URL = reverse("product-prices")


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
        self.assertTrue(res.data["has_more"])
