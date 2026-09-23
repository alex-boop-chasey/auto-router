# Model Router App — Phase 2.5: Developer Completeness

**Status: build now.** Inserted between Phase 2 (done, verified) and Phase 3 (design pass —
still blocked on a visual-direction decision, unaffected by this phase). This phase adds the
features that were missing to make the app feel like a real LiteLLM replacement rather than
a thin admin shell, before any styling work happens.

## Dependency
Phases 1 and 2 are both complete and verified — this phase builds on their existing tables,
endpoints, and UI rather than replacing anything. Do not start Phase 3 until this phase's
Definition of Done is met.

## Stack & deployment (inherited)
- Local Mac build via Docker Compose (Docker Desktop).
- Same Postgres instance (`jev_router`) — this phase adds columns/tables to it, no new DB.
- The sandboxed build agent ("hermes docker") writes code and any schema migrations; it has
  no root, no Docker-in-Docker, and cannot run `docker compose up` itself. Host-level rebuild
  and live verification is run by "hermes local" or Alex directly. Never request or accept a
  password inside the sandbox.

## What's already there (don't rebuild these)
- Live OpenRouter model catalog proxy (`GET /api/openrouter/models`)
- Live balance proxy (`GET /api/openrouter/credits`)
- Request log + spend dashboard (`GET /api/requests`, `GET /api/spend`)
- Tier → model mapping, editable without redeploy (`GET/PUT /api/tiers`)
- API key management (`GET/POST/DELETE /api/keys`)
- `used_fallback` and `jev_error` columns already exist on `requests` (added during Phase 1)

## What's missing — five areas

### 1. Request log upgrades
- Show which tier was chosen and why: confidence, and whether fallback was used, as a clear
  visual distinction (not just a true/false flag buried in a column).
- Full error message visible (expand `jev_error`/`error`, not just a red dot).
- `prompt_preview` column shown when enabled for that key (already exists, just wasn't
  surfaced clearly in Phase 2's log view).

### 2. Model catalog page (the actual LiteLLM-style gap)
Right now the tier editor picks *one* model per tier from OpenRouter's full live catalog
(450+ entries). What's missing is a shortlist: which models Alex actually wants available at
all, independent of tier assignment — the same "enable/disable per model" pattern LiteLLM
uses.
- New table: `enabled_models (model_slug TEXT PRIMARY KEY, enabled BOOLEAN DEFAULT true,
  updated_at TIMESTAMPTZ)`.
- New page: searchable list of the live catalog with price (prompt + completion), context
  length, and an enable/disable toggle per model.
- The tier editor's dropdown (Phase 2) now defaults to showing only *enabled* models, with an
  explicit "show all" option to fall back to the full catalog — don't silently hide the full
  list, just narrow the default.

### 3. Routing transparency
The single biggest trust gap right now: a request is routed and logged, but there's no way
to see *why*.
- Add to `requests`: `confidence FLOAT`, `probability_gap FLOAT`, `probabilities JSONB`
  (the full per-tier probability object Jev returns) — this finally implements the item
  Phase 1's checkpoint deferred ("log Jev probabilities/gap per request").
- Surface this on each request row or a detail drawer: tier chosen, confidence/gap, whether
  the confidence-threshold escalation rule fired, whether fallback was used, final model.

### 4. Settings / config panel
Currently-hardcoded values become editable, effective without redeploy (same principle as
`tiers.json`):
- `confidence_gap_threshold` (currently hardcoded at 0.15 in `app/decision.py`)
- Default `prompt_preview_enabled` for new keys
- A "routing conservatism" preset — `aggressive` / `balanced` / `conservative` / `custom` —
  where picking a named preset sets the threshold to a fixed value, and manually editing the
  threshold afterward flips the setting to `custom`. Store as a `settings` table (single row
  is fine, or key-value — implementer's choice, just don't hardcode it in code anymore).

### 5. Error & health visibility
- Enhance `GET /health` to include `last_successful_decision_at` — the timestamp of the most
  recent request where `success = true AND used_fallback = false`. This can be computed
  directly from the existing `requests` table (`SELECT MAX(timestamp) ...`) — no new table
  needed.
- Surface this on the dashboard as a simple "decision layer last confirmed working: Xm ago"
  indicator, so a Jev outage is visible at a glance rather than only discoverable by reading
  individual request rows.

## Definition of Done
1. The request log visually distinguishes fallback-routed requests from normal ones, and the
   full error message is viewable per request (not truncated to a boolean).
2. The model catalog page lists the live OpenRouter catalog with price/context/enabled state;
   toggling a model's enabled state persists and immediately affects the tier editor's
   default dropdown, with no redeploy.
3. Confidence, probability gap, and fallback status are visible per request in the browser,
   backed by real persisted values (not `NULL` on every row — confirm this by sending one
   request and checking its row directly).
4. The confidence-gap threshold, default prompt-preview setting, and a routing-conservatism
   preset are all editable from the browser and take effect on the next request with no
   redeploy.
5. The dashboard shows a "decision layer last confirmed working" indicator that reflects a
   real, recent `requests` row when checked against the database directly.
6. All of the above works browser-only, no terminal use, and prerequisite: the Docker Compose
   stack from Phases 1–2 is already running on the host.

## Sub-agent loop rules (apply to this phase)
Max 3 sub-agents per loop: Planner, Builder, Critic.
- **Stop conditions must be objective** — a real logged request row showing the new columns
  populated correctly, a toggled model actually filtering the tier-editor dropdown, a changed
  threshold actually changing routing behavior on the next request. Not a code read-through.
- **Hard cap: 3 loop iterations per child task.** This phase is still "functional," not
  design work — the same reasoning as Phase 2 applies, not Phase 3's iteration-budget pattern.
  If unresolved after 3, flag it and report to Alex in plain language.
- **Checkpoint when this phase is done**, before Phase 3's design pass starts (which still
  separately requires the visual-direction decision from the Phase 3 doc).