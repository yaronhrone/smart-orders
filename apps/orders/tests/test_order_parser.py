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
        # (a message about changing a total, so that the dictionary leaves it to the AI)
        parse_modification_intent(
            "תעדכן את הפלפל ל-2 קילו",
            ["פלפל אדום", "פלפל ירוק", "פלפל צהוב", "עגבנייה"],
        )

        sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("פלפל", sent_prompt)
        self.assertIn("do NOT guess", sent_prompt)
        self.assertIn("פלפל אדום", sent_prompt)
        self.assertIn("פלפל ירוק", sent_prompt)


class AmbiguousWithLeftoverTests(TestCase):
    """
    A message with an ambiguous product ("חסה") AND a segment the dictionary
    can't read used to lose the second one without a word: the customer was
    asked about the lettuce and the final offer had no trace of the rest.
    """

    MESSAGE = "30 קילו סלק מיוחד\n30 יחידות חסה\n40 קילו מלפפון"
    NAMES = ["חסה סלנובה", "חסה קיסר", "מלפפון", "סלק אדום"]

    def _parse(self, mock_get_client, ai_items):
        client = MagicMock()
        mock_get_client.return_value = client
        client.chat.completions.create.return_value = _openai_response(ai_items)
        from apps.orders.order_parser import AmbiguousProductError, parse_customer_order
        with self.assertRaises(AmbiguousProductError) as ctx:
            parse_customer_order(self.MESSAGE, self.NAMES)
        return ctx.exception, client

    @patch("apps.orders.order_parser._get_client")
    def test_the_ai_reads_what_the_dictionary_could_not_and_it_rides_along(self, mock_get_client):
        exc, client = self._parse(mock_get_client, [{"product_name": "סלק אדום", "quantity": "30"}])

        self.assertEqual([a["query"] for a in exc.ambiguous], ["חסה"])
        self.assertEqual(
            sorted((r["product_name"], r["quantity"]) for r in exc.resolved),
            [("מלפפון", Decimal("40")), ("סלק אדום", Decimal("30"))],
        )
        sent = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("סלק מיוחד", sent)
        self.assertNotIn("חסה", sent.split("Message:")[1])  # only the leftover goes to the AI

    @patch("apps.orders.order_parser._get_client")
    def test_when_the_ai_fails_the_customer_is_told_instead_of_silently_losing_items(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        client.chat.completions.create.side_effect = RuntimeError("network")
        from apps.orders.order_parser import parse_customer_order

        with self.assertRaises(ValueError):
            parse_customer_order(self.MESSAGE, self.NAMES)

    @patch("apps.orders.order_parser._get_client")
    def test_nothing_left_over_means_no_ai_call(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        from apps.orders.order_parser import AmbiguousProductError, parse_customer_order

        with self.assertRaises(AmbiguousProductError) as ctx:
            parse_customer_order("30 יחידות חסה\n40 קילו מלפפון", self.NAMES)

        self.assertFalse(client.chat.completions.create.called)
        self.assertEqual([r["product_name"] for r in ctx.exception.resolved], ["מלפפון"])


class ParseCustomerOrderTests(TestCase):

    @patch("apps.orders.order_parser._get_client")
    def test_returns_parsed_items(self, mock_get_client):
        """Happy path: valid items are returned with correct names and quantities."""
        client = MagicMock()
        mock_get_client.return_value = client
        # "10 גזר" is read by the dictionary; only "5 עגבניות" is left for the AI.
        client.chat.completions.create.return_value = _openai_response([
            {"product_name": "עגבניה", "quantity": "5.0"},
        ])

        from apps.orders.order_parser import parse_customer_order
        result = parse_customer_order("5 עגבניות ו-10 גזר", ["עגבניה", "גזר"])

        quantities = {item["product_name"]: item["quantity"] for item in result}
        self.assertEqual(len(result), 2)
        self.assertEqual(quantities, {"עגבניה": Decimal("5.0"), "גזר": Decimal("10.0")})
        sent = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertNotIn("10 גזר", sent)

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


class DictionaryFirstModificationTests(TestCase):
    """A change to an open order is read by the dictionary when it can be; the AI only reads the rest."""

    NAMES = ["עגבנייה", "גזר", "בצל סגול", "בצל ירוק", "חסה קיסר", "חסה רומית"]

    def _parse(self, message):
        from apps.orders.order_parser import parse_modification_intent
        with patch("apps.orders.order_parser._get_client") as get_client:
            result = parse_modification_intent(message, self.NAMES)
        return result, get_client.return_value.chat.completions.create

    def test_a_plain_list_of_known_products_never_reaches_the_ai(self):
        result, ai_call = self._parse("20 קילו עגבנייה, 40 קילו גזר, 20 קילו בצל סגול ו-5 יחידות חסה קיסר")

        ai_call.assert_not_called()
        self.assertEqual(result["intent"], "add")
        self.assertEqual(
            [(i["product_name"], i["quantity"], i["intent"]) for i in result["items"]],
            [("עגבנייה", Decimal("20"), "add"), ("גזר", Decimal("40"), "add"),
             ("בצל סגול", Decimal("20"), "add"), ("חסה קיסר", Decimal("5"), "add")],
        )

    def test_an_add_word_and_an_alias_are_still_the_dictionary(self):
        result, ai_call = self._parse("תוסיף 5 קילו גזרים")

        ai_call.assert_not_called()
        self.assertEqual([(i["product_name"], i["quantity"]) for i in result["items"]], [("גזר", Decimal("5"))])

    def test_a_family_root_comes_back_as_the_root_so_the_caller_asks(self):
        result, ai_call = self._parse("עוד 5 חסה")

        ai_call.assert_not_called()
        self.assertEqual(result["items"][0]["product_name"], "חסה")

    def test_words_about_changing_a_total_or_reducing_go_to_the_ai(self):
        for message in ("תעדכן עגבניות ל-50", "תוריד 10 קילו גזר", "במקום 30 גזר תביא 50"):
            with self.subTest(message=message):
                _result, ai_call = self._parse(message)
                ai_call.assert_called_once()

    def test_an_unknown_name_goes_to_the_ai(self):
        _result, ai_call = self._parse("תוסיף 3 קילו ברוקולי")

        ai_call.assert_called_once()


class AskInsteadOfGuessTests(TestCase):
    """
    "חסה" or "תפוחי אדמה" said without a type: the order must ask which one, whatever the AI did
    with it. Spoken orders hit this often: numbers in words ("עשר יחידות") and plurals both get past
    the dictionary, so the AI reads them, and the AI used to hand back a bare root (ending in
    "not recognized") or quietly pick a variant.
    """

    NAMES = ["חסה אייסברג", "חסה קיסר", "תפוח אדמה אדום", "תפוח אדמה לבן", "תפוח עץ ירוק", "מלפפון"]

    def _parse(self, mock_get_client, message, ai_items):
        client = MagicMock()
        mock_get_client.return_value = client
        client.chat.completions.create.return_value = _openai_response(ai_items)
        from apps.orders.order_parser import AmbiguousProductError, parse_customer_order
        try:
            return parse_customer_order(message, self.NAMES), client
        except AmbiguousProductError as exc:
            return exc, client

    @patch("apps.orders.order_parser._get_client")
    def test_a_bare_root_from_the_ai_becomes_a_question(self, mock_get_client):
        exc, _ = self._parse(
            mock_get_client, "עשר יחידות חסה ועשרים קילו מלפפון",
            [{"product_name": "חסה", "quantity": "10"}, {"product_name": "מלפפון", "quantity": "20"}],
        )
        self.assertEqual([(a["query"], a["quantity"]) for a in exc.ambiguous], [("חסה", Decimal("10"))])
        self.assertEqual(sorted(exc.ambiguous[0]["candidates"]), ["חסה אייסברג", "חסה קיסר"])
        self.assertEqual([(r["product_name"], r["quantity"]) for r in exc.resolved], [("מלפפון", Decimal("20"))])

    @patch("apps.orders.order_parser.aliases_of", return_value=[])  # no alias says "תפוחי אדמה" means one of them
    @patch("apps.orders.order_parser._get_client")
    def test_a_variant_the_customer_never_named_is_asked_about_not_picked(self, mock_get_client, _aliases):
        exc, _ = self._parse(
            mock_get_client, "שלושים קילו תפוחי אדמה ועשרים קילו תפוח עץ ירוק",
            [{"product_name": "תפוח אדמה לבן", "quantity": "30"}, {"product_name": "תפוח עץ ירוק", "quantity": "20"}],
        )
        self.assertEqual([(a["query"], a["quantity"]) for a in exc.ambiguous], [("תפוח אדמה", Decimal("30"))])
        self.assertEqual(exc.ambiguous[0]["candidates"], ["תפוח אדמה אדום", "תפוח אדמה לבן"])
        self.assertEqual([r["product_name"] for r in exc.resolved], ["תפוח עץ ירוק"])

    @patch("apps.orders.order_parser._get_client")
    def test_a_variant_the_customer_did_name_is_kept(self, mock_get_client):
        items, _ = self._parse(
            mock_get_client, "שלושים קילו תפוחי אדמה לבן ועשר יחידות קיסר",
            [{"product_name": "תפוח אדמה לבן", "quantity": "30"}, {"product_name": "חסה קיסר", "quantity": "10"}],
        )
        self.assertEqual(
            [(i["product_name"], i["quantity"]) for i in items],
            [("תפוח אדמה לבן", Decimal("30")), ("חסה קיסר", Decimal("10"))],
        )

    @patch("apps.orders.order_parser._get_client")
    def test_an_alias_chosen_for_the_variant_counts_as_naming_it(self, mock_get_client):
        """The alias list says "תפוחי אדמה" is white potato: spoken the same way, it is not asked about."""
        from apps.catalog.models import Product, ProductAlias, Unit
        white = Product.objects.create(name="תפוח אדמה לבן", unit=Unit.KG)
        Product.objects.create(name="תפוח אדמה אדום", unit=Unit.KG)
        ProductAlias.objects.create(product=white, alias="תפוחי אדמה")
        items, _ = self._parse(
            mock_get_client, "שלושים קילו תפוחי אדמה", [{"product_name": "תפוח אדמה לבן", "quantity": "30"}],
        )
        self.assertEqual([(i["product_name"], i["quantity"]) for i in items], [("תפוח אדמה לבן", Decimal("30"))])

    @patch("apps.orders.order_parser._get_client")
    def test_the_prompt_tells_the_ai_to_return_the_root_and_not_guess(self, mock_get_client):
        _, client = self._parse(mock_get_client, "שלושים קילו תפוחי אדמה", [{"product_name": "תפוח אדמה", "quantity": "30"}])
        prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("do NOT guess", prompt.replace("Do NOT guess", "do NOT guess"))
        self.assertIn("תפוח אדמה אדום", prompt)
        self.assertIn("חסה קיסר", prompt)

    @patch("apps.orders.order_parser._get_client")
    def test_a_plain_product_is_unaffected(self, mock_get_client):
        items, _ = self._parse(
            mock_get_client, "ארבעים קילו מלפפון", [{"product_name": "מלפפון", "quantity": "40"}],
        )
        self.assertEqual([(i["product_name"], i["quantity"]) for i in items], [("מלפפון", Decimal("40"))])

    @patch("apps.orders.order_parser._get_client")
    def test_the_question_reaches_the_customer_with_the_options(self, mock_get_client):
        """End to end through the WhatsApp handler: the clarification text lists the types."""
        from apps.orders.whatsapp import user_flow
        exc, _ = self._parse(
            mock_get_client, "עשר יחידות חסה", [{"product_name": "חסה", "quantity": "10"}],
        )
        with patch("apps.orders.whatsapp.validators.send_whatsapp_message") as send,                 patch("apps.orders.whatsapp.user_flow.save_pending_clarification") as save:
            user_flow._handle_ambiguous_products("+972501234567", exc.ambiguous, exc.resolved)
        text = send.call_args[0][1]
        self.assertIn("חסה", text)
        self.assertIn("קיסר", text)
        self.assertIn("אייסברג", text)
        save.assert_called_once()

