from __future__ import annotations

import json
from dataclasses import replace
from threading import Event

import pytest

from farm_merge_valet.cdp.evaluation import _evaluate_target, evaluate
from farm_merge_valet.cdp.portals import (
    PORTALS,
    GamePortal,
    PortalSupportLevel,
    portal_definition,
    portal_for_page_url,
)
from farm_merge_valet.cdp.targets import (
    _invalidate_target_pair,
    _normalize_local_ws_url,
    _select_target_pair,
    _version_page_metadata,
    discover_game_targets,
    dismiss_pogo_inactivity_prompt,
    find_game_frame_target,
    find_top_page_target,
    read_background_flag_status,
    read_browser_metadata,
    try_start_game,
)
from farm_merge_valet.cdp.transport import (
    CdpCancelledError,
    CdpConnectionError,
    CdpTimeoutError,
    _CdpSession,
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


def _crazy_page(id_: str = "crazy-page") -> dict[str, str]:
    return {
        "id": id_,
        "type": "page",
        "title": "Farm Merge Valley - CrazyGames",
        "url": "https://www.crazygames.com/game/farm-merge-valley",
        "webSocketDebuggerUrl": f"ws://page/{id_}",
    }


def _crazy_game_frame(id_: str, parent_id: str) -> dict[str, str]:
    return {
        "id": id_,
        "parentId": parent_id,
        "type": "iframe",
        "url": (
            "https://farm-merge-valley.game-files.crazygames.com/"
            "farm-merge-valley/175/index.html?build=175"
        ),
        "webSocketDebuggerUrl": f"ws://frame/{id_}",
    }


def _pogo_page(id_: str = "pogo-page") -> dict[str, str]:
    return {
        "id": id_,
        "type": "page",
        "title": "Play Farm Merge Valley — A Free Merge-3 Game, No Download | Pogo",
        "url": "https://www.pogo.com/games/farm-merge-valley/play",
        "webSocketDebuggerUrl": f"ws://page/{id_}",
    }


def _pogo_game_frame(id_: str, parent_id: str) -> dict[str, str]:
    return {
        "id": id_,
        "parentId": parent_id,
        "type": "iframe",
        "url": "https://cdn-h5farmvalley-prod.pogospike.com/20/index.html",
        "webSocketDebuggerUrl": f"ws://frame/{id_}",
    }


def _msn_page(id_: str = "msn-page") -> dict[str, str]:
    return {
        "id": id_,
        "type": "page",
        "title": "Play Farm Merge Valley in your browser | Games from MSN",
        "url": "https://www.msn.com/en-nz/play/games/farm-merge-valley/cg-9nf2hg8fnlts",
        "webSocketDebuggerUrl": f"ws://page/{id_}",
    }


def _msn_game_frame(id_: str, parent_id: str) -> dict[str, str]:
    return {
        "id": id_,
        "parentId": parent_id,
        "type": "iframe",
        "url": "https://cdn.games.mobinozer.com/ext/farm_merge_microsoft/prod/1.80.0-4/",
        "webSocketDebuggerUrl": f"ws://frame/{id_}",
    }


def _discord_page(id_: str = "discord-page") -> dict[str, str]:
    return {
        "id": id_,
        "type": "page",
        "title": "Discord | @Farm Merge Valley",
        "url": "https://discord.com/channels/@me/1378504101163434044",
        "webSocketDebuggerUrl": f"ws://page/{id_}",
    }


def _discord_game_frame(id_: str, parent_id: str) -> dict[str, str]:
    return {
        "id": id_,
        "parentId": parent_id,
        "type": "iframe",
        "url": "https://1187013846746005515.discordsays.com/?instance_id=ephemeral",
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


def test_select_target_pair_ignores_title_match_without_game_frame() -> None:
    game_page = {
        **_page("game", "Farm Merge Valley"),
        "url": "https://www.reddit.com/r/FarmMergeValley/",
    }
    unrelated_page = {
        **_page("post", "Visit a farm! : r/FarmMergeValley"),
        "url": "https://www.reddit.com/user/example/comments/post",
    }
    targets = [
        game_page,
        _frame("game-frame", "game"),
        unrelated_page,
        {
            **_frame("screenshot", "post"),
            "url": "https://playfmv-example.devvit.net/screenshot/screenshot.html",
        },
    ]

    assert _select_target_pair(targets, "r/FarmMergeValley") == (
        "ws://frame/game-frame",
        "ws://page/game",
    )


def test_try_start_game_clicks_launcher_for_matching_page(monkeypatch) -> None:
    page = _page("game", "Crates are waiting! : r/FarmMergeValley")
    launcher = {
        **_frame("launcher", "game"),
        "url": "https://playfmv-example.devvit.net/launcher/launcher.html",
    }
    monkeypatch.setattr(
        "farm_merge_valet.cdp.targets._load_targets",
        lambda _port: [page, launcher],
    )
    commands: list[tuple[str, str, dict[str, object] | None]] = []

    def command(ws_url, method, params=None, **_kwargs):
        commands.append((ws_url, method, params))
        if method != "Runtime.evaluate":
            return {}
        if ws_url == "ws://frame/launcher":
            return {"result": {"value": {"xRatio": 0.5, "yRatio": 0.8}}}
        if len([command for command in commands if command[1] == "Runtime.evaluate"]) == 2:
            return {"result": {"value": True}}
        return {"result": {"value": {"x": 500, "y": 400}}}

    monkeypatch.setattr("farm_merge_valet.cdp.targets._command_target", command)

    assert try_start_game(9222, "r/FarmMergeValley")
    assert commands[0][1] == "Runtime.evaluate"
    assert "#start-button" in str(commands[0][2])
    assert [method for _url, method, _params in commands[1:]] == [
        "Runtime.evaluate",
        "Runtime.evaluate",
        "Input.dispatchMouseEvent",
        "Input.dispatchMouseEvent",
        "Input.dispatchMouseEvent",
    ]
    assert commands[-2][2] == {
        "type": "mousePressed",
        "x": 500,
        "y": 400,
        "button": "left",
        "buttons": 1,
        "clickCount": 1,
    }


def test_dismiss_pogo_inactivity_prompt_uses_exact_sdk_dialog(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.targets._load_targets",
        lambda _port: [_pogo_page(), _pogo_game_frame("game-frame", "pogo-page")],
    )
    commands: list[tuple[str, str, dict[str, object] | None]] = []

    def command(ws_url, method, params=None, **_kwargs):
        commands.append((ws_url, method, params))
        if method == "Page.getFrameTree":
            return {
                "frameTree": {
                    "frame": {"id": "pogo-page"},
                    "childFrames": [{"frame": {"id": "sdk-frame", "name": "gameBrick"}}],
                }
            }
        if method == "Page.createIsolatedWorld":
            return {"executionContextId": 17}
        if method == "Runtime.evaluate" and params and params.get("contextId") == 17:
            return {"result": {"value": True}}
        return {}

    monkeypatch.setattr("farm_merge_valet.cdp.targets._command_target", command)

    assert dismiss_pogo_inactivity_prompt(9222, "Pogo")
    assert commands[-1][1] == "Runtime.evaluate"
    assert commands[-1][2]["contextId"] == 17
    assert "Still Playing" in str(commands[-1][2]["expression"])
    assert "button.click()" in str(commands[-1][2]["expression"])


def test_version_page_metadata_uses_hidden_target(monkeypatch) -> None:
    commands: list[tuple[str, str, dict[str, object] | None]] = []

    def command(ws_url, method, params=None, **_kwargs):
        commands.append((ws_url, method, params))
        if method == "Target.createTarget":
            return {"targetId": "version-target"}
        if method == "Runtime.evaluate":
            return {
                "result": {
                    "value": "Command Line\tchrome.exe --remote-debugging-port=9222\n"
                    "Executable Path\tC:\\Chrome\\chrome.exe\n"
                    "Profile Path\tC:\\Profile\\Default"
                }
            }
        return {}

    monkeypatch.setattr("farm_merge_valet.cdp.targets._command_target", command)
    monkeypatch.setattr("farm_merge_valet.cdp.targets.time.sleep", lambda _seconds: None)

    metadata = _version_page_metadata("ws://127.0.0.1:9222/devtools/browser/id")

    assert metadata["Executable Path"] == "C:\\Chrome\\chrome.exe"
    assert commands[0][2] == {
        "url": "chrome://version/",
        "background": True,
        "hidden": True,
    }
    assert commands[1][0] == "ws://127.0.0.1:9222/devtools/page/version-target"


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


def test_discover_game_targets_resolves_nested_crazygames_frame() -> None:
    wrapper = {
        "id": "wrapper",
        "parentId": "crazy-page",
        "type": "iframe",
        "url": "https://games.crazygames.com/en_US/farm-merge-valley/index.html",
        "webSocketDebuggerUrl": "ws://frame/wrapper",
    }

    discovered = discover_game_targets(
        [_crazy_page(), wrapper, _crazy_game_frame("game-frame", "wrapper")]
    )

    assert len(discovered) == 1
    target = discovered[0]
    assert target.portal is GamePortal.CRAZY_GAMES
    assert target.ancestor_ids == ("wrapper", "crazy-page")
    assert target.page_target_id == "crazy-page"
    assert target.game_target_id == "game-frame"
    assert target.support_level is PortalSupportLevel.AUTOMATION


def test_discover_game_targets_recognizes_pogo_as_automation_enabled() -> None:
    discovered = discover_game_targets(
        [_pogo_page(), _pogo_game_frame("game-frame", "pogo-page")], "Pogo"
    )

    assert len(discovered) == 1
    assert discovered[0].portal is GamePortal.POGO
    assert discovered[0].support_level is PortalSupportLevel.AUTOMATION
    assert _select_target_pair(
        [_pogo_page(), _pogo_game_frame("game-frame", "pogo-page")], "Pogo"
    ) == ("ws://frame/game-frame", "ws://page/pogo-page")


def test_discover_game_targets_resolves_nested_msn_frame() -> None:
    wrapper = {
        "id": "wrapper",
        "parentId": "msn-page",
        "type": "iframe",
        "url": "https://www.msn.com/game-wrapper",
        "webSocketDebuggerUrl": "ws://frame/wrapper",
    }

    discovered = discover_game_targets(
        [_msn_page(), wrapper, _msn_game_frame("game-frame", "wrapper")]
    )

    assert len(discovered) == 1
    assert discovered[0].portal is GamePortal.MSN
    assert discovered[0].ancestor_ids == ("wrapper", "msn-page")
    assert discovered[0].support_level is PortalSupportLevel.AUTOMATION
    assert _select_target_pair([_msn_page(), _msn_game_frame("game-frame", "msn-page")], "MSN") == (
        "ws://frame/game-frame",
        "ws://page/msn-page",
    )


def test_msn_recognition_rejects_spoofed_hosts_and_unrelated_paths() -> None:
    portal = portal_definition(GamePortal.MSN)
    spoofed_page = {
        **_msn_page(),
        "url": "https://www.msn.com.example.test/en-nz/play/games/farm-merge-valley",
    }

    assert not discover_game_targets([spoofed_page, _msn_game_frame("game-frame", "msn-page")])
    assert not portal.matches_game_frame_url(
        "https://cdn.games.mobinozer.com.example.test/ext/farm_merge_microsoft/prod/1/"
    )
    assert not portal.matches_game_frame_url(
        "https://cdn.games.mobinozer.com/ext/unrelated/prod/1/"
    )


def test_discover_game_targets_recognizes_discord_activity() -> None:
    discovered = discover_game_targets(
        [_discord_page(), _discord_game_frame("game-frame", "discord-page")]
    )
    assert len(discovered) == 1
    assert discovered[0].portal is GamePortal.DISCORD
    assert discovered[0].ancestor_ids == ("discord-page",)
    assert discovered[0].support_level is PortalSupportLevel.AUTOMATION
    assert _select_target_pair(
        [_discord_page(), _discord_game_frame("game-frame", "discord-page")], "Discord"
    ) == ("ws://frame/game-frame", "ws://page/discord-page")


def test_discord_recognition_rejects_spoofed_hosts_and_paths() -> None:
    portal = portal_definition(GamePortal.DISCORD)
    page = {**_discord_page(), "url": "https://discord.com.example.test/channels/@me/123"}
    assert not discover_game_targets([page, _discord_game_frame("game-frame", "discord-page")])
    assert not portal.matches_game_frame_url("https://999.discordsays.com/")
    assert not portal.matches_game_frame_url(
        "https://1187013846746005515.discordsays.com/unrelated"
    )


def test_select_target_pair_rejects_observation_only_portal(monkeypatch) -> None:
    observation_portal = replace(
        portal_definition(GamePortal.CRAZY_GAMES),
        support_level=PortalSupportLevel.OBSERVATION,
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.targets.PORTALS",
        tuple(portal for portal in PORTALS if portal.kind is not GamePortal.CRAZY_GAMES)
        + (observation_portal,),
    )
    targets = [_crazy_page(), _crazy_game_frame("game-frame", "crazy-page")]

    with pytest.raises(CdpConnectionError, match="observation-only.*crazygames"):
        _select_target_pair(targets, "CrazyGames")


def test_select_target_pair_allows_observation_only_portal_explicitly(monkeypatch) -> None:
    observation_portal = replace(
        portal_definition(GamePortal.CRAZY_GAMES),
        support_level=PortalSupportLevel.OBSERVATION,
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.targets.PORTALS",
        tuple(portal for portal in PORTALS if portal.kind is not GamePortal.CRAZY_GAMES)
        + (observation_portal,),
    )
    targets = [_crazy_page(), _crazy_game_frame("game-frame", "crazy-page")]

    assert _select_target_pair(targets, "CrazyGames", allow_observation=True) == (
        "ws://frame/game-frame",
        "ws://page/crazy-page",
    )


def test_discover_game_targets_rejects_spoofed_portal_host() -> None:
    page = {
        **_crazy_page(),
        "url": "https://www.crazygames.com.example.test/game/farm-merge-valley",
    }

    assert not discover_game_targets([page, _crazy_game_frame("game-frame", "crazy-page")])


def test_reddit_game_frame_matcher_rejects_non_devvit_host() -> None:
    portal = portal_definition(GamePortal.REDDIT)

    assert portal.matches_game_frame_url("https://playfmv-example.devvit.net/index.html")
    assert not portal.matches_game_frame_url("https://playfmv-attacker.example/index.html")


def test_portal_canonical_urls_resolve_to_their_definitions() -> None:
    assert all(portal_for_page_url(portal.canonical_url) is portal for portal in PORTALS)


def test_evaluate_target_wraps_websocket_failures(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise OSError("connection closed")

    monkeypatch.setattr("farm_merge_valet.cdp.evaluation._command_target", fail)

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
    monkeypatch.setattr("farm_merge_valet.cdp.targets._load_targets", load_targets)

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
    monkeypatch.setattr("farm_merge_valet.cdp.targets._load_targets", load_targets)
    monkeypatch.setattr("farm_merge_valet.cdp.evaluation._evaluate_target", evaluate_target)

    assert evaluate(port, "6 * 7", title) == 42
    assert used_targets == ["ws://frame/frame-old", "ws://frame/frame-new"]
    assert loads == 2

    _invalidate_target_pair(port, title)


def test_background_flag_status_reports_missing_switches(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.targets._load_json_endpoint",
        lambda *_args, **_kwargs: {"webSocketDebuggerUrl": "ws://browser"},
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.targets._command_target",
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
        "farm_merge_valet.cdp.targets._load_json_endpoint",
        lambda *_args, **_kwargs: {
            "Browser": "Chrome/151",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/id",
        },
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.targets._command_target",
        lambda *_, **__: (_ for _ in ()).throw(CdpConnectionError("not enabled")),
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.targets._version_page_metadata",
        lambda _ws, **_kwargs: {
            "Command Line": '"C:\\Chrome\\chrome.exe" --remote-debugging-port=9222',
            "Executable Path": "C:\\Chrome\\chrome.exe",
            "Profile Path": "C:\\Profile\\Default",
        },
    )

    metadata = read_browser_metadata(9222)

    assert metadata["browser"] == "Chrome/151"
    assert metadata["executable_path"] == "C:\\Chrome\\chrome.exe"
    assert metadata["profile_path"] == "C:\\Profile\\Default"


def test_version_page_metadata_waits_for_page_content(monkeypatch) -> None:
    evaluations = iter(("", "Command Line\tchrome.exe --remote-debugging-port=9222"))
    closed_targets: list[str] = []

    def command(_ws_url, method, params=None, **_kwargs):
        if method == "Target.createTarget":
            return {"targetId": "version-page"}
        if method == "Runtime.evaluate":
            return {"result": {"value": next(evaluations)}}
        if method == "Target.closeTarget":
            closed_targets.append(params["targetId"])
        return {}

    monkeypatch.setattr("farm_merge_valet.cdp.targets._command_target", command)
    monkeypatch.setattr("farm_merge_valet.cdp.targets.time.sleep", lambda _seconds: None)

    metadata = _version_page_metadata("ws://127.0.0.1:9222/devtools/browser/id")

    assert metadata["Command Line"] == "chrome.exe --remote-debugging-port=9222"
    assert closed_targets == ["version-page"]


def test_version_page_metadata_tries_next_browser_url(monkeypatch) -> None:
    created_urls: list[str] = []

    def command(_ws_url, method, params=None, **_kwargs):
        if method == "Target.createTarget":
            created_urls.append(params["url"])
            if params["url"].startswith("chrome:"):
                raise CdpConnectionError("unsupported URL")
            return {"targetId": "version-page"}
        if method == "Runtime.evaluate":
            return {"result": {"value": "Executable Path\tC:\\Edge\\msedge.exe"}}
        return {}

    monkeypatch.setattr("farm_merge_valet.cdp.targets._command_target", command)

    metadata = _version_page_metadata("ws://127.0.0.1:9222/devtools/browser/id")

    assert metadata["Executable Path"] == "C:\\Edge\\msedge.exe"
    assert created_urls == ["chrome://version/", "edge://version/"]


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

    monkeypatch.setattr("farm_merge_valet.cdp.transport.connect", fake_connect)
    session = _CdpSession("ws://local")

    assert session.command("One") == {"value": 1}
    assert session.command("Two") == {"value": 2}
    assert len(connections) == 1


def test_cdp_session_honors_cancellation_before_connect(monkeypatch) -> None:
    cancel = Event()
    cancel.set()
    monkeypatch.setattr(
        "farm_merge_valet.cdp.transport.connect",
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
        "farm_merge_valet.cdp.transport.connect", lambda *_args, **_kwargs: Connection()
    )

    with pytest.raises(CdpTimeoutError):
        _CdpSession("ws://local").command("Runtime.evaluate", timeout=0.01)

    assert closed
