from __future__ import annotations

import pytest

from app.settings_store import (
    CONSERVATISM_PRESETS,
    DEFAULT_SETTINGS,
    SettingsValidationError,
    decode_setting,
    resolve_settings_update,
    validate_confidence_gap,
)


def test_defaults_shape():
    assert DEFAULT_SETTINGS["confidence_gap_threshold"] == 0.15
    assert DEFAULT_SETTINGS["prompt_preview_default"] is False
    assert DEFAULT_SETTINGS["routing_conservatism"] == "balanced"


def test_presets_are_ordered():
    # aggressive < balanced < conservative threshold (cheap -> quality).
    assert CONSERVATISM_PRESETS["aggressive"] < CONSERVATISM_PRESETS["balanced"]
    assert CONSERVATISM_PRESETS["balanced"] < CONSERVATISM_PRESETS["conservative"]


def test_validate_confidence_gap_accepts_valid():
    assert validate_confidence_gap(0.15) == 0.15
    assert validate_confidence_gap("0.3") == 0.3
    assert validate_confidence_gap(1.0) == 1.0


@pytest.mark.parametrize("bad", [0, -0.1, 1.5, "not-a-number", None])
def test_validate_confidence_gap_rejects(bad):
    with pytest.raises(SettingsValidationError):
        validate_confidence_gap(bad)


def test_resolve_preset_sets_threshold_and_name():
    updates = resolve_settings_update({"routing_conservatism": "conservative"})
    pairs = dict(updates)
    assert pairs["routing_conservatism"] == "conservative"
    assert pairs["confidence_gap_threshold"] == CONSERVATISM_PRESETS["conservative"]


def test_resolve_manual_threshold_flips_to_custom():
    updates = resolve_settings_update({"confidence_gap_threshold": 0.22})
    pairs = dict(updates)
    assert pairs["confidence_gap_threshold"] == 0.22
    assert pairs["routing_conservatism"] == "custom"


def test_resolve_unknown_preset_rejected():
    with pytest.raises(SettingsValidationError):
        resolve_settings_update({"routing_conservatism": "yolo"})


def test_resolve_prompt_preview_default():
    updates = resolve_settings_update({"prompt_preview_default": True})
    assert dict(updates) == {"prompt_preview_default": True}


def test_resolve_rejects_non_bool_preview():
    with pytest.raises(SettingsValidationError):
        resolve_settings_update({"prompt_preview_default": "yes"})


def test_decode_setting_types():
    assert decode_setting("confidence_gap_threshold", 0.15) == 0.15
    assert decode_setting("confidence_gap_threshold", "0.15") == 0.15
    assert decode_setting("prompt_preview_default", True) is True
    assert decode_setting("prompt_preview_default", "false") is False
    assert decode_setting("routing_conservatism", "balanced") == "balanced"
