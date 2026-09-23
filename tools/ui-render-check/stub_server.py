"""Render harness for the Phase 3 UI — dev tool, not part of the app.

Serves app/static/ exactly as the FastAPI app does (index at /, assets under /static/) plus
stub /health and /api/* JSON, so the frontend can be rendered and exercised in a browser
without Postgres, OpenRouter, or the Docker stack. This is what lets the Phase 3 Critic
rubric ("render it and actually look at the screenshot") be checked objectively.

Run:  python3 tools/ui-render-check/stub_server.py
Env:  AR_STATIC_DIR (default: this repo's app/static), AR_STUB_PORT (default 8765)

It intentionally has NO auth: it returns canned data for every /api/* route.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import urllib.parse

STATIC = pathlib.Path(
    os.environ.get("AR_STATIC_DIR", pathlib.Path(__file__).resolve().parents[2] / "app" / "static")
)
PORT = int(os.environ.get("AR_STUB_PORT", "8765"))

_now = "2026-09-23T05:40:00Z"

REQUESTS = [
    {
        "timestamp": _now, "caller_id": "hermes", "tier": "simple",
        "model": "openai/gpt-4o-mini", "confidence": 0.91, "probability_gap": 0.44,
        "escalation_fired": False, "input_tokens": 812, "output_tokens": 190,
        "cost_usd": 0.000214, "latency_ms": 640, "success": True,
        "used_fallback": False, "jev_error": None, "error": None,
        "prompt_preview": "Summarise this changelog entry.",
        "probabilities": {"simple": 0.91, "medium": 0.47, "complex": 0.02},
    },
    {
        "timestamp": _now, "caller_id": "hermes", "tier": "complex",
        "model": "anthropic/claude-3.5-sonnet", "confidence": 0.63, "probability_gap": 0.08,
        "escalation_fired": True, "input_tokens": 15402, "output_tokens": 3877,
        "cost_usd": 0.091200, "latency_ms": 8420, "success": True,
        "used_fallback": False, "jev_error": None, "error": None,
        "prompt_preview": "Refactor this router to support streaming tool calls.",
        "probabilities": {"simple": 0.05, "medium": 0.55, "complex": 0.63},
    },
    {
        "timestamp": _now, "caller_id": "cron-nightly", "tier": "medium",
        "model": "openai/gpt-4o", "confidence": 0.72, "probability_gap": 0.19,
        "escalation_fired": False, "input_tokens": 2210, "output_tokens": 640,
        "cost_usd": 0.012400, "latency_ms": 2100, "success": False,
        "used_fallback": True, "jev_error": "jev timeout after 3 attempts",
        "error": "upstream 502 from OpenRouter", "prompt_preview": None,
        "probabilities": None,
    },
]

SPEND = {
    "bucket": "day", "total_cost": 0.103814, "total_requests": 3,
    "input_tokens": 18424, "output_tokens": 4707,
    "by_period": [{"period": _now, "cost": 0.103814, "requests": 3}],
    "by_tier": [
        {"tier": "simple", "cost": 0.000214, "requests": 1},
        {"tier": "medium", "cost": 0.012400, "requests": 1},
        {"tier": "complex", "cost": 0.091200, "requests": 1},
    ],
    "by_model": [
        {"model": "anthropic/claude-3.5-sonnet", "cost": 0.091200, "requests": 1},
        {"model": "openai/gpt-4o", "cost": 0.012400, "requests": 1},
        {"model": "openai/gpt-4o-mini", "cost": 0.000214, "requests": 1},
    ],
}

MODELS = {"data": [
    {"id": "openai/gpt-4o-mini", "name": "GPT-4o mini", "context_length": 128000,
     "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}},
    {"id": "openai/gpt-4o", "name": "GPT-4o", "context_length": 128000,
     "pricing": {"prompt": "0.0000025", "completion": "0.00001"}},
    {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet", "context_length": 200000,
     "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
    {"id": "deepseek/deepseek-chat", "name": "DeepSeek V3", "context_length": 64000,
     "pricing": {"prompt": "0.00000014", "completion": "0.00000028"}},
]}

ROUTES = {
    "/health": {"status": "ok", "last_successful_decision_at": _now},
    "/api/requests": {"rows": REQUESTS, "total": 3, "limit": 50, "offset": 0},
    "/api/spend": SPEND,
    "/api/openrouter/credits": {"balance": 41.2077, "total_credits": 50.0, "total_usage": 8.7923},
    "/api/openrouter/models": MODELS,
    "/api/models/enabled": {"disabled": ["deepseek/deepseek-chat"]},
    "/api/tiers": {
        "simple": {"model": "openai/gpt-4o-mini", "context_length": 128000},
        "medium": {"model": "openai/gpt-4o", "context_length": 128000},
        "complex": {"model": "anthropic/claude-3.5-sonnet", "context_length": 200000},
    },
    "/api/keys": {"data": [
        {"id": "1", "caller_id": "hermes", "api_key": "sk-router-...a91f",
         "prompt_preview_enabled": True, "created_at": _now},
    ]},
    "/api/settings": {
        "settings": {"routing_conservatism": "balanced", "confidence_gap_threshold": 0.15,
                     "prompt_preview_default": True},
        "presets": {"aggressive": 0.05, "balanced": 0.15, "conservative": 0.30},
    },
}

CTYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - matches BaseHTTPRequestHandler
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _static(self, rel):
        p = STATIC / rel
        if not p.is_file():
            return self._send(404, "not found", "text/plain")
        self._send(200, p.read_bytes(), CTYPES.get(p.suffix, "application/octet-stream"))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path in ROUTES:
            return self._send(200, json.dumps(ROUTES[path]))
        return self._send(404, json.dumps({"detail": "not found"}))


if __name__ == "__main__":
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"stub serving {STATIC} on http://127.0.0.1:{PORT}", flush=True)
        httpd.serve_forever()
