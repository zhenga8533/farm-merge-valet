from __future__ import annotations

from farm_merge_valet.cdp.board_store import (
    LiveCellState,
    _arm_board_store_target,
    read_board_state,
)
from farm_merge_valet.core.board import ProducerKind, ProducerState


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


def test_read_board_state_preserves_claim_semantics(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.board_store.evaluate",
        lambda *_args, **_kwargs: [
            {
                "column": 4,
                "row": 5,
                "hasContent": True,
                "blueprintID": "milk",
                "objectID": 31,
                "collectableIngredient": True,
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
            },
        ],
    )

    state = read_board_state(9222, "Farm")

    assert state is not None
    assert state[(4, 5)].collectable_ingredient
    assert state[(4, 5)].object_id == 31
    assert state[(6, 7)].producer_kind is ProducerKind.ANIMAL
    assert state[(6, 7)].tier == 4
    assert state[(6, 7)].producer_state is ProducerState.COOLING
    assert "cooldownPreview" in state[(6, 7)].behavior_names


def test_live_producer_state_uses_active_cooldown_not_preview() -> None:
    from farm_merge_valet.cdp.board_store import _READ_EXPRESSION

    assert "hasBehavior?.('cooldown')" in _READ_EXPRESSION
    assert "hasBehavior?.('cooldownPreview')" not in _READ_EXPRESSION
