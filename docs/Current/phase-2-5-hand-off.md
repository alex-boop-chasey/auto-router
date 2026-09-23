# Phase 2.5 Hand-off — Developer Completeness

**Status:** code complete on branch `feature/phase-2-5-developer-completeness`.
Sandbox-verifiable work passes (`ruff` clean, `pytest` 48 passed); the browser DoD
items below need the host stack rebuilt and exercised.

## What was built

Five areas, per the spec in `docs/Current/phase_2_5.md`:

### 1. Request log upgrades
- Each row now shows `conf` (top-tier probability) and `gap` (top minus second),
  plus a `routing` badge: **normal** / **escalated** / **FALLBACK**. Fallback rows
  get a distinct row tint and badge, not just a boolean column.
- A `details` toggle on every row expands a hidden sub-row with the full
  `error`, `jev_error`, `prompt_preview`, and the per-tier `probabilities` object.

### 2. Model catalog page (the LiteLLM-style shortlist)
- New `enabled_models` table (`model_slug TEXT PRIMARY KEY, enabled BOOLEAN DEFAULT
  true, updated_at TIMESTAMPTZ`) — a **blacklist**: a model is enabled unless a row
  says otherwise, so the 450+ catalog doesn't need to be enabled by hand.
- New **Models** tab: searchable live catalog with context length, prompt +
  completion price (per 1M tokens), and an enable/disable toggle per model.
  Persists via `PUT /api/models/enabled`; toggling takes effect immediately.
- The tier editor's dropdown now shows only **enabled** models by default, with a
  "show all models (including disabled)" checkbox to fall back to the full catalog.

### 3. Routing transparency
- `requests` gained `confidence FLOAT`, `probability_gap FLOAT`, `probabilities
  JSONB`, and `escalation_fired BOOLEAN` — implementing the Phase 1 checkpoint's
  deferred "log Jev probabilities/gap per request" item.
- `app/decision.py` now returns a `Decision` dataclass (tier, used_fallback,
  jev_error, confidence, probability_gap, probabilities, escalation_fired) instead
  of a 3-tuple.

### 4. Settings / config panel
- New `settings` key-value table. New **Settings** tab exposes:
  - `confidence_gap_threshold` (was hardcoded 0.15 in `decision.py` — now read from
    DB on every request).
  - `prompt_preview_default` (default for new keys; `POST /api/keys` falls back to
    it when the flag is omitted).
  - A routing-conservatism preset — `aggressive` (0.05) / `balanced` (0.15) /
    `conservative` (0.30) / `custom`. Picking a preset sets the threshold; editing
    the threshold manually flips it to `custom`.
- Logic lives in `app/settings_store.py`; persistence in `app/db.py`
  (`get_settings` / `set_settings`).

### 5. Error & health visibility
- `GET /health` now returns `last_successful_decision_at` — `MAX(timestamp)` over
  `requests` where `success AND NOT used_fallback` (no new table, computed on the
  fly; degrades to `null` if the DB is unreachable).
- Surfaced in the UI as a persistent "decision layer last confirmed working: Xm ago"
  indicator above the nav, plus refreshed when the Spend tab loads.

## Schema migration

No standalone migration is required. `app/db.py:init_db()` now runs idempotent
`ALTER TABLE requests ADD COLUMN IF NOT EXISTS …` plus `CREATE TABLE IF NOT EXISTS`
for `enabled_models` and `settings` on startup, so the existing `jev_router` DB is
upgraded in place on the next container start. Existing `requests` rows keep the new
columns as `NULL` (expected — see the DoD note below).

## Sandbox verification (already done)

- `ruff check app tests` — clean.
- `pytest tests/` — **48 passed** (27 prior + 21 new: settings-store presets/custom
  flip/validation/decode, settings GET/PUT endpoints, model enabled-state endpoints,
  and a mocked-Jev `Decision` transparency test asserting confidence/gap/escalation
  are populated).

## Host steps (browser DoD requires these)

Code changed, so the image must be rebuilt:

```bash
cd /Users/alex/hermes-stack/hermes-data/projects/auto-router
git checkout feature/phase-2-5-developer-completeness
docker compose up --build -d
```

Then open <http://localhost:8000/>, paste a router key, and confirm:

| # | DoD item | How to check |
|---|----------|--------------|
| 1 | Fallback rows visually distinct + full error viewable | Send a prompt with `JEV_URL` pointed at a bad URL (or wait for a Jev blip); the log row shows a **FALLBACK** badge and `details` reveals `jev_error` |
| 2 | Model catalog lists live catalog with price/context/toggle; toggle persists and filters the tier dropdown | Models tab → toggle a model off → Tier mapping tab → its slug is gone from the dropdown unless "show all" is ticked |
| 3 | Confidence / gap / fallback backed by real values, not `NULL` | Send one prompt, then `SELECT confidence, probability_gap, probabilities, escalation_fired FROM requests ORDER BY id DESC LIMIT 1;` — confidence/gap populated |
| 4 | Threshold, preview default, and conservatism preset editable, effective next request | Settings tab → set `conservative`, save, send a near-tie prompt; next request routes to the pricier tier. Set preview default on, create a key → preview column populated |
| 5 | "Decision layer last confirmed working" reflects a real row | Send a prompt, refresh — indicator reads "Xs ago"; check against `SELECT MAX(timestamp) FROM requests WHERE success AND NOT used_fallback;` |
| 6 | All of the above browser-only, no terminal | — |

## Notes / limitations

- `enabled_models` is a **blacklist** (default-enabled). This is deliberate so Alex
  doesn't enable 450 models by hand; flipping the semantics to a whitelist later is
  a one-line change in `db.get_disabled_model_slugs` + the UI `isEnabled` helper.
- `escalation_fired` means the confidence-gap rule *triggered* (gap < threshold),
  not necessarily that the tier changed (the top tier may already be the pricier
  one). `conf`/`gap`/`probabilities` are the source of truth for "why this tier".
- Fallback requests persist `confidence`/`gap`/`probabilities` as `NULL` by design —
  there's no Jev answer to record.
- Settings are read per request (`get_settings()` in the completion path), so edits
  take effect on the *next* prompt with no redeploy — same principle as
  `tiers.json`.
