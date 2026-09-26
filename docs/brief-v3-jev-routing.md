# Auto-Router — Brief v3: Jev-First Routing & Slimmed Feature Set

**Status: basic brief.** This is scope and direction, not a detailed spec — it gets split
into phase docs (with schemas, endpoints, Definitions of Done) the way Phases 1–2 and the
multi-provider plan were. Purpose here is to lock down *what* and *why* before the *how*.

## Purpose (revised)
A single-user tool for one vibe coder (Alex) who wants the best available AI models at the
lowest reasonable cost, with Jev doing real judgment — not a rules engine wearing an AI
costume. No multi-tenant, no orgs, no teams. Every design choice below is filtered through
one question: does this actually need Jev's judgment, or could a config file do it?

## Why Jev, specifically — the honest answer
A keyword/length heuristic can bucket a prompt by its surface shape (how long, which words
appear) but can't judge what a change actually *means* — a one-line variable rename and a
one-line auth-logic change look identical to a script, but aren't remotely the same task.
That's the one thing Jev does that a YAML file structurally cannot. Everything below is
built to actually use that, instead of flattening it back down to three buckets.

## The new Jev architecture — replaces the fixed simple/medium/complex tiers

**Favorites list, not fixed buckets.** Alex maintains a shortlist of favorite models
(roughly 5–10 — enough for real choice, few enough that Jev can meaningfully discriminate
between them). Each favorite gets a real, specific description — not a generic label, but
actual known strengths/weaknesses ("strong at algorithmic Python, noticeably weaker at
React/frontend debugging"). Jev only ever sees what's written in these descriptions — it
has no access to outside leaderboards or rankings, so a model's numeric rank on some
external list is just a note to help Alex *write* a better description, not something Jev
reads directly.

**Jev does a direct Choice among the favorites**, not a difficulty score mapped onto a
ranked list — a score-on-a-line can only say "harder/easier," it can't express "the lower-
ranked model actually wins here for a specific reason." A real Choice call, with real
per-model descriptions, is what makes "Jev recognizes model B is weaker at this specific
thing" actually possible.

**Two different tie-break rules, for two different kinds of uncertainty:**
- Uncertain between models of genuinely different capability tiers (a cheap model vs. a
  frontier one) → escalate to the more capable/expensive option. Safety-first, same logic
  as the original tier-escalation rule.
- Uncertain between two comparably-capable peers → prefer the cheaper one. This is a
  different kind of ambiguity (a coin-flip between equals, not a risk of under-provisioning)
  and deserves the opposite default.

**Budget-aware decisioning.** The user sets a budget (a spend cap, and/or a stance —
economical / balanced / best-quality). This isn't just a hard cutoff that rejects requests
once exceeded — it should shape which favorite Jev leans toward *before* the cap is hit,
via the instructions given to the Choice call, plus live spend shown to the user with a
warning approaching the limit, not just a silent rejection after.

## Auto mode — Jev picks from the full OpenRouter catalog, not just favorites

**The ask:** let Jev consider any OpenRouter model, not just the curated favorites, so a
task gets whatever genuinely fits best within budget, without Alex having to have
pre-added it to a list.

**Reality check before this gets built — being direct about the actual constraint:**
Jev can't be handed all 400+ OpenRouter models with real per-model criteria in one Choice
call — there's no practical way to write (or fit) meaningful descriptions for hundreds of
models per request, and most catalog entries have no such description to begin with, only
raw metadata (price, context length, modality, supported parameters). A literal "any of
400+ models, fully judged" mode isn't a Jev call away, it's a different, unbuilt system.

**The practical version, which still delivers what was actually asked for:**
1. **Mechanical pre-filter first** (a script, not Jev) — narrow OpenRouter's live catalog
   down using real, available fields: exclude anything over the user's per-token budget
   ceiling, anything under a minimum required context length, anything missing a needed
   capability (e.g. tool calling, if the task needs it).
2. **Jev judges the shortlist**, not the full catalog — same Choice mechanism as the
   favorites flow, just assembled dynamically per request instead of hand-curated. The
   shortlist's descriptions can lean on OpenRouter's own model description field plus
   price/context/capability data, since there's no hand-written Alex-authored blurb for
   most of the catalog.
3. Auto mode and the favorites list aren't mutually exclusive — favorites can be pinned as
   always-eligible, with auto mode filling in around them for anything a favorite doesn't
   cover well.

This is materially more complex than the favorites flow (live catalog fetch + mechanical
filter + dynamic Jev call, versus a static pre-curated list) — it's a later phase, not
something to build alongside the favorites version.

## Manual override
A user can pin a specific request to a specific model, skipping Jev entirely. Real,
named complaint from actual users of routing tools: full auto-routing with no escape
hatch is exactly what people push back on.

## Model quality feedback loop — promoted from "nice to have" to load-bearing
Hand-written weakness descriptions are only as good as Alex's memory from the last time he
used a model. Tracking real error rates / thumbs-up-down per model over time, and folding
that back into the descriptions Jev reads, is what keeps this system honest as models
update and Alex's impressions go stale. This used to be a someday item; the favorites/auto
design above genuinely depends on it working well long-term, so it's worth treating as core
sooner rather than later — even if the first version is manual (Alex edits descriptions
himself) before anything automatic.

## Prompt handling (unchanged from earlier discussion)
- Free, always-on rule-based cleanup (strip filler, repeated phrases) — no toggle needed
- Toggleable cheap-model condensation for prompts still long after cleanup, with its own
  fallback (a failed condensation call sends the original prompt through, never blocks)
- Condensation runs before Jev classification, so Jev judges the cleaned-up version

## Multi-provider & model configuration
- Real "add a model" flow: provider, model, API key, context length, reasoning level,
  on/off toggle
- Multiple providers (OpenRouter, direct Anthropic/OpenAI/Grok, local) — scoped to any
  OpenAI-compatible endpoint, not a full reimplementation of every provider's native API
- Multiple deployments per favorite for load balancing / ordered fallback
- Provider (not just model) logged per request — different providers hosting the same
  model can behave differently
- Separate Providers page for credentials (encrypted at rest, never shown back in full)

## Virtual keys
- Existing key system extended with budgets, rpm/tpm limits, allowed-model restrictions
- Keys should be genuinely differentiated (e.g. separate keys per Hermes instance) so one
  can be capped/revoked without affecting another
- Router's own auth field moved out of the persistent page header

## Model catalog UI
- Existing search extended with real filters: provider, price/cheapest-first, minimum
  context length, modality, supported parameters, free-tier-only
- No attempt to mirror OpenRouter's opaque "coding ranking" — the favorites list with
  real descriptions is the actual substitute

## Transparency
- Log the routing *reasoning* (confidence, why), not just which model was picked
- Live spend visible with a warning before a budget cap is hit

## Reliability
- A second real provider isn't just "more choice" — OpenRouter itself has had real
  outages and carries no SLA, so multi-provider is genuine outage insurance if
  OpenRouter's completion endpoint (not just Decisions) goes down

## Smaller extras (build if there's time, not core)
- "Code judge": Jev Choice between two candidate code snippets on idiom/safety
- Two-model consensus for high-stakes requests using Jev's Score type
- Secret/PII redaction pass on outgoing prompts

## Explicitly out of scope
Multi-tenant/orgs/teams — this is a single-user tool and stays one.

## Next step
Split this into phase docs once Alex confirms the shape above is right — particularly
whether Auto mode is worth building now or genuinely later, given it's the most complex
single item here.
