from decimal import Decimal
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model

from apps.catalog.models import Product, Supplier, Region, Unit
from apps.orders.models import OrderRequest, OrderRequestProduct

User = get_user_model()


def make_user(email="user@test.com"):
    return User.objects.create_user(email=email, password="pass1234")


def make_product(name):
    return Product.objects.create(name=name, unit=Unit.KG)


_supplier_counter = 0


def make_supplier(name, region=Region.CENTER):
    global _supplier_counter
    _supplier_counter += 1
    phone = f"05{_supplier_counter:08d}"
    return Supplier.objects.create(
        name=name, phone=phone, whatsapp_number=phone,
        region=region, minimum_order=0,
    )


def make_order(user, total="100.00", status_val=OrderRequest.Status.PENDING):
    return OrderRequest.objects.create(user=user, total_price=total, status=status_val)


def make_order_item(order, product, supplier, quantity="10", price="5.00"):
    return OrderRequestProduct.objects.create(
        order_request=order, product=product, supplier=supplier,
        quantity=quantity, unit_price=price,
    )


class OrderListViewTests(APITestCase):

    def setUp(self):
        self.user = make_user()
        self.other = make_user("other@test.com")
        self.client.force_authenticate(user=self.user)

    def test_returns_only_own_orders(self):
        """User sees only their own orders."""
        make_order(self.user, status_val=OrderRequest.Status.SENT)
        make_order(self.other, status_val=OrderRequest.Status.SENT)

        res = self.client.get(reverse("orders-list"))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data["results"]), 1)
        self.assertFalse(res.data["has_more"])

    def test_returns_correct_fields(self):
        """Response includes id, status, total_price, created_at, item_count."""
        order = make_order(self.user, total="50.00", status_val=OrderRequest.Status.SENT)
        product = make_product("עגבנייה")
        supplier = make_supplier("ספק א")
        make_order_item(order, product, supplier)

        res = self.client.get(reverse("orders-list"))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.data["results"][0]
        self.assertEqual(data["id"], order.id)
        self.assertEqual(float(data["total_price"]), 50.0)
        self.assertEqual(data["product_count"], 1)

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        res = self.client.get(reverse("orders-list"))
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_empty_list_when_no_orders(self):
        res = self.client.get(reverse("orders-list"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["results"], [])
        self.assertFalse(res.data["has_more"])

    def test_ordered_newest_first(self):
        """Orders are returned newest first."""
        o1 = make_order(self.user, total="10.00", status_val=OrderRequest.Status.SENT)
        o2 = make_order(self.user, total="20.00", status_val=OrderRequest.Status.SENT)

        res = self.client.get(reverse("orders-list"))

        ids = [d["id"] for d in res.data["results"]]
        self.assertEqual(ids, [o2.id, o1.id])

    def test_pagination_has_more_and_limit(self):
        """With more orders than the limit, has_more is True and results are capped."""
        for i in range(3):
            make_order(self.user, total="10.00", status_val=OrderRequest.Status.SENT)

        res = self.client.get(reverse("orders-list"), {"limit": 2})

        self.assertEqual(len(res.data["results"]), 2)
        self.assertTrue(res.data["has_more"])

    def test_pagination_offset(self):
        """offset skips the first N results."""
        orders = [
            make_order(self.user, total="10.00", status_val=OrderRequest.Status.SENT)
            for _ in range(3)
        ]
        newest_first_ids = [o.id for o in reversed(orders)]

        res = self.client.get(reverse("orders-list"), {"limit": 2, "offset": 2})

        ids = [d["id"] for d in res.data["results"]]
        self.assertEqual(ids, newest_first_ids[2:])
        self.assertFalse(res.data["has_more"])


class OrderDetailViewTests(APITestCase):

    def setUp(self):
        self.user = make_user()
        self.other = make_user("other@test.com")
        self.client.force_authenticate(user=self.user)
        self.product = make_product("עגבנייה")
        self.supplier = make_supplier("ספק א")

    def test_returns_order_with_items(self):
        """Detail view returns order with full item breakdown."""
        order = make_order(self.user, total="50.00")
        make_order_item(order, self.product, self.supplier, quantity="10", price="5.00")

        res = self.client.get(reverse("orders-detail", args=[order.id]))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["id"], order.id)
        self.assertEqual(len(res.data["products"]), 1)
        item = res.data["products"][0]
        self.assertEqual(item["product_name"], "עגבנייה")
        self.assertEqual(item["supplier_name"], "ספק א")
        self.assertEqual(float(item["subtotal"]), 50.0)

    def test_cannot_access_other_users_order(self):
        """User cannot see another user's order — returns 404."""
        order = make_order(self.other)

        res = self.client.get(reverse("orders-detail", args=[order.id]))

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_nonexistent_order_returns_404(self):
        res = self.client.get(reverse("orders-detail", args=[9999]))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_returns_401(self):
        order = make_order(self.user)
        self.client.force_authenticate(user=None)
        res = self.client.get(reverse("orders-detail", args=[order.id]))
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class OrderStatusUpdateViewTests(APITestCase):

    def setUp(self):
        self.user = make_user()
        self.other = make_user("other@test.com")
        self.client.force_authenticate(user=self.user)

    def test_update_status_to_approved(self):
        order = make_order(self.user)
        res = self.client.patch(
            reverse("orders-status", args=[order.id]),
            {"status": "approved"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "approved")
        order.refresh_from_db()
        self.assertEqual(order.status, OrderRequest.Status.APPROVED)

    def test_update_status_to_sent(self):
        order = make_order(self.user)
        order.status = OrderRequest.Status.APPROVED
        order.save(update_fields=["status"])
        res = self.client.patch(
            reverse("orders-status", args=[order.id]),
            {"status": "sent"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "sent")

    def test_illegal_transition_returns_400(self):
        order = make_order(self.user)
        res = self.client.patch(
            reverse("orders-status", args=[order.id]),
            {"status": "delivered"},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_status_returns_400(self):
        order = make_order(self.user)
        res = self.client.patch(
            reverse("orders-status", args=[order.id]),
            {"status": "invalid_status"},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_update_other_users_order(self):
        order = make_order(self.other)
        res = self.client.patch(
            reverse("orders-status", args=[order.id]),
            {"status": "approved"},
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
