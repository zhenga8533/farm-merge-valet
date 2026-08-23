"""Application configuration.

Settings are loaded from environment variables / a `.env` file, with sane
defaults for local development. See `.env.example` for available options.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FMV_",
        extra="ignore",
    )

    # Window targeting: substring match against open window titles.
    window_title: str = "Discord"

    # Port Chrome's remote debugging endpoint is expected on -- see
    # cdp/client.py for why the bot needs this rather than just working
    # against a normal, already-open browser window.
    cdp_port: int = 9222

    # Where template images used for vision matching live.
    templates_dir: Path = PROJECT_ROOT / "assets" / "templates"

    # Where downloaded game atlas PNGs/manifests are cached between
    # `extract-templates` runs, so re-running without --force doesn't
    # re-fetch everything.
    atlas_cache_dir: Path = PROJECT_ROOT / ".atlas_cache"

    # Minimum confidence (0-1) for template matches to be accepted.
    match_confidence: float = 0.85

    # Confidence for the "Need more empty space!" fallback. Live board
    # state is preferred because the banner is not shown consistently.
    need_space_confidence: float = 0.8

    # Clicks between live board-state refreshes while claiming crates.
    crate_click_batch_size: int = 10

    # Delay between crate clicks so the game can register each action.
    crate_click_settle: float = 0.1

    # Clickable board bounds in captured-frame pixels. Merge actions outside
    # this area are scrolled into view instead of landing on browser/game UI.
    board_min_pixel_x: float = 200.0
    board_max_pixel_x: float = 1700.0
    board_min_pixel_y: float = 170.0

    # Default merge-5 preference (merging exactly 5 identical items yields
    # 2 of the next tier instead of 1 from a merge-3 -- see
    # docs/game-mechanics.md). Per-item/tier overrides aren't env-var
    # configurable yet; pass `merge_five_overrides` to `Bot` directly.
    prefer_merge_five: bool = False

    # Seconds between bot loop iterations.
    loop_interval: float = 1.0

    # Global hotkeys (work even when the terminal isn't focused, since the
    # bot is busy driving mouse input elsewhere) to pause/resume and quit
    # `run_forever`. See `keyboard` package syntax for valid values, e.g.
    # "ctrl+alt+p".
    pause_hotkey: str = "f9"
    quit_hotkey: str = "f10"

    # Start paused rather than acting immediately -- lets you get the
    # target window in position (and check the overlay GUI/logs, if
    # enabled) before anything drives mouse input. `initialize()` (the
    # zoom-out/scroll-to-bottom environment setup) is deferred until the
    # first resume too, same as pausing mid-run already does, so nothing
    # touches the game at all until you press `pause_hotkey`.
    start_paused: bool = True

    # Logging verbosity: DEBUG, INFO, WARNING, ERROR.
    log_level: str = "INFO"

    # Overlay GUI (see gui/overlay.py) -- off by default so `run` keeps its
    # existing terminal-only behavior unless explicitly opted into.
    gui_enabled: bool = False
    # 0 (fully transparent) - 1 (fully opaque).
    gui_opacity: float = 0.85
    # Always-on-top, and click-through (clicks land on whatever's behind
    # it) *while it isn't the active window* -- alt-tab or click its
    # taskbar entry to interact with it normally (drag, resize, scroll
    # the log) until it loses focus again. Disable for a plain window
    # instead -- see gui/overlay.py.
    gui_overlay_mode: bool = True


settings = Settings()
