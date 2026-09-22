# Model Router App — Phase 2: Functional, Unstyled UI

**Status: build now — but only after Phase 1's Definition of Done is confirmed.** This
phase builds a browser UI on top of Phase 1's engine. No design/styling work here — that's
Phase 3. If it's ugly but works, that's correct for this phase.

## Dependency on Phase 1
This phase assumes the following already exist and work: the `keys` and `requests` tables,
`POST /v1/chat/completions`, `GET /v1/models`, and real logged request data to build a UI
against. Don't start this phase against a Phase 1 that hasn't met its own Definition of
Done — you'll be building a UI for data that doesn't exist yet or is wrong.

## Stack & deployment (inherited from Phase 1)
- Local Mac build via Docker Compose (Docker Desktop).
- Fresh Postgres instance (database `jev_router`) started by the same compose file.
- Kimi runs inside Hermes' sandboxed container (no root, no Docker-in-Docker). It writes
  code and any additional compose/service definitions; Alex runs `docker compose up` on
  the host. Never request or accept a password.

## App-facing endpoints to build
- `GET /api/requests` — paginated request log (filterable by tier, model, success/fail, date range)
- `GET /api/spend` — aggregated spend-rate data (daily/weekly/monthly totals, by tier and by model)
- `GET /api/openrouter/models` — proxies OpenRouter's model catalog (see below)
- `GET /api/openrouter/credits` — proxies OpenRouter's live credit balance (see below)
- `POST /api/keys`, `DELETE /api/keys/{id}` — key management

## OpenRouter integration — concrete endpoints
Two read-only OpenRouter endpoints, called server-side by the router app itself (not
exposed directly to the browser — proxy them through the `/api/openrouter/*` routes above
so the OpenRouter key never reaches the frontend):

- **`GET https://openrouter.ai/api/v1/models`** — public model catalog, no special auth
  tier needed. Use this to populate the tier-mapping editor with a searchable list of real,
  current OpenRouter model slugs, instead of Alex hand-typing them into `tiers.json`.
- **`GET https://openrouter.ai/api/v1/credits`** — returns `{ data: { total_credits,
  total_usage } }` for the authenticated account. Balance = `total_credits - total_usage`.
  Use this for the live "remaining balance" figure on the spend dashboard.

**Unconfirmed — verify before building this:** some of OpenRouter's alpha/analytics
endpoints return a 403 with "Only management keys can perform this operation" for certain
operations. It's not confirmed whether `/v1/credits` needs a management-tier key or works
with a normal request-signing key — test this directly against Alex's actual key before
assuming either way. If a management key is required, add a second, clearly-labeled
"OpenRouter management key" field in settings, distinct from the completions key — don't
let the two get conflated in config or in the UI.

## Logging & spend dashboard — what "done" looks like
- **Request log:** timestamp, tier, model, prompt preview (if enabled), tokens, cost,
  latency, success/fail. Filterable and searchable — not a raw unpaginated dump.
- **Spend-rate view:** cost over time (daily/weekly/monthly), broken down by tier and by
  model — e.g. "how much did the complex tier cost this week."
- **Live OpenRouter balance** shown alongside the app's own calculated spend total. These
  two numbers should roughly agree — a visible, persistent mismatch is a signal something's
  wrong in Phase 1's cost-logging step, not something to silently paper over here.
- **Tier-mapping editor:** backed by the live `/api/openrouter/models` list — a searchable
  dropdown, not a free-text field, so Alex can't typo a model slug into `tiers.json`.
- **API key create/revoke screen.**

## Definition of Done
1. Alex can create an API key from the browser, with no terminal use.
2. Alex can edit a tier mapping by picking a model from the live OpenRouter catalog (not
   typing a slug from memory) and see it take effect without a redeploy.
3. Alex can view the recent request log, filter it, and see what was spent on each request.
4. The spend-rate summary is visible and its total roughly matches the live OpenRouter
   balance figure pulled from `/api/openrouter/credits`.
5. All of the above works with zero terminal interaction — browser only.
   (Prerequisite: the Docker Compose stack from Phase 1 is already running on the host.)

## Sub-agent loop rules (apply to this phase)
Max 3 sub-agents per loop: Planner, Builder, Critic.
- **Stop conditions must be objective.** For this phase, "objective" means the Critic
  actually exercises the UI (creates a key, edits a mapping, loads the log) and confirms
  real data flows through — not a read of the source code. Live UI tests require the host
  containers to be up; Kimi should clearly note when a check can only be performed after
  Alex has started the stack.
- **Hard cap: 3 loop iterations per child task, and keep it at 3 here.** This phase is
  "functional, unstyled" by design — the goals (key created, mapping saved, log visible)
  are pass/fail, not a matter of taste, so the same reasoning as Phase 1 applies. Visual
  judgment calls are deliberately out of scope until Phase 3.
- **Checkpoint when this phase is done**, summarizing what was built and tested, before
  Phase 3 starts.
