from __future__ import annotations

from farm_merge_valet.cdp.board_store import LiveCellState, read_board_state


def test_read_board_state_preserves_cells_without_content(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.board_store.evaluate",
        lambda *_args: [
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
