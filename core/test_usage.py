from types import SimpleNamespace
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core import llm
from core.models import AIUsage


def _openai_response(content='{"items": []}', prompt_tokens=1000, completion_tokens=200):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


class RecordingTests(TestCase):
    def test_complete_json_records_tokens_model_kind_and_cost(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _openai_response()
        llm.complete_json("p", lambda: client, kind="order_parse")
        row = AIUsage.objects.get()
        self.assertEqual((row.kind, row.model, row.input_tokens, row.output_tokens, row.ok),
                         ("order_parse", llm.model(), 1000, 200, True))
        # gpt-4o-mini: 1000 x 0.15 + 200 x 0.60 per million
        self.assertAlmostEqual(float(row.cost_usd), 0.00027, places=6)

    def test_a_failed_call_is_recorded_as_failed_and_still_raises(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("down")
        with self.assertRaises(RuntimeError):
            llm.complete_json("p", lambda: client, kind="price_parse")
        row = AIUsage.objects.get()
        self.assertEqual((row.kind, row.ok, row.input_tokens), ("price_parse", False, 0))

    def test_assistant_chat_turn_is_recorded(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _openai_response("hi")
        llm.chat_session("sys", [], "q", [], openai_client_factory=lambda: client).send()
        self.assertEqual(AIUsage.objects.get().kind, "assistant")

    def test_unknown_model_costs_nothing_but_is_still_counted(self):
        self.assertEqual(llm.estimate_cost("some-new-model", 5000, 5000), 0)

    def test_a_recording_failure_never_breaks_the_call(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _openai_response()
        from unittest.mock import patch
        with patch("core.models.AIUsage.objects.create", side_effect=RuntimeError("db")):
            self.assertEqual(llm.complete_json("p", lambda: client, kind="order_parse"), {"items": []})


class UsageEndpointTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(email="a@x.com", password="x", is_staff=True)
        self.customer = User.objects.create_user(email="c@x.com", password="x")
        AIUsage.objects.create(kind="order_parse", provider="openai", model="gpt-4o-mini",
                               input_tokens=100, output_tokens=50, cost_usd="0.001", duration_ms=1000)
        AIUsage.objects.create(kind="order_parse", provider="anthropic", model="claude-sonnet-5-5",
                               input_tokens=100, output_tokens=50, cost_usd="0.010", duration_ms=3000)
        AIUsage.objects.create(kind="assistant", provider="openai", model="gpt-4o-mini",
                               input_tokens=10, output_tokens=5, cost_usd="0", duration_ms=500, ok=False)

    def _get(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client.get("/api/core/ai-usage/")

    def test_only_an_admin_can_read_it(self):
        self.assertEqual(self._get(self.customer).status_code, 403)
        self.assertEqual(APIClient().get("/api/core/ai-usage/").status_code, 401)

    def test_totals_and_the_model_comparison(self):
        data = self._get(self.admin).json()
        self.assertEqual((data["totals"]["calls"], data["totals"]["failed"]), (3, 1))
        by_model = {row["model"]: row for row in data["by_model"]}
        self.assertEqual(by_model["gpt-4o-mini"]["calls"], 2)
        self.assertEqual(by_model["claude-sonnet-5-5"]["avg_seconds"], 3.0)
        self.assertEqual(by_model["claude-sonnet-5-5"]["avg_cost_per_call_usd"], 0.01)
        pairs = {(row["model"], row["kind"]) for row in data["by_model_and_kind"]}
        self.assertIn(("claude-sonnet-5-5", "order_parse"), pairs)
        self.assertEqual(len(data["by_day"]), 1)


class CallDetailsTests(TestCase):
    def test_the_parser_keeps_the_message_and_what_the_ai_returned_but_the_assistant_does_not(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _openai_response('{"items": [{"product_name": "חציל"}]}')
        llm.complete_json("long prompt", lambda: client, kind="order_parse", message="40 חציל")
        llm.chat_session("sys", [], "private question", [], openai_client_factory=lambda: client).send()
        parse = AIUsage.objects.get(kind="order_parse")
        self.assertEqual(parse.message, "40 חציל")
        self.assertIn("חציל", parse.result)
        self.assertEqual(AIUsage.objects.get(kind="assistant").message, "")

    def test_the_endpoint_lists_recent_parses_without_the_assistant(self):
        User = get_user_model()
        admin = User.objects.create_user(email="a2@x.com", password="x", is_staff=True)
        AIUsage.objects.create(kind="order_parse", provider="openai", model="m", message="40 חציל", result="{}")
        AIUsage.objects.create(kind="assistant", provider="openai", model="m")
        client = APIClient()
        client.force_authenticate(admin)
        recent = client.get("/api/core/ai-usage/").json()["recent"]
        self.assertEqual([r["kind"] for r in recent], ["order_parse"])
        self.assertEqual(recent[0]["message"], "40 חציל")


class PurgeTests(TestCase):
    def test_text_older_than_a_day_is_blanked_but_the_statistics_stay(self):
        from datetime import timedelta
        from django.core.cache import cache
        from django.utils import timezone

        old = AIUsage.objects.create(kind="order_parse", provider="openai", model="m",
                                     input_tokens=7, message="40 חציל", result="{}")
        fresh = AIUsage.objects.create(kind="order_parse", provider="openai", model="m", message="5 גזר", result="{}")
        AIUsage.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=25))
        cache.delete("ai_usage_purge")
        self.assertEqual(llm.purge_old_content(), 1)
        old.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual((old.message, old.result, old.input_tokens), ("", "", 7))
        self.assertEqual(fresh.message, "5 גזר")
        # once an hour at most
        self.assertEqual(llm.purge_old_content(), 0)
