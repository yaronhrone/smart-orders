"""
Compares AI models on the same labeled order messages: how accurate, how fast, how much.

    python manage.py compare_models
    python manage.py compare_models --models openai:gpt-4o-mini anthropic:claude-sonnet-5-5
    python manage.py compare_models --repeat 3

Needs the key of every provider compared in .env (OPENAI_API_KEY, ANTHROPIC_API_KEY,
DEEPSEEK_API_KEY); a provider without a key is skipped. Every message goes through
the real modification parser (apps/orders/order_parser.py), so this measures what
production would do, and it spends real money: about 25 short calls per model per run.
The database is not touched.
"""
import statistics
import time
from decimal import Decimal

from django.core.management.base import BaseCommand

from core import llm

PRODUCTS = ["עגבניה", "מלפפון", "גזר", "בצל יבש", "פלפל אדום", "פלפל ירוק", "תפוח אדמה אדום", "תפוח אדמה לבן", "חסה", "לימון"]

# message -> expected [(product, quantity, action)]; [] means "not a request to change the order".
# Families ("פלפל", "תפוח אדמה") must come back as the bare root, not a guessed variant.
CASES = [
    ("תוסיף 20 קילו עגבניות", [("עגבניה", 20, "add")]),
    ("גם 15 מלפפונים", [("מלפפון", 15, "add")]),
    ("עגבניות 20", [("עגבניה", 20, "add")]),
    ("50 גזר", [("גזר", 50, "add")]),
    ("תביא עוד 10 קילו חסה", [("חסה", 10, "add")]),
    ("תגדיל את הלימונים ב-5", [("לימון", 5, "add")]),
    ("תעדכן עגבניות ל-50", [("עגבניה", 50, "update")]),
    ("במקום 30 מלפפונים תביא 45", [("מלפפון", 45, "update")]),
    ("תקטין את הגזר ל-20", [("גזר", 20, "update")]),
    ("שנה את החסה ל-12", [("חסה", 12, "update")]),
    ("תוריד 10 קילו עגבניות", [("עגבניה", 10, "reduce")]),
    ("תפחית 5 מלפפונים", [("מלפפון", 5, "reduce")]),
    ("תקטין ב-3 את הלימונים", [("לימון", 3, "reduce")]),
    ("תוסיף 20 עגבניות ותעדכן מלפפונים ל-15", [("עגבניה", 20, "add"), ("מלפפון", 15, "update")]),
    ("תוריד 5 גזר ותוסיף 10 חסה", [("גזר", 5, "reduce"), ("חסה", 10, "add")]),
    ("עגבניות 30, מלפפון 20, גזר 10", [("עגבניה", 30, "add"), ("מלפפון", 20, "add"), ("גזר", 10, "add")]),
    ("תוסיף 10 פלפל", [("פלפל", 10, "add")]),
    ("תוסיף 25 תפוח אדמה", [("תפוח אדמה", 25, "add")]),
    ("תוסיף 10 פלפל אדום", [("פלפל אדום", 10, "add")]),
    ("תוסיף 8 תפוחי אדמה לבנים", [("תפוח אדמה לבן", 8, "add")]),
    ("עוד 12 בצל", [("בצל יבש", 12, "add")]),
    ("תודה רבה!", []),
    ("מתי ההזמנה מגיעה?", []),
    ("שלום", []),
    ("כמה עולה עגבניה היום?", []),
]

# USD per million tokens (input, output). Check the providers' price pages: these change.
PRICES = {
    "gpt-4o-mini": (0.15, 0.60),
    "deepseek-flash": (0.30, 1.20),
    "deepseek-v4-pro": (1.32, 3.96),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
DEFAULT_MODELS = ["openai", "anthropic"]  # deepseek works too, but is left out on purpose (data goes to China)


def _normalise(items):
    return sorted((i["product_name"], Decimal(str(i["quantity"])), i["intent"]) for i in items)


def _expected(case):
    return sorted((name, Decimal(qty), action) for name, qty, action in case)


class Command(BaseCommand):
    help = "Compare AI models on accuracy, speed and cost with labeled order messages."

    def add_arguments(self, parser):
        parser.add_argument(
            "--models", nargs="*", default=DEFAULT_MODELS,
            help="provider or provider:model, e.g. openai anthropic:claude-opus-5-5 deepseek",
        )
        parser.add_argument("--repeat", type=int, default=1, help="run every message this many times")
        parser.add_argument("--show-misses", action="store_true", help="print each wrong answer")

    def handle(self, *args, **options):
        import os
        from apps.orders import order_parser

        rows = []
        for spec in options["models"]:
            provider, _, model_name = spec.partition(":")
            os.environ["LLM_PROVIDER"], os.environ["LLM_MODEL"] = provider, model_name
            llm._clients.clear()
            try:
                if not llm.api_key_configured():
                    self.stdout.write(self.style.WARNING(f"{spec}: אין מפתח ב-.env, מדלג"))
                    continue
                name = llm.model()
            except ValueError as exc:
                self.stdout.write(self.style.ERROR(str(exc)))
                continue

            self.stdout.write(f"\n{provider} / {name} ...")
            hits, errors, times, tokens_in, tokens_out, misses = 0, 0, [], 0, 0, []
            total = 0
            for message, expected in CASES * options["repeat"]:
                total += 1
                llm.last_usage.update(input=0, output=0)
                started = time.monotonic()
                try:
                    result = order_parser.parse_modification_intent(message, PRODUCTS)
                except Exception:  # the parser itself swallows model errors; this is a safety net
                    result = {"items": []}
                    errors += 1
                times.append(time.monotonic() - started)
                tokens_in += llm.last_usage["input"]
                tokens_out += llm.last_usage["output"]
                if llm.last_usage["input"] == 0:
                    errors += 1  # no tokens used: the call failed (the parser returned "none")
                if _normalise(result["items"]) == _expected(expected):
                    hits += 1
                else:
                    misses.append((message, expected, result["items"]))

            price_in, price_out = PRICES.get(name, (None, None))
            cost = None if price_in is None else (tokens_in * price_in + tokens_out * price_out) / 1_000_000
            rows.append({
                "name": f"{provider}/{name}", "accuracy": hits / total, "hits": hits, "total": total,
                "median": statistics.median(times), "p95": sorted(times)[int(len(times) * 0.95) - 1],
                "cost_per_1000": None if cost is None else cost / total * 1000,
                "errors": errors,
            })
            if options["show_misses"]:
                for message, expected, got in misses:
                    self.stdout.write(f"  ✗ {message!r}\n      צפוי: {expected}\n      התקבל: {[(i['product_name'], str(i['quantity']), i['intent']) for i in got]}")

        if not rows:
            self.stdout.write(self.style.ERROR("אין מה להשוות: חסרים מפתחות"))
            return

        self.stdout.write("\n" + "מודל".ljust(36) + "דיוק".ljust(14) + "חציוני".ljust(10) + "p95".ljust(9) + "$ ל-1000 הודעות".ljust(17) + "שגיאות")
        for r in sorted(rows, key=lambda r: -r["accuracy"]):
            cost = "?" if r["cost_per_1000"] is None else f"${r['cost_per_1000']:.3f}"
            self.stdout.write(
                r["name"].ljust(36) + f"{r['accuracy']:.0%} ({r['hits']}/{r['total']})".ljust(14)
                + f"{r['median']:.1f}s".ljust(10) + f"{r['p95']:.1f}s".ljust(9) + cost.ljust(17) + str(r["errors"])
            )
        best = max(rows, key=lambda r: r["accuracy"])
        fastest = min(rows, key=lambda r: r["median"])
        priced = [r for r in rows if r["cost_per_1000"] is not None]
        self.stdout.write(f"\nהכי מדויק: {best['name']} · הכי מהיר: {fastest['name']}"
                          + (f" · הכי זול: {min(priced, key=lambda r: r['cost_per_1000'])['name']}" if priced else ""))
