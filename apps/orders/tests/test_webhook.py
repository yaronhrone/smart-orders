"""
Tests for apps/orders/whatsapp_webhook.py.

External dependencies mocked:
  - send_whatsapp_message  (Twilio)
  - parse_customer_order   (OpenAI)
  - update_prices_from_message (OpenAI, in supplier price-update path)

Cache is overridden to LocMemCache so tests are isolated.
"""
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

from apps.catalog.models import Product, Supplier, SupplierProduct, Region, Unit
from apps.orders.models import OrderRequest, OrderRequestProduct, SupplierConfirmation
from apps.orders.whatsapp_webhook import (
    _parse_supplier_reply,
    save_pending_order,
)
from apps.orders.whatsapp import save_supplier_pending_order
from apps.users.models import Profile

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

_counter = 0


def _unique_phone():
    global _counter
    _counter += 1
    return f"+9720{_counter:08d}"


def make_supplier(name, region=Region.CENTER, minimum_order=0):
    phone = _unique_phone()
    return Supplier.objects.create(
        name=name, phone=phone, whatsapp_number=phone,
        region=region, minimum_order=minimum_order,
    )


def make_product(name):
    return Product.objects.create(name=name, unit=Unit.KG)


def make_user_with_profile(email="user@test.com", phone="+972501234567", region=Region.CENTER):
    user = User.objects.create_user(email=email, password="pass")
    Profile.objects.create(user=user, phone=phone, region=region)
    return user


# ─────────────────────── _parse_supplier_reply (pure) ───────────────────────

class ParseSupplierReplyTests(TestCase):

    def _products(self):
        return [
            {"orp_id": 1, "product_name": "עגבניה", "quantity": "20", "unit": 'ק"ג'},
            {"orp_id": 2, "product_name": "גזר", "quantity": "15", "unit": 'ק"ג'},
        ]

    def test_confirm_word_returns_all_products(self):
        """'אישור' returns all products at their full requested quantity."""
        confirmed, _ = _parse_supplier_reply("אישור", self._products())
        self.assertEqual(confirmed[1], Decimal("20"))
        self.assertEqual(confirmed[2], Decimal("15"))

    def test_ok_english_confirms_all(self):
        confirmed, _ = _parse_supplier_reply("ok", self._products())
        self.assertEqual(len(confirmed), 2)

    def test_thumbsup_emoji_confirms_all(self):
        confirmed, _ = _parse_supplier_reply("👍", self._products())
        self.assertEqual(len(confirmed), 2)

    def test_partial_reply_by_name(self):
        """Product name followed by number updates that product's qty."""
        confirmed, _ = _parse_supplier_reply("עגבניה: 18", self._products())
        self.assertEqual(confirmed[1], Decimal("18"))
        self.assertNotIn(2, confirmed)

    def test_single_product_lone_number(self):
        """Single product + bare number → treated as its quantity."""
        products = [{"orp_id": 5, "product_name": "עגבניה", "quantity": "10", "unit": 'ק"ג'}]
        confirmed, _ = _parse_supplier_reply("25", products)
        self.assertEqual(confirmed[5], Decimal("25"))

    def test_unrecognized_reply_returns_empty(self):
        """Gibberish → no confirmations."""
        confirmed, missing = _parse_supplier_reply("בדיקה בדיקה", self._products())
        self.assertEqual(confirmed, {})
        self.assertEqual(missing, [])

    def test_single_product_lone_number_not_applied_to_multi(self):
        """Lone number is NOT auto-applied when there are multiple products."""
        confirmed, _ = _parse_supplier_reply("25", self._products())
        self.assertEqual(confirmed, {})


# ─────────────────────── Webhook routing ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE)
class WebhookRoutingTests(TestCase):

    def setUp(self):
        cache.clear()

    def test_get_returns_405(self):
        res = self.client.get("/whatsapp/webhook/")
        self.assertEqual(res.status_code, 405)

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    @patch("apps.orders.whatsapp_webhook._handle_supplier_flow")
    def test_known_supplier_phone_routes_to_supplier_flow(self, mock_supplier, mock_send):
        mock_supplier.return_value = __import__("django.http", fromlist=["HttpResponse"]).HttpResponse(status=200)
        supplier = make_supplier("ספק א")
        self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{supplier.whatsapp_number}",
            "Body": "אישור",
        })
        mock_supplier.assert_called_once()

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    @patch("apps.orders.whatsapp_webhook._handle_user_flow")
    def test_unknown_phone_routes_to_user_flow(self, mock_user, mock_send):
        mock_user.return_value = __import__("django.http", fromlist=["HttpResponse"]).HttpResponse(status=200)
        self.client.post("/whatsapp/webhook/", {
            "From": "whatsapp:+972509999999",
            "Body": "שלום",
        })
        mock_user.assert_called_once()


# ─────────────────────── User: new order flow ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE)
class UserNewOrderFlowTests(TestCase):

    def setUp(self):
        cache.clear()
        self.tomato = make_product("עגבניה")
        self.carrot = make_product("גזר")
        self.supplier = make_supplier("ספק א")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.tomato, price_per_unit="5.00")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.carrot, price_per_unit="3.00")

    def _post(self, phone, body):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{phone}",
            "Body": body,
        })

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_unknown_phone_sends_not_registered_message(self, mock_send):
        """Phone not in any Profile → 'not registered' message."""
        self._post("+972500000001", "5 עגבניות")
        mock_send.assert_called_once()
        self.assertIn("לא רשום", mock_send.call_args[0][1])

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_valid_order_sends_options_and_caches(self, mock_parse, mock_send):
        """Registered user sends valid order → receives both options, cache updated."""
        user = make_user_with_profile(phone="+972501111111")
        mock_parse.return_value = [
            {"product_name": "עגבניה", "quantity": Decimal("5")},
            {"product_name": "גזר", "quantity": Decimal("10")},
        ]

        self._post("+972501111111", "5 עגבניות ו-10 גזר")

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][1]
        self.assertIn("עגבניה", msg)
        self.assertIn("גזר", msg)

        # Cache must contain pending order
        cached = cache.get(f"whatsapp_order:+972501111111")
        self.assertIsNotNone(cached)
        data = json.loads(cached)
        self.assertIn("cheapest", data)
        self.assertIn("fewest", data)
        self.assertIn("user_id", data)
        self.assertEqual(data["user_id"], user.id)

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_ai_parse_failure_sends_help_message(self, mock_parse, mock_send):
        """AI error → user gets a 'couldn't understand' message."""
        make_user_with_profile(phone="+972502222222")
        mock_parse.side_effect = ValueError("no_items")

        self._post("+972502222222", "בלה בלה")

        mock_send.assert_called_once()
        self.assertIn("לא הצלחתי", mock_send.call_args[0][1])

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_all_products_unrecognized_sends_warning(self, mock_parse, mock_send):
        """All parsed products not in catalog → 'no known products' message."""
        make_user_with_profile(phone="+972503333333")
        mock_parse.return_value = [
            {"product_name": "אבטיח ירחי", "quantity": Decimal("5")},
        ]

        self._post("+972503333333", "5 אבטיחים ירחיים")

        mock_send.assert_called_once()
        self.assertIn("לא זיהיתי", mock_send.call_args[0][1])

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_partial_unrecognized_adds_warning_to_options(self, mock_parse, mock_send):
        """Some recognized, some not → options shown + warning about unrecognized."""
        make_user_with_profile(phone="+972504444444")
        mock_parse.return_value = [
            {"product_name": "עגבניה", "quantity": Decimal("5")},
            {"product_name": "מוצר_לא_קיים", "quantity": Decimal("3")},
        ]

        self._post("+972504444444", "5 עגבניות ו-3 מוצר_לא_קיים")

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][1]
        self.assertIn("עגבניה", msg)
        self.assertIn("⚠️", msg)
        self.assertIn("מוצר_לא_קיים", msg)

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_phone_with_local_format_resolved(self, mock_parse, mock_send):
        """Profile with 0XXXXXXXXX format is found even if WhatsApp sends +972XXXXXXXXX."""
        user = User.objects.create_user(email="local@test.com", password="pass")
        Profile.objects.create(user=user, phone="0501234567", region=Region.CENTER)
        mock_parse.return_value = [{"product_name": "עגבניה", "quantity": Decimal("5")}]

        self._post("+972501234567", "5 עגבניות")

        # Should reach suggest_order (not "not registered")
        msg = mock_send.call_args[0][1]
        self.assertNotIn("לא רשום", msg)


# ─────────────────────── User: confirmation flow ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE)
class UserConfirmationFlowTests(TestCase):

    def setUp(self):
        cache.clear()
        self.tomato = make_product("עגבניה")
        self.supplier = make_supplier("ספק א")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.tomato, price_per_unit="5.00")
        self.user = make_user_with_profile(phone="+972505555555")

    def _seed_cache(self, phone, same=False):
        """Put a fake pending order into the cache."""
        cheapest = {
            "scenario": "cheapest",
            "total_price": "50.00",
            "supplier_count": 1,
            "products": [
                {
                    "product_id": self.tomato.id,
                    "product_name": "עגבניה",
                    "unit": 'ק"ג',
                    "quantity": "10",
                    "unit_price": "5.00",
                    "subtotal": "50.00",
                    "supplier_id": self.supplier.id,
                    "supplier_name": "ספק א",
                }
            ],
        }
        if same:
            fewest = cheapest
        else:
            fewest = {**cheapest, "scenario": "fewest_suppliers", "total_price": "60.00"}

        save_pending_order(
            phone, cheapest, fewest,
            products=[{"product_id": self.tomato.id, "quantity": "10"}],
            user_id=self.user.id,
            region=Region.CENTER,
        )

    def _post(self, phone, body):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{phone}",
            "Body": body,
        })

    @patch("apps.orders.whatsapp.send_whatsapp_message")
    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_reply_aleph_builds_cheapest_order(self, mock_send_webhook, mock_send_whatsapp):
        """Replying 'א' selects cheapest scenario and builds DB order."""
        self._seed_cache("+972505555555")

        self._post("+972505555555", "א")

        order = OrderRequest.objects.filter(user=self.user).first()
        self.assertIsNotNone(order)
        self.assertEqual(order.status, OrderRequest.Status.SENT)

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_reply_bet_builds_fewest_suppliers_order(self, mock_send):
        """Replying 'ב' selects fewest_suppliers scenario."""
        self._seed_cache("+972505555555")

        self._post("+972505555555", "ב")

        order = OrderRequest.objects.filter(user=self.user).first()
        self.assertIsNotNone(order)

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_invalid_reply_sends_choose_message(self, mock_send):
        """Invalid reply keeps cache intact and asks user to choose."""
        self._seed_cache("+972505555555")

        self._post("+972505555555", "גגגג")

        mock_send.assert_called_once()
        self.assertIn("*א*", mock_send.call_args[0][1])

        # Cache should still exist
        self.assertIsNotNone(cache.get("whatsapp_order:+972505555555"))

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_same_scenarios_any_reply_confirms(self, mock_send):
        """When cheapest == fewest any reply confirms (including 'שלום')."""
        self._seed_cache("+972505555555", same=True)

        self._post("+972505555555", "שלום")

        # Cache cleared → confirmed
        self.assertIsNone(cache.get("whatsapp_order:+972505555555"))
        order = OrderRequest.objects.filter(user=self.user).first()
        self.assertIsNotNone(order)

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_confirmation_clears_cache(self, mock_send):
        """After confirming, pending order is removed from cache."""
        self._seed_cache("+972505555555")

        self._post("+972505555555", "א")

        self.assertIsNone(cache.get("whatsapp_order:+972505555555"))

    @patch("apps.orders.whatsapp.send_whatsapp_message")
    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_confirmation_sends_supplier_whatsapp(self, mock_send_webhook, mock_send_whatsapp):
        """Supplier receives a WhatsApp message when user confirms."""
        self._seed_cache("+972505555555")

        self._post("+972505555555", "א")

        # Supplier order message goes through whatsapp.py; user confirmation through whatsapp_webhook.py
        all_calls_text = " ".join(
            str(c) for c in mock_send_webhook.call_args_list + mock_send_whatsapp.call_args_list
        )
        self.assertIn("להזמין", all_calls_text)

    @patch("apps.orders.whatsapp.send_whatsapp_message")
    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_confirmation_saves_supplier_pending_in_cache(self, mock_send_webhook, mock_send_whatsapp):
        """After confirmation, supplier's pending order is cached for their reply."""
        self._seed_cache("+972505555555")

        self._post("+972505555555", "א")

        supplier_cache = cache.get(f"whatsapp_supplier_pending:{self.supplier.whatsapp_number}")
        self.assertIsNotNone(supplier_cache)


# ─────────────────────── Supplier: confirmation flow ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE)
class SupplierConfirmationFlowTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user_with_profile()
        self.tomato = make_product("עגבניה")
        self.carrot = make_product("גזר")
        self.supplier = make_supplier("ספק א")
        self.order = OrderRequest.objects.create(user=self.user, total_price="100.00")
        self.orp1 = OrderRequestProduct.objects.create(
            order_request=self.order, product=self.tomato, supplier=self.supplier,
            quantity="20", unit_price="5.00",
        )
        self.orp2 = OrderRequestProduct.objects.create(
            order_request=self.order, product=self.carrot, supplier=self.supplier,
            quantity="15", unit_price="3.00",
        )
        save_supplier_pending_order(
            supplier_phone=self.supplier.whatsapp_number,
            order_request_id=self.order.id,
            products=[
                {"orp_id": self.orp1.id, "product_name": "עגבניה", "quantity": "20", "unit": 'ק"ג'},
                {"orp_id": self.orp2.id, "product_name": "גזר", "quantity": "15", "unit": 'ק"ג'},
            ],
        )

    def _post_supplier(self, body):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{self.supplier.whatsapp_number}",
            "Body": body,
        })

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_full_confirmation_word_creates_supplier_confirmations(self, mock_send):
        """'אישור' creates SupplierConfirmation for all products."""
        self._post_supplier("אישור")

        self.assertEqual(SupplierConfirmation.objects.filter(order_request_product__order_request=self.order).count(), 2)

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_full_confirmation_clears_cache(self, mock_send):
        """After full confirmation, supplier pending cache is cleared."""
        self._post_supplier("אישור")

        self.assertIsNone(cache.get(f"whatsapp_supplier_pending:{self.supplier.whatsapp_number}"))

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_partial_confirmation_by_name(self, mock_send):
        """Partial reply (name: quantity) creates SupplierConfirmation only for matched products."""
        self._post_supplier("עגבניה: 18")

        confirmations = SupplierConfirmation.objects.filter(order_request_product__order_request=self.order)
        self.assertEqual(confirmations.count(), 1)
        self.assertEqual(confirmations.first().confirmed_quantity, Decimal("18"))
        self.assertEqual(confirmations.first().order_request_product, self.orp1)

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_unrecognized_reply_sends_help_message(self, mock_send):
        """Gibberish reply → supplier gets 'couldn't understand' message."""
        self._post_supplier("xxxxxxxxx")

        mock_send.assert_called_once()
        self.assertIn("אישור", mock_send.call_args[0][1])
        # Cache should remain
        self.assertIsNotNone(cache.get(f"whatsapp_supplier_pending:{self.supplier.whatsapp_number}"))

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    def test_sends_confirmation_summary_back_to_supplier(self, mock_send):
        """After confirming, supplier receives a summary of the confirmed quantities."""
        self._post_supplier("כן")

        # First call is the supplier ack; customer notification may add a second call
        self.assertGreaterEqual(mock_send.call_count, 1)
        msg = mock_send.call_args_list[0][0][1]
        self.assertIn("✅", msg)
        self.assertIn("עגבניה", msg)


# ─────────────────────── Supplier: price update flow ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE)
class SupplierPriceUpdateFlowTests(TestCase):

    def setUp(self):
        cache.clear()
        self.supplier = make_supplier("ספק ב")

    def _post_supplier(self, body):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{self.supplier.whatsapp_number}",
            "Body": body,
        })

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    @patch("apps.catalog.price_parser.update_prices_from_message")
    def test_no_pending_order_routes_to_price_update(self, mock_update, mock_send):
        """Supplier with no pending order gets price-update flow."""
        mock_update.return_value = {
            "updated": [{"product_name": "עגבניה", "price": "4.00", "is_new": False}],
            "skipped": [],
        }

        self._post_supplier("עגבניה 4.00")

        mock_update.assert_called_once()
        mock_send.assert_called_once()
        self.assertIn("עודכנו", mock_send.call_args[0][1])

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    @patch("apps.catalog.price_parser.update_prices_from_message")
    def test_price_update_sends_new_products_section(self, mock_update, mock_send):
        """New products are highlighted in the response."""
        mock_update.return_value = {
            "updated": [{"product_name": "פרי חדש", "price": "7.50", "is_new": True}],
            "skipped": [],
        }

        self._post_supplier("פרי חדש 7.50")

        msg = mock_send.call_args[0][1]
        self.assertIn("🆕", msg)

    @patch("apps.orders.whatsapp_webhook.send_whatsapp_message")
    @patch("apps.catalog.price_parser.update_prices_from_message")
    def test_unrecognized_price_message_sends_help(self, mock_update, mock_send):
        """Empty update result → help message."""
        mock_update.return_value = {"updated": [], "skipped": []}

        self._post_supplier("שלום מה שלומך")

        mock_send.assert_called_once()
        self.assertIn("לא זיהיתי", mock_send.call_args[0][1])
