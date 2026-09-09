from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from farm_merge_valet.browser.manager import (
    BrowserKind,
    BrowserManager,
    BrowserManagerError,
    BrowserStatus,
    SupportTier,
)
from farm_merge_valet.cdp.transport import CdpConnectionError
from farm_merge_valet.config import AppConfig
from farm_merge_valet.integrations import PortalSupportLevel, portal_for_page_url


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_auto_detection_prefers_supported_chrome_then_edge(tmp_path, monkeypatch) -> None:
    program_files = tmp_path / "Program Files"
    program_files_x86 = tmp_path / "Program Files (x86)"
    chrome = _executable(program_files / "Google/Chrome/Application/chrome.exe")
    edge = _executable(program_files_x86 / "Microsoft/Edge/Application/msedge.exe")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("ProgramFiles(x86)", str(program_files_x86))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

    installations = BrowserManager(AppConfig()).installations()

    assert [(item.kind, item.executable) for item in installations] == [
        (BrowserKind.CHROME, chrome),
        (BrowserKind.EDGE, edge),
    ]
    assert all(item.support is SupportTier.SUPPORTED for item in installations)


def test_explicit_brave_is_marked_experimental(tmp_path) -> None:
    executable = _executable(tmp_path / "BraveSoftware/Brave-Browser/Application/brave.exe")
    settings = AppConfig(browser="brave", browser_executable=executable)

    installation = BrowserManager(settings).installations()[0]

    assert installation.kind is BrowserKind.BRAVE
    assert installation.support is SupportTier.EXPERIMENTAL


def test_status_verifies_profile_flags_and_ownership(tmp_path, monkeypatch) -> None:
    executable = _executable(tmp_path / "chrome.exe")
    profile = tmp_path / "profile"
    profile.mkdir()
    settings = AppConfig(
        _env_file=None,
        browser="chrome",
        browser_executable=executable,
        browser_profile_dir=profile,
    )
    marker = {"kind": "chrome", "executable": str(executable), "port": 9222}
    (profile / ".farm-merge-valet-browser.json").write_text(json.dumps(marker), encoding="utf-8")
    command_line = " ".join(
        [
            f'"{executable}"',
            "--remote-debugging-port=9222",
            f'--user-data-dir="{profile}"',
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-background-mode",
        ]
    )
    manager = BrowserManager(settings)
    monkeypatch.setattr(manager, "_endpoint_reachable", lambda: True)
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.read_browser_metadata",
        lambda _port: {
            "browser": "Chrome/151",
            "executable_path": str(executable),
            "command_line": command_line,
        },
    )
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.has_game_target_pair",
        lambda _port, _title, **_kwargs: True,
    )

    status = manager.status()

    assert status.running
    assert status.compatible
    assert status.managed
    assert status.game_loaded
    assert status.game_frame_available
    assert status.missing_switches == ()


def test_status_does_not_treat_launcher_as_loaded_game(tmp_path, monkeypatch) -> None:
    executable = _executable(tmp_path / "chrome.exe")
    profile = tmp_path / "profile"
    profile.mkdir()
    settings = AppConfig(
        _env_file=None,
        browser="chrome",
        browser_executable=executable,
        browser_profile_dir=profile,
    )
    manager = BrowserManager(settings)
    monkeypatch.setattr(manager, "_endpoint_reachable", lambda: True)
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.read_browser_metadata",
        lambda _port: {
            "browser": "Chrome/151",
            "executable_path": str(executable),
            "arguments": [],
        },
    )
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.has_game_target_pair",
        lambda _port, _title, **_kwargs: False,
    )

    assert not manager.status().game_loaded


def test_stop_refuses_an_unowned_browser(monkeypatch) -> None:
    manager = BrowserManager(AppConfig())
    monkeypatch.setattr(
        manager,
        "status",
        lambda: BrowserStatus(True, True, False, kind=BrowserKind.CHROME),
    )

    with pytest.raises(BrowserManagerError, match="ownership marker"):
        manager.stop()


def test_status_accepts_profile_paths_with_spaces_from_argument_list(tmp_path, monkeypatch) -> None:
    executable = _executable(tmp_path / "Browser App" / "chrome.exe")
    profile = tmp_path / "Managed Profile"
    settings = AppConfig(
        _env_file=None,
        browser="chrome",
        browser_executable=executable,
        browser_profile_dir=profile,
    )
    manager = BrowserManager(settings)
    arguments = [
        str(executable),
        "--remote-debugging-port=9222",
        f"--user-data-dir={profile}",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--disable-background-mode",
    ]
    monkeypatch.setattr(manager, "_endpoint_reachable", lambda: True)
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.read_browser_metadata",
        lambda _port: {
            "browser": "Chrome/151",
            "executable_path": str(executable),
            "command_line": "",
            "arguments": arguments,
        },
    )
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.has_game_target_pair",
        lambda _port, _title, **_kwargs: False,
    )

    status = manager.status()

    assert status.compatible
    assert status.profile_dir == profile


def test_launch_writes_marker_and_required_switches(tmp_path, monkeypatch) -> None:
    executable = _executable(tmp_path / "chrome.exe")
    profile = tmp_path / "profile"
    settings = AppConfig(
        _env_file=None,
        browser="chrome",
        browser_executable=executable,
        browser_profile_dir=profile,
    )
    manager = BrowserManager(settings)
    ready = BrowserStatus(
        True,
        True,
        True,
        BrowserKind.CHROME,
        SupportTier.SUPPORTED,
        executable,
        profile,
    )
    statuses = iter([BrowserStatus(False, False, False), ready])
    endpoints = iter([True])
    launched = []

    class Process:
        pid = 123
        returncode = None

        def poll(self):
            return None

    monkeypatch.setattr(manager, "status", lambda: next(statuses))
    monkeypatch.setattr(manager, "_endpoint_reachable", lambda: next(endpoints))
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.subprocess.Popen",
        lambda arguments: launched.append(arguments) or Process(),
    )

    assert manager.launch() is ready
    assert (profile / ".farm-merge-valet-browser.json").is_file()
    arguments = launched[0]
    assert "--remote-debugging-port=9222" in arguments
    assert "--enable-automation" not in arguments
    assert "--disable-background-mode" in arguments
    assert "https://www.reddit.com/r/FarmMergeValley/" in arguments


def test_stop_closes_only_a_verified_managed_endpoint(monkeypatch) -> None:
    manager = BrowserManager(AppConfig())
    monkeypatch.setattr(
        manager,
        "status",
        lambda: BrowserStatus(True, True, True, kind=BrowserKind.CHROME),
    )
    endpoint_samples = iter([False, False])
    monkeypatch.setattr(manager, "_endpoint_reachable", lambda: next(endpoint_samples))
    closed = []
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.close_browser", lambda port: closed.append(port)
    )

    manager.stop()

    assert closed == [9222]


def test_launch_retries_after_profile_handoff_exit(tmp_path, monkeypatch) -> None:
    executable = _executable(tmp_path / "chrome.exe")
    settings = AppConfig(
        _env_file=None,
        browser="chrome",
        browser_executable=executable,
        browser_profile_dir=tmp_path / "profile",
    )
    manager = BrowserManager(settings)
    ready = BrowserStatus(True, True, True, kind=BrowserKind.CHROME)
    statuses = iter([BrowserStatus(False, False, False), ready])
    endpoints = iter([False, True])
    launches = 0

    class Process:
        pid = 123
        returncode = 0

        def __init__(self, exited: bool) -> None:
            self.exited = exited

        def poll(self):
            return 0 if self.exited else None

    def launch(_arguments):
        nonlocal launches
        launches += 1
        return Process(launches == 1)

    monkeypatch.setattr(manager, "status", lambda: next(statuses))
    monkeypatch.setattr(manager, "_endpoint_reachable", lambda: next(endpoints))
    monkeypatch.setattr("farm_merge_valet.browser.manager.subprocess.Popen", launch)
    monkeypatch.setattr("farm_merge_valet.browser.manager.time.sleep", lambda _seconds: None)

    assert manager.launch() is ready
    assert launches == 2


def test_ensure_game_open_opens_configured_url_once_in_managed_browser(monkeypatch) -> None:
    settings = AppConfig(game_url="https://www.reddit.com/r/FarmMergeValley/")
    manager = BrowserManager(settings)
    waiting = BrowserStatus(True, True, True, kind=BrowserKind.CHROME, game_loaded=False)
    monkeypatch.setattr(manager, "ensure_running", lambda: waiting)
    monkeypatch.setattr(manager, "status", lambda: waiting)
    monkeypatch.setattr("farm_merge_valet.browser.manager._GAME_LOAD_TIMEOUT", 0.0)
    monkeypatch.setattr("farm_merge_valet.browser.manager.has_page_url", lambda *_args: False)
    opened: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.open_browser_page",
        lambda port, url: opened.append((port, url)),
    )

    with pytest.raises(BrowserManagerError, match="did not finish loading"):
        manager.ensure_game_open()
    assert opened == [(9222, "https://www.reddit.com/r/FarmMergeValley/")]


@pytest.mark.parametrize(
    ("game_url", "window_title"),
    [
        ("https://www.crazygames.com/game/farm-merge-valley", "CrazyGames"),
        ("https://www.pogo.com/games/farm-merge-valley/play", "Pogo"),
        (
            "https://www.msn.com/en-nz/play/games/farm-merge-valley/cg-9nf2hg8fnlts",
            "MSN",
        ),
    ],
)
def test_ensure_game_open_waits_for_direct_portal_without_launcher(
    monkeypatch, game_url: str, window_title: str
) -> None:
    settings = AppConfig(
        game_url=game_url,
        window_title=window_title,
    )
    manager = BrowserManager(settings)
    waiting = BrowserStatus(True, True, True, kind=BrowserKind.CHROME, game_loaded=False)
    loaded = BrowserStatus(True, True, True, kind=BrowserKind.CHROME, game_loaded=True)
    monkeypatch.setattr(manager, "ensure_running", lambda: waiting)
    monkeypatch.setattr(manager, "status", lambda: loaded)
    monkeypatch.setattr("farm_merge_valet.browser.manager.has_page_url", lambda *_args: True)

    def unexpected_start(*_args):
        raise AssertionError("direct-load portal invoked a launcher")

    monkeypatch.setattr("farm_merge_valet.browser.manager.request_game_start", unexpected_start)

    assert manager.ensure_game_open() is loaded


def test_ensure_game_open_refuses_unowned_browser(monkeypatch) -> None:
    manager = BrowserManager(AppConfig())
    monkeypatch.setattr(
        manager,
        "ensure_running",
        lambda: BrowserStatus(True, True, False, game_loaded=False),
    )

    with pytest.raises(BrowserManagerError, match="unowned browser"):
        manager.ensure_game_open()


def test_ensure_game_open_retries_transient_play_error_until_game_is_loaded(
    monkeypatch,
) -> None:
    manager = BrowserManager(AppConfig())
    waiting = BrowserStatus(True, True, True, game_loaded=False)
    loaded = BrowserStatus(True, True, True, game_loaded=True)
    statuses = iter((waiting, waiting, loaded))
    clock = iter(index / 10 for index in range(20))
    attempts: list[bool] = []
    outcomes = iter((CdpConnectionError("launcher changed"), True))

    def try_start(*_args) -> bool:
        attempts.append(True)
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(manager, "ensure_running", lambda: waiting)
    monkeypatch.setattr(manager, "status", lambda: next(statuses))
    monkeypatch.setattr("farm_merge_valet.browser.manager.has_page_url", lambda *_args: True)
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.request_game_start",
        try_start,
    )
    monkeypatch.setattr("farm_merge_valet.browser.manager._GAME_START_RETRY_SECONDS", 0.0)
    monkeypatch.setattr("farm_merge_valet.browser.manager.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("farm_merge_valet.browser.manager.time.sleep", lambda _seconds: None)

    assert manager.ensure_game_open() is loaded
    assert attempts == [True, True]


def test_recover_game_reloads_loaded_managed_page(monkeypatch) -> None:
    manager = BrowserManager(AppConfig(window_title="game title"))
    loaded = BrowserStatus(True, True, True, game_loaded=True)
    monkeypatch.setattr(manager, "ensure_running", lambda: loaded)
    reloaded: list[tuple[int, str | None, bool]] = []
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.reload_game_page",
        lambda port, title, *, allow_observation=False: reloaded.append(
            (port, title, allow_observation)
        ),
    )
    monkeypatch.setattr(manager, "status", lambda: loaded)
    monkeypatch.setattr("farm_merge_valet.browser.manager.time.sleep", lambda _seconds: None)

    assert manager.recover_game() is loaded
    assert reloaded == [(9222, "game title", False)]


def test_recover_game_allows_observation_portal_reload(monkeypatch) -> None:
    manager = BrowserManager(AppConfig(game_portal="pogo"))
    pogo = portal_for_page_url(manager.settings.game_url)
    assert pogo is not None
    observation_portal = replace(pogo, support_level=PortalSupportLevel.OBSERVATION)
    loaded = BrowserStatus(True, True, True, game_loaded=True)
    monkeypatch.setattr(manager, "ensure_running", lambda: loaded)
    monkeypatch.setattr(manager, "status", lambda: loaded)
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.portal_for_page_url",
        lambda _url: observation_portal,
    )
    reloaded = []
    monkeypatch.setattr(
        "farm_merge_valet.browser.manager.reload_game_page",
        lambda port, title, *, allow_observation=False: reloaded.append(allow_observation),
    )
    monkeypatch.setattr("farm_merge_valet.browser.manager.time.sleep", lambda _seconds: None)

    assert manager.recover_game() is loaded
    assert reloaded == [True]
