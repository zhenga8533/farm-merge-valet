"""Central ownership and reset behavior for GUI configuration sections."""

from __future__ import annotations

from enum import StrEnum

from farm_merge_valet.config import AppConfig


class ConfigSection(StrEnum):
    ITEMS = "items"
    SHOPS = "shops"
    MARKETPLACE = "marketplace"
    BROWSER = "browser"
    SETTINGS = "settings"
    VIEW = "view"


SECTION_FIELDS: dict[ConfigSection, tuple[str, ...]] = {
    ConfigSection.ITEMS: (
        "prefer_merge_five",
        "item_automation_enabled",
        "item_policy_defaults",
        "item_category_defaults",
        "item_default_overrides",
        "item_policy_overrides",
    ),
    ConfigSection.SHOPS: (
        "shop_automation_enabled",
        "shop_default_enabled",
        "recipe_default_enabled",
        "shop_overrides",
        "recipe_overrides",
    ),
    ConfigSection.MARKETPLACE: (
        "marketplace_automation_enabled",
        "marketplace_policy_overrides",
    ),
    ConfigSection.BROWSER: (
        "window_title",
        "browser",
        "browser_executable",
        "browser_profile_dir",
        "browser_auto_launch",
        "game_url",
        "cdp_port",
        "catalog_dir",
        "atlas_cache_dir",
    ),
    ConfigSection.SETTINGS: (
        "merge_empty_cell_reserve",
        "producer_interact_min_empty_cells",
        "auto_dismiss_overlays",
        "auto_pop_storage_bubbles",
        "auto_claim_supply_crates",
        "allow_obstacle_stage_starts",
        "loop_interval",
        "idle_wait_seconds",
        "item_action_delay_min",
        "item_action_delay_max",
        "crate_delay_min",
        "crate_delay_max",
        "start_stop_hotkey",
        "pause_hotkey",
        "quit_hotkey",
        "start_paused",
        "log_level",
        "discord_webhook_url",
        "webhook_summary_interval",
        "webhook_status_interval",
        "theme",
        "start_minimized",
        "bot_autostart",
        "close_to_tray",
        "close_managed_browser_on_exit",
        "main_always_on_top",
        "main_focused_opacity",
        "main_unfocused_opacity",
        "overlay_visible",
        "overlay_always_on_top",
        "overlay_click_through",
        "overlay_focused_opacity",
        "overlay_unfocused_opacity",
    ),
    ConfigSection.VIEW: (
        "items_sort_column",
        "items_sort_descending",
        "shops_sort_column",
        "shops_sort_descending",
        "marketplace_sort_column",
        "marketplace_sort_descending",
    ),
}


def reset_config_section(config: AppConfig, section: ConfigSection) -> AppConfig:
    defaults = AppConfig()
    changes = {field: getattr(defaults, field) for field in SECTION_FIELDS[section]}
    candidate = config.model_copy(update=changes)
    return AppConfig.model_validate(candidate.model_dump())


def _validate_section_ownership() -> None:
    configured_fields = [field for fields in SECTION_FIELDS.values() for field in fields]
    duplicates = {field for field in configured_fields if configured_fields.count(field) > 1}
    expected = set(AppConfig.model_fields) - {"schema_version"}
    actual = set(configured_fields)
    if duplicates or actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise RuntimeError(
            "GUI configuration ownership is invalid: "
            f"duplicates={sorted(duplicates)}, missing={missing}, unknown={unknown}"
        )


_validate_section_ownership()
