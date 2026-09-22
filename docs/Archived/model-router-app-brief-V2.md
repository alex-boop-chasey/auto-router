# Model Router App — Build Brief (v2)

## Purpose
Replace LiteLLM as the layer between Hermes and OpenRouter. Uses Jev (OpenRouter's
Decisions endpoint) to classify each incoming prompt's complexity and route it to the
cheapest model that can handle it. Built for single-user use (Alex) initially, structured
so it can become a multi-tenant SaaS later without a rewrite — eventual ambition is a
product other "vibe coders" could use to save money and time on AI model spend, not just
a personal tool.

## Stack
- **Language/framework:** Python 3.x, FastAPI (async)
- **DB driver:** asyncpg
- **LLM client:** `openai` Python client (OpenRouter is OpenAI-compatible), streaming enabled
- **Database:** Postgres — reuse the existing VPS Postgres instance, new database `jev_router`
  (kept separate from the old `litellm` database so it can be dropped later without side effects)
- **Deployment:** Docker Compose on the Vultr VPS, alongside the existing Hermes container

## Endpoints (Hermes-facing)
- `POST /v1/chat/completions` — main routing endpoint, OpenAI-compatible, must support streaming
- `GET /v1/models` — returns the logical model list Hermes' model picker displays (even though
  every request gets re-routed by Jev underneath)

## Endpoints (app-facing, for the UI in Phase 2)
- `GET /api/requests` — paginated request log (see Logging section)
- `GET /api/spend` — aggregated spend-rate data (daily/weekly/monthly totals, by tier and model)
- `GET /api/openrouter/models` — proxies OpenRouter's model catalog into the app's tier-mapping UI
- `GET /api/openrouter/credits` — proxies OpenRouter's live credit balance into the app's spend dashboard
- `POST /api/keys`, `DELETE /api/keys/{id}` — key management

## Hermes integration (confirmed from official docs)
Hermes is itself a Python (3.11) application, installed via `uv`. It connects to any
OpenAI-compatible endpoint through a `custom` provider entry — this is a well-trodden path
(DeepInfra, Novita, APIYI and others all integrate with Hermes this way), so the router app is
not doing anything unusual from Hermes' side. Configure it via `~/.hermes/config.yaml`:
```yaml
model:
  default: router-default        # arbitrary — router decides the real model server-side
  provider: custom
  base_url: https://<router-app-host>/v1
  api_key: ${ROUTER_API_KEY}     # falls back to OPENAI_API_KEY env var if omitted
  context_length: 128000         # see constraint below — set explicitly, don't rely on discovery
```
**Hard constraint:** Hermes requires a minimum ~64,000-token context window and will reject a
model below that at connection time. Since the router presents one logical endpoint to Hermes
but picks the real model per-request behind the scenes, `context_length` in Hermes' config must
be set to the **smallest** context window among all models the router might route to — if the
`simple` tier's cheap model has a smaller window than that, Hermes will still assume the
configured value, so keep the declared value honest (don't just set it to the largest model's
window and hope). Worth deciding: should `GET /v1/models` also report `context_length` per
logical model so Hermes' discovery reflects reality accurately? DeepInfra's integration exposes
a `/v1/openai/models?filter=with_meta` pattern for exactly this.

## Prior art — what LiteLLM already calls this
LiteLLM (the tool being replaced) ships "virtual keys": per-key spend budgets, rate limits, and
model restrictions, plus built-in routing strategies (cost-based, latency-based, least-busy,
semantic). Worth adopting the same vocabulary since it's now the de facto standard — the `keys`
table design in this brief is a lightweight version of LiteLLM's virtual keys, and extending it
later with a `budget_usd` / `rpm_limit` column per row is a natural, well-precedented next step
rather than a novel design choice.

## Request flow
1. Incoming request's API key checked against a `keys` lookup table (not a hardcoded string —
   single row for Alex today, ready for more rows later)
2. Decision layer calls Jev via OpenRouter's Decisions endpoint with the tier criteria below
   - On error or timeout: fall back to a simple rule-based classifier (word count / keyword
     heuristic) so a Jev outage never takes the whole router down
3. Tier is resolved to a concrete model via `config/tiers.json` — editable without a redeploy
4. Request is proxied to OpenRouter through a single isolated provider module, streaming passed
   straight through to the caller
5. Every request is logged to the `requests` table regardless of success or failure

## Jev tier criteria (v1 — 3-tier scheme)
| Tier | Criteria | Example model |
|---|---|---|
| simple | Short factual, greeting, or trivial question a cheap model handles perfectly | `openai/gpt-4o-mini` |
| medium | Normal coding, reasoning, or multi-step task benefiting from a mid-tier model | `anthropic/claude-sonnet-4` |
| complex | Hard reasoning, long context, or high-stakes task needing a frontier model | `anthropic/claude-sonnet-4` or stronger |

This table lives in `config/tiers.json`, not in code. The exact Jev prompt/criteria wording
should be versioned alongside it as a living doc — it will get tuned after the first week of
real usage data.

## Jev / Decisions API — confirmed constraints (read before writing the decision layer)
- **Explicitly labeled "Experimental" by OpenRouter itself**: the request/response shape "may
  change in a future release without a deprecation period." This is a stronger warning than
  ordinary alpha software — the fallback path isn't optional insurance, it's load-bearing.
- Endpoint only accepts **`user`-role, text-only** content in the `state` field — no system,
  assistant, or tool messages, no images/files. The router must flatten whatever context it
  wants Jev to see (the prompt, maybe a summary of prior turns) into a single plain-text blob
  before calling it — it can't just forward Hermes' raw message array.
- **Non-streaming only** for the decision call (`stream: true` returns a 400) — irrelevant in
  practice since it's a small classification call that happens before the real, streamed
  completion request.
- Returns **probabilities, not just a hard pick** — a `choice` question comes back with a
  probability per option. The router should apply a confidence threshold rather than blindly
  taking the top answer: e.g. if `simple` and `medium` are near a coin-flip, default to
  `medium` rather than risk under-provisioning a request that turns out to be harder than
  expected.
- Jev also supports `Score` and `Noul` (yes/no) decision types, not just `Choice` — worth
  keeping in mind for future features (see roadmap) that need a rating rather than a category.
- Errors include `402 Payment Required` distinctly from other 4xx — that's OpenRouter credits
  for Decisions specifically running out, and should trigger the fallback classifier the same
  way a timeout does.
- Cost is negligible: listed at roughly $0.042 per 1M input tokens with no output-token charge,
  so calling it on every request isn't a meaningful cost driver even at scale.

## Database schema — `requests` table
```
id, timestamp, caller_id, tier, model, input_tokens, output_tokens,
cost_usd, latency_ms, success, error (nullable), prompt_preview (nullable)
```
`prompt_preview` is new in v2: a truncated (e.g. first ~200 chars) copy of the incoming prompt,
stored specifically so the log viewer can show "which model got picked for which prompt" without
storing full conversation content. Truncation length and whether to store it at all should be a
per-key setting (some users may not want prompt content logged even partially) — default this to
off until Alex confirms a comfortable default.
Add further columns (e.g. `prompt_hash`, `jev_raw_response`) later, only once there's a concrete reason.

## Auth
`keys` table: `id, api_key, caller_id, created_at`. One row for Alex today. Adding a second
caller later is an insert, not a code change.

## OpenRouter integration (beyond the completions proxy)
Two read-only OpenRouter endpoints are worth wiring into the app itself, so day-to-day
management doesn't require leaving the router's own UI:
- `GET https://openrouter.ai/api/v1/models` — public model catalog. Use this to populate the
  tier-mapping editor (Phase 2) with a searchable dropdown of real, current OpenRouter models
  instead of Alex hand-typing model slugs into `tiers.json`.
- `GET https://openrouter.ai/api/v1/credits` — returns `total_credits` and `total_usage` for the
  authenticated OpenRouter account. Use this to show a live "remaining balance" figure on the
  spend dashboard, alongside the app's own calculated `cost_usd` totals from the `requests` table.
- **Unconfirmed — needs checking before Phase 2 build:** OpenRouter's deeper usage-analytics
  endpoints (breakdown by model/provider/date) may require a separate **management-tier** API
  key rather than the normal request-signing key. If so, the app needs a second, optional
  "OpenRouter management key" field in settings, clearly distinct from the key used for actual
  completions — don't conflate the two.

## Logging & spend dashboard (Phase 2, elevated from a basic log viewer)
- Request log: timestamp, tier, model, prompt preview, tokens, cost, latency, success/fail —
  filterable and searchable, not just a raw dump
- Spend-rate view: cost over time (daily/weekly/monthly), broken down by tier and by model, so
  Alex can see e.g. "how much did the complex tier cost me this week"
- Live OpenRouter balance shown alongside the app's own spend totals (see above) — two numbers
  that should roughly agree, and a visible mismatch is itself a useful signal something's wrong
  with the router's cost tracking

## Explicitly out of scope for v1
Rate limiting, per-user billing, signup/onboarding flow. Not needed for single-user use — the
structure above just means they slot in later instead of requiring a rebuild.

## Open items before coding starts
- Confirm Postgres connection details for reuse from the existing VPS instance
- Decide the `context_length` value to declare to Hermes (must not exceed the smallest tier
  model's real context window — pick the models first, then set this)
- Decide whether `GET /v1/models` reports per-model `context_length` metadata for accurate
  Hermes discovery, or a single fixed value is good enough for v1
- Finalize the exact wording of the Jev decision prompt (draft above is a starting point) and
  decide the confidence threshold for falling back to a safer tier on an ambiguous answer
- Confirm whether OpenRouter's usage-analytics endpoints need a management key distinct from
  the completions key (see OpenRouter integration section)
- Decide the default for `prompt_preview` logging (on/off, truncation length) before Phase 1
  ships, since it affects the `requests` table schema
- Decide what the Phase 4 "rules editor" governs (Jev tier criteria vs. broader app rules) and
  what the built-in chatbot is actually for (config assistant / test console / support widget)

---

## Build now (v1 — Phases 1–3)

### Phase 1 — Core engine (blocks everything else)
Child tasks: `keys` auth table, `config/tiers.json` + loader, Jev decision layer with the
rule-based fallback classifier, OpenRouter provider module with streaming, `requests` logging
table (including `prompt_preview`), `POST /v1/chat/completions`, `GET /v1/models`.
**Definition of done:** a real request sent from Hermes through the router returns a correct
completion, is logged in Postgres with the right model/tier/cost/latency, and the fallback path
is demonstrated by forcing a Jev failure and confirming the request still completes.

### Phase 2 — Functional, unstyled UI
Child tasks: request log viewer (filterable, with prompt preview), spend-rate dashboard (own
totals + live OpenRouter balance), tier-mapping editor backed by the live OpenRouter model
catalog, API key create/revoke screen.
**Definition of done:** Alex can create an API key, edit a tier mapping by picking from real
OpenRouter models, view recent requests with what was spent on each, and see a spend-rate
summary that roughly matches his OpenRouter balance — entirely from the browser, no terminal use.

### Phase 3 — Design pass
Child tasks: design system/tokens, logo, navigation with drawers, dark/light toggle, responsive
layout. Does not start until Phase 2 is confirmed working — the functional shell determines
what actually needs polishing, rather than guessing up front.
**Definition of done:** see the UI critic-loop rubric below — not a self-declared "looks good."

---

## Build later (future roadmap — ideas, not commitments)

### Phase 4 — Extras (original scope)
Config editor, rules editor (scope TBD), built-in chatbot (purpose TBD), docs pages.

### Everything else — parked ideas, roughly grouped
**Cost & usage controls**
- Per-key spend budgets with alerts/auto-block when a key approaches its cap
- Fallback chains — auto-retry against a backup model if the chosen one errors or rate-limits
- Confidence-based auto-escalation made visible/tunable (a "how conservative should routing be"
  setting), rather than a hardcoded threshold

**Prompt handling**
- Optional prompt-condensing feature: shorten/clean long (often speech-to-text) prompts before
  they're sent to the main model, to save tokens. User-configurable on/off switch with a length
  threshold. Rule-based filler-stripping first (free, no model call); a cheap-model condensation
  pass only for prompts still long after that, gated so it never touches code blocks and never
  drops stated requirements
- Secret/PII redaction pass — rule-based scan for API keys, tokens, passwords accidentally
  pasted into a prompt before it leaves the server

**Trust & transparency**
- Routing transparency — return the tier, confidence score, and reasoning tag in response
  metadata or a debug panel, so the routing decision isn't a total black box
- Prompt safety/injection screening using Jev's `Noul` (yes/no) type as a cheap pre-check

**Code-specific features**
- "Code judge" feature: use Jev's `Choice` type to pick between two candidate code snippets on
  criteria like idiom/safety — separate self-contained feature (paste two snippets + criteria,
  get a pick + confidence), reusing the existing Jev client but not touching the routing path.
  Good for style/safety judgment calls; explicitly not a substitute for a linter or test suite,
  and not suited to deep multi-file architectural decisions
- Two-model consensus for complex-tier requests (opt-in "high stakes" flag) — run against two
  models and use Jev's `Score` type to rate which answer is stronger before returning one

**Product maturity (real projects, not weekend add-ons)**
- Multi-tenant support — orgs/teams, per-user roles, shared vs. private keys
- Model quality feedback loop — track error rates / thumbs-up-down per model per tier over time
  and let that data inform routing, not just the static `tiers.json`

---

## Orchestration plan — instructions for the build agent (Hermes)

**Do not build this in a single one-shot prompt.** The full scope is a full product. Build it
in the sequential phases above, each gated on the previous one actually working — not in
parallel, and not "build everything then debug."

### Sub-agent loop rules (max 3 sub-agents per loop: Planner, Builder, Critic)
- **Stop conditions must be objective, never self-declared.**
  - Backend/logic tasks: stop when there's a real, correctly-logged request/response cycle —
    not when the Critic says the code "looks correct."
  - UI/UX tasks: the Critic must render the page and take a screenshot, then check it against a
    written rubric (e.g. dark mode present, responsive at 375px width, no console errors, uses
    the defined color tokens). Judging UI quality from source code alone is unreliable — a
    critic that never sees rendered output will approve mediocre results confidently.
- **Hard cap: 3 loop iterations per task**, regardless of Critic verdict. If unresolved after 3
  iterations, flag the task and report it to Alex in plain language rather than continuing to
  loop — a stuck loop rarely self-resolves on iteration 4, it just burns cost.
- **Checkpoint after every phase.** The orchestrator pauses and summarizes in plain language
  what was built, what's been tested, and what (if anything) is unresolved before starting the
  next phase. No multi-hour silent autonomous run across all phases.
