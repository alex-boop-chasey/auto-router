from __future__ import annotations

from typing import Any

import httpx

from .config import SETTINGS

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SETTINGS.openrouter_api_key}",
        "HTTP-Referer": "https://auto-router.local/",
        "X-Title": "auto-router",
    }


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
