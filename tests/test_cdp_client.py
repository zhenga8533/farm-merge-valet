from __future__ import annotations

import pytest

from farm_merge_valet.cdp.client import CdpConnectionError, _evaluate_target, _select_target_pair


def _page(id_: str, title: str) -> dict[str, str]:
    return {
        "id": id_,
        "type": "page",
        "title": title,
        "url": "https://www.reddit.com/r/FarmMergeValley/comments/example",
        "webSocketDebuggerUrl": f"ws://page/{id_}",
    }


def _frame(id_: str, parent_id: str) -> dict[str, str]:
    return {
        "id": id_,
        "parentId": parent_id,
        "type": "iframe",
        "url": "https://playfmv-example.devvit.net/index.html",
        "webSocketDebuggerUrl": f"ws://frame/{id_}",
    }


def test_select_target_pair_uses_parent_page_and_title() -> None:
    targets = [
        _page("other", "Another post"),
        _frame("other-frame", "other"),
        _page("game", "Crates are waiting! : r/FarmMergeValley"),
        _frame("game-frame", "game"),
    ]

    assert _select_target_pair(targets, "r/FarmMergeValley") == (
        "ws://frame/game-frame",
        "ws://page/game",
    )


def test_select_target_pair_rejects_ambiguous_game_tabs() -> None:
    targets = [
        _page("one", "r/FarmMergeValley"),
        _frame("one-frame", "one"),
        _page("two", "r/FarmMergeValley"),
        _frame("two-frame", "two"),
    ]

    with pytest.raises(CdpConnectionError, match="Multiple matching"):
        _select_target_pair(targets, "r/FarmMergeValley")


def test_select_target_pair_rejects_orphaned_iframe() -> None:
    with pytest.raises(CdpConnectionError, match="no Farm Merge Valley iframe"):
        _select_target_pair([_frame("game-frame", "missing")], "r/FarmMergeValley")


def test_evaluate_target_wraps_websocket_failures(monkeypatch) -> None:
    async def fail(*_args):
        raise OSError("connection closed")

    monkeypatch.setattr("farm_merge_valet.cdp.client._evaluate_async", fail)

    with pytest.raises(CdpConnectionError, match="WebSocket"):
        _evaluate_target("ws://frame/game", "1 + 1")
