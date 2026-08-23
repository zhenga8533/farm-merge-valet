from __future__ import annotations

import pytest

from farm_merge_valet.cdp.client import (
    CdpConnectionError,
    _evaluate_target,
    _invalidate_target_pair,
    _select_target_pair,
    evaluate,
    find_game_frame_target,
    find_top_page_target,
)


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


def test_target_pair_is_shared_by_game_and_page_operations(monkeypatch) -> None:
    port = 9333
    title = "Cached game"
    loads = 0
    targets = [_page("game", title), _frame("game-frame", "game")]

    def load_targets(_port: int):
        nonlocal loads
        loads += 1
        return targets

    _invalidate_target_pair(port, title)
    monkeypatch.setattr("farm_merge_valet.cdp.client._load_targets", load_targets)

    assert find_game_frame_target(port, title) == "ws://frame/game-frame"
    assert find_top_page_target(port, title) == "ws://page/game"
    assert loads == 1

    _invalidate_target_pair(port, title)


def test_evaluate_refreshes_stale_target_once(monkeypatch) -> None:
    port = 9444
    title = "Reloaded game"
    loads = 0
    used_targets = []

    def load_targets(_port: int):
        nonlocal loads
        loads += 1
        suffix = "old" if loads == 1 else "new"
        return [_page(f"game-{suffix}", title), _frame(f"frame-{suffix}", f"game-{suffix}")]

    def evaluate_target(ws_url: str, _expression: str):
        used_targets.append(ws_url)
        if ws_url.endswith("old"):
            raise CdpConnectionError("stale iframe")
        return 42

    _invalidate_target_pair(port, title)
    monkeypatch.setattr("farm_merge_valet.cdp.client._load_targets", load_targets)
    monkeypatch.setattr("farm_merge_valet.cdp.client._evaluate_target", evaluate_target)

    assert evaluate(port, "6 * 7", title) == 42
    assert used_targets == ["ws://frame/frame-old", "ws://frame/frame-new"]
    assert loads == 2

    _invalidate_target_pair(port, title)
