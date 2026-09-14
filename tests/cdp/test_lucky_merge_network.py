from __future__ import annotations

from threading import Event
from types import SimpleNamespace

import pytest

from farm_merge_valet.cdp import lucky_merge
from farm_merge_valet.cdp.transport import CdpConnectionError
from farm_merge_valet.integrations import GamePortal


def test_offline_probe_failure_restores_both_targets(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    network = lucky_merge.LuckyMergeNetwork(9222, None, "game", "page", "https://example.test")

    def command(ws_url, method, params=None):
        calls.append((ws_url, method, params))
        return {}

    monkeypatch.setattr(lucky_merge, "_command_target", command)
    monkeypatch.setattr(lucky_merge, "_evaluate_target", lambda *_args, **_kwargs: False)

    with pytest.raises(CdpConnectionError, match="not isolated"):
        network.set_offline()

    offline = [
        ws_url
        for ws_url, method, params in calls
        if method == "Network.emulateNetworkConditions" and params and params["offline"] is True
    ]
    restored = [
        ws_url
        for ws_url, method, params in calls
        if method == "Network.emulateNetworkConditions" and params and params["offline"] is False
    ]
    assert offline == ["page", "game"]
    assert restored == ["page", "game"]
    assert not network.offline


def test_game_socket_is_closed_only_after_request_isolation(monkeypatch) -> None:
    calls: list[str] = []
    network = lucky_merge.LuckyMergeNetwork(9222, None, "game", "page", "https://example.test")

    def command(_ws_url, method, _params=None):
        calls.append(method)
        return {}

    def evaluate(_ws_url, expression, **_kwargs):
        calls.append("backend-socket" if "socket.close()" in expression else "offline-probe")
        return True

    monkeypatch.setattr(lucky_merge, "_command_target", command)
    monkeypatch.setattr(lucky_merge, "_evaluate_target", evaluate)

    network.set_offline()

    assert calls.index("backend-socket") > calls.index("offline-probe")
    assert calls.index("backend-socket") > calls.index("Network.emulateNetworkConditions")
    assert network.offline


def test_discard_navigates_away_before_restoring_network(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    network = lucky_merge.LuckyMergeNetwork(9222, None, "game", "page", "https://example.test")
    network.offline = True

    def command(ws_url, method, params=None):
        calls.append((ws_url, method, params))
        return {}

    monkeypatch.setattr(lucky_merge, "_command_target", command)
    monkeypatch.setattr(lucky_merge, "_evaluate_target", lambda *_args: "about:blank")
    monkeypatch.setattr(lucky_merge, "_invalidate_target_pair", lambda *_args: None)
    monkeypatch.setattr(lucky_merge, "_load_targets", lambda _port: [])
    monkeypatch.setattr(
        lucky_merge,
        "discover_game_targets",
        lambda _targets, _title: [SimpleNamespace(page_ws_url="page")],
    )
    monkeypatch.setattr(lucky_merge.time, "sleep", lambda _seconds: None)

    network.discard_and_reload()

    assert calls[0] == ("page", "Page.navigate", {"url": "about:blank"})
    restore_index = next(
        index
        for index, (_target, method, params) in enumerate(calls)
        if method == "Network.emulateNetworkConditions" and params and params["offline"] is False
    )
    assert restore_index > 0
    assert calls[-1] == ("page", "Page.navigate", {"url": "https://example.test"})
    assert not network.offline


def test_reload_starts_portal_launcher_before_waiting_for_board(monkeypatch) -> None:
    network = lucky_merge.LuckyMergeNetwork(9222, None, "game", "page", "https://example.test")
    started = False
    calls: list[str] = []

    def start(_port, _title, _portal):
        nonlocal started
        started = True
        calls.append("start")
        return True

    monkeypatch.setattr(lucky_merge, "_command_target", lambda *_args: {})
    monkeypatch.setattr(lucky_merge, "_invalidate_target_pair", lambda *_args: None)
    monkeypatch.setattr(lucky_merge, "_load_targets", lambda _port: [])
    monkeypatch.setattr(lucky_merge, "request_game_start", start)
    monkeypatch.setattr(
        lucky_merge,
        "discover_game_targets",
        lambda _targets, _title: [SimpleNamespace(page_ws_url="page")] if started else [],
    )
    monkeypatch.setattr(lucky_merge.time, "sleep", lambda _seconds: None)

    network._open_game_page()

    assert calls == ["start"]


def test_direct_portal_reload_waits_for_game_without_play(monkeypatch) -> None:
    network = lucky_merge.LuckyMergeNetwork(
        9222, None, "game", "page", "https://example.test", GamePortal.CRAZY_GAMES
    )
    checks = 0

    def discover(_targets, _title):
        nonlocal checks
        checks += 1
        return [SimpleNamespace(page_ws_url="page")] if checks == 2 else []

    monkeypatch.setattr(lucky_merge, "_command_target", lambda *_args: {})
    monkeypatch.setattr(lucky_merge, "_invalidate_target_pair", lambda *_args: None)
    monkeypatch.setattr(lucky_merge, "_load_targets", lambda _port: [])
    monkeypatch.setattr(lucky_merge, "discover_game_targets", discover)
    monkeypatch.setattr(
        lucky_merge,
        "request_game_start",
        lambda *_args: pytest.fail("Direct portal must not request Play"),
    )
    monkeypatch.setattr(lucky_merge.time, "sleep", lambda _seconds: None)

    network._open_game_page()

    assert checks == 2


def test_reload_wait_can_be_cancelled(monkeypatch) -> None:
    cancelled = Event()
    network = lucky_merge.LuckyMergeNetwork(
        9222, None, "game", "page", "https://example.test", cancel_event=cancelled
    )
    monkeypatch.setattr(lucky_merge, "_command_target", lambda *_args: {})
    monkeypatch.setattr(lucky_merge, "_invalidate_target_pair", lambda *_args: None)
    monkeypatch.setattr(lucky_merge, "_load_targets", lambda _port: [])
    monkeypatch.setattr(lucky_merge, "discover_game_targets", lambda *_args: [])
    monkeypatch.setattr(
        lucky_merge,
        "request_game_start",
        lambda *_args: (cancelled.set(), False)[1],
    )

    with pytest.raises(CdpConnectionError, match="did not reopen"):
        network._open_game_page()


def test_reload_fails_if_game_iframe_never_reappears(monkeypatch) -> None:
    network = lucky_merge.LuckyMergeNetwork(
        9222, None, "game", "page", "https://example.test", GamePortal.CRAZY_GAMES
    )
    ticks = iter((0.0, 46.0))
    monkeypatch.setattr(lucky_merge, "_command_target", lambda *_args: {})
    monkeypatch.setattr(lucky_merge, "_invalidate_target_pair", lambda *_args: None)
    monkeypatch.setattr(lucky_merge.time, "monotonic", lambda: next(ticks))

    with pytest.raises(CdpConnectionError, match="did not reopen"):
        network._open_game_page()
