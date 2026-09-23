# Phase 2 Hand-off — Functional (unstyled) UI

**Status:** code complete on branch `feature/phase-2-functional-ui`. Sandbox-verifiable
work passes; the browser DoD items below need the host stack rebuilt and exercised.

## What was built

- **Admin API** (`app/admin_api.py`, prefix `/api`, bearer-key auth):
  - `GET /api/requests` — paginated + filterable log (`tier`, `model`, `success`, `start`, `end`, `limit`, `offset`).
  - `GET /api/spend` — totals, by period (`bucket=day|week|month`), by tier, by model.
  - `GET /api/openrouter/models` — proxies OpenRouter's model catalog (key stays server-side).
  - `GET /api/openrouter/credits` — proxies the live account balance.
  - `GET /api/tiers`, `PUT /api/tiers` — read/update the tier mapping.
  - `GET /api/keys`, `POST /api/keys`, `DELETE /api/keys/{id}` — key management.
- **DB layer** (`app/db.py`): `list_requests`, `spend_summary`, `list_keys`, `create_key`,
  `delete_key`. Cost fields are coerced from `Decimal` to `float` so API consumers get numbers.
- **Tier store** (`app/tiers_store.py`): validated, atomic writes to `config/tiers.json`.
  `load_tiers()` reads per request, so a saved mapping takes effect with **no redeploy**.
- **OpenRouter admin client** (`app/openrouter_admin.py`): catalog + credits fetchers.
- **UI** (`app/static/`): plain HTML/CSS/JS, intentionally unstyled — request log with
  filters + paging, spend dashboard with live balance, tier-mapping editor with a searchable
  model dropdown (auto-fills `context_length`), and a key create/revoke screen. Served at `/`.
- **Compose**: `config/` is now bind-mounted into the router container so tier edits persist
  on the host.

## Findings (resolve before/at checkpoint)

1. **`/v1/credits` needs NO management key.** Verified live against Alex's standard
   `sk-or-v1...` key: HTTP 200, `{data:{total_credits, total_usage}}`. No second
   "management key" field is required. (Phase 2 doc flagged this as unconfirmed.)
2. **⚠️ Stale router keys in the `keys` table.** The table currently holds three `alex`
   rows with different key values/lengths (73 / 64 / 109 chars), accumulated across restarts.
   The 73-char one is **the OpenRouter API key itself** — seeded by Phase 1's
   "reuse `OPENROUTER_API_KEY` as the router key" fallback — and it is **still accepted as a
   valid router bearer key** (verified: HTTP 200). This is a real exposure. Fix: revoke the
   stale rows from the new **API keys** screen (or `DELETE /api/keys/{id}`). Decide whether
   the 109-char row is intentional before removing it.

## Sandbox verification (already done)

- `ruff check app tests` — clean.
- `pytest tests/` — **27 passed** (11 Phase 1 + 16 new: tiers-store validation/round-trip,
  request filters, spend, OpenRouter proxies, tier PUT valid/invalid, key CRUD, UI/static
  serving, auth).
- **Live smoke against the real Postgres** (served the app in-sandbox on :8099 over the
  shared docker network, real DB): `/api/tiers`, `/api/requests` (paging, `total=6`),
  `/api/spend` (by period/tier/model — totals reconcile), `/api/openrouter/models` (454),
  `/api/openrouter/credits` (balance ≈ 139.37), key create→list→delete, `PUT /api/tiers`
  (no-op round-trip; `config/` left clean), invalid PUT → 400, `/` and `/static/*` → 200,
  unauthenticated `/api/*` → 401.

## Host steps (browser DoD requires these)

Code changed, so the image must be rebuilt:

```bash
cd /Users/alex/hermes-stack/hermes-data/projects/auto-router
git checkout feature/phase-2-functional-ui
docker compose up --build -d
```

Then open <http://localhost:8000/>, paste a router key from `ROUTER_API_KEYS`, and confirm:

| # | DoD item | How to check |
|---|----------|--------------|
| 1 | Create an API key from the browser, no terminal | Keys tab → create → copy the returned key |
| 2 | Edit a tier mapping by picking from the live catalog, effective without redeploy | Tier mapping tab → pick a model → Save → send a prompt routed to that tier |
| 3 | View the recent request log, filter it, see per-request spend | Request log tab → apply filters → check cost column |
| 4 | Spend summary roughly matches live OpenRouter balance | Spend tab → compare app total vs the balance line |
| 5 | All of the above browser-only | — |

## Notes / limitations

- Admin `/api/*` uses the same bearer router key as the routing endpoint; the UI stores it in
  `localStorage`. Acceptable for a local single-user tool; revisit if this is ever exposed
  beyond localhost.
- `GET /api/keys` returns full key values (needed so Alex can copy them). Local-only.
- Spend shown is only what *this router* has logged; OpenRouter's `total_usage` is
  account-wide/lifetime, so the two will not be equal — treat a large divergence as a signal,
  not an exact match (per the phase doc).
