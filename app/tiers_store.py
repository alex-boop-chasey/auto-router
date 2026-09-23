from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import TIERS_PATH, load_tiers

VALID_TIERS = ("simple", "medium", "complex")


class TierValidationError(ValueError):
    """Raised when a proposed tiers mapping is malformed."""


def validate_tiers(tiers: dict[str, Any]) -> dict[str, Any]:
    """Validate a full tiers mapping and return a normalised copy."""
    if not isinstance(tiers, dict):
        raise TierValidationError("tiers must be an object")

    missing = [t for t in VALID_TIERS if t not in tiers]
    if missing:
        raise TierValidationError(f"missing tier(s): {', '.join(missing)}")

    normalised: dict[str, Any] = {}
    for tier in VALID_TIERS:
        entry = tiers[tier]
        if not isinstance(entry, dict):
            raise TierValidationError(f"tier '{tier}' must be an object")
        model = entry.get("model")
        if not isinstance(model, str) or not model.strip():
            raise TierValidationError(f"tier '{tier}' needs a non-empty 'model'")
        ctx = entry.get("context_length")
        if not isinstance(ctx, int) or isinstance(ctx, bool) or ctx <= 0:
            raise TierValidationError(
                f"tier '{tier}' needs a positive integer 'context_length'"
            )
        normalised[tier] = {"model": model.strip(), "context_length": ctx}
    return normalised


def save_tiers(tiers: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Validate then atomically write tiers.json.

    Atomic write (tmp file + os.replace) so a concurrent read in the routing
    path never sees a half-written file. load_tiers() reads from disk on every
    call, so a saved mapping takes effect immediately with no redeploy.
    """
    path = path or TIERS_PATH
    normalised = validate_tiers(tiers)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(normalised, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return normalised


def update_tier(
    tier: str,
    model: str,
    context_length: int | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Update a single tier, keeping the others, and persist."""
    if tier not in VALID_TIERS:
        raise TierValidationError(f"unknown tier '{tier}'")
    tiers = load_tiers(path)
    entry = tiers.get(tier, {})
    tiers[tier] = {
        "model": model,
        "context_length": context_length
        if context_length is not None
        else entry.get("context_length"),
    }
    return save_tiers(tiers, path)
