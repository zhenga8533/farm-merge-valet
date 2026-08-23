from __future__ import annotations

import pytest
from pydantic import ValidationError

from farm_merge_valet.config import Settings


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cdp_port", 0),
        ("match_confidence", 1.1),
        ("crate_click_batch_size", 0),
        ("merge_empty_cell_reserve", -1),
        ("loop_interval", 0),
        ("gui_opacity", 1.1),
        ("log_level", "VERBOSE"),
        ("board_dead_left_ratio", 0.6),
        ("pan_anchor_x_ratio", 0.5),
    ],
)
def test_settings_reject_invalid_runtime_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_settings_reject_removed_legacy_keys() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, need_space_confidence=0.8)


def test_merge_space_reserve_can_be_disabled() -> None:
    assert Settings(_env_file=None, merge_empty_cell_reserve=0).merge_empty_cell_reserve == 0


def test_merge_five_is_enabled_by_default() -> None:
    assert Settings(_env_file=None).prefer_merge_five
