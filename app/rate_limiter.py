"""
Rate limiter - RPM + TPM enforcement per key.
Redis-backed when available, in-memory fallback for single-worker.

Uses redis.asyncio for the same reason as cache.py: the sync redis client's
pipeline.execute() blocks the whole event loop for a full Redis round trip,
and this check runs on every single chat completion request.
"""
from __future__ import annotations

import threading
import time
from typing import Any

try:
    import redis.asyncio as _redis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False
    _redis = None

_redis_client: Any = None
_fallback_store: dict[str, dict[str, int]] = {}
_fallback_lock = threading.Lock()

def init_rate_limiter(redis_url: str | None = None) -> None:
    global _redis_client
    if redis_url and HAS_REDIS:
        _redis_client = _redis.from_url(redis_url, decode_responses=True)
    else:
        _redis_client = None

def _now_sec() -> int:
    return int(time.time())

async def check_rate_limit(
    key_id: str,
    rpm_limit: int | None = None,
    tpm_limit: int | None = None,
    estimated_tokens: int = 0,
) -> tuple[bool, str]:
    """Check rate limits. Returns (allowed, reason)."""
    if not rpm_limit and not tpm_limit:
        return True, ""

    now = _now_sec()
    minute_bucket = now // 60

    if _redis_client:
        pipe = _redis_client.pipeline()
        rpm_key = f"rl:rpm:{key_id}:{minute_bucket}"
        tpm_key = f"rl:tpm:{key_id}:{minute_bucket}"

        if rpm_limit:
            pipe.incr(rpm_key)
            pipe.expire(rpm_key, 120)
        if tpm_limit and estimated_tokens > 0:
            pipe.incrby(tpm_key, estimated_tokens)
            pipe.expire(tpm_key, 120)
        results = await pipe.execute()

        idx = 0
        if rpm_limit:
            rpm_val = results[idx]
            idx += 1
            if rpm_val > rpm_limit:
                return False, f"RPM limit exceeded ({rpm_limit}/min, current={rpm_val})"
        if tpm_limit and estimated_tokens > 0:
            tpm_val = results[idx]
            if tpm_val > tpm_limit:
                return False, f"TPM limit exceeded ({tpm_limit}/min, current={tpm_val})"
    else:
        with _fallback_lock:
            store_key = f"{key_id}:{minute_bucket}"
            entry = _fallback_store.get(store_key, {"rpm": 0, "tpm": 0})

            if rpm_limit:
                entry["rpm"] += 1
                if entry["rpm"] > rpm_limit:
                    return False, f"RPM limit exceeded ({rpm_limit}/min)"
            if tpm_limit and estimated_tokens > 0:
                entry["tpm"] += estimated_tokens
                if entry["tpm"] > tpm_limit:
                    return False, f"TPM limit exceeded ({tpm_limit}/min)"

            _fallback_store[store_key] = entry
            # Cleanup old buckets
            for k in list(_fallback_store):
                parts = k.split(":")
                if len(parts) >= 3:
                    try:
                        bucket_ts = int(parts[-1])
                        if now - bucket_ts * 60 > 120:
                            del _fallback_store[k]
                    except ValueError:
                        pass

    return True, ""

async def track_tokens(key_id: str, actual_tokens: int) -> None:
    """Add actual token usage after response (for TPM enforcement)."""
    if not actual_tokens:
        return
    now = _now_sec()
    minute_bucket = now // 60
    if _redis_client:
        tpm_key = f"rl:tpm:{key_id}:{minute_bucket}"
        await _redis_client.incrby(tpm_key, actual_tokens)
        await _redis_client.expire(tpm_key, 120)

def key_rate_limit_headers(key_id: str, rpm_limit: int | None, tpm_limit: int | None) -> dict[str, str]:
    """Return X-RateLimit headers."""
    headers = {}
    if rpm_limit:
        headers["X-RateLimit-Limit-Requests"] = str(rpm_limit)
    if tpm_limit:
        headers["X-RateLimit-Limit-Tokens"] = str(tpm_limit)
    return headers
