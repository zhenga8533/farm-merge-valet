from __future__ import annotations

from farm_merge_valet.cdp.reddit_host import (
    RedditFullscreenState,
    expand_game,
    read_fullscreen_state,
)


def test_read_fullscreen_state_parses_browser_and_game_state(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.reddit_host.evaluate_top_page",
        lambda *_args: {"browserFullscreen": True, "gameExpanded": False},
    )

    assert read_fullscreen_state(9222, "Farm") == RedditFullscreenState(True, False)


def test_expand_game_returns_host_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.reddit_host.evaluate_top_page",
        lambda *_args: "already-expanded",
    )

    assert expand_game(9222, "Farm") == "already-expanded"
