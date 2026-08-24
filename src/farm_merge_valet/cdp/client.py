"""Read live game state directly from the page's JavaScript context via the
Chromium DevTools Protocol (CDP), instead of inferring it from screen
captures.

`evaluate`/`evaluate_top_page` are the low-level building blocks; see
`cdp/board_store.py` for board content and `cdp/scene_geometry.py` for
board-to-screen pixel positions -- both read real, live objects out of
the page directly rather than inferring anything from a screen capture.

Requires a Chromium-family browser launched with remote debugging and a
dedicated profile. Use `farm-merge-valet browser launch`; the equivalent manual
Chrome command is:

    chrome.exe --remote-debugging-port=9222 --user-data-dir="<some other dir>"

See the README for setup instructions.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import time
from collections.abc import Callable
from threading import Event, Lock
from typing import Any, TypeVar
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

logger = logging.getLogger(__name__)

# The game is a Reddit "devvit" app, embedded as a cross-origin iframe
# inside the Reddit post page -- this is what distinguishes its CDP target
# from the top-level Reddit page and any other iframe (e.g. a reCAPTCHA
# challenge frame) that might also be open. Not underscore-prefixed since
# `cdp/scene_geometry.py` reuses it to identify the *same* iframe from the
# outside (matching its `src`, from the top-level page's own DOM).
GAME_FRAME_URL_MARKER = "playfmv-"
GAME_FRAME_ENTRYPOINT = "/index.html"

# Distinguishes the actual Reddit post page hosting the game from any
# other unrelated tab that might happen to be open in the same
# remote-debugging browser instance -- see `find_top_page_target`.
_REDDIT_PAGE_URL_MARKER = "reddit.com"

_TargetPair = tuple[str, str]
_TargetKey = tuple[int, str | None]
_target_pairs: dict[_TargetKey, _TargetPair] = {}
_target_pairs_lock = Lock()
_sessions: dict[str, _CdpSession] = {}
_sessions_lock = Lock()


def is_game_frame_url(value: object) -> bool:
    url = str(value)
    parts = urlsplit(url)
    return GAME_FRAME_URL_MARKER in parts.netloc.casefold() and parts.path.casefold().endswith(
        GAME_FRAME_ENTRYPOINT
    )


_T = TypeVar("_T")

_CDP_CONNECT_TIMEOUT = 3.0
_CDP_COMMAND_TIMEOUT = 5.0
_CDP_POLL_INTERVAL = 0.1

REQUIRED_BACKGROUND_FLAGS = (
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
)


class CdpConnectionError(RuntimeError):
    """Raised when the browser DevTools Protocol endpoint isn't reachable,
    or the game's iframe target can't be found there.

    Almost always means either: the browser wasn't launched with
    `--remote-debugging-port`, or the game hasn't been opened (clicked
    "Play") in that browser yet.
    """


class CdpTimeoutError(CdpConnectionError):
    """Raised when the browser doesn't answer a CDP command within its deadline."""


class CdpCancelledError(CdpConnectionError):
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


def _load_json_endpoint(port: int, path: str) -> object:
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
        return json.loads(response.read())


def _load_targets(port: int) -> list[dict[str, Any]]:
    try:
        targets = _load_json_endpoint(port, "/json")
    except (OSError, URLError, ValueError) as exc:
        raise CdpConnectionError(
            f"Could not reach the browser's remote debugging endpoint on port {port}. "
            "Is it running with --remote-debugging-port set?"
        ) from exc
    if not isinstance(targets, list):
        raise CdpConnectionError("Browser returned an invalid remote-debugging target list.")
    return [target for target in targets if isinstance(target, dict)]


def _normalize_local_ws_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.hostname != "localhost":
        return value
    port = f":{parts.port}" if parts.port is not None else ""
    return urlunsplit((parts.scheme, f"127.0.0.1{port}", parts.path, parts.query, parts.fragment))


def list_targets(port: int) -> list[dict[str, str | None]]:
    """Return a compact, stable view of the browser's debuggable targets."""
    fields = ("id", "parentId", "type", "title", "url", "webSocketDebuggerUrl")
    targets = [
        {field: str(target[field]) if target.get(field) is not None else None for field in fields}
        for target in _load_targets(port)
    ]
    for target in targets:
        for field in ("title", "url"):
            if value := target[field]:
                parts = urlsplit(value)
                if parts.scheme and parts.netloc:
                    target[field] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        # Debugger websocket URLs are useful internally but are intentionally
        # omitted from user-facing diagnostics.
        target["webSocketDebuggerUrl"] = None
    return targets


def read_background_flag_status(
    port: int, *, cancel_event: Event | None = None
) -> dict[str, object]:
    """Report whether the remote-debugging browser has the required launch flags."""
    try:
        metadata = read_browser_metadata(port, cancel_event=cancel_event)
    except CdpCancelledError:
        raise
    except (OSError, URLError, ValueError, CdpConnectionError) as exc:
        return {
            "available": False,
            "all_present": None,
            "missing": list(REQUIRED_BACKGROUND_FLAGS),
            "detail": str(exc),
        }
    command_line = str(metadata.get("command_line", ""))
    present = {
        flag for flag in REQUIRED_BACKGROUND_FLAGS if _command_line_has_switch(command_line, flag)
    }
    missing = [flag for flag in REQUIRED_BACKGROUND_FLAGS if flag not in present]
    return {"available": True, "all_present": not missing, "missing": missing, "detail": None}


def _command_line_has_switch(command_line: str, switch: str) -> bool:
    return re.search(rf"(?<!\S){re.escape(switch)}(?:=\S+)?(?=\s|$)", command_line) is not None


def _parse_version_page(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("\t")
        if separator and key in {"Command Line", "Executable Path", "Profile Path"}:
            values[key] = value.strip()
    return values


def _version_page_metadata(browser_ws: str) -> dict[str, str]:
    for url in ("chrome://version/", "edge://version/", "brave://version/"):
        created = _command_target(
            browser_ws, "Target.createTarget", {"url": url, "background": True}
        )
        target_id = created.get("targetId")
        if not isinstance(target_id, str):
            continue
        try:
            ws_url: str | None = None
            for _ in range(30):
                for target in _load_targets_from_browser_ws(browser_ws):
                    if target.get("id") == target_id and isinstance(
                        target.get("webSocketDebuggerUrl"), str
                    ):
                        ws_url = _normalize_local_ws_url(target["webSocketDebuggerUrl"])
                        break
                if ws_url is not None:
                    break
                time.sleep(0.05)
            if ws_url is None:
                continue
            time.sleep(0.1)
            result = _command_target(
                ws_url,
                "Runtime.evaluate",
                {"expression": "document.body?.innerText || ''", "returnByValue": True},
            )
            value = result.get("result", {}).get("value")
            if isinstance(value, str) and (metadata := _parse_version_page(value)):
                return metadata
        finally:
            try:
                _command_target(browser_ws, "Target.closeTarget", {"targetId": target_id})
            except CdpConnectionError:
                pass
    raise CdpConnectionError("Could not inspect the browser's internal version page.")


def _load_targets_from_browser_ws(browser_ws: str) -> list[dict[str, Any]]:
    port = urlsplit(browser_ws).port
    if port is None:
        raise CdpConnectionError("Browser DevTools target did not include a port.")
    return _load_targets(port)


def read_browser_metadata(port: int, *, cancel_event: Event | None = None) -> dict[str, object]:
    """Read browser identity, command line, executable, and active profile."""
    version = _load_json_endpoint(port, "/json/version")
    if not isinstance(version, dict):
        raise CdpConnectionError("Browser returned invalid DevTools version metadata.")
    ws_url = version.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str):
        raise CdpConnectionError("Browser did not expose its DevTools target.")
    browser_ws = _normalize_local_ws_url(ws_url)
    arguments: list[str] | None = None
    try:
        result = _command_target(
            browser_ws, "Browser.getBrowserCommandLine", cancel_event=cancel_event
        )
        raw_arguments = result.get("arguments")
        if isinstance(raw_arguments, list) and all(
            isinstance(value, str) for value in raw_arguments
        ):
            arguments = raw_arguments
    except CdpCancelledError:
        raise
    except CdpConnectionError:
        pass

    page_metadata: dict[str, str] = {}
    if arguments is None:
        page_metadata = _version_page_metadata(browser_ws)
    command_line = (
        subprocess.list2cmdline(arguments)
        if arguments is not None
        else page_metadata.get("Command Line", "")
    )
    executable = arguments[0] if arguments else page_metadata.get("Executable Path")
    return {
        "browser": version.get("Browser"),
        "user_agent": version.get("User-Agent"),
        "command_line": command_line,
        "arguments": arguments,
        "executable_path": executable,
        "profile_path": page_metadata.get("Profile Path"),
    }


def close_browser(port: int) -> None:
    """Gracefully close the browser instance owning a DevTools endpoint."""
    version = _load_json_endpoint(port, "/json/version")
    ws_url = version.get("webSocketDebuggerUrl") if isinstance(version, dict) else None
    if not isinstance(ws_url, str):
        raise CdpConnectionError("Browser did not expose its DevTools target.")
    _command_target(_normalize_local_ws_url(ws_url), "Browser.close")


def _select_target_pair(
    targets: list[dict[str, Any]], page_title: str | None = None
) -> tuple[str, str]:
    """Return the game-iframe and owning-page websocket URLs."""
    title_filter = page_title.casefold() if page_title else None
    reddit_pages = {
        target.get("id"): target
        for target in targets
        if target.get("type") == "page" and _REDDIT_PAGE_URL_MARKER in str(target.get("url", ""))
    }
    title_matches = {
        id_: target
        for id_, target in reddit_pages.items()
        if title_filter is not None and title_filter in str(target.get("title", "")).casefold()
    }
    pages = title_matches or {
        id_: target
        for id_, target in reddit_pages.items()
        if title_filter is None or title_filter in str(target.get("url", "")).casefold()
    }
    pairs: list[tuple[str, str]] = []
    for frame in targets:
        if frame.get("type") != "iframe" or not is_game_frame_url(frame.get("url", "")):
            continue
        page = pages.get(frame.get("parentId"))
        frame_ws = frame.get("webSocketDebuggerUrl")
        page_ws = page.get("webSocketDebuggerUrl") if page else None
        if frame_ws and page_ws:
            pairs.append(
                (_normalize_local_ws_url(str(frame_ws)), _normalize_local_ws_url(str(page_ws)))
            )

    if len(pairs) == 1:
        return pairs[0]
    if len(pairs) > 1:
        raise CdpConnectionError(
            "Multiple matching Farm Merge Valley tabs are open. Narrow FMV_WINDOW_TITLE "
            "or close the extra game tabs so board state and screen geometry are unambiguous."
        )

    raise CdpConnectionError(
        "The browser's remote debugging endpoint is reachable, but no Farm Merge Valley iframe "
        "paired with a matching Reddit page is open. Has the game been loaded (clicked "
        "'Play'), and does FMV_WINDOW_TITLE match that tab?"
    )


def _target_pair(port: int, page_title: str | None = None) -> _TargetPair:
    key = (port, page_title)
    with _target_pairs_lock:
        cached = _target_pairs.get(key)
    if cached is not None:
        return cached

    selected = _select_target_pair(_load_targets(port), page_title)
    with _target_pairs_lock:
        return _target_pairs.setdefault(key, selected)


def _invalidate_target_pair(port: int, page_title: str | None = None) -> None:
    with _target_pairs_lock:
        pair = _target_pairs.pop((port, page_title), None)
    if pair is not None:
        _close_sessions(pair)


def find_game_frame_target(port: int, page_title: str | None = None) -> str:
    """Return the game iframe target paired with its owning Reddit page."""
    game_ws, _ = _target_pair(port, page_title)
    return game_ws


def find_top_page_target(port: int, page_title: str | None = None) -> str:
    """Returns the `webSocketDebuggerUrl` for the top-level Reddit post
    page hosting the game -- as opposed to the game's own cross-origin
    iframe (`find_game_frame_target`).

    Needed for anything that has to know the iframe's own position/size
    from the *outside*: a cross-origin iframe can never read that about
    itself (`window.frameElement` is null across the origin boundary) --
    see `cdp/scene_geometry.py`.
    """
    _, page_ws = _target_pair(port, page_title)
    return page_ws


def run_game_frame_operation(
    port: int,
    page_title: str | None,
    operation: Callable[[str], _T],
) -> _T:
    """Run against the cached game target, refreshing it once on failure."""
    for attempt in range(2):
        try:
            return operation(find_game_frame_target(port, page_title))
        except CdpCancelledError:
            raise
        except CdpConnectionError:
            _invalidate_target_pair(port, page_title)
            if attempt == 1:
                raise
            logger.info("CDP game target changed; refreshing browser targets.")
    raise AssertionError("unreachable")


def _run_top_page_operation(
    port: int,
    page_title: str | None,
    operation: Callable[[str], _T],
) -> _T:
    for attempt in range(2):
        try:
            return operation(find_top_page_target(port, page_title))
        except CdpCancelledError:
            raise
        except CdpConnectionError:
            _invalidate_target_pair(port, page_title)
            if attempt == 1:
                raise
            logger.info("CDP page target changed; refreshing browser targets.")
    raise AssertionError("unreachable")


def _evaluate_target(
    ws_url: str,
    expression: str,
    *,
    timeout: float = _CDP_COMMAND_TIMEOUT,
    cancel_event: Event | None = None,
) -> object:
    try:
        command_result = _command_target(
            ws_url,
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout=timeout,
            cancel_event=cancel_event,
        )
        if exception := command_result.get("exceptionDetails"):
            description = exception.get("exception", {}).get("description")
            raise CdpConnectionError(
                f"CDP JavaScript evaluation failed: {description or exception.get('text')}"
            )
        result = command_result.get("result", {})
        return result.get("value") if isinstance(result, dict) else None
    except (CdpConnectionError, CdpCancelledError, CdpTimeoutError):
        raise
    except (OSError, ValueError, WebSocketException, TimeoutError) as exc:
        raise CdpConnectionError("Lost the browser DevTools WebSocket connection.") from exc


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


def evaluate(
    port: int,
    expression: str,
    page_title: str | None = None,
    *,
    timeout: float = _CDP_COMMAND_TIMEOUT,
    cancel_event: Event | None = None,
) -> object:
    """Evaluate `expression` in the game iframe's JS context and return its
    value (must be JSON-serializable -- CDP's `returnByValue` requirement).
    """
    return run_game_frame_operation(
        port,
        page_title,
        lambda ws_url: _evaluate_target(
            ws_url, expression, timeout=timeout, cancel_event=cancel_event
        ),
    )


def evaluate_top_page(
    port: int,
    expression: str,
    page_title: str | None = None,
    *,
    timeout: float = _CDP_COMMAND_TIMEOUT,
    cancel_event: Event | None = None,
) -> object:
    """Same as `evaluate`, but in the top-level Reddit page's JS context
    rather than the game's own iframe -- for reading things a cross-origin
    iframe can't see about its own placement (see `find_top_page_target`)."""
    return _run_top_page_operation(
        port,
        page_title,
        lambda ws_url: _evaluate_target(
            ws_url, expression, timeout=timeout, cancel_event=cancel_event
        ),
    )


def apply_background_overrides(
    port: int, page_title: str | None = None, *, cancel_event: Event | None = None
) -> None:
    """Apply supported lifecycle/focus overrides without activating a window."""
    game_ws, page_ws = _target_pair(port, page_title)
    for ws_url in (page_ws, game_ws):
        # Unsupported methods are deliberately ignored individually: protocol
        # support differs by browser version, while the launch flags remain the
        # primary protection against background throttling.
        for method, params in (
            ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
            ("Emulation.setIdleOverride", {"isUserActive": True, "isScreenUnlocked": True}),
            ("Page.setWebLifecycleState", {"state": "active"}),
        ):
            try:
                _command_target(ws_url, method, params, cancel_event=cancel_event)
            except CdpCancelledError:
                raise
            except CdpConnectionError:
                logger.debug("CDP override %s is unavailable for this target.", method)


def capture_game_frame(port: int, page_title: str | None = None) -> bytes:
    """Capture only the game iframe from the top page without focusing the browser."""
    geometry = evaluate_top_page(port, _IFRAME_CAPTURE_GEOMETRY, page_title)
    if not isinstance(geometry, dict):
        raise CdpConnectionError("Could not locate the Farm Merge Valley iframe in the page.")
    clip = {
        "x": float(geometry["x"]),
        "y": float(geometry["y"]),
        "width": float(geometry["width"]),
        "height": float(geometry["height"]),
        "scale": 1,
    }
    result = _run_top_page_operation(
        port,
        page_title,
        lambda ws_url: _command_target(
            ws_url,
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": False, "fromSurface": True, "clip": clip},
        ),
    )
    data = result.get("data")
    if not isinstance(data, str):
        raise CdpConnectionError("Browser returned an invalid screenshot payload.")
    try:
        return base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise CdpConnectionError("Browser returned malformed screenshot data.") from exc


_IFRAME_CAPTURE_GEOMETRY = f"""
(() => {{
  const marker = {GAME_FRAME_URL_MARKER!r};
  const entrypoint = {GAME_FRAME_ENTRYPOINT!r};
  let frame = null;
  const walk = (root) => {{
    for (const element of root.querySelectorAll('*')) {{
      const src = element.src || '';
      if (element.tagName === 'IFRAME' && src.includes(marker) &&
          new URL(src, document.baseURI).pathname.toLowerCase().endsWith(entrypoint)) {{
        frame = element;
        return;
      }}
      if (element.shadowRoot) walk(element.shadowRoot);
      if (frame) return;
    }}
  }};
  walk(document);
  if (!frame) return null;
  const rect = frame.getBoundingClientRect();
  if (!(rect.width > 0 && rect.height > 0)) return null;
  return {{
    x: rect.left + window.scrollX,
    y: rect.top + window.scrollY,
    width: rect.width,
    height: rect.height,
  }};
}})()
"""
