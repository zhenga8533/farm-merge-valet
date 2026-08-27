"""Versioned application configuration and atomic per-user persistence."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from farm_merge_valet.core.board import item_base_policy_key, item_family_policy_key
from farm_merge_valet.hotkeys import normalize_hotkey

CONFIG_SCHEMA_VERSION: Literal[1] = 1


def user_data_root() -> Path:
    if local_app_data := os.environ.get("LOCALAPPDATA"):
        return Path(local_app_data) / "FarmMergeValet"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FarmMergeValet"
    if xdg_data_home := os.environ.get("XDG_DATA_HOME"):
        return Path(xdg_data_home) / "farm-merge-valet"
    return Path.home() / ".local" / "share" / "farm-merge-valet"


def user_config_path() -> Path:
    return user_data_root() / "config.json"


def user_cache_root() -> Path:
    if local_app_data := os.environ.get("LOCALAPPDATA"):
        return Path(local_app_data) / "FarmMergeValet" / "Cache"
    if xdg_cache_home := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg_cache_home) / "farm-merge-valet"
    return Path.home() / ".cache" / "farm-merge-valet"


class ItemPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    merge: bool = True
    prefer_merge_five: bool = True
    collect: bool = False
    always_remove: bool = False


class ItemPolicyOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool | None = None
    merge: bool | None = None
    prefer_merge_five: bool | None = None
    collect: bool | None = None
    always_remove: bool | None = None


def _item_category_defaults() -> dict[str, ItemPolicyOverride]:
    return {"ingredients": ItemPolicyOverride(collect=True)}


def _item_specific_defaults() -> dict[str, ItemPolicyOverride]:
    return {"currencies/ticket": ItemPolicyOverride(collect=True)}


class AppConfig(BaseModel):
    """Complete validated configuration snapshot used by the GUI and runtime."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal[1] = CONFIG_SCHEMA_VERSION
    window_title: str = "r/FarmMergeValley"
    browser: Literal["auto", "chrome", "edge", "brave", "chromium"] = "auto"
    browser_executable: Path | None = None
    browser_profile_dir: Path | None = None
    browser_auto_launch: bool = True
    game_url: str = "https://www.reddit.com/r/FarmMergeValley/"
    cdp_port: int = Field(default=9222, ge=1, le=65535)
    catalog_dir: Path = Field(default_factory=lambda: user_cache_root() / "catalog")
    atlas_cache_dir: Path = Field(default_factory=lambda: user_cache_root() / "atlases")

    merge_empty_cell_reserve: int = Field(default=1, ge=0, le=50)
    producer_collect_min_empty_cells: int = Field(default=4, ge=1, le=50)
    prefer_merge_five: bool = True
    item_policy_defaults: ItemPolicy = Field(default_factory=ItemPolicy)
    item_category_defaults: dict[str, ItemPolicyOverride] = Field(
        default_factory=_item_category_defaults
    )
    item_default_overrides: dict[str, ItemPolicyOverride] = Field(
        default_factory=_item_specific_defaults
    )
    item_policy_overrides: dict[str, ItemPolicyOverride] = Field(default_factory=dict)
    shop_default_enabled: bool = True
    recipe_default_enabled: bool = True
    shop_overrides: dict[str, bool] = Field(default_factory=dict)
    recipe_overrides: dict[str, bool] = Field(default_factory=dict)
    items_sort_column: Literal[
        "item", "category", "enabled", "merge", "merge_five", "collect", "remove"
    ] = "item"
    items_sort_descending: bool = False
    shops_sort_column: Literal["item", "type", "enabled"] = "item"
    shops_sort_descending: bool = False

    loop_interval: float = Field(default=1.0, ge=0.01, le=60.0)
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

    theme: Literal["system", "dark", "light"] = "system"
    start_minimized: bool = False
    bot_autostart: bool = False
    close_to_tray: bool = True
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

    @field_validator("shop_overrides", "recipe_overrides")
    @classmethod
    def validate_game_ids(cls, value: dict[str, bool]) -> dict[str, bool]:
        if any(not game_id.strip() or game_id != game_id.strip() for game_id in value):
            raise ValueError("game IDs must be non-empty and have no surrounding whitespace")
        return value

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
        policy_key = item_base_policy_key(item_family_policy_key(policy_key))
        values = self.item_policy_defaults.model_dump()
        if not self.prefer_merge_five:
            values["prefer_merge_five"] = False
        category_key = category or policy_key.partition("/")[0]
        if category_override := self.item_category_defaults.get(category_key):
            values.update(category_override.model_dump(exclude_none=True))
        if item_default := self.item_default_overrides.get(policy_key):
            values.update(item_default.model_dump(exclude_none=True))
        return ItemPolicy.model_validate(values)

    def item_policy(self, policy_key: str, category: str | None = None) -> ItemPolicy:
        family_key = item_family_policy_key(policy_key)
        base_key = item_base_policy_key(family_key)
        values = self.item_policy_default(base_key, category).model_dump()
        if override := self.item_policy_overrides.get(base_key):
            values.update(override.model_dump(exclude_none=True))
        if family_key != base_key and (
            variant_override := self.item_policy_overrides.get(family_key)
        ):
            values.update(variant_override.model_dump(exclude_none=True))
        if policy_key != family_key and (
            tier_override := self.item_policy_overrides.get(policy_key)
        ):
            values.update(tier_override.model_dump(exclude_none=True))
        return ItemPolicy.model_validate(values)


Settings = AppConfig
ConfigListener = Callable[[AppConfig], None]


class ConfigStore:
    """Own a validated snapshot and persist successful updates atomically."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_config_path()
        self._lock = RLock()
        self._write_lock = RLock()
        self._listeners: list[ConfigListener] = []
        self._current = AppConfig()

    @property
    def current(self) -> AppConfig:
        with self._lock:
            return self._current.model_copy(deep=True)

    def load(self) -> AppConfig:
        with self._lock:
            if self.path.exists():
                self._current = AppConfig.model_validate_json(self.path.read_text(encoding="utf-8"))
            else:
                self._persist(self._current)
            return self._current.model_copy(deep=True)

    def replace(self, config: AppConfig) -> AppConfig:
        snapshot = AppConfig.model_validate(config.model_dump())
        with self._lock:
            unchanged = snapshot == self._current and self.path.exists()
        if unchanged:
            return snapshot.model_copy(deep=True)
        self.persist(snapshot)
        return self.publish(snapshot)

    def persist(self, config: AppConfig) -> AppConfig:
        """Durably write a validated snapshot without notifying listeners."""

        snapshot = AppConfig.model_validate(config.model_dump())
        with self._write_lock:
            self._persist(snapshot)
        return snapshot.model_copy(deep=True)

    def publish(self, config: AppConfig) -> AppConfig:
        """Make an already-persisted snapshot current and notify listeners."""

        snapshot = AppConfig.model_validate(config.model_dump())
        with self._lock:
            self._current = snapshot
            listeners = tuple(self._listeners)
        delivered = snapshot.model_copy(deep=True)
        for listener in listeners:
            listener(delivered)
        return delivered

    def update(self, **changes: object) -> AppConfig:
        with self._lock:
            values = self._current.model_dump()
        values.update(changes)
        return self.replace(AppConfig.model_validate(values))

    def reset(self) -> AppConfig:
        return self.replace(AppConfig())

    def subscribe(self, listener: ConfigListener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def _persist(self, config: AppConfig) -> None:
        payload = config.model_dump(mode="json")
        payload["discord_webhook_url"] = (
            config.discord_webhook_url.get_secret_value()
            if config.discord_webhook_url is not None
            else None
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp", text=True
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass


class ConfigProxy:
    """Live read-through facade for legacy runtime modules during GUI updates."""

    def __init__(self, config: AppConfig) -> None:
        object.__setattr__(self, "_lock", RLock())
        object.__setattr__(self, "_config", config)

    def replace(self, config: AppConfig) -> None:
        with self._lock:
            object.__setattr__(self, "_config", config.model_copy(deep=True))

    def snapshot(self) -> AppConfig:
        with self._lock:
            return self._config.model_copy(deep=True)

    def __getattr__(self, name: str) -> Any:
        with self._lock:
            return getattr(self._config, name)

    def __setattr__(self, name: str, value: object) -> None:
        with self._lock:
            setattr(self._config, name, value)


# Runtime reads are live, while constructors receive snapshots where practical.
settings = ConfigProxy(AppConfig())
