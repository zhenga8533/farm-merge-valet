"""Application configuration.

Settings are loaded from environment variables / a `.env` file, with sane
defaults for local development. See `.env.example` for available options.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FMV_",
        extra="forbid",
    )

    # Window targeting: substring match against open window titles.
    window_title: str = "r/FarmMergeValley"

    # Port Chrome's remote debugging endpoint is expected on -- see
    # cdp/client.py for why the bot needs this rather than just working
    # against a normal, already-open browser window.
    cdp_port: int = Field(default=9222, ge=1, le=65535)

    # Where template images used for vision matching live.
    templates_dir: Path = PROJECT_ROOT / "assets" / "templates"

    # Where downloaded game atlas PNGs/manifests are cached between
    # `extract-templates` runs, so re-running without --force doesn't
    # re-fetch everything.
    atlas_cache_dir: Path = PROJECT_ROOT / ".atlas_cache"

    # Minimum confidence (0-1) for template matches to be accepted.
    match_confidence: float = Field(default=0.85, ge=0.0, le=1.0)

    # Clicks between live board-state refreshes while claiming crates.
    crate_click_batch_size: int = Field(default=10, ge=1)

    # Delay between crate clicks so the game can register each action.
    crate_click_settle: float = Field(default=0.1, ge=0.0)

    # Stop claiming before the board is completely full so gather/degroup
    # moves always have a destination. Relocating an item vacates its source,
    # so one reserved cell is enough for a multi-step rearrangement.
    merge_empty_cell_reserve: int = Field(default=1, ge=0)

    # Resolution-independent dead-zone slices around the viewport. The
    # bottom-center crate rectangle is an additional outlier inside the board.
    board_dead_left_ratio: float = Field(default=0.10, ge=0.0, lt=0.5)
    board_dead_right_ratio: float = Field(default=0.10, ge=0.0, lt=0.5)
    board_dead_top_ratio: float = Field(default=0.15, ge=0.0, lt=0.5)
    board_dead_bottom_ratio: float = Field(default=0.15, ge=0.0, lt=0.5)
    crate_dead_left_ratio: float = Field(default=0.45, ge=0.0, le=1.0)
    crate_dead_right_ratio: float = Field(default=0.55, ge=0.0, le=1.0)
    crate_dead_top_ratio: float = Field(default=0.79, ge=0.0, le=1.0)

    # Safe background point where drag gestures move the board camera. It is
    # inside the right dead zone, away from tiles and the fixed toolbar.
    pan_anchor_x_ratio: float = Field(default=0.925, ge=0.0, le=1.0)
    pan_anchor_y_ratio: float = Field(default=0.50, ge=0.0, le=1.0)

    # Merging exactly 5 identical items yields 2 of the next tier instead of
    # 1 from a merge-3 (see docs/game-mechanics.md).
    prefer_merge_five: bool = True

    # Seconds between bot loop iterations.
    loop_interval: float = Field(default=1.0, gt=0.0)

    # Global hotkeys (work even when the terminal isn't focused, since the
    # bot is busy driving mouse input elsewhere) to pause/resume and quit
    # `run_forever`. See `keyboard` package syntax for valid values, e.g.
    # "ctrl+alt+p".
    pause_hotkey: str = "f9"
    quit_hotkey: str = "f10"

    # Defer environment setup and all input until the first resume.
    start_paused: bool = True

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Overlay GUI (see gui/overlay.py) -- off by default so `run` keeps its
    # existing terminal-only behavior unless explicitly opted into.
    gui_enabled: bool = False
    # 0 (fully transparent) - 1 (fully opaque).
    gui_opacity: float = Field(default=0.85, ge=0.0, le=1.0)
    # Always-on-top, and click-through (clicks land on whatever's behind
    # it) *while it isn't the active window* -- alt-tab or click its
    # taskbar entry to interact with it normally (drag, resize, scroll
    # the log) until it loses focus again. Disable for a plain window
    # instead -- see gui/overlay.py.
    gui_overlay_mode: bool = True

    @model_validator(mode="after")
    def validate_viewport_layout(self) -> Self:
        if self.board_dead_left_ratio + self.board_dead_right_ratio >= 1.0:
            raise ValueError("horizontal board dead zones must leave clickable space")
        if self.board_dead_top_ratio + self.board_dead_bottom_ratio >= 1.0:
            raise ValueError("vertical board dead zones must leave clickable space")
        if self.crate_dead_left_ratio >= self.crate_dead_right_ratio:
            raise ValueError("crate dead-zone left edge must be before its right edge")
        board_left = self.board_dead_left_ratio
        board_right = 1.0 - self.board_dead_right_ratio
        board_bottom = 1.0 - self.board_dead_bottom_ratio
        if not (
            board_left <= self.crate_dead_left_ratio < self.crate_dead_right_ratio <= board_right
            and self.board_dead_top_ratio <= self.crate_dead_top_ratio < board_bottom
        ):
            raise ValueError("crate dead zone must overlap the usable board area")
        if not board_right <= self.pan_anchor_x_ratio < 1.0:
            raise ValueError("pan anchor must be inside the right dead zone")
        if (
            not self.board_dead_top_ratio
            <= self.pan_anchor_y_ratio
            <= (1.0 - self.board_dead_bottom_ratio)
        ):
            raise ValueError("pan anchor must be vertically aligned with the board")
        return self


settings = Settings()
