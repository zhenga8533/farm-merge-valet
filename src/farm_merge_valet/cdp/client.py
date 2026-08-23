"""Read live game state directly from the page's JavaScript context via the
Chrome DevTools Protocol (CDP), instead of inferring it from screen
captures.

`evaluate`/`evaluate_top_page` are the low-level building blocks; see
`cdp/board_store.py` for board content and `cdp/scene_geometry.py` for
board-to-screen pixel positions -- both read real, live objects out of
the page directly rather than inferring anything from a screen capture.

Requires Chrome to be launched with remote debugging enabled, which Chrome
refuses to do on your normal, default profile (a deliberate security
restriction -- it would let any local process silently take over an
already-authenticated browser). This means running the bot this way needs
a *separate* Chrome instance/profile just for automation:

    chrome.exe --remote-debugging-port=9222 --user-data-dir="<some other dir>"

See the README for setup instructions.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from threading import Lock
from typing import Any, TypeVar

import httpx
import websockets
from websockets.exceptions import WebSocketException

logger = logging.getLogger(__name__)

# The game is a Reddit "devvit" app, embedded as a cross-origin iframe
# inside the Reddit post page -- this is what distinguishes its CDP target
# from the top-level Reddit page and any other iframe (e.g. a reCAPTCHA
# challenge frame) that might also be open. Not underscore-prefixed since
# `cdp/scene_geometry.py` reuses it to identify the *same* iframe from the
# outside (matching its `src`, from the top-level page's own DOM).
GAME_FRAME_URL_MARKER = "playfmv-"

# Distinguishes the actual Reddit post page hosting the game from any
# other unrelated tab that might happen to be open in the same
# remote-debugging Chrome instance -- see `find_top_page_target`.
_REDDIT_PAGE_URL_MARKER = "reddit.com"

_TargetPair = tuple[str, str]
_TargetKey = tuple[int, str | None]
_target_pairs: dict[_TargetKey, _TargetPair] = {}
_target_pairs_lock = Lock()
_T = TypeVar("_T")


class CdpConnectionError(RuntimeError):
    """Raised when the Chrome DevTools Protocol endpoint isn't reachable,
    or the game's iframe target can't be found there.

    Almost always means either: Chrome wasn't launched with
    `--remote-debugging-port`, or the game hasn't been opened (clicked
    "Play") in that browser yet.
    """


def _load_targets(port: int) -> list[dict[str, Any]]:
    try:
        resp = httpx.get(f"http://localhost:{port}/json", timeout=5)
        resp.raise_for_status()
        targets = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CdpConnectionError(
            f"Could not reach Chrome's remote debugging endpoint on port {port}. "
            "Is Chrome running with --remote-debugging-port set?"
        ) from exc
    if not isinstance(targets, list):
        raise CdpConnectionError("Chrome returned an invalid remote-debugging target list.")
    return [target for target in targets if isinstance(target, dict)]


def _select_target_pair(
    targets: list[dict[str, Any]], page_title: str | None = None
) -> tuple[str, str]:
    """Return the game-iframe and owning-page websocket URLs."""
    title_filter = page_title.casefold() if page_title else None
    pages = {
        target.get("id"): target
        for target in targets
        if target.get("type") == "page"
        and _REDDIT_PAGE_URL_MARKER in str(target.get("url", ""))
        and (title_filter is None or title_filter in str(target.get("title", "")).casefold())
    }
    pairs: list[tuple[str, str]] = []
    for frame in targets:
        if frame.get("type") != "iframe" or GAME_FRAME_URL_MARKER not in str(frame.get("url", "")):
            continue
        page = pages.get(frame.get("parentId"))
        frame_ws = frame.get("webSocketDebuggerUrl")
        page_ws = page.get("webSocketDebuggerUrl") if page else None
        if frame_ws and page_ws:
            pairs.append((str(frame_ws), str(page_ws)))

    if len(pairs) == 1:
        return pairs[0]
    if len(pairs) > 1:
        raise CdpConnectionError(
            "Multiple matching Farm Merge Valley tabs are open. Narrow FMV_WINDOW_TITLE "
            "or close the extra game tabs so board state and screen geometry are unambiguous."
        )

    raise CdpConnectionError(
        "Chrome's remote debugging endpoint is reachable, but no Farm Merge Valley iframe "
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
        _target_pairs.pop((port, page_title), None)


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
        except CdpConnectionError:
            _invalidate_target_pair(port, page_title)
            if attempt == 1:
                raise
            logger.info("CDP game target changed; refreshing Chrome targets.")
    raise AssertionError("unreachable")


def _run_top_page_operation(
    port: int,
    page_title: str | None,
    operation: Callable[[str], _T],
) -> _T:
    for attempt in range(2):
        try:
            return operation(find_top_page_target(port, page_title))
        except CdpConnectionError:
            _invalidate_target_pair(port, page_title)
            if attempt == 1:
                raise
            logger.info("CDP page target changed; refreshing Chrome targets.")
    raise AssertionError("unreachable")


async def _evaluate_async(ws_url: str, expression: str) -> object:
    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        request = {
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                # Lets callers pass an async IIFE (e.g. `indexedDB.databases()`
                # is Promise-based) and get its resolved value back directly
                # -- without this, `returnByValue` serializes the pending
                # Promise object itself, not its result. A no-op for plain,
                # non-Promise expressions.
                "awaitPromise": True,
            },
        }
        await ws.send(json.dumps(request))
        while True:
            response = json.loads(await ws.recv())
            if response.get("id") == 1:
                command_result = response.get("result", {})
                if exception := command_result.get("exceptionDetails"):
                    description = exception.get("exception", {}).get("description")
                    raise CdpConnectionError(
                        f"CDP JavaScript evaluation failed: {description or exception.get('text')}"
                    )
                result = command_result.get("result", {})
                if "value" not in result:
                    # Intentional undefined results have no value field.
                    # JavaScript errors are handled above.
                    return None
                return result["value"]


def _evaluate_target(ws_url: str, expression: str) -> object:
    try:
        return asyncio.run(_evaluate_async(ws_url, expression))
    except CdpConnectionError:
        raise
    except (OSError, ValueError, WebSocketException) as exc:
        raise CdpConnectionError("Lost the Chrome DevTools WebSocket connection.") from exc


def evaluate(port: int, expression: str, page_title: str | None = None) -> object:
    """Evaluate `expression` in the game iframe's JS context and return its
    value (must be JSON-serializable -- CDP's `returnByValue` requirement).
    """
    return run_game_frame_operation(
        port,
        page_title,
        lambda ws_url: _evaluate_target(ws_url, expression),
    )


def evaluate_top_page(port: int, expression: str, page_title: str | None = None) -> object:
    """Same as `evaluate`, but in the top-level Reddit page's JS context
    rather than the game's own iframe -- for reading things a cross-origin
    iframe can't see about its own placement (see `find_top_page_target`)."""
    return _run_top_page_operation(
        port,
        page_title,
        lambda ws_url: _evaluate_target(ws_url, expression),
    )
