import datetime as dt
import math
from collections import Counter

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDay
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import AssistantCall
from .serializers import AskSerializer, FeedbackSerializer
from .service import ask
from .tools import IL_TZ, build_context

# gpt-4o-mini list prices, USD per million tokens. An estimate, not a bill.
PRICE_PER_M_INPUT = 0.15
PRICE_PER_M_OUTPUT = 0.60


class AskView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "assistant"

    def throttled(self, request, wait):
        AssistantCall.objects.create(
            user=request.user, is_admin=request.user.is_staff, outcome=AssistantCall.Outcome.THROTTLED,
        )
        super().throttled(request, wait)

    def post(self, request):
        serializer = AskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.is_staff and not hasattr(user, "profile"):
            return Response({"detail": "המשתמש אינו מקושר לפרופיל חברה"}, status=status.HTTP_400_BAD_REQUEST)

        result = ask(
            build_context(user), serializer.validated_data["message"], serializer.validated_data["history"],
        )
        call = AssistantCall.objects.create(
            user=user, is_admin=user.is_staff, outcome=result.outcome, error_type=result.error_type,
            duration_ms=result.duration_ms, tool_rounds=result.tool_rounds, tools_called=result.tools_called,
            empty_result=result.empty_result, ambiguous=result.ambiguous,
            prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
        )
        return Response({"answer": result.answer, "call_id": call.id})


class FeedbackView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = FeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = AssistantCall.objects.filter(
            id=serializer.validated_data["call_id"], user=request.user,
        ).update(feedback=serializer.validated_data["rating"])
        if not updated:
            return Response({"detail": "לא נמצא"}, status=status.HTTP_404_NOT_FOUND)
        return Response({"ok": True})


class StatsView(APIView):
    """GET /api/assistant/stats/?days=30 — usage, failures and cost of the assistant (admin only)."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        try:
            days = max(1, min(int(request.query_params.get("days", 30)), 365))
        except ValueError:
            return Response({"detail": "days must be a number"}, status=status.HTTP_400_BAD_REQUEST)

        calls = AssistantCall.objects.filter(created_at__gte=timezone.now() - dt.timedelta(days=days))
        count = lambda **flt: Count("id", filter=Q(**flt))  # noqa: E731
        totals = calls.aggregate(
            total=Count("id"),
            unique_users=Count("user", distinct=True),
            answered=count(outcome=AssistantCall.Outcome.ANSWERED),
            off_topic=count(outcome=AssistantCall.Outcome.OFF_TOPIC),
            errors=count(outcome=AssistantCall.Outcome.ERROR),
            round_limit=count(outcome=AssistantCall.Outcome.ROUND_LIMIT),
            throttled=count(outcome=AssistantCall.Outcome.THROTTLED),
            empty_result=count(empty_result=True),
            ambiguous=count(ambiguous=True),
            thumbs_up=count(feedback=AssistantCall.Feedback.UP),
            thumbs_down=count(feedback=AssistantCall.Feedback.DOWN),
            prompt_tokens=Sum("prompt_tokens"),
            completion_tokens=Sum("completion_tokens"),
            avg_duration_ms=Avg("duration_ms", filter=~Q(outcome=AssistantCall.Outcome.THROTTLED)),
        )

        real = calls.exclude(outcome=AssistantCall.Outcome.THROTTLED)
        durations = sorted(real.values_list("duration_ms", flat=True))
        p95 = durations[max(0, math.ceil(len(durations) * 0.95) - 1)] if durations else None
        tools = Counter(name for names in real.values_list("tools_called", flat=True) for name in names)
        per_day = (
            calls.annotate(day=TruncDay("created_at", tzinfo=IL_TZ)).values("day")
            .annotate(n=Count("id")).order_by("day")
        )

        prompt_tokens = totals["prompt_tokens"] or 0
        completion_tokens = totals["completion_tokens"] or 0
        handled = totals["total"] - totals["throttled"]
        return Response({
            "days": days,
            "total_calls": totals["total"],
            "unique_users": totals["unique_users"],
            "outcomes": {
                "answered": totals["answered"], "off_topic": totals["off_topic"], "error": totals["errors"],
                "round_limit": totals["round_limit"], "throttled": totals["throttled"],
            },
            "success_rate": round(totals["answered"] / handled, 3) if handled else None,
            "empty_result_calls": totals["empty_result"],
            "ambiguous_calls": totals["ambiguous"],
            "thumbs_up": totals["thumbs_up"],
            "thumbs_down": totals["thumbs_down"],
            "avg_duration_ms": round(totals["avg_duration_ms"]) if totals["avg_duration_ms"] else None,
            "p95_duration_ms": p95,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost_usd": round(
                prompt_tokens / 1e6 * PRICE_PER_M_INPUT + completion_tokens / 1e6 * PRICE_PER_M_OUTPUT, 4,
            ),
            "top_tools": [{"tool": name, "calls": n} for name, n in tools.most_common(10)],
            "calls_per_day": [{"day": row["day"].date().isoformat(), "calls": row["n"]} for row in per_day],
        })
