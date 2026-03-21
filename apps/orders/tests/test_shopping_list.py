from decimal import Decimal
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model

from apps.catalog.models import Product, Supplier, SupplierProduct, Region, Unit
from apps.orders.models import ShoppingList, ShoppingListItem

User = get_user_model()


def make_user(email="user@test.com"):
    return User.objects.create_user(email=email, password="pass1234")


def make_product(name):
    return Product.objects.create(name=name, unit=Unit.KG)


def make_supplier(name, region=Region.CENTER):
    return Supplier.objects.create(
        name=name, phone="050000", whatsapp_number="050000",
        region=region, minimum_order=0,
    )


def make_list(user, name="רשימה א", products=None):
    sl = ShoppingList.objects.create(user=user, name=name)
    for product, qty in (products or []):
        ShoppingListItem.objects.create(shopping_list=sl, product=product, default_quantity=qty)
    return sl


class ShoppingListCRUDTests(APITestCase):

    def setUp(self):
        self.user = make_user()
        self.other = make_user("other@test.com")
        self.client.force_authenticate(user=self.user)
        self.tomato = make_product("עגבנייה")
        self.cucumber = make_product("מלפפון")

    def test_create_shopping_list_with_items(self):
        res = self.client.post(reverse("shopping-lists"), {
            "name": "הזמנה שבועית",
            "items": [
                {"product_name": self.tomato.name, "default_quantity": "10.00"},
                {"product_name": self.cucumber.name, "default_quantity": "5.00"},
            ],
        }, format="json")

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["name"], "הזמנה שבועית")
        self.assertEqual(len(res.data["items"]), 2)

    def test_list_returns_only_own_lists(self):
        make_list(self.user, "שלי")
        make_list(self.other, "שלו")

        res = self.client.get(reverse("shopping-lists"))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["name"], "שלי")

    def test_get_detail(self):
        sl = make_list(self.user, "רשימה", [(self.tomato, "8.00")])

        res = self.client.get(reverse("shopping-lists-detail", args=[sl.id]))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["id"], sl.id)
        self.assertEqual(len(res.data["items"]), 1)
        self.assertEqual(res.data["items"][0]["product_name"], "עגבנייה")

    def test_update_replaces_items(self):
        sl = make_list(self.user, "ישן", [(self.tomato, "5.00")])

        res = self.client.put(reverse("shopping-lists-detail", args=[sl.id]), {
            "name": "מעודכן",
            "items": [{"product_name": self.cucumber.name, "default_quantity": "3.00"}],
        }, format="json")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["name"], "מעודכן")
        self.assertEqual(len(res.data["items"]), 1)
        self.assertEqual(res.data["items"][0]["product_name"], "מלפפון")

    def test_delete(self):
        sl = make_list(self.user)

        res = self.client.delete(reverse("shopping-lists-detail", args=[sl.id]))

        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ShoppingList.objects.filter(pk=sl.id).exists())

    def test_cannot_access_other_users_list(self):
        sl = make_list(self.other)

        res = self.client.get(reverse("shopping-lists-detail", args=[sl.id]))

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        res = self.client.get(reverse("shopping-lists"))
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class ShoppingListSuggestTests(APITestCase):

    def setUp(self):
        self.user = make_user()
        self.client.force_authenticate(user=self.user)
        self.tomato = make_product("עגבנייה")
        self.supplier = make_supplier("ספק א")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.tomato, price_per_unit="4.00")

    def test_suggest_from_shopping_list(self):
        sl = make_list(self.user, "שבועי", [(self.tomato, "10.00")])

        res = self.client.post(
            reverse("shopping-lists-suggest", args=[sl.id]),
            {"region": Region.CENTER},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("cheapest", res.data)
        self.assertIn("fewest_suppliers", res.data)
        self.assertIn("market_comparison", res.data)

    def test_suggest_returns_404_for_other_users_list(self):
        other = make_user("other@test.com")
        sl = make_list(other, "שלו", [(self.tomato, "5.00")])

        res = self.client.post(
            reverse("shopping-lists-suggest", args=[sl.id]),
            {"region": Region.CENTER},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
