from __future__ import annotations

from typing import Any

# Default values applied when a setting has no row in the `settings` table.
DEFAULT_SETTINGS: dict[str, Any] = {
    "confidence_gap_threshold": 0.15,
    "prompt_preview_default": False,
    "routing_conservatism": "balanced",
    "prompt_cleaning_enabled": True,
    "prompt_compaction_enabled": False,
    "prompt_compaction_model": "openai/gpt-4o-mini",
    "prompt_compaction_min_chars": 500,
}

# Named routing-conservatism presets -> confidence_gap_threshold value.
# "aggressive"  = cheap: escalate to a pricier tier only on a very close tie.
# "balanced"    = the Phase 1 default of 0.15.
# "conservative" = quality: escalate on a wider gap, so pricier tiers win more.
CONSERVATISM_PRESETS: dict[str, float] = {
    "aggressive": 0.05,
    "balanced": 0.15,
    "conservative": 0.30,
}

# Setting key -> Python type used to decode the JSON-stored value.
_SETTING_TYPES: dict[str, type] = {
    "confidence_gap_threshold": float,
    "prompt_preview_default": bool,
    "routing_conservatism": str,
    "prompt_cleaning_enabled": bool,
    "prompt_compaction_enabled": bool,
    "prompt_compaction_model": str,
    "prompt_compaction_min_chars": int,
}


class SettingsValidationError(ValueError):
    """Raised when a proposed settings update is malformed."""


def validate_confidence_gap(value: Any) -> float:
    try:
        gap = float(value)
    except (TypeError, ValueError):
        raise SettingsValidationError("confidence_gap_threshold must be a number")
    if not (0.0 < gap <= 1.0):
        raise SettingsValidationError(
            "confidence_gap_threshold must be greater than 0 and at most 1"
        )
    return gap


def decode_setting(key: str, raw: Any) -> Any:
    """Decode a JSON-stored setting value back to its native type."""
    target = _SETTING_TYPES.get(key)
    if target is float:
        return float(raw)
    if target is int:
        return int(raw)
    if target is bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in {"true", "1", "yes", "on"}
        return bool(raw)
    return str(raw)


def resolve_settings_update(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    """Turn a settings PUT body into ordered (key, value) pairs to persist.

    - A named preset sets the threshold to that preset's value and records the
      preset name.
    - A manually-edited threshold (with no preset) flips `routing_conservatism`
      to ``custom``.
    - ``prompt_preview_default`` is a plain boolean.
    """
    updates: dict[str, Any] = {}

    preset = payload.get("routing_conservatism")
    if preset is not None:
        if preset not in CONSERVATISM_PRESETS:
            raise SettingsValidationError(
                f"unknown routing_conservatism preset {preset!r}; "
                f"expected one of {', '.join(CONSERVATISM_PRESETS)}"
            )
        updates["routing_conservatism"] = preset
        updates["confidence_gap_threshold"] = CONSERVATISM_PRESETS[preset]

    if "confidence_gap_threshold" in payload:
        gap = validate_confidence_gap(payload["confidence_gap_threshold"])
        updates["confidence_gap_threshold"] = gap
        if preset is None:
            updates["routing_conservatism"] = "custom"

    if "prompt_preview_default" in payload:
        v = payload["prompt_preview_default"]
        if not isinstance(v, bool):
            raise SettingsValidationError("prompt_preview_default must be a boolean")
        updates["prompt_preview_default"] = v

    for bool_key in ("prompt_cleaning_enabled", "prompt_compaction_enabled"):
        if bool_key in payload:
            v = payload[bool_key]
            if not isinstance(v, bool):
                raise SettingsValidationError(f"{bool_key} must be a boolean")
            updates[bool_key] = v

    if "prompt_compaction_model" in payload:
        v = payload["prompt_compaction_model"]
        if not isinstance(v, str) or not v.strip():
            raise SettingsValidationError("prompt_compaction_model must be a non-empty string")
        updates["prompt_compaction_model"] = v.strip()

    if "prompt_compaction_min_chars" in payload:
        v = payload["prompt_compaction_min_chars"]
        if not isinstance(v, int) or v < 100:
            raise SettingsValidationError("prompt_compaction_min_chars must be an integer >= 100")
        updates["prompt_compaction_min_chars"] = v

    return list(updates.items())
