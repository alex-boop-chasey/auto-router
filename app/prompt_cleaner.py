"""
Prompt cleaner for auto-router.

Two layers:
  1. Basic (regex, free) — removes filler words, fixes doubled words,
     normalises whitespace. Always runs first.
  2. Smart compaction (LLM, toggleable) — rewrites long waffling prompts
     into concise prompts using the cheapest capable model before the
     prompt hits Jev classification and the real model.

Settings (in DB settings table):
  - prompt_cleaning_enabled : bool  (default True)   — basic regex clean
  - prompt_compaction_enabled : bool (default False)  — LLM compaction
  - prompt_compaction_model : str   (default "openai/gpt-4o-mini")
  - prompt_compaction_min_chars : int (default 500)   — only compact if prompt > N chars
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Basic cleaning (regex, always runs if enabled)
# ---------------------------------------------------------------------------

# Filler words/phrases to strip — case-insensitive, word-boundary aware
FILLER_PATTERNS: list[tuple[str, str]] = [
    # Isolated filler words (preceded/followed by space or boundary)
    (r"\bum[,\s]+", " "),           # "um, " → " "
    (r"\buh[,\s]+", " "),           # "uh, " → " "
    (r"\bah[,\s]+", " "),           # "ah, " → " "
    (r"\ber[,\s]+", " "),           # "er, " → " "
    (r"\bhm[,\s]+", " "),           # "hm, " → " "
    (r"\buhh+\b", ""),              # "uhhh" → ""
    (r"\bumm+\b", ""),              # "ummm" → ""
    (r"\blike\b(?=\s+(?:I|it|the|that|this|a|an))", ""),  # "like I was saying" → "I was saying"
    (r"\byou know[,\s]+", " "),     # "you know, " → " "
    (r"\bI mean[,\s]+", " "),       # "I mean, " → " "
    # Filler at sentence starts (line start or after sentence-ending punctuation)
    (r"^\s*(?:um|uh|ah|er|like|so|well|yeah|ok|okay|right|alright)[,\s]+", ""),
    (r"[.!?]\s+(?:um|uh|ah|er|like|so|well|yeah|ok|okay|right|alright)[,\s]+", ". "),
]

# Repeated word stutter (case insensitive): "the the" → "the"
_REPEATED_WORD = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE)

# Multiple spaces/tabs → single space
_MULTI_SPACE = re.compile(r"[ \t]+")

# Space before punctuation
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")

# Missing space after punctuation
_MISSING_SPACE = re.compile(r"([,.;:!?])([A-Za-z])")


def basic_clean(text: str) -> str:
    """Remove filler words, fix stutters, normalise whitespace. Free, fast, regex-based."""
    if not text:
        return text

    # Apply filler patterns
    for pattern, replacement in FILLER_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Fix repeated words (stutter)
    # Run multiple times in case of "the the the" → needs 2 passes
    for _ in range(3):
        new_text = _REPEATED_WORD.sub(r"\1", text)
        if new_text == text:
            break
        text = new_text

    # Normalise whitespace
    text = _MULTI_SPACE.sub(" ", text)

    # Fix spacing around punctuation
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MISSING_SPACE.sub(r"\1 \2", text)

    return text.strip()


# ---------------------------------------------------------------------------
# Smart compaction (LLM, toggleable)
# ---------------------------------------------------------------------------

COMPACTION_SYSTEM_PROMPT = """\
You are a prompt compactor. Rewrite the user's long, waffling, or
repetitive message into a shorter, more concise version that preserves
all key information and intent. Do NOT add new facts or change the
meaning. Output ONLY the compacted prompt, no explanations or quotes.

Rules:
- Remove filler and redundant phrasing
- Merge repeated points
- Preserve all technical details, code references, and specific asks
- If the original is already concise, return it unchanged
- Aim for 30-50% shorter while keeping everything important
"""


COMPACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "compacted": {
            "type": "string",
            "description": "The compacted, concise version of the prompt",
        },
    },
    "required": ["compacted"],
}


async def smart_compact(
    prompt: str,
    *,
    model: str,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
    max_tokens: int = 1024,
    timeout: float = 15.0,
) -> str:
    """Rewrite a long prompt into a concise version using a cheap LLM.

    Only call this when the prompt is long/waffling — use the caller's
    `prompt_compaction_min_chars` threshold as a gate.
    """
    import json as _json

    import httpx

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": COMPACTION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://auto-router.local/",
        "X-Title": "auto-router-compactor",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------------------
# Combined pipeline
# ---------------------------------------------------------------------------


async def clean_prompt(
    prompt: str,
    *,
    settings: dict[str, Any],
    openrouter_api_key: str,
) -> tuple[str, dict[str, Any]]:
    """Run the full prompt cleaning pipeline.

    Returns (cleaned_prompt, meta) where meta describes what happened.
    """
    meta: dict[str, Any] = {
        "original_length": len(prompt),
        "basic_cleaned": False,
        "compacted": False,
        "compaction_model": None,
        "compaction_cost": None,
    }

    # Layer 1: basic regex cleaning
    if settings.get("prompt_cleaning_enabled", True):
        cleaned = basic_clean(prompt)
        if cleaned != prompt:
            meta["basic_cleaned"] = True
            prompt = cleaned
            meta["after_basic_length"] = len(prompt)

    # Layer 2: smart compaction (only if enabled AND prompt is long enough)
    if (
        settings.get("prompt_compaction_enabled", False)
        and len(prompt) >= settings.get("prompt_compaction_min_chars", 500)
    ):
        try:
            compacted = await smart_compact(
                prompt,
                model=settings.get("prompt_compaction_model", "openai/gpt-4o-mini"),
                api_key=openrouter_api_key,
            )
            if compacted and len(compacted) < len(prompt) * 0.95:
                meta["compacted"] = True
                meta["compaction_model"] = settings.get("prompt_compaction_model")
                meta["after_compaction_length"] = len(compacted)
                meta["compression_ratio"] = round(len(compacted) / max(len(prompt), 1), 3)
                prompt = compacted
        except Exception:
            # Compaction failure is non-fatal — proceed with original
            meta["compaction_error"] = True

    meta["final_length"] = len(prompt)
    return prompt, meta