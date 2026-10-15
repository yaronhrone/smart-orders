from django.http import JsonResponse
from django.db import connection
from django.core.cache import cache

from core import llm


def health_check(request):
    HEALTH_CHECK_CACHE_KEY = "health_check"
    status = "healthy"
    services = {}

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
        services["database"] = "ok"
    except Exception:
        services["database"] = "failed"
        status = "unhealthy"

    try:
        cache.set(HEALTH_CHECK_CACHE_KEY, "ok", timeout=5)
        if cache.get(HEALTH_CHECK_CACHE_KEY) == "ok":
            services["redis"] = "ok"
        else:
            raise Exception("Redis cache verification failed")
    except Exception:
        services["redis"] = "failed"
        status = "unhealthy"

    if llm.api_key_configured():
        services["ai_service"] = "ok"
    else:
        services["ai_service"] = "missing_api_key"
        status = "unhealthy"

    # Information only: a failing WhatsApp account must not make the whole service look
    # down (a 503 here can take the site out of rotation), but it must be visible.
    try:
        failure = cache.get("twilio_last_send_failure")
    except Exception:
        failure = None
    services["whatsapp_sending"] = "ok" if not failure else f"last send failed at {failure['at']}: {failure['error']}"

    try:
        services["parsing_today"] = llm.parse_counts_today() or "none yet"
    except Exception:
        pass

    response_status = 200 if status == "healthy" else 503

    return JsonResponse({
        "status": status,
        "services": services
        },
        status=response_status
        )


