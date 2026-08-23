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

import httpx
import websockets

# The game is a Reddit "devvit" app, embedded as a cross-origin iframe
# inside the Reddit post page -- this is what distinguishes its CDP target
# from the top-level Reddit page and any other iframe (e.g. a reCAPTCHA
# challenge frame) that might also be open. Not underscore-prefixed since
# `cdp/scene_geometry.py` reuses it to identify the *same* iframe from the
# outside (matching its `src`, from the top-level page's own DOM).
GAME_FRAME_URL_MARKER = "devvit.net"

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


def find_game_frame_target(port: int) -> str:
    """Returns the `webSocketDebuggerUrl` for the game's iframe target.

    Raises `CdpConnectionError` if the debugging endpoint isn't reachable
    at all, or no matching iframe target is currently open.
    """
    try:
        resp = httpx.get(f"http://localhost:{port}/json", timeout=5)
        resp.raise_for_status()
        targets = resp.json()
    except httpx.HTTPError as exc:
        raise CdpConnectionError(
            f"Could not reach Chrome's remote debugging endpoint on port {port}. "
            "Is Chrome running with --remote-debugging-port set?"
        ) from exc

    for target in targets:
        if target.get("type") == "iframe" and GAME_FRAME_URL_MARKER in target.get("url", ""):
            ws_url = target.get("webSocketDebuggerUrl")
            if ws_url:
                return str(ws_url)

    raise CdpConnectionError(
        "Chrome's remote debugging endpoint is reachable, but no game iframe "
        f"(url containing {GAME_FRAME_URL_MARKER!r}) is currently open. "
        "Has the game been loaded (clicked 'Play') in that browser?"
    )


def find_top_page_target(port: int) -> str:
    """Returns the `webSocketDebuggerUrl` for the top-level Reddit post
    page hosting the game -- as opposed to the game's own cross-origin
    iframe (`find_game_frame_target`).

    Needed for anything that has to know the iframe's own position/size
    from the *outside*: a cross-origin iframe can never read that about
    itself (`window.frameElement` is null across the origin boundary,
    confirmed live) -- see `cdp/scene_geometry.py`.
    """
    try:
        resp = httpx.get(f"http://localhost:{port}/json", timeout=5)
        resp.raise_for_status()
        targets = resp.json()
    except httpx.HTTPError as exc:
        raise CdpConnectionError(
            f"Could not reach Chrome's remote debugging endpoint on port {port}. "
            "Is Chrome running with --remote-debugging-port set?"
        ) from exc

    for target in targets:
        if target.get("type") == "page" and _REDDIT_PAGE_URL_MARKER in target.get("url", ""):
            ws_url = target.get("webSocketDebuggerUrl")
            if ws_url:
                return str(ws_url)

    raise CdpConnectionError(
        "Chrome's remote debugging endpoint is reachable, but no Reddit post page "
        f"(url containing {_REDDIT_PAGE_URL_MARKER!r}) is currently open."
    )


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


def evaluate(port: int, expression: str) -> object:
    """Evaluate `expression` in the game iframe's JS context and return its
    value (must be JSON-serializable -- CDP's `returnByValue` requirement).
    """
    ws_url = find_game_frame_target(port)
    return asyncio.run(_evaluate_async(ws_url, expression))


def evaluate_top_page(port: int, expression: str) -> object:
    """Same as `evaluate`, but in the top-level Reddit page's JS context
    rather than the game's own iframe -- for reading things a cross-origin
    iframe can't see about its own placement (see `find_top_page_target`)."""
    ws_url = find_top_page_target(port)
    return asyncio.run(_evaluate_async(ws_url, expression))

