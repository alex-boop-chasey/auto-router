# Model Router App — Phase 4 & Future Roadmap

**Status: not build-now.** Nothing in this doc is committed or scoped tightly enough to
greenlight directly — treat it as a backlog to pull from, not a spec to hand to a build
agent as-is. If/when Alex picks something from here to actually build, it needs its own
short brief (endpoints, schema, Definition of Done) written the way Phases 1–3 were, before
it goes to Hermes — including deciding its own sub-agent loop iteration cap using the same
split as Phases 1–3: deterministic/backend goals (most of the items below) keep a hard cap
of 3 with no exceptions; anything with a real visual/UX component (e.g. a "code judge" UI,
a rules editor's interface) should use Phase 3's iteration-budget pattern — cap at 3, then
a fresh capped loop after Alex gives one sentence of direction — rather than a longer
unsupervised loop.

## Dependency
Phases 1–3 (core engine, functional UI, design pass) should all be done first. Nothing here
blocks on a specific order among themselves.

## Stack & deployment (inherited from Phase 1)
- Local Mac build via Docker Compose (Docker Desktop).
- Fresh Postgres instance (database `jev_router`) started by the same compose file.
- Kimi runs inside Hermes' sandboxed container (no root, no Docker-in-Docker). It writes
  code; Alex runs `docker compose up` on the host. Never request or accept a password.

## Phase 4 — Extras (original scope)
- Config editor
- Rules editor — **scope undecided**: does this govern the Jev tier criteria specifically,
  or broader app rules? Needs deciding before this is buildable.
- Built-in chatbot — **purpose undecided**: config assistant, test console, or support
  widget are all different builds. Needs deciding before this is buildable.
- Docs pages

## Cost & usage controls
- Per-key spend budgets with alerts/auto-block when a key approaches its cap
- Fallback chains — auto-retry against a backup model if the chosen one errors or rate-limits
- Confidence-based auto-escalation made visible/tunable in the UI (a "how conservative
  should routing be" setting), rather than the hardcoded 0.15 threshold from Phase 1

## Prompt handling
- **Prompt-condensing feature:** shorten/clean long (often speech-to-text) prompts before
  they're sent to the main model, to save tokens. User-configurable on/off switch with a
  length threshold. Rule-based filler-stripping first (free, no model call); a cheap-model
  condensation pass only for prompts still long after that — gated so it never touches code
  blocks and never drops stated requirements.
- **Secret/PII redaction pass** — rule-based scan for API keys, tokens, and passwords
  accidentally pasted into a prompt before it leaves the server.

## Trust & transparency
- Routing transparency — return the tier, confidence score, and reasoning tag in response
  metadata or a debug panel, so the routing decision isn't a total black box.
- Prompt safety/injection screening using Jev's `noul` (yes/no) question type as a cheap
  pre-check.

## Code-specific features
- **"Code judge" feature:** use Jev's `choice` question type to pick between two candidate
  code snippets on criteria like idiom/safety. Self-contained: paste two snippets +
  criteria, get a pick + confidence back. Reuses the Jev client already built in Phase 1
  but doesn't touch the routing path. Good for style/safety judgment calls; explicitly not
  a substitute for a linter or test suite, and not suited to deep multi-file architectural
  decisions.
- **Two-model consensus for complex-tier requests** (opt-in "high stakes" flag) — run
  against two models and use Jev's `score` question type to rate which answer is stronger
  before returning one.

## Product maturity (real projects, not weekend add-ons)
- **Multi-tenant support** — orgs/teams, per-user roles, shared vs. private keys.
- **Model quality feedback loop** — track error rates / thumbs-up-down per model per tier
  over time and let that data inform routing, not just the static `tiers.json`.

## Explicitly out of scope, even long-term, unless revisited
Rate limiting, per-user billing, signup/onboarding flow were all explicitly deferred in the
original brief for single-user use. The `keys` table design leaves room for a `budget_usd` /
`rpm_limit` column per row later without a rebuild, but building that is not scheduled.
