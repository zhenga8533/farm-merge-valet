"""Read live game state directly from the page's JavaScript context via the
Chrome DevTools Protocol (CDP), instead of inferring it from screen
captures.

Confirmed live: the game persists its camera state (world position + zoom)
to `localStorage['cameraData']` on every frame. Reading that directly gives
an exact, always-correct board-to-screen alignment -- see
`vision/camera_calibration.py` -- replacing the fragile, error-prone
vision-based grid calibration this project started with.

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
# challenge frame) that might also be open.
_GAME_FRAME_URL_MARKER = "devvit.net"


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
        if target.get("type") == "iframe" and _GAME_FRAME_URL_MARKER in target.get("url", ""):
            ws_url = target.get("webSocketDebuggerUrl")
            if ws_url:
                return str(ws_url)

    raise CdpConnectionError(
        "Chrome's remote debugging endpoint is reachable, but no game iframe "
        f"(url containing {_GAME_FRAME_URL_MARKER!r}) is currently open. "
        "Has the game been loaded (clicked 'Play') in that browser?"
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


def read_camera_data(port: int) -> dict | None:
    """Reads and parses the game's live camera state from localStorage.
    Returns None if the key isn't set yet (e.g. game just loaded and
    hasn't rendered a frame). See module docstring for the shape:
    `{"cameraPosition": {"x": ..., "y": ...}, "cameraZoom": ...}`.
    """
    raw = evaluate(port, "localStorage.getItem('cameraData')")
    if not isinstance(raw, str):
        return None
    result: dict = json.loads(raw)
    return result
