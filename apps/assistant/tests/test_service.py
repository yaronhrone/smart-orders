import json
from itertools import repeat
from unittest.mock import patch

from apps.assistant.models import AssistantCall
from apps.assistant.prompt import ERROR_MESSAGE, FALLBACK_MESSAGE, OFF_TOPIC_MESSAGE
from apps.assistant.service import MAX_TOOL_ROUNDS, ask
from apps.assistant.tools import build_context
from apps.catalog.models import Product, Unit

from .fakes import fake_client, reply, tool_call
from .test_tools import ToolTestBase, add_order, il

Outcome = AssistantCall.Outcome


@patch("apps.assistant.service._get_client")
class AskTests(ToolTestBase):
    def ask(self, user, message="כמה הוצאתי?", history=None):
        return ask(build_context(user), message, history or [])

    def test_plain_answer_without_tools(self, get_client):
        get_client.return_value = fake_client(reply("שלום!", prompt=100, completion=7))

        result = self.ask(self.alice)

        self.assertEqual(result.answer, "שלום!")
        self.assertEqual(result.outcome, Outcome.ANSWERED)
        self.assertEqual((result.tool_rounds, result.tools_called), (0, []))
        self.assertEqual((result.prompt_tokens, result.completion_tokens), (100, 7))

    def test_tool_result_reaches_the_model_and_the_final_answer_comes_back(self, get_client):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))
        client = fake_client(
            reply(tool_calls=[tool_call("c1", "query_spending", {"date_from": "2026-09-01", "date_to": "2026-09-30"})]),
            reply("הוצאת 50.00 ₪."),
        )
        get_client.return_value = client

        result = self.ask(self.alice)

        self.assertEqual(result.answer, "הוצאת 50.00 ₪.")
        self.assertEqual(result.tool_rounds, 1)
        self.assertEqual(result.tools_called, ["query_spending"])
        first, second = (call.kwargs for call in client.chat.completions.create.call_args_list)
        tool_message = second["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["tool_call_id"], "c1")
        self.assertEqual(json.loads(tool_message["content"])["total_spend"], "50.00")
        self.assertEqual(second["messages"][-2]["tool_calls"][0]["function"]["name"], "query_spending")
        self.assertEqual(first["model"], "gpt-4o-mini")
        self.assertEqual(first["temperature"], 0)

    def test_customer_and_admin_get_different_tool_sets_and_prompts(self, get_client):
        get_client.return_value = fake_client(reply("ok"), reply("ok"))

        self.ask(self.alice)
        self.ask(self.admin)

        customer_call, admin_call = (c.kwargs for c in get_client.return_value.chat.completions.create.call_args_list)
        names = lambda call: [t["function"]["name"] for t in call["tools"]]  # noqa: E731
        self.assertNotIn("list_customers", names(customer_call))
        self.assertIn("list_customers", names(admin_call))
        self.assertIn("המשתמש הוא לקוח", customer_call["messages"][0]["content"])
        self.assertIn("מנהל המערכת", admin_call["messages"][0]["content"])

    def test_history_and_todays_date_are_sent(self, get_client):
        get_client.return_value = fake_client(reply("ok"))
        history = [{"role": "user", "content": "שלום"}, {"role": "assistant", "content": "היי"}]

        self.ask(self.alice, "ומה עם חסה?", history)

        messages = get_client.return_value.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(messages[1:3], history)
        self.assertEqual(messages[-1], {"role": "user", "content": "ומה עם חסה?"})
        self.assertIn(build_context(self.alice).today.isoformat(), messages[0]["content"])

    def test_prompt_anchors_date_ranges_to_the_current_year(self, get_client):
        get_client.return_value = fake_client(reply("ok"))

        self.ask(self.alice)

        prompt = get_client.return_value.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        year = build_context(self.alice).today.year
        self.assertIn(f"date_from={year}-07-10", prompt)
        self.assertIn(f"date_to={year}-09-10", prompt)

    def test_off_topic_answer_is_normalised_to_the_fixed_message(self, get_client):
        get_client.return_value = fake_client(reply(f"בטח! {OFF_TOPIC_MESSAGE} אבל אשמח לעזור בשיר."))

        result = self.ask(self.alice, "תכתוב לי שיר")

        self.assertEqual(result.answer, OFF_TOPIC_MESSAGE)
        self.assertEqual(result.outcome, Outcome.OFF_TOPIC)

    def test_gives_up_after_the_round_limit(self, get_client):
        endless = repeat(reply(tool_calls=[tool_call("c", "list_products", {})]))
        client = fake_client()
        client.chat.completions.create.side_effect = endless
        get_client.return_value = client

        result = self.ask(self.alice)

        self.assertEqual(result.outcome, Outcome.ROUND_LIMIT)
        self.assertEqual(result.answer, FALLBACK_MESSAGE)
        self.assertEqual(result.tool_rounds, MAX_TOOL_ROUNDS)
        self.assertEqual(client.chat.completions.create.call_count, MAX_TOOL_ROUNDS + 1)

    def test_openai_failure_becomes_an_error_outcome(self, get_client):
        get_client.return_value.chat.completions.create.side_effect = TimeoutError("boom")

        result = self.ask(self.alice)

        self.assertEqual(result.outcome, Outcome.ERROR)
        self.assertEqual(result.answer, ERROR_MESSAGE)
        self.assertEqual(result.error_type, "TimeoutError")

    def test_failures_are_logged_without_any_user_content(self, get_client):
        get_client.return_value.chat.completions.create.side_effect = RuntimeError("secret-question-text")

        with self.assertLogs("apps.assistant.service", level="ERROR") as logs:
            self.ask(self.alice, "secret-question-text")

        self.assertNotIn("secret-question-text", "\n".join(logs.output))

    def test_a_broken_tool_call_does_not_crash_the_conversation(self, get_client):
        get_client.return_value = fake_client(
            reply(tool_calls=[tool_call("c1", "query_spending", "{not json")]),
            reply("לא הצלחתי"),
        )

        result = self.ask(self.alice)

        self.assertEqual(result.outcome, Outcome.ANSWERED)
        second = get_client.return_value.chat.completions.create.call_args_list[1].kwargs
        self.assertIn("error", json.loads(second["messages"][-1]["content"]))

    def test_empty_and_ambiguous_flags(self, get_client):
        Product.objects.create(name="תפוח אדמה אדום", unit=Unit.KG)
        Product.objects.create(name="תפוח אדמה לבן", unit=Unit.KG)
        get_client.return_value = fake_client(
            reply(tool_calls=[tool_call("c1", "query_spending", {})]), reply("אין נתונים"),
            reply(tool_calls=[tool_call("c2", "query_spending", {"products": ["תפוח אדמה"]})]), reply("איזה?"),
        )

        empty = self.ask(self.alice)
        ambiguous = self.ask(self.alice)

        self.assertTrue(empty.empty_result)
        self.assertFalse(empty.ambiguous)
        self.assertTrue(ambiguous.ambiguous)

    def test_non_empty_results_are_not_flagged_empty(self, get_client):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))
        get_client.return_value = fake_client(
            reply(tool_calls=[tool_call("c1", "query_spending", {})]), reply("50"),
        )

        self.assertFalse(self.ask(self.alice).empty_result)
