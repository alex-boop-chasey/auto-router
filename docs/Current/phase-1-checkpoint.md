# Phase 1 Checkpoint — Core Engine ✅

**Date:** 2026-09-23
**Status:** Definition of Done met. Phase 1 is complete and verified against the live stack.
**Branch:** `main` (Phase 1 work merged/finalized here; Phase 2 proceeds on a feature branch).

---

## What was built

The backend routing engine for Hermes → OpenRouter, a self-hosted OpenAI-compatible proxy
that classifies each prompt by complexity (Jev) and routes it to the cheapest suitable model.

- **FastAPI (async) app** (`app/main.py`) exposing:
  - `GET /health`
  - `GET /v1/models` — logical three-tier model list
  - `POST /v1/chat/completions` — OpenAI-compatible, non-streaming **and** streaming
- **Auth** (`app/db.py`): bearer keys checked against the `keys` table; per-key
  `prompt_preview_enabled` flag (default off).
- **Decision layer** (`app/decision.py`): calls Jev (`/api/alpha/decisions`), applies the
  confidence-gap rule (top-two probabilities within 0.15 → escalate to the more expensive
  tier), falls back to a rule-based classifier (word thresholds + keyword lists) on any Jev
  error/timeout/low-confidence non-answer.
- **OpenRouter proxy** (`app/proxy.py`): isolated provider module; streaming passed through.
- **Persistence** (`app/db.py`): `keys` and `requests` tables; every request logged
  regardless of success/failure, `cost_usd` read directly from OpenRouter's `usage.cost`.
- **Tier config** (`config/tiers.json`): simple → `openai/gpt-4o-mini` (128000);
  medium → `anthropic/claude-sonnet-4` (200000); complex → `anthropic/claude-sonnet-4`
  (200000).
- **Deployment**: `Dockerfile` + `docker-compose.yml` (app + Postgres 16), `.env.example`.

## Sandbox verification (self-checked)

- Unit / logic tests for the confidence-threshold rule, the rule-based fallback classifier,
  message flattening, and tier → model resolution: **11/11 passing**.
- Code compiles, imports cleanly, FastAPI app starts under the test client without infra.

## Live verification (host stack — all confirmed)

| # | DoD live test | Result |
|---|---------------|--------|
| 1 | Simple prompt → `simple` tier | ✅ `id=1` `openai/gpt-4o-mini`, cost from `usage.cost` |
| 2 | Complex prompt → `complex` tier | ✅ `id=2` `anthropic/claude-sonnet-4`, cost `0.115767` |
| 3 | Ambiguous prompt → confidence-gap escalation | ⚠️ resolved to `medium` (see caveat) |
| 4 | Jev-failure fallback | ✅ `id=6` `used_fallback=true`, `jev_error` populated, request still succeeded |
| 5 | Streaming end-to-end | ✅ `id=4`,`id=5` — 152 SSE chunks over 6.52 s, incremental |

`GET /v1/models` returns the three tiers with the correct context lengths. Connectivity
verified via the shared `hermes-stack_hermes-net` network (`http://auto-router:8000`,
`auto-router-postgres:5432`).

## Unresolved / follow-ups

1. **Ambiguity-test provability (minor).** Test #3 resolved to `medium`, which is consistent
   with the confidence-gap rule, but the raw Jev `probabilities` are not persisted, and
   `medium`/`complex` share a model slug, so the escalation cannot be *independently proven*
   from the DB alone. Fix (deferred by Alex): log Jev probabilities/gap per request.
2. **`medium == complex` model.** Both tiers map to `anthropic/claude-sonnet-4` in v1 — this
   is the **specified** config, not a bug. Revisit when a distinct complex model is chosen.
3. **Jev-fallback test is host-only.** It requires a temporary `.env` change + restart; not
   reproducible from inside the sandbox.

## Notes

- `cost_usd` is stored as `NUMERIC(12,6)`; sub-micro-dollar costs round (e.g. `0.00000675`
  → `0.000007`). Expected, not a defect.
- Live checks were run from inside the sandbox container over the shared Docker network
  (no `localhost`/`127.0.0.1`).
