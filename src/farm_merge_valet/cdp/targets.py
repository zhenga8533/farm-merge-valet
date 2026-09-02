"""Browser and game target discovery, metadata, and cached target operations."""

from __future__ import annotations

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

from farm_merge_valet.cdp.transport import (
    CdpCancelledError,
    CdpConnectionError,
    _close_sessions,
    _command_target,
)
from farm_merge_valet.observability.logging import log_event

logger = logging.getLogger(__name__)
GAME_FRAME_URL_MARKER = "playfmv-"
GAME_FRAME_ENTRYPOINT = "/index.html"
_REDDIT_PAGE_URL_MARKER = "reddit.com"
_T = TypeVar("_T")
_TargetPair = tuple[str, str]
_TargetKey = tuple[int, str | None]
_target_pairs: dict[_TargetKey, _TargetPair] = {}
_target_pairs_lock = Lock()
REQUIRED_BACKGROUND_FLAGS = (
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
)


def is_game_frame_url(value: object) -> bool:
    url = str(value)
    parts = urlsplit(url)
    return GAME_FRAME_URL_MARKER in parts.netloc.casefold() and parts.path.casefold().endswith(
        GAME_FRAME_ENTRYPOINT
    )


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
        # omitted from user-facing target descriptions.
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
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for frame in targets:
        if frame.get("type") != "iframe" or not is_game_frame_url(frame.get("url", "")):
            continue
        page = reddit_pages.get(frame.get("parentId"))
        if page is not None:
            candidates.append((frame, page))

    title_matches = [
        pair
        for pair in candidates
        if title_filter is not None and title_filter in str(pair[1].get("title", "")).casefold()
    ]
    matches = title_matches or [
        pair
        for pair in candidates
        if title_filter is None or title_filter in str(pair[1].get("url", "")).casefold()
    ]
    pairs: list[tuple[str, str]] = []
    for frame, page in matches:
        frame_ws = frame.get("webSocketDebuggerUrl")
        page_ws = page.get("webSocketDebuggerUrl")
        if frame_ws and page_ws:
            pairs.append(
                (_normalize_local_ws_url(str(frame_ws)), _normalize_local_ws_url(str(page_ws)))
            )

    if len(pairs) == 1:
        return pairs[0]
    if len(pairs) > 1:
        raise CdpConnectionError(
            "Multiple matching Farm Merge Valley tabs are open. Narrow the page target setting "
            "or close the extra game tabs so board state and screen geometry are unambiguous."
        )

    raise CdpConnectionError(
        "The browser's remote debugging endpoint is reachable, but no Farm Merge Valley iframe "
        "paired with a matching Reddit page is open. Has the game been loaded (clicked "
        "'Play'), and does the configured page target match that tab?"
    )


def has_game_target_pair(port: int, page_title: str | None = None) -> bool:
    """Return whether exactly one usable game iframe and Reddit page pair is open."""
    try:
        _select_target_pair(_load_targets(port), page_title)
    except CdpConnectionError:
        return False
    return True


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

    Used for browser-level controls that cannot run inside the cross-origin
    game frame.
    """
    _, page_ws = _target_pair(port, page_title)
    return page_ws


def run_game_frame_operation(
    port: int,
    page_title: str | None,
    operation: Callable[[str], _T],
    *,
    retry: bool = True,
) -> _T:
    """Run against the cached game target, refreshing it once on failure."""
    attempts = 2 if retry else 1
    for attempt in range(attempts):
        try:
            return operation(find_game_frame_target(port, page_title))
        except CdpCancelledError:
            raise
        except CdpConnectionError:
            _invalidate_target_pair(port, page_title)
            if attempt + 1 == attempts:
                raise
            log_event(
                logger,
                logging.DEBUG,
                "cdp.game_target_refresh",
                "CDP game target changed; refreshing browser targets.",
                attempt=attempt + 1,
            )
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
            log_event(
                logger,
                logging.DEBUG,
                "cdp.page_target_refresh",
                "CDP page target changed; refreshing browser targets.",
                attempt=attempt + 1,
            )
    raise AssertionError("unreachable")
