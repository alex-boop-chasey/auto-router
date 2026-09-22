from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import SETTINGS


def _normalise(prompt: str) -> str:
    return re.sub(r"\s+", " ", prompt).strip().lower()


def fallback_classify(prompt: str) -> str:
    """Rule-based fallback when the Jev decision layer fails.

    Phase 1 concrete heuristic:
    - Normalise prompt and count whitespace-separated words.
    - If any COMPLEX_KEYWORDS is found in the normalised text → "complex".
    - Else if word count is at most SIMPLE_WORD_THRESHOLD and any SIMPLE_KEYWORDS
      is found → "simple".
    - Else if word count is at least COMPLEX_WORD_THRESHOLD → "complex".
    - Otherwise → "medium".
    """
    text = _normalise(prompt)
    words = text.split()
    word_count = len(words)

    if any(kw in text for kw in SETTINGS.complex_kw_list):
        return "complex"

    if word_count <= SETTINGS.simple_word_threshold and any(kw in text for kw in SETTINGS.simple_kw_list):
        return "simple"

    if word_count >= SETTINGS.complex_word_threshold:
        return "complex"

    return "medium"


def flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Flatten a Hermes/OpenAI message list into the plain-text state Jev expects.

    Only user-role content can be sent as the `state` value. Earlier turns are
    summarised as a short prefix so classification still has context.
    """
    parts: list[str] = []
    current = ""
    for i, msg in enumerate(messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        if role == "user" and i == len(messages) - 1:
            current = content
        else:
            label = {"system": "SYS", "assistant": "AST", "tool": "TOOL", "user": "USR"}.get(
                role, role.upper()
            )
            parts.append(f"[{label}] {content}")

    history = " | ".join(parts[-4:])
    if history:
        return f"HISTORY: {history}\nCURRENT: {current}".strip()
    return current


class TierClassifier:
    """Jev/OpenRouter Decisions integration with fallback."""

    def __init__(self) -> None:
        self.url = SETTINGS.jev_url
        self.model = SETTINGS.jev_model
        self.confidence_gap = SETTINGS.jev_confidence_gap
        self.timeout = 10.0

    @staticmethod
    def _resolve_with_confidence(probabilities: dict[str, float], choice: str) -> str:
        """Apply the Phase 1 confidence-gap escalation rule."""
        tiers_order = {"simple": 0, "medium": 1, "complex": 2}
        sorted_probs = sorted(
            probabilities.items(),
            key=lambda kv: (-kv[1], -tiers_order.get(kv[0], 0)),
        )
        if len(sorted_probs) < 2:
            return sorted_probs[0][0] if sorted_probs else "medium"

        top_tier, top_prob = sorted_probs[0]
        second_tier, second_prob = sorted_probs[1]

        gap = top_prob - second_prob
        if gap < SETTINGS.jev_confidence_gap:
            return (
                top_tier
                if tiers_order[top_tier] > tiers_order[second_tier]
                else second_tier
            )
        return top_tier

    async def classify(self, prompt: str) -> tuple[str, bool, str | None]:
        """Return (tier, used_fallback, jev_error_message)."""
        payload = {
            "model": self.model,
            "questions": {
                "tier": {
                    "type": "choice",
                    "instructions": "Which complexity tier does this task belong to?",
                    "criteria": {
                        "simple": "Short factual, greeting, or trivial question a cheap model handles perfectly",
                        "medium": "Normal coding, reasoning, or multi-step task benefiting from a mid-tier model",
                        "complex": "Hard reasoning, long context, or high-stakes task needing a frontier model",
                    },
                }
            },
            "state": prompt,
        }
        headers = {
            "Authorization": f"Bearer {SETTINGS.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://auto-router.local/",
            "X-Title": "auto-router",
        }

        jev_error: str | None = None

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    self.url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
            except (httpx.TimeoutException, httpx.NetworkError, TimeoutError) as exc:
                jev_error = f"Network/timeout: {type(exc).__name__}: {exc}"
            else:
                if resp.status_code in {400, 401, 403}:
                    jev_error = f"Auth/malformed: HTTP {resp.status_code} - {resp.text[:200]}"
                elif resp.status_code in {402, 429, 503, 529, 524}:
                    jev_error = f"Service/credits/rate-limit: HTTP {resp.status_code} - {resp.text[:200]}"
                elif resp.status_code >= 400:
                    jev_error = f"Unexpected HTTP {resp.status_code} - {resp.text[:200]}"
                else:
                    try:
                        body = resp.json()
                    except json.JSONDecodeError:
                        jev_error = f"Invalid JSON response: {resp.text[:200]}"
                    else:
                        try:
                            answer: dict[str, Any] = body["answers"]["tier"]
                            choice = answer.get("choice", "")
                            probabilities = answer.get("probabilities", {choice: 1.0})
                            if set(probabilities.keys()) != {"simple", "medium", "complex"}:
                                probabilities = {
                                    "simple": 0.0,
                                    "medium": 0.0,
                                    "complex": 0.0,
                                    **probabilities,
                                }
                            if choice in {"simple", "medium", "complex"}:
                                return self._resolve_with_confidence(probabilities, choice), False, None
                            jev_error = f"Unexpected tier choice: {choice}"
                        except KeyError as exc:
                            jev_error = f"Missing field: {exc}"

        return fallback_classify(prompt), True, jev_error


async def classify_tier(prompt: str) -> tuple[str, bool, str | None]:
    return await TierClassifier().classify(prompt)
