# Phase 1 Hand-off — Live host tests

## Start the stack

From a normal macOS terminal **outside the Hermes sandbox**:

```bash
cd /Users/alex/components/auto-router
docker compose up --build -d
```

This starts:
- `auto-router-postgres` on host port `5432`.
- `auto-router` on host port `8000`.

## Required `.env` values

`/Users/alex/components/auto-router/.env` must contain at least:

```bash
OPENROUTER_API_KEY=<your-openrouter-key>
ROUTER_API_KEYS=alex:<a-secret-key-hermes-will-use>
```

`ROUTER_API_KEYS` format: `caller_id_1:key_1;caller_id_2:key_2`.

The key you give Hermes in `~/.hermes/config.yaml` must match the key in
`ROUTER_API_KEYS`.

## Hermes `config.yaml`

```yaml
model:
  default: router-default
  provider: custom
  base_url: http://localhost:8000/v1
  api_key: ${ROUTER_API_KEY}
  context_length: 128000
```

Use `context_length: 128000` because that is the minimum value in
`config/tiers.json`.

## Health checks

```bash
curl http://localhost:8000/health
curl http://localhost:8000/v1/models -H "Authorization: Bearer $ROUTER_API_KEY"
```

## Definition of Done — live tests

Run each prompt through Hermes (or via curl) and then inspect the Postgres log:

```bash
docker exec -it auto-router-postgres psql -U postgres -d jev_router -c "SELECT * FROM requests ORDER BY timestamp DESC LIMIT 10;"
```

1. **Simple prompt**
   - Prompt: `what is 2+2`
   - Expected: `tier = simple`, `model = openai/gpt-4o-mini`, `success = true`,
     `cost_usd` populated from `usage.cost`.

2. **Complex prompt**
   - Prompt: `Refactor this multi-file Python project to async/await and add tests.`
   - Expected: `tier = complex`, `model = anthropic/claude-sonnet-4`,
     `success = true`, `cost_usd` populated.

3. **Ambiguous prompt (confidence threshold)**
   - Prompt deliberately vague or borderline so Jev returns near-tied probabilities.
   - Verify the logged `tier` matches the confidence-threshold rule: when the top
     two probabilities are within 0.15, the more expensive tier is chosen.

4. **Jev failure fallback**
   - Temporarily set `JEV_URL` to an invalid URL in `.env`, run
     `docker compose up -d` to restart the app, send a prompt, then check the log.
   - Expected: request still succeeds, `used_fallback = true`, `jev_error` is set.
   - Restore the correct `JEV_URL` afterwards.

5. **Streaming**
   - Send any prompt with `stream: true`.
   - Expected: tokens arrive incrementally (not one blocked JSON blob) and the
     final request log has `success = true`.

## Notes / open items

- `prompt_preview` is **off by default** and controlled per key in the `keys`
  table (`prompt_preview_enabled`).
- `GET /v1/models` returns per-model `context_length`; Hermes should still be
  configured with the minimum value (`128000`) because that is the smallest
  window the router might select.
