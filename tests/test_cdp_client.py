from __future__ import annotations

import base64
import json
from threading import Event

import pytest

from farm_merge_valet.cdp.client import (
    CdpCancelledError,
    CdpConnectionError,
    CdpTimeoutError,
    _CdpSession,
    _evaluate_target,
    _invalidate_target_pair,
    _normalize_local_ws_url,
    _select_target_pair,
    capture_game_frame,
    evaluate,
    find_game_frame_target,
    find_top_page_target,
    read_background_flag_status,
    read_browser_metadata,
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


def test_select_target_pair_accepts_page_url_filter_and_ignores_helper_frames() -> None:
    page = _page("game", "Farm Merge Valley")
    targets = [
        page,
        {
            **_frame("launcher", "game"),
            "url": "https://playfmv-example.devvit.net/launcher/launcher.html",
        },
        {
            **_frame("screenshot", "game"),
            "url": "https://playfmv-example.devvit.net/screenshot/screenshot.html",
        },
        _frame("game-frame", "game"),
    ]

    assert _select_target_pair(targets, "r/FarmMergeValley") == (
        "ws://frame/game-frame",
        "ws://page/game",
    )


def test_local_websocket_urls_use_numeric_loopback() -> None:
    assert (
        _normalize_local_ws_url("ws://localhost:9222/devtools/page/abc")
        == "ws://127.0.0.1:9222/devtools/page/abc"
    )
    assert _normalize_local_ws_url("ws://remote:9222/devtools/page/abc").startswith("ws://remote:")


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
    def fail(*_args, **_kwargs):
        raise OSError("connection closed")

    monkeypatch.setattr("farm_merge_valet.cdp.client._command_target", fail)

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

    def evaluate_target(ws_url: str, _expression: str, **_kwargs):
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


def test_capture_game_frame_decodes_cdp_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.client.evaluate_top_page",
        lambda *_: {"x": 10, "y": 20, "width": 300, "height": 200},
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.client._run_top_page_operation",
        lambda _port, _title, operation: {"data": base64.b64encode(b"png").decode()},
    )

    assert capture_game_frame(9222, "Farm") == b"png"


def test_background_flag_status_reports_missing_switches(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.client._load_json_endpoint",
        lambda *_args, **_kwargs: {"webSocketDebuggerUrl": "ws://browser"},
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.client._command_target",
        lambda *_, **__: {
            "arguments": [
                "chrome.exe",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
            ]
        },
    )

    status = read_background_flag_status(9222)

    assert status["available"] is True
    assert status["all_present"] is False
    assert status["missing"] == ["--disable-backgrounding-occluded-windows"]


def test_browser_metadata_uses_version_page_without_enable_automation(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.client._load_json_endpoint",
        lambda *_args, **_kwargs: {
            "Browser": "Chrome/151",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/id",
        },
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.client._command_target",
        lambda *_, **__: (_ for _ in ()).throw(CdpConnectionError("not enabled")),
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.client._version_page_metadata",
        lambda _ws: {
            "Command Line": '"C:\\Chrome\\chrome.exe" --remote-debugging-port=9222',
            "Executable Path": "C:\\Chrome\\chrome.exe",
            "Profile Path": "C:\\Profile\\Default",
        },
    )

    metadata = read_browser_metadata(9222)

    assert metadata["browser"] == "Chrome/151"
    assert metadata["executable_path"] == "C:\\Chrome\\chrome.exe"
    assert metadata["profile_path"] == "C:\\Profile\\Default"


def test_cdp_session_reuses_one_connection(monkeypatch) -> None:
    connections = []

    class Connection:
        def __init__(self):
            self.requests = []

        def send(self, payload):
            self.requests.append(json.loads(payload))

        def recv(self, timeout):
            request = self.requests[-1]
            return json.dumps({"id": request["id"], "result": {"value": request["id"]}})

        def close(self):
            return None

    def fake_connect(*_args, **_kwargs):
        connection = Connection()
        connections.append(connection)
        return connection

    monkeypatch.setattr("farm_merge_valet.cdp.client.connect", fake_connect)
    session = _CdpSession("ws://local")

    assert session.command("One") == {"value": 1}
    assert session.command("Two") == {"value": 2}
    assert len(connections) == 1


def test_cdp_session_honors_cancellation_before_connect(monkeypatch) -> None:
    cancel = Event()
    cancel.set()
    monkeypatch.setattr(
        "farm_merge_valet.cdp.client.connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not connect")),
    )

    with pytest.raises(CdpCancelledError):
        _CdpSession("ws://local").command("Runtime.evaluate", cancel_event=cancel)


def test_cdp_session_times_out_and_closes_connection(monkeypatch) -> None:
    closed = False

    class Connection:
        def send(self, _payload):
            return None

        def recv(self, timeout):
            raise TimeoutError

        def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr(
        "farm_merge_valet.cdp.client.connect", lambda *_args, **_kwargs: Connection()
    )

    with pytest.raises(CdpTimeoutError):
        _CdpSession("ws://local").command("Runtime.evaluate", timeout=0.01)

    assert closed
