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

See docs/automation-methodology.md for the full setup (copying just the
session cookies into that separate profile, rather than a full profile
copy, is enough to stay logged in).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import websockets

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
        and (
            title_filter is None
            or title_filter in str(target.get("title", "")).casefold()
        )
    }
    pairs: list[tuple[str, str]] = []
    for frame in targets:
        if frame.get("type") != "iframe" or GAME_FRAME_URL_MARKER not in str(
            frame.get("url", "")
        ):
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


def find_game_frame_target(port: int, page_title: str | None = None) -> str:
    """Return the game iframe target paired with its owning Reddit page."""
    game_ws, _ = _select_target_pair(_load_targets(port), page_title)
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
    _, page_ws = _select_target_pair(_load_targets(port), page_title)
    return page_ws


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
                result = response.get("result", {}).get("result", {})
                if "value" not in result:
                    # e.g. the expression evaluated to `undefined`, or the
                    # page threw -- CDP reports the latter under
                    # response["result"]["exceptionDetails"], surfaced
                    # here as a plain None rather than a raised error,
                    # since "the key doesn't exist yet" is an expected,
                    # non-fatal outcome for a fresh/empty localStorage read.
                    return None
                return result["value"]


def evaluate(port: int, expression: str, page_title: str | None = None) -> object:
    """Evaluate `expression` in the game iframe's JS context and return its
    value (must be JSON-serializable -- CDP's `returnByValue` requirement).
    """
    ws_url = find_game_frame_target(port, page_title)
    return asyncio.run(_evaluate_async(ws_url, expression))


def evaluate_top_page(port: int, expression: str, page_title: str | None = None) -> object:
    """Same as `evaluate`, but in the top-level Reddit page's JS context
    rather than the game's own iframe -- for reading things a cross-origin
    iframe can't see about its own placement (see `find_top_page_target`)."""
    ws_url = find_top_page_target(port, page_title)
    return asyncio.run(_evaluate_async(ws_url, expression))
