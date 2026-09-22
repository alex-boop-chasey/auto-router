from __future__ import annotations

import re
from typing import Any

from .config import SETTINGS


def _normalise(prompt: str) -> str:
    return re.sub(r"\s+", " ", prompt).strip().lower()


def fallback_classify(prompt: str) -> str:
    """Rule-based fallback when the Jev decision layer fails.

    Phase 1 concrete heuristic:
    - Normalise prompt and count whitespace-separated words.
    - If any COMPLEX_KEYWORDS is found in the normalised text -> "complex".
    - Else if word count is at most SIMPLE_WORD_THRESHOLD and any SIMPLE_KEYWORDS
      is found -> "simple".
    - Else if word count is at least COMPLEX_WORD_THRESHOLD -> "complex".
    - Otherwise -> "medium".
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
