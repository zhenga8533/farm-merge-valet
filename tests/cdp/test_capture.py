from __future__ import annotations

import base64

import pytest

from farm_merge_valet.cdp.capture import capture_game_screenshot
from farm_merge_valet.cdp.transport import CdpConnectionError


def _targets() -> list[dict[str, str]]:
    return [
        {
            "id": "page",
            "type": "page",
            "title": "Farm Merge Valley",
            "url": "https://www.reddit.com/r/FarmMergeValley/",
            "webSocketDebuggerUrl": "ws://page",
        },
        {
            "id": "game",
            "parentId": "page",
            "type": "iframe",
            "url": "https://playfmv-test.devvit.net/index.html",
            "webSocketDebuggerUrl": "ws://game",
        },
    ]


def test_capture_game_screenshot_uses_verified_game_target(monkeypatch) -> None:
    jpeg = b"\xff\xd8\xffexample"
    commands: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setattr("farm_merge_valet.cdp.capture._load_targets", lambda _port: _targets())

    def command(ws_url, method, params, **_kwargs):
        commands.append((ws_url, method, params))
        if method == "DOM.getFrameOwner":
            return {"backendNodeId": 42}
        if method == "DOM.getBoxModel":
            return {"model": {"content": [10, 20, 810, 20, 810, 620, 10, 620]}}
        if method == "Page.getLayoutMetrics":
            return {"cssVisualViewport": {"pageX": 4, "pageY": 300}}
        return {"data": base64.b64encode(jpeg).decode()}

    monkeypatch.setattr("farm_merge_valet.cdp.capture._command_target", command)

    assert capture_game_screenshot(9222) == jpeg
    assert commands == [
        (
            "ws://page",
            "DOM.getFrameOwner",
            {"frameId": "game"},
        ),
        (
            "ws://page",
            "DOM.getBoxModel",
            {"backendNodeId": 42},
        ),
        (
            "ws://page",
            "Page.getLayoutMetrics",
            {},
        ),
        (
            "ws://page",
            "Page.captureScreenshot",
            {
                "format": "jpeg",
                "quality": 75,
                "fromSurface": True,
                "captureBeyondViewport": False,
                "clip": {
                    "x": 14,
                    "y": 320,
                    "width": 800,
                    "height": 600,
                    "scale": 1,
                },
            },
        )
    ]


def test_capture_game_screenshot_requires_one_verified_target(monkeypatch) -> None:
    monkeypatch.setattr("farm_merge_valet.cdp.capture._load_targets", lambda _port: [])

    with pytest.raises(CdpConnectionError, match="exactly one"):
        capture_game_screenshot(9222)


def test_capture_game_screenshot_rejects_missing_css_viewport(monkeypatch) -> None:
    monkeypatch.setattr("farm_merge_valet.cdp.capture._load_targets", lambda _port: _targets())

    def command(_ws_url, method, _params, **_kwargs):
        if method == "DOM.getFrameOwner":
            return {"backendNodeId": 42}
        if method == "DOM.getBoxModel":
            return {"model": {"content": [0, 0, 800, 0, 800, 600, 0, 600]}}
        return {"visualViewport": {"pageX": 0, "pageY": 0}}

    monkeypatch.setattr("farm_merge_valet.cdp.capture._command_target", command)

    with pytest.raises(CdpConnectionError, match="viewport metrics"):
        capture_game_screenshot(9222)
