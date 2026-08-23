from __future__ import annotations

from farm_merge_valet.capture.window import WindowRegion
from farm_merge_valet.cdp.reddit_host import RedditFullscreenState
from farm_merge_valet.core.environment import ensure_reddit_fullscreen, pan


def test_ensure_reddit_fullscreen_enters_f11_and_expands_game(monkeypatch) -> None:
    region = WindowRegion(0, 0, 1600, 900)
    states = iter(
        [
            RedditFullscreenState(False, False),
            RedditFullscreenState(True, True),
        ]
    )
    pressed: list[str] = []
    expanded: list[bool] = []

    monkeypatch.setattr(
        "farm_merge_valet.core.environment.read_fullscreen_state", lambda *_args: next(states)
    )
    monkeypatch.setattr(
        "farm_merge_valet.core.environment.expand_game",
        lambda *_args: expanded.append(True) or "expanded",
    )
    monkeypatch.setattr("farm_merge_valet.core.environment.press", pressed.append)
    monkeypatch.setattr("farm_merge_valet.core.environment.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("farm_merge_valet.core.environment.find_window", lambda _title: region)

    assert ensure_reddit_fullscreen() == region
    assert pressed == ["f11"]
    assert expanded == [True]


def test_pan_uses_dedicated_resolution_independent_lane(monkeypatch) -> None:
    region = WindowRegion(0, 0, 1920, 1080)
    drags = []
    monkeypatch.setattr(
        "farm_merge_valet.core.environment.drag",
        lambda _region, start, end, duration: drags.append((start, end, duration)),
    )
    monkeypatch.setattr("farm_merge_valet.core.environment.time.sleep", lambda _seconds: None)

    pan(region, toward_bottom=True)

    assert drags == [((1776, 238), (1776, 842), 0.15)]
