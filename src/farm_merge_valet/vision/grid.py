"""Derive the isometric board's tile-to-pixel geometry from a live capture.

`core/environment.py` forces the game into a known, fixed state (fully
zoomed out, panned to the bottom of the board) before the bot starts
acting, specifically so that geometry doesn't have to be rediscovered from
scratch every session. With zoom pinned, the tile grid's *shape* -- the
pixel offset per grid step -- is a fixed constant of that state, not
something to re-infer at runtime: it was measured once, directly, against
a reference capture (by overlaying candidate grid lines on the board's
tile-boundary pattern and adjusting until they lined up -- see chat
history), and is reused as-is here.

An earlier version of this module instead *inferred* the step vectors at
runtime, by matching several instances of a common item template and
clustering the pixel vectors between them. That turned out to be fragile
in practice: a small/low-contrast template (e.g. a young wheat sprout)
produced thousands of spurious matches against grass and other green
content, and even after several rounds of filtering (mirror-pair
requirements, minimum-length floors), it kept locking onto short,
coincidental vectors between unrelated matches rather than genuine
adjacent tiles -- because on a real, busy board there are enough
same-tier item instances that *some* pair of them looks plausible by
chance. None of that problem exists once the geometry itself is a known
constant; only the board's scroll-independent *origin* still needs to be
located per session, via a single confident template match.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

Vector = tuple[float, float]

# Measured once, directly, against a 1920x1020 capture of the game fully
# zoomed out (see `core/environment.zoom_out_fully`): the pixel offset for
# one step along each isometric grid axis. Assumes the window stays this
# size -- if that ever changes, this needs to be remeasured the same way.
#
# Went through two corrections to get here, both confirmed against the
# game's own extracted map data (assets/board_map/ -- see
# tools/template_extraction.py) rather than by eye:
#   1. An initial by-eye ruler measurement gave (80, 50) -- its 1.6:1 x:y
#      ratio didn't match the game's tile art (320x160 = exactly 2:1 for
#      a standard isometric projection), corrected to 2:1 while keeping
#      the same magnitude (~94.3px/step).
#   2. That magnitude itself was still off by ~3.2%: fitting a
#      least-squares regression against 76 real, precisely
#      template-matched cloud-tile positions (matching a background
#      template gives sub-pixel accuracy no by-eye measurement can) gave
#      a magnitude of ~97.3px/step instead. This is the version that
#      finally lines up with the map's own (column, row) coordinates
#      cleanly (integer-valued relative offsets between matched tiles,
#      confirmed live).
REFERENCE_COL_STEP: Vector = (87.045, 43.755)
REFERENCE_ROW_STEP: Vector = (-87.045, 43.755)


@dataclass(frozen=True)
class GridCalibration:
    """Screen-pixel offset per +1 step along each grid axis, the scale
    factor the anchor template had to be resized by to match the live
    capture, and the pixel center where that scaled match was found."""

    col_step: Vector
    row_step: Vector
    scale: float
    anchor: Vector

    def grid_to_pixel_delta(self, d_col: float, d_row: float) -> Vector:
        return (
            self.col_step[0] * d_col + self.row_step[0] * d_row,
            self.col_step[1] * d_col + self.row_step[1] * d_row,
        )

    def pixel_to_grid_delta(self, dx: float, dy: float) -> tuple[float, float]:
        """Inverse of `grid_to_pixel_delta` -- solves the 2x2 linear system
        for (d_col, d_row) given a pixel offset. Returned as floats; round
        to the nearest int once you trust the result is a whole number of
        tile steps."""
        a, b = self.col_step
        c, d = self.row_step
        det = a * d - b * c
        if abs(det) < 1e-6:
            raise ValueError("col_step and row_step are degenerate (parallel)")
        d_col = (dx * d - dy * c) / det
        d_row = (a * dy - b * dx) / det
        return d_col, d_row


def _best_scale(
    frame: np.ndarray, template_bgr: np.ndarray, template_mask: np.ndarray, scales: list[float]
) -> tuple[float, float, Vector] | None:
    """Returns (diff, scale, center) for whichever candidate scale gets the
    single best (lowest-difference) match somewhere in `frame`, or None if
    the template never fits within the frame at any candidate scale.
    `center` is that match's pixel center in `frame` -- returned so callers
    don't need a second, separately-scaled search just to relocate the
    same match (a redundant search at the *unscaled* template size missed
    it entirely once render scale drifted enough, confirmed live).

    Uses TM_SQDIFF_NORMED, not TM_CCORR_NORMED: OpenCV only supports
    masking with SQDIFF/CCORR (not CCOEFF), and CCORR isn't mean-centered,
    so it can report a deceptively high "match" against completely
    unrelated or even flat/empty content -- confirmed against the crate
    button template earlier and reproduced here against synthetic content
    (see tests/test_grid.py). SQDIFF actually measures pixel difference.
    """
    best: tuple[float, float, Vector] | None = None  # (diff, scale, center)
    for scale in scales:
        resized_bgr = cv2.resize(
            template_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
        )
        if resized_bgr.shape[0] > frame.shape[0] or resized_bgr.shape[1] > frame.shape[1]:
            continue
        resized_mask = cv2.resize(
            template_mask,
            (resized_bgr.shape[1], resized_bgr.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
        result = cv2.matchTemplate(frame, resized_bgr, cv2.TM_SQDIFF_NORMED, mask=resized_mask)
        # See find_best_match in vision/matcher.py: TM_SQDIFF_NORMED can
        # divide by zero (-> NaN) against flat/empty regions, and
        # cv2.minMaxLoc doesn't skip NaN.
        result = np.nan_to_num(result, nan=1.0)
        min_val, _, min_loc, _ = cv2.minMaxLoc(result)
        if best is None or min_val < best[0]:
            h, w = resized_mask.shape[:2]
            center = (min_loc[0] + w / 2, min_loc[1] + h / 2)
            best = (float(min_val), scale, center)
    return best


def calibrate_grid(
    frame: np.ndarray,
    template_bgra: np.ndarray,
    *,
    scales: list[float] | None = None,
    max_diff: float = 0.2,
) -> GridCalibration | None:
    """Confirm `template_bgra` (a BGRA item template -- alpha channel
    required, used as the `cv2.matchTemplate` mask) is confidently visible
    somewhere in `frame`, at whatever scale it's actually rendered at, and
    pair that render scale with the fixed reference grid geometry (see
    module docstring).

    `max_diff` is a TM_SQDIFF_NORMED threshold (lower = more similar; 0 is
    a pixel-perfect match). 0.2 comfortably separates a real match (~0.15,
    measured against the crate button template earlier) from unrelated
    content (~0.34+) with room to spare.

    Returns None if the template can't be confidently matched at any
    scale -- this only confirms the game is actually on screen and
    measures its render scale, it doesn't need multiple instances the way
    the old vector-inference approach did.
    """
    scales = scales or [0.5 + 0.05 * i for i in range(31)]  # 0.5x - 2.0x
    template_bgr = template_bgra[:, :, :3]
    template_mask = template_bgra[:, :, 3]

    picked = _best_scale(frame, template_bgr, template_mask, scales)
    if picked is None or picked[0] > max_diff:
        return None
    _diff, scale, anchor = picked

    return GridCalibration(
        col_step=REFERENCE_COL_STEP, row_step=REFERENCE_ROW_STEP, scale=scale, anchor=anchor
    )
