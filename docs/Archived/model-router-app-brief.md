# Model Router App — Build Brief

## Purpose
Replace LiteLLM as the layer between Hermes and OpenRouter. Uses Jev (OpenRouter's
Decisions endpoint) to classify each incoming prompt's complexity and route it to the
cheapest model that can handle it. Built for single-user use (Alex) initially, structured
so it can become a multi-tenant SaaS later without a rewrite.

## Stack
- **Language/framework:** Python 3.x, FastAPI (async)
- **DB driver:** asyncpg
- **LLM client:** `openai` Python client (OpenRouter is OpenAI-compatible), streaming enabled
- **Database:** Postgres — reuse the existing VPS Postgres instance, new database `jev_router`
  (kept separate from the old `litellm` database so it can be dropped later without side effects)
- **Deployment:** Docker Compose on the Vultr VPS, alongside the existing Hermes container

## Endpoints
- `POST /v1/chat/completions` — main routing endpoint, OpenAI-compatible, must support streaming
- `GET /v1/models` — returns the logical model list Hermes' model picker displays (even though
  every request gets re-routed by Jev underneath)

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
- Errors include `402 Payment Required` distinctly from other 4xx — that's OpenRouter credits
  for Decisions specifically running out, and should trigger the fallback classifier the same
  way a timeout does.
- Cost is negligible: listed at roughly $0.042 per 1M input tokens with no output-token charge,
  so calling it on every request isn't a meaningful cost driver even at scale.

## Database schema — `requests` table (kept deliberately minimal for v1)
```
id, timestamp, caller_id, tier, model, input_tokens, output_tokens,
cost_usd, latency_ms, success, error (nullable)
```
Add columns (e.g. `prompt_hash`, `jev_raw_response`) later, only once there's a concrete reason.

## Auth
`keys` table: `id, api_key, caller_id, created_at`. One row for Alex today. Adding a second
caller later is an insert, not a code change.

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
- Decide what the Phase 4 "rules editor" governs (Jev tier criteria vs. broader app rules) and
  what the built-in chatbot is actually for (config assistant / test console / support widget)

## Orchestration plan — instructions for the build agent (Hermes)

**Do not build this in a single one-shot prompt.** The full scope (routing engine, auth, API
key management, config/rules editing, a built-in chatbot, docs, and a polished UI) is a full
product. Build it in the four sequential phases below, each gated on the previous one actually
working — not in parallel, and not "build everything then debug."

### Phase 1 — Core engine (blocks everything else)
Child tasks: `keys` auth table, `config/tiers.json` + loader, Jev decision layer with the
rule-based fallback classifier, OpenRouter provider module with streaming, `requests` logging
table, `POST /v1/chat/completions`, `GET /v1/models`.
**Definition of done:** a real request sent from Hermes through the router returns a correct
completion, is logged in Postgres with the right model/tier/cost/latency, and the fallback path
is demonstrated by forcing a Jev failure and confirming the request still completes.

### Phase 2 — Functional, unstyled UI
Child tasks: request log viewer, raw tier-mapping editor, API key create/revoke screen.
**Definition of done:** Alex can create an API key, edit a tier mapping, and view recent
requests, entirely from the browser, with no terminal use.

### Phase 3 — Design pass
Child tasks: design system/tokens, logo, navigation with drawers, dark/light toggle, responsive
layout. Does not start until Phase 2 is confirmed working — the functional shell determines
what actually needs polishing, rather than guessing up front.
**Definition of done:** see the UI critic-loop rubric below — not a self-declared "looks good."

### Phase 4 — Extras
Child tasks: config editor, rules editor (scope TBD — see open items), built-in chatbot
(purpose TBD — see open items), docs pages. Does not start until Phases 1–3 are done.

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
  next phase. No multi-hour silent autonomous run across all four phases.
