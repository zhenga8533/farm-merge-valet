"""Resolution-independent viewport regions for safe board interaction."""

from __future__ import annotations

from dataclasses import dataclass

from farm_merge_valet.config import settings


@dataclass(frozen=True)
class PixelRect:
    left: int
    top: int
    right: int
    bottom: int

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass(frozen=True)
class ViewportLayout:
    width: int
    height: int
    board: PixelRect
    crate: PixelRect
    pan_anchor: tuple[int, int]

    def is_actionable(self, x: int, y: int) -> bool:
        return self.board.contains(x, y) and not self.crate.contains(x, y)

    @property
    def dead_zones(self) -> tuple[PixelRect, ...]:
        return (
            PixelRect(0, 0, self.width, self.board.top),
            PixelRect(0, self.board.bottom, self.width, self.height),
            PixelRect(0, self.board.top, self.board.left, self.board.bottom),
            PixelRect(self.board.right, self.board.top, self.width, self.board.bottom),
            self.crate,
        )


def _scaled(value: float, size: int) -> int:
    return round(value * size)


def viewport_layout(width: int, height: int) -> ViewportLayout:
    return ViewportLayout(
        width=width,
        height=height,
        board=PixelRect(
            left=_scaled(settings.board_dead_left_ratio, width),
            top=_scaled(settings.board_dead_top_ratio, height),
            right=_scaled(1.0 - settings.board_dead_right_ratio, width),
            bottom=_scaled(1.0 - settings.board_dead_bottom_ratio, height),
        ),
        crate=PixelRect(
            left=_scaled(settings.crate_dead_left_ratio, width),
            top=_scaled(settings.crate_dead_top_ratio, height),
            right=_scaled(settings.crate_dead_right_ratio, width),
            bottom=height,
        ),
        pan_anchor=(
            _scaled(settings.pan_anchor_x_ratio, width),
            _scaled(settings.pan_anchor_y_ratio, height),
        ),
    )
