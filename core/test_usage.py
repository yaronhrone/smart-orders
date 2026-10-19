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
