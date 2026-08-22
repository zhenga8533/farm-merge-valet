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

    # Where template images used for vision matching live.
    templates_dir: Path = PROJECT_ROOT / "assets" / "templates"

    # Where downloaded game atlas PNGs/manifests are cached between
    # `extract-templates` runs, so re-running without --force doesn't
    # re-fetch everything.
    atlas_cache_dir: Path = PROJECT_ROOT / ".atlas_cache"

    # Minimum confidence (0-1) for template matches to be accepted.
    match_confidence: float = 0.85

    # Board scanning is two-pass (see core/board_scan.py): a cheap
    # downscaled sweep (`board_scan_scale`, matched against
    # `board_scan_coarse_confidence`) locates candidate cells, then each
    # candidate is classified for real against `match_confidence` using a
    # full-resolution crop sized from its own apparent size -- so
    # downscaling for speed no longer risks misclassifying visually-similar
    # items the way a single downscaled pass did (confirmed live: a
    # never-unlocked item was "detected" this way). Parallelized across
    # `board_scan_workers` threads (cv2.matchTemplate releases the GIL).
    board_scan_scale: float = 0.35
    board_scan_coarse_confidence: float = 0.85
    board_scan_workers: int = 8

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

    # Optional Discord webhook URL for posting run statistics.
    discord_webhook_url: str | None = None

    # Logging verbosity: DEBUG, INFO, WARNING, ERROR.
    log_level: str = "INFO"


settings = Settings()
