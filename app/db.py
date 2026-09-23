from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import HTTPException, Request

from .config import SETTINGS

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None or _pool.is_closing():
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


_REQUEST_COLUMNS = (
    "id, timestamp, caller_id, tier, model, input_tokens, output_tokens, "
    "cost_usd, latency_ms, success, error, prompt_preview, used_fallback, jev_error"
)


def _build_filters(
    *,
    tier: str | None,
    model: str | None,
    success: bool | None,
    start: Any,
    end: Any,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    def add(clause: str, value: Any) -> None:
        params.append(value)
        clauses.append(clause.format(n=len(params)))

    if tier:
        add("tier = ${n}", tier)
    if model:
        add("model = ${n}", model)
    if success is not None:
        add("success = ${n}", success)
    if start is not None:
        add("timestamp >= ${n}", start)
    if end is not None:
        add("timestamp <= ${n}", end)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def _coerce_cost(row: dict[str, Any]) -> dict[str, Any]:
    """asyncpg returns NUMERIC as Decimal, which pydantic v2 serialises to a
    string. Coerce cost fields to float so API consumers get real numbers."""
    for key in ("cost_usd", "cost"):
        if row.get(key) is not None:
            row[key] = float(row[key])
    return row


async def list_requests(
    *,
    tier: str | None = None,
    model: str | None = None,
    success: bool | None = None,
    start: Any = None,
    end: Any = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated, filterable request log. Returns {rows, total, limit, offset}."""
    where, params = _build_filters(tier=tier, model=model, success=success, start=start, end=end)
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM requests{where}", *params)
        rows = await conn.fetch(
            f"""
            SELECT {_REQUEST_COLUMNS}
            FROM requests{where}
            ORDER BY timestamp DESC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params,
            limit,
            offset,
        )
    return {
        "rows": [_coerce_cost(dict(r)) for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


async def spend_summary(
    *,
    bucket: str = "day",
    start: Any = None,
    end: Any = None,
) -> dict[str, Any]:
    """Aggregated spend: totals, by period bucket, by tier, and by model."""
    if bucket not in {"day", "week", "month"}:
        bucket = "day"
    where, params = _build_filters(tier=None, model=None, success=None, start=start, end=end)
    pool = await get_pool()
    async with pool.acquire() as conn:
        totals = await conn.fetchrow(
            f"""
            SELECT COALESCE(sum(cost_usd), 0) AS cost,
                   count(*) AS requests,
                   COALESCE(sum(input_tokens), 0) AS input_tokens,
                   COALESCE(sum(output_tokens), 0) AS output_tokens
            FROM requests{where}
            """,
            *params,
        )
        by_period = await conn.fetch(
            f"""
            SELECT date_trunc(${len(params) + 1}, timestamp) AS period,
                   COALESCE(sum(cost_usd), 0) AS cost,
                   count(*) AS requests
            FROM requests{where}
            GROUP BY period
            ORDER BY period
            """,
            *params,
            bucket,
        )
        by_tier = await conn.fetch(
            f"""
            SELECT tier,
                   COALESCE(sum(cost_usd), 0) AS cost,
                   count(*) AS requests
            FROM requests{where}
            GROUP BY tier
            ORDER BY cost DESC
            """,
            *params,
        )
        by_model = await conn.fetch(
            f"""
            SELECT model,
                   COALESCE(sum(cost_usd), 0) AS cost,
                   count(*) AS requests
            FROM requests{where}
            GROUP BY model
            ORDER BY cost DESC
            """,
            *params,
        )
    return {
        "bucket": bucket,
        "total_cost": float(totals["cost"]),
        "total_requests": int(totals["requests"]),
        "input_tokens": int(totals["input_tokens"]),
        "output_tokens": int(totals["output_tokens"]),
        "by_period": [_coerce_cost(dict(r)) for r in by_period],
        "by_tier": [_coerce_cost(dict(r)) for r in by_tier],
        "by_model": [_coerce_cost(dict(r)) for r in by_model],
    }


# --- API key management ---------------------------------------------------


async def list_keys() -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, caller_id, api_key, created_at, prompt_preview_enabled
            FROM keys
            ORDER BY created_at
            """
        )
    return [dict(r) for r in rows]


async def create_key(
    *,
    caller_id: str,
    api_key: str,
    prompt_preview_enabled: bool = False,
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO keys (caller_id, api_key, prompt_preview_enabled)
            VALUES ($1, $2, $3)
            RETURNING id, caller_id, api_key, created_at, prompt_preview_enabled
            """,
            caller_id,
            api_key,
            prompt_preview_enabled,
        )
    return dict(row)


async def delete_key(key_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("DELETE FROM keys WHERE id = $1 RETURNING id", key_id)
    return row is not None
