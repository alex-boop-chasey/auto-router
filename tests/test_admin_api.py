from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("OPENROUTER_API_KEY", "test-sk-openrouter")
os.environ.setdefault("ROUTER_API_KEYS", "test-caller:test-sk-router")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_DB", "jev_router_test")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")

from app import admin_api, db
from app.main import create_app
from app.tiers_store import TierValidationError, save_tiers, validate_tiers


@pytest_asyncio.fixture
async def client(monkeypatch) -> AsyncIterator[AsyncClient]:
    app = create_app(init_db_on_startup=False)

    async def _fake_verify(request: Request):
        return "test-caller", False

    monkeypatch.setattr(db, "init_db", lambda: None)
    app.dependency_overrides[db.verify_api_key] = _fake_verify

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# --- tiers_store unit tests (real file IO, isolated to tmp_path) ----------


def test_validate_tiers_rejects_missing_tier():
    with pytest.raises(TierValidationError):
        validate_tiers({"simple": {"model": "x", "context_length": 1}})


def test_validate_tiers_rejects_bad_context_length():
    with pytest.raises(TierValidationError):
        validate_tiers(
            {
                "simple": {"model": "openai/gpt-4o-mini", "context_length": 0},
                "medium": {"model": "m", "context_length": 1},
                "complex": {"model": "c", "context_length": 1},
            }
        )


def test_save_tiers_roundtrip(tmp_path):
    path = tmp_path / "tiers.json"
    path.write_text("{}")
    saved = save_tiers(
        {
            "simple": {"model": "openai/gpt-4o-mini", "context_length": 128000},
            "medium": {"model": "anthropic/claude-sonnet-4", "context_length": 200000},
            "complex": {"model": "anthropic/claude-opus-4", "context_length": 200000},
        },
        path=path,
    )
    assert saved["complex"]["model"] == "anthropic/claude-opus-4"
    import json

    on_disk = json.loads(path.read_text())
    assert on_disk["complex"]["model"] == "anthropic/claude-opus-4"
    # No leftover tmp file.
    assert not (tmp_path / "tiers.json.tmp").exists()


# --- route wiring ---------------------------------------------------------


@pytest.mark.anyio
async def test_get_requests(client: AsyncClient, monkeypatch):
    captured = {}

    async def _fake_list(**kwargs):
        captured.update(kwargs)
        return {"rows": [{"id": 1, "tier": "simple", "cost_usd": 0.000007}], "total": 1, "limit": 50, "offset": 0}

    monkeypatch.setattr(db, "list_requests", _fake_list)
    r = await client.get("/api/requests?tier=simple&success=true&limit=10")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert captured["tier"] == "simple"
    assert captured["success"] is True
    assert captured["limit"] == 10


@pytest.mark.anyio
async def test_get_requests_rejects_bad_date(client: AsyncClient):
    r = await client.get("/api/requests?start=not-a-date")
    assert r.status_code == 400


@pytest.mark.anyio
async def test_get_spend(client: AsyncClient, monkeypatch):
    async def _fake_spend(**kwargs):
        return {"bucket": kwargs["bucket"], "total_cost": 0.12, "by_tier": [], "by_model": [], "by_period": []}

    monkeypatch.setattr(db, "spend_summary", _fake_spend)
    r = await client.get("/api/spend?bucket=week")
    assert r.status_code == 200
    assert r.json()["bucket"] == "week"


@pytest.mark.anyio
async def test_openrouter_models_proxy(client: AsyncClient, monkeypatch):
    async def _fake_models():
        return [{"id": "openai/gpt-4o-mini", "context_length": 128000}]

    monkeypatch.setattr(admin_api, "fetch_model_catalog", _fake_models)
    r = await client.get("/api/openrouter/models")
    assert r.status_code == 200
    assert r.json()["data"][0]["id"] == "openai/gpt-4o-mini"


@pytest.mark.anyio
async def test_openrouter_credits_proxy(client: AsyncClient, monkeypatch):
    async def _fake_credits():
        return {"total_credits": 202.0, "total_usage": 62.61, "balance": 139.39}

    monkeypatch.setattr(admin_api, "fetch_credits", _fake_credits)
    r = await client.get("/api/openrouter/credits")
    assert r.status_code == 200
    assert r.json()["balance"] == 139.39


@pytest.mark.anyio
async def test_get_tiers(client: AsyncClient):
    r = await client.get("/api/tiers")
    assert r.status_code == 200
    assert set(r.json().keys()) == {"simple", "medium", "complex"}


@pytest.mark.anyio
async def test_put_tiers_valid(client: AsyncClient, monkeypatch):
    captured = {}

    def _fake_save(tiers):
        captured["tiers"] = tiers
        return tiers

    monkeypatch.setattr(admin_api, "save_tiers", _fake_save)
    payload = {
        "tiers": {
            "simple": {"model": "openai/gpt-4o-mini", "context_length": 128000},
            "medium": {"model": "anthropic/claude-sonnet-4", "context_length": 200000},
            "complex": {"model": "anthropic/claude-opus-4", "context_length": 200000},
        }
    }
    r = await client.put("/api/tiers", json=payload)
    assert r.status_code == 200
    assert captured["tiers"]["complex"]["model"] == "anthropic/claude-opus-4"


@pytest.mark.anyio
async def test_put_tiers_invalid(client: AsyncClient, monkeypatch):
    def _fake_save(tiers):
        raise TierValidationError("missing tier(s): complex")

    monkeypatch.setattr(admin_api, "save_tiers", _fake_save)
    r = await client.put("/api/tiers", json={"tiers": {"simple": {"model": "x", "context_length": 1}}})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_list_and_create_and_delete_keys(client: AsyncClient, monkeypatch):
    async def _fake_list():
        return [{"id": "11111111-1111-1111-1111-111111111111", "caller_id": "alex", "api_key": "sk-router-x", "prompt_preview_enabled": False}]

    created = {}

    async def _fake_create(**kwargs):
        created.update(kwargs)
        return {"id": "22222222-2222-2222-2222-222222222222", "caller_id": kwargs["caller_id"], "api_key": kwargs["api_key"], "prompt_preview_enabled": kwargs["prompt_preview_enabled"]}

    deleted = {}

    async def _fake_delete(key_id):
        deleted["id"] = key_id
        return True

    monkeypatch.setattr(db, "list_keys", _fake_list)
    monkeypatch.setattr(db, "create_key", _fake_create)
    monkeypatch.setattr(db, "delete_key", _fake_delete)

    r = await client.get("/api/keys")
    assert r.status_code == 200 and len(r.json()["data"]) == 1

    r = await client.post("/api/keys", json={"caller_id": "dev", "prompt_preview_enabled": True})
    assert r.status_code == 201
    assert created["caller_id"] == "dev"
    assert created["prompt_preview_enabled"] is True
    assert created["api_key"].startswith("sk-router-")

    r = await client.delete("/api/keys/22222222-2222-2222-2222-222222222222")
    assert r.status_code == 200
    assert deleted["id"] == "22222222-2222-2222-2222-222222222222"


@pytest.mark.anyio
async def test_delete_key_invalid_uuid(client: AsyncClient):
    r = await client.delete("/api/keys/not-a-uuid")
    assert r.status_code == 400


@pytest.mark.anyio
async def test_delete_key_not_found(client: AsyncClient, monkeypatch):
    async def _fake_delete(key_id):
        return False

    monkeypatch.setattr(db, "delete_key", _fake_delete)
    r = await client.delete("/api/keys/33333333-3333-3333-3333-333333333333")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_ui_index_served(client: AsyncClient):
    r = await client.get("/")
    assert r.status_code == 200
    assert "auto-router" in r.text
    assert "/static/app.js" in r.text
    # Phase 3: themed design system + responsive shell must be wired in.
    assert "/static/tokens.css" in r.text
    assert "/static/style.css" in r.text
    assert "/static/logo.svg" in r.text
    assert 'id="theme-toggle"' in r.text
    assert 'id="nav-toggle"' in r.text


@pytest.mark.anyio
async def test_static_app_js_served(client: AsyncClient):
    r = await client.get("/static/app.js")
    assert r.status_code == 200
    assert "loadLog" in r.text
    # Phase 3: theme toggle + mobile drawer behaviour.
    assert "applyTheme" in r.text
    assert "openDrawer" in r.text


@pytest.mark.anyio
async def test_static_phase3_assets_served(client: AsyncClient):
    for path, needle in [
        ("/static/tokens.css", "--color-primary"),
        ("/static/style.css", ".sidebar.is-open"),
        ("/static/logo.svg", "<svg"),
    ]:
        r = await client.get(path)
        assert r.status_code == 200, path
        assert needle in r.text, path


# --- Phase 2.5: settings --------------------------------------------------


@pytest.mark.anyio
async def test_get_settings(client: AsyncClient, monkeypatch):
    async def _fake_get_settings():
        return {
            "confidence_gap_threshold": 0.15,
            "prompt_preview_default": False,
            "routing_conservatism": "balanced",
        }

    monkeypatch.setattr(db, "get_settings", _fake_get_settings)
    r = await client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["settings"]["confidence_gap_threshold"] == 0.15
    assert body["presets"]["aggressive"] < body["presets"]["conservative"]


@pytest.mark.anyio
async def test_put_settings_preset(client: AsyncClient, monkeypatch):
    saved = {}

    async def _fake_set_settings(updates):
        saved["updates"] = updates

    async def _fake_get_settings():
        return {
            "confidence_gap_threshold": 0.30,
            "prompt_preview_default": False,
            "routing_conservatism": "conservative",
        }

    monkeypatch.setattr(db, "set_settings", _fake_set_settings)
    monkeypatch.setattr(db, "get_settings", _fake_get_settings)
    r = await client.put("/api/settings", json={"routing_conservatism": "conservative"})
    assert r.status_code == 200
    pairs = dict(saved["updates"])
    assert pairs["routing_conservatism"] == "conservative"
    assert pairs["confidence_gap_threshold"] == 0.30


@pytest.mark.anyio
async def test_put_settings_manual_threshold_flips_custom(client: AsyncClient, monkeypatch):
    saved = {}

    async def _fake_set_settings(updates):
        saved["updates"] = updates

    async def _fake_get_settings():
        return {
            "confidence_gap_threshold": 0.22,
            "prompt_preview_default": False,
            "routing_conservatism": "custom",
        }

    monkeypatch.setattr(db, "set_settings", _fake_set_settings)
    monkeypatch.setattr(db, "get_settings", _fake_get_settings)
    r = await client.put("/api/settings", json={"confidence_gap_threshold": 0.22})
    assert r.status_code == 200
    assert dict(saved["updates"])["routing_conservatism"] == "custom"


@pytest.mark.anyio
async def test_put_settings_invalid_threshold(client: AsyncClient):
    r = await client.put("/api/settings", json={"confidence_gap_threshold": 2.0})
    assert r.status_code == 400


# --- Phase 2.5: model enabled shortlist -----------------------------------


@pytest.mark.anyio
async def test_get_models_enabled(client: AsyncClient, monkeypatch):
    async def _fake_disabled():
        return {"openai/gpt-4o-mini", "foo/bar"}

    monkeypatch.setattr(db, "get_disabled_model_slugs", _fake_disabled)
    r = await client.get("/api/models/enabled")
    assert r.status_code == 200
    assert set(r.json()["disabled"]) == {"openai/gpt-4o-mini", "foo/bar"}


@pytest.mark.anyio
async def test_put_model_enabled(client: AsyncClient, monkeypatch):
    captured = {}

    async def _fake_set(slug, enabled):
        captured["slug"] = slug
        captured["enabled"] = enabled

    monkeypatch.setattr(db, "set_model_enabled", _fake_set)
    r = await client.put("/api/models/enabled", json={"model_slug": "foo/bar", "enabled": False})
    assert r.status_code == 200
    assert captured["slug"] == "foo/bar"
    assert captured["enabled"] is False
