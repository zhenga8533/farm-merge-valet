"""Detect, launch, inspect, and safely stop a dedicated browser instance."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from farm_merge_valet.cdp.targets import (
    REQUIRED_BACKGROUND_FLAGS,
    close_browser,
    has_game_target_pair,
    has_page_url,
    open_browser_page,
    read_browser_metadata,
    reload_game_page,
    try_start_game,
)
from farm_merge_valet.cdp.transport import CdpConnectionError
from farm_merge_valet.config import AppConfig
from farm_merge_valet.observability.logging import log_event

logger = logging.getLogger(__name__)

_MANAGED_MARKER = ".farm-merge-valet-browser.json"
_LAUNCH_TIMEOUT = 20.0
_STOP_TIMEOUT = 15.0
_LAUNCH_ATTEMPTS = 2
_GAME_LOAD_TIMEOUT = 20.0
_GAME_START_RETRY_SECONDS = 2.0
_MANAGED_SWITCHES = (*REQUIRED_BACKGROUND_FLAGS, "--disable-background-mode")


class BrowserKind(StrEnum):
    AUTO = "auto"
    CHROME = "chrome"
    EDGE = "edge"
    BRAVE = "brave"
    CHROMIUM = "chromium"


class SupportTier(StrEnum):
    SUPPORTED = "supported"
    EXPERIMENTAL = "experimental"


@dataclass(frozen=True)
class BrowserInstallation:
    kind: BrowserKind
    executable: Path
    support: SupportTier


@dataclass(frozen=True)
class BrowserStatus:
    running: bool
    compatible: bool
    managed: bool
    kind: BrowserKind | None = None
    support: SupportTier | None = None
    executable: Path | None = None
    profile_dir: Path | None = None
    missing_switches: tuple[str, ...] = ()
    game_loaded: bool = False
    detail: str | None = None

    @property
    def game_frame_available(self) -> bool:
        """Whether a matching game iframe exists, independent of runtime readiness."""
        return self.game_loaded


class BrowserManagerError(RuntimeError):
    """Raised when a managed browser operation cannot be completed safely."""


def _candidate_paths(environ: dict[str, str] | os._Environ[str]) -> dict[BrowserKind, list[Path]]:
    program_files = Path(environ.get("ProgramFiles", r"C:\Program Files"))
    program_files_x86 = Path(environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    local_app_data = Path(environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    return {
        BrowserKind.CHROME: [
            program_files / "Google/Chrome/Application/chrome.exe",
            program_files_x86 / "Google/Chrome/Application/chrome.exe",
            local_app_data / "Google/Chrome/Application/chrome.exe",
        ],
        BrowserKind.EDGE: [
            program_files_x86 / "Microsoft/Edge/Application/msedge.exe",
            program_files / "Microsoft/Edge/Application/msedge.exe",
            local_app_data / "Microsoft/Edge/Application/msedge.exe",
        ],
        BrowserKind.BRAVE: [
            program_files / "BraveSoftware/Brave-Browser/Application/brave.exe",
            program_files_x86 / "BraveSoftware/Brave-Browser/Application/brave.exe",
            local_app_data / "BraveSoftware/Brave-Browser/Application/brave.exe",
        ],
        BrowserKind.CHROMIUM: [
            program_files / "Chromium/Application/chrome.exe",
            program_files_x86 / "Chromium/Application/chrome.exe",
            local_app_data / "Chromium/Application/chrome.exe",
            local_app_data / "Chromium/chrome.exe",
        ],
    }


def _support_tier(kind: BrowserKind) -> SupportTier:
    return (
        SupportTier.SUPPORTED
        if kind in (BrowserKind.CHROME, BrowserKind.EDGE)
        else SupportTier.EXPERIMENTAL
    )


def _infer_kind(executable: Path | None, product: object = None) -> BrowserKind | None:
    text = f"{executable or ''} {product or ''}".casefold()
    if "msedge" in text or "edg/" in text:
        return BrowserKind.EDGE
    if "brave" in text:
        return BrowserKind.BRAVE
    if "chromium" in text:
        return BrowserKind.CHROMIUM
    if "chrome" in text:
        return BrowserKind.CHROME
    return None


def _normalized_path(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve(strict=False)))


def _same_path(left: Path | None, right: Path | None) -> bool:
    return (
        left is not None and right is not None and _normalized_path(left) == _normalized_path(right)
    )


def _switch_value(command_line: str, switch: str) -> str | None:
    match = re.search(
        rf"(?<!\S){re.escape(switch)}(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|(\S+))",
        command_line,
    )
    if match is None:
        return None
    return next((value for value in match.groups() if value is not None), None)


def _has_switch(command_line: str, switch: str) -> bool:
    return re.search(rf"(?<!\S){re.escape(switch)}(?:=\S+)?(?=\s|$)", command_line) is not None


def _argument_switch_value(
    arguments: list[str] | None, command_line: str, switch: str
) -> str | None:
    if arguments is None:
        return _switch_value(command_line, switch)
    for index, argument in enumerate(arguments):
        if argument.startswith(f"{switch}="):
            return argument.partition("=")[2]
        if argument == switch and index + 1 < len(arguments):
            return arguments[index + 1]
    return None


def _arguments_have_switch(arguments: list[str] | None, command_line: str, switch: str) -> bool:
    if arguments is None:
        return _has_switch(command_line, switch)
    return any(argument == switch or argument.startswith(f"{switch}=") for argument in arguments)


class BrowserManager:
    def __init__(self, settings: AppConfig, browser: BrowserKind | str | None = None) -> None:
        self.settings = settings
        try:
            self.browser = BrowserKind(browser or settings.browser)
        except ValueError as exc:
            raise BrowserManagerError(f"Unsupported browser selection: {browser}") from exc

    def installations(self) -> list[BrowserInstallation]:
        if self.settings.browser_executable is not None:
            executable = self.settings.browser_executable.expanduser().resolve(strict=False)
            if not executable.is_file():
                return []
            kind = (
                self.browser
                if self.browser is not BrowserKind.AUTO
                else _infer_kind(executable) or BrowserKind.CHROMIUM
            )
            return [BrowserInstallation(kind, executable, _support_tier(kind))]

        installations: list[BrowserInstallation] = []
        seen: set[str] = set()
        candidates = _candidate_paths(os.environ)
        kinds = (
            (BrowserKind.CHROME, BrowserKind.EDGE, BrowserKind.BRAVE, BrowserKind.CHROMIUM)
            if self.browser is BrowserKind.AUTO
            else (self.browser,)
        )
        for kind in kinds:
            for path in candidates.get(kind, []):
                if path.is_file() and (normalized := _normalized_path(path)) not in seen:
                    seen.add(normalized)
                    installations.append(BrowserInstallation(kind, path, _support_tier(kind)))
                    break
        return installations

    def _installation(self) -> BrowserInstallation:
        installations = self.installations()
        if installations:
            return installations[0]
        requested = self.browser.value
        detail = (
            f" at {self.settings.browser_executable}"
            if self.settings.browser_executable is not None
            else ""
        )
        raise BrowserManagerError(f"Could not find the requested {requested} browser{detail}.")

    def profile_dir(self, kind: BrowserKind) -> Path:
        if self.settings.browser_profile_dir is not None:
            return self.settings.browser_profile_dir.expanduser().resolve(strict=False)
        local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        return local_app_data / "FarmMergeValet" / "BrowserProfiles" / kind.value

    def _endpoint_reachable(self) -> bool:
        try:
            with urlopen(
                f"http://127.0.0.1:{self.settings.cdp_port}/json/version", timeout=1
            ) as response:
                return response.status == 200
        except (OSError, URLError):
            return False

    def _marker_matches(
        self, profile_dir: Path | None, kind: BrowserKind | None, executable: Path | None
    ) -> bool:
        if profile_dir is None or kind is None or executable is None:
            return False
        marker_path = profile_dir / _MANAGED_MARKER
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (
            marker.get("kind") == kind.value
            and marker.get("port") == self.settings.cdp_port
            and _same_path(Path(str(marker.get("executable", ""))), executable)
        )

    def status(self) -> BrowserStatus:
        if not self._endpoint_reachable():
            return BrowserStatus(False, False, False, detail="DevTools endpoint is not running.")
        try:
            metadata = read_browser_metadata(self.settings.cdp_port)
        except CdpConnectionError as exc:
            return BrowserStatus(True, False, False, detail=str(exc))

        command_line = str(metadata.get("command_line", ""))
        raw_arguments = metadata.get("arguments")
        arguments = (
            raw_arguments
            if isinstance(raw_arguments, list)
            and all(isinstance(argument, str) for argument in raw_arguments)
            else None
        )
        executable_value = metadata.get("executable_path")
        executable = Path(executable_value) if isinstance(executable_value, str) else None
        kind = _infer_kind(executable, metadata.get("browser"))
        profile_value = _argument_switch_value(arguments, command_line, "--user-data-dir")
        profile_dir = Path(profile_value) if profile_value else None
        missing = [
            switch
            for switch in _MANAGED_SWITCHES
            if not _arguments_have_switch(arguments, command_line, switch)
        ]
        expected_port = _argument_switch_value(arguments, command_line, "--remote-debugging-port")
        if expected_port != str(self.settings.cdp_port):
            missing.append(f"--remote-debugging-port={self.settings.cdp_port}")
        if profile_dir is None:
            missing.append("--user-data-dir=<dedicated-profile>")

        configured_profile_matches = self.settings.browser_profile_dir is None or _same_path(
            profile_dir, self.settings.browser_profile_dir
        )
        configured_browser_matches = self.browser is BrowserKind.AUTO or kind is self.browser
        configured_executable_matches = self.settings.browser_executable is None or _same_path(
            executable, self.settings.browser_executable
        )
        try:
            game_loaded = has_game_target_pair(self.settings.cdp_port, self.settings.window_title)
        except CdpConnectionError:
            game_loaded = False
        managed = self._marker_matches(profile_dir, kind, executable)
        compatible = (
            not missing
            and configured_profile_matches
            and configured_browser_matches
            and configured_executable_matches
        )
        detail = None
        if not configured_profile_matches:
            detail = "DevTools port belongs to a different browser profile."
        elif not configured_browser_matches or not configured_executable_matches:
            detail = "DevTools port belongs to a different browser executable."
        elif missing:
            detail = "Browser is missing required managed-launch switches."
        return BrowserStatus(
            True,
            compatible,
            managed,
            kind,
            _support_tier(kind) if kind is not None else None,
            executable,
            profile_dir,
            tuple(missing),
            game_loaded,
            detail,
        )

    def _write_marker(self, installation: BrowserInstallation, profile_dir: Path) -> None:
        try:
            profile_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BrowserManagerError(
                f"Could not create managed browser profile directory {profile_dir}: {exc}"
            ) from exc
        marker = {
            "kind": installation.kind.value,
            "executable": str(installation.executable),
            "port": self.settings.cdp_port,
        }
        try:
            (profile_dir / _MANAGED_MARKER).write_text(
                json.dumps(marker, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise BrowserManagerError(
                f"Could not write the managed-browser ownership marker in {profile_dir}: {exc}"
            ) from exc

    def launch(self) -> BrowserStatus:
        current = self.status()
        if current.running:
            if current.compatible:
                return current
            raise BrowserManagerError(
                f"Port {self.settings.cdp_port} is already owned by an incompatible browser: "
                f"{current.detail or 'unknown mismatch'}"
            )

        installation = self._installation()
        profile_dir = self.profile_dir(installation.kind)
        self._write_marker(installation, profile_dir)
        arguments = [
            str(installation.executable),
            f"--remote-debugging-port={self.settings.cdp_port}",
            f"--user-data-dir={profile_dir}",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-background-mode",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if self.settings.game_url:
            arguments.append(self.settings.game_url)
        last_process: subprocess.Popen[bytes] | None = None
        last_exit_code: int | None = None
        for attempt in range(_LAUNCH_ATTEMPTS):
            process = subprocess.Popen(arguments)
            last_process = process
            deadline = time.monotonic() + _LAUNCH_TIMEOUT
            while time.monotonic() < deadline:
                if self._endpoint_reachable():
                    status = self.status()
                    if status.compatible:
                        if installation.support is SupportTier.EXPERIMENTAL:
                            log_event(
                                logger,
                                logging.WARNING,
                                "browser.experimental",
                                "%s browser support is experimental.",
                                installation.kind.value.title(),
                                browser=installation.kind.value,
                            )
                        return status
                    if status.running:
                        time.sleep(0.25)
                        continue
                if process.poll() is not None:
                    last_exit_code = process.returncode
                    break
                time.sleep(0.25)
            if attempt + 1 < _LAUNCH_ATTEMPTS and process.poll() is not None:
                log_event(
                    logger,
                    logging.DEBUG,
                    "browser.profile_handoff_retry",
                    "Browser exited during profile handoff; retrying launch.",
                    attempt=attempt + 1,
                )
                time.sleep(1.0)
                continue
            break
        if last_process is not None and last_process.poll() is not None:
            raise BrowserManagerError(
                f"{installation.kind.value.title()} exited during startup "
                f"with code {last_exit_code}."
            )
        pid = last_process.pid if last_process is not None else "unknown"
        raise BrowserManagerError(
            f"{installation.kind.value.title()} started as PID {pid}, but its DevTools "
            f"endpoint did not become ready on port {self.settings.cdp_port}."
        )

    def ensure_running(self) -> BrowserStatus:
        status = self.status()
        if not status.running:
            return self.launch()
        if not status.compatible:
            missing = ", ".join(status.missing_switches)
            raise BrowserManagerError(
                status.detail or f"Browser is incompatible; missing switches: {missing}"
            )
        return status

    def ensure_game_open(self) -> BrowserStatus:
        """Ensure a managed browser has a page for the configured game URL."""
        status = self.ensure_running()
        if status.game_frame_available or not self.settings.game_url:
            return status
        if not status.managed:
            raise BrowserManagerError(
                "Refusing to open the game automatically in an unowned browser."
            )
        if not has_page_url(self.settings.cdp_port, self.settings.game_url):
            open_browser_page(self.settings.cdp_port, self.settings.game_url)
            log_event(
                logger,
                logging.INFO,
                "browser.game_page_opened",
                "Opened the configured game page in the managed browser.",
            )
        deadline = time.monotonic() + _GAME_LOAD_TIMEOUT
        start_attempted = False
        next_start_attempt_at = 0.0
        while time.monotonic() < deadline:
            status = self.status()
            if status.game_frame_available:
                return status
            now = time.monotonic()
            if now >= next_start_attempt_at:
                next_start_attempt_at = now + _GAME_START_RETRY_SECONDS
                try:
                    start_requested = try_start_game(
                        self.settings.cdp_port, self.settings.window_title
                    )
                except CdpConnectionError as exc:
                    start_requested = False
                    log_event(
                        logger,
                        logging.DEBUG,
                        "browser.game_start_retry",
                        "The Reddit launcher target changed during Play; retrying.",
                        detail=str(exc),
                    )
                if start_requested:
                    if not start_attempted:
                        log_event(
                            logger,
                            logging.INFO,
                            "browser.game_start_requested",
                            "Requested game startup through the Reddit launcher.",
                        )
                    start_attempted = True
            time.sleep(0.25)
        status = self.status()
        if status.game_frame_available:
            return status
        raise BrowserManagerError(
            "The game did not finish loading after the Play request. "
            "Open the configured Reddit page, click Play manually, and start the bot again."
        )

    def recover_game(self) -> BrowserStatus:
        """Reload a frozen game page or restore its managed browser and page."""
        status = self.ensure_running()
        if not status.managed:
            raise BrowserManagerError(
                "Refusing to recover the game in an unowned browser."
            )
        if status.game_frame_available:
            reload_game_page(self.settings.cdp_port, self.settings.window_title)
            log_event(
                logger,
                logging.WARNING,
                "browser.game_reloaded",
                "Reloaded the game page after its action pipeline stopped responding.",
            )
            time.sleep(0.25)
            return self.ensure_game_open()
        return self.ensure_game_open()

    def stop(self) -> None:
        status = self.status()
        if not status.running:
            return
        if not status.managed:
            raise BrowserManagerError(
                "Refusing to close the browser: its profile has no matching Farm Merge Valet "
                "ownership marker."
            )
        try:
            close_browser(self.settings.cdp_port)
        except CdpConnectionError:
            if self._endpoint_reachable():
                raise
        deadline = time.monotonic() + _STOP_TIMEOUT
        while self._endpoint_reachable() and time.monotonic() < deadline:
            time.sleep(0.25)
        if self._endpoint_reachable():
            raise BrowserManagerError("Managed browser did not close before the timeout.")

    def restart(self) -> BrowserStatus:
        self.stop()
        time.sleep(1.0)
        return self.launch()

    @staticmethod
    def status_dict(status: BrowserStatus) -> dict[str, Any]:
        return {
            "running": status.running,
            "compatible": status.compatible,
            "managed": status.managed,
            "browser": status.kind.value if status.kind else None,
            "support": status.support.value if status.support else None,
            "executable": str(status.executable) if status.executable else None,
            "profile_dir": str(status.profile_dir) if status.profile_dir else None,
            "missing_switches": list(status.missing_switches),
            "game_loaded": status.game_loaded,
            "game_frame_available": status.game_frame_available,
            "detail": status.detail,
        }
