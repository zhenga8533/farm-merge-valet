"""One-time environment setup: force the game into a known, stable state
(fully zoomed out, panned to the bottom of the board) so grid geometry only
needs to be calibrated once per session rather than re-derived every scan.

A per-scan dynamic calibration (inferring tile pixel geometry from vectors
between matched item icons) turned out to be both slow and fragile in
practice -- see chat history. Since the game's zoom and scroll position are
the only things that actually vary run to run, and both are things *we*
control once the bot is driving input, it's simpler and far more robust to
force them into a fixed, known state once and calibrate against that,
rather than support arbitrary zoom/scroll at every step.

Assumes the user doesn't interact with the game while the bot is running.
If they do -- or the bot is paused and resumed, since that's an explicit
opportunity for them to have scrolled/zoomed -- `initialize_environment`
should be re-run to re-establish the known state.
"""

from __future__ import annotations

import logging
import time

from farm_merge_valet.actions.input import drag, scroll
from farm_merge_valet.capture.window import WindowRegion

logger = logging.getLogger(__name__)

_SETTLE_DELAY = 0.3

# A single `scroll()` call has no gesture-distance limit, so in principle
# one sufficiently large scroll should reach the game's own minimum-zoom
# clamp outright -- but a lone burst isn't reliably registered in full
# (confirmed live: one -300 call sometimes left the view well short of
# minimum zoom, apparently OS/app-side wheel-event coalescing under a
# single rapid burst rather than an actual smaller zoom range). Repeating
# the burst, like `scroll_to_bottom`'s repeated drags below, fixes that:
# once already at minimum zoom, an extra burst is a harmless no-op, so a
# fixed repeat count is safe without needing to poll for convergence.
_ZOOM_OUT_CLICKS = -300
_ZOOM_OUT_REPEATS = 5
_SCROLL_TO_BOTTOM_REPEATS = 5

# Fixed screen-space column reserved for panning drags -- never board
# content, and never a clickable UI button either. Between the board's
# confirmed right extent (`Settings.board_max_pixel_x`, tuned this session
# via live testing of the board-scan cutoffs) and the fixed settings/
# shovel/currency icon column further right (~1855px+ in a 1920px-wide
# capture): dragging from the middle of the screen instead risks the
# start or end point landing on a real tile, which in this game can
# itself be misread as a merge-drag between two board items -- the exact
# gesture `Bot._merge_cluster` uses for real merges.
_DRAG_ANCHOR_X = 1780


def zoom_out_fully(region: WindowRegion) -> None:
    """Zoom out to the game's own minimum zoom via repeated mouse-wheel
    scrolls down -- `scroll()`'s pyautogui convention is that negative
    `clicks` is a downward scroll, confirmed empirically to zoom out
    (rather than in) in this game. See `_ZOOM_OUT_REPEATS` for why this is
    more than one call."""
    cx, cy = region.width // 2, region.height // 2
    for _ in range(_ZOOM_OUT_REPEATS):
        scroll(region, cx, cy, _ZOOM_OUT_CLICKS)
        time.sleep(_SETTLE_DELAY)


def pan(region: WindowRegion, *, toward_bottom: bool, repeats: int = 1) -> None:
    """One or more single-drag pan gestures, anchored at `_DRAG_ANCHOR_X`
    rather than screen-center so the drag can never land on (and
    accidentally interact with) a real tile.

    `toward_bottom` picks the drag direction: confirmed empirically
    against the live game, dragging from lower to higher on screen pans
    the view toward the *top* of the board, so panning toward the
    bottom drags the other way (higher to lower on screen) instead.
    """
    x = min(_DRAG_ANCHOR_X, region.width - 1)
    cy = region.height // 2
    start, end = (cy + 300, cy - 300) if toward_bottom else (cy - 300, cy + 300)
    for _ in range(repeats):
        drag(region, (x, start), (x, end), duration=0.15)
    time.sleep(_SETTLE_DELAY)


def scroll_to_bottom(region: WindowRegion) -> None:
    """Pan the view down to the bottom of the board -- its fixed starting
    area (see module docstring)."""
    pan(region, toward_bottom=True, repeats=_SCROLL_TO_BOTTOM_REPEATS)


def initialize_environment(region: WindowRegion) -> None:
    """Force the game into a known, stable state: fully zoomed out, panned
    to the bottom of the board. Zoom is settled first, since zooming can
    itself shift the visible area (typically toward the zoom center),
    which would undo a scroll done beforehand.
    """
    logger.info("Initializing environment: zooming out and scrolling to bottom.")
    zoom_out_fully(region)
    scroll_to_bottom(region)
