# auto-router — Phase 1

Backend routing engine for Hermes → OpenRouter.

## Env variables

Required:
- `OPENROUTER_API_KEY` — forwarded to OpenRouter for completions and Jev decisions.

Router auth keys:
- `ROUTER_API_KEYS` — semicolon-separated `"caller_id:api_key"` pairs, e.g.:
  ```
  ROUTER_API_KEYS=alex:sk-router-xxx;dev:sk-router-yyy
  ```
  If omitted, the app falls back to using `OPENROUTER_API_KEY` as the single
  router key (not recommended for multi-user deployments).

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
cd /Users/alex/components/auto-router
docker compose up --build -d
```

Wait for Postgres to be healthy, then verify:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/v1/models
```

Live tests require the containers to be running; the hand-off note in
`docs/Current/phase-1-hand-off.md` lists exactly what to run.

## Sandbox-only verification

Inside the Hermes sandbox (no containers available):

```bash
uv sync --frozen
source .venv/bin/activate
python -m pytest tests/
```
