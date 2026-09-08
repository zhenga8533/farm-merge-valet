from __future__ import annotations

import pytest
from pydantic import ValidationError

from farm_merge_valet.config import AppConfig, ConfigStore


def test_land_expansion_requires_explicit_enablement_and_spending_limits() -> None:
    config = AppConfig()

    assert not config.land_expansion_automation_enabled
    assert config.land_expansion_max_coin_cost == 0
    assert config.land_expansion_max_gem_cost == 0


def test_spending_reserves_default_to_non_disruptive_values() -> None:
    config = AppConfig()

    assert config.minimum_energy_reserve == 0
    assert config.minimum_ticket_reserve == 0
    assert config.minimum_coin_reserve == 0
    assert config.minimum_gem_reserve == 0
    assert config.shop_ingredient_reserve_default == 0
    assert config.shop_ingredient_reserves == {}


def test_shop_ingredient_reserves_validate_ids_and_amounts() -> None:
    assert AppConfig(shop_ingredient_reserves={"wheat": 3}).shop_ingredient_reserves == {"wheat": 3}
    with pytest.raises(ValidationError):
        AppConfig(shop_ingredient_reserves={" wheat": 3})
    with pytest.raises(ValidationError):
        AppConfig(shop_ingredient_reserves={"wheat": -1})


def test_building_repair_policies_default_enabled_and_support_overrides() -> None:
    config = AppConfig(building_repair_overrides={"bakery": False})

    assert config.preserve_building_repair_resources
    assert config.building_repair_enabled("barn")
    assert not config.building_repair_enabled("bakery")


def test_marketplace_policies_default_free_claims_enabled_and_validate_semantic_keys() -> None:
    config = AppConfig()
    assert config.marketplace_policy_enabled("free:gems_5_no_ads")
    assert not config.marketplace_policy_enabled("flash:flash_deal_ingredient:wheat")
    enabled = AppConfig(marketplace_policy_overrides={"flash:flash_deal_ingredient:wheat": True})
    assert enabled.marketplace_policy_enabled("flash:flash_deal_ingredient:wheat")
    disabled = AppConfig(marketplace_policy_overrides={"free:gems_5_no_ads": False})
    assert not disabled.marketplace_policy_enabled("free:gems_5_no_ads")
    with pytest.raises(ValidationError):
        AppConfig(marketplace_policy_overrides={"flash:slot": True})


def test_legacy_marketplace_live_sort_normalizes_to_offer() -> None:
    config = AppConfig.model_validate({"marketplace_sort_column": "stock"})
    assert config.marketplace_sort_column == "offer"
    config = AppConfig.model_validate({"marketplace_sort_column": "availability"})
    assert config.marketplace_sort_column == "offer"
    config = AppConfig.model_validate({"marketplace_sort_column": "reward"})
    assert config.marketplace_sort_column == "offer"


def test_legacy_shop_start_toggle_is_discarded() -> None:
    config = AppConfig.model_validate({"allow_shop_order_starts": False})

    assert "allow_shop_order_starts" not in config.model_dump()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cdp_port", 0),
        ("merge_empty_cell_reserve", -1),
        ("merge_empty_cell_reserve", 51),
        ("producer_interact_min_empty_cells", 0),
        ("producer_interact_min_empty_cells", 51),
        ("loop_interval", 0),
        ("loop_interval", 60.1),
        ("idle_wait_seconds", -0.1),
        ("crate_delay_max", 5.1),
        ("webhook_summary_interval", 59),
        ("webhook_status_interval", -0.1),
        ("gui_opacity", 1.1),
        ("log_level", "VERBOSE"),
        ("browser", "firefox"),
    ],
)
def test_settings_reject_invalid_runtime_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AppConfig(**{field: value})


def test_hotkey_commands_have_distinct_defaults_and_allow_disabling() -> None:
    config = AppConfig()

    assert (config.start_stop_hotkey, config.pause_hotkey, config.quit_hotkey) == (
        "f8",
        "f9",
        "f10",
    )
    assert AppConfig(start_stop_hotkey=None).start_stop_hotkey is None


def test_duplicate_hotkeys_are_rejected() -> None:
    with pytest.raises(ValidationError, match="hotkeys must be different"):
        AppConfig(start_stop_hotkey="Ctrl + X", pause_hotkey="ctrl+x")


def test_hotkeys_are_canonicalized_before_persistence() -> None:
    config = AppConfig(start_stop_hotkey="Shift + Ctrl + X")

    assert config.start_stop_hotkey == "ctrl+shift+x"


@pytest.mark.parametrize(
    "field",
    [
        "need_space_confidence",
        "crate_click_batch_size",
        "match_confidence",
        "board_dead_left_ratio",
        "pan_step_ratio",
        "merge_drag_duration",
        "drag_duration_min",
        "drag_duration_max",
        "crate_click_settle",
        "producer_collect_min_empty_cells",
    ],
)
def test_settings_reject_removed_legacy_keys(field: str) -> None:
    with pytest.raises(ValidationError):
        AppConfig(**{field: 1})


def test_merge_space_reserve_can_be_disabled() -> None:
    assert AppConfig(merge_empty_cell_reserve=0).merge_empty_cell_reserve == 0


def test_merge_five_is_enabled_by_default() -> None:
    settings = AppConfig()

    assert settings.item_policy("crops/wheat").enabled
    assert settings.item_policy("crops/wheat").merge
    assert settings.item_policy("crops/wheat").prefer_merge_five
    assert settings.item_policy("crops/wheat").interact
    assert not settings.item_policy("crops/wheat").always_remove


def test_interaction_defaults_support_category_and_item_specific_policies() -> None:
    settings = AppConfig()

    assert settings.item_policy("ingredients/milk").interact
    assert settings.item_policy("ingredients/egg").interact
    assert settings.item_policy("currencies/ticket").interact
    assert settings.item_policy("resources/crate").interact
    assert settings.item_policy("resources/crate/tier/1").interact
    assert not settings.item_policy("currencies/coin").interact
    assert settings.item_policy("crops/wheat").interact
    assert settings.item_policy("animals/cow").interact
    assert settings.item_policy("obstacles/rock").interact
    assert settings.item_policy("rewards/reward_chest/tier/1").interact
    assert settings.item_policy("rewards/reward_crate_stickerbook").interact


def test_upgrade_card_interaction_defaults_to_tiers_one_and_three_for_every_target() -> None:
    settings = AppConfig()

    for target in ("wheat", "milk"):
        key = f"upgrade_cards/upgrade_card/{target}"
        assert settings.item_policy(f"{key}/tier/1").interact
        assert not settings.item_policy(f"{key}/tier/2").interact
        assert settings.item_policy(f"{key}/tier/3").interact


def test_configured_interaction_defaults_override_recommendations() -> None:
    settings = AppConfig(
        _env_file=None,
        item_category_defaults={"ingredients": {"interact": False}},
        item_default_overrides={"currencies/ticket": {"interact": False}},
    )

    assert not settings.item_policy("ingredients/milk").interact
    assert not settings.item_policy("currencies/ticket").interact
    assert settings.item_policy("crops/wheat").interact
    assert settings.item_policy("resources/crate/tier/2").interact


def test_legacy_claim_policy_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig(item_policy_overrides={"ingredients/milk": {"claim": True}})


def test_legacy_collect_policy_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig(item_policy_overrides={"ingredients/milk": {"collect": True}})


def test_item_policy_partial_override_inherits_other_defaults() -> None:
    settings = AppConfig(
        _env_file=None,
        item_policy_overrides={"animals/cow": {"prefer_merge_five": False}},
    )

    assert not settings.item_policy("animals/cow").prefer_merge_five
    assert not settings.item_policy("animals/cow").always_remove
    assert settings.item_policy("crops/wheat").prefer_merge_five


def test_tier_policy_inherits_family_policy_and_can_override_one_tier() -> None:
    settings = AppConfig(
        _env_file=None,
        item_policy_overrides={
            "crops/wheat": {"prefer_merge_five": False},
            "crops/wheat/tier/2": {"merge": False},
        },
    )

    assert not settings.item_policy("crops/wheat/tier/1").prefer_merge_five
    assert settings.item_policy("crops/wheat/tier/1").merge
    assert not settings.item_policy("crops/wheat/tier/2").merge


def test_upgrade_card_variant_inherits_base_family_policy() -> None:
    settings = AppConfig(
        _env_file=None,
        item_policy_overrides={
            "upgrade_cards/upgrade_card": {"merge": False},
            "upgrade_cards/upgrade_card/wheat": {"always_remove": True},
        },
    )

    policy = settings.item_policy("upgrade_cards/upgrade_card/wheat/tier/2")

    assert not policy.merge
    assert policy.always_remove


def test_upgrade_card_base_tier_override_applies_to_every_target() -> None:
    settings = AppConfig(
        _env_file=None,
        item_policy_overrides={
            "upgrade_cards/upgrade_card/tier/1": {"interact": False},
        },
    )

    assert not settings.item_policy("upgrade_cards/upgrade_card/wheat/tier/1").interact


def test_item_policy_override_can_reenable_family_when_global_default_is_disabled() -> None:
    settings = AppConfig(
        _env_file=None,
        item_policy_defaults={"enabled": False},
        item_policy_overrides={"animals/cow": {"enabled": True}},
    )

    assert not settings.item_policy("crops/wheat").enabled
    assert settings.item_policy("animals/cow").enabled


def test_legacy_merge_five_switch_remains_a_global_kill_switch() -> None:
    settings = AppConfig(
        _env_file=None,
        prefer_merge_five=False,
        item_policy_overrides={"animals/cow": {"prefer_merge_five": True}},
    )

    assert not settings.item_policy("crops/wheat").prefer_merge_five
    assert settings.item_policy("animals/cow").prefer_merge_five


def test_item_policy_environment_variables_are_ignored(monkeypatch) -> None:
    monkeypatch.setenv(
        "FMV_ITEM_POLICY_DEFAULTS",
        '{"enabled": true, "merge": true, "prefer_merge_five": true, '
        '"interact": false, "always_remove": false}',
    )
    monkeypatch.setenv(
        "FMV_ITEM_POLICY_OVERRIDES",
        '{"resources/stone": {"always_remove": true}}',
    )

    settings = AppConfig()

    assert not settings.item_policy("resources/stone").always_remove


def test_item_interaction_override_can_disable_one_category_default() -> None:
    settings = AppConfig(
        _env_file=None,
        item_policy_overrides={"ingredients/milk": {"interact": False}},
    )

    assert not settings.item_policy("ingredients/milk").interact
    assert settings.item_policy("ingredients/egg").interact


def test_all_shop_automation_is_enabled_by_default() -> None:
    settings = AppConfig()

    assert settings.shop_default_enabled
    assert settings.recipe_default_enabled
    assert settings.shop_overrides == {}
    assert settings.recipe_overrides == {}


def test_shop_policy_environment_variables_are_ignored(monkeypatch) -> None:
    monkeypatch.setenv("FMV_SHOP_DEFAULT_ENABLED", "false")
    monkeypatch.setenv("FMV_SHOP_OVERRIDES", '{"market": true, "bakery": false}')
    monkeypatch.setenv("FMV_RECIPE_OVERRIDES", '{"recipe_flour": false}')

    settings = AppConfig()

    assert settings.shop_default_enabled
    assert settings.recipe_default_enabled
    assert settings.shop_overrides == {}
    assert settings.recipe_overrides == {}


def test_producer_interaction_reserves_four_cells_by_default() -> None:
    assert AppConfig().producer_interact_min_empty_cells == 4


def test_reward_overlays_are_dismissed_by_default() -> None:
    assert AppConfig().auto_dismiss_overlays


def test_storage_bubbles_are_popped_by_default() -> None:
    assert AppConfig().auto_pop_storage_bubbles


def test_managed_browser_defaults_to_auto_launch() -> None:
    settings = AppConfig()

    assert settings.browser == "auto"
    assert settings.browser_auto_launch
    assert settings.auto_recover_game
    assert settings.browser_profile_dir is None


def test_generated_assets_default_to_the_per_user_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    settings = AppConfig()

    assert settings.catalog_dir == tmp_path / "FarmMergeValet" / "Cache" / "catalog"
    assert settings.atlas_cache_dir == tmp_path / "FarmMergeValet" / "Cache" / "atlases"


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [
        ("item_action_delay_min", "item_action_delay_max"),
        ("crate_delay_min", "crate_delay_max"),
    ],
)
def test_timing_minimum_cannot_exceed_maximum(minimum: str, maximum: str) -> None:
    with pytest.raises(ValidationError):
        AppConfig(**{minimum: 2.0, maximum: 1.0})


def test_action_cadence_defaults_keep_crates_fast() -> None:
    settings = AppConfig()

    assert (settings.item_action_delay_min, settings.item_action_delay_max) == (1.5, 3.5)
    assert (settings.crate_delay_min, settings.crate_delay_max) == (0.05, 0.2)


def test_idle_and_webhook_summary_defaults() -> None:
    settings = AppConfig()

    assert settings.idle_wait_seconds == 30.0
    assert settings.webhook_summary_interval == 3600.0
    assert settings.webhook_status_interval == 60.0


def test_empty_webhook_url_disables_notifications() -> None:
    assert AppConfig(discord_webhook_url="").discord_webhook_url is None


def test_webhook_url_is_stored_as_a_secret() -> None:
    settings = AppConfig(
        _env_file=None,
        discord_webhook_url="https://example.test/private-token",
    )

    assert settings.discord_webhook_url is not None
    assert "private-token" not in repr(settings.discord_webhook_url)


def test_config_store_round_trips_atomically_and_notifies(tmp_path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    received = []
    unsubscribe = store.subscribe(received.append)

    initial = store.load()
    updated = store.update(
        theme="dark",
        item_policy_overrides={"animals/cow": {"interact": True}},
        discord_webhook_url="https://example.test/private-token",
    )
    unsubscribe()

    assert initial == AppConfig()
    assert updated.theme == "dark"
    assert received == [updated]
    assert ConfigStore(path).load() == updated
    assert "private-token" in path.read_text(encoding="utf-8")
    assert not list(path.parent.glob(".config.json.*.tmp"))


def test_config_store_skips_unchanged_snapshot_notifications(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    config = store.load()
    received: list[AppConfig] = []
    store.subscribe(received.append)

    result = store.replace(config)

    assert result == config
    assert received == []


def test_config_store_rejects_malformed_files_without_overwriting(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ValueError):
        ConfigStore(path).load()

    assert path.read_text(encoding="utf-8") == "not-json"


def test_webhook_requires_https() -> None:
    with pytest.raises(ValidationError):
        AppConfig(discord_webhook_url="http://example.test/token")
