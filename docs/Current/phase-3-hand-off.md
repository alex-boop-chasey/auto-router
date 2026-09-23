# Phase 3 Hand-off — Design pass

**Status:** code complete on branch `feature/phase-3-design-pass`. Sandbox-verifiable work
passes **and** the render-based rubric was actually run (41/41) with screenshots committed to
`docs/Current/phase-3-screenshots/`. Host rebuild still needed to see it on your own browser.

## Aesthetic direction

Resolved before starting — the phase doc flagged that inventing a look from nothing was the
most likely way to waste this phase. Alex chose **match rebirthwebdesign.com.au** (brand
consistency). Tokens were **extracted from the live site's compiled CSS**, not invented, and
recorded in `docs/Current/phase-3-design-pass.md` → "Aesthetic direction — RESOLVED".

Short version: Inter throughout; vivid blue `#0161EF` primary, `#0154CF` hover, violet
`#6D28D9` accent, success `#107A57`; white/near-black light theme, `#030620` deep-navy dark
theme with `#E5ECF6` text; pill radii as the dominant motif, 6–12px on cards/inputs; sticky
header with a horizontally-centred nav that collapses to a drawer. Kept **denser than the
marketing site** — this is a log/table tool, so type scale and spacing are tightened.

## What was built

- **Design tokens** (`app/static/tokens.css`, new) — single source of truth for colour, type,
  space, radius, elevation, light + dark. Every component reads these; no scattered one-offs.
- **Logo** (`app/static/logo.svg`, new) — inline SVG router mark (input node → hub → two
  branch outputs) in the brand gradient.
- **Restructured shell** (`app/static/index.html`) — sticky header (logo/wordmark + pill nav +
  theme toggle + hamburger), auth/health toolbar, content in cards. Theme is set by an inline
  boot script **before first paint**, so there is no flash of the wrong theme.
- **Stylesheet** (`app/static/style.css`, rewritten) — token-driven components, responsive
  breakpoints, mobile nav drawer, and the mobile table→card transform.
- **Behaviour** (`app/static/app.js`) — dark/light toggle persisted in `localStorage` (falls
  back to `prefers-color-scheme`), drawer open/close with backdrop + Escape + focus-in, active
  nav tracking, and per-cell `data-label`s that drive the mobile card layout.
- **Render check** (`tools/ui-render-check/`, new) — stub backend + Playwright script that
  renders the UI and asserts the rubric. Dev-only; the Dockerfile copies only `app/` and
  `config/`, so it cannot reach the image.
- **Tests** (`tests/test_admin_api.py`) — extended the UI-serving test to pin the Phase 3
  assets (`tokens.css`, `style.css`, `logo.svg`, theme/nav toggles) and added a static-asset
  test. **49 passed** (was 48).

## Findings — 4 real bugs, all found by rendering rather than reading

The phase doc was right that judging from source alone is unreliable. Every one of these
looked fine in the source and only showed up in a browser:

1. **Drawer links were completely unclickable on mobile.** `.app-header` has `z-index: 40`,
   which creates a stacking context, so the drawer (a header descendant, `z-index: 60`) could
   never paint above the backdrop (`z-index: 55`). The backdrop covered every drawer link —
   the drawer *opened* and looked correct, but nothing in it could be tapped. Fixed by putting
   the backdrop *below* the header (`z-index: 30`); it is inset below the header anyway.
2. **Horizontal scroll at 375px.** The auth-key input's `size="36"` gave it a ~380px
   intrinsic width, and without `min-width: 0` that became a flex min-content floor the
   toolbar couldn't shrink below — forcing the page to **446px** on a 375px phone. Fixed by
   dropping `size=` and making the field/input shrinkable + full-width on mobile.
3. **Focus-into-drawer silently did nothing.** `focus()` was called in the same task the
   `is-open` class was added, before the browser recomputed `visibility`, so it no-op'd on a
   still-hidden element. Also `visibility` was being *animated*, which kept the drawer
   unfocusable for a frame. Fixed in both places.
4. **The Request log tab was empty on page load.** `loadLog()` was only called from the nav
   click handler, so the default visible tab showed an empty table until you clicked the tab
   you were already on.

Also fixed from the screenshot review: routing badges were mixed-case
(`normal`/`escalated`/`FALLBACK`) — now uniformly uppercase; the API-key placeholder was
truncated mid-word; and the theme toggle's accessible name now states the action
("Switch to dark mode") rather than being ambiguous about which icon means what.

## Sandbox verification (already done)

- `ruff check app tests` — clean.
- `pytest -q` — **49 passed** (48 + 1 new static-asset test).
- **Rendered Chromium run — 41/41 checks**, against a stub backend serving the real
  `app/static/`. Covers: dark mode toggles *and* repaints *and* persists (checked via the
  `--bg-page` token, not just the attribute); all six tabs render real content; routing
  transparency badges + expandable per-request detail; no horizontal scroll at 375px; no
  element wider than the viewport; tables collapse to labelled cards; drawer opens, is
  hit-testable, closes on Escape and on tab-pick, and takes focus; and **zero console errors,
  page errors, or failed requests**.
- Screenshots at 1280px and 375px, light and dark, plus the open drawer, in
  `docs/Current/phase-3-screenshots/` (with `report.json`).
- Scribbled over: `node --check app/static/app.js` clean; app imports and serves all assets.

Reproduce with `tools/ui-render-check/README.md` (needs Python + Node; no Postgres/Docker).

## One note on the loop rules

Ran a single capped loop (builder → render-based critic) and it converged — no iterations were
exhausted, so there was no need to spend the iteration-budget escalation. The screenshots are
committed anyway: this is the phase where your own taste is the deciding vote, so it is worth
looking at `docs/Current/phase-3-screenshots/desktop-light.png` and `mobile-light.png` before
Phase 4 is greenlit and telling me anything that reads wrong. That starts a fresh capped loop
rather than me guessing.

## Host steps

Code changed, so the image must be rebuilt:

```bash
cd /Users/alex/hermes-stack/hermes-data/projects/auto-router
git checkout feature/phase-3-design-pass
docker compose up --build -d
```

Then open <http://localhost:8000/> and confirm:

| # | DoD item | How to check |
|---|----------|--------------|
| 1 | Dark mode present and functional | Toggle top-right; page repaints, survives a reload |
| 2 | Responsive at 375px, no horizontal scroll | DevTools device toolbar → iPhone SE (375px); scroll sideways, nothing should move |
| 3 | No console errors | DevTools console should be empty on load and after clicking through all tabs |
| 4 | Tokens used consistently | Toggle dark — every surface/border/badge should switch, nothing left white |
| 5 | Nav drawer works on mobile | At 375px: hamburger → drawer opens, links are tappable, Escape closes it |

## Notes / limitations

- **Inter is loaded from Google Fonts** (`fonts.googleapis.com`) in `index.html`. Offline the
  UI falls back to the system sans stack — deliberate, but worth knowing if this ever runs on
  a network with no egress. Self-hosting the font would be a one-file change.
- The mobile table→card transform relies on each `<td>` carrying `data-label`; new columns
  added in `app.js` need a label or they render unlabelled on mobile. Called out in a comment
  next to the `cell()` helper.
- Phase 2's caveat still stands: `/api/*` uses the same bearer router key, stored in
  `localStorage` — fine for a localhost tool, revisit if exposed.
- `tools/ui-render-check/node_modules` is gitignored; the tool is not wired into CI (there is
  no CI yet). It exits non-zero on failure if that is ever useful.
