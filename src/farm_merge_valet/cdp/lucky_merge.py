"""Scoped network isolation for an offline merge attempt."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Event

from farm_merge_valet.cdp.evaluation import _evaluate_target
from farm_merge_valet.cdp.portal_lifecycle import request_game_start
from farm_merge_valet.cdp.targets import (
    PortalStartSubmissionError,
    _invalidate_target_pair,
    _load_targets,
    discover_game_targets,
)
from farm_merge_valet.cdp.transport import CdpConnectionError, _command_target
from farm_merge_valet.integrations import (
    GamePortal,
    PortalStartupStrategy,
    PortalSupportLevel,
    portal_definition,
)

logger = logging.getLogger(__name__)


@dataclass
class LuckyMergeNetwork:
    port: int
    page_title: str | None
    game_ws_url: str
    page_ws_url: str
    page_url: str
    portal: GamePortal = GamePortal.REDDIT
    offline: bool = False
    cancel_event: Event | None = None

    @classmethod
    def for_game(
        cls, port: int, page_title: str | None, cancel_event: Event | None = None
    ) -> LuckyMergeNetwork:
        matches = [
            target
            for target in discover_game_targets(_load_targets(port), page_title)
            if target.support_level is PortalSupportLevel.AUTOMATION
        ]
        if len(matches) != 1:
            raise CdpConnectionError("Lucky merge requires exactly one verified game page.")
        target = matches[0]
        return cls(
            port,
            page_title,
            target.game_ws_url,
            target.page_ws_url,
            target.page_url,
            target.portal,
            cancel_event=cancel_event,
        )

    def set_offline(self) -> None:
        try:
            for ws_url in (self.page_ws_url, self.game_ws_url):
                _command_target(ws_url, "Network.enable")
                _command_target(ws_url, "Network.setBypassServiceWorker", {"bypass": True})
                _command_target(
                    ws_url,
                    "Network.emulateNetworkConditions",
                    {
                        "offline": True,
                        "latency": 0,
                        "downloadThroughput": 0,
                        "uploadThroughput": 0,
                    },
                )
            self.offline = True
            probe = _evaluate_target(
                self.game_ws_url,
                """(async () => {
                  try {
                    await fetch(new URL('/favicon.ico?fmv_offline_probe=' + Date.now(),
                      location.origin), {cache: 'no-store'});
                    return false;
                  } catch { return true; }
                })()""",
                timeout=5,
            )
            if probe is not True:
                raise CdpConnectionError("Game request was not isolated from the network.")
            disconnected = _evaluate_target(
                self.game_ws_url,
                """(() => {
                  const services = window.__fmvGameplayServices;
                  const connection = services?.ordersService?._autoSaveService?.services
                    ?.connection;
                  const socket = connection?._nakamaProvider?._connectivity?._socket
                    ?.adapter?._socket;
                  if (connection?.isConnected !== true || !(socket instanceof WebSocket) ||
                      socket.readyState !== WebSocket.OPEN) return false;
                  socket.close();
                  return true;
                })()""",
            )
            if disconnected is not True:
                raise CdpConnectionError("The current game backend socket could not be isolated.")
        except Exception:
            self.restore_online()
            raise

    def discard_and_reload(self) -> None:
        self._leave_game_page()
        time.sleep(2.0)
        self.restore_online(game_target_required=False)
        self._open_game_page()

    def reload_online(self) -> None:
        self._leave_game_page()
        time.sleep(2.0)
        self._open_game_page()

    def _leave_game_page(self) -> None:
        result = _command_target(self.page_ws_url, "Page.navigate", {"url": "about:blank"})
        if result.get("errorText"):
            raise CdpConnectionError("Could not leave the unsaved game document.")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                if _evaluate_target(self.page_ws_url, "location.href") == "about:blank":
                    return
            except CdpConnectionError:
                pass
            time.sleep(0.1)
        raise CdpConnectionError("Unsaved game document did not close before reconnecting.")

    def _open_game_page(self) -> None:
        result = _command_target(self.page_ws_url, "Page.navigate", {"url": self.page_url})
        if result.get("errorText"):
            raise CdpConnectionError("Could not reload the saved game page.")
        _invalidate_target_pair(self.port, self.page_title)
        portal = portal_definition(self.portal)
        deadline = time.monotonic() + 45.0
        next_start_attempt = 0.0
        start_attempts = 0
        while time.monotonic() < deadline and not (
            self.cancel_event and self.cancel_event.is_set()
        ):
            matches = discover_game_targets(_load_targets(self.port), self.page_title)
            if any(target.page_ws_url == self.page_ws_url for target in matches):
                logger.info("Game iframe reopened after lucky-merge reload.")
                return
            if (
                portal.startup_strategy is not PortalStartupStrategy.DIRECT
                and time.monotonic() >= next_start_attempt
            ):
                next_start_attempt = time.monotonic() + 8.0
                try:
                    if request_game_start(self.port, self.page_title, portal):
                        start_attempts += 1
                        logger.info(
                            "Portal Play requested after lucky-merge reload (%d).", start_attempts
                        )
                except PortalStartSubmissionError:
                    start_attempts += 1
                    logger.info("Portal Play result is uncertain; checking for the game iframe.")
            if self.cancel_event:
                self.cancel_event.wait(0.25)
            else:
                time.sleep(0.25)
        raise CdpConnectionError("The game did not reopen after restoring the saved page.")

    def restore_online(self, *, game_target_required: bool = True) -> None:
        errors: list[Exception] = []
        for ws_url in (self.page_ws_url, self.game_ws_url):
            try:
                _command_target(
                    ws_url,
                    "Network.emulateNetworkConditions",
                    {
                        "offline": False,
                        "latency": 0,
                        "downloadThroughput": -1,
                        "uploadThroughput": -1,
                    },
                )
                _command_target(ws_url, "Network.setBypassServiceWorker", {"bypass": False})
            except Exception as exc:
                if ws_url == self.page_ws_url or game_target_required:
                    errors.append(exc)
        self.offline = bool(errors)
        if errors:
            raise CdpConnectionError("Could not restore the managed game network.") from errors[0]
