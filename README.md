# auto-router

Self-hosted OpenAI-compatible proxy that sits between Hermes and OpenRouter. It
classifies each prompt by complexity (simple / medium / complex) via Jev and routes it to
the cheapest suitable model, logs every request, and exposes a browser admin UI.

- **Phase 1 — Core Engine:** routing, auth, tier classification, OpenRouter proxy, logging.
  See `docs/Current/phase-1-checkpoint.md`.
- **Phase 2 — Functional (unstyled) UI:** request log, spend dashboard, tier-mapping editor,
  API-key management. See `docs/Current/phase-2-hand-off.md`.

## Env variables

Required:
- `OPENROUTER_API_KEY` — forwarded to OpenRouter for completions, Jev decisions, and the
  admin OpenRouter proxy routes.

Router auth keys:
- `ROUTER_API_KEYS` — semicolon-separated `"caller_id:api_key"` pairs, e.g.:
  ```
  ROUTER_API_KEYS=alex:sk-router-xxx;dev:sk-router-yyy
  ```
  If omitted, the app falls back to using `OPENROUTER_API_KEY` as the single
  router key (not recommended for multi-user deployments — and note this fallback row
  persists in the `keys` table; revoke it from the UI once a real key is set).

Database:
- `DATABASE_URL` — optional full Postgres URL.
- If not set: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, `POSTGRES_DB`.

Decision layer:
- `JEV_URL` — default `https://openrouter.ai/api/alpha/decisions`.
- `JEV_MODEL` — default `typesafe/jev-1.13`.
- `JEV_CONFIDENCE_GAP` — default `0.15`.

Fallback classifier:
- `SIMPLE_WORD_THRESHOLD` — default `25`.
- `COMPLEX_WORD_THRESHOLD` — default `100`.
- `SIMPLE_KEYWORDS` — default `what is,how many,define,short,quick,brief`.
- `COMPLEX_KEYWORDS` — default `refactor,multi-file,complex,architecture,design,debug,compare,analyze in detail,long context`.

Privacy:
- `PROMPT_PREVIEW_DEFAULT` — default `false`.
- `PROMPT_PREVIEW_LENGTH` — default `200`.

## API

Routing (Hermes-facing, Phase 1):
- `POST /v1/chat/completions` — OpenAI-compatible, streaming and non-streaming.
- `GET /v1/models` — logical three-tier model list.

Admin (browser UI, Phase 2) — all require the same bearer key:
- `GET /api/requests` — paginated, filterable log (`tier`, `model`, `success`, `start`, `end`, `limit`, `offset`).
- `GET /api/spend` — aggregated spend (`bucket=day|week|month`), by period/tier/model.
- `GET /api/openrouter/models` — proxied OpenRouter model catalog (key stays server-side).
- `GET /api/openrouter/credits` — proxied live OpenRouter balance.
- `GET /api/tiers`, `PUT /api/tiers` — read/update the tier mapping (writes `config/tiers.json`).
- `GET /api/keys`, `POST /api/keys`, `DELETE /api/keys/{id}` — key management.
- `GET /` — the admin UI; static assets under `/static/`.

## Hermes `config.yaml` snippet

```yaml
model:
  default: router-default
  provider: custom
  base_url: http://localhost:8000/v1   # or https://<your-host>/v1 when deployed
  api_key: ${ROUTER_API_KEY}
  context_length: 128000   # min context_length from config/tiers.json
```

## Running locally inside Docker Desktop

From a normal macOS terminal (outside the Hermes sandbox):

```bash
cd /Users/alex/hermes-stack/hermes-data/projects/auto-router
docker compose up --build -d
```

`config/` is bind-mounted into the app container, so tier-mapping edits made in the UI
persist on the host and take effect immediately (no redeploy).

Wait for Postgres to be healthy, then verify:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/v1/models
```

Then open the admin UI at <http://localhost:8000/> and paste a router key from
`ROUTER_API_KEYS`. Live-test checklists live in `docs/Current/phase-1-hand-off.md` and
`docs/Current/phase-2-hand-off.md`.

## Sandbox-only verification

Inside the Hermes sandbox (no containers available):

```bash
uv sync --frozen
source .venv/bin/activate
python -m pytest tests/
ruff check app tests
```
