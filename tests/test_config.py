from __future__ import annotations

import pytest
from pydantic import ValidationError

from farm_merge_valet.config import Settings


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cdp_port", 0),
        ("merge_empty_cell_reserve", -1),
        ("loop_interval", 0),
        ("crate_delay_max", 5.1),
        ("gui_opacity", 1.1),
        ("log_level", "VERBOSE"),
        ("browser", "firefox"),
    ],
)
def test_settings_reject_invalid_runtime_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "need_space_confidence",
        "crate_click_batch_size",
        "match_confidence",
        "board_dead_left_ratio",
        "pan_step_ratio",
        "merge_drag_duration",
        "drag_duration_min",
        "drag_duration_max",
        "crate_click_settle",
    ],
)
def test_settings_reject_removed_legacy_keys(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: 1})


def test_merge_space_reserve_can_be_disabled() -> None:
    assert Settings(_env_file=None, merge_empty_cell_reserve=0).merge_empty_cell_reserve == 0


def test_merge_five_is_enabled_by_default() -> None:
    assert Settings(_env_file=None).prefer_merge_five


def test_managed_browser_defaults_to_auto_launch() -> None:
    settings = Settings(_env_file=None)

    assert settings.browser == "auto"
    assert settings.browser_auto_launch
    assert settings.browser_profile_dir is None


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [
        ("item_action_delay_min", "item_action_delay_max"),
        ("crate_delay_min", "crate_delay_max"),
    ],
)
def test_timing_minimum_cannot_exceed_maximum(minimum: str, maximum: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{minimum: 2.0, maximum: 1.0})


def test_action_cadence_defaults_keep_crates_fast() -> None:
    settings = Settings(_env_file=None)

    assert (settings.item_action_delay_min, settings.item_action_delay_max) == (1.5, 3.5)
    assert (settings.crate_delay_min, settings.crate_delay_max) == (0.05, 0.2)
