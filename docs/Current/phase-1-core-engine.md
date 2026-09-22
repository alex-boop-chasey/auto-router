# Model Router App — Phase 1: Core Engine

**Status: build now.** This phase blocks everything else — Phases 2–4 all depend on the
endpoints and tables built here. Do not start UI or design work until this phase's
Definition of Done is met.

## What this phase is (and isn't)
Build the backend routing engine only: auth, tier classification, the OpenRouter proxy,
and logging. No UI, no dashboard, no design work — those are Phases 2 and 3. The only
"interface" in this phase is Hermes itself, talking to the router over HTTP.

## Purpose (context, not a task)
This app replaces LiteLLM as the layer between Hermes and OpenRouter. It classifies each
incoming prompt's complexity via Jev (OpenRouter's Decisions endpoint) and routes it to
the cheapest model that can handle it.

## Stack
- **Language/framework:** Python 3.x, FastAPI (async)
- **DB driver:** asyncpg
- **LLM client:** `openai` Python client (OpenRouter is OpenAI-compatible), streaming enabled
- **Database:** Postgres — fresh instance via Docker Compose (database name `jev_router`)
- **Deployment:** Docker Compose on the local Mac (Docker Desktop). The app runs alongside
  Hermes, which is itself running in Docker.

## Build environment constraints (critical — read before any infra steps)
Kimi (the build agent) runs **inside Hermes' sandboxed Docker container**. That container:
- has no root / no sudo
- has no Docker-in-Docker access
- cannot provision Postgres, start containers, or install system packages that need elevated privileges

**Correct workflow:**
1. Kimi writes application code + a complete `docker-compose.yml` (app service + Postgres service).
2. Alex runs `docker compose up` (or the exact one-liner Kimi provides) in a normal Mac terminal outside the Hermes sandbox.
3. Live testing against a real Postgres happens on the host after the containers are up.

**Never ask for, accept, or attempt to use a password.** If any command prompts for sudo or a password, stop immediately and hand the exact command back to Alex to run himself. There is no correct password to give inside the sandbox.

## Endpoints to build
- `POST /v1/chat/completions` — main routing endpoint, OpenAI-compatible, must support streaming
- `GET /v1/models` — returns the logical model list Hermes' model picker displays

## Hermes integration (confirmed from official docs)
Hermes connects to any OpenAI-compatible endpoint through a `custom` provider entry.
Configure it via `~/.hermes/config.yaml`:
```yaml
model:
  default: router-default        # arbitrary — router decides the real model server-side
  provider: custom
  base_url: https://<router-app-host>/v1   # or http://localhost:<port>/v1 for local
  api_key: ${ROUTER_API_KEY}     # falls back to OPENAI_API_KEY env var if omitted
  context_length: 128000         # see constraint below
```
**Hard constraint:** Hermes requires a minimum ~64,000-token context window and rejects a
model below that at connection time. Since the router presents one logical endpoint but
picks the real model per-request, `context_length` must be set to the **smallest** context
window among all models the router might route to — don't set it to the largest model's
window and hope. Decide the models first (see tiers.json below), then set this value.

## Request flow
1. Incoming request's API key checked against the `keys` lookup table
2. Decision layer calls Jev via OpenRouter's Decisions endpoint (see full spec below)
   - On error, timeout, or low-confidence non-answer: fall back to the rule-based classifier
3. Tier is resolved to a concrete model via `config/tiers.json`
4. Request is proxied to OpenRouter through a single isolated provider module, streaming
   passed straight through to the caller
5. Every request is logged to the `requests` table regardless of success or failure

## Jev / Decisions API — full working spec (read before writing the decision layer)

This is the trickiest, least-familiar part of the build — it's an experimental endpoint most
coding models won't have accurate training data on. Use the concrete request/response shapes
below rather than improvising.

**Endpoint:** `POST https://openrouter.ai/api/alpha/decisions`
**Auth:** `Authorization: Bearer <OPENROUTER_API_KEY>` (same key type as normal completions)

**Example request body**, using this app's actual 3-tier criteria:
```json
{
  "model": "typesafe/jev-1.13",
  "questions": {
    "tier": {
      "type": "choice",
      "instructions": "Which complexity tier does this task belong to?",
      "criteria": {
        "simple": "Short factual, greeting, or trivial question a cheap model handles perfectly",
        "medium": "Normal coding, reasoning, or multi-step task benefiting from a mid-tier model",
        "complex": "Hard reasoning, long context, or high-stakes task needing a frontier model"
      }
    }
  },
  "state": "<the incoming prompt, flattened to plain text — see note below>"
}
```
**`state` constraint:** accepts a plain string, or a JSON object/array — but must be
`user`-role, text-only content. No system/assistant/tool messages, no images or files. If
Hermes sends multi-turn context, flatten it into a single plain-text blob (e.g. a short
summary of prior turns + the current prompt) before calling Jev — don't forward Hermes'
raw message array.

**Example response body:**
```json
{
  "answers": {
    "tier": {
      "type": "choice",
      "choice": "medium",
      "confidence": 0.75,
      "probabilities": { "simple": 0.05, "medium": 0.75, "complex": 0.20 }
    }
  },
  "id": "gen-dec-...",
  "model": "typesafe/jev-1.13-20260917",
  "provider": "TypeSafe",
  "usage": { "cost": 0.000019992, "input_tokens": 476, "output_tokens": 70 }
}
```
Read `answers.tier.choice` and `answers.tier.probabilities` — do not just take `.choice`
blindly (see confidence rule below).

**Confidence threshold — concrete rule, not just "near a coin-flip":**
Sort `probabilities` descending. If the gap between the top two values is **less than 0.15**,
resolve to the **more expensive** of those two tiers, not automatically "medium." E.g. if
`simple: 0.48` and `medium: 0.42` (gap 0.06, under threshold) → resolve to `medium`. If
`medium: 0.55` and `complex: 0.44` (gap 0.11, under threshold) → resolve to `complex`. This
threshold (0.15) is a starting point — expect to tune it after real usage data, but ship v1
with this exact rule so behavior is deterministic and testable, not vibes-based.

**Full error handling table** (trigger the rule-based fallback classifier on all of these,
not just 402):
| Code | Meaning | Action |
|---|---|---|
| 402 | Decisions credits exhausted | Fallback |
| 429 | Rate limited | Fallback |
| 503, 529 | Provider overloaded/unavailable | Fallback |
| 524 | Request timed out at edge | Fallback |
| Any network timeout (no response) | — | Fallback |
| 400, 401, 403 | Malformed request / auth issue | Fallback + log loudly — these indicate a bug in the router itself, not a transient Jev issue, so they should be visibly different in logs from the others |

**Non-streaming only:** `stream: true` on this endpoint returns a 400. Irrelevant in
practice — this is a fast classification call before the real, streamed completion.

**Cost:** the Decisions response includes its own `usage.cost` (shown above, ~$0.00002/call)
— negligible, not worth tracking per-request in v1's `requests` table (that table's `cost_usd`
is for the actual completion, see below).

## `config/tiers.json` — concrete schema
```json
{
  "simple": {
    "model": "openai/gpt-4o-mini",
    "context_length": 128000
  },
  "medium": {
    "model": "anthropic/claude-sonnet-4",
    "context_length": 200000
  },
  "complex": {
    "model": "anthropic/claude-sonnet-4",
    "context_length": 200000
  }
}
```
Loaded at startup and on file change (no redeploy needed to edit). The `context_length`
per tier is what feeds the Hermes `context_length` decision above — take the **minimum**
across all three entries.

## Cost calculation for the `requests` table — use OpenRouter's built-in field, don't compute manually
OpenRouter automatically includes a `usage` object in every chat completion response
(final SSE chunk for streaming, full response body otherwise) — **no request parameter
needed**, this is always on:
```json
{
  "usage": {
    "completion_tokens": 2,
    "cost": 0.95,
    "cost_details": { "upstream_inference_cost": 19 },
    "prompt_tokens": 194,
    "total_tokens": 196
  }
}
```
Read `usage.cost` directly into the `requests.cost_usd` column. **Do not** hardcode a
price-per-token table and calculate cost manually — that's the exact kind of shortcut that
looks correct in testing and quietly drifts wrong later (e.g. when OpenRouter's pricing for
a model changes, or a request gets routed to a different provider under the hood). This is
also why the Phase 2 spend dashboard comparing this total against OpenRouter's real balance
is a useful sanity check — if they disagree, something's wrong with this step specifically.

## Database schema — `requests` table
```
id, timestamp, caller_id, tier, model, input_tokens, output_tokens,
cost_usd, latency_ms, success, error (nullable), prompt_preview (nullable)
```
`prompt_preview`: a truncated (first ~200 chars) copy of the incoming prompt, so a later
log viewer can show "which model got picked for which prompt" without storing full
conversation content. **Default this to off** — make it a per-key setting, not a default —
until Alex confirms he's comfortable with it on.

## Auth
`keys` table: `id, api_key, caller_id, created_at`. One row for Alex today.

## Definition of Done — concrete test cases, not "looks correct"

Split into what Kimi can verify inside the sandbox vs. what requires the live containers
on the host.

### A. Sandbox-verifiable (Kimi can and should confirm these itself)
1. Unit / logic tests for the confidence-threshold rule, the rule-based fallback classifier,
   message flattening, and tier → model resolution from `tiers.json`.
2. Request/response shape validation for the OpenAI-compatible endpoints (without needing
   a live Postgres or OpenRouter call if mocks are used).
3. Code compiles, imports cleanly, and the FastAPI app starts in isolation (or under a
   test client) without runtime errors related to missing infra.

### B. Live host tests (only possible after Alex has run `docker compose up`)
All of the following must be demonstrated with real logged Postgres rows:
1. A short, obviously-simple prompt (e.g. "what's 2+2") sent from Hermes routes to the
   `simple` tier model, returns a correct completion, and is logged with the right tier,
   model, `cost_usd` (from `usage.cost`, not hardcoded), and latency.
2. A clearly complex prompt (e.g. a multi-file refactor request) routes to the `complex`
   tier and logs correctly.
3. An ambiguous prompt deliberately worded to produce a near-50/50 Jev response is sent,
   and the logged tier matches the confidence-threshold rule above (escalates to the
   pricier of the two contenders), not just whatever `.choice` happened to say.
4. The Jev call is forced to fail (e.g. by pointing at an invalid URL or temporarily
   removing Decisions credits) and the request still completes successfully via the
   rule-based fallback classifier — confirm this in the logs (the `error` or a dedicated
   marker should show fallback was used).
5. Streaming works end-to-end: Hermes receives tokens incrementally, not as one blocked
   response.

Kimi must produce a clear hand-off note listing the exact `docker compose` command and
any `.env` values Alex needs to set before the live tests can run.

## Open items specific to this phase
- Decide the rule-based fallback classifier's actual heuristic (word count threshold?
  keyword list?) — not specified yet, needs a concrete rule same as the confidence
  threshold above, not left to the build agent to invent silently
- Decide the default for `prompt_preview` logging (off, per above) and its truncation length
- Confirm whether `GET /v1/models` should report per-model `context_length` for Hermes
  discovery accuracy, or a single fixed value is fine for v1

## Sub-agent loop rules (apply to this phase)
Max 3 sub-agents per loop: Planner, Builder, Critic.
- **Stop conditions must be objective.** Stop when the sandbox-verifiable items above pass
  and a clear hand-off for the live tests is written — not when the Critic says the code
  "looks correct."
- **Hard cap: 3 loop iterations per child task, and keep it at 3 here.** This phase's goals
  are objectively checkable, so 3 iterations of try/check/fix is enough. If unresolved
  after 3, flag it and report to Alex in plain language rather than continuing to loop.
- **Checkpoint when this phase is done.** Summarize in plain language what was built, what
  was tested against the Definition of Done above, and anything unresolved, before Phase 2
  starts.
