# Model Router App — Phase 3: Design Pass

**Status: build now — but only after Phase 2's Definition of Done is confirmed, and only
after the aesthetic direction gap below is resolved.** Unlike Phases 1 and 2, this phase is
NOT ready to greenlight as-is — see "Before starting this phase" first.

## Dependency on Phase 2
This phase styles the functional shell built in Phase 2. It does not add new features or
endpoints — if something's missing functionally, that's a Phase 2 bug, not a Phase 3 task.

## Stack & deployment (inherited from Phase 1)
- Local Mac build via Docker Compose (Docker Desktop).
- Fresh Postgres instance (database `jev_router`) started by the same compose file.
- Kimi runs inside Hermes' sandboxed container (no root, no Docker-in-Docker). It writes
  code and assets; Alex runs `docker compose up` on the host. Never request or accept a
  password.

## Before starting this phase — the one real gap in this brief
Nothing in the build brief specifies what this app should actually *look like*. The
Definition of Done below is a quality bar (dark mode present, responsive, no console
errors) — it says how to check the work, not what direction to aim it in. Left as-is, the
build agent has to invent an aesthetic from nothing, and since Alex runs a web design
business, a generic invented result is the most likely outcome to get rejected and rebuilt.

**Resolve this before telling Hermes to start Phase 3** — pick one:
- Point it at a reference: an existing site, app, or mood board to match the feel of
  (could be rebirthwebdesign.com.au for brand consistency, or something else entirely if
  this should feel distinct from the agency's own site — a developer tool doesn't have to
  look like a marketing site)
- Give it a short explicit direction in a sentence or two (e.g. "dark, minimal, monospace
  accents, nothing that looks like a generic SaaS dashboard template")
- Provide actual design tokens (hex codes, font choices) if Alex already has a preference

Whichever of these Alex picks, add it to this doc as a new section before greenlighting.

## Scope
- Design system / tokens (color palette, type scale, spacing)
- Logo
- Navigation with drawers
- Dark/light toggle
- Responsive layout across the UI built in Phase 2

## Critic rubric — how "done" gets checked
The Critic must actually render the page and take a screenshot, then check it against this
written rubric — judging from source code alone is unreliable and will approve mediocre
results confidently:
- Dark mode present and functional (not just a toggle that does nothing)
- Responsive at 375px width (phone) with no horizontal scroll or broken layout
- No console errors on load or on interaction
- Uses the defined color/spacing tokens consistently — not one-off inline styles scattered
  through components
- Navigation drawers open/close correctly and don't trap focus or break on mobile

## Definition of Done
The rubric above passes, confirmed by an actual rendered screenshot, not a self-declared
"looks good" from the Critic sub-agent.
(Prerequisite: the Docker Compose stack is running on the host so the UI can be rendered.)

## Sub-agent loop rules (apply to this phase)
Max 3 sub-agents per loop: Planner, Builder, Critic.
- **Stop conditions must be objective** — the screenshot-plus-rubric check above, not the
  Critic's opinion of its own work.
- **Hard cap stays at 3 loop iterations per child task — do not raise it.** Visual/design
  work genuinely tends to need more than 3 passes to land somewhere good, unlike Phase 1
  and 2's deterministic pass/fail goals. The fix for that here isn't a longer unsupervised
  loop (more iterations without your input just means more attempts at guessing your
  taste) — it's the iteration budget pattern below.

### Iteration budget — what happens when a task hits the 3-iteration cap
This is expected to happen in this phase more than in Phases 1–2, and isn't a failure by
itself. When a child task is flagged unresolved after 3 iterations:
1. The orchestrator reports it to Alex with the current screenshot and a plain-language
   summary of what's been tried.
2. Alex reviews the screenshot and gives one sentence of direction (e.g. "too much
   whitespace in the header" or "nav feels cramped on mobile").
3. A **new** capped loop (another 3 iterations) starts from that direction.
This keeps Alex in the loop specifically on the part of the build most likely to need his
own taste, rather than trusting a longer unsupervised loop to converge on something he'd
actually pick — repeat as many capped loops as needed, rather than widening any single one.

- **Checkpoint when this phase is done**, before Phase 4 starts (if Phase 4 is greenlit at
  all — see the roadmap doc, none of it is committed yet).
