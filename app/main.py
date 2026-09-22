from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import SETTINGS, load_tiers, tier_to_model
from .db import init_db, log_request, verify_api_key
from .decision import classify_tier
from .fallback import flatten_messages
from .proxy import non_stream_completion, stream_completion

router = APIRouter()


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
        yield

    app = FastAPI(title="auto-router", version="0.1.0", lifespan=_lifespan)
    app.include_router(router)
    return app


app = create_app()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/models", dependencies=[Depends(verify_api_key)])
async def list_models() -> dict[str, Any]:
    tiers = load_tiers()
    data = []
    for tier, cfg in tiers.items():
        data.append(
            {
                "id": cfg["model"],
                "object": "model",
                "owned_by": "auto-router",
                "context_length": cfg["context_length"],
                "tier": tier,
            }
        )
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

    preview: str | None = None
    if preview_enabled:
        preview = prompt_text[: SETTINGS.prompt_preview_length]

    start_ns = _time_ns()
    tier, used_fallback, jev_error = await classify_tier(prompt_text)
    model = tier_to_model(tier)

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
        await _persist_log(
            caller_id=caller_id,
            tier=tier,
            model=model,
            usage=None,
            latency_ms=(_time_ns() - start_ns) // 1_000_000,
            success=False,
            error=error_msg,
            preview=preview,
            used_fallback=used_fallback,
            jev_error=jev_error,
        )
        raise HTTPException(status_code=502, detail=error_msg)

    usage = result.get("usage")
    latency_ms = (_time_ns() - start_ns) // 1_000_000
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
    )
    return JSONResponse(content=result)


async def _streamed_response(
    *,
    caller_id: str,
    tier: str,
    model: str,
    messages: list[dict[str, Any]],
    preview: str | None,
    used_fallback: bool,
    jev_error: str | None,
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
    )


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
    )


def _time_ns() -> int:
    from time import time_ns

    return time_ns()
