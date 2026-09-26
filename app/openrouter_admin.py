from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import SETTINGS

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_FRONTEND_BASE = "https://openrouter.ai/api/frontend/v1"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SETTINGS.openrouter_api_key}",
        "HTTP-Referer": "https://auto-router.local/",
        "X-Title": "auto-router",
    }


async def _get_json(url: str) -> Any:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def fetch_model_catalog() -> list[dict[str, Any]]:
    """Return OpenRouter's public model catalog (GET /api/v1/models).

    No special auth tier required. Used to populate the tier-mapping editor so
    model slugs are picked from a live list rather than typed from memory.
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{OPENROUTER_BASE}/models", headers=_headers())
        resp.raise_for_status()
        body = resp.json()
    models = body.get("data", [])
    # Trim to the fields the UI needs.
    return [
        {
            "id": m.get("id"),
            "name": m.get("name"),
            "created": m.get("created"),
            "context_length": m.get("context_length"),
            "pricing": m.get("pricing"),
        }
        for m in models
        if m.get("id")
    ]


async def fetch_credits() -> dict[str, Any]:
    """Return the account's credit balance (GET /api/v1/credits).

    Response shape: {"data": {"total_credits": N, "total_usage": N}}.
    Balance = total_credits - total_usage.

    Confirmed against Alex's standard request-signing key (sk-or-v1...): the
    endpoint returns HTTP 200 and does NOT require a management key.
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{OPENROUTER_BASE}/credits", headers=_headers())
        resp.raise_for_status()
        body = resp.json()
    data = body.get("data", {}) or {}
    total_credits = float(data.get("total_credits", 0) or 0)
    total_usage = float(data.get("total_usage", 0) or 0)
    return {
        "total_credits": total_credits,
        "total_usage": total_usage,
        "balance": total_credits - total_usage,
    }


async def fetch_rankings_tokens() -> dict[str, int]:
    """Aggregate token volume (prompt + completion) per model slug across the
    trailing days returned by OpenRouter's usage leaderboard.

    Feeds the "Most Popular" / "Top Weekly" / "Weekly Tokens" sorts.
    """
    try:
        body = await _get_json(f"{OPENROUTER_FRONTEND_BASE}/rankings/models")
    except httpx.HTTPError:
        return {}
    tokens: dict[str, int] = {}
    for row in body.get("data", []):
        slug = row.get("model_permaslug")
        if not slug:
            continue
        try:
            prompt = int(row.get("total_prompt_tokens") or 0)
            completion = int(row.get("total_completion_tokens") or 0)
        except (TypeError, ValueError):
            prompt = completion = 0
        tokens[slug] = tokens.get(slug, 0) + prompt + completion
    return tokens


async def fetch_rankings_performance() -> dict[str, dict[str, Any]]:
    """p50 throughput / p50 latency / request count per model slug.

    Feeds the "Throughput" and "Latency" sorts.
    """
    try:
        body = await _get_json(f"{OPENROUTER_FRONTEND_BASE}/rankings/performance")
    except httpx.HTTPError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in body.get("data", []):
        slug = row.get("slug") or row.get("id")
        if not slug:
            continue
        out[slug] = {
            "p50_throughput": row.get("p50_throughput"),
            "p50_latency": row.get("p50_latency"),
            "request_count": row.get("request_count"),
        }
    return out


async def fetch_benchmarks() -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
    """Return (ai_metrics_by_slug, design_arena_elo_by_slug).

    ai_metrics: intelligence / coding / agentic index (artificial-analysis).
    design_arena_elo: mean ELO across the model's design-arena categories.
    """
    try:
        body = await _get_json(f"{OPENROUTER_BASE}/benchmarks")
    except httpx.HTTPError:
        return {}, {}
    ai: dict[str, dict[str, Any]] = {}
    da: dict[str, list[float]] = {}
    for row in body.get("data", []):
        slug = row.get("model_permaslug")
        if not slug:
            continue
        source = row.get("source")
        if source == "artificial-analysis":
            ai[slug] = {
                "intelligence_index": row.get("intelligence_index"),
                "coding_index": row.get("coding_index"),
                "agentic_index": row.get("agentic_index"),
            }
        elif source == "design-arena":
            elo = row.get("elo")
            if elo is not None:
                da.setdefault(slug, []).append(float(elo))
    design = {slug: sum(vals) / len(vals) for slug, vals in da.items() if vals}
    return ai, design


async def fetch_model_catalog_enriched() -> list[dict[str, Any]]:
    """Full catalog with sort metrics (benchmarks, usage, performance) merged.

    Adds per-model keys consumed by the admin UI sort dropdown:
      created, price_usd, discount, weekly_tokens, request_count,
      p50_throughput, p50_latency,
      intelligence_index, coding_index, agentic_index, design_arena_elo.

    Enrichment sources are intentionally best-effort: if any of the three
    secondary endpoints are down, the catalog still renders with those sort
    keys left null (nulls sort to the bottom).
    """
    models = await fetch_model_catalog()
    (ai, design), tokens, perf = await asyncio.gather(
        fetch_benchmarks(), fetch_rankings_tokens(), fetch_rankings_performance()
    )
    for m in models:
        slug = m.get("id") or ""
        ai_metrics = ai.get(slug) or {}
        m["intelligence_index"] = ai_metrics.get("intelligence_index")
        m["coding_index"] = ai_metrics.get("coding_index")
        m["agentic_index"] = ai_metrics.get("agentic_index")
        m["design_arena_elo"] = design.get(slug)
        m["weekly_tokens"] = tokens.get(slug, 0)
        perf_metrics = perf.get(slug) or {}
        m["p50_throughput"] = perf_metrics.get("p50_throughput")
        m["p50_latency"] = perf_metrics.get("p50_latency")
        m["request_count"] = perf_metrics.get("request_count")

        pricing = m.get("pricing") or {}
        # Combined input + output price (USD per token) for the pricing sorts.
        # OpenRouter uses "-1" for variable/unavailable pricing — treat those
        # as null so they don't surface as artificially cheap.
        try:
            prompt_price = float(pricing.get("prompt") or 0)
            completion_price = float(pricing.get("completion") or 0)
            if prompt_price < 0 or completion_price < 0:
                m["price_usd"] = None
            else:
                m["price_usd"] = prompt_price + completion_price
        except (TypeError, ValueError):
            m["price_usd"] = None
        # "Discount" approximation: how much cheaper cached input is vs full
        # price (fraction 0..1). OpenRouter's promotional-discount flag isn't
        # exposed via a stable API, so this proxies it with the cache discount.
        try:
            prompt_price = float(pricing.get("prompt") or 0)
            cache = float(pricing.get("input_cache_read") or 0)
            m["discount"] = (prompt_price - cache) / prompt_price if (prompt_price > 0 and "input_cache_read" in pricing) else None
        except (TypeError, ValueError):
            m["discount"] = None
    return models