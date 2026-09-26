"""
Redis-backed response cache for auto-router.

Caches LLM responses by (model, messages_hash, temperature).
Saves cost by avoiding redundant API calls.
"""
from __future__ import annotations
import hashlib, json
from typing import Any

try:
    import redis as _redis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False
    _redis = None

_cache_client: Any = None
CACHE_TTL: int = 3600

def init_cache(redis_url: str | None = None, ttl: int = 3600) -> None:
    global _cache_client, CACHE_TTL
    CACHE_TTL = ttl
    if redis_url and HAS_REDIS:
        _cache_client = _redis.from_url(redis_url, decode_responses=False)
    else:
        _cache_client = None

def _make_cache_key(model: str, messages: list[dict], temperature: float | None) -> str:
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature or 0.0,
    }, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"ar:cache:{model}:{digest}"

def get_cached(cache_key: str) -> bytes | None:
    if not _cache_client:
        return None
    try:
        return _cache_client.get(cache_key)
    except Exception:
        return None

def set_cached(cache_key: str, data: bytes, ttl: int | None = None) -> None:
    if not _cache_client:
        return
    try:
        _cache_client.setex(cache_key, ttl or CACHE_TTL, data)
    except Exception:
        pass

def make_and_check_cache(
    model: str, messages: list[dict], temperature: float | None
) -> tuple[str, bytes | None]:
    key = _make_cache_key(model, messages, temperature)
    data = get_cached(key)
    return key, data
