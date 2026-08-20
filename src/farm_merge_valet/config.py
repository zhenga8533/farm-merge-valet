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

    # Minimum confidence (0-1) for template matches to be accepted.
    match_confidence: float = 0.85

    # Seconds between bot loop iterations.
    loop_interval: float = 1.0

    # Optional Discord webhook URL for posting run statistics.
    discord_webhook_url: str | None = None

    # Logging verbosity: DEBUG, INFO, WARNING, ERROR.
    log_level: str = "INFO"


settings = Settings()
