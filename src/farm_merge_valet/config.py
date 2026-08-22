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

    # Default merge-5 preference (merging exactly 5 identical items yields
    # 2 of the next tier instead of 1 from a merge-3 -- see
    # docs/game-mechanics.md). Per-item/tier overrides aren't env-var
    # configurable yet; pass `merge_five_overrides` to `Bot` directly.
    prefer_merge_five: bool = False

    # Seconds between bot loop iterations.
    loop_interval: float = 1.0

    # Optional Discord webhook URL for posting run statistics.
    discord_webhook_url: str | None = None

    # Logging verbosity: DEBUG, INFO, WARNING, ERROR.
    log_level: str = "INFO"


settings = Settings()
