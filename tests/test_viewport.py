from __future__ import annotations

from farm_merge_valet.core.viewport import viewport_layout


def test_fullscreen_layout_uses_proportional_dead_zones() -> None:
    layout = viewport_layout(1920, 1080)

    assert (layout.board.left, layout.board.top) == (192, 162)
    assert (layout.board.right, layout.board.bottom) == (1728, 918)
    assert (layout.crate.left, layout.crate.top) == (864, 853)
    assert (layout.crate.right, layout.crate.bottom) == (1056, 1080)
    assert layout.pan_anchor == (1776, 540)


def test_actionable_area_excludes_outer_slices_and_crate_outlier() -> None:
    layout = viewport_layout(1920, 1080)

    assert layout.is_actionable(500, 500)
    assert not layout.is_actionable(100, 500)
    assert not layout.is_actionable(1800, 500)
    assert not layout.is_actionable(500, 100)
    assert not layout.is_actionable(500, 1000)
    assert not layout.is_actionable(960, 860)
