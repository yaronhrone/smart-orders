import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from core import llm


def _anthropic_response(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(
        content=list(blocks), stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=120, output_tokens=8),
    )


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _thinking():
    return SimpleNamespace(type="thinking", thinking="")


def _tool_use(call_id, name, args):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=args)


TOOLS = [{"type": "function", "function": {
    "name": "query_spending", "description": "spend",
    "parameters": {"type": "object", "properties": {"date_from": {"type": "string"}}},
}}]


class ProviderSwitchTests(SimpleTestCase):
    def setUp(self):
        llm._clients.clear()
        self.addCleanup(llm._clients.clear)

    def test_openai_is_the_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_PROVIDER", None)
            os.environ.pop("LLM_MODEL", None)
            self.assertEqual((llm.provider(), llm.model()), ("openai", "gpt-4o-mini"))

    def test_each_provider_has_its_default_model_and_llm_model_overrides_it(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic", "LLM_MODEL": ""}):
            self.assertEqual(llm.model(), "claude-sonnet-5-5")
        with patch.dict(os.environ, {"LLM_PROVIDER": "deepseek", "LLM_MODEL": ""}):
            self.assertEqual(llm.model(), "deepseek-flash")
        with patch.dict(os.environ, {"LLM_PROVIDER": "Anthropic", "LLM_MODEL": "claude-sonnet-5-5"}):
            self.assertEqual(llm.model(), "claude-sonnet-5-5")

    def test_an_unknown_provider_is_an_error_not_a_silent_default(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini"}):
            with self.assertRaises(ValueError):
                llm.provider()

    def test_deepseek_uses_the_openai_sdk_on_its_own_url_and_key(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "ds-key"}), \
                patch("openai.OpenAI") as openai_cls:
            llm.openai_client()
        openai_cls.assert_called_once_with(api_key="ds-key", base_url=llm.DEEPSEEK_BASE_URL)

    def test_the_health_check_looks_at_the_key_of_the_chosen_provider(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": ""}):
            self.assertFalse(llm.api_key_configured())
        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k"}):
            self.assertTrue(llm.api_key_configured())


@patch.dict(os.environ, {"LLM_PROVIDER": "anthropic", "LLM_MODEL": "", "ANTHROPIC_API_KEY": "k"})
class AnthropicTests(SimpleTestCase):
    def setUp(self):
        self.client = MagicMock()
        patcher = patch("core.llm.anthropic_client", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_complete_json_reads_the_text_block_and_tolerates_a_fence(self):
        self.client.beta.messages.create.return_value = _anthropic_response(
            _thinking(), _text('```json\n{"items": [{"product_name": "עגבניה", "quantity": "5"}]}\n```'),
        )

        data = llm.complete_json("prompt")

        self.assertEqual(data["items"][0]["product_name"], "עגבניה")
        kwargs = self.client.beta.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-sonnet-5-5")
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertNotIn("temperature", kwargs)  # rejected by this model

    def test_a_refusal_is_raised_for_the_caller_to_handle(self):
        self.client.beta.messages.create.return_value = _anthropic_response(stop_reason="refusal")

        with self.assertRaises(RuntimeError):
            llm.complete_json("prompt")

    def test_tool_loop_sends_tools_in_anthropic_format_and_returns_results_in_one_message(self):
        first = _anthropic_response(_thinking(), _tool_use("t1", "query_spending", {"date_from": "2026-09-01"}),
                                    stop_reason="tool_use")
        self.client.beta.messages.create.side_effect = [first, _anthropic_response(_text("הוצאת 50 ₪."))]
        history = [{"role": "assistant", "content": "שלום"}, {"role": "user", "content": "היי"},
                   {"role": "assistant", "content": "מה תרצה?"}]

        chat = llm.chat_session("system", history, "כמה הוצאתי?", TOOLS)
        reply = chat.send()
        self.assertEqual([(c.id, c.name) for c in reply.tool_calls], [("t1", "query_spending")])
        self.assertEqual(json.loads(reply.tool_calls[0].arguments), {"date_from": "2026-09-01"})
        chat.add_tool_results(reply, [("t1", '{"total_spend": "50.00"}')])
        final = chat.send()

        self.assertEqual(final.text, "הוצאת 50 ₪.")
        self.assertEqual((final.prompt_tokens, final.completion_tokens), (120, 8))
        first_call, second_call = (c.kwargs for c in self.client.beta.messages.create.call_args_list)
        self.assertEqual(first_call["tools"][0]["name"], "query_spending")
        self.assertIn("input_schema", first_call["tools"][0])
        self.assertEqual(first_call["system"], "system")
        messages = second_call["messages"]
        self.assertEqual(messages[0], {"role": "user", "content": "היי"})  # opens with the user
        self.assertEqual(messages[-2], {"role": "assistant", "content": first.content})  # thinking kept
        self.assertEqual(messages[-1]["content"], [
            {"type": "tool_result", "tool_use_id": "t1", "content": '{"total_spend": "50.00"}'},
        ])


class SendFailureVisibilityTests(SimpleTestCase):
    """A failed WhatsApp send used to leave only a log line; /health/ now shows it."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)

    @patch("apps.orders.whatsapp.validators.Client")
    def test_a_failed_send_is_recorded_and_the_next_good_send_clears_it(self, client_cls):
        from django.core.cache import cache
        from apps.orders.whatsapp import validators

        client_cls.return_value.messages.create.side_effect = Exception("exceeded the 50 daily messages limit")
        with self.assertRaises(Exception):
            validators.send_whatsapp_message("+972501234567", "שלום")
        failure = cache.get(validators.SEND_FAILURE_KEY)
        self.assertIn("50 daily messages", failure["error"])

        client_cls.return_value.messages.create.side_effect = None
        client_cls.return_value.messages.create.return_value.sid = "SM1"
        validators.send_whatsapp_message("+972501234567", "שלום")
        self.assertIsNone(cache.get(validators.SEND_FAILURE_KEY))


class CeleryReliabilitySettingsTests(SimpleTestCase):
    def test_tasks_with_a_long_countdown_are_not_redelivered_and_survive_a_dead_worker(self):
        from django.conf import settings
        from apps.orders.whatsapp.cache import MINIMUM_GRACE_SECONDS

        # The longest countdown we schedule must fit inside the Redis visibility timeout.
        self.assertGreater(settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"], MINIMUM_GRACE_SECONDS)
        self.assertTrue(settings.CELERY_TASK_ACKS_LATE)
        self.assertTrue(settings.CELERY_TASK_REJECT_ON_WORKER_LOST)


class ParseCountersTests(SimpleTestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)

    def test_each_parse_is_counted_by_where_it_was_understood(self):
        llm.note_parse("order_parse", used_ai=False, dictionary=3)
        llm.note_parse("order_parse", used_ai=False, dictionary=1)
        llm.note_parse("order_parse", used_ai=True, left_for_ai=2)
        llm.note_parse("price_parse", used_ai=True)

        self.assertEqual(llm.parse_counts_today(), {
            "order_parse": {"dictionary": 2, "ai": 1},
            "price_parse": {"dictionary": 0, "ai": 1},
        })
