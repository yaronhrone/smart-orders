from django.db import models


class AIUsage(models.Model):
    """
    One row per call to an AI model: what it was for, which model answered, the tokens it used, an
    estimated cost and how long it took. Metadata only: never the message, the prompt or the answer.
    Feeds the admin "AI usage" page, which also compares models side by side.
    """

    kind = models.CharField(max_length=30)  # order_parse, modification_parse, price_parse, assistant
    provider = models.CharField(max_length=20)
    model = models.CharField(max_length=60)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    duration_ms = models.PositiveIntegerField(default=0)
    ok = models.BooleanField(default=True)
    # Parsers only (not the assistant): the message that went to the AI and what it returned.
    message = models.TextField(blank=True, default="")
    result = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} {self.model} {self.input_tokens}+{self.output_tokens}"
