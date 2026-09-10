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
from apps.orders.whatsapp import (
    _parse_supplier_reply,
    save_pending_order,
    save_supplier_pending_order,
)
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

    def test_missing_matches_plural_of_catalog_spelling(self):
        """
        The catalog's canonical name is "עגבנייה" (double yud) while suppliers
        write "עגבניות". The prefix heuristic cannot bridge that two-letter
        suffix, so the alias dictionary has to — otherwise the reply confirms
        the very item the supplier just declared out of stock.
        """
        products = [
            {"orp_id": 1, "product_name": "עגבנייה", "quantity": "20", "unit": 'ק"ג'},
            {"orp_id": 2, "product_name": "גזר", "quantity": "15", "unit": 'ק"ג'},
        ]
        confirmed, missing = _parse_supplier_reply("חסר עגבניות, שאר אישור", products)
        self.assertEqual([p["orp_id"] for p in missing], [1])
        self.assertNotIn(1, confirmed)
        self.assertEqual(confirmed[2], Decimal("15"))

    def test_missing_matches_plural_of_multiword_product(self):
        """Same path for a two-word product: 'אין בצלים' → 'בצל יבש' is missing."""
        products = [
            {"orp_id": 1, "product_name": "בצל יבש", "quantity": "40", "unit": 'ק"ג'},
            {"orp_id": 2, "product_name": "גזר", "quantity": "15", "unit": 'ק"ג'},
        ]
        confirmed, missing = _parse_supplier_reply("אין בצלים, השאר אישור", products)
        self.assertEqual([p["orp_id"] for p in missing], [1])
        self.assertEqual(confirmed[2], Decimal("15"))


# ─────────────────────── Webhook routing ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class WebhookRoutingTests(TestCase):

    def setUp(self):
        cache.clear()

    def test_get_returns_405(self):
        res = self.client.get("/whatsapp/webhook/")
        self.assertEqual(res.status_code, 405)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.whatsapp._handle_supplier_flow")
    def test_known_supplier_phone_routes_to_supplier_flow(self, mock_supplier, mock_send):
        mock_supplier.return_value = __import__("django.http", fromlist=["HttpResponse"]).HttpResponse(status=200)
        supplier = make_supplier("ספק א")
        self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{supplier.whatsapp_number}",
            "Body": "אישור",
        })
        mock_supplier.assert_called_once()

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.whatsapp._handle_user_flow")
    def test_unknown_phone_routes_to_user_flow(self, mock_user, mock_send):
        mock_user.return_value = __import__("django.http", fromlist=["HttpResponse"]).HttpResponse(status=200)
        self.client.post("/whatsapp/webhook/", {
            "From": "whatsapp:+972509999999",
            "Body": "שלום",
        })
        mock_user.assert_called_once()


# ─────────────────────── User: new order flow ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
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

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_unknown_phone_sends_not_registered_message(self, mock_send):
        """Phone not in any Profile → 'not registered' message."""
        self._post("+972500000001", "5 עגבניות")
        mock_send.assert_called_once()
        self.assertIn("לא רשום", mock_send.call_args[0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
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

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_ai_parse_failure_sends_help_message(self, mock_parse, mock_send):
        """AI error → user gets a 'couldn't understand' message."""
        make_user_with_profile(phone="+972502222222")
        mock_parse.side_effect = ValueError("no_items")

        self._post("+972502222222", "בלה בלה")

        mock_send.assert_called_once()
        self.assertIn("לא הצלחתי", mock_send.call_args[0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
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

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
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

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
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

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
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

    @patch("apps.orders.tasks.send_supplier_order_notification_task")
    @patch("apps.orders.whatsapp.send_whatsapp_message")
    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_reply_aleph_builds_cheapest_order(self, mock_send_webhook, mock_send_whatsapp, mock_supplier_task):
        """Replying 'א' selects cheapest scenario and builds DB order."""
        self._seed_cache("+972505555555")

        self._post("+972505555555", "א")

        order = OrderRequest.objects.filter(user=self.user).first()
        self.assertIsNotNone(order)
        self.assertEqual(order.status, OrderRequest.Status.SENT)

    @patch("apps.orders.tasks.send_supplier_order_notification_task")
    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_reply_bet_builds_fewest_suppliers_order(self, mock_send, mock_supplier_task):
        """Replying 'ב' selects fewest_suppliers scenario."""
        self._seed_cache("+972505555555")

        self._post("+972505555555", "ב")

        order = OrderRequest.objects.filter(user=self.user).first()
        self.assertIsNotNone(order)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_invalid_reply_sends_choose_message(self, mock_send):
        """Invalid reply keeps cache intact and asks user to choose."""
        self._seed_cache("+972505555555")

        self._post("+972505555555", "גגגג")

        mock_send.assert_called_once()
        self.assertIn("*א*", mock_send.call_args[0][1])

        # Cache should still exist
        self.assertIsNotNone(cache.get("whatsapp_order:+972505555555"))

    @patch("apps.orders.tasks.send_supplier_order_notification_task")
    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_same_scenarios_any_reply_confirms(self, mock_send, mock_supplier_task):
        """When cheapest == fewest any reply confirms (including 'שלום')."""
        self._seed_cache("+972505555555", same=True)

        self._post("+972505555555", "שלום")

        # Cache cleared → confirmed
        self.assertIsNone(cache.get("whatsapp_order:+972505555555"))
        order = OrderRequest.objects.filter(user=self.user).first()
        self.assertIsNotNone(order)

    @patch("apps.orders.tasks.send_supplier_order_notification_task")
    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_confirmation_clears_cache(self, mock_send, mock_supplier_task):
        """After confirming, pending order is removed from cache."""
        self._seed_cache("+972505555555")

        self._post("+972505555555", "א")

        self.assertIsNone(cache.get("whatsapp_order:+972505555555"))

    @patch("apps.orders.tasks.send_supplier_order_notification_task")
    @patch("apps.orders.whatsapp.send_whatsapp_message")
    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_confirmation_sends_supplier_whatsapp(self, mock_send_webhook, mock_send_whatsapp, mock_supplier_task):
        """Supplier receives a WhatsApp order-notification message when user confirms."""
        self._seed_cache("+972505555555")

        self._post("+972505555555", "א")

        # Supplier order message is dispatched via the Celery task, not sent inline.
        mock_supplier_task.delay.assert_called_once()
        phone, message = mock_supplier_task.delay.call_args[0]
        self.assertEqual(phone, self.supplier.whatsapp_number)
        self.assertIn("להזמין", message)

    @patch("apps.orders.tasks.send_supplier_order_notification_task")
    @patch("apps.orders.whatsapp.send_whatsapp_message")
    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_confirmation_saves_supplier_pending_in_cache(self, mock_send_webhook, mock_send_whatsapp, mock_supplier_task):
        """After confirmation, supplier's pending order is cached for their reply."""
        self._seed_cache("+972505555555")

        self._post("+972505555555", "א")

        supplier_cache = cache.get(f"whatsapp_supplier_pending:{self.supplier.whatsapp_number}")
        self.assertIsNotNone(supplier_cache)


# ─────────────────────── User: minimum-order scenario filtering ─────────────
#
# suggest_order() itself is mocked (its real assignment/minimum-enforcement
# logic is exercised elsewhere) — these target only what _handle_new_order
# does with the minimum_issues it gets back: whether it still offers a
# scenario that can't actually be ordered.

def _scenario(price, supplier_name="ספק"):
    return {
        "scenario": "x",
        "total_price": price,
        "supplier_count": 1,
        "products": [{
            "product_id": 1, "product_name": "עגבניה", "unit": 'ק"ג',
            "quantity": "10", "unit_price": price, "subtotal": price,
            "supplier_id": 1, "supplier_name": supplier_name,
        }],
    }


def _issue(supplier_name, missing="900.00"):
    return [{
        "supplier_id": 2, "supplier_name": supplier_name,
        "current_total": "20.00", "minimum_required": "1000.00",
        "missing_amount": missing,
    }]


@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class MinimumScenarioFilteringTests(TestCase):

    def setUp(self):
        cache.clear()
        self.tomato = make_product("עגבניה")
        self.supplier = make_supplier("ספק א")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.tomato, price_per_unit="5.00")
        self.user = make_user_with_profile(phone="+972506666666")

    def _post(self, phone, body):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{phone}",
            "Body": body,
        })

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    @patch("apps.orders.services.suggest_order")
    def test_both_valid_offers_both_as_before(self, mock_suggest, mock_parse, mock_send):
        mock_parse.return_value = [{"product_name": "עגבניה", "quantity": Decimal("10")}]
        mock_suggest.return_value = {
            "cheapest": _scenario("50.00"),
            "fewest_suppliers": _scenario("60.00"),
            "minimum_issues": {"cheapest": [], "fewest_suppliers": []},
        }

        self._post("+972506666666", "10 עגבניות")

        msg = mock_send.call_args[0][1]
        self.assertIn("*א*", msg)
        self.assertIn("*ב*", msg)
        data = json.loads(cache.get("whatsapp_order:+972506666666"))
        self.assertNotIn("single_scenario", data)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    @patch("apps.orders.services.suggest_order")
    def test_scenario_failing_minimum_is_not_offered_as_a_choice(self, mock_suggest, mock_parse, mock_send):
        """
        fewest_suppliers fails its supplier's minimum, cheapest doesn't. The
        customer must not be offered a choice that leads to a dead end — only
        the valid scenario should appear, already ready to confirm.
        """
        mock_parse.return_value = [{"product_name": "עגבניה", "quantity": Decimal("10")}]
        mock_suggest.return_value = {
            "cheapest": _scenario("50.00"),
            "fewest_suppliers": _scenario("20.00", supplier_name="ספק גדול"),
            "minimum_issues": {"cheapest": [], "fewest_suppliers": _issue("ספק גדול")},
        }

        self._post("+972506666666", "10 עגבניות")

        msg = mock_send.call_args[0][1]
        self.assertNotIn("*א*", msg)
        self.assertNotIn("*ב*", msg)
        self.assertIn("ענה *אישור*", msg)
        self.assertIn("ספק גדול", msg)  # explains what's excluded and why

        data = json.loads(cache.get("whatsapp_order:+972506666666"))
        self.assertEqual(data["single_scenario"], "cheapest")

    @patch("apps.orders.tasks.send_supplier_order_notification_task")
    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    @patch("apps.orders.services.suggest_order")
    @patch("apps.orders.services.build_order")
    def test_single_scenario_confirms_on_any_reply(
        self, mock_build, mock_suggest, mock_parse, mock_send, mock_supplier_task
    ):
        """The single offered scenario confirms the same way the same-price shortcut does."""
        mock_parse.return_value = [{"product_name": "עגבניה", "quantity": Decimal("10")}]
        mock_suggest.return_value = {
            "cheapest": _scenario("50.00"),
            "fewest_suppliers": _scenario("20.00", supplier_name="ספק גדול"),
            "minimum_issues": {"cheapest": [], "fewest_suppliers": _issue("ספק גדול")},
        }
        order = OrderRequest.objects.create(user=self.user, total_price=Decimal("50.00"))
        mock_build.return_value = (order, [])

        self._post("+972506666666", "10 עגבניות")
        self._post("+972506666666", "אישור")

        self.assertIsNone(cache.get("whatsapp_order:+972506666666"))
        mock_build.assert_called_once()
        self.assertEqual(mock_build.call_args.kwargs["scenario"], "cheapest")

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    @patch("apps.orders.services.suggest_order")
    def test_both_failing_minimum_offers_nothing_to_confirm(self, mock_suggest, mock_parse, mock_send):
        mock_parse.return_value = [{"product_name": "עגבניה", "quantity": Decimal("10")}]
        mock_suggest.return_value = {
            "cheapest": _scenario("50.00", supplier_name="ספק קטן"),
            "fewest_suppliers": _scenario("20.00", supplier_name="ספק גדול"),
            "minimum_issues": {
                "cheapest": _issue("ספק קטן"),
                "fewest_suppliers": _issue("ספק גדול"),
            },
        }

        self._post("+972506666666", "10 עגבניות")

        msg = mock_send.call_args[0][1]
        self.assertIn("⛔", msg)
        self.assertNotIn("*א*", msg)
        self.assertNotIn("ענה *אישור*", msg)
        self.assertIsNone(cache.get("whatsapp_order:+972506666666"))


# ─────────────────────── User: ambiguous product names ──────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class AmbiguousProductWebhookTests(TestCase):
    """
    "תפוח אדמה" alone matches two catalog products (אדום/לבן) — end-to-end
    through the real webhook: ask first, compute only after the customer
    answers. parse_customer_order itself is real here (no OpenAI call
    needed for these messages — resolves entirely via the fast dict path).
    """

    def setUp(self):
        cache.clear()
        self.red = make_product("תפוח אדמה אדום")
        self.white = make_product("תפוח אדמה לבן")
        self.supplier = make_supplier("ספק א")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.red, price_per_unit="3.00")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.white, price_per_unit="2.50")
        self.user = make_user_with_profile(phone="+972508888888")

    def _post(self, phone, body):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{phone}",
            "Body": body,
        })

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_ambiguous_name_asks_instead_of_computing(self, mock_send):
        self._post("+972508888888", "5 קילו תפוח אדמה")

        msg = mock_send.call_args[0][1]
        self.assertIn("אדום", msg)
        self.assertIn("לבן", msg)
        # No scenario/price was computed — this is a question, not an offer.
        self.assertNotIn("₪", msg)
        self.assertIsNotNone(cache.get("whatsapp_clarify:+972508888888"))
        self.assertIsNone(cache.get("whatsapp_order:+972508888888"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_answering_the_clarification_completes_the_order(self, mock_send):
        self._post("+972508888888", "5 קילו תפוח אדמה")
        self._post("+972508888888", "אדום")

        msg = mock_send.call_args[0][1]
        self.assertIn("תפוח אדמה אדום", msg)
        self.assertIn("₪", msg)
        self.assertIsNone(cache.get("whatsapp_clarify:+972508888888"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_unrecognized_answer_asks_again(self, mock_send):
        self._post("+972508888888", "5 קילו תפוח אדמה")
        self._post("+972508888888", "לא יודע")

        msg = mock_send.call_args[0][1]
        self.assertIn("אדום", msg)
        self.assertIn("לבן", msg)
        self.assertIsNotNone(cache.get("whatsapp_clarify:+972508888888"))


# ─────────────────────── User: modifying a SENT order ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class OrderModificationTests(TestCase):
    """
    A message from a phone with an existing SENT order routes to
    _handle_order_modification instead of _handle_new_order — these exercise
    that path directly against a real DB, only parse_modification_intent
    (the OpenAI call) mocked.
    """

    def setUp(self):
        cache.clear()
        self.tomato = make_product("עגבניה")
        self.potato = make_product("תפוח אדמה אדום")
        self.supplier = make_supplier("ספק א")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.tomato, price_per_unit="5.00")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.potato, price_per_unit="3.00")
        self.user = make_user_with_profile(phone="+972507777777")
        self.order = OrderRequest.objects.create(
            user=self.user, status=OrderRequest.Status.SENT, total_price=Decimal("50.00")
        )
        self.orp = OrderRequestProduct.objects.create(
            order_request=self.order, product=self.tomato, supplier=self.supplier,
            quantity=Decimal("10"), unit_price=Decimal("5.00"),
        )

    def _post(self, phone, body):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{phone}",
            "Body": body,
        })

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_modification_intent")
    def test_update_intent_for_item_not_in_order_still_adds_it(self, mock_parse, mock_send):
        """
        Regression: the AI can classify "I want 5kg of red potatoes" as
        'update' even though red potatoes aren't in the order yet. That used
        to match neither the update branch (no existing_orp) nor the add
        branch (guarded to intent=="add" only), so it silently did nothing —
        no error, no confirmation, no log. It must now be added.
        """
        mock_parse.return_value = {
            "intent": "update",
            "items": [{"product_name": "תפוח אדמה אדום", "quantity": Decimal("5")}],
        }

        self._post("+972507777777", "רוצה גם 5 קילו תפוח אדמה אדום")

        self.assertTrue(
            OrderRequestProduct.objects.filter(order_request=self.order, product=self.potato).exists()
        )
        msg = mock_send.call_args_list[-1][0][1]
        self.assertNotIn("לא הצלחתי לזהות שינוי", msg)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_modification_intent")
    def test_update_intent_for_existing_item_updates_quantity(self, mock_parse, mock_send):
        """Regression guard: real updates to an item already in the order still work."""
        mock_parse.return_value = {
            "intent": "update",
            "items": [{"product_name": "עגבניה", "quantity": Decimal("15")}],
        }

        self._post("+972507777777", "תעדכן את העגבניות ל-15")

        self.orp.refresh_from_db()
        self.assertEqual(self.orp.quantity, Decimal("15"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_modification_intent")
    def test_supplier_can_actually_confirm_an_added_item(self, mock_parse, mock_send):
        """
        Regression: adding an item sent the supplier a "please confirm"
        message but never registered it as pending, so their "אישור" reply
        fell through to the free-text price-update parser and got
        "המוצר 'אישור' לא קיים בקטלוג" instead of ever confirming anything.
        """
        mock_parse.return_value = {
            "intent": "add",
            "items": [{"product_name": "תפוח אדמה אדום", "quantity": Decimal("5")}],
        }
        self._post("+972507777777", "תוסיף 5 קילו תפוח אדמה אדום")
        mock_send.reset_mock()

        response = self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{self.supplier.whatsapp_number}",
            "Body": "אישור",
        })

        self.assertEqual(response.status_code, 200)
        ack = mock_send.call_args_list[0][0][1]
        self.assertNotIn("לא קיים בקטלוג", ack)
        self.assertIn("קיבלתי", ack)
        new_orp = OrderRequestProduct.objects.get(order_request=self.order, product=self.potato)
        self.assertEqual(SupplierConfirmation.objects.filter(order_request_product=new_orp).count(), 1)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_modification_intent")
    def test_multiple_additions_to_same_supplier_send_one_message(self, mock_parse, mock_send):
        """Two items added in one go must reach the supplier as one message, not two."""
        lettuce = make_product("חסה")
        SupplierProduct.objects.create(supplier=self.supplier, product=lettuce, price_per_unit="4.00")
        mock_parse.return_value = {
            "intent": "add",
            "items": [
                {"product_name": "תפוח אדמה אדום", "quantity": Decimal("5")},
                {"product_name": "חסה", "quantity": Decimal("2")},
            ],
        }

        self._post("+972507777777", "תוסיף גם תפוח אדמה אדום וגם חסה")

        supplier_calls = [
            c for c in mock_send.call_args_list
            if c[0][0] == self.supplier.whatsapp_number
        ]
        self.assertEqual(len(supplier_calls), 1)
        body = supplier_calls[0][0][1]
        self.assertIn("תפוח אדמה אדום", body)
        self.assertIn("חסה", body)


# ─────────────────────── Supplier: confirmation flow ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class SupplierConfirmationFlowTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user_with_profile()
        self.tomato = make_product("עגבניה")
        self.carrot = make_product("גזר")
        self.supplier = make_supplier("ספק א")
        self.order = OrderRequest.objects.create(
            user=self.user, total_price="100.00", status=OrderRequest.Status.SENT,
        )
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

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_full_confirmation_word_creates_supplier_confirmations(self, mock_send):
        """'אישור' creates SupplierConfirmation for all products."""
        self._post_supplier("אישור")

        self.assertEqual(SupplierConfirmation.objects.filter(order_request_product__order_request=self.order).count(), 2)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_full_confirmation_marks_order_approved(self, mock_send):
        """Once every item is confirmed, the order moves from SENT to APPROVED."""
        self._post_supplier("אישור")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.APPROVED)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_full_confirmation_clears_cache(self, mock_send):
        """After full confirmation, supplier pending cache is cleared."""
        self._post_supplier("אישור")

        self.assertIsNone(cache.get(f"whatsapp_supplier_pending:{self.supplier.whatsapp_number}"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_partial_confirmation_by_name(self, mock_send):
        """Partial reply (name: quantity) creates SupplierConfirmation only for matched products."""
        self._post_supplier("עגבניה: 18")

        confirmations = SupplierConfirmation.objects.filter(order_request_product__order_request=self.order)
        self.assertEqual(confirmations.count(), 1)
        self.assertEqual(confirmations.first().confirmed_quantity, Decimal("18"))
        self.assertEqual(confirmations.first().order_request_product, self.orp1)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_unrecognized_reply_sends_help_message(self, mock_send):
        """Gibberish reply → supplier gets 'couldn't understand' message."""
        self._post_supplier("xxxxxxxxx")

        mock_send.assert_called_once()
        self.assertIn("אישור", mock_send.call_args[0][1])
        # Cache should remain
        self.assertIsNotNone(cache.get(f"whatsapp_supplier_pending:{self.supplier.whatsapp_number}"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_sends_confirmation_summary_back_to_supplier(self, mock_send):
        """After confirming, supplier receives a summary of the confirmed quantities."""
        self._post_supplier("כן")

        # First call is the supplier ack; customer notification may add a second call
        self.assertGreaterEqual(mock_send.call_count, 1)
        msg = mock_send.call_args_list[0][0][1]
        self.assertIn("✅", msg)
        self.assertIn("עגבניה", msg)


# ─────────────────────── Supplier: price update flow ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class SupplierPriceUpdateFlowTests(TestCase):

    def setUp(self):
        cache.clear()
        self.supplier = make_supplier("ספק ב")

    def _post_supplier(self, body):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{self.supplier.whatsapp_number}",
            "Body": body,
        })

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.catalog.price_parser.update_prices_from_message")
    def test_no_pending_order_routes_to_price_update(self, mock_update, mock_send):
        """Supplier with no pending order gets price-update flow."""
        mock_update.return_value = {
            "updated": [{"product_name": "עגבניה", "price": "4.00", "is_new": False}],
            "removed": [],
            "skipped": [],
        }

        self._post_supplier("עגבניה 4.00")

        mock_update.assert_called_once()
        mock_send.assert_called_once()
        self.assertIn("עודכנו", mock_send.call_args[0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.catalog.price_parser.update_prices_from_message")
    def test_price_update_sends_new_products_section(self, mock_update, mock_send):
        """New products are highlighted in the response."""
        mock_update.return_value = {
            "updated": [{"product_name": "פרי חדש", "price": "7.50", "is_new": True}],
            "removed": [],
            "skipped": [],
        }

        self._post_supplier("פרי חדש 7.50")

        msg = mock_send.call_args[0][1]
        self.assertIn("🆕", msg)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.catalog.price_parser.update_prices_from_message")
    def test_unrecognized_price_message_sends_help(self, mock_update, mock_send):
        """Empty update result → help message."""
        mock_update.return_value = {"updated": [], "removed": [], "skipped": []}

        self._post_supplier("שלום מה שלומך")

        mock_send.assert_called_once()
        self.assertIn("לא זיהיתי", mock_send.call_args[0][1])
