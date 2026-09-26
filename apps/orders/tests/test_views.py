from decimal import Decimal
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model

from apps.catalog.models import Product, Supplier, Region, Unit
from apps.orders.models import OrderRequest, OrderRequestProduct
from apps.orders.tests.factories import make_order as make_supplier_order

User = get_user_model()


def make_user(email="user@test.com"):
    return User.objects.create_user(email=email, password="pass1234")


def make_product(name):
    return Product.objects.create(name=name, unit=Unit.KG)


_supplier_counter = 0


def make_supplier(name=None, region=Region.CENTER):
    global _supplier_counter
    _supplier_counter += 1
    phone = f"05{_supplier_counter:08d}"
    return Supplier.objects.create(
        name=name or f"ספק {_supplier_counter}", phone=phone, whatsapp_number=phone,
        region=region, minimum_order=0,
    )


def make_order(user, total="100.00", status_val=OrderRequest.Status.PENDING, supplier=None, batch=None):
    return make_supplier_order(
        user, supplier or make_supplier(), batch=batch, total_price=total, status=status_val,
    )


def make_order_item(order, product, quantity="10", price="5.00"):
    return OrderRequestProduct.objects.create(
        order_request=order, product=product, supplier=order.supplier,
        quantity=quantity, unit_price=price,
    )


class OrderBatchListViewTests(APITestCase):

    def setUp(self):
        self.user = make_user()
        self.other = make_user("other@test.com")
        self.client.force_authenticate(user=self.user)

    def test_returns_only_own_batches(self):
        make_order(self.user, status_val=OrderRequest.Status.SENT)
        make_order(self.other, status_val=OrderRequest.Status.SENT)

        res = self.client.get(reverse("orders-batches"))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data["results"]), 1)
        self.assertFalse(res.data["has_more"])

    def test_one_checkout_with_two_suppliers_is_one_row_with_two_orders(self):
        """The whole point of the redesign: one checkout, one row, expanding
        into one order per supplier — each with its own status and total."""
        a = make_order(self.user, total="120.00", status_val=OrderRequest.Status.APPROVED)
        b = make_order(self.user, total="80.00", status_val=OrderRequest.Status.SENT, batch=a.batch)
        make_order_item(a, make_product("עגבנייה"))
        make_order_item(b, make_product("גזר"))

        res = self.client.get(reverse("orders-batches"))

        self.assertEqual(len(res.data["results"]), 1)
        batch = res.data["results"][0]
        self.assertEqual(batch["id"], a.batch_id)
        self.assertEqual(float(batch["total_price"]), 200.0)
        # Overall status = the least advanced order ("only as done as its slowest supplier").
        self.assertEqual(batch["status"], "sent")
        self.assertEqual([o["id"] for o in batch["orders"]], [a.id, b.id])
        self.assertEqual(batch["orders"][0]["supplier_name"], a.supplier.name)
        self.assertEqual(batch["orders"][0]["status"], "approved")
        self.assertEqual(batch["orders"][0]["product_count"], 1)

    def test_cancelled_order_excluded_from_total_and_status(self):
        a = make_order(self.user, total="120.00", status_val=OrderRequest.Status.DELIVERED)
        make_order(self.user, total="80.00", status_val=OrderRequest.Status.CANCELLED, batch=a.batch)

        batch = self.client.get(reverse("orders-batches")).data["results"][0]

        self.assertEqual(float(batch["total_price"]), 120.0)
        self.assertEqual(batch["status"], "delivered")

    def test_all_pending_batch_is_hidden(self):
        """A checkout that never reached a supplier isn't an order yet."""
        make_order(self.user, status_val=OrderRequest.Status.PENDING)
        res = self.client.get(reverse("orders-batches"))
        self.assertEqual(res.data["results"], [])

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        res = self.client.get(reverse("orders-batches"))
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_empty_list_when_no_orders(self):
        res = self.client.get(reverse("orders-batches"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["results"], [])
        self.assertFalse(res.data["has_more"])

    def test_ordered_newest_first(self):
        o1 = make_order(self.user, status_val=OrderRequest.Status.SENT)
        o2 = make_order(self.user, status_val=OrderRequest.Status.SENT)

        res = self.client.get(reverse("orders-batches"))

        ids = [d["id"] for d in res.data["results"]]
        self.assertEqual(ids, [o2.batch_id, o1.batch_id])

    def test_pagination_has_more_and_limit(self):
        for _ in range(3):
            make_order(self.user, status_val=OrderRequest.Status.SENT)

        res = self.client.get(reverse("orders-batches"), {"limit": 2})

        self.assertEqual(len(res.data["results"]), 2)
        self.assertTrue(res.data["has_more"])

    def test_pagination_offset(self):
        orders = [make_order(self.user, status_val=OrderRequest.Status.SENT) for _ in range(3)]
        newest_first_ids = [o.batch_id for o in reversed(orders)]

        res = self.client.get(reverse("orders-batches"), {"limit": 2, "offset": 2})

        ids = [d["id"] for d in res.data["results"]]
        self.assertEqual(ids, newest_first_ids[2:])
        self.assertFalse(res.data["has_more"])

    def test_batch_with_two_orders_counted_once(self):
        """Filtering batches through their orders mustn't duplicate a batch per matching order."""
        a = make_order(self.user, status_val=OrderRequest.Status.SENT)
        make_order(self.user, status_val=OrderRequest.Status.SENT, batch=a.batch)

        res = self.client.get(reverse("orders-batches"))

        self.assertEqual(len(res.data["results"]), 1)


class OrderDetailViewTests(APITestCase):

    def setUp(self):
        self.user = make_user()
        self.other = make_user("other@test.com")
        self.client.force_authenticate(user=self.user)
        self.product = make_product("עגבנייה")
        self.supplier = make_supplier("ספק א")

    def test_returns_order_with_items_supplier_and_batch(self):
        order = make_order(self.user, total="50.00", supplier=self.supplier)
        make_order_item(order, self.product, quantity="10", price="5.00")

        res = self.client.get(reverse("orders-detail", args=[order.id]))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["id"], order.id)
        self.assertEqual(res.data["supplier_name"], "ספק א")
        self.assertEqual(res.data["batch_id"], order.batch_id)
        self.assertEqual(len(res.data["products"]), 1)
        item = res.data["products"][0]
        self.assertEqual(item["product_name"], "עגבנייה")
        self.assertEqual(float(item["subtotal"]), 50.0)

    def test_cannot_access_other_users_order(self):
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

    def test_update_status_to_sent(self):
        order = make_order(self.user)
        res = self.client.patch(reverse("orders-status", args=[order.id]), {"status": "sent"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "sent")
        order.refresh_from_db()
        self.assertEqual(order.status, OrderRequest.Status.SENT)

    def test_update_status_to_approved(self):
        order = make_order(self.user, status_val=OrderRequest.Status.SENT)
        res = self.client.patch(reverse("orders-status", args=[order.id]), {"status": "approved"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "approved")

    def test_updating_one_order_leaves_its_sibling_alone(self):
        """Each supplier's order has its own status — marking one delivered
        must not touch the other order from the same checkout."""
        a = make_order(self.user, status_val=OrderRequest.Status.APPROVED)
        b = make_order(self.user, status_val=OrderRequest.Status.APPROVED, batch=a.batch)

        self.client.patch(reverse("orders-status", args=[a.id]), {"status": "delivered"})

        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.status, OrderRequest.Status.DELIVERED)
        self.assertEqual(b.status, OrderRequest.Status.APPROVED)

    def test_illegal_transition_returns_400(self):
        order = make_order(self.user)
        res = self.client.patch(reverse("orders-status", args=[order.id]), {"status": "delivered"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_pending_cannot_jump_to_approved(self):
        order = make_order(self.user)
        res = self.client.patch(reverse("orders-status", args=[order.id]), {"status": "approved"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_status_returns_400(self):
        order = make_order(self.user)
        res = self.client.patch(reverse("orders-status", args=[order.id]), {"status": "invalid_status"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_update_other_users_order(self):
        order = make_order(self.other)
        res = self.client.patch(reverse("orders-status", args=[order.id]), {"status": "approved"})
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_staff_can_update_any_users_order(self):
        staff = User.objects.create_user(email="staff@test.com", password="pass1234", is_staff=True)
        self.client.force_authenticate(user=staff)
        order = make_order(self.other, status_val=OrderRequest.Status.SENT)

        res = self.client.patch(reverse("orders-status", args=[order.id]), {"status": "delivered"})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderRequest.Status.DELIVERED)


class AdminOrderBatchListViewTests(APITestCase):

    def setUp(self):
        self.staff = User.objects.create_user(email="staff@test.com", password="pass1234", is_staff=True)
        self.customer = make_user("customer@test.com")

    def test_non_staff_forbidden(self):
        self.client.force_authenticate(user=self.customer)
        res = self.client.get(reverse("orders-admin-batches"))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_sees_batches_across_customers(self):
        make_order(self.customer, status_val=OrderRequest.Status.SENT)
        self.client.force_authenticate(user=self.staff)

        res = self.client.get(reverse("orders-admin-batches"))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(res.data["results"][0]["customer_email"], "customer@test.com")

    def test_default_shows_only_batches_with_an_open_order(self):
        open_order = make_order(self.customer, status_val=OrderRequest.Status.SENT)
        make_order(self.customer, status_val=OrderRequest.Status.DELIVERED)
        make_order(self.customer, status_val=OrderRequest.Status.CANCELLED)
        self.client.force_authenticate(user=self.staff)

        res = self.client.get(reverse("orders-admin-batches"))

        self.assertEqual([b["id"] for b in res.data["results"]], [open_order.batch_id])

    def test_batch_with_one_open_and_one_delivered_order_still_listed_with_both(self):
        a = make_order(self.customer, status_val=OrderRequest.Status.DELIVERED)
        make_order(self.customer, status_val=OrderRequest.Status.SENT, batch=a.batch)
        self.client.force_authenticate(user=self.staff)

        res = self.client.get(reverse("orders-admin-batches"))

        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(len(res.data["results"][0]["orders"]), 2)

    def test_status_filter_overrides_default(self):
        make_order(self.customer, status_val=OrderRequest.Status.DELIVERED)
        self.client.force_authenticate(user=self.staff)

        res = self.client.get(reverse("orders-admin-batches"), {"status": "delivered"})

        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(res.data["results"][0]["status"], "delivered")


class PlaceAndSuggestViewTests(APITestCase):
    """Site checkout: one recommended option, and a WhatsApp to the customer."""

    def setUp(self):
        from unittest.mock import patch
        from apps.catalog.models import SupplierProduct
        from apps.users.models import Profile

        self.user = make_user()
        Profile.objects.create(user=self.user, phone="0501112222", region=Region.CENTER)
        self.client.force_authenticate(user=self.user)
        self.tomato = make_product("עגבנייה")
        self.carrot = make_product("גזר")
        self.a = make_supplier("ספק א")
        self.b = make_supplier("ספק ב")
        SupplierProduct.objects.create(supplier=self.a, product=self.tomato, price_per_unit="5.00")
        SupplierProduct.objects.create(supplier=self.b, product=self.carrot, price_per_unit="3.00")

        task_patch = patch("apps.orders.tasks.send_supplier_order_notification_task")
        send_patch = patch("apps.orders.whatsapp.validators.send_whatsapp_message")
        task_patch.start()
        self.mock_send = send_patch.start()
        self.addCleanup(task_patch.stop)
        self.addCleanup(send_patch.stop)

    def _basket(self):
        return {"products": [
            {"product_name": "עגבנייה", "quantity": "10"},
            {"product_name": "גזר", "quantity": "4"},
        ]}

    def test_suggest_returns_the_single_recommended_scenario(self):
        res = self.client.post(reverse("orders-suggest"), self._basket(), format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn(res.data["recommended"], ("cheapest", "fewest_suppliers"))

    def test_place_whatsapps_the_customer_every_order_number(self):
        res = self.client.post(
            reverse("orders-place"), {**self._basket(), "scenario": "cheapest"}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data["orders"]), 2)
        customer_calls = [c for c in self.mock_send.call_args_list if c[0][0] == "+972501112222"]
        self.assertEqual(len(customer_calls), 1)
        msg = customer_calls[0][0][1]
        for o in res.data["orders"]:
            self.assertIn(f"הזמנה #{o['order_id']} — {o['supplier_name']}", msg)
