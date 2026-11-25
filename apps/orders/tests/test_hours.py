"""
Operating hours (apps/orders/hours.py): suppliers update prices 03:00-10:00, customers order
10:00-22:00, Israel time; admins are never limited.

The suite runs with the limit off (settings.py) so it passes at any time of day; every test here
turns it on and fixes the clock.
"""
from datetime import datetime, time
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import Region, SupplierProduct
from apps.orders import hours
from apps.orders.tests.test_webhook import (
    LOCMEM_CACHE, make_product, make_supplier, make_user_with_profile,
)
from apps.orders.whatsapp.cache import save_draft_order, get_draft_order

IL = ZoneInfo("Asia/Jerusalem")
PHONE = "+972501234567"
SEND = "apps.orders.whatsapp.validators.send_whatsapp_message"
User = get_user_model()


def at(hour, minute=0, month=1, day=15):
    """A fixed Israel-time clock (January: winter time, UTC+2)."""
    moment = datetime(2026, month, day, hour, minute, tzinfo=IL)
    return patch("django.utils.timezone.now", return_value=moment)


class LimitOnMixin:
    """HOURS_LIMIT_ENABLED=1 with the default windows, whatever the environment says."""

    def setUp(self):
        super().setUp()
        patcher = patch.dict("os.environ", {"HOURS_LIMIT_ENABLED": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("SUPPLIER_OPEN", "SUPPLIER_CLOSE", "CUSTOMER_OPEN", "CUSTOMER_CLOSE"):
            cleaner = patch.dict("os.environ", {name: ""})
            cleaner.start()
            self.addCleanup(cleaner.stop)


# ─────────────────────── the windows themselves ───────────────────────

class WindowTests(LimitOnMixin, SimpleTestCase):

    def test_suppliers_03_to_10(self):
        for h, m, expected in [(2, 59, False), (3, 0, True), (9, 59, True), (10, 0, False), (15, 0, False)]:
            with at(h, m):
                self.assertEqual(hours.suppliers_open(), expected, f"{h:02d}:{m:02d}")

    def test_customers_10_to_22(self):
        for h, m, expected in [(9, 59, False), (10, 0, True), (21, 59, True), (22, 0, False), (3, 0, False)]:
            with at(h, m):
                self.assertEqual(hours.customers_open(), expected, f"{h:02d}:{m:02d}")

    def test_between_22_and_03_both_are_closed(self):
        for h, m in [(22, 0), (23, 30), (0, 0), (1, 15), (2, 59)]:
            with at(h, m):
                self.assertFalse(hours.customers_open(), f"customers {h:02d}:{m:02d}")
                self.assertFalse(hours.suppliers_open(), f"suppliers {h:02d}:{m:02d}")

    def test_the_two_windows_never_overlap(self):
        for h in range(24):
            for m in (0, 30, 59):
                with at(h, m):
                    self.assertFalse(hours.customers_open() and hours.suppliers_open(), f"{h:02d}:{m:02d}")

    def test_israel_time_is_used_in_winter_and_summer(self):
        winter = datetime(2026, 1, 15, 7, 59, tzinfo=ZoneInfo("UTC"))   # 09:59 in Israel (UTC+2)
        summer = datetime(2026, 7, 15, 7, 0, tzinfo=ZoneInfo("UTC"))    # 10:00 in Israel (UTC+3)
        self.assertFalse(hours.customers_open(winter))
        self.assertTrue(hours.customers_open(summer))
        self.assertTrue(hours.suppliers_open(winter))
        self.assertFalse(hours.suppliers_open(summer))

    def test_the_limit_can_be_switched_off(self):
        with patch.dict("os.environ", {"HOURS_LIMIT_ENABLED": "0"}), at(3, 0):
            self.assertTrue(hours.customers_open())
            self.assertTrue(hours.suppliers_open())

    def test_hours_come_from_env_and_a_typo_falls_back_to_the_default(self):
        with patch.dict("os.environ", {"CUSTOMER_OPEN": "08:30", "CUSTOMER_CLOSE": "23:15"}):
            with at(8, 29):
                self.assertFalse(hours.customers_open())
            with at(8, 30):
                self.assertTrue(hours.customers_open())
            with at(23, 14):
                self.assertTrue(hours.customers_open())
            with at(23, 15):
                self.assertFalse(hours.customers_open())
        with patch.dict("os.environ", {"CUSTOMER_OPEN": "ten o'clock"}), at(10, 0):
            self.assertEqual(hours.customer_window()[0], time(10, 0))

    def test_a_window_may_cross_midnight(self):
        self.assertTrue(hours._inside((time(22, 0), time(3, 0)), datetime(2026, 1, 15, 23, 0, tzinfo=IL)))
        self.assertTrue(hours._inside((time(22, 0), time(3, 0)), datetime(2026, 1, 15, 1, 0, tzinfo=IL)))
        self.assertFalse(hours._inside((time(22, 0), time(3, 0)), datetime(2026, 1, 15, 12, 0, tzinfo=IL)))

    def test_messages_name_the_hours(self):
        self.assertIn("10:00", hours.customer_closed_text())
        self.assertIn("22:00", hours.customer_closed_text())
        self.assertIn("03:00", hours.supplier_closed_text())
        self.assertIn("10:00", hours.supplier_closed_text())

    def test_open_orders_stay_changeable_until_customers_close(self):
        from apps.orders.services import before_update_cutoff
        self.assertTrue(before_update_cutoff(datetime(2026, 1, 15, 21, 59, tzinfo=IL)))
        self.assertFalse(before_update_cutoff(datetime(2026, 1, 15, 22, 0, tzinfo=IL)))


# ─────────────────────── the API (site, and the MCP server through it) ───────────────────────

class ApiHoursTests(LimitOnMixin, APITestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user_with_profile(email="c@test.com", phone=PHONE)
        self.admin = User.objects.create_superuser(email="admin@test.com", password="pass")
        self.client.force_authenticate(user=self.user)

    def _place(self):
        return self.client.post(reverse("orders-place"), {"products": [], "scenario": "cheapest"}, format="json")

    def test_placing_an_order_is_refused_outside_customer_hours(self):
        for h, m in [(9, 59), (22, 0), (23, 30), (1, 0)]:
            with at(h, m):
                res = self._place()
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, f"{h:02d}:{m:02d}")
            self.assertIn("10:00", res.data["detail"])
            self.assertIn("22:00", res.data["detail"])

    def test_placing_an_order_goes_on_to_normal_checks_inside_hours(self):
        with at(10, 0):
            self.assertEqual(self._place().status_code, status.HTTP_400_BAD_REQUEST)  # empty basket, not 403
        with at(21, 59):
            self.assertEqual(self._place().status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_admin_is_never_limited(self):
        self.client.force_authenticate(user=self.admin)
        with at(1, 0):
            res = self._place()
        self.assertNotEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_changing_the_open_order_is_refused_but_a_preview_is_not(self):
        body = {"changes": [{"product_name": "עגבניה", "intent": "add", "quantity": "5"}]}
        with at(23, 0):
            real = self.client.post(reverse("orders-open-change"), {**body, "dry_run": False}, format="json")
            preview = self.client.post(reverse("orders-open-change"), {**body, "dry_run": True}, format="json")
        self.assertEqual(real.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotEqual(preview.status_code, status.HTTP_403_FORBIDDEN)

    def test_cancelling_and_price_suggestions_stay_open(self):
        with at(23, 0):
            cancel = self.client.post(reverse("orders-open-cancel"), {"dry_run": True}, format="json")
            suggest = self.client.post(reverse("orders-suggest"), {"products": []}, format="json")
        self.assertNotEqual(cancel.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotEqual(suggest.status_code, status.HTTP_403_FORBIDDEN)

    def test_the_hours_endpoint_tells_the_site(self):
        with at(23, 0):
            closed = self.client.get(reverse("orders-hours")).data
        with at(12, 0):
            opened = self.client.get(reverse("orders-hours")).data
        self.assertFalse(closed["open"])
        self.assertIn("10:00", closed["message"])
        self.assertEqual((closed["opens_at"], closed["closes_at"]), ("10:00", "22:00"))
        self.assertTrue(opened["open"])
        self.assertEqual(opened["message"], "")

    def test_the_hours_endpoint_says_open_for_an_admin(self):
        self.client.force_authenticate(user=self.admin)
        with at(23, 0):
            self.assertTrue(self.client.get(reverse("orders-hours")).data["open"])


# ─────────────────────── WhatsApp: customers ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class CustomerWhatsAppHoursTests(LimitOnMixin, TestCase):

    def setUp(self):
        super().setUp()
        cache.clear()
        self.tomato = make_product("עגבניה")
        self.supplier = make_supplier("ספק א")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.tomato, price_per_unit="5.00")
        self.user = make_user_with_profile(phone=PHONE)

    def _post(self, body, phone=PHONE):
        return self.client.post("/whatsapp/webhook/", {"From": f"whatsapp:{phone}", "Body": body})

    @patch(SEND)
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_an_order_message_outside_hours_gets_the_note_and_is_not_parsed(self, mock_parse, mock_send):
        with at(22, 30):
            self._post("5 עגבניות")
        mock_parse.assert_not_called()
        self.assertIsNone(get_draft_order(PHONE))
        self.assertEqual(mock_send.call_count, 1)
        text = mock_send.call_args[0][1]
        self.assertIn("10:00", text)
        self.assertIn("22:00", text)
        self.assertIn("לא נקלטה", text)

    @patch(SEND)
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_inside_hours_the_same_message_is_an_order(self, mock_parse, mock_send):
        mock_parse.return_value = [{"product_name": "עגבניה", "quantity": Decimal("5")}]
        with at(12, 0):
            self._post("5 עגבניות")
        mock_parse.assert_called_once()
        self.assertIsNotNone(get_draft_order(PHONE))

    @patch(SEND)
    def test_an_offer_waiting_for_confirmation_is_dropped_at_closing(self, mock_send):
        cache.set(f"whatsapp_order:{PHONE}", "{}", 3600)
        with at(22, 5):
            self._post("אישור")
        self.assertIsNone(cache.get(f"whatsapp_order:{PHONE}"))
        self.assertIn("10:00", mock_send.call_args[0][1])
        # and it cannot be confirmed the next day either
        with at(10, 30, day=16):
            self._post("אישור")
        self.assertIn("אין כרגע הזמנה שממתינה", mock_send.call_args[0][1])

    @patch(SEND)
    def test_cancelling_still_works_when_closed(self, mock_send):
        with at(23, 0):
            self._post("בטל את ההזמנה")
        self.assertIn("אין לך הזמנה פתוחה לביטול", mock_send.call_args[0][1])

    @patch(SEND)
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_an_admin_is_never_limited_on_whatsapp(self, mock_parse, mock_send):
        self.user.is_staff = True
        self.user.save()
        mock_parse.return_value = [{"product_name": "עגבניה", "quantity": Decimal("5")}]
        with at(2, 0):
            self._post("5 עגבניות")
        mock_parse.assert_called_once()

    @patch(SEND)
    def test_an_unregistered_number_is_still_told_so(self, mock_send):
        with at(2, 0):
            self._post("5 עגבניות", phone="+972500000077")
        self.assertIn("לא רשום", mock_send.call_args[0][1])

    @patch(SEND)
    def test_a_held_basket_that_comes_due_after_closing_is_dropped_not_offered(self, mock_send):
        from apps.orders.tasks import dispatch_draft_order_task
        generation = save_draft_order(PHONE, [{"product_name": "עגבניה", "quantity": Decimal("2")}])
        with at(22, 30):
            dispatch_draft_order_task(PHONE, generation, is_grace_retry=True)
        text = mock_send.call_args[0][1]
        self.assertIn("10:00", text)
        self.assertIn("לא נשמרה", text)
        self.assertIsNone(cache.get(f"whatsapp_order:{PHONE}"))


# ─────────────────────── WhatsApp: suppliers ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class SupplierWhatsAppHoursTests(LimitOnMixin, TestCase):

    def setUp(self):
        super().setUp()
        cache.clear()
        self.supplier = make_supplier("ספק א")

    def _post(self, body):
        return self.client.post("/whatsapp/webhook/", {"From": f"whatsapp:{self.supplier.whatsapp_number}", "Body": body})

    @patch(SEND)
    @patch("apps.catalog.price_parser.update_prices_from_message")
    def test_prices_are_updated_between_03_and_10(self, mock_update, mock_send):
        mock_update.return_value = {
            "updated": [{"product_name": "עגבניה", "price": "4.5", "unit": 'ק"ג'}], "removed": [], "skipped": [],
        }
        for h, m in [(3, 0), (9, 59)]:
            mock_update.reset_mock()
            with at(h, m):
                self._post("עגבניה 4.5")
            mock_update.assert_called_once()

    @patch(SEND)
    @patch("apps.catalog.price_parser.update_prices_from_message")
    def test_a_price_message_outside_the_hours_gets_the_note_and_changes_nothing(self, mock_update, mock_send):
        for h, m in [(2, 59), (10, 0), (15, 0), (22, 30)]:
            mock_send.reset_mock()
            with at(h, m):
                self._post("עגבניה 4.5")
            mock_update.assert_not_called()
            text = mock_send.call_args[0][1]
            self.assertIn("03:00", text, f"{h:02d}:{m:02d}")
            self.assertIn("10:00", text)
            self.assertIn("לא עודכנו", text)

    @patch(SEND)
    def test_a_suppliers_answers_are_never_limited(self, mock_send):
        """An order confirmation is an answer, not a price update: it works at any hour."""
        from apps.orders.whatsapp.cache import save_supplier_pending_order
        save_supplier_pending_order(self.supplier.whatsapp_number, 999, [])
        with at(15, 0), patch("apps.orders.whatsapp.supplier_flow._handle_supplier_price_update") as prices:
            self._post("אישור")
        prices.assert_not_called()
