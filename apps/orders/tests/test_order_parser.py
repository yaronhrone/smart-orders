import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase


def _openai_response(items: list) -> MagicMock:
    """Build a mock OpenAI response that returns the given items list."""
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = json.dumps({"items": items})
    return mock_resp


class ParseCustomerOrderTests(TestCase):

    @patch("apps.orders.order_parser._get_client")
    def test_returns_parsed_items(self, mock_get_client):
        """Happy path: valid items are returned with correct names and quantities."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.chat.completions.create.return_value = _openai_response([
            {"product_name": "עגבניה", "quantity": "5.0"},
            {"product_name": "גזר", "quantity": "10.0"},
        ])

        from apps.orders.order_parser import parse_customer_order
        result = parse_customer_order("5 עגבניות ו-10 גזר", ["עגבניה", "גזר"])

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["product_name"], "עגבניה")
        self.assertEqual(result[0]["quantity"], Decimal("5.0"))
        self.assertEqual(result[1]["product_name"], "גזר")
        self.assertEqual(result[1]["quantity"], Decimal("10.0"))

    @patch("apps.orders.order_parser._get_client")
    def test_raises_no_items_when_ai_returns_empty_list(self, mock_get_client):
        """Empty AI response raises ValueError('no_items')."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.chat.completions.create.return_value = _openai_response([])

        from apps.orders.order_parser import parse_customer_order
        with self.assertRaises(ValueError) as ctx:
            parse_customer_order("שלום מה שלומך", [])
        self.assertIn("no_items", str(ctx.exception))

    @patch("apps.orders.order_parser._get_client")
    def test_raises_on_openai_exception(self, mock_get_client):
        """OpenAI error raises ValueError with 'AI parsing failed' prefix."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.chat.completions.create.side_effect = Exception("network error")

        from apps.orders.order_parser import parse_customer_order
        with self.assertRaises(ValueError) as ctx:
            parse_customer_order("5 עגבניות", ["עגבניה"])
        self.assertIn("AI parsing failed", str(ctx.exception))

    @patch("apps.orders.order_parser._get_client")
    def test_filters_zero_and_negative_quantities(self, mock_get_client):
        """Items with quantity <= 0 are filtered out."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.chat.completions.create.return_value = _openai_response([
            {"product_name": "עגבניה", "quantity": "0"},
            {"product_name": "גזר", "quantity": "-1"},
            {"product_name": "מלפפון", "quantity": "5"},
        ])

        from apps.orders.order_parser import parse_customer_order
        result = parse_customer_order("...", ["עגבניה", "גזר", "מלפפון"])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["product_name"], "מלפפון")

    @patch("apps.orders.order_parser._get_client")
    def test_filters_invalid_quantity_strings(self, mock_get_client):
        """Items with non-numeric quantity strings are filtered out."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.chat.completions.create.return_value = _openai_response([
            {"product_name": "עגבניה", "quantity": "abc"},
            {"product_name": "גזר", "quantity": "10"},
        ])

        from apps.orders.order_parser import parse_customer_order
        result = parse_customer_order("...", ["עגבניה", "גזר"])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["product_name"], "גזר")
        self.assertEqual(result[0]["quantity"], Decimal("10"))

    @patch("apps.orders.order_parser._get_client")
    def test_filters_items_with_empty_name(self, mock_get_client):
        """Items with empty product_name are skipped."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.chat.completions.create.return_value = _openai_response([
            {"product_name": "", "quantity": "5"},
            {"product_name": "גזר", "quantity": "10"},
        ])

        from apps.orders.order_parser import parse_customer_order
        result = parse_customer_order("...", ["גזר"])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["product_name"], "גזר")

    @patch("apps.orders.order_parser._get_client")
    def test_handles_top_level_dict_without_items_key(self, mock_get_client):
        """If AI returns a top-level dict without 'items', fall back to first value."""
        client = MagicMock()
        mock_get_client.return_value = client
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps({
            "results": [{"product_name": "עגבניה", "quantity": "3"}]
        })
        client.chat.completions.create.return_value = mock_resp

        from apps.orders.order_parser import parse_customer_order
        result = parse_customer_order("3 עגבניות", ["עגבניה"])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["quantity"], Decimal("3"))
