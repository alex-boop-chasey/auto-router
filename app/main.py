from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import admin_api
from .cache import init_cache, set_cached
from .cache import make_and_check_cache as check_cache
from .config import PROJECT_ROOT, SETTINGS, load_tiers, tier_to_model
from .db import (
    get_enabled_models_for_routing,
    get_fallback_model,
    get_settings,
    init_db,
    last_successful_decision_at,
    log_request,
    verify_api_key,
)
from .decision import aclose_jev_client, classify_tier
from .fallback import flatten_messages
from .key_manager import check_key_budget, record_spend, set_budget_pool
from .logger import logger
from .metrics import (
    record_cache_hit,
    record_cache_miss,
    record_jev_decision,
    record_request,
)
from .prompt_cleaner import aclose_compactor_client, basic_clean
from .proxy import non_stream_completion, stream_completion
from .rate_limiter import check_rate_limit, init_rate_limiter

router = APIRouter()
STATIC_DIR = PROJECT_ROOT / "app" / "static"


class ChatMessage(BaseModel):
    role: str
    content: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    extra_body: dict[str, Any] | None = None


def create_app(init_db_on_startup: bool = True) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        if init_db_on_startup:
            await init_db()
        from .db import get_pool
        pool = await get_pool()
        set_budget_pool(pool)
        init_rate_limiter()
        init_cache()
        yield
        await aclose_jev_client()
        await aclose_compactor_client()

    app = FastAPI(title="auto-router", version="0.1.0", lifespan=_lifespan)
    app.include_router(router)
    app.include_router(admin_api.router)
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()


@router.get("/health")
async def health() -> dict[str, Any]:
    last_at: datetime | None = None
    try:
        last_at = await last_successful_decision_at()
    except Exception:  # noqa: BLE001 — health must degrade gracefully if the DB is unreachable
        last_at = None
    return {
        "status": "ok",
        "last_successful_decision_at": last_at.isoformat() if last_at else None,
    }


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    from .metrics import metrics_response
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type)


@router.get("/", include_in_schema=False)
async def ui_index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@router.get("/v1/models", dependencies=[Depends(verify_api_key)])
async def list_models() -> dict[str, Any]:
    try:
        rows = await get_enabled_models_for_routing()
    except Exception:
        # Fall back to tiers.json if the models table doesn't exist yet
        tiers = load_tiers()
        rows = [{"openrouter_model_id": cfg["model"], "context_length": cfg["context_length"]} for cfg in tiers.values()]
    data = [
        {
            "id": row["openrouter_model_id"],
            "object": "model",
            "owned_by": "auto-router",
            "context_length": row.get("context_length", 128000),
        }
        for row in rows
    ]
    return {"object": "list", "data": data}


@router.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    request: Request,
    auth: tuple[str, bool] = Depends(verify_api_key),
) -> JSONResponse | StreamingResponse:
    caller_id, preview_enabled = auth

    body = await request.json()
    try:
        req = ChatCompletionRequest(**body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid request body: {exc}")

    if not req.messages:
        raise HTTPException(status_code=400, detail="messages are required")

    messages = [{"role": m.role, "content": m.content or ""} for m in req.messages]
    prompt_text = flatten_messages(messages)

    # --- Rate limit check (must pass before any model work) ---
    rate_ok, rate_detail = await check_rate_limit(caller_id)
    if not rate_ok:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. {rate_detail}",
        )

    # --- Budget check (must pass before any spend) ---
    budget_ok, budget_remaining = await check_key_budget(caller_id)
    if not budget_ok:
        raise HTTPException(
            status_code=402,
            detail=f"Budget exceeded. Remaining: ${budget_remaining:.4f}",
        )

    # --- Cache check (skip Jev + model if we have it) ---
    import json as _json_cache
    cache_key, cached_raw = await check_cache(req.model, messages, req.temperature)
    if cached_raw is not None:
        record_cache_hit()
        return JSONResponse(content=_json_cache.loads(cached_raw))

    record_cache_miss()

    preview: str | None = None
    if preview_enabled:
        preview = prompt_text[: SETTINGS.prompt_preview_length]

    start_ns = _time_ns()
    settings = await get_settings()

    # --- Prompt cleaning pipeline ---
    clean_meta: dict[str, Any] = {}
    if settings.get("prompt_cleaning_enabled", True):
        # Layer 1: basic regex clean (always runs if enabled)
        cleaned_text = basic_clean(prompt_text)
        if cleaned_text != prompt_text:
            clean_meta["basic_cleaned"] = True
            prompt_text = cleaned_text

        # Layer 2: smart compaction (toggleable, gated by length)
        if (
            settings.get("prompt_compaction_enabled", False)
            and len(prompt_text) >= settings.get("prompt_compaction_min_chars", 500)
        ):
            from .config import SETTINGS as _SETTINGS
            from .prompt_cleaner import smart_compact

            try:
                compacted = await smart_compact(
                    prompt_text,
                    model=settings["prompt_compaction_model"],
                    api_key=_SETTINGS.openrouter_api_key,
                )
                if compacted and len(compacted) < len(prompt_text) * 0.95:
                    clean_meta["compacted"] = True
                    clean_meta["compaction_model"] = settings["prompt_compaction_model"]
                    clean_meta["original_length"] = len(prompt_text)
                    clean_meta["compacted_length"] = len(compacted)
                    clean_meta["compression_ratio"] = round(
                        len(compacted) / max(len(prompt_text), 1), 3
                    )
                    prompt_text = compacted
            except Exception:
                clean_meta["compaction_error"] = True

    # --- Load models for Jev classification (DB-driven, not tiers.json) ---
    enabled_models = await get_enabled_models_for_routing()
    if not enabled_models:
        raise HTTPException(status_code=503, detail="No enabled models configured")

    choices: dict[str, str] = {
        m["openrouter_model_id"]: m["description"] or m["display_name"]
        for m in enabled_models
    }
    fallback_row = await get_fallback_model()
    fallback_model_id = fallback_row["openrouter_model_id"] if fallback_row else "openai/gpt-4o-mini"

    decision = await classify_tier(
        prompt_text,
        confidence_gap=settings["confidence_gap_threshold"],
        choices=choices,
        fallback_model_id=fallback_model_id,
    )
    tier = decision.tier  # in dynamic mode, tier IS the model_id
    used_fallback = decision.used_fallback
    jev_error = decision.jev_error
    model = decision.tier  # model_id directly from Jev choice

    # Structured JSON log of the Jev decision
    logger.jev_decision(
        caller_id=caller_id,
        tier=tier,
        confidence=decision.confidence,
        gap=decision.probability_gap,
        latency_ms=decision.decision_latency_ms,
        success=not decision.used_fallback,
        error=decision.jev_error,
    )
    record_jev_decision(tier, not decision.used_fallback, decision.decision_latency_ms / 1000.0)
    if decision.used_fallback:
        logger.fallback(caller_id=caller_id, reason=decision.jev_error or "unknown", tier=tier)
    if decision.escalation_fired:
        logger.escalation(
            caller_id=caller_id,
            original_tier=decision.jev_raw_choice or "unknown",
            escalated_tier=tier,
            confidence=decision.confidence or 0,
            gap=decision.probability_gap or 0,
        )

    if req.stream:
        return StreamingResponse(
            _streamed_response(
                caller_id=caller_id,
                tier=tier,
                model=model,
                messages=messages,
                preview=preview,
                used_fallback=used_fallback,
                jev_error=jev_error,
                confidence=decision.confidence,
                probability_gap=decision.probability_gap,
                probabilities=decision.probabilities,
                escalation_fired=decision.escalation_fired,
                started_ns=start_ns,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                top_p=req.top_p,
                extra_body=req.extra_body,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Router-Tier": tier,
                "X-Router-Model": model,
            },
        )

    try:
        result = await non_stream_completion(
            model=model,
            messages=messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            top_p=req.top_p,
            extra_body=req.extra_body,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        latency_ms = (_time_ns() - start_ns) // 1_000_000
        await _persist_log(
            caller_id=caller_id,
            tier=tier,
            model=model,
            usage=None,
            latency_ms=latency_ms,
            success=False,
            error=error_msg,
            preview=preview,
            used_fallback=used_fallback,
            jev_error=jev_error,
            confidence=decision.confidence,
            probability_gap=decision.probability_gap,
            probabilities=decision.probabilities,
            escalation_fired=decision.escalation_fired,
        )
        logger.error(
            caller_id=caller_id,
            error=error_msg,
            tier=tier,
            model=model,
            latency_ms=latency_ms,
        )
        raise HTTPException(status_code=502, detail=error_msg)

    usage = result.get("usage")
    latency_ms = (_time_ns() - start_ns) // 1_000_000

    # Everything below (request-log insert, spend update, response cache
    # write, structured log line, metrics) is bookkeeping that has nothing to
    # do with the answer the caller is waiting on. It used to run inline
    # here, adding a DB insert + a DB update + a Redis write to every single
    # response's latency. It now runs as a Starlette BackgroundTask *after*
    # the response bytes have already gone out, so callers only wait on the
    # model call itself. Trade-off: if the process dies in the brief window
    # between "response sent" and "background task ran", that one request's
    # log/spend/cache write is lost — an acceptable, standard trade-off for
    # telemetry that's normally never observed.
    background = BackgroundTasks()
    background.add_task(
        _finalize_success,
        caller_id=caller_id,
        tier=tier,
        model=model,
        usage=usage,
        latency_ms=latency_ms,
        preview=preview,
        used_fallback=used_fallback,
        jev_error=jev_error,
        confidence=decision.confidence,
        probability_gap=decision.probability_gap,
        probabilities=decision.probabilities,
        escalation_fired=decision.escalation_fired,
        cache_key=cache_key,
        result=result,
    )
    return JSONResponse(content=result, background=background)


async def _finalize_success(
    *,
    caller_id: str,
    tier: str,
    model: str,
    usage: dict[str, Any] | None,
    latency_ms: int,
    preview: str | None,
    used_fallback: bool,
    jev_error: str | None,
    confidence: float | None,
    probability_gap: float | None,
    probabilities: dict[str, float] | None,
    escalation_fired: bool,
    cache_key: str,
    result: dict[str, Any],
) -> None:
    """Post-response bookkeeping for a successful non-streaming completion.

    Runs as a BackgroundTask after the response has already been sent (see
    chat_completions above) — none of this should ever add to client-visible
    latency. Each step is independently guarded so one failure (e.g. a
    transient DB blip) can't swallow the others.
    """
    try:
        await _persist_log(
            caller_id=caller_id,
            tier=tier,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
            success=True,
            error=None,
            preview=preview,
            used_fallback=used_fallback,
            jev_error=jev_error,
            confidence=confidence,
            probability_gap=probability_gap,
            probabilities=probabilities,
            escalation_fired=escalation_fired,
        )
    except Exception as exc:  # noqa: BLE001 — background task, must not raise
        logger.error(
            caller_id=caller_id,
            error=f"post-response log_request failed: {exc}",
            tier=tier,
            model=model,
            latency_ms=latency_ms,
        )

    logger.request(
        caller_id=caller_id,
        tier=tier,
        model=model,
        input_tokens=usage.get("prompt_tokens", 0) if usage else 0,
        output_tokens=usage.get("completion_tokens", 0) if usage else 0,
        cost_usd=float(usage.get("cost", 0)) if usage else 0.0,
        latency_ms=latency_ms,
        stream=False,
        used_fallback=used_fallback,
        escalation_fired=escalation_fired,
        confidence=confidence,
        probability_gap=probability_gap,
    )
    record_request(
        caller_id=caller_id,
        tier=tier,
        model=model,
        success=True,
        latency_s=latency_ms / 1000.0,
        input_tokens=usage.get("prompt_tokens", 0) if usage else 0,
        output_tokens=usage.get("completion_tokens", 0) if usage else 0,
        cost_usd=float(usage.get("cost", 0)) if usage else 0.0,
    )

    cost = float(usage.get("cost", 0)) if usage else 0.0
    try:
        await record_spend(caller_id, cost)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            caller_id=caller_id,
            error=f"post-response record_spend failed: {exc}",
            tier=tier,
            model=model,
            latency_ms=latency_ms,
        )

    try:
        await set_cached(cache_key, json.dumps(result).encode())
    except Exception:  # noqa: BLE001 — cache.set_cached already swallows its own errors; belt and suspenders
        pass


async def _streamed_response(
    *,
    caller_id: str,
    tier: str,
    model: str,
    messages: list[dict[str, Any]],
    preview: str | None,
    used_fallback: bool,
    jev_error: str | None,
    confidence: float | None,
    probability_gap: float | None,
    probabilities: dict[str, float] | None,
    escalation_fired: bool,
    started_ns: int,
    temperature: float | None,
    max_tokens: int | None,
    top_p: float | None,
    extra_body: dict[str, Any] | None,
):
    usage: dict[str, Any] | None = None
    try:
        async for sse_text, final_usage in stream_completion(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            extra_body=extra_body,
        ):
            if sse_text:
                yield sse_text
            if final_usage:
                usage = final_usage
    except HTTPException:
        raise
    except RuntimeError as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        yield f"data: {json.dumps({'error': error_msg})}\n\n"
        await _persist_log(
            caller_id=caller_id,
            tier=tier,
            model=model,
            usage=usage,
            latency_ms=(_time_ns() - started_ns) // 1_000_000,
            success=False,
            error=error_msg,
            preview=preview,
            used_fallback=used_fallback,
            jev_error=jev_error,
            confidence=confidence,
            probability_gap=probability_gap,
            probabilities=probabilities,
            escalation_fired=escalation_fired,
        )
        logger.error(
            caller_id=caller_id,
            error=error_msg,
            tier=tier,
            model=model,
            latency_ms=(_time_ns() - started_ns) // 1_000_000,
        )
        return

    await _persist_log(
        caller_id=caller_id,
        tier=tier,
        model=model,
        usage=usage,
        latency_ms=(_time_ns() - started_ns) // 1_000_000,
        success=True,
        error=None,
        preview=preview,
        used_fallback=used_fallback,
        jev_error=jev_error,
        confidence=confidence,
        probability_gap=probability_gap,
        probabilities=probabilities,
        escalation_fired=escalation_fired,
    )
    logger.request(
        caller_id=caller_id,
        tier=tier,
        model=model,
        input_tokens=usage.get("prompt_tokens", 0) if usage else 0,
        output_tokens=usage.get("completion_tokens", 0) if usage else 0,
        cost_usd=float(usage.get("cost", 0)) if usage else 0.0,
        latency_ms=(_time_ns() - started_ns) // 1_000_000,
        stream=True,
        used_fallback=used_fallback,
        escalation_fired=escalation_fired,
        confidence=confidence,
        probability_gap=probability_gap,
    )
    record_request(
        caller_id=caller_id,
        tier=tier,
        model=model,
        success=True,
        latency_s=((_time_ns() - started_ns) // 1_000_000) / 1000.0,
        input_tokens=usage.get("prompt_tokens", 0) if usage else 0,
        output_tokens=usage.get("completion_tokens", 0) if usage else 0,
        cost_usd=float(usage.get("cost", 0)) if usage else 0.0,
    )
    cost = float(usage.get("cost", 0)) if usage else 0.0
    await record_spend(caller_id, cost)


async def _persist_log(
    *,
    caller_id: str,
    tier: str,
    model: str,
    usage: dict[str, Any] | None,
    latency_ms: int,
    success: bool,
    error: str | None,
    preview: str | None,
    used_fallback: bool,
    jev_error: str | None,
    confidence: float | None = None,
    probability_gap: float | None = None,
    probabilities: dict[str, float] | None = None,
    escalation_fired: bool = False,
) -> None:
    await log_request(
        caller_id=caller_id,
        tier=tier,
        model=model,
        input_tokens=usage.get("prompt_tokens", 0) if usage else 0,
        output_tokens=usage.get("completion_tokens", 0) if usage else 0,
        cost_usd=float(usage.get("cost", 0)) if usage else 0.0,
        latency_ms=latency_ms,
        success=success,
        error=error,
        prompt_preview=preview,
        used_fallback=used_fallback,
        jev_error=jev_error,
        confidence=confidence,
        probability_gap=probability_gap,
        probabilities=probabilities,
        escalation_fired=escalation_fired,
    )


def _time_ns() -> int:
    from time import time_ns

    return time_ns()
