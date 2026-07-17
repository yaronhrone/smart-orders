import json
from datetime import date

from django.test import TestCase, override_settings

from apps.catalog.models import MarketPrice, Product

URL = "/api/catalog/market-prices/push/"
SECRET = "test-market-secret"


def _post(client, payload, key=SECRET):
    return client.post(
        URL,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Api-Key {key}",
    )


class MarketPricesPushAuthTests(TestCase):
    """Authentication edge-cases."""

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_missing_header_returns_403(self):
        resp = self.client.post(
            URL,
            data=json.dumps({"prices": []}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_wrong_key_returns_403(self):
        resp = _post(self.client, {"prices": []}, key="wrong-key")
        self.assertEqual(resp.status_code, 403)

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_wrong_scheme_returns_403(self):
        """Bearer <secret> should not be accepted — must use Api-Key scheme."""
        resp = self.client.post(
            URL,
            data=json.dumps({"prices": []}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )
        self.assertEqual(resp.status_code, 403)

    @override_settings(MARKET_AGENT_SECRET="")
    def test_empty_secret_in_settings_denies_all(self):
        """If MARKET_AGENT_SECRET is not configured the endpoint must be fully closed."""
        resp = _post(self.client, {"prices": []})
        self.assertEqual(resp.status_code, 403)


class MarketPricesPushCoreTests(TestCase):
    """Core upsert logic."""

    def setUp(self):
        # All products must exist in the catalog before prices can be pushed.
        for name in ["עגבניה", "מלפפון", "גזר", "פלפל", "בצל", "שום", "תפוח אדמה",
                     "חסה", "תות שדה", "אוכמניות", "ענבים"]:
            Product.objects.get_or_create(name=name)

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_valid_push_returns_200(self):
        payload = {
            "prices": [
                {
                    "product_name": "עגבניה",
                    "price_grade_a": "3.50",
                    "price_premium": "4.20",
                    "market_date": "2026-05-28",
                }
            ]
        }
        resp = _post(self.client, payload)
        self.assertEqual(resp.status_code, 200)

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_unknown_product_is_skipped(self):
        """A product name not in the catalog must not create a new Product row."""
        payload = {
            "prices": [
                {"product_name": "כרוב", "price_grade_a": "2.00", "market_date": "2026-05-28"}
            ]
        }
        resp = _post(self.client, payload)
        data = resp.json()
        self.assertFalse(Product.objects.filter(name="כרוב").exists())
        self.assertEqual(len(data["skipped"]), 1)
        self.assertEqual(data["skipped"][0]["product_name"], "כרוב")

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_market_price_record_created(self):
        payload = {
            "prices": [
                {
                    "product_name": "מלפפון",
                    "price_grade_a": "1.80",
                    "price_premium": "2.10",
                    "market_date": "2026-05-28",
                }
            ]
        }
        resp = _post(self.client, payload)
        data = resp.json()
        self.assertEqual(data["created"], 1)
        self.assertEqual(data["updated"], 0)
        self.assertEqual(data["total_received"], 1)

        mp = MarketPrice.objects.get(product__name="מלפפון")
        self.assertEqual(str(mp.price_per_unit), "1.80")
        self.assertEqual(str(mp.price_grade_a), "1.80")
        self.assertEqual(str(mp.price_premium), "2.10")
        self.assertEqual(mp.market_date, date(2026, 5, 28))
        self.assertEqual(mp.source, "market-agent")

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_price_per_unit_prefers_grade_a_over_premium(self):
        payload = {
            "prices": [
                {"product_name": "גזר", "price_grade_a": "3.00", "price_premium": "4.00"}
            ]
        }
        _post(self.client, payload)
        mp = MarketPrice.objects.get(product__name="גזר")
        self.assertEqual(str(mp.price_per_unit), "3.00")

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_price_per_unit_falls_back_to_premium_when_grade_a_missing(self):
        payload = {
            "prices": [{"product_name": "פלפל", "price_premium": "5.50"}]
        }
        _post(self.client, payload)
        mp = MarketPrice.objects.get(product__name="פלפל")
        self.assertEqual(str(mp.price_per_unit), "5.50")

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_second_push_updates_existing_record(self):
        product = Product.objects.get(name="בצל")
        MarketPrice.objects.create(
            product=product,
            price_per_unit="2.00",
            price_grade_a="2.00",
            market_date=date(2026, 5, 27),
            source="market-agent",
        )

        payload = {
            "prices": [
                {"product_name": "בצל", "price_grade_a": "2.50", "market_date": "2026-05-28"}
            ]
        }
        resp = _post(self.client, payload)
        data = resp.json()
        self.assertEqual(data["created"], 0)
        self.assertEqual(data["updated"], 1)

        mp = MarketPrice.objects.get(product__name="בצל")
        self.assertEqual(str(mp.price_per_unit), "2.50")
        self.assertEqual(mp.market_date, date(2026, 5, 28))

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_item_without_any_price_is_skipped(self):
        payload = {
            "prices": [
                {"product_name": "שום", "market_date": "2026-05-28"},
                {"product_name": "תפוח אדמה", "price_grade_a": "1.20"},
            ]
        }
        resp = _post(self.client, payload)
        data = resp.json()
        self.assertEqual(data["created"], 1)   # only תפוח אדמה
        self.assertEqual(len(data["skipped"]), 1)
        self.assertEqual(data["skipped"][0]["product_name"], "שום")
        self.assertFalse(MarketPrice.objects.filter(product__name="שום").exists())

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_empty_prices_list_returns_zeros(self):
        resp = _post(self.client, {"prices": []})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["created"], 0)
        self.assertEqual(data["updated"], 0)

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_market_date_defaults_to_today_when_omitted(self):
        payload = {"prices": [{"product_name": "חסה", "price_grade_a": "0.80"}]}
        _post(self.client, payload)
        mp = MarketPrice.objects.get(product__name="חסה")
        self.assertEqual(mp.market_date, date.today())

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_bulk_push_multiple_products(self):
        payload = {
            "prices": [
                {"product_name": "תות שדה", "price_grade_a": "12.00"},
                {"product_name": "אוכמניות", "price_premium": "25.00"},
                {"product_name": "ענבים", "price_grade_a": "8.00", "price_premium": "10.00"},
            ]
        }
        resp = _post(self.client, payload)
        data = resp.json()
        self.assertEqual(data["created"], 3)
        self.assertEqual(data["total_received"], 3)

    @override_settings(MARKET_AGENT_SECRET=SECRET)
    def test_invalid_payload_returns_400(self):
        """Payload missing the top-level 'prices' key should fail validation."""
        resp = _post(self.client, {"items": [{"product_name": "x", "price_grade_a": "1.0"}]})
        self.assertEqual(resp.status_code, 400)
