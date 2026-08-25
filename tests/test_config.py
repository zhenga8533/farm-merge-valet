from __future__ import annotations

import pytest
from pydantic import ValidationError

from farm_merge_valet.config import Settings


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cdp_port", 0),
        ("merge_empty_cell_reserve", -1),
        ("producer_claim_min_empty_cells", 0),
        ("loop_interval", 0),
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
        Settings(_env_file=None, **{field: value})


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
    ],
)
def test_settings_reject_removed_legacy_keys(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: 1})


def test_merge_space_reserve_can_be_disabled() -> None:
    assert Settings(_env_file=None, merge_empty_cell_reserve=0).merge_empty_cell_reserve == 0


def test_merge_five_is_enabled_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.item_policy("crops/wheat").enabled
    assert settings.item_policy("crops/wheat").prefer_merge_five
    assert not settings.item_policy("crops/wheat").always_remove


def test_item_policy_partial_override_inherits_other_defaults() -> None:
    settings = Settings(
        _env_file=None,
        item_policy_overrides={"animals/cow": {"prefer_merge_five": False}},
    )

    assert not settings.item_policy("animals/cow").prefer_merge_five
    assert not settings.item_policy("animals/cow").always_remove
    assert settings.item_policy("crops/wheat").prefer_merge_five


def test_item_policy_override_can_reenable_family_when_global_default_is_disabled() -> None:
    settings = Settings(
        _env_file=None,
        item_policy_defaults={"enabled": False},
        item_policy_overrides={"animals/cow": {"enabled": True}},
    )

    assert not settings.item_policy("crops/wheat").enabled
    assert settings.item_policy("animals/cow").enabled


def test_legacy_merge_five_switch_remains_a_global_kill_switch() -> None:
    settings = Settings(
        _env_file=None,
        prefer_merge_five=False,
        item_policy_overrides={"animals/cow": {"prefer_merge_five": True}},
    )

    assert not settings.item_policy("crops/wheat").prefer_merge_five
    assert settings.item_policy("animals/cow").prefer_merge_five


def test_item_policy_loads_from_json_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "FMV_ITEM_POLICY_DEFAULTS",
        '{"enabled": true, "prefer_merge_five": true, "always_remove": false}',
    )
    monkeypatch.setenv(
        "FMV_ITEM_POLICY_OVERRIDES",
        '{"building_resources/stone": {"always_remove": true}}',
    )

    settings = Settings(_env_file=None)

    assert settings.item_policy("building_resources/stone").always_remove


def test_all_shop_automation_is_enabled_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.shop_default_enabled
    assert settings.recipe_default_enabled
    assert settings.shop_overrides == {}
    assert settings.recipe_overrides == {}


def test_shop_policy_overrides_load_from_json_environment_maps(monkeypatch) -> None:
    monkeypatch.setenv("FMV_SHOP_DEFAULT_ENABLED", "false")
    monkeypatch.setenv("FMV_SHOP_OVERRIDES", '{"market": true, "bakery": false}')
    monkeypatch.setenv("FMV_RECIPE_OVERRIDES", '{"recipe_flour": false}')

    settings = Settings(_env_file=None)

    assert not settings.shop_default_enabled
    assert settings.recipe_default_enabled
    assert settings.shop_overrides == {"market": True, "bakery": False}
    assert settings.recipe_overrides == {"recipe_flour": False}


def test_producer_claim_reserves_four_cells_by_default() -> None:
    assert Settings(_env_file=None).producer_claim_min_empty_cells == 4


def test_managed_browser_defaults_to_auto_launch() -> None:
    settings = Settings(_env_file=None)

    assert settings.browser == "auto"
    assert settings.browser_auto_launch
    assert settings.browser_profile_dir is None


def test_generated_assets_default_to_the_per_user_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    settings = Settings(_env_file=None)

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
        Settings(_env_file=None, **{minimum: 2.0, maximum: 1.0})


def test_action_cadence_defaults_keep_crates_fast() -> None:
    settings = Settings(_env_file=None)

    assert (settings.item_action_delay_min, settings.item_action_delay_max) == (1.5, 3.5)
    assert (settings.crate_delay_min, settings.crate_delay_max) == (0.05, 0.2)


def test_idle_and_webhook_summary_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.idle_wait_seconds == 30.0
    assert settings.webhook_summary_interval == 3600.0
    assert settings.webhook_status_interval == 60.0


def test_empty_webhook_url_disables_notifications() -> None:
    assert Settings(_env_file=None, discord_webhook_url="").discord_webhook_url is None


def test_webhook_url_is_stored_as_a_secret() -> None:
    settings = Settings(
        _env_file=None,
        discord_webhook_url="https://example.test/private-token",
    )

    assert settings.discord_webhook_url is not None
    assert "private-token" not in repr(settings.discord_webhook_url)
