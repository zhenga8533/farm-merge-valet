from __future__ import annotations

from farm_merge_valet.cdp.upgrade_progress import (
    _READ_UPGRADE_PROGRESS_EXPRESSION,
    read_upgrade_progress,
)
from farm_merge_valet.core.upgrade_progress import UpgradeTierState


def test_upgrade_progress_reads_highest_applied_tier_and_producer(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.upgrade_progress.evaluate",
        lambda *_args: [
            {
                "targetID": "wheat",
                "producerBlueprintID": "wheat_4",
                "appliedTier": 3,
            },
            {
                "targetID": "milk",
                "producerBlueprintID": "cow_4",
                "appliedTier": 1,
            },
        ],
    )

    progress = read_upgrade_progress(9222, "Farm")

    assert progress is not None
    assert [target.target_id for target in progress.targets] == ["milk", "wheat"]
    wheat = progress.target("wheat")
    assert wheat is not None
    assert wheat.producer_blueprint_id == "wheat_4"
    assert wheat.tier_state(1) is UpgradeTierState.APPLIED
    assert wheat.tier_state(2) is UpgradeTierState.APPLIED
    assert wheat.tier_state(3) is UpgradeTierState.APPLIED
    assert wheat.tier_state(4) is UpgradeTierState.NOT_APPLIED


def test_upgrade_progress_rejects_unavailable_or_malformed_runtime_data(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.upgrade_progress.evaluate",
        lambda *_args: None,
    )
    assert read_upgrade_progress(9222) is None

    monkeypatch.setattr(
        "farm_merge_valet.cdp.upgrade_progress.evaluate",
        lambda *_args: [
            {"targetID": "wheat", "producerBlueprintID": "wheat_4", "appliedTier": True},
            {"targetID": "milk", "producerBlueprintID": "cow_4", "appliedTier": -1},
        ],
    )
    progress = read_upgrade_progress(9222)
    assert progress is not None and progress.targets == ()


def test_upgrade_progress_uses_authoritative_game_model_and_harvest_rewards() -> None:
    assert "upgradeCard?._model?._itemData" in _READ_UPGRADE_PROGRESS_EXPRESSION
    assert "components?.harvestable" in _READ_UPGRADE_PROGRESS_EXPRESSION
    assert "harvestable.harvestReward" in _READ_UPGRADE_PROGRESS_EXPRESSION
