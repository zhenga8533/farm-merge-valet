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

# One `scroll()` call has no gesture-distance limit, so a single
# sufficiently large scroll reaches the game's own minimum-zoom clamp in
# one action -- confirmed empirically negative clicks zoom out (see
# `zoom_out_fully`). A `drag()`, in contrast, is limited to one screen's
# worth of on-screen mouse travel per call, so reaching the bottom of a
# board taller than one screen needs several drags in a row; a fixed
# repeat count comfortably larger than any realistic board height is used
# instead of polling for convergence -- once the view is already at the
# bottom, extra drags are harmless no-ops.
_ZOOM_OUT_CLICKS = -300
_SCROLL_TO_BOTTOM_REPEATS = 15


def zoom_out_fully(region: WindowRegion) -> None:
    """Zoom out to the game's own minimum zoom in one action."""
    cx, cy = region.width // 2, region.height // 2
    scroll(region, cx, cy, _ZOOM_OUT_CLICKS)
    time.sleep(_SETTLE_DELAY)


def scroll_to_bottom(region: WindowRegion) -> None:
    """Pan the view down to the bottom of the board -- its fixed starting
    area (see module docstring) -- via repeated drags.

    Confirmed empirically against the live game: dragging from lower to
    higher on screen panned the view toward the *top* of the board (the
    opposite of what's wanted here), so this drags from higher to lower on
    screen instead.
    """
    cx, cy = region.width // 2, region.height // 2
    for _ in range(_SCROLL_TO_BOTTOM_REPEATS):
        drag(region, (cx, cy + 300), (cx, cy - 300), duration=0.15)
    time.sleep(_SETTLE_DELAY)


def initialize_environment(region: WindowRegion) -> None:
    """Force the game into a known, stable state: fully zoomed out, panned
    to the bottom of the board. Zoom is settled first, since zooming can
    itself shift the visible area (typically toward the zoom center),
    which would undo a scroll done beforehand.
    """
    logger.info("Initializing environment: zooming out and scrolling to bottom.")
    zoom_out_fully(region)
    scroll_to_bottom(region)
