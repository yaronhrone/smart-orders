from django.conf import settings
from django.db import models


class AssistantCall(models.Model):
    """
    One row per question asked of the data assistant. Metadata only, on
    purpose: it never holds the question, the answer or any tool argument, so
    it can be read freely to see usage, failures and cost.
    """

    class Outcome(models.TextChoices):
        ANSWERED = "answered", "נענתה"
        OFF_TOPIC = "off_topic", "לא קשורה"
        ERROR = "error", "שגיאה"
        ROUND_LIMIT = "round_limit", "חריגה ממספר הסבבים"
        THROTTLED = "throttled", "נחסמה (עומס)"

    class Feedback(models.TextChoices):
        UP = "up", "👍"
        DOWN = "down", "👎"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assistant_calls",
    )
    is_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    error_type = models.CharField(max_length=100, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    tool_rounds = models.PositiveSmallIntegerField(default=0)
    tools_called = models.JSONField(default=list, blank=True)
    empty_result = models.BooleanField(default=False)
    ambiguous = models.BooleanField(default=False)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    feedback = models.CharField(max_length=4, choices=Feedback.choices, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.id} {self.outcome} {self.created_at:%Y-%m-%d %H:%M}"
