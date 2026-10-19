from datetime import timedelta

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from core import llm
from core.models import AIUsage


def _rows(queryset, *group_by):
    return [
        {
            **{key: (str(row[key]) if key == "day" else row[key]) for key in group_by},
            "calls": row["calls"],
            "failed": row["failed"],
            "input_tokens": row["input_tokens"] or 0,
            "output_tokens": row["output_tokens"] or 0,
            "cost_usd": round(float(row["cost_usd"] or 0), 4),
            "avg_seconds": round((row["avg_ms"] or 0) / 1000, 2),
            "avg_cost_per_call_usd": round(float(row["cost_usd"] or 0) / row["calls"], 5) if row["calls"] else 0,
        }
        for row in queryset.values(*group_by).annotate(
            calls=Count("id"),
            failed=Count("id", filter=Q(ok=False)),
            input_tokens=Sum("input_tokens"),
            output_tokens=Sum("output_tokens"),
            cost_usd=Sum("cost_usd"),
            avg_ms=Avg("duration_ms"),
        ).order_by(*group_by)
    ]


class AIUsageView(APIView):
    """
    Admin only. GET ?days=30 (default) -> the AI calls of the last N days:
      totals, per day, per kind, and per model (and per model x kind) for comparing models.
    Costs are estimates from llm.PRICES_PER_MILLION. `parsing_today` is how many order/price
    messages the dictionary understood for free vs how many needed the AI.
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        try:
            days = min(max(int(request.query_params.get("days", 30)), 1), 365)
        except ValueError:
            days = 30
        since = timezone.now() - timedelta(days=days)
        qs = AIUsage.objects.filter(created_at__gte=since)
        totals = qs.aggregate(
            calls=Count("id"), failed=Count("id", filter=Q(ok=False)),
            input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens"), cost_usd=Sum("cost_usd"),
        )
        return Response({
            "days": days,
            "current_model": llm.model(),
            "totals": {
                "calls": totals["calls"], "failed": totals["failed"],
                "input_tokens": totals["input_tokens"] or 0, "output_tokens": totals["output_tokens"] or 0,
                "cost_usd": round(float(totals["cost_usd"] or 0), 4),
            },
            "by_day": _rows(qs.annotate(day=TruncDate("created_at")), "day"),
            "by_kind": _rows(qs, "kind"),
            "by_model": _rows(qs, "model"),
            "by_model_and_kind": _rows(qs, "model", "kind"),
            "parsing_today": llm.parse_counts_today(),
        })
