from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient

from apps.catalog.models import SupplierProduct
from apps.orders.models import OrderRequest, OrderRequestProduct
from apps.orders.tests.factories import make_order
from apps.orders.tests.test_webhook import make_product, make_supplier, make_user_with_profile

CHANGE = reverse("orders-open-change")
CANCEL = reverse("orders-open-cancel")


class OpenOrderChangeTests(TestCase):
    """Changing / cancelling the open order through the API (website and MCP), same rules as WhatsApp."""

    def setUp(self):
        cache.clear()
        self.tomato = make_product("עגבניה")
        self.potato = make_product("תפוח אדמה אדום")
        self.supplier = make_supplier("ספק א")
        for product, price in ((self.tomato, "5.00"), (self.potato, "3.00")):
            SupplierProduct.objects.create(supplier=self.supplier, product=product, price_per_unit=price)
        self.user = make_user_with_profile(email="a@test.com", phone="+972507777771")
        self.order = make_order(self.user, self.supplier, status=OrderRequest.Status.SENT, total_price=Decimal("50.00"))
        self.orp = OrderRequestProduct.objects.create(
            order_request=self.order, product=self.tomato, supplier=self.supplier,
            quantity=Decimal("10"), unit_price=Decimal("5.00"),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        send = patch("apps.orders.whatsapp.validators.send_whatsapp_message")
        self.mock_send = send.start()
        self.addCleanup(send.stop)
        for target, kwargs in (
            ("apps.orders.services.before_update_cutoff", {"return_value": True}),
            ("apps.orders.whatsapp.user_flow.timezone.localtime",
             {"return_value": django_timezone.make_aware(datetime(2026, 1, 1, 10, 0))}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _change(self, name, quantity, intent="add", dry_run=False, client=None):
        return (client or self.client).post(CHANGE, {
            "changes": [{"product_name": name, "quantity": quantity, "intent": intent}], "dry_run": dry_run,
        }, format="json")

    def _told(self, supplier):
        return [c[0][1] for c in self.mock_send.call_args_list if c[0][0] == supplier.whatsapp_number]

    # ── change ──

    def test_dry_run_reports_and_changes_nothing(self):
        res = self._change("תפוח אדמה אדום", 5, dry_run=True)

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["changes"])
        self.assertFalse(res.data["applied"])
        self.assertFalse(OrderRequestProduct.objects.filter(product=self.potato).exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.SENT)
        self.mock_send.assert_not_called()

    def test_add_creates_the_line_and_tells_the_supplier(self):
        res = self._change("תפוח אדמה אדום", 5)

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["applied"])
        line = OrderRequestProduct.objects.get(order_request=self.order, product=self.potato)
        self.assertEqual(line.quantity, Decimal("5"))
        self.assertTrue(self._told(self.supplier))

    def test_reduce_lowers_the_quantity_and_tells_the_supplier(self):
        res = self._change("עגבניה", 4, intent="reduce")

        self.assertEqual(res.status_code, 200)
        self.orp.refresh_from_db()
        self.assertEqual(self.orp.quantity, Decimal("6"))
        self.assertTrue(self._told(self.supplier))

    def test_update_sets_the_total(self):
        self._change("עגבניה", 15, intent="update")
        self.orp.refresh_from_db()
        self.assertEqual(self.orp.quantity, Decimal("15"))

    def test_unknown_product_is_reported_not_guessed(self):
        res = self._change("מוצר שלא קיים", 3)
        self.assertEqual(res.data["not_found"], ["מוצר שלא קיים"])
        self.assertFalse(res.data["applied"])

    def test_reducing_what_is_not_in_the_order_is_blocked(self):
        res = self._change("תפוח אדמה אדום", 2, intent="reduce")
        self.assertTrue(res.data["blocked"])
        self.assertFalse(res.data["applied"])

    def test_supplier_cutoff_blocks_the_change(self):
        cache.set(f"supplier_cutoff:{self.supplier.whatsapp_number}:{self.order.id}", "00:01")
        res = self._change("עגבניה", 2)
        self.assertTrue(any("שעת הגבלה" in b for b in res.data["blocked"]))
        self.orp.refresh_from_db()
        self.assertEqual(self.orp.quantity, Decimal("10"))

    def test_no_open_order_is_404(self):
        other = make_user_with_profile(email="none@test.com", phone="+972507777772")
        client = APIClient()
        client.force_authenticate(other)
        self.assertEqual(self._change("עגבניה", 1, client=client).status_code, 404)

    def test_a_supplier_that_cannot_be_told_rolls_the_change_back(self):
        self.mock_send.side_effect = RuntimeError("twilio down")
        res = self._change("תפוח אדמה אדום", 5)

        self.assertEqual(res.status_code, 502)
        self.assertFalse(OrderRequestProduct.objects.filter(product=self.potato).exists())

    def test_another_customers_order_is_never_touched(self):
        other = make_user_with_profile(email="b@test.com", phone="+972507777773")
        other_order = make_order(other, self.supplier, status=OrderRequest.Status.SENT, total_price=Decimal("50"))
        other_line = OrderRequestProduct.objects.create(
            order_request=other_order, product=self.tomato, supplier=self.supplier,
            quantity=Decimal("10"), unit_price=Decimal("5.00"),
        )
        self._change("עגבניה", 8, intent="reduce")
        other_line.refresh_from_db()
        self.assertEqual(other_line.quantity, Decimal("10"))

    def test_anonymous_is_rejected(self):
        res = APIClient().post(CHANGE, {"changes": [{"product_name": "עגבניה", "quantity": 1}]}, format="json")
        self.assertEqual(res.status_code, 401)

    def test_bad_input_is_400(self):
        for body in ({"changes": []}, {"changes": [{"product_name": "עגבניה", "quantity": -1}]},
                     {"changes": [{"product_name": "עגבניה", "quantity": 1, "intent": "delete"}]}):
            self.assertEqual(self.client.post(CHANGE, body, format="json").status_code, 400, body)

    # ── cancel ──

    def test_cancel_dry_run_lists_and_cancels_nothing(self):
        res = self.client.post(CANCEL, {"dry_run": True}, format="json")

        self.assertEqual(res.status_code, 200)
        self.assertEqual([o["order_id"] for o in res.data["orders"]], [self.order.id])
        self.assertFalse(res.data["cancelled"])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.SENT)
        self.mock_send.assert_not_called()

    def test_cancel_cancels_and_tells_the_supplier(self):
        res = self.client.post(CANCEL, {}, format="json")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["cancelled"])
        self.assertEqual(res.data["suppliers_notified"], ["ספק א"])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.CANCELLED)
        self.assertIn("ביטל את הזמנה", self._told(self.supplier)[-1])

    def test_cancel_twice_the_second_finds_nothing(self):
        self.client.post(CANCEL, {}, format="json")
        self.assertEqual(self.client.post(CANCEL, {}, format="json").status_code, 404)

    def test_shipped_orders_are_not_cancelled(self):
        self.order.status = OrderRequest.Status.SHIPPED
        self.order.save()
        res = self.client.post(CANCEL, {}, format="json")
        self.assertEqual(res.status_code, 409)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.SHIPPED)

    def test_cancel_only_touches_the_callers_orders(self):
        other = make_user_with_profile(email="c@test.com", phone="+972507777774")
        other_order = make_order(other, self.supplier, status=OrderRequest.Status.SENT, total_price=Decimal("50"))
        self.client.post(CANCEL, {}, format="json")
        other_order.refresh_from_db()
        self.assertEqual(other_order.status, OrderRequest.Status.SENT)
