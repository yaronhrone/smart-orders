from django.core.cache import cache
from django.core.management.base import BaseCommand

from core.middleware import STATS_COUNT_KEY, STATS_TIME_KEY


def _decode(value):
    return value.decode() if isinstance(value, bytes) else value


class Command(BaseCommand):
    help = (
        "Show cumulative API call counts and average response times per endpoint, "
        "to help decide where caching would actually help. Data is collected by "
        "ApiStatsMiddleware and stored in Redis since the app last started (or "
        "since the last --reset)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true",
            help="Clear all collected stats instead of showing them.",
        )
        parser.add_argument(
            "--sort", choices=["count", "avg", "total"], default="total",
            help="Column to sort by (default: total, i.e. count * avg — where caching pays off most).",
        )

    def handle(self, *args, **options):
        client = cache.client.get_client(write=True)

        if options["reset"]:
            client.delete(STATS_COUNT_KEY, STATS_TIME_KEY)
            self.stdout.write(self.style.SUCCESS("API stats cleared."))
            return

        counts = client.hgetall(STATS_COUNT_KEY)
        times = client.hgetall(STATS_TIME_KEY)
        if not counts:
            self.stdout.write("No API calls recorded yet.")
            return

        rows = []
        for raw_key, raw_count in counts.items():
            key = _decode(raw_key)
            count = int(raw_count)
            total_ms = float(times.get(raw_key, 0) or 0)
            avg_ms = total_ms / count if count else 0
            rows.append((key, count, avg_ms, total_ms))

        sort_index = {"count": 1, "avg": 2, "total": 3}[options["sort"]]
        rows.sort(key=lambda r: r[sort_index], reverse=True)

        endpoint_width = max(len(r[0]) for r in rows) + 2
        header = f"{'ENDPOINT':<{endpoint_width}}{'CALLS':>8}{'AVG (ms)':>12}{'TOTAL (ms)':>14}"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for key, count, avg_ms, total_ms in rows:
            self.stdout.write(f"{key:<{endpoint_width}}{count:>8}{avg_ms:>12.1f}{total_ms:>14.0f}")

        self.stdout.write("")
        self.stdout.write(
            f"{len(rows)} endpoints tracked. Sorted by {options['sort']}. "
            "Run with --reset to clear."
        )
