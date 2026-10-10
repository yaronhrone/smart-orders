import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase


def _openai_response(items: list, intent: str | None = None) -> MagicMock:
    """Build a mock OpenAI response that returns the given items list."""
    payload = {"items": items}
    if intent is not None:
        payload["intent"] = intent
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = json.dumps(payload)
    return mock_resp


class ParseModificationActionTests(TestCase):
    """Each product in a message carries its own action: add, update (to a total) or reduce."""

    def _parse(self, mock_get_client, payload, names=("עגבניה", "מלפפון")):
        client = MagicMock()
        mock_get_client.return_value = client
        response = MagicMock()
        response.choices[0].message.content = json.dumps(payload)
        client.chat.completions.create.return_value = response
        from apps.orders.order_parser import parse_modification_intent
        return parse_modification_intent("x", list(names)), client

    @patch("apps.orders.order_parser._get_client")
    def test_every_item_keeps_its_own_action(self, mock_get_client):
        result, _ = self._parse(mock_get_client, {"intent": "add", "items": [
            {"product_name": "עגבניה", "quantity": "20", "action": "add"},
            {"product_name": "מלפפון", "quantity": "15", "action": "update"},
            {"product_name": "עגבניה", "quantity": "3", "action": "reduce"},
        ]})

        self.assertEqual([i["intent"] for i in result["items"]], ["add", "update", "reduce"])
        self.assertEqual(result["intent"], "add")  # the first item's action
        self.assertEqual(result["items"][1]["quantity"], Decimal("15"))

    @patch("apps.orders.order_parser._get_client")
    def test_missing_or_unknown_action_falls_back_to_the_message_intent_then_add(self, mock_get_client):
        result, _ = self._parse(mock_get_client, {"intent": "reduce", "items": [
            {"product_name": "עגבניה", "quantity": "2"},
            {"product_name": "מלפפון", "quantity": "2", "action": "bogus"},
        ]})
        self.assertEqual([i["intent"] for i in result["items"]], ["reduce", "reduce"])

        result, _ = self._parse(mock_get_client, {"items": [{"product_name": "עגבניה", "quantity": "2"}]})
        self.assertEqual(result["items"][0]["intent"], "add")

    @patch("apps.orders.order_parser._get_client")
    def test_a_message_that_is_not_a_modification_has_no_intent(self, mock_get_client):
        result, _ = self._parse(mock_get_client, {"intent": "none", "items": []})
        self.assertEqual(result, {"intent": "none", "items": []})

        result, _ = self._parse(mock_get_client, {"intent": "gibberish", "items": []})
        self.assertEqual(result["intent"], "none")

    @patch("apps.orders.order_parser._get_client")
    def test_bad_quantities_are_dropped(self, mock_get_client):
        result, _ = self._parse(mock_get_client, {"intent": "add", "items": [
            {"product_name": "עגבניה", "quantity": "הרבה", "action": "add"},
            {"product_name": "מלפפון", "quantity": "0", "action": "add"},
            {"product_name": "עגבניה", "quantity": "4", "action": "add"},
        ]})
        self.assertEqual([(i["product_name"], i["quantity"]) for i in result["items"]], [("עגבניה", Decimal("4"))])

    @patch("apps.orders.order_parser._get_client")
    def test_prompt_defines_what_the_quantity_means_for_each_action(self, mock_get_client):
        _, client = self._parse(mock_get_client, {"intent": "none", "items": []})

        prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        for text in ("'add'", "'update'", "'reduce'", "NEW TOTAL", "amount to REMOVE", "ALWAYS 'add'", "תוריד"):
            self.assertIn(text, prompt)


class ParseModificationIntentTests(TestCase):

    @patch("apps.orders.order_parser._get_client")
    def test_prompt_warns_about_ambiguous_family_roots(self, mock_get_client):
        """
        Regression: the AI used to be told to "always return the exact known
        product name" with no notion of family roots like "פלפל" (which isn't
        itself a product — only "פלפל אדום"/"פלפל ירוק"/... are), so it just
        picked a variant on its own instead of the caller ever getting a
        chance to ask which one was meant. The prompt must spell the family
        out and tell the model to return the bare root instead of guessing.
        """
        client = MagicMock()
        mock_get_client.return_value = client
        client.chat.completions.create.return_value = _openai_response(
            [{"product_name": "פלפל", "quantity": "2"}], intent="add",
        )

        from apps.orders.order_parser import parse_modification_intent
        parse_modification_intent(
            "תוסיף גם 2 קילו פלפל",
            ["פלפל אדום", "פלפל ירוק", "פלפל צהוב", "עגבנייה"],
        )

        sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("פלפל", sent_prompt)
        self.assertIn("do NOT guess", sent_prompt)
        self.assertIn("פלפל אדום", sent_prompt)
        self.assertIn("פלפל ירוק", sent_prompt)


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
