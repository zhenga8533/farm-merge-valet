from __future__ import annotations

from farm_merge_valet.automation.runtime import LiveCellState, RewardRequirement
from farm_merge_valet.cdp.board_store import _arm_board_store_target, read_board_state
from farm_merge_valet.core.items import ProducerKind, ProducerState
from farm_merge_valet.core.obstacles import ObstacleState


def test_read_board_state_preserves_cells_without_content(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.board_store.evaluate",
        lambda *_args, **_kwargs: [
            {"column": 1, "row": 2, "hasContent": False, "blueprintID": None},
            {"column": 3, "row": 4, "hasContent": True, "blueprintID": "empty"},
            {"column": 5, "row": 6, "hasContent": True, "blueprintID": "wheat_1"},
        ],
    )

    assert read_board_state(9222, "Farm") == {
        (1, 2): LiveCellState(False, None),
        (3, 4): LiveCellState(True, "empty"),
        (5, 6): LiveCellState(True, "wheat_1"),
    }


def test_arm_board_store_releases_query_object(monkeypatch) -> None:
    methods = []

    def command(_ws_url, method, _params=None, **_kwargs):
        methods.append(method)
        if method == "Runtime.evaluate":
            return {"result": {"objectId": "map-prototype"}}
        if method == "Runtime.queryObjects":
            return {"objects": {"objectId": "map-instances"}}
        if method == "Runtime.callFunctionOn":
            return {
                "result": {
                    "value": {
                        "status": "found",
                        "contentCount": 900,
                        "renderableCount": 850,
                        "size": 1006,
                    }
                }
            }
        return {}

    monkeypatch.setattr("farm_merge_valet.cdp.board_store._command_target", command)

    result = _arm_board_store_target("ws://game", None)

    assert result.startswith("found")
    assert methods[-1] == "Runtime.releaseObject"


def test_read_board_state_preserves_collectable_semantics(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.board_store.evaluate",
        lambda *_args, **_kwargs: [
            {
                "column": 4,
                "row": 5,
                "hasContent": True,
                "blueprintID": "milk",
                "objectID": 31,
                "collectable": True,
                "collectableIngredient": True,
                "itemVariant": "wheat",
                "behaviorNames": ["clickable", "ingredient", "collectable"],
            },
            {
                "column": 6,
                "row": 7,
                "hasContent": True,
                "blueprintID": "cow_4",
                "objectID": 32,
                "tier": 4,
                "producerKind": "animal",
                "producerState": "cooling",
                "behaviorNames": ["animal", "harvestable", "cooldownPreview"],
                "claimOutputCapacity": 7,
                "claimOutputIDs": ["milk", "upgrade_card_1"],
            },
        ],
    )

    state = read_board_state(9222, "Farm")

    assert state is not None
    assert state[(4, 5)].collectable
    assert state[(4, 5)].collectable_ingredient
    assert state[(4, 5)].item_variant == "wheat"
    assert state[(4, 5)].object_id == 31
    assert state[(6, 7)].producer_kind is ProducerKind.ANIMAL
    assert state[(6, 7)].tier == 4
    assert state[(6, 7)].producer_state is ProducerState.COOLING
    assert state[(6, 7)].claim_output_capacity == 7
    assert state[(6, 7)].claim_output_ids == frozenset({"milk", "upgrade_card_1"})
    assert "cooldownPreview" in state[(6, 7)].behavior_names


def test_read_board_state_preserves_upgrade_card_target_and_applied_tier(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.board_store.evaluate",
        lambda *_args, **_kwargs: [
            {
                "column": 8,
                "row": 9,
                "hasContent": True,
                "blueprintID": "upgrade_card_1",
                "objectID": 44,
                "tier": 1,
                "itemVariant": "soybeans",
                "upgradeAppliedTier": 0,
                "behaviorNames": ["upgradeCard", "mergeable"],
            }
        ],
    )

    state = read_board_state(9222, "Farm")

    assert state is not None
    assert state[(8, 9)].item_variant == "soybeans"
    assert state[(8, 9)].upgrade_applied_tier == 0


def test_board_reader_gets_upgrade_progress_from_authoritative_model() -> None:
    from farm_merge_valet.cdp.board_store import _READ_EXPRESSION

    assert "services?.upgradeCard?._model?.getItemTier" in _READ_EXPRESSION
    assert "upgradeAppliedTier" in _READ_EXPRESSION


def test_live_producer_state_uses_active_cooldown_not_preview() -> None:
    from farm_merge_valet.cdp.board_store import _READ_EXPRESSION

    assert "hasBehavior?.('cooldown')" in _READ_EXPRESSION
    assert "hasBehavior?.('cooldownPreview')" not in _READ_EXPRESSION


def test_read_board_state_preserves_obstacle_progress_and_cost(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.board_store.evaluate",
        lambda *_args, **_kwargs: [
            {
                "column": 6,
                "row": 7,
                "hasContent": True,
                "blueprintID": "rock_medium",
                "objectID": 32,
                "claimOutputCapacity": 4,
                "claimOutputIDs": ["stone_1"],
                "obstacle": {
                    "stagesRemaining": 4,
                    "totalStages": 5,
                    "energyCost": 10,
                    "requiredWorkers": 1,
                    "movable": False,
                    "clearing": True,
                },
            }
        ],
    )

    state = read_board_state(9222, "Farm")

    assert state is not None
    assert state[(6, 7)].obstacle == ObstacleState(4, 5, 10, False, True, 1)
    assert state[(6, 7)].claim_output_capacity == 4
    assert state[(6, 7)].claim_output_ids == frozenset({"stone_1"})


def test_fresh_obstacle_gate_is_not_hidden_by_previous_paid_marker() -> None:
    from farm_merge_valet.cdp.board_store import _READ_EXPRESSION

    assert "content.hasBehavior?.('resourceGatePaid') && !resourceGate" in _READ_EXPRESSION


def test_board_reader_derives_claim_capacity_from_live_rewards() -> None:
    from farm_merge_valet.cdp.board_store import _READ_EXPRESSION

    assert "rewardCapacity(harvestReward)" in _READ_EXPRESSION


def test_board_reader_derives_reward_container_capacity_and_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.board_store.evaluate",
        lambda *_args, **_kwargs: [
            {
                "column": 4,
                "row": 8,
                "hasContent": True,
                "blueprintID": "reward_crate_stickerbook",
                "objectID": 55,
                "claimOutputCapacity": 6,
                "claimOutputIDs": ["sticker_pack", "energy_1"],
                "rewardRequirements": [
                    {"blueprintID": "reward_crate_key_gold", "amount": 2}
                ],
                "rewardRequirementsMet": False,
                "behaviorNames": ["crateReward", "cooldown"],
            }
        ],
    )

    state = read_board_state(9222, "Farm")

    assert state is not None
    assert state[(4, 8)].claim_output_capacity == 6
    assert state[(4, 8)].claim_output_ids == frozenset({"sticker_pack", "energy_1"})
    assert state[(4, 8)].reward_requirements == (
        RewardRequirement("reward_crate_key_gold", 2),
    )
    assert state[(4, 8)].reward_requirements_met is False

    from farm_merge_valet.cdp.board_store import _READ_EXPRESSION

    assert "crateReward?._data?.rewards" in _READ_EXPRESSION
    assert "crateRewards.length" in _READ_EXPRESSION
    assert "obstacleLoot.length" in _READ_EXPRESSION
    assert "claimOutputIDs" in _READ_EXPRESSION
    assert "services.gridFilter.hasEnoughItems(rewardRequirements)" in _READ_EXPRESSION
    assert "rewardRequirementsMet" in _READ_EXPRESSION
