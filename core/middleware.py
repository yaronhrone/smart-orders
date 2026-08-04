import time

from django.core.cache import cache

STATS_COUNT_KEY = "api_stats:count"
STATS_TIME_KEY = "api_stats:total_ms"

# Noise we don't care about for caching decisions.
_EXCLUDED_ROUTE_PREFIXES = ("django-admin", "schema", "static")


class ApiStatsMiddleware:
    """
    Records a per-endpoint call count and cumulative response time in Redis,
    keyed by "<METHOD> /<url pattern>" (not the raw path, so /orders/1/ and
    /orders/2/ count as the same endpoint). Read via `manage.py api_stats`.

    Never lets stats tracking break or slow down the actual request — any
    failure here (e.g. Redis briefly down) is swallowed silently.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        try:
            route = request.resolver_match.route if request.resolver_match else None
            if route and not route.startswith(_EXCLUDED_ROUTE_PREFIXES):
                key = f"{request.method} /{route}"
                client = cache.client.get_client(write=True)
                client.hincrby(STATS_COUNT_KEY, key, 1)
                client.hincrbyfloat(STATS_TIME_KEY, key, elapsed_ms)
        except Exception:
            pass

        return response
