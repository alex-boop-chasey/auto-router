# UI render check (Phase 3)

A dev-time tool that renders the admin UI in headless Chromium and asserts the Phase 3
rubric **objectively**, instead of a sub-agent declaring its own work "looks good":

- dark mode is present, actually repaints, and persists
- responsive at 375px with **no horizontal scroll** and no element wider than the viewport
- tables collapse into labelled stacked cards on mobile
- the nav drawer opens/closes, is hit-testable, closes on Escape, and takes focus
- no console errors / uncaught page errors / failed requests
- design tokens are actually in use (Inter, brand primary `rgb(1, 97, 239)`, `--bg-page`)

It also guards the specific regressions found during Phase 3 (the backdrop/stacking-context
bug that made drawer links unclickable, the unshrinkable API-key input that forced a 446px
page on a 375px phone, and inconsistent badge casing).

## Why it exists

The Phase 3 Definition of Done requires an **actual rendered screenshot**, and its Critic
rubric says judging from source alone is unreliable. `stub_server.py` serves `app/static/`
plus canned `/api/*` JSON, so the UI renders with no Postgres, no OpenRouter, and no Docker
stack — the check runs anywhere Python and Node are available.

This tool is **not** part of the app: the Dockerfile copies only `app/` and `config/`, so
nothing here reaches the container image.

## Run it

```bash
# once, from this directory
npm i playwright

# terminal 1 — stub backend + static assets
python3 tools/ui-render-check/stub_server.py

# terminal 2 — drive the browser and write screenshots + report.json into ./shots
node tools/ui-render-check/verify.mjs
```

Exit code is non-zero if any check fails.

If Chromium isn't already installed, point it at an existing binary:

```bash
AGENT_BROWSER_EXECUTABLE_PATH=/path/to/chrome-headless-shell node tools/ui-render-check/verify.mjs
```

## Env overrides

| Variable | Default | Purpose |
|---|---|---|
| `AR_BASE_URL` | `http://127.0.0.1:8765/` | Point at a running stack instead of the stub |
| `AR_SHOTS_DIR` | `./shots` | Where screenshots and `report.json` go |
| `AR_STATIC_DIR` | repo `app/static` | What the stub serves |
| `AR_STUB_PORT` | `8765` | Stub listen port |
| `AGENT_BROWSER_EXECUTABLE_PATH` | — | Explicit Chromium binary |

To run the same checks against the **real** stack, start the compose stack on the host and
use `AR_BASE_URL=http://localhost:8000/` — but note the real app requires a router API key,
so unauthenticated `/api/*` calls will 401 and the "no failed requests" check will fail by
design. The stub exists to keep that check meaningful.
