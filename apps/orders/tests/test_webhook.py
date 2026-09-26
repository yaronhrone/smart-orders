"""
Tests for apps/orders/whatsapp_webhook.py.

External dependencies mocked:
  - send_whatsapp_message  (Twilio)
  - parse_customer_order   (OpenAI)
  - update_prices_from_message (OpenAI, in supplier price-update path)

Cache is overridden to LocMemCache so tests are isolated.
"""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone as django_timezone

from apps.catalog.models import Product, Supplier, SupplierProduct, Region, Unit
from apps.orders.models import OrderRequest, OrderRequestProduct, SupplierConfirmation
from apps.orders.tests.factories import make_order
from apps.orders.whatsapp import (
    _parse_delivery_eta,
    _parse_supplier_reply,
    save_pending_order,
    save_supplier_pending_order,
)
from apps.orders.whatsapp.cache import get_draft_order
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


def _flush_draft(phone, is_grace_retry=False):
    """
    A new order no longer prices/replies inline — it merges into a draft and
    schedules dispatch_draft_order_task to fire after the debounce window.
    Tests run with no Celery worker consuming the broker, so simulate "the
    debounce window elapsed" by invoking the task directly, synchronously,
    with the draft's current generation.
    """
    from apps.orders.tasks import dispatch_draft_order_task
    from apps.orders.whatsapp.cache import get_draft_order

    draft = get_draft_order(phone)
    if draft is not None:
        dispatch_draft_order_task(phone, draft["generation"], is_grace_retry=is_grace_retry)


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


# ─────────────────────── _parse_delivery_eta (pure) ───────────────────────

class ParseDeliveryEtaTests(TestCase):

    def test_absolute_time_with_arrival_verb(self):
        self.assertEqual(_parse_delivery_eta("אישור, יגיע עד 14:00").strftime("%H:%M"), "14:00")

    def test_absolute_time_with_different_phrasing(self):
        self.assertEqual(
            _parse_delivery_eta("אישור, המשלוח יגיע בסביבות השעה 09:30").strftime("%H:%M"), "09:30"
        )

    def test_no_eta_mentioned_returns_none(self):
        self.assertIsNone(_parse_delivery_eta("אישור"))

    def test_bare_cutoff_phrasing_is_not_mistaken_for_an_eta(self):
        """"ניתן לשנות עד 14:00" is a change-cutoff (_parse_supplier_cutoff's job), not an ETA."""
        self.assertIsNone(_parse_delivery_eta("אישור, ניתן לשנות עד 14:00"))

    def test_relative_hour_word(self):
        eta = _parse_delivery_eta("אישור, תוך שעה")
        expected = (django_timezone.localtime() + timedelta(hours=1)).time()
        self.assertEqual(eta.strftime("%H:%M"), expected.strftime("%H:%M"))

    def test_relative_two_hours_word(self):
        eta = _parse_delivery_eta("אישור, תוך שעתיים")
        expected = (django_timezone.localtime() + timedelta(hours=2)).time()
        self.assertEqual(eta.strftime("%H:%M"), expected.strftime("%H:%M"))

    def test_relative_numeric_hours(self):
        eta = _parse_delivery_eta("אישור, תוך 3 שעות")
        expected = (django_timezone.localtime() + timedelta(hours=3)).time()
        self.assertEqual(eta.strftime("%H:%M"), expected.strftime("%H:%M"))


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
        _flush_draft("+972501111111")

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
        _flush_draft("+972503333333")

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
        _flush_draft("+972504444444")

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
        _flush_draft("+972501234567")

        # Should reach suggest_order (not "not registered")
        msg = mock_send.call_args[0][1]
        self.assertNotIn("לא רשום", msg)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_one_unavailable_product_does_not_block_the_rest(self, mock_parse, mock_send):
        """
        Regression: a single product with zero suppliers in the region used
        to fail the ENTIRE order (suggest_order -> ValueError), losing every
        other perfectly orderable item along with it. The rest must still
        be offered, with the unavailable one reported alongside it.
        """
        onion = make_product("בצל")  # deliberately no SupplierProduct at all
        make_user_with_profile(phone="+972505555555")
        mock_parse.return_value = [
            {"product_name": "עגבניה", "quantity": Decimal("5")},
            {"product_name": "בצל", "quantity": Decimal("3")},
            {"product_name": "גזר", "quantity": Decimal("10")},
        ]

        self._post("+972505555555", "5 עגבניה, 3 בצל, 10 גזר")
        _flush_draft("+972505555555")

        msg = mock_send.call_args[0][1]
        self.assertIn("עגבניה", msg)
        self.assertIn("גזר", msg)
        self.assertIn("בצל", msg)
        self.assertIn("אין ספק שיכול לספק", msg)
        # The rest still priced and offered — not the generic hard-fail message.
        self.assertIn("₪", msg)

        cached = json.loads(cache.get("whatsapp_order:+972505555555"))
        cached_product_ids = {p["product_id"] for p in cached["products"]}
        self.assertNotIn(onion.id, cached_product_ids)
        self.assertEqual(cached_product_ids, {self.tomato.id, self.carrot.id})


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
        _flush_draft("+972506666666")

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
        _flush_draft("+972506666666")

        msg = mock_send.call_args[0][1]
        self.assertNotIn("*א*", msg)
        self.assertNotIn("*ב*", msg)
        self.assertIn("ענה *אישור*", msg)
        self.assertIn("ספק גדול", msg)  # explains what's excluded and why

        data = json.loads(cache.get("whatsapp_order:+972506666666"))
        self.assertEqual(data["single_scenario"], "cheapest")

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    @patch("apps.orders.services.suggest_order")
    def test_same_price_scenarios_still_checked_for_minimum(self, mock_suggest, mock_parse, mock_send):
        """
        Regression: cheapest and fewest_suppliers ending up at the exact same
        total price (the common case — one dominant supplier for everything)
        used to take the single-confirm shortcut unconditionally, without
        ever checking whether that shared total actually clears its
        supplier's minimum. The customer got a bare "✅ ... ענה אישור" and
        only found out it would be rejected after replying, losing the whole
        basket with no grace period — see _handle_user_flow's dead-code-ish
        scenario_issues safety net, which this bug is what actually reached.
        """
        mock_parse.return_value = [{"product_name": "עגבניה", "quantity": Decimal("10")}]
        mock_suggest.return_value = {
            "cheapest": _scenario("20.00", supplier_name="ספק גדול"),
            "fewest_suppliers": _scenario("20.00", supplier_name="ספק גדול"),
            "minimum_issues": {
                "cheapest": _issue("ספק גדול"),
                "fewest_suppliers": _issue("ספק גדול"),
            },
        }

        self._post("+972506666666", "10 עגבניות")
        _flush_draft("+972506666666")

        msg = mock_send.call_args[0][1]
        self.assertIn("⛔", msg)
        self.assertNotIn("ענה *אישור*", msg)
        # Same as the "both fail minimum" path elsewhere — held as a draft
        # with a grace window, not silently confirmable, and no stale
        # whatsapp_order cache waiting to fool the old scenario_issues check.
        self.assertIsNone(cache.get("whatsapp_order:+972506666666"))

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
        order = make_order(self.user, make_supplier("ספק זול"), total_price=Decimal("50.00"))
        mock_build.return_value = (order.batch, [order], {})

        self._post("+972506666666", "10 עגבניות")
        _flush_draft("+972506666666")
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
        _flush_draft("+972506666666")

        msg = mock_send.call_args[0][1]
        self.assertIn("⛔", msg)
        self.assertNotIn("*א*", msg)
        self.assertNotIn("ענה *אישור*", msg)
        self.assertIsNone(cache.get("whatsapp_order:+972506666666"))

    @patch("apps.orders.tasks.dispatch_draft_order_task.apply_async")
    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    @patch("apps.orders.services.suggest_order")
    def test_both_failing_minimum_holds_a_grace_draft_instead_of_dropping(
        self, mock_suggest, mock_parse, mock_send, mock_apply_async
    ):
        """
        Regression: a basket that clears no supplier's minimum used to be
        dropped outright, forcing the customer to retype the whole order.
        It should instead be held as a draft so a top-up message can merge
        into it, with a follow-up task scheduled to give up later if nobody does.
        """
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
        _flush_draft("+972506666666")

        msg = mock_send.call_args[0][1]
        self.assertIn("נשמרת", msg)
        self.assertIn("2 שעות", msg)

        from apps.orders.whatsapp.cache import get_draft_order, MINIMUM_GRACE_SECONDS
        draft = get_draft_order("+972506666666")
        self.assertIsNotNone(draft)
        self.assertEqual(draft["items"][0]["product_name"], "עגבניה")

        # apply_async was also called once already for the normal short
        # debounce (_handle_new_order, on the way in) — the grace scheduling
        # is the second, distinguishable by its own countdown/kwargs.
        grace_call = mock_apply_async.call_args_list[-1]
        self.assertEqual(grace_call.kwargs["countdown"], MINIMUM_GRACE_SECONDS)
        self.assertEqual(grace_call.kwargs["kwargs"], {"is_grace_retry": True})

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    @patch("apps.orders.services.suggest_order")
    def test_topup_message_during_grace_period_merges_into_the_held_draft(
        self, mock_suggest, mock_parse, mock_send
    ):
        """A follow-up message while the grace draft is open adds to the same basket, not a separate one."""
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
        _flush_draft("+972506666666")

        mock_parse.return_value = [{"product_name": "עגבניה", "quantity": Decimal("40")}]
        mock_suggest.return_value = {
            "cheapest": _scenario("500.00"),
            "fewest_suppliers": _scenario("550.00"),
            "minimum_issues": {"cheapest": [], "fewest_suppliers": []},
        }
        self._post("+972506666666", "עוד 40 עגבניות")
        _flush_draft("+972506666666")

        from apps.orders.whatsapp.cache import get_draft_order
        self.assertIsNone(get_draft_order("+972506666666"))  # cleared once it actually dispatched
        msg = mock_send.call_args[0][1]
        self.assertIn("*א*", msg)
        self.assertIn("*ב*", msg)
        # Proves the top-up actually merged into the held draft (10 + 40),
        # rather than the second message replacing or ignoring the first.
        priced_items = mock_suggest.call_args.kwargs["products"]
        self.assertEqual(priced_items[0]["quantity"], Decimal("50"))

    @patch("apps.orders.tasks.dispatch_draft_order_task.apply_async")
    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    @patch("apps.orders.services.suggest_order")
    def test_grace_retry_still_failing_cancels_for_good(
        self, mock_suggest, mock_parse, mock_send, mock_apply_async
    ):
        """After the grace window, still under minimum -> cancel outright, no second grace period."""
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
        _flush_draft("+972506666666")
        mock_apply_async.reset_mock()
        mock_send.reset_mock()

        _flush_draft("+972506666666", is_grace_retry=True)

        msg = mock_send.call_args[0][1]
        self.assertIn("בוטלה", msg)
        mock_apply_async.assert_not_called()  # no second grace period

        from apps.orders.whatsapp.cache import get_draft_order
        self.assertIsNone(get_draft_order("+972506666666"))


# ─────────────────────── User: ambiguous product names ──────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class AmbiguousProductWebhookTests(TestCase):
    """
    "תפוח אדמה" alone matches two catalog products (אדום/לבן) — end-to-end
    through the real webhook. An ambiguous item rides along in the draft
    like any other message (see cache.save_draft_order's `ambiguous` param)
    and is only asked about once the debounce window closes — never
    mid-message, so it can no longer split one customer's intent into two
    separate orders when another message lands in the same window (see
    test_clean_message_before_ambiguous_one_merges_into_one_order below).
    parse_customer_order itself is real here (no OpenAI call needed for
    these messages — resolves entirely via the fast dict path).
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
    def test_ambiguous_name_does_not_ask_until_debounce_fires(self, mock_send):
        """The ambiguous item is held, not answered immediately — nothing sent yet."""
        self._post("+972508888888", "5 קילו תפוח אדמה")

        mock_send.assert_not_called()
        draft = get_draft_order("+972508888888")
        self.assertEqual(len(draft["ambiguous"]), 1)
        self.assertEqual(draft["ambiguous"][0]["query"], "תפוח אדמה")

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_debounce_asks_about_the_ambiguous_item(self, mock_send):
        self._post("+972508888888", "5 קילו תפוח אדמה")
        _flush_draft("+972508888888")

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
        _flush_draft("+972508888888")
        self._post("+972508888888", "אדום")

        msg = mock_send.call_args[0][1]
        self.assertIn("תפוח אדמה אדום", msg)
        self.assertIn("₪", msg)
        self.assertIsNone(cache.get("whatsapp_clarify:+972508888888"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_unrecognized_answer_asks_again(self, mock_send):
        self._post("+972508888888", "5 קילו תפוח אדמה")
        _flush_draft("+972508888888")
        self._post("+972508888888", "לא יודע")

        msg = mock_send.call_args[0][1]
        self.assertIn("אדום", msg)
        self.assertIn("לבן", msg)
        self.assertIsNotNone(cache.get("whatsapp_clarify:+972508888888"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_clean_message_before_ambiguous_one_merges_into_one_order(self, mock_send):
        """
        Regression: an ambiguous item used to be asked about immediately,
        outside the draft entirely — a clean message sent moments before or
        after in the same debounce window ended up building a SEPARATE
        order instead of merging into one basket with everything else.
        """
        self._post("+972508888888", "10 קילו תפוח אדמה אדום")  # resolves cleanly
        self._post("+972508888888", "5 קילו תפוח אדמה")         # ambiguous
        _flush_draft("+972508888888")

        ask = mock_send.call_args[0][1]
        self.assertIn("אדום", ask)
        self.assertIn("לבן", ask)
        mock_send.reset_mock()

        self._post("+972508888888", "לבן")

        final = mock_send.call_args[0][1]
        # Both the already-clean item and the newly-resolved one land in ONE
        # scenario — not two separate orders from two separate timers.
        self.assertIn("תפוח אדמה אדום", final)
        self.assertIn("תפוח אדמה לבן", final)
        self.assertIn("₪", final)


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
        self.order = make_order(self.user, self.supplier, status=OrderRequest.Status.SENT, total_price=Decimal("50.00")
        )
        self.orp = OrderRequestProduct.objects.create(
            order_request=self.order, product=self.tomato, supplier=self.supplier,
            quantity=Decimal("10"), unit_price=Decimal("5.00"),
        )
        # These tests exercise the pre-cutoff modification path — pin the
        # clock well before DAILY_UPDATE_CUTOFF (23:00) so the suite isn't
        # flaky depending on what time it's actually run.
        self.time_patcher = patch(
            "apps.orders.whatsapp.user_flow.timezone.localtime",
            return_value=django_timezone.make_aware(datetime(2026, 1, 1, 10, 0)),
        )
        self.time_patcher.start()
        self.addCleanup(self.time_patcher.stop)

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
        self.assertIn(f"#{self.order.id}", ack)
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

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_modification_intent")
    def test_ambiguous_addition_asks_then_completes_on_reply(self, mock_parse, mock_send):
        """
        "בצל" isn't itself a catalog product (only בצל יבש/בצל סגול are) —
        adding it must ask which one, then actually add it once answered,
        including sending the supplier a real confirmable message.
        """
        onion_dry = make_product("בצל יבש")
        onion_red = make_product("בצל סגול")
        SupplierProduct.objects.create(supplier=self.supplier, product=onion_dry, price_per_unit="2.00")
        SupplierProduct.objects.create(supplier=self.supplier, product=onion_red, price_per_unit="2.50")
        mock_parse.return_value = {
            "intent": "add",
            "items": [{"product_name": "בצל", "quantity": Decimal("3")}],
        }

        self._post("+972507777777", "תוסיף גם 3 קילו בצל")
        ask = mock_send.call_args[0][1]
        self.assertIn("בצל", ask)
        self.assertIn("יבש", ask)
        self.assertIn("סגול", ask)
        self.assertFalse(
            OrderRequestProduct.objects.filter(order_request=self.order, product=onion_dry).exists()
        )

        mock_send.reset_mock()
        self._post("+972507777777", "יבש")

        self.assertTrue(
            OrderRequestProduct.objects.filter(order_request=self.order, product=onion_dry).exists()
        )
        supplier_calls = [c for c in mock_send.call_args_list if c[0][0] == self.supplier.whatsapp_number]
        self.assertEqual(len(supplier_calls), 1)
        self.assertIn("בצל יבש", supplier_calls[0][0][1])
        pending = cache.get(f"whatsapp_supplier_pending:{self.supplier.whatsapp_number}")
        self.assertIsNotNone(pending)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_modification_intent")
    def test_resolved_items_dispatch_before_asking_about_ambiguous_one(self, mock_parse, mock_send):
        """A clear item in the same message must not wait on the ambiguous one."""
        onion_dry = make_product("בצל יבש")
        onion_red = make_product("בצל סגול")
        SupplierProduct.objects.create(supplier=self.supplier, product=onion_dry, price_per_unit="2.00")
        SupplierProduct.objects.create(supplier=self.supplier, product=onion_red, price_per_unit="2.50")
        mock_parse.return_value = {
            "intent": "update",
            "items": [
                {"product_name": "עגבניה", "quantity": Decimal("20")},
                {"product_name": "בצל", "quantity": Decimal("3")},
            ],
        }

        self._post("+972507777777", "עדכן 20 עגבניה וגם 3 בצל")

        self.orp.refresh_from_db()
        self.assertEqual(self.orp.quantity, Decimal("20"))
        supplier_calls = [c for c in mock_send.call_args_list if c[0][0] == self.supplier.whatsapp_number]
        self.assertEqual(len(supplier_calls), 1)
        self.assertIn("עגבניה", supplier_calls[0][0][1])


# ─────────────────────── User: delivery confirmation ─────────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class DeliveryConfirmationWordTests(TestCase):
    """"קיבלתי" is literally the word a customer types to say goods arrived —
    it was missing from ARRIVAL_WORDS, so that exact message fell straight
    through delivery-flow detection instead of confirming anything."""

    def setUp(self):
        cache.clear()
        self.user = make_user_with_profile(phone="+972509999999")
        self.tomato = make_product("עגבניה")
        self.supplier = make_supplier("ספק א")
        # A supplier reply moves the order SENT -> APPROVED before delivery
        # is ever a question — APPROVED (not SENT) is the realistic starting
        # status for testing "קיבלתי", and is what the delivery_flow filter
        # fix now actually looks for.
        self.order = make_order(self.user, self.supplier, status=OrderRequest.Status.APPROVED, total_price=Decimal("50.00")
        )
        OrderRequestProduct.objects.create(
            order_request=self.order, product=self.tomato, supplier=self.supplier,
            quantity=Decimal("10"), unit_price=Decimal("5.00"),
        )

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_receiving_word_marks_single_supplier_order_delivered(self, mock_send):
        self.client.post("/whatsapp/webhook/", {
            "From": "whatsapp:+972509999999",
            "Body": "קיבלתי",
        })

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.DELIVERED)
        mock_send.assert_called_once()
        self.assertIn("אושרה כנמסרה", mock_send.call_args[0][1])


# ─────────────────────── Supplier: confirmation flow ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class SupplierConfirmationFlowTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user_with_profile()
        self.tomato = make_product("עגבניה")
        self.carrot = make_product("גזר")
        self.supplier = make_supplier("ספק א")
        self.order = make_order(self.user, self.supplier, total_price="100.00", status=OrderRequest.Status.SENT,
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
    def test_shortage_report_without_confirm_word_keeps_the_rest_pending(self, mock_send):
        """
        Regression, found live: a supplier reporting a shortage with no
        explicit confirm word in the same message ("חסר גזר", no "שאר
        אישור") used to leave the OTHER item neither confirmed nor missing —
        and since the pending cache was cleared unconditionally regardless,
        there was no way to ever confirm it afterward. The order stayed
        SENT forever with no sign anything was still needed.
        """
        self._post_supplier("חסר גזר")

        # Tomato is neither confirmed nor missing yet.
        self.assertFalse(
            SupplierConfirmation.objects.filter(order_request_product=self.orp1).exists()
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.SENT)

        ack = mock_send.call_args_list[0][0][1]
        self.assertIn("טרם אושר", ack)
        self.assertIn("עגבניה", ack)

        # The pending state must still be alive, trimmed to just the tomato,
        # so a follow-up reply can actually confirm it.
        pending = cache.get(f"whatsapp_supplier_pending:{self.supplier.whatsapp_number}")
        self.assertIsNotNone(pending)
        self.assertEqual(json.loads(pending)["products"], [
            {"orp_id": self.orp1.id, "product_name": "עגבניה", "quantity": "20", "unit": 'ק"ג'}
        ])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_shortage_then_followup_confirm_completes_the_order(self, mock_send):
        """The follow-up reply left possible by the fix above actually finishes the order."""
        self._post_supplier("חסר גזר")

        self._post_supplier("אישור")

        self.assertTrue(
            SupplierConfirmation.objects.filter(order_request_product=self.orp1).exists()
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.APPROVED)
        self.assertIsNone(cache.get(f"whatsapp_supplier_pending:{self.supplier.whatsapp_number}"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_full_confirmation_mentions_shipping_keyword(self, mock_send):
        """
        Nothing told a supplier the "יצא למשלוח" feature exists at all once
        an order is fully confirmed — it's otherwise a completely
        undocumented keyword.
        """
        self._post_supplier("אישור")

        ack = mock_send.call_args_list[0][0][1]
        self.assertIn("יצא למשלוח", ack)

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

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_confirmation_with_eta_reaches_both_supplier_ack_and_customer(self, mock_send):
        """A supplier who mentions an arrival time while confirming gets it
        echoed back, and the customer's notification carries it too."""
        self._post_supplier("אישור, יגיע עד 13:45")

        supplier_msg = mock_send.call_args_list[0][0][1]
        self.assertIn("13:45", supplier_msg)

        customer_calls = [c for c in mock_send.call_args_list if c[0][0] == "+972501234567"]
        self.assertEqual(len(customer_calls), 1)
        self.assertIn("13:45", customer_calls[0][0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_confirmation_without_eta_mentions_none(self, mock_send):
        """No ETA phrasing at all -> no ETA line anywhere, same messages as before this feature."""
        self._post_supplier("אישור")

        for call in mock_send.call_args_list:
            self.assertNotIn("צפוי", call[0][1])
            self.assertNotIn("צפויה", call[0][1])


# ─────────────────────── Supplier: cancellation flow ───────────────────────

@override_settings(
    CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True,
    ADMIN_WHATSAPP_NUMBER="+972500000000",
)
class SupplierCancellationFlowTests(TestCase):
    """
    A supplier replying with a cancel keyword ("ביטול" etc.) to its pending
    order must: (1) get rerouted onto whichever other supplier(s) are
    cheapest, split across more than one if that's what it takes — not just
    a single all-or-nothing replacement; (2) block the cancelling supplier
    from new assignments for 10 days; (3) alert the admin; and only when
    NOTHING can be rerouted, cancel the order outright.
    """

    def setUp(self):
        cache.clear()
        self.user = make_user_with_profile()
        self.tomato = make_product("עגבניה")
        self.carrot = make_product("גזר")
        self.supplier = make_supplier("ספק א")
        self.order = make_order(self.user, self.supplier, total_price="100.00", status=OrderRequest.Status.SENT,
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
    def test_single_replacement_supplier_gets_everything(self, mock_send):
        replacement = make_supplier("מחליף")
        SupplierProduct.objects.create(supplier=replacement, product=self.tomato, price_per_unit="6.00")
        SupplierProduct.objects.create(supplier=replacement, product=self.carrot, price_per_unit="4.00")

        self._post_supplier("ביטול")

        self.orp1.refresh_from_db()
        self.orp2.refresh_from_db()
        self.assertEqual(self.orp1.supplier, replacement)
        self.assertEqual(self.orp2.supplier, replacement)
        # One order per supplier: the items now live in the replacement's own
        # order in the same checkout, which is live; the canceller's order
        # is cancelled — and ONLY it.
        new_order = self.orp1.order_request
        self.assertEqual(new_order, self.orp2.order_request)
        self.assertEqual(new_order.supplier, replacement)
        self.assertEqual(new_order.batch, self.order.batch)
        self.assertEqual(new_order.status, OrderRequest.Status.SENT)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.CANCELLED)

        # New supplier got its own order message + pending state.
        replacement_calls = [c for c in mock_send.call_args_list if c[0][0] == replacement.whatsapp_number]
        self.assertEqual(len(replacement_calls), 1)
        self.assertIsNotNone(cache.get(f"whatsapp_supplier_pending:{replacement.whatsapp_number}"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_splits_across_two_suppliers_when_no_single_one_covers_both(self, mock_send):
        tomato_only = make_supplier("רק עגבניה")
        carrot_only = make_supplier("רק גזר")
        SupplierProduct.objects.create(supplier=tomato_only, product=self.tomato, price_per_unit="6.00")
        SupplierProduct.objects.create(supplier=carrot_only, product=self.carrot, price_per_unit="4.00")

        self._post_supplier("ביטול")

        self.orp1.refresh_from_db()
        self.orp2.refresh_from_db()
        self.assertEqual(self.orp1.supplier, tomato_only)
        self.assertEqual(self.orp2.supplier, carrot_only)

        self.assertIsNotNone(cache.get(f"whatsapp_supplier_pending:{tomato_only.whatsapp_number}"))
        self.assertIsNotNone(cache.get(f"whatsapp_supplier_pending:{carrot_only.whatsapp_number}"))

        customer_calls = [c for c in mock_send.call_args_list if c[0][0] == "+972501234567"]
        self.assertEqual(len(customer_calls), 1)
        self.assertIn(tomato_only.name, customer_calls[0][0][1])
        self.assertIn(carrot_only.name, customer_calls[0][0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_no_replacement_at_all_cancels_the_order(self, mock_send):
        self._post_supplier("ביטול")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.CANCELLED)
        customer_calls = [c for c in mock_send.call_args_list if c[0][0] == "+972501234567"]
        self.assertEqual(len(customer_calls), 1)
        self.assertIn("לא נמצא ספק חלופי", customer_calls[0][0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_cancelling_supplier_is_blocked_for_ten_days(self, mock_send):
        before = django_timezone.now()
        self._post_supplier("ביטול")

        self.supplier.refresh_from_db()
        self.assertIsNotNone(self.supplier.blocked_until)
        self.assertGreater(self.supplier.blocked_until, before + timedelta(days=9))
        self.assertLess(self.supplier.blocked_until, before + timedelta(days=11))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_admin_is_notified(self, mock_send):
        self._post_supplier("ביטול")

        admin_calls = [c for c in mock_send.call_args_list if c[0][0] == "+972500000000"]
        self.assertEqual(len(admin_calls), 1)
        self.assertIn(self.supplier.name, admin_calls[0][0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_split_left_under_minimum_holds_open_for_a_customer_topup(self, mock_send):
        """
        A split across two specialist suppliers that between them cover
        everything, but neither alone clears its own minimum, must NOT be
        dispatched AND must NOT be cancelled outright — the user's rule: give
        the customer a chance to add enough to clear it first.
        """
        tomato_only = make_supplier("רק עגבניה", minimum_order=1000)
        carrot_only = make_supplier("רק גזר", minimum_order=1000)
        SupplierProduct.objects.create(supplier=tomato_only, product=self.tomato, price_per_unit="6.00")
        SupplierProduct.objects.create(supplier=carrot_only, product=self.carrot, price_per_unit="4.00")

        self._post_supplier("ביטול")

        self.orp1.refresh_from_db()
        self.orp2.refresh_from_db()
        self.assertEqual(self.orp1.supplier, self.supplier)  # untouched — still parked on the canceller
        self.assertEqual(self.orp2.supplier, self.supplier)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.SENT)  # held, not cancelled
        customer_calls = [c for c in mock_send.call_args_list if c[0][0] == "+972501234567"]
        self.assertEqual(len(customer_calls), 1)
        self.assertIn("לא עומד במינימום", customer_calls[0][0][1])
        self.assertIn("רק עגבניה", customer_calls[0][0][1])
        self.assertIn("רק גזר", customer_calls[0][0][1])
        self.assertIsNotNone(cache.get("whatsapp_reroute_grace:+972501234567"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_topup_that_clears_the_minimum_dispatches_the_reroute(self, mock_parse, mock_send):
        """A customer reply that adds enough quantity clears the minimum and
        the held reroute finally gets dispatched for real."""
        tomato_only = make_supplier("רק עגבניה", minimum_order=100)
        carrot_only = make_supplier("רק גזר", minimum_order=1000)
        SupplierProduct.objects.create(supplier=tomato_only, product=self.tomato, price_per_unit="6.00")
        SupplierProduct.objects.create(supplier=carrot_only, product=self.carrot, price_per_unit="4.00")
        self._post_supplier("ביטול")  # tomato (20*6=120) clears its 100 min; carrot (15*4=60) doesn't

        mock_parse.return_value = [{"product_name": "גזר", "quantity": Decimal("250")}]
        self.client.post("/whatsapp/webhook/", {
            "From": "whatsapp:+972501234567", "Body": "עוד 250 גזר",
        })

        self.orp2.refresh_from_db()
        self.assertEqual(self.orp2.supplier, carrot_only)
        self.assertEqual(self.orp2.order_request.supplier, carrot_only)
        self.assertEqual(self.orp2.order_request.status, OrderRequest.Status.SENT)
        # Held open until now; once dispatched the canceller's order is cancelled.
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.CANCELLED)
        self.assertIsNone(cache.get("whatsapp_reroute_grace:+972501234567"))
        carrot_supplier_calls = [c for c in mock_send.call_args_list if c[0][0] == carrot_only.whatsapp_number]
        self.assertEqual(len(carrot_supplier_calls), 1)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_topup_still_short_reports_remaining_shortfall_and_keeps_waiting(self, mock_parse, mock_send):
        tomato_only = make_supplier("רק עגבניה", minimum_order=100)
        carrot_only = make_supplier("רק גזר", minimum_order=1000)
        SupplierProduct.objects.create(supplier=tomato_only, product=self.tomato, price_per_unit="6.00")
        SupplierProduct.objects.create(supplier=carrot_only, product=self.carrot, price_per_unit="4.00")
        self._post_supplier("ביטול")

        mock_parse.return_value = [{"product_name": "גזר", "quantity": Decimal("5")}]
        self.client.post("/whatsapp/webhook/", {
            "From": "whatsapp:+972501234567", "Body": "עוד 5 גזר",
        })

        self.orp2.refresh_from_db()
        self.assertEqual(self.orp2.supplier, self.supplier)  # still not moved — still short
        self.assertIsNotNone(cache.get("whatsapp_reroute_grace:+972501234567"))
        last_msg = mock_send.call_args_list[-1][0][1]
        self.assertIn("עדיין לא מספיק", last_msg)

    def test_grace_timeout_cancels_the_order_when_never_topped_up(self):
        tomato_only = make_supplier("רק עגבניה", minimum_order=1000)
        carrot_only = make_supplier("רק גזר", minimum_order=1000)
        SupplierProduct.objects.create(supplier=tomato_only, product=self.tomato, price_per_unit="6.00")
        SupplierProduct.objects.create(supplier=carrot_only, product=self.carrot, price_per_unit="4.00")
        with patch("apps.orders.whatsapp.validators.send_whatsapp_message"):
            self._post_supplier("ביטול")

        from apps.orders.tasks import handle_reroute_grace_timeout
        with patch("apps.orders.tasks.send_whatsapp_message") as mock_send:
            handle_reroute_grace_timeout(phone="+972501234567", order_request_id=self.order.id)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.CANCELLED)
        self.assertIsNone(cache.get("whatsapp_reroute_grace:+972501234567"))
        mock_send.assert_called_once()
        self.assertIn("פג הזמן", mock_send.call_args[0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_blocked_supplier_is_not_chosen_as_replacement_even_if_cheapest(self, mock_send):
        blocked = make_supplier("חסום")
        SupplierProduct.objects.create(supplier=blocked, product=self.tomato, price_per_unit="1.00")
        SupplierProduct.objects.create(supplier=blocked, product=self.carrot, price_per_unit="1.00")
        blocked.blocked_until = django_timezone.now() + timedelta(days=3)
        blocked.save(update_fields=["blocked_until"])
        pricier_available = make_supplier("זמין")
        SupplierProduct.objects.create(supplier=pricier_available, product=self.tomato, price_per_unit="9.00")
        SupplierProduct.objects.create(supplier=pricier_available, product=self.carrot, price_per_unit="9.00")

        self._post_supplier("ביטול")

        self.orp1.refresh_from_db()
        self.orp2.refresh_from_db()
        self.assertEqual(self.orp1.supplier, pricier_available)
        self.assertEqual(self.orp2.supplier, pricier_available)


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


# ─────────────────────── Component B: draft debounce ───────────────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class DraftDebounceTests(TestCase):
    """
    A customer's order-building messages merge into one draft instead of a
    second message ("גם 5 חסה") landing on an already-cached scenario offer
    from the first and getting silently dropped. No Celery worker runs
    during tests, so `_flush_draft` invokes dispatch_draft_order_task
    directly to simulate the debounce window elapsing.
    """

    def setUp(self):
        cache.clear()
        self.tomato = make_product("עגבניה")
        self.lettuce = make_product("חסה")
        self.supplier = make_supplier("ספק א")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.tomato, price_per_unit="5.00")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.lettuce, price_per_unit="2.00")
        self.phone = "+972510000001"
        make_user_with_profile(phone=self.phone)

    def _post(self, body):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{self.phone}",
            "Body": body,
        })

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_two_rapid_messages_merge_into_one_draft(self, mock_parse, mock_send):
        mock_parse.side_effect = [
            [{"product_name": "עגבניה", "quantity": Decimal("20")}],
            [{"product_name": "חסה", "quantity": Decimal("5")}],
        ]

        self._post("20 עגבניה")
        self._post("גם 5 חסה")

        # Still inside the debounce window — nothing sent yet, and nothing lost.
        mock_send.assert_not_called()

        draft = get_draft_order(self.phone)
        by_name = {i["product_name"]: i["quantity"] for i in draft["items"]}
        self.assertEqual(by_name["עגבניה"], Decimal("20"))
        self.assertEqual(by_name["חסה"], Decimal("5"))
        self.assertEqual(draft["generation"], 2)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_matching_generation_dispatches_and_clears_draft(self, mock_parse, mock_send):
        mock_parse.return_value = [{"product_name": "עגבניה", "quantity": Decimal("20")}]

        self._post("20 עגבניה")
        _flush_draft(self.phone)

        mock_send.assert_called_once()
        self.assertIn("עגבניה", mock_send.call_args[0][1])
        self.assertIsNone(get_draft_order(self.phone))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_stale_generation_task_run_is_noop(self, mock_parse, mock_send):
        """
        A task scheduled after the first message fires only after the
        countdown — by the time it does, a second message already bumped
        the generation and scheduled its own task. The stale run must do
        nothing, since acting would price/offer an incomplete order.
        """
        mock_parse.side_effect = [
            [{"product_name": "עגבניה", "quantity": Decimal("20")}],
            [{"product_name": "חסה", "quantity": Decimal("5")}],
        ]

        self._post("20 עגבניה")
        stale_generation = get_draft_order(self.phone)["generation"]
        self._post("גם 5 חסה")

        from apps.orders.tasks import dispatch_draft_order_task
        dispatch_draft_order_task(self.phone, stale_generation)

        mock_send.assert_not_called()
        self.assertIsNotNone(get_draft_order(self.phone))  # untouched by the stale run

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_ambiguous_product_enters_the_draft_instead_of_asking_immediately(self, mock_send):
        """
        An ambiguous item rides along in the draft (cache.save_draft_order's
        `ambiguous` param) instead of being answered immediately — asking
        right away used to let it escape the debounce window and become a
        separate order from whatever else the customer was mid-typing.
        """
        red = make_product("תפוח אדמה אדום")
        white = make_product("תפוח אדמה לבן")
        SupplierProduct.objects.create(supplier=self.supplier, product=red, price_per_unit="3.00")
        SupplierProduct.objects.create(supplier=self.supplier, product=white, price_per_unit="2.50")

        self._post("5 קילו תפוח אדמה")

        mock_send.assert_not_called()
        draft = get_draft_order(self.phone)
        self.assertIsNotNone(draft)
        self.assertEqual(draft["ambiguous"][0]["query"], "תפוח אדמה")
        self.assertIsNone(cache.get(f"whatsapp_clarify:{self.phone}"))


# ─────────────────────── Component C: daily update cutoff (23:00) ──────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class DailyUpdateCutoffTests(TestCase):
    """After 23:00, a message to an already-SENT order starts a new order
    instead of tacking onto the old one — without a cutoff, "update" would
    apply indefinitely, including to an order from days ago."""

    def setUp(self):
        cache.clear()
        self.phone = "+972511111111"
        self.tomato = make_product("עגבניה")
        self.supplier = make_supplier("ספק א")
        SupplierProduct.objects.create(supplier=self.supplier, product=self.tomato, price_per_unit="5.00")
        self.user = make_user_with_profile(phone=self.phone)
        self.order = make_order(self.user, self.supplier, status=OrderRequest.Status.SENT, total_price=Decimal("50.00")
        )
        OrderRequestProduct.objects.create(
            order_request=self.order, product=self.tomato, supplier=self.supplier,
            quantity=Decimal("10"), unit_price=Decimal("5.00"),
        )

    def _post(self, body):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{self.phone}",
            "Body": body,
        })

    def _at(self, hour, minute):
        return patch(
            "apps.orders.whatsapp.user_flow.timezone.localtime",
            return_value=django_timezone.make_aware(datetime(2026, 1, 1, hour, minute)),
        )

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_modification_intent")
    def test_before_cutoff_modifies_existing_order(self, mock_parse, mock_send):
        mock_parse.return_value = {
            "intent": "update",
            "items": [{"product_name": "עגבניה", "quantity": Decimal("15")}],
        }
        with self._at(10, 0):
            self._post("תעדכן את העגבניות ל-15")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.SENT)
        orp = OrderRequestProduct.objects.get(order_request=self.order, product=self.tomato)
        self.assertEqual(orp.quantity, Decimal("15"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_customer_order")
    def test_after_cutoff_sends_window_closed_and_starts_new_order(self, mock_parse, mock_send):
        mock_parse.return_value = [{"product_name": "עגבניה", "quantity": Decimal("5")}]
        with self._at(23, 30):
            self._post("5 עגבניה")

        msg = mock_send.call_args[0][1]
        self.assertIn("נסגר", msg)
        self.assertIn(f"#{self.order.id}", msg)

        # Routed through _handle_new_order (the draft/debounce path of
        # Component B), not a modification of the old SENT order.
        draft = get_draft_order(self.phone)
        self.assertIsNotNone(draft)
        self.assertEqual(draft["items"][0]["product_name"], "עגבניה")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.SENT)


# ─────────────────────── Component D: shipping + delivery chain ────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class ShippingAndDeliveryChainTests(TestCase):
    """
    Closes the chain: supplier marks an approved order shipped, customer
    confirms receipt — from both APPROVED (SHIPPED is optional) and SHIPPED.
    Also the direct regression guard for the pre-existing bug where
    delivery_flow only recognized SENT, so "קיבלתי" found nothing for the
    single most common case (a supplier who already confirmed).
    """

    def setUp(self):
        cache.clear()
        self.customer_phone = "+972512222222"
        self.tomato = make_product("עגבניה")
        self.supplier = make_supplier("ספק א")
        self.user = make_user_with_profile(phone=self.customer_phone)

    def _make_order(self, status):
        order = make_order(self.user, self.supplier, status=status, total_price=Decimal("50.00"))
        OrderRequestProduct.objects.create(
            order_request=order, product=self.tomato, supplier=self.supplier,
            quantity=Decimal("10"), unit_price=Decimal("5.00"),
        )
        return order

    def _post_customer(self, body):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{self.customer_phone}",
            "Body": body,
        })

    def _post_supplier(self, body):
        return self.client.post("/whatsapp/webhook/", {
            "From": f"whatsapp:{self.supplier.whatsapp_number}",
            "Body": body,
        })

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_receiving_word_confirms_from_approved_status(self, mock_send):
        """Regression guard: an APPROVED order (the realistic status once a
        supplier has fully confirmed) is found by "קיבלתי", where it used
        to find nothing at all."""
        order = self._make_order(OrderRequest.Status.APPROVED)

        self._post_customer("קיבלתי")

        order.refresh_from_db()
        self.assertEqual(order.status, OrderRequest.Status.DELIVERED)
        self.assertIn("אושרה כנמסרה", mock_send.call_args[0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_receiving_word_confirms_from_shipped_status(self, mock_send):
        order = self._make_order(OrderRequest.Status.SHIPPED)

        self._post_customer("קיבלתי")

        order.refresh_from_db()
        self.assertEqual(order.status, OrderRequest.Status.DELIVERED)
        self.assertIn("אושרה כנמסרה", mock_send.call_args[0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_not_arrived_is_not_misread_as_arrived(self, mock_send):
        """
        Regression: "לא הגיע" contains the bare word "הגיע", so this used to
        be silently read as confirming delivery — the opposite of what the
        customer said. It must not touch the order's status at all.
        """
        order = self._make_order(OrderRequest.Status.APPROVED)

        self._post_customer("לא הגיע")

        order.refresh_from_db()
        self.assertEqual(order.status, OrderRequest.Status.APPROVED)
        for call in mock_send.call_args_list:
            self.assertNotIn("אושרה כנמסרה", call[0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_not_arrived_single_supplier_asks_for_eta_directly(self, mock_send):
        self._make_order(OrderRequest.Status.APPROVED)

        self._post_customer("לא הגיע")

        supplier_calls = [c for c in mock_send.call_args_list if c[0][0] == self.supplier.whatsapp_number]
        self.assertEqual(len(supplier_calls), 1)
        self.assertIn("עדיין לא הגיעה", supplier_calls[0][0][1])

        customer_calls = [c for c in mock_send.call_args_list if c[0][0] == self.customer_phone]
        self.assertEqual(len(customer_calls), 1)
        self.assertIn(self.supplier.name, customer_calls[0][0][1])

        from apps.orders.whatsapp.cache import get_eta_request
        eta_request = get_eta_request(self.supplier.whatsapp_number)
        self.assertIsNotNone(eta_request)
        self.assertEqual(eta_request["customer_phone"], self.customer_phone)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_not_arrived_multi_supplier_asks_which_one_then_notifies_it(self, mock_send):
        carrot = make_product("גזר")
        supplier_b = make_supplier("ספק ב")
        order = self._make_order(OrderRequest.Status.APPROVED)
        order_b = make_order(self.user, supplier_b, batch=order.batch, status=OrderRequest.Status.APPROVED)
        OrderRequestProduct.objects.create(
            order_request=order_b, product=carrot, supplier=supplier_b,
            quantity=Decimal("5"), unit_price=Decimal("3.00"),
        )

        self._post_customer("לא הגיע")
        ask_msg = mock_send.call_args[0][1]
        self.assertIn(self.supplier.name, ask_msg)
        self.assertIn(supplier_b.name, ask_msg)
        mock_send.reset_mock()

        self._post_customer("1")

        supplier_a_calls = [c for c in mock_send.call_args_list if c[0][0] == self.supplier.whatsapp_number]
        supplier_b_calls = [c for c in mock_send.call_args_list if c[0][0] == supplier_b.whatsapp_number]
        self.assertEqual(len(supplier_a_calls), 1)
        self.assertEqual(len(supplier_b_calls), 0)

        from apps.orders.whatsapp.cache import get_eta_request
        self.assertIsNotNone(get_eta_request(self.supplier.whatsapp_number))
        self.assertIsNone(get_eta_request(supplier_b.whatsapp_number))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_supplier_eta_reply_updates_customer_and_clears_request(self, mock_send):
        self._make_order(OrderRequest.Status.APPROVED)
        self._post_customer("לא הגיע")
        mock_send.reset_mock()

        self._post_supplier("מצטער, יגיע עד 18:00")

        customer_calls = [c for c in mock_send.call_args_list if c[0][0] == self.customer_phone]
        self.assertEqual(len(customer_calls), 1)
        self.assertIn("18:00", customer_calls[0][0][1])
        self.assertIn(self.supplier.name, customer_calls[0][0][1])

        supplier_calls = [c for c in mock_send.call_args_list if c[0][0] == self.supplier.whatsapp_number]
        self.assertEqual(len(supplier_calls), 1)
        self.assertIn("18:00", supplier_calls[0][0][1])

        from apps.orders.whatsapp.cache import get_eta_request
        self.assertIsNone(get_eta_request(self.supplier.whatsapp_number))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_supplier_reply_without_a_time_is_asked_again(self, mock_send):
        self._make_order(OrderRequest.Status.APPROVED)
        self._post_customer("לא הגיע")
        mock_send.reset_mock()

        self._post_supplier("בודק ומחזיר תשובה")

        self.assertEqual(mock_send.call_args_list[0][0][0], self.supplier.whatsapp_number)
        self.assertNotIn("18:00", mock_send.call_args_list[0][0][1])
        from apps.orders.whatsapp.cache import get_eta_request
        self.assertIsNotNone(get_eta_request(self.supplier.whatsapp_number))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_supplier_shipping_notice_marks_approved_order_shipped(self, mock_send):
        """Supplier with no pending confirmation sends a shipping keyword →
        their most recent APPROVED order moves to SHIPPED, customer notified."""
        order = self._make_order(OrderRequest.Status.APPROVED)

        self._post_supplier("יצא למשלוח")

        order.refresh_from_db()
        self.assertEqual(order.status, OrderRequest.Status.SHIPPED)

        supplier_msg = mock_send.call_args_list[0][0][1]
        self.assertIn("יצאה למשלוח", supplier_msg)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_shipping_notice_with_eta_forwards_it_to_customer(self, mock_send):
        self._make_order(OrderRequest.Status.APPROVED)

        self._post_supplier("יצא למשלוח, יגיע עד 16:30")

        customer_calls = [c for c in mock_send.call_args_list if c[0][0] == self.customer_phone]
        self.assertEqual(len(customer_calls), 1)
        self.assertIn("16:30", customer_calls[0][0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.catalog.price_parser.update_prices_from_message")
    def test_supplier_shipping_keyword_without_approved_order_falls_back_to_price_update(
        self, mock_update, mock_send
    ):
        """A supplier with no APPROVED order at all must not crash or
        false-positive on a shipping keyword — it's ordinary free text to
        the price-update parser instead."""
        mock_update.return_value = {"updated": [], "removed": [], "skipped": []}

        self._post_supplier("נשלח")

        mock_update.assert_called_once()


# ─────────────────────── Fallback flow (missing/partial items) ─────────────

@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class FallbackFlowTests(TestCase):
    """
    A supplier reports a product missing/partial -> the system offers a
    fallback supplier -> customer answers כן/לא. `_handle_missing_items` is
    called directly (bypassing `_parse_supplier_reply`) since these tests
    are about what happens AFTER a shortage is detected, not about
    detecting one.
    """

    def setUp(self):
        cache.clear()
        self.tomato = make_product("עגבניה")
        self.supplier_a = make_supplier("ספק א", minimum_order=0)
        self.supplier_b = make_supplier("ספק ב", minimum_order=1000)
        SupplierProduct.objects.create(supplier=self.supplier_a, product=self.tomato, price_per_unit="5.00")
        SupplierProduct.objects.create(supplier=self.supplier_b, product=self.tomato, price_per_unit="6.00")
        self.customer_phone = "+972509999999"
        self.user = make_user_with_profile(phone=self.customer_phone)
        self.order = make_order(self.user, self.supplier_a, status=OrderRequest.Status.SENT, total_price="50.00"
        )
        self.orp = OrderRequestProduct.objects.create(
            order_request=self.order, product=self.tomato, supplier=self.supplier_a,
            quantity="10", unit_price="5.00",
        )

    def _report_missing(self):
        from apps.orders.whatsapp.fallback_flow import _handle_missing_items
        _handle_missing_items(
            self.supplier_a,
            [{"orp_id": self.orp.id, "product_name": "עגבניה", "quantity": "10", "unit": 'ק"ג'}],
            self.order.id,
        )

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_missing_item_offers_fallback_and_saves_state(self, mock_send):
        self._report_missing()

        self.assertIsNotNone(cache.get(f"whatsapp_fallback:{self.customer_phone}"))
        msg = mock_send.call_args[0][1]
        self.assertIn("ספק ב", msg)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_failed_transfer_keeps_state_alive_for_retry(self, mock_send):
        """
        Regression, found live: the fallback pending state used to be
        cleared unconditionally regardless of whether the transfer actually
        succeeded — a customer whose "כן" failed on minimum had no way back
        to retry or answer "לא" afterward, and the shortfall silently had
        no record anywhere while the order went on to auto-approve as if
        the reduced quantity had been the whole order all along.
        """
        from apps.orders.whatsapp.fallback_flow import _handle_fallback_approval
        self._report_missing()

        resp = _handle_fallback_approval(self.customer_phone, "כן")

        self.assertIsNotNone(resp)
        msg = mock_send.call_args[0][1]
        self.assertIn("לא בוצעה", msg)
        # The actual regression check — state must still be alive.
        self.assertIsNotNone(cache.get(f"whatsapp_fallback:{self.customer_phone}"))
        # Nothing moved — still assigned to the original supplier.
        self.orp.refresh_from_db()
        self.assertEqual(self.orp.supplier, self.supplier_a)

        # A second reply must still be recognized — not return None like live.
        resp2 = _handle_fallback_approval(self.customer_phone, "לא")
        self.assertIsNotNone(resp2)
        self.assertFalse(OrderRequestProduct.objects.filter(id=self.orp.id).exists())
        self.assertIsNone(cache.get(f"whatsapp_fallback:{self.customer_phone}"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_declining_notifies_original_supplier(self, mock_send):
        from apps.orders.whatsapp.fallback_flow import _handle_fallback_approval
        self._report_missing()
        mock_send.reset_mock()

        _handle_fallback_approval(self.customer_phone, "לא")

        supplier_calls = [c for c in mock_send.call_args_list if c[0][0] == self.supplier_a.whatsapp_number]
        self.assertEqual(len(supplier_calls), 1)
        self.assertIn("עגבניה", supplier_calls[0][0][1])

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_successful_transfer_still_clears_state(self, mock_send):
        """Regression guard the other way — a genuinely successful transfer must still clear state as before."""
        from apps.orders.whatsapp.fallback_flow import _handle_fallback_approval
        self.supplier_b.minimum_order = Decimal("10.00")
        self.supplier_b.save(update_fields=["minimum_order"])
        self._report_missing()

        _handle_fallback_approval(self.customer_phone, "כן")

        self.assertIsNone(cache.get(f"whatsapp_fallback:{self.customer_phone}"))
        self.orp.refresh_from_db()
        self.assertEqual(self.orp.supplier, self.supplier_b)
        # The item moved into supplier B's own order in the same checkout;
        # supplier A's order, now empty, is cancelled.
        self.assertNotEqual(self.orp.order_request, self.order)
        self.assertEqual(self.orp.order_request.supplier, self.supplier_b)
        self.assertEqual(self.orp.order_request.batch, self.order.batch)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderRequest.Status.CANCELLED)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_redirect_into_open_sibling_order_reregisters_all_its_items(self, mock_send):
        """
        Supplier B already has its own SENT order in this checkout. The
        redirected item joins that order, and B's pending state must cover
        the order's older unconfirmed items too — otherwise B's "אישור"
        would confirm only the new item and the order would never approve.
        """
        from apps.orders.whatsapp.fallback_flow import _handle_fallback_approval
        carrot = make_product("גזר")
        SupplierProduct.objects.create(supplier=self.supplier_b, product=carrot, price_per_unit="3.00")
        sibling = make_order(self.user, self.supplier_b, batch=self.order.batch, status=OrderRequest.Status.SENT)
        older_item = OrderRequestProduct.objects.create(
            order_request=sibling, product=carrot, supplier=self.supplier_b,
            quantity="400", unit_price="3.00",
        )
        self._report_missing()

        _handle_fallback_approval(self.customer_phone, "כן")

        self.orp.refresh_from_db()
        self.assertEqual(self.orp.order_request, sibling)
        pending = json.loads(cache.get(f"whatsapp_supplier_pending:{self.supplier_b.whatsapp_number}"))
        self.assertEqual(pending["order_request_id"], sibling.id)
        self.assertEqual({p["orp_id"] for p in pending["products"]}, {older_item.id, self.orp.id})



# ─────────────── One order per supplier: cross-order behaviour ───────────────

@override_settings(
    CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True,
    ADMIN_WHATSAPP_NUMBER="+972500000000",
)
class PerSupplierOrderTests(TestCase):
    """
    A checkout spanning two suppliers is two orders in one OrderBatch. Each
    supplier's order lives its own life — confirming, shipping, cancelling
    or being delivered never touches the sibling order.
    """

    def setUp(self):
        cache.clear()
        self.customer_phone = "+972508888888"
        self.user = make_user_with_profile(phone=self.customer_phone)
        self.tomato = make_product("עגבניה")
        self.carrot = make_product("גזר")
        self.supplier_a = make_supplier("ספק א")
        self.supplier_b = make_supplier("ספק ב")
        self.order_a = make_order(self.user, self.supplier_a, status=OrderRequest.Status.SENT, total_price="50.00")
        self.order_b = make_order(
            self.user, self.supplier_b, batch=self.order_a.batch,
            status=OrderRequest.Status.SENT, total_price="30.00",
        )
        self.item_a = OrderRequestProduct.objects.create(
            order_request=self.order_a, product=self.tomato, supplier=self.supplier_a,
            quantity=Decimal("10"), unit_price=Decimal("5.00"),
        )
        self.item_b = OrderRequestProduct.objects.create(
            order_request=self.order_b, product=self.carrot, supplier=self.supplier_b,
            quantity=Decimal("10"), unit_price=Decimal("3.00"),
        )

    def _post(self, phone, body):
        return self.client.post("/whatsapp/webhook/", {"From": f"whatsapp:{phone}", "Body": body})

    def _pending(self, order, items):
        save_supplier_pending_order(
            supplier_phone=order.supplier.whatsapp_number,
            order_request_id=order.id,
            products=[
                {"orp_id": i.id, "product_name": i.product.name, "quantity": str(i.quantity), "unit": "kg"}
                for i in items
            ],
        )

    def _before_cutoff(self):
        return patch(
            "apps.orders.whatsapp.user_flow.timezone.localtime",
            return_value=django_timezone.make_aware(datetime(2026, 1, 1, 10, 0)),
        )

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_supplier_confirmation_approves_only_its_own_order(self, mock_send):
        self._pending(self.order_a, [self.item_a])

        self._post(self.supplier_a.whatsapp_number, "אישור")

        self.order_a.refresh_from_db()
        self.order_b.refresh_from_db()
        self.assertEqual(self.order_a.status, OrderRequest.Status.APPROVED)
        self.assertEqual(self.order_b.status, OrderRequest.Status.SENT)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_both_suppliers_can_report_shipped_independently(self, mock_send):
        """The bug that started this redesign: the second supplier's
        "יצא למשלוח" used to find nothing once the shared status had moved on."""
        for order in (self.order_a, self.order_b):
            order.transition_to(OrderRequest.Status.APPROVED)

        self._post(self.supplier_a.whatsapp_number, "יצא למשלוח")
        self._post(self.supplier_b.whatsapp_number, "יצא למשלוח")

        self.order_a.refresh_from_db()
        self.order_b.refresh_from_db()
        self.assertEqual(self.order_a.status, OrderRequest.Status.SHIPPED)
        self.assertEqual(self.order_b.status, OrderRequest.Status.SHIPPED)
        customer_msgs = [c[0][1] for c in mock_send.call_args_list if c[0][0] == self.customer_phone]
        self.assertEqual(len(customer_msgs), 2)
        self.assertTrue(any(f"#{self.order_a.id}" in m and "ספק א" in m for m in customer_msgs))
        self.assertTrue(any(f"#{self.order_b.id}" in m and "ספק ב" in m for m in customer_msgs))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_cancelling_supplier_cancels_only_its_own_order(self, mock_send):
        """Used to cancel the WHOLE order when nothing could be rerouted —
        including every other supplier's items."""
        self._pending(self.order_a, [self.item_a])  # nobody else carries tomatoes

        self._post(self.supplier_a.whatsapp_number, "ביטול")

        self.order_a.refresh_from_db()
        self.order_b.refresh_from_db()
        self.assertEqual(self.order_a.status, OrderRequest.Status.CANCELLED)
        self.assertEqual(self.order_b.status, OrderRequest.Status.SENT)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_reroute_merges_into_an_open_sibling_order(self, mock_send):
        """If the replacement supplier already has an open order in this
        checkout, the items join it instead of opening a second order."""
        SupplierProduct.objects.create(supplier=self.supplier_b, product=self.tomato, price_per_unit="6.00")
        self._pending(self.order_a, [self.item_a])

        self._post(self.supplier_a.whatsapp_number, "ביטול")

        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.order_request, self.order_b)
        self.assertEqual(self.order_a.batch.orders.count(), 2)
        self.order_b.refresh_from_db()
        self.assertEqual(self.order_b.total_price, Decimal("90.00"))  # 30 + 10*6
        b_msg = [c[0][1] for c in mock_send.call_args_list if c[0][0] == self.supplier_b.whatsapp_number][0]
        self.assertIn(f"להוסיף להזמנה #{self.order_b.id}", b_msg)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_delivery_menu_lists_orders_and_delivers_only_the_one_picked(self, mock_send):
        for order in (self.order_a, self.order_b):
            order.transition_to(OrderRequest.Status.APPROVED)

        self._post(self.customer_phone, "קיבלתי")
        menu = mock_send.call_args[0][1]
        self.assertIn(f"#{self.order_a.id}", menu)
        self.assertIn(f"#{self.order_b.id}", menu)

        self._post(self.customer_phone, "2")

        self.order_a.refresh_from_db()
        self.order_b.refresh_from_db()
        self.assertEqual(self.order_a.status, OrderRequest.Status.APPROVED)
        self.assertEqual(self.order_b.status, OrderRequest.Status.DELIVERED)

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_modification_intent")
    def test_adding_a_product_from_a_new_supplier_opens_a_new_order_in_the_checkout(self, mock_parse, mock_send):
        supplier_c = make_supplier("ספק ג")
        potato = make_product("תפוח אדמה אדום")
        SupplierProduct.objects.create(supplier=supplier_c, product=potato, price_per_unit="2.00")
        mock_parse.return_value = {"intent": "add", "items": [{"product_name": potato.name, "quantity": Decimal("20")}]}

        with self._before_cutoff():
            self._post(self.customer_phone, "תוסיף 20 תפוח אדמה אדום")

        new_order = OrderRequest.objects.get(batch=self.order_a.batch, supplier=supplier_c)
        self.assertEqual(new_order.status, OrderRequest.Status.SENT)
        self.assertEqual(new_order.products.get().quantity, Decimal("20"))
        c_msg = [c[0][1] for c in mock_send.call_args_list if c[0][0] == supplier_c.whatsapp_number][0]
        self.assertIn(f"הזמנה #{new_order.id}", c_msg)
        self.assertIsNotNone(cache.get(f"whatsapp_supplier_pending:{supplier_c.whatsapp_number}"))

    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    @patch("apps.orders.order_parser.parse_modification_intent")
    def test_updating_an_item_the_supplier_already_approved_is_refused(self, mock_parse, mock_send):
        self.order_a.transition_to(OrderRequest.Status.APPROVED)
        mock_parse.return_value = {"intent": "update", "items": [{"product_name": "עגבניה", "quantity": Decimal("30")}]}

        with self._before_cutoff():
            self._post(self.customer_phone, "תעדכן עגבניות ל-30")

        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.quantity, Decimal("10"))
        self.assertIn("כבר אישר", mock_send.call_args[0][1])


@override_settings(CACHES=LOCMEM_CACHE, DEBUG=True, TWILIO_SKIP_SIGNATURE_VALIDATION=True)
class CheckoutConfirmationMessageTests(TestCase):
    """Confirming a scenario over WhatsApp tells the customer each order
    number and its supplier — later messages ("הזמנה #41 יצאה למשלוח")
    have to map back to something they were told."""

    @patch("apps.orders.tasks.send_supplier_order_notification_task")
    @patch("apps.orders.whatsapp.validators.send_whatsapp_message")
    def test_confirmation_lists_one_order_per_supplier(self, mock_send, mock_task):
        cache.clear()
        user = make_user_with_profile(phone="+972504444444")
        tomato = make_product("עגבניה")
        carrot = make_product("גזר")
        a = make_supplier("ספק א")
        b = make_supplier("ספק ב")
        SupplierProduct.objects.create(supplier=a, product=tomato, price_per_unit="5.00")
        SupplierProduct.objects.create(supplier=b, product=carrot, price_per_unit="3.00")
        scenario = _scenario("62.00")
        save_pending_order(
            "+972504444444", scenario, scenario,
            products=[
                {"product_id": tomato.id, "quantity": "10"},
                {"product_id": carrot.id, "quantity": "4"},
            ],
            user_id=user.id, region=Region.CENTER,
        )

        self.client.post("/whatsapp/webhook/", {"From": "whatsapp:+972504444444", "Body": "אישור"})

        orders = list(OrderRequest.objects.filter(user=user).order_by("id"))
        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[0].batch, orders[1].batch)
        msg = mock_send.call_args[0][1]
        for order in orders:
            self.assertIn(f"הזמנה #{order.id} — {order.supplier.name}", msg)
