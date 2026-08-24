from __future__ import annotations

from farm_merge_valet.cdp.board_store import (
    LiveCellState,
    _arm_board_store_target,
    read_board_state,
)


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
