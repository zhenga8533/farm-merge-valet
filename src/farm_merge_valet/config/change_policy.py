"""Configuration fields that require process components to be rebuilt."""

from __future__ import annotations

from farm_merge_valet.config.models import AppConfig

BROWSER_RESTART_FIELDS = frozenset(
    {
        "browser",
        "browser_executable",
        "browser_profile_dir",
        "cdp_port",
        "game_portal",
        "game_url",
        "window_title",
    }
)

BOT_RESTART_FIELDS = frozenset(
    {
        "discord_webhook_url",
        "webhook_summary_interval",
        "webhook_status_interval",
        "webhook_notification_profile",
        "webhook_include_charts",
        "catalog_dir",
        "atlas_cache_dir",
        "start_paused",
    }
)

RESTART_REQUIRED_FIELDS = BROWSER_RESTART_FIELDS | BOT_RESTART_FIELDS


def changed_fields(previous: AppConfig, current: AppConfig) -> tuple[str, ...]:
    return tuple(
        name for name in AppConfig.model_fields if getattr(previous, name) != getattr(current, name)
    )


def live_config(previous: AppConfig, current: AppConfig) -> AppConfig:
    preserved = {name: getattr(previous, name) for name in RESTART_REQUIRED_FIELDS}
    return current.model_copy(update=preserved, deep=True)
