"""CDP sessions, target access, evaluation, and loaded-resource transport."""

from __future__ import annotations

import json
import logging
import time
from threading import Event, Lock
from typing import Any, TypeVar

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

from farm_merge_valet.automation.runtime import RuntimeCancelledError, RuntimeConnectionError

logger = logging.getLogger(__name__)

_sessions: dict[str, _CdpSession] = {}
_sessions_lock = Lock()


_T = TypeVar("_T")

_CDP_CONNECT_TIMEOUT = 3.0
_CDP_COMMAND_TIMEOUT = 5.0
_CDP_POLL_INTERVAL = 0.1


class CdpConnectionError(RuntimeConnectionError):
    """Raised when the browser DevTools Protocol endpoint isn't reachable,
    or the game's iframe target can't be found there.

    Almost always means either: the browser wasn't launched with
    `--remote-debugging-port`, or the game hasn't been opened (clicked
    "Play") in that browser yet.
    """


class CdpTimeoutError(CdpConnectionError):
    """Raised when the browser doesn't answer a CDP command within its deadline."""


class CdpEvaluationError(RuntimeConnectionError):
    """Raised when JavaScript ran but failed inside the target renderer."""


class CdpCancelledError(CdpConnectionError, RuntimeCancelledError):
    """Raised when pause or quit cancels an in-flight CDP command."""


class _CdpSession:
    def __init__(self, ws_url: str) -> None:
        self.ws_url = ws_url
        self._connection: Any = None
        self._lock = Lock()
        self._next_id = 1

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except (OSError, WebSocketException):
                pass

    def _connect_unlocked(self) -> Any:
        if self._connection is None:
            self._connection = connect(
                self.ws_url,
                proxy=None,
                open_timeout=_CDP_CONNECT_TIMEOUT,
                close_timeout=1,
                ping_interval=20,
                ping_timeout=5,
                max_size=50 * 1024 * 1024,
            )
        return self._connection

    def command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = _CDP_COMMAND_TIMEOUT,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while not self._lock.acquire(timeout=_CDP_POLL_INTERVAL):
            _check_cancelled(cancel_event, method)
            if time.monotonic() >= deadline:
                raise CdpTimeoutError(f"Timed out waiting to send CDP {method}.")
        try:
            _check_cancelled(cancel_event, method)
            connection = self._connect_unlocked()
            request_id = self._next_id
            self._next_id += 1
            connection.send(
                json.dumps({"id": request_id, "method": method, "params": params or {}})
            )
            while True:
                _check_cancelled(cancel_event, method)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CdpTimeoutError(f"CDP {method} timed out after {timeout:g}s.")
                try:
                    payload = connection.recv(timeout=min(_CDP_POLL_INTERVAL, remaining))
                except TimeoutError:
                    continue
                response = json.loads(payload)
                if response.get("id") != request_id:
                    continue
                if error := response.get("error"):
                    raise CdpConnectionError(
                        f"CDP {method} failed: {error.get('message', 'unknown error')}"
                    )
                result = response.get("result", {})
                return result if isinstance(result, dict) else {}
        except (CdpCancelledError, CdpTimeoutError, ConnectionClosed, OSError, ValueError):
            self._close_unlocked()
            raise
        finally:
            self._lock.release()


def _check_cancelled(cancel_event: Event | None, method: str) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CdpCancelledError(f"CDP {method} was cancelled.")


def _session(ws_url: str) -> _CdpSession:
    with _sessions_lock:
        return _sessions.setdefault(ws_url, _CdpSession(ws_url))


def _close_sessions(ws_urls: tuple[str, ...]) -> None:
    with _sessions_lock:
        sessions = [_sessions.pop(ws_url, None) for ws_url in ws_urls]
    for session in sessions:
        if session is not None:
            session.close()


def _command_target(
    ws_url: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = _CDP_COMMAND_TIMEOUT,
    cancel_event: Event | None = None,
) -> dict[str, Any]:
    try:
        return _session(ws_url).command(method, params, timeout=timeout, cancel_event=cancel_event)
    except (CdpConnectionError, CdpCancelledError, CdpTimeoutError):
        raise
    except (OSError, ValueError, WebSocketException, TimeoutError) as exc:
        raise CdpConnectionError("Lost the browser DevTools WebSocket connection.") from exc
