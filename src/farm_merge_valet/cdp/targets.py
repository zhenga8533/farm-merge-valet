"""Browser and game target discovery, metadata, and cached target operations."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock
from typing import Any, TypeVar
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from farm_merge_valet.cdp.transport import (
    CdpCancelledError,
    CdpConnectionError,
    CdpTimeoutError,
    _close_sessions,
    _command_target,
)
from farm_merge_valet.integrations import (
    PORTALS,
    GamePortal,
    PortalSupportLevel,
    portal_definition,
)
from farm_merge_valet.observability.logging import log_event

logger = logging.getLogger(__name__)


class PortalStartSubmissionError(CdpConnectionError):
    """Raised when a portal Play request was sent but its result is unknown."""


_LAUNCHER_FRAME_LOOKUP_SCRIPT = """
const findLauncherFrame = launcherUrl => {
  const frames = [];
  const visit = root => {
    for (const element of root.querySelectorAll('*')) {
      if (element instanceof HTMLIFrameElement && element.src === launcherUrl) {
        frames.push(element);
      }
      if (element.shadowRoot) visit(element.shadowRoot);
    }
  };
  visit(document);
  return frames.find(element => {
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }) || null;
};
"""
_T = TypeVar("_T")
_TargetPair = tuple[str, str]
_TargetKey = tuple[int, str | None, bool]
_target_pairs: dict[_TargetKey, _TargetPair] = {}
_target_pairs_lock = Lock()
REQUIRED_BACKGROUND_FLAGS = (
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
)
_VERSION_PAGE_TIMEOUT = 2.0
_VERSION_PAGE_POLL_INTERVAL = 0.1


@dataclass(frozen=True)
class DiscoveredGameTarget:
    portal: GamePortal
    game_target_id: str
    page_target_id: str
    game_ws_url: str
    page_ws_url: str
    ancestor_ids: tuple[str, ...]
    support_level: PortalSupportLevel
    page_title: str
    page_url: str
    game_url: str


def is_game_frame_url(value: object) -> bool:
    """Return whether any registered portal recognizes a game client URL."""
    return any(portal.matches_game_frame_url(value) for portal in PORTALS)


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


def _version_page_metadata(browser_ws: str, *, cancel_event: Event | None = None) -> dict[str, str]:
    for url in ("chrome://version/", "edge://version/", "brave://version/"):
        try:
            created = _command_target(
                browser_ws,
                "Target.createTarget",
                {"url": url, "background": True, "hidden": True},
                cancel_event=cancel_event,
            )
        except CdpCancelledError:
            raise
        except CdpConnectionError:
            continue
        target_id = created.get("targetId")
        if not isinstance(target_id, str):
            continue
        try:
            browser_parts = urlsplit(browser_ws)
            ws_url = urlunsplit(
                (browser_parts.scheme, browser_parts.netloc, f"/devtools/page/{target_id}", "", "")
            )
            deadline = time.monotonic() + _VERSION_PAGE_TIMEOUT
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise CdpCancelledError("Browser metadata inspection was cancelled.")
                try:
                    result = _command_target(
                        ws_url,
                        "Runtime.evaluate",
                        {
                            "expression": "document.body?.innerText || ''",
                            "returnByValue": True,
                        },
                        cancel_event=cancel_event,
                    )
                except CdpCancelledError:
                    raise
                except CdpConnectionError:
                    result = {}
                value = result.get("result", {}).get("value")
                if isinstance(value, str) and (metadata := _parse_version_page(value)):
                    return metadata
                time.sleep(_VERSION_PAGE_POLL_INTERVAL)
        finally:
            try:
                _command_target(
                    browser_ws,
                    "Target.closeTarget",
                    {"targetId": target_id},
                    cancel_event=cancel_event,
                )
            except CdpCancelledError:
                raise
            except CdpConnectionError:
                pass
    raise CdpConnectionError("Could not inspect the browser's internal version page.")


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
        page_metadata = _version_page_metadata(browser_ws, cancel_event=cancel_event)
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


def open_browser_page(port: int, url: str) -> None:
    """Open a URL in the browser exposed by the configured DevTools endpoint."""
    version = _load_json_endpoint(port, "/json/version")
    ws_url = version.get("webSocketDebuggerUrl") if isinstance(version, dict) else None
    if not isinstance(ws_url, str):
        raise CdpConnectionError("Browser did not expose its DevTools target.")
    result = _command_target(
        _normalize_local_ws_url(ws_url),
        "Target.createTarget",
        {"url": url},
    )
    if not isinstance(result.get("targetId"), str):
        raise CdpConnectionError("Browser did not create the requested game page.")


def has_page_url(port: int, url: str) -> bool:
    """Return whether a top-level page already has the configured URL open."""
    expected = urlsplit(url)
    expected_path = expected.path.rstrip("/") or "/"
    return any(
        target.get("type") == "page"
        and (actual := urlsplit(str(target.get("url", "")))).scheme == expected.scheme
        and actual.netloc.casefold() == expected.netloc.casefold()
        and (actual.path.rstrip("/") or "/") == expected_path
        for target in _load_targets(port)
    )


def _dispatch_mouse_click(ws_url: str, x: float, y: float) -> None:
    _command_target(ws_url, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    _command_target(
        ws_url,
        "Input.dispatchMouseEvent",
        {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1},
    )
    _command_target(
        ws_url,
        "Input.dispatchMouseEvent",
        {"type": "mouseReleased", "x": x, "y": y, "button": "left", "buttons": 0, "clickCount": 1},
    )


def try_start_game(
    port: int,
    page_title: str | None = None,
    portal: GamePortal = GamePortal.REDDIT,
) -> bool:
    """Request startup through a portal's trusted browser control."""
    title_filter = page_title.casefold() if page_title else None
    targets = _load_targets(port)
    if portal is not GamePortal.REDDIT:
        return False
    reddit = portal_definition(GamePortal.REDDIT)
    pages = {
        target.get("id"): target
        for target in targets
        if target.get("type") == "page"
        and reddit.matches_page_url(target.get("url", ""))
        and (
            title_filter is None
            or title_filter in str(target.get("title", "")).casefold()
            or title_filter in str(target.get("url", "")).casefold()
        )
    }
    launcher = next(
        (
            target
            for target in targets
            if target.get("type") == "iframe"
            and target.get("parentId") in pages
            and "/launcher/launcher.html" in str(target.get("url", "")).casefold()
            and isinstance(target.get("webSocketDebuggerUrl"), str)
        ),
        None,
    )
    if launcher is None:
        return False
    button_result = _command_target(
        _normalize_local_ws_url(launcher["webSocketDebuggerUrl"]),
        "Runtime.evaluate",
        {
            "expression": """
(() => {
  const button = document.querySelector('#start-button');
  if (!(button instanceof HTMLElement)) return null;
  const rect = button.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0 || innerWidth <= 0 || innerHeight <= 0) return null;
  return {
    xRatio: (rect.x + rect.width / 2) / innerWidth,
    yRatio: (rect.y + rect.height / 2) / innerHeight,
  };
})()
""",
            "returnByValue": True,
        },
    )
    button_position = button_result.get("result", {}).get("value")
    if not isinstance(button_position, dict):
        return False
    x_ratio = button_position.get("xRatio")
    y_ratio = button_position.get("yRatio")
    if not isinstance(x_ratio, (int, float)) or not isinstance(y_ratio, (int, float)):
        return False

    page = pages[launcher.get("parentId")]
    page_ws_url = page.get("webSocketDebuggerUrl")
    if not isinstance(page_ws_url, str):
        return False
    launcher_url = json.dumps(str(launcher.get("url", "")))
    normalized_page_ws_url = _normalize_local_ws_url(page_ws_url)
    frame_result = _command_target(
        normalized_page_ws_url,
        "Runtime.evaluate",
        {
            "expression": f"""
(() => {{
{_LAUNCHER_FRAME_LOOKUP_SCRIPT}
  const frame = findLauncherFrame({launcher_url});
  if (!frame) return null;
  frame.scrollIntoView({{block: 'center', inline: 'center'}});
  return true;
}})()
""",
            "returnByValue": True,
        },
    )
    if frame_result.get("result", {}).get("value") is not True:
        return False

    click_position: object = None
    for _ in range(20):
        time.sleep(0.05)
        frame_result = _command_target(
            normalized_page_ws_url,
            "Runtime.evaluate",
            {
                "expression": f"""
(() => {{
{_LAUNCHER_FRAME_LOOKUP_SCRIPT}
  const frame = findLauncherFrame({launcher_url});
  if (!frame) return null;
  const rect = frame.getBoundingClientRect();
  const x = rect.x + rect.width * {float(x_ratio)};
  const y = rect.y + rect.height * {float(y_ratio)};
  return x >= 0 && x < innerWidth && y >= 0 && y < innerHeight ? {{x, y}} : null;
}})()
""",
                "returnByValue": True,
            },
        )
        click_position = frame_result.get("result", {}).get("value")
        if isinstance(click_position, dict):
            break
    if not isinstance(click_position, dict):
        return False
    x = click_position.get("x")
    y = click_position.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return False
    try:
        _dispatch_mouse_click(normalized_page_ws_url, float(x), float(y))
    except CdpTimeoutError as exc:
        raise PortalStartSubmissionError(str(exc)) from exc
    return True


def reload_game_page(
    port: int,
    page_title: str | None = None,
    *,
    allow_observation: bool = False,
) -> None:
    """Reload the top-level page that owns the active game iframe."""
    try:
        _run_top_page_operation(
            port,
            page_title,
            lambda ws_url: _command_target(ws_url, "Page.reload"),
            allow_observation=allow_observation,
        )
    finally:
        _invalidate_target_pair(port, page_title, allow_observation=allow_observation)


def _owning_page(
    target: dict[str, Any], targets_by_id: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    ancestor_ids: list[str] = []
    visited: set[str] = set()
    parent_id = target.get("parentId")
    while isinstance(parent_id, str) and parent_id not in visited:
        visited.add(parent_id)
        ancestor_ids.append(parent_id)
        parent = targets_by_id.get(parent_id)
        if parent is None:
            return None, tuple(ancestor_ids)
        if parent.get("type") == "page":
            return parent, tuple(ancestor_ids)
        parent_id = parent.get("parentId")
    return None, tuple(ancestor_ids)


def discover_game_targets(
    targets: list[dict[str, Any]], page_title: str | None = None
) -> tuple[DiscoveredGameTarget, ...]:
    """Recognize game clients and resolve their top-level portal pages."""
    targets_by_id = {
        target_id: target for target in targets if isinstance((target_id := target.get("id")), str)
    }
    discovered: list[DiscoveredGameTarget] = []
    for frame in targets:
        if frame.get("type") != "iframe":
            continue
        portal = next(
            (
                candidate
                for candidate in PORTALS
                if candidate.matches_game_frame_url(frame.get("url", ""))
            ),
            None,
        )
        if portal is None:
            continue
        page, ancestor_ids = _owning_page(frame, targets_by_id)
        if page is None or not portal.matches_page_url(page.get("url", "")):
            continue
        frame_id = frame.get("id")
        page_id = page.get("id")
        frame_ws = frame.get("webSocketDebuggerUrl")
        page_ws = page.get("webSocketDebuggerUrl")
        if (
            not isinstance(frame_id, str)
            or not isinstance(page_id, str)
            or not isinstance(frame_ws, str)
            or not isinstance(page_ws, str)
        ):
            continue
        discovered.append(
            DiscoveredGameTarget(
                portal=portal.kind,
                game_target_id=frame_id,
                page_target_id=page_id,
                game_ws_url=_normalize_local_ws_url(frame_ws),
                page_ws_url=_normalize_local_ws_url(page_ws),
                ancestor_ids=ancestor_ids,
                support_level=portal.support_level,
                page_title=str(page.get("title", "")),
                page_url=str(page.get("url", "")),
                game_url=str(frame.get("url", "")),
            )
        )
    if page_title is None:
        return tuple(discovered)

    title_filter = page_title.casefold()
    title_matches = [
        target for target in discovered if title_filter in target.page_title.casefold()
    ]
    matches = title_matches or [
        target for target in discovered if title_filter in target.page_url.casefold()
    ]
    return tuple(matches)


def _find_frame_by_name(frame_tree: object, name: str) -> dict[str, Any] | None:
    if not isinstance(frame_tree, dict):
        return None
    frame = frame_tree.get("frame")
    if isinstance(frame, dict) and frame.get("name") == name:
        return frame
    children = frame_tree.get("childFrames")
    if not isinstance(children, list):
        return None
    return next(
        (found for child in children if (found := _find_frame_by_name(child, name)) is not None),
        None,
    )


def dismiss_pogo_inactivity_prompt(port: int, page_title: str | None = None) -> bool:
    """Dismiss Pogo's exact inactivity prompt with trusted page-level input."""
    targets = _load_targets(port)
    matches = [
        target
        for target in discover_game_targets(targets, page_title)
        if target.portal is GamePortal.POGO
    ]
    if len(matches) != 1:
        return False
    page_ws_url = matches[0].page_ws_url
    frame_tree_result = _command_target(page_ws_url, "Page.getFrameTree")
    sdk_frame = _find_frame_by_name(frame_tree_result.get("frameTree"), "gameBrick")
    frame_id = sdk_frame.get("id") if sdk_frame is not None else None
    if not isinstance(frame_id, str):
        return False
    world = _command_target(
        page_ws_url,
        "Page.createIsolatedWorld",
        {
            "frameId": frame_id,
            "worldName": "farm-merge-valet-pogo-keepalive",
            "grantUniveralAccess": False,
        },
    )
    context_id = world.get("executionContextId")
    if not isinstance(context_id, int):
        return False
    inner_result = _command_target(
        page_ws_url,
        "Runtime.evaluate",
        {
            "contextId": context_id,
            "expression": r"""
(() => {
  const dialogs = [...document.querySelectorAll(
    '[role="dialog"], dialog, [aria-modal="true"]')];
  const dialog = dialogs.find((candidate) =>
    /Still Playing\?/i.test(candidate.innerText || ''));
  const button = dialog && [...dialog.querySelectorAll('button')].find((candidate) =>
    candidate.innerText.trim().toUpperCase() === 'CONTINUE' && !candidate.disabled);
  if (!button || button.offsetWidth <= 0 || button.offsetHeight <= 0) return false;
  button.click();
  return true;
})()
""",
            "returnByValue": True,
        },
    )
    return inner_result.get("result", {}).get("value") is True


def _select_target_pair(
    targets: list[dict[str, Any]],
    page_title: str | None = None,
    *,
    allow_observation: bool = False,
) -> tuple[str, str]:
    """Return a single eligible game and owning-page target pair."""
    discovered = discover_game_targets(targets, page_title)
    pairs = [
        (target.game_ws_url, target.page_ws_url)
        for target in discovered
        if allow_observation or target.support_level is PortalSupportLevel.AUTOMATION
    ]

    if len(pairs) == 1:
        return pairs[0]
    if len(pairs) > 1:
        raise CdpConnectionError(
            "Multiple matching Farm Merge Valley tabs are open. Narrow the page target setting "
            "or close the extra game tabs so board state and screen geometry are unambiguous."
        )

    if discovered:
        portals = ", ".join(sorted({target.portal.value for target in discovered}))
        raise CdpConnectionError(
            f"Recognized an observation-only Farm Merge Valley target ({portals}); "
            "automation is not enabled for that portal."
        )
    raise CdpConnectionError(
        "The browser's remote debugging endpoint is reachable, but no Farm Merge Valley iframe "
        "paired with a matching supported portal page is open. Has the game been loaded, and "
        "does the configured page target match that tab?"
    )


def has_game_target_pair(
    port: int, page_title: str | None = None, *, allow_observation: bool = False
) -> bool:
    """Return whether exactly one automation-enabled game target pair is open."""
    try:
        _select_target_pair(_load_targets(port), page_title, allow_observation=allow_observation)
    except CdpConnectionError:
        return False
    return True


def _target_pair(
    port: int, page_title: str | None = None, *, allow_observation: bool = False
) -> _TargetPair:
    key = (port, page_title, allow_observation)
    with _target_pairs_lock:
        cached = _target_pairs.get(key)
    if cached is not None:
        return cached

    selected = _select_target_pair(
        _load_targets(port), page_title, allow_observation=allow_observation
    )
    with _target_pairs_lock:
        return _target_pairs.setdefault(key, selected)


def _invalidate_target_pair(
    port: int, page_title: str | None = None, *, allow_observation: bool = False
) -> None:
    with _target_pairs_lock:
        pair = _target_pairs.pop((port, page_title, allow_observation), None)
    if pair is not None:
        _close_sessions(pair)


def find_game_frame_target(
    port: int, page_title: str | None = None, *, allow_observation: bool = False
) -> str:
    """Return the game iframe target paired with its owning portal page."""
    game_ws, _ = _target_pair(port, page_title, allow_observation=allow_observation)
    return game_ws


def find_top_page_target(
    port: int, page_title: str | None = None, *, allow_observation: bool = False
) -> str:
    """Returns the `webSocketDebuggerUrl` for the top-level portal page
    hosting the game -- as opposed to the game's own cross-origin
    iframe (`find_game_frame_target`).

    Used for browser-level controls that cannot run inside the cross-origin
    game frame.
    """
    _, page_ws = _target_pair(port, page_title, allow_observation=allow_observation)
    return page_ws


def run_game_frame_operation(
    port: int,
    page_title: str | None,
    operation: Callable[[str], _T],
    *,
    retry: bool = True,
    allow_observation: bool = False,
) -> _T:
    """Run against the cached game target, refreshing it once on failure."""
    attempts = 2 if retry else 1
    for attempt in range(attempts):
        try:
            return operation(
                find_game_frame_target(port, page_title, allow_observation=allow_observation)
            )
        except CdpCancelledError:
            raise
        except CdpConnectionError:
            _invalidate_target_pair(port, page_title, allow_observation=allow_observation)
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
    *,
    allow_observation: bool = False,
) -> _T:
    for attempt in range(2):
        try:
            return operation(
                find_top_page_target(port, page_title, allow_observation=allow_observation)
            )
        except CdpCancelledError:
            raise
        except CdpConnectionError:
            _invalidate_target_pair(port, page_title, allow_observation=allow_observation)
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
