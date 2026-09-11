"""Central ownership and reset behavior for GUI configuration sections."""

from __future__ import annotations

from enum import StrEnum

from farm_merge_valet.config import AppConfig


class ConfigSection(StrEnum):
    ITEMS = "items"
    SHOPS = "shops"
    BUILDINGS = "buildings"
    MARKETPLACE = "marketplace"
    BROWSER = "browser"
    SETTINGS = "settings"
    LOGS = "logs"
    VIEW = "view"


SECTION_FIELDS: dict[ConfigSection, tuple[str, ...]] = {
    ConfigSection.ITEMS: (
        "item_policy_defaults",
        "item_category_defaults",
        "item_default_overrides",
        "item_policy_overrides",
    ),
    ConfigSection.SHOPS: (
        "shop_default_enabled",
        "recipe_default_enabled",
        "shop_overrides",
        "recipe_overrides",
        "shop_ingredient_reserve_default",
        "shop_ingredient_reserves",
    ),
    ConfigSection.BUILDINGS: (
        "building_repair_default_enabled",
        "building_repair_overrides",
    ),
    ConfigSection.MARKETPLACE: ("marketplace_policy_overrides",),
    ConfigSection.BROWSER: (
        "game_portal",
        "window_title",
        "browser",
        "browser_executable",
        "browser_profile_dir",
        "browser_auto_launch",
        "auto_recover_game",
        "game_url",
        "cdp_port",
        "catalog_dir",
        "atlas_cache_dir",
        "close_managed_browser_on_exit",
    ),
    ConfigSection.SETTINGS: (
        "event_automation_enabled",
        "event_default_enabled",
        "event_automation_overrides",
        "event_visit_energy_threshold",
        "merge_empty_cell_reserve",
        "producer_interact_min_empty_cells",
        "auto_dismiss_overlays",
        "auto_pop_storage_bubbles",
        "auto_claim_supply_crates",
        "allow_obstacle_stage_starts",
        "preserve_building_repair_resources",
        "prioritize_repair_shops",
        "prioritize_cheaper_repairs",
        "prioritize_obstacle_repair_resources",
        "minimum_energy_reserve",
        "minimum_ticket_reserve",
        "minimum_coin_reserve",
        "minimum_gem_reserve",
        "item_automation_enabled",
        "shop_automation_enabled",
        "marketplace_automation_enabled",
        "farm_visit_automation_enabled",
        "land_expansion_automation_enabled",
        "land_expansion_max_coin_cost",
        "land_expansion_max_gem_cost",
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
        "discord_webhook_url",
        "webhook_summary_interval",
        "webhook_status_interval",
        "webhook_notification_profile",
        "webhook_include_charts",
        "webhook_include_screenshots",
        "theme",
        "start_minimized",
        "bot_autostart",
        "close_to_tray",
        "main_always_on_top",
        "main_focused_opacity",
        "main_unfocused_opacity",
        "overlay_always_on_top",
        "overlay_click_through",
        "overlay_focused_opacity",
        "overlay_unfocused_opacity",
    ),
    ConfigSection.LOGS: ("log_level",),
    ConfigSection.VIEW: (
        "overlay_visible",
        "items_sort_column",
        "items_sort_descending",
        "shops_sort_column",
        "shops_sort_descending",
        "buildings_sort_column",
        "buildings_sort_descending",
        "marketplace_sort_column",
        "marketplace_sort_descending",
    ),
}


def reset_config_section(config: AppConfig, section: ConfigSection) -> AppConfig:
    defaults = AppConfig()
    fields = SECTION_FIELDS[section]
    changes = {field: getattr(defaults, field) for field in fields}
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
