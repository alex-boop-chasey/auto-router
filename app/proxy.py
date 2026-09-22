from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.encoders import jsonable_encoder
from openai import AsyncOpenAI

from .config import SETTINGS

_CLIENT: AsyncOpenAI | None = None


def get_openrouter_client() -> AsyncOpenAI:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=SETTINGS.openrouter_api_key,
            default_headers={
                "HTTP-Referer": "https://auto-router.local/",
                "X-Title": "auto-router",
            },
        )
    return _CLIENT


def _json_dump(obj: Any) -> str:
    return json.dumps(jsonable_encoder(obj), separators=(",", ":"))


async def stream_completion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    extra_body: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any] | None]]:
    """Yield SSE text chunks and a final usage object."""
    client = get_openrouter_client()
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if top_p is not None:
        kwargs["top_p"] = top_p
    if extra_body:
        kwargs["extra_body"] = extra_body

    usage: dict[str, Any] | None = None
    stream = await client.chat.completions.create(**kwargs)
    try:
        async for chunk in stream:
            data = chunk.model_dump() if hasattr(chunk, "model_dump") else chunk
            if data.get("usage"):
                usage = data["usage"]
            yield f"data: {_json_dump(data)}\n\n", None
    finally:
        await stream.close()

    stop_payload: dict[str, Any] = {
        "id": "router-stop",
        "object": "chat.completion.chunk",
        "choices": [],
    }
    if usage:
        stop_payload["usage"] = usage
    yield f"data: {_json_dump(stop_payload)}\n\n", usage


async def non_stream_completion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = get_openrouter_client()
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if top_p is not None:
        kwargs["top_p"] = top_p
    if extra_body:
        kwargs["extra_body"] = extra_body

    response = await client.chat.completions.create(**kwargs)
    return response.to_dict()
