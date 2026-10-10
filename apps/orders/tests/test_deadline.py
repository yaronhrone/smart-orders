from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catalog.models import SupplierProduct
from apps.orders.models import OrderRequest, OrderRequestProduct, SupplierConfirmation
from apps.orders.tests.factories import make_order
from apps.orders.tests.test_webhook import make_product, make_supplier, make_user_with_profile
from apps.orders.whatsapp.cache import save_supplier_pending_order
from apps.orders.whatsapp.deadline_flow import confirmation_deadline, handle_overdue_orders, overdue_orders

IL = ZoneInfo("Asia/Jerusalem")
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
ADMIN = "+972500000099"
CUSTOMER = "+972509999999"


def il_today(hour, minute=0, days_ago=0):
    day = timezone.now().astimezone(IL).date() - timedelta(days=days_ago)
    return datetime.combine(day, time(hour, minute), tzinfo=IL)


@override_settings(CACHES=LOCMEM_CACHE, ADMIN_WHATSAPP_NUMBER=ADMIN)
class SupplierConfirmationDeadlineTests(TestCase):
    """A supplier who hasn't confirmed by 18:00 (or 2 hours after sending) loses the order to the next supplier."""

    def setUp(self):
        cache.clear()
        self.tomato = make_product("עגבניה")
        self.a = make_supplier("ספק א")
        self.b = make_supplier("ספק ב")
        SupplierProduct.objects.create(supplier=self.a, product=self.tomato, price_per_unit="5.00")
        SupplierProduct.objects.create(supplier=self.b, product=self.tomato, price_per_unit="6.00")
        self.user = make_user_with_profile(phone=CUSTOMER)
        self.order = self._order(sent_at=il_today(10))
        send = patch("apps.orders.whatsapp.validators.send_whatsapp_message")
        self.mock_send = send.start()
        self.addCleanup(send.stop)

    def _order(self, sent_at, supplier=None):
        order = make_order(self.user, supplier or self.a, status=OrderRequest.Status.SENT, total_price=Decimal("50"))
        OrderRequestProduct.objects.create(
            order_request=order, product=self.tomato, supplier=supplier or self.a,
            quantity=Decimal("10"), unit_price=Decimal("5.00"),
        )
        OrderRequest.objects.filter(pk=order.pk).update(created_at=sent_at)
        order.refresh_from_db()
        return order

    def _to(self, phone):
        return [c[0][1] for c in self.mock_send.call_args_list if c[0][0] == phone]

    def test_deadline_is_18_00_or_two_hours_after_sending(self):
        self.assertEqual(confirmation_deadline(il_today(10)), il_today(18))
        self.assertEqual(confirmation_deadline(il_today(17, 30)), il_today(19, 30))
        self.assertEqual(confirmation_deadline(il_today(20)), il_today(22))

    def test_not_overdue_before_the_deadline(self):
        self.assertEqual(overdue_orders(now=il_today(17, 59)), [])
        self.assertEqual(overdue_orders(now=il_today(18)), [self.order])

    def test_unconfirmed_order_moves_to_the_next_supplier_without_blocking_anyone(self):
        handled = handle_overdue_orders(now=il_today(18))

        self.assertEqual(handled, 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.CANCELLED)
        moved = OrderRequestProduct.objects.get(order_request__batch=self.order.batch, product=self.tomato)
        self.assertEqual((moved.supplier, moved.order_request.status), (self.b, OrderRequest.Status.SENT))
        self.a.refresh_from_db()
        self.assertIsNone(self.a.blocked_until)

        self.assertIn(f"הזמנה #{self.order.id} בוטלה כי לא אושרה עד 18:00", self._to(self.a.whatsapp_number)[-1])
        self.assertIn("מבקש להזמין", self._to(self.b.whatsapp_number)[-1])
        customer = self._to(CUSTOMER)[-1]
        self.assertIn(f"לא אישר את הזמנה #{self.order.id} עד 18:00", customer)
        self.assertIn("הועבר לספק חלופי", customer)
        self.assertIn("הספק לא נחסם", self._to(ADMIN)[-1])

    def test_each_order_is_handled_once(self):
        # The replacement order for ספק ב is created "at 18:00", so it has until 20:00.
        with patch("django.utils.timezone.now", return_value=il_today(18)):
            handle_overdue_orders(now=il_today(18))
        sent = self.mock_send.call_count

        self.assertEqual(handle_overdue_orders(now=il_today(18, 15)), 0)
        self.assertEqual(self.mock_send.call_count, sent)

    def test_the_replacement_supplier_also_gets_two_hours(self):
        with patch("django.utils.timezone.now", return_value=il_today(18)):
            handle_overdue_orders(now=il_today(18))
        replacement = OrderRequest.objects.get(batch=self.order.batch, supplier=self.b)

        self.assertEqual(confirmation_deadline(replacement.created_at), il_today(20))
        self.assertEqual(handle_overdue_orders(now=il_today(20)), 1)  # ספק ב didn't answer either
        self.assertEqual(self._to(CUSTOMER)[-1].count("לא אישר"), 1)

    def test_an_order_sent_in_the_evening_gets_two_hours(self):
        OrderRequest.objects.filter(pk=self.order.pk).update(created_at=il_today(20))

        self.assertEqual(handle_overdue_orders(now=il_today(21, 45)), 0)
        self.assertEqual(handle_overdue_orders(now=il_today(22)), 1)

    def test_no_other_supplier_tells_the_customer(self):
        SupplierProduct.objects.filter(supplier=self.b).delete()

        handle_overdue_orders(now=il_today(18))

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.CANCELLED)
        self.assertIn("אין ספק חלופי עבור: עגבניה", self._to(CUSTOMER)[-1])

    @patch("apps.orders.tasks.handle_reroute_grace_timeout.apply_async")
    def test_next_supplier_below_minimum_asks_the_customer_to_top_up(self, mock_grace_timeout):
        self.b.minimum_order = Decimal("1000")
        self.b.save(update_fields=["minimum_order"])

        handle_overdue_orders(now=il_today(18))

        customer = self._to(CUSTOMER)[-1]
        self.assertIn("ספק ב: חסר 940.00₪", customer)  # 1000 - 10 * 6
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.SENT)  # held for the top-up
        self.assertEqual(handle_overdue_orders(now=il_today(18, 15)), 0)  # and not handled again

    def test_partly_confirmed_order_only_alerts_the_admin(self):
        lettuce = make_product("חסה")
        OrderRequestProduct.objects.create(
            order_request=self.order, product=lettuce, supplier=self.a, quantity=Decimal("2"), unit_price=Decimal("4"),
        )
        SupplierConfirmation.objects.create(
            order_request_product=self.order.products.get(product=self.tomato), confirmed_quantity=Decimal("10"),
        )

        handle_overdue_orders(now=il_today(18))

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.SENT)
        admin = self._to(ADMIN)[-1]
        self.assertIn("אישר רק חלק", admin)
        self.assertIn("חסה", admin)
        self.assertEqual(self._to(self.a.whatsapp_number), [])

    def test_orders_older_than_yesterday_are_left_alone(self):
        OrderRequest.objects.filter(pk=self.order.pk).update(created_at=il_today(10, days_ago=3))

        self.assertEqual(overdue_orders(now=il_today(18)), [])

    def test_the_suppliers_pending_reply_for_this_order_is_forgotten(self):
        key = f"whatsapp_supplier_pending:{self.a.whatsapp_number}"
        save_supplier_pending_order(self.a.whatsapp_number, self.order.id, [{"orp_id": 1}])

        handle_overdue_orders(now=il_today(18))

        self.assertIsNone(cache.get(key))  # a late "אישור" can't confirm items now at ספק ב

    def test_a_pending_reply_for_another_order_is_kept(self):
        key = f"whatsapp_supplier_pending:{self.a.whatsapp_number}"
        save_supplier_pending_order(self.a.whatsapp_number, self.order.id + 1000, [{"orp_id": 1}])

        handle_overdue_orders(now=il_today(18))

        self.assertIsNotNone(cache.get(key))

    def test_beat_runs_the_task_every_evening(self):
        from apps.orders.tasks import enforce_supplier_confirmation_deadline

        entry = settings.CELERY_BEAT_SCHEDULE["supplier-confirmation-deadline"]
        self.assertEqual(entry["task"], enforce_supplier_confirmation_deadline.name)
        self.assertEqual(entry["schedule"].hour, set(range(18, 24)))
        self.assertEqual(settings.CELERY_TIMEZONE, "Asia/Jerusalem")
