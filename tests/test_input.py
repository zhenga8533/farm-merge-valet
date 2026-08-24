from __future__ import annotations

from farm_merge_valet.actions.input import drag
from farm_merge_valet.capture.window import WindowRegion


def test_drag_holds_at_pickup_and_destination(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        "farm_merge_valet.actions.input.pyautogui.moveTo",
        lambda x, y, duration=None: events.append(("move", x, y, duration)),
    )
    monkeypatch.setattr(
        "farm_merge_valet.actions.input.pyautogui.mouseDown",
        lambda **kwargs: events.append(("down", kwargs)),
    )
    monkeypatch.setattr(
        "farm_merge_valet.actions.input.pyautogui.mouseUp",
        lambda **kwargs: events.append(("up", kwargs)),
    )
    monkeypatch.setattr(
        "farm_merge_valet.actions.input.time.sleep",
        lambda seconds: events.append(("sleep", seconds)),
    )

    drag(
        WindowRegion(100, 200, 800, 600),
        (10, 20),
        (30, 40),
        duration=0.8,
        pickup_delay=0.2,
        release_delay=0.3,
    )

    assert events == [
        ("move", 110, 220, None),
        ("down", {"button": "left"}),
        ("sleep", 0.2),
        ("move", 130, 240, 0.8),
        ("sleep", 0.3),
        ("up", {"button": "left"}),
    ]


def test_drag_releases_mouse_if_movement_fails(monkeypatch) -> None:
    releases = []
    moves = iter([None, RuntimeError("movement failed")])

    def move(*_args, **_kwargs) -> None:
        result = next(moves)
        if isinstance(result, Exception):
            raise result

    monkeypatch.setattr("farm_merge_valet.actions.input.pyautogui.moveTo", move)
    monkeypatch.setattr(
        "farm_merge_valet.actions.input.pyautogui.mouseDown", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        "farm_merge_valet.actions.input.pyautogui.mouseUp",
        lambda **_kwargs: releases.append(True),
    )

    try:
        drag(WindowRegion(0, 0, 100, 100), (10, 10), (20, 20))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected movement error")

    assert releases == [True]


def test_decelerating_drag_uses_zero_velocity_endpoint_curve(monkeypatch) -> None:
    movements = []
    monkeypatch.setattr(
        "farm_merge_valet.actions.input.pyautogui.moveTo",
        lambda x, y, **kwargs: movements.append((x, y, kwargs)),
    )
    monkeypatch.setattr(
        "farm_merge_valet.actions.input.pyautogui.mouseDown", lambda **_kwargs: None
    )
    monkeypatch.setattr("farm_merge_valet.actions.input.pyautogui.mouseUp", lambda **_kwargs: None)

    drag(
        WindowRegion(0, 0, 100, 100),
        (10, 10),
        (20, 20),
        duration=0.6,
        decelerate=True,
    )

    tween = movements[1][2]["tween"]
    assert tween(0.0) == 0.0
    assert tween(0.5) == 0.5
    assert tween(1.0) == 1.0
    assert tween(1.0) - tween(0.9) < tween(0.6) - tween(0.5)
