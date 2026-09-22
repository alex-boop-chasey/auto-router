from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import HTTPException, Request

from .config import SETTINGS

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None or _pool.is_closed():
        _pool = await asyncpg.create_pool(
            SETTINGS.database_url,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
    assert _pool is not None
    return _pool


async def init_db() -> None:
    """Create tables and seed default router keys."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS keys (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                caller_id TEXT NOT NULL,
                api_key TEXT UNIQUE NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                prompt_preview_enabled BOOLEAN DEFAULT FALSE
            );

            CREATE TABLE IF NOT EXISTS requests (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ DEFAULT NOW(),
                caller_id TEXT,
                tier TEXT,
                model TEXT,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cost_usd NUMERIC(12, 6) DEFAULT 0,
                latency_ms INTEGER DEFAULT 0,
                success BOOLEAN DEFAULT FALSE,
                error TEXT,
                prompt_preview TEXT,
                used_fallback BOOLEAN DEFAULT FALSE,
                jev_error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_requests_timestamp ON requests(timestamp DESC);
            """
        )
        await _seed_keys(conn)


async def _seed_keys(conn: asyncpg.Connection) -> None:
    raw = SETTINGS.router_api_keys
    if not raw and SETTINGS.openrouter_api_key:
        # Single-user Phase 1 fallback: reuse the OpenRouter key as the
        # router key. Not suitable for multi-user deployments.
        raw = f"alex:{SETTINGS.openrouter_api_key}"

    if not raw:
        return

    for part in raw.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        caller_id, key = part.split(":", 1)
        await conn.execute(
            """
            INSERT INTO keys (caller_id, api_key, prompt_preview_enabled)
            VALUES ($1, $2, $3)
            ON CONFLICT (api_key) DO UPDATE SET caller_id = EXCLUDED.caller_id;
            """,
            caller_id.strip(),
            key.strip(),
            SETTINGS.prompt_preview_default,
        )


async def verify_api_key(request: Request) -> tuple[str, bool]:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth[7:].strip()

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT caller_id, prompt_preview_enabled FROM keys WHERE api_key = $1",
            token,
        )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return row["caller_id"], row["prompt_preview_enabled"]


async def log_request(
    *,
    caller_id: str,
    tier: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
    success: bool = False,
    error: str | None = None,
    prompt_preview: str | None = None,
    used_fallback: bool = False,
    jev_error: str | None = None,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO requests (
                caller_id, tier, model, input_tokens, output_tokens, cost_usd,
                latency_ms, success, error, prompt_preview, used_fallback, jev_error
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING id
            """,
            caller_id,
            tier,
            model,
            input_tokens,
            output_tokens,
            cost_usd,
            latency_ms,
            success,
            error,
            prompt_preview,
            used_fallback,
            jev_error,
        )
        return int(row["id"])


async def get_recent_requests(limit: int = 20) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, timestamp, caller_id, tier, model, input_tokens,
                   output_tokens, cost_usd, latency_ms, success, error,
                   prompt_preview, used_fallback, jev_error
            FROM requests
            ORDER BY timestamp DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]
