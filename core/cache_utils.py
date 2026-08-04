"""
"Generation"/versioned caching helper.

Instead of tracking and deleting every cache key a piece of data could be
stored under (e.g. every limit/offset pagination combo), each cache key
embeds a version number for its namespace+key. Bumping the version instantly
invalidates every previously cached entry under it — old entries are simply
never read again, and expire naturally via their own TTL as a safety net.
"""
from django.core.cache import cache


def get_cache_version(namespace: str, key) -> int:
    version_key = f"cache_version:{namespace}:{key}"
    version = cache.get(version_key)
    if version is None:
        version = 1
        cache.set(version_key, version, timeout=None)
    return version


def bump_cache_version(namespace: str, key) -> None:
    version_key = f"cache_version:{namespace}:{key}"
    try:
        cache.incr(version_key)
    except ValueError:
        cache.set(version_key, 1, timeout=None)
