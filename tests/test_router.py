from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient

# Test environment must be set before importing app modules.
os.environ.setdefault("OPENROUTER_API_KEY", "test-sk-openrouter")
os.environ.setdefault("ROUTER_API_KEYS", "test-caller:test-sk-router")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_DB", "jev_router_test")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")

from app.config import load_tiers, tier_to_model
from app.decision import Decision, TierClassifier
from app.fallback import fallback_classify, flatten_messages
from app.main import create_app


@pytest_asyncio.fixture
async def client(monkeypatch) -> AsyncIterator[AsyncClient]:
    app = create_app(init_db_on_startup=False)

    async def _fake_verify(request: Request):
        return "test-caller", False

    from app import db

    monkeypatch.setattr(db, "init_db", lambda: None)
    app.dependency_overrides[db.verify_api_key] = _fake_verify

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def test_load_tiers():
    tiers = load_tiers()
    assert set(tiers.keys()) == {"simple", "medium", "complex"}
    assert tiers["simple"]["model"] == "openai/gpt-4o-mini"
    assert tiers["simple"]["context_length"] == 128000
    assert tier_to_model("simple") == tiers["simple"]["model"]
    assert tier_to_model("unknown") == tiers["medium"]["model"]


def test_flatten_messages():
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "what is 2+2"},
    ]
    text = flatten_messages(messages)
    assert "SYS" in text
    assert "CURRENT: what is 2+2" in text

    single = [{"role": "user", "content": "hello"}]
    assert flatten_messages(single) == "hello"


@pytest.mark.parametrize(
    "probs,expected",
    [
        ({"simple": 0.48, "medium": 0.42, "complex": 0.10}, "medium"),
        ({"simple": 0.80, "medium": 0.10, "complex": 0.10}, "simple"),
        ({"medium": 0.55, "complex": 0.44, "simple": 0.01}, "complex"),
        ({"simple": 0.30, "medium": 0.40, "complex": 0.30}, "complex"),
    ],
)
def test_confidence_resolution(probs, expected):
    choice = max(probs, key=probs.get)
    assert TierClassifier._resolve_with_confidence(probs, choice) == expected


@pytest.mark.anyio
async def test_classify_populates_transparency_metadata(monkeypatch):
    from app import decision

    class FakeResp:
        status_code = 200

        @property
        def text(self) -> str:
            return ""

        def json(self) -> dict:
            return {
                "answers": {
                    "tier": {
                        "choice": "simple",
                        "probabilities": {"simple": 0.48, "medium": 0.42, "complex": 0.10},
                    }
                }
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return FakeResp()

    monkeypatch.setattr(decision.httpx, "AsyncClient", FakeClient)

    result = await decision.TierClassifier(confidence_gap=0.15).classify("hello")

    assert result.used_fallback is False
    # gap 0.48-0.42 = 0.06 < 0.15 -> escalate simple -> medium.
    assert result.tier == "medium"
    assert result.confidence == pytest.approx(0.48)
    assert result.probability_gap == pytest.approx(0.06)
    assert result.escalation_fired is True
    assert result.probabilities["simple"] == pytest.approx(0.48)
    assert result.jev_error is None


def test_fallback_classifier():
    assert fallback_classify("what is 2+2") == "simple"
    assert (
        fallback_classify("Refactor this multi-file codebase to use async/await")
        == "complex"
    )
    long_prompt = " ".join(["explain"] * 120)
    assert fallback_classify(long_prompt) == "complex"
    assert fallback_classify("Write a short Python function to sort a list") == "simple"
    assert fallback_classify("Create a REST API with authentication and rate limiting") == "medium"


@pytest.mark.anyio
async def test_list_models(client: AsyncClient):
    r = await client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert len(data["data"]) == 3
    assert min(m["context_length"] for m in data["data"]) == 128000


@pytest.mark.anyio
async def test_chat_completions_non_stream_shape(client: AsyncClient, monkeypatch):
    from app import main

    captured = {}

    async def _fake_classify(prompt, confidence_gap=None):
        return Decision(
            tier="simple",
            used_fallback=False,
            jev_error=None,
            confidence=0.8,
            probability_gap=0.7,
            probabilities={"simple": 0.8, "medium": 0.1, "complex": 0.1},
            escalation_fired=False,
        )

    async def _fake_get_settings():
        return {"confidence_gap_threshold": 0.15}

    async def _fake_non_stream(*, model, messages, **kwargs):
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "4"}, "finish_reason": "stop"}
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 1,
                "total_tokens": 11,
                "cost": 0.000005,
            },
        }

    async def _fake_log(**kwargs):
        captured["log"] = kwargs
        return 1

    monkeypatch.setattr(main, "classify_tier", _fake_classify)
    monkeypatch.setattr(main, "get_settings", _fake_get_settings)
    monkeypatch.setattr(main, "non_stream_completion", _fake_non_stream)
    monkeypatch.setattr(main, "log_request", _fake_log)

    r = await client.post(
        "/v1/chat/completions",
        json={"model": "router-default", "messages": [{"role": "user", "content": "what is 2+2"}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "4"
    assert body["usage"]["cost"] == 0.000005
    assert captured["log"]["tier"] == "simple"
    assert captured["log"]["model"] == "openai/gpt-4o-mini"
    assert captured["log"]["cost_usd"] == 0.000005
    assert captured["log"]["success"] is True
    assert captured["log"]["used_fallback"] is False
    assert captured["log"]["confidence"] == 0.8
    assert captured["log"]["probability_gap"] == 0.7
    assert captured["log"]["probabilities"]["simple"] == 0.8
    assert captured["log"]["escalation_fired"] is False


@pytest.mark.anyio
async def test_chat_completions_stream_shape(client: AsyncClient, monkeypatch):
    from app import main

    captured = {}

    async def _fake_classify(prompt, confidence_gap=None):
        return Decision(
            tier="complex",
            used_fallback=True,
            jev_error="forced fallback",
            confidence=None,
            probability_gap=None,
            probabilities=None,
            escalation_fired=False,
        )

    async def _fake_get_settings():
        return {"confidence_gap_threshold": 0.15}

    async def _fake_stream(*, model, messages, **kwargs):
        chunk = {
            "id": "chatcmpl-chunk",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hi"}}],
        }
        yield f"data: {json.dumps(chunk)}\n\n", None
        usage = {"prompt_tokens": 5, "completion_tokens": 1, "cost": 0.0001}
        yield f"data: {json.dumps({'usage': usage})}\n\n", usage

    async def _fake_log(**kwargs):
        captured["log"] = kwargs
        return 1

    monkeypatch.setattr(main, "classify_tier", _fake_classify)
    monkeypatch.setattr(main, "get_settings", _fake_get_settings)
    monkeypatch.setattr(main, "stream_completion", _fake_stream)
    monkeypatch.setattr(main, "log_request", _fake_log)

    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "router-default",
            "messages": [{"role": "user", "content": "big refactor"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        chunks = []
        async for line in r.aiter_text():
            chunks.append(line)
        text = "".join(chunks)
        assert "chat.completion.chunk" in text
        assert "Hi" in text

    assert captured["log"]["tier"] == "complex"
    assert captured["log"]["used_fallback"] is True
    assert captured["log"]["jev_error"] == "forced fallback"
    assert captured["log"]["cost_usd"] == 0.0001
    assert captured["log"]["success"] is True


@pytest.mark.anyio
async def test_unauthorized_when_auth_enabled(monkeypatch):
    real_app = create_app(init_db_on_startup=False)

    async def _fake_init() -> None:
        return None

    from app import db

    monkeypatch.setattr(db, "init_db", _fake_init)

    async with AsyncClient(transport=ASGITransport(app=real_app), base_url="http://test") as ac:
        r = await ac.get("/v1/models")
    assert r.status_code == 401
