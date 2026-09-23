from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import db
from .config import load_tiers
from .openrouter_admin import fetch_credits, fetch_model_catalog
from .settings_store import (
    CONSERVATISM_PRESETS,
    SettingsValidationError,
    resolve_settings_update,
)
from .tiers_store import TierValidationError, save_tiers

router = APIRouter(prefix="/api", tags=["admin"])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: {value!r}")


# --- Request log ----------------------------------------------------------


@router.get("/requests", dependencies=[Depends(db.verify_api_key)])
async def api_requests(
    tier: str | None = None,
    model: str | None = None,
    success: bool | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return await db.list_requests(
        tier=tier,
        model=model,
        success=success,
        start=_parse_dt(start),
        end=_parse_dt(end),
        limit=limit,
        offset=offset,
    )


# --- Spend -----------------------------------------------------------------


@router.get("/spend", dependencies=[Depends(db.verify_api_key)])
async def api_spend(
    bucket: str = Query(default="day", pattern="^(day|week|month)$"),
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    return await db.spend_summary(bucket=bucket, start=_parse_dt(start), end=_parse_dt(end))


# --- OpenRouter proxy (key never reaches the browser) ----------------------


@router.get("/openrouter/models", dependencies=[Depends(db.verify_api_key)])
async def api_openrouter_models() -> dict[str, Any]:
    try:
        models = await fetch_model_catalog()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"OpenRouter models fetch failed: {exc}")
    return {"data": models}


@router.get("/openrouter/credits", dependencies=[Depends(db.verify_api_key)])
async def api_openrouter_credits() -> dict[str, Any]:
    try:
        return await fetch_credits()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"OpenRouter credits fetch failed: {exc}")


# --- Tier mapping editor ---------------------------------------------------


@router.get("/tiers", dependencies=[Depends(db.verify_api_key)])
async def api_get_tiers() -> dict[str, Any]:
    return load_tiers()


class TiersPayload(BaseModel):
    tiers: dict[str, dict[str, Any]]


@router.put("/tiers", dependencies=[Depends(db.verify_api_key)])
async def api_put_tiers(payload: TiersPayload) -> dict[str, Any]:
    try:
        return save_tiers(payload.tiers)
    except TierValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --- API key management ----------------------------------------------------


@router.get("/keys", dependencies=[Depends(db.verify_api_key)])
async def api_list_keys() -> dict[str, Any]:
    return {"data": await db.list_keys()}


class KeyCreatePayload(BaseModel):
    caller_id: str = Field(min_length=1, max_length=128)
    prompt_preview_enabled: bool | None = None
    api_key: str | None = Field(default=None, min_length=8)


@router.post("/keys", status_code=201, dependencies=[Depends(db.verify_api_key)])
async def api_create_key(payload: KeyCreatePayload) -> dict[str, Any]:
    api_key = payload.api_key or f"sk-router-{secrets.token_urlsafe(32)}"
    preview = payload.prompt_preview_enabled
    if preview is None:
        preview = (await db.get_settings())["prompt_preview_default"]
    try:
        row = await db.create_key(
            caller_id=payload.caller_id,
            api_key=api_key,
            prompt_preview_enabled=preview,
        )
    except Exception as exc:  # unique violation on api_key
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail="API key already exists")
        raise
    return row


@router.delete("/keys/{key_id}", dependencies=[Depends(db.verify_api_key)])
async def api_delete_key(key_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="key id must be a UUID")
    deleted = await db.delete_key(key_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="key not found")
    return {"deleted": key_id}


# --- Model catalog shortlist (enabled/disabled) ---------------------------


@router.get("/models/enabled", dependencies=[Depends(db.verify_api_key)])
async def api_models_enabled() -> dict[str, Any]:
    """Return the model slugs explicitly disabled (everything else is enabled)."""
    return {"disabled": sorted(await db.get_disabled_model_slugs())}


class ModelEnabledPayload(BaseModel):
    model_slug: str = Field(min_length=1, max_length=256)
    enabled: bool


@router.put("/models/enabled", dependencies=[Depends(db.verify_api_key)])
async def api_put_model_enabled(payload: ModelEnabledPayload) -> dict[str, Any]:
    await db.set_model_enabled(payload.model_slug, payload.enabled)
    return {"model_slug": payload.model_slug, "enabled": payload.enabled}


# --- Settings / config panel ----------------------------------------------


@router.get("/settings", dependencies=[Depends(db.verify_api_key)])
async def api_get_settings() -> dict[str, Any]:
    return {"settings": await db.get_settings(), "presets": CONSERVATISM_PRESETS}


class SettingsPayload(BaseModel):
    confidence_gap_threshold: float | None = None
    prompt_preview_default: bool | None = None
    routing_conservatism: str | None = None


@router.put("/settings", dependencies=[Depends(db.verify_api_key)])
async def api_put_settings(payload: SettingsPayload) -> dict[str, Any]:
    try:
        updates = resolve_settings_update(payload.model_dump(exclude_none=True))
    except SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.set_settings(updates)
    return {"settings": await db.get_settings(), "presets": CONSERVATISM_PRESETS}
