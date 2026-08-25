"""Application configuration.

Settings are loaded from environment variables / a `.env` file, with sane
defaults for local development. See `.env.example` for available options.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FMV_",
        extra="forbid",
    )

    # CDP page targeting: substring match against Reddit page titles or URLs.
    window_title: str = "r/FarmMergeValley"

    # Dedicated Chromium-family browser managed by the CLI. Chrome and Edge
    # are supported; Brave and Chromium can be selected for experimental use.
    browser: Literal["auto", "chrome", "edge", "brave", "chromium"] = "auto"
    browser_executable: Path | None = None
    browser_profile_dir: Path | None = None
    browser_auto_launch: bool = True
    game_url: str = "https://www.reddit.com/r/FarmMergeValley/"

    # Port the managed browser's remote debugging endpoint is expected on -- see
    # cdp/client.py for why the bot needs this rather than just working
    # against a normal, already-open browser window.
    cdp_port: int = Field(default=9222, ge=1, le=65535)

    # Where blueprint reference assets used to identify game items live.
    templates_dir: Path = PROJECT_ROOT / "assets" / "templates"

    # Where downloaded game atlas PNGs/manifests are cached between
    # `extract-templates` runs, so re-running without --force doesn't
    # re-fetch everything.
    atlas_cache_dir: Path = PROJECT_ROOT / ".atlas_cache"

    # Stop claiming before the board is completely full when productive merge
    # work exists. Swaps can recover a full board, while one reserved empty cell
    # also permits ordinary gather/degroup moves.
    merge_empty_cell_reserve: int = Field(default=1, ge=0)

    # Ready tier-4 crops and animals can place several ingredients at once.
    # Defer their harvest until this many cells are open.
    producer_claim_min_empty_cells: int = Field(default=4, ge=1)

    # Merging exactly 5 identical items yields 2 of the next tier instead of
    # 1 from a merge-3 (see docs/game-mechanics.md).
    prefer_merge_five: bool = True

    # Seconds between bot loop iterations.
    loop_interval: float = Field(default=1.0, gt=0.0)

    # Back off when the planner can find no action at all. Set to 0 to keep
    # checking at the normal loop interval.
    idle_wait_seconds: float = Field(default=30.0, ge=0.0, le=3600.0)

    # Delay after a resolved item action and between accepted crate claims.
    item_action_delay_min: float = Field(default=1.5, ge=0.0, le=60.0)
    item_action_delay_max: float = Field(default=3.5, ge=0.0, le=60.0)
    crate_delay_min: float = Field(default=0.05, ge=0.0, le=5.0)
    crate_delay_max: float = Field(default=0.2, ge=0.0, le=5.0)

    # Global hotkeys work even when the terminal or managed browser is not
    # focused. See `keyboard` package syntax for valid values, e.g.
    # "ctrl+alt+p".
    pause_hotkey: str = "f9"
    quit_hotkey: str = "f10"

    # Defer runtime discovery and actions until the first resume.
    start_paused: bool = True

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Optional Discord notifications. The URL is secret so settings dumps and
    # validation errors do not expose the webhook token.
    discord_webhook_url: SecretStr | None = None
    webhook_summary_interval: float = Field(default=3600.0, ge=60.0, le=86400.0)
    webhook_status_interval: float = Field(default=60.0, ge=0.0, le=3600.0)

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

    @field_validator("discord_webhook_url", mode="before")
    @classmethod
    def empty_webhook_url_is_disabled(cls, value: object) -> object:
        if isinstance(value, str):
            if not value.strip():
                return None
            return SecretStr(value)
        return value

    @model_validator(mode="after")
    def validate_timing_ranges(self) -> Self:
        for name in ("item_action_delay", "crate_delay"):
            minimum = getattr(self, f"{name}_min")
            maximum = getattr(self, f"{name}_max")
            if minimum > maximum:
                raise ValueError(f"{name}_min must be less than or equal to {name}_max")
        return self


settings = Settings()
