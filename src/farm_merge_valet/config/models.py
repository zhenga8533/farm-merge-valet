"""Validated application configuration models."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from farm_merge_valet.config.hotkeys import normalize_hotkey
from farm_merge_valet.config.paths import user_cache_root
from farm_merge_valet.core.items import item_base_policy_key, item_family_policy_key
from farm_merge_valet.core.obstacles import ObstaclePriorityFocus
from farm_merge_valet.integrations import GamePortal, portal_definition, portal_for_page_url

CONFIG_SCHEMA_VERSION: Literal[2] = 2


class ItemPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    merge: bool = True
    prefer_merge_five: bool = True
    force_lucky_merge: bool = False
    interact: bool = False
    always_remove: bool = False
    keep_minimum: int = Field(default=0, ge=0, le=1_000_000_000, strict=True)


class ItemPolicyOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool | None = None
    merge: bool | None = None
    prefer_merge_five: bool | None = None
    force_lucky_merge: bool | None = None
    interact: bool | None = None
    always_remove: bool | None = None
    keep_minimum: int | None = Field(default=None, ge=0, le=1_000_000_000, strict=True)


_INTERACT_BY_DEFAULT = ItemPolicyOverride(interact=True)
_RECOMMENDED_ITEM_CATEGORY_DEFAULTS: Mapping[str, ItemPolicyOverride] = MappingProxyType(
    {
        "animals": _INTERACT_BY_DEFAULT,
        "crops": _INTERACT_BY_DEFAULT,
        "ingredients": _INTERACT_BY_DEFAULT,
        "obstacles": _INTERACT_BY_DEFAULT,
        "rewards": _INTERACT_BY_DEFAULT,
    }
)
_RECOMMENDED_ITEM_DEFAULTS: Mapping[str, ItemPolicyOverride] = MappingProxyType(
    {
        "currencies/ticket": _INTERACT_BY_DEFAULT,
        "resources/crate": _INTERACT_BY_DEFAULT,
        "upgrade_cards/upgrade_card": _INTERACT_BY_DEFAULT,
    }
)


def _item_policy_resolution_keys(policy_key: str) -> tuple[str, ...]:
    family_key = item_family_policy_key(policy_key)
    base_key = item_base_policy_key(family_key)
    keys = [base_key]
    if policy_key != family_key:
        keys.append(f"{base_key}{policy_key.removeprefix(family_key)}")
    if family_key != base_key:
        keys.append(family_key)
    if policy_key not in keys:
        keys.append(policy_key)
    return tuple(keys)


class AppConfig(BaseModel):
    """Complete validated configuration snapshot used by the GUI and runtime."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal[2] = CONFIG_SCHEMA_VERSION
    game_portal: GamePortal = GamePortal.REDDIT
    window_title: str = ""
    browser: Literal["auto", "chrome", "edge", "brave", "chromium"] = "auto"
    browser_executable: Path | None = None
    browser_profile_dir: Path | None = None
    browser_auto_launch: bool = True
    auto_recover_game: bool = True
    # 0 means keep retrying recovery indefinitely instead of giving up.
    max_game_recovery_attempts: int = Field(default=1, ge=0, le=100)
    game_url: str = "https://www.reddit.com/r/FarmMergeValley/"
    cdp_port: int = Field(default=9222, ge=1, le=65535)
    catalog_dir: Path = Field(default_factory=lambda: user_cache_root() / "catalog")
    atlas_cache_dir: Path = Field(default_factory=lambda: user_cache_root() / "atlases")

    merge_empty_cell_reserve: int = Field(default=1, ge=0, le=50)
    emergency_merge_three_max_tier: int = Field(default=3, ge=0, le=50)
    producer_interact_min_empty_cells: int = Field(default=4, ge=1, le=50)
    auto_dismiss_overlays: bool = True
    auto_pop_storage_bubbles: bool = True
    auto_claim_supply_crates: bool = True
    allow_obstacle_stage_starts: bool = True
    obstacle_priority_focus: ObstaclePriorityFocus = ObstaclePriorityFocus.STARTED
    preserve_building_repair_resources: bool = True
    building_repair_default_enabled: bool = True
    building_repair_overrides: dict[str, bool] = Field(default_factory=dict)
    prioritize_repair_shops: bool = True
    prioritize_cheaper_repairs: bool = True
    prioritize_obstacle_repair_resources: bool = True
    minimum_energy_reserve: int = Field(default=0, ge=0, le=1_000_000_000)
    minimum_ticket_reserve: int = Field(default=0, ge=0, le=1_000_000_000)
    minimum_coin_reserve: int = Field(default=0, ge=0, le=1_000_000_000)
    minimum_gem_reserve: int = Field(default=0, ge=0, le=1_000_000_000)
    shop_ingredient_reserve_default: int = Field(default=0, ge=0, le=1_000_000_000)
    shop_ingredient_reserves: dict[str, int] = Field(default_factory=dict)
    item_automation_enabled: bool = True
    item_policy_defaults: ItemPolicy = Field(default_factory=ItemPolicy)
    item_category_defaults: dict[str, ItemPolicyOverride] = Field(default_factory=dict)
    item_default_overrides: dict[str, ItemPolicyOverride] = Field(default_factory=dict)
    item_policy_overrides: dict[str, ItemPolicyOverride] = Field(default_factory=dict)
    shop_default_enabled: bool = True
    recipe_default_enabled: bool = True
    shop_automation_enabled: bool = True
    shop_overrides: dict[str, bool] = Field(default_factory=dict)
    recipe_overrides: dict[str, bool] = Field(default_factory=dict)
    marketplace_policy_overrides: dict[str, bool] = Field(default_factory=dict)
    marketplace_automation_enabled: bool = True
    farm_visit_automation_enabled: bool = False
    auto_claim_friend_rewards: bool = True
    event_automation_enabled: bool = False
    event_default_enabled: bool = True
    event_automation_overrides: dict[str, bool] = Field(default_factory=dict)
    auto_claim_event_rewards: bool = True
    event_visit_energy_threshold: int = Field(default=50, ge=0, le=1_000_000_000)
    land_expansion_automation_enabled: bool = False
    land_expansion_max_coin_cost: int = Field(default=0, ge=0, le=1_000_000_000)
    land_expansion_max_gem_cost: int = Field(default=0, ge=0, le=1_000_000_000)
    items_sort_column: Literal[
        "item", "category", "enabled", "merge", "merge_five", "lucky_merge", "interact", "remove"
    ] = "item"
    items_sort_descending: bool = False
    shops_sort_column: Literal["item", "type", "repair", "enabled"] = "item"
    shops_sort_descending: bool = False
    buildings_sort_column: Literal["building", "type", "availability", "repair", "enabled"] = (
        "building"
    )
    buildings_sort_descending: bool = False
    marketplace_sort_column: Literal["offer", "cost", "enabled"] = "offer"
    marketplace_sort_descending: bool = False

    loop_interval: float = Field(default=1.0, ge=0.25, le=60.0)
    idle_wait_seconds: float = Field(default=30.0, ge=0.0, le=3600.0)
    item_action_delay_min: float = Field(default=1.5, ge=0.0, le=60.0)
    item_action_delay_max: float = Field(default=3.5, ge=0.0, le=60.0)
    crate_delay_min: float = Field(default=0.05, ge=0.0, le=5.0)
    crate_delay_max: float = Field(default=0.2, ge=0.0, le=5.0)
    start_stop_hotkey: str | None = "f8"
    pause_hotkey: str | None = "f9"
    quit_hotkey: str | None = "f10"
    start_paused: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    discord_webhook_url: SecretStr | None = None
    webhook_summary_interval: float = Field(default=3600.0, ge=60.0, le=86400.0)
    webhook_status_interval: float = Field(default=60.0, ge=0.0, le=3600.0)
    webhook_notification_profile: Literal["minimal", "balanced", "detailed"] = "balanced"
    webhook_include_charts: bool = True
    webhook_include_screenshots: bool = False
    check_for_updates: bool = True

    theme: Literal["system", "dark", "light"] = "system"
    start_minimized: bool = False
    bot_autostart: bool = False
    close_to_tray: bool = True
    close_managed_browser_on_exit: bool = False
    main_always_on_top: bool = False
    main_focused_opacity: float = Field(default=1.0, ge=0.25, le=1.0)
    main_unfocused_opacity: float = Field(default=0.92, ge=0.25, le=1.0)
    overlay_visible: bool = False
    overlay_always_on_top: bool = True
    overlay_click_through: bool = True
    overlay_focused_opacity: float = Field(default=1.0, ge=0.25, le=1.0)
    overlay_unfocused_opacity: float = Field(default=0.85, ge=0.25, le=1.0)

    def __init__(self, **data: Any) -> None:
        data.pop("_env_file", None)
        super().__init__(**data)

    def event_automation_enabled_for(self, event_key: str) -> bool:
        return self.event_automation_enabled and self.event_automation_overrides.get(
            event_key, self.event_default_enabled
        )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_configuration(cls, value: object) -> object:
        if isinstance(value, Mapping):
            value = dict(value)
            legacy_merge_five = value.pop("prefer_merge_five", None)
            if legacy_merge_five is not None:
                defaults = dict(value.get("item_policy_defaults") or {})
                if not legacy_merge_five:
                    defaults["prefer_merge_five"] = False
                value["item_policy_defaults"] = defaults
            value.pop("allow_shop_order_starts", None)
            value.pop("event_revisit_interval", None)
            if value.get("schema_version", 1) == 1:
                value["schema_version"] = CONFIG_SCHEMA_VERSION
            if "game_portal" not in value:
                portal = portal_for_page_url(value.get("game_url", ""))
                if portal is not None:
                    value["game_portal"] = portal.kind
                    value["game_url"] = portal.canonical_url
                elif value.get("game_url"):
                    raise ValueError("game_url does not match a registered integration")
            if "game_portal" in value and "game_url" not in value:
                value["game_url"] = portal_definition(
                    GamePortal(value["game_portal"])
                ).canonical_url
        return value

    @model_validator(mode="after")
    def game_url_matches_selected_portal(self) -> Self:
        portal = portal_definition(self.game_portal)
        if not portal.matches_page_url(self.game_url):
            raise ValueError("game_url must be an HTTPS page owned by the selected integration")
        return self

    @field_validator("discord_webhook_url", mode="before")
    @classmethod
    def empty_webhook_url_is_disabled(cls, value: object) -> object:
        if isinstance(value, str):
            return SecretStr(value) if value.strip() else None
        return value

    @field_validator("discord_webhook_url")
    @classmethod
    def webhook_url_uses_https(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith("https://"):
            raise ValueError("Discord webhook URL must use HTTPS")
        return value

    @field_validator("shop_overrides", "recipe_overrides", "building_repair_overrides")
    @classmethod
    def validate_game_ids(cls, value: dict[str, bool]) -> dict[str, bool]:
        if any(not game_id.strip() or game_id != game_id.strip() for game_id in value):
            raise ValueError("game IDs must be non-empty and have no surrounding whitespace")
        return value

    @field_validator("shop_ingredient_reserves")
    @classmethod
    def validate_shop_ingredient_reserves(cls, value: dict[str, int]) -> dict[str, int]:
        if any(
            not item_id.strip()
            or item_id != item_id.strip()
            or isinstance(amount, bool)
            or amount < 0
            or amount > 1_000_000_000
            for item_id, amount in value.items()
        ):
            raise ValueError("shop ingredient reserves must use valid IDs and non-negative amounts")
        return value

    @field_validator("marketplace_policy_overrides")
    @classmethod
    def validate_marketplace_policy_keys(cls, value: dict[str, bool]) -> dict[str, bool]:
        for key in value:
            parts = key.split(":")
            valid = (len(parts) == 2 and parts[0] == "free" and bool(parts[1])) or (
                len(parts) == 3 and parts[0] == "flash" and bool(parts[1]) and bool(parts[2])
            )
            if not valid or key != key.strip():
                raise ValueError("invalid marketplace policy key")
        return value

    @field_validator("marketplace_sort_column", mode="before")
    @classmethod
    def normalize_legacy_marketplace_live_sort(cls, value: object) -> object:
        return "offer" if value in {"stock", "availability", "reward"} else value

    def marketplace_policy_enabled(self, policy_key: str) -> bool:
        return self.marketplace_policy_overrides.get(
            policy_key, self.marketplace_policy_default_enabled(policy_key)
        )

    @staticmethod
    def marketplace_policy_default_enabled(policy_key: str) -> bool:
        return policy_key.startswith("free:")

    def building_repair_enabled(self, building_id: str) -> bool:
        return self.building_repair_overrides.get(building_id, self.building_repair_default_enabled)

    @field_validator("item_default_overrides", "item_policy_overrides")
    @classmethod
    def validate_policy_keys(
        cls, value: dict[str, ItemPolicyOverride]
    ) -> dict[str, ItemPolicyOverride]:
        if any(not key.strip() or key != key.strip() for key in value):
            raise ValueError("policy keys must be non-empty and have no surrounding whitespace")
        return value

    @field_validator("item_category_defaults")
    @classmethod
    def validate_item_categories(
        cls, value: dict[str, ItemPolicyOverride]
    ) -> dict[str, ItemPolicyOverride]:
        if any(
            not category.strip() or category != category.strip() or "/" in category
            for category in value
        ):
            raise ValueError("item categories must be non-empty single path segments")
        return value

    @field_validator("start_stop_hotkey", "pause_hotkey", "quit_hotkey", mode="before")
    @classmethod
    def normalize_hotkeys(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("hotkeys must be non-empty strings or null")
        return normalize_hotkey(value)

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        for name in ("item_action_delay", "crate_delay"):
            if getattr(self, f"{name}_min") > getattr(self, f"{name}_max"):
                raise ValueError(f"{name}_min must be less than or equal to {name}_max")
        normalized_hotkeys = tuple(
            hotkey
            for hotkey in (
                self.start_stop_hotkey,
                self.pause_hotkey,
                self.quit_hotkey,
            )
            if hotkey is not None
        )
        if len(set(normalized_hotkeys)) != len(normalized_hotkeys):
            raise ValueError("start/stop, pause/resume, and quit hotkeys must be different")
        return self

    def item_policy_default(self, policy_key: str, category: str | None = None) -> ItemPolicy:
        resolution_keys = _item_policy_resolution_keys(policy_key)
        base_key = resolution_keys[0]
        values = self.item_policy_defaults.model_dump()
        category_key = category or base_key.partition("/")[0]
        if recommended := _RECOMMENDED_ITEM_CATEGORY_DEFAULTS.get(category_key):
            values.update(recommended.model_dump(exclude_none=True))
        if category_override := self.item_category_defaults.get(category_key):
            values.update(category_override.model_dump(exclude_none=True))
        for key in resolution_keys:
            if recommended := _RECOMMENDED_ITEM_DEFAULTS.get(key):
                values.update(recommended.model_dump(exclude_none=True))
            if item_default := self.item_default_overrides.get(key):
                values.update(item_default.model_dump(exclude_none=True))
        return ItemPolicy.model_validate(values)

    def item_policy(self, policy_key: str, category: str | None = None) -> ItemPolicy:
        values = self.item_policy_default(policy_key, category).model_dump()
        for key in _item_policy_resolution_keys(policy_key):
            if override := self.item_policy_overrides.get(key):
                values.update(override.model_dump(exclude_none=True))
        return ItemPolicy.model_validate(values)
