from django.http import JsonResponse
from django.db import connection
from django.core.cache import cache
import os


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

    if os.environ.get("OPENAI_API_KEY"):
        services["ai_service"] = "ok"
    else:
        services["ai_service"] = "missing_api_key"
        status = "unhealthy"

    response_status = 200 if status == "healthy" else 503

    return JsonResponse({
        "status": status,
        "services": services
        },
        status=response_status
        )


