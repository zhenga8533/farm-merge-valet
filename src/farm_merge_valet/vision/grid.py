"""Measure the board's on-screen item render scale from a live capture.

Board-to-screen tile geometry itself is no longer derived here at all --
see `cdp/scene_geometry.py`, which reads it directly from the game's own
live rendering state instead. What's left is a narrower, unrelated job:
`calibrate_grid` confirms a known template (the always-present "cloud"
background tile) is visible somewhere in a captured frame and reports
what scale factor it had to be resized by to match -- the same factor
`Bot._ensure_item_scale` then uses to resize the fixed-size UI templates
(the supply-crate button, the "need more space" banner) that render at
that same scale.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

Vector = tuple[float, float]


@dataclass(frozen=True)
class GridCalibration:
    """Scale factor the anchor template needed to match the live capture."""

    scale: float


def _best_scale(
    frame: np.ndarray, template_bgr: np.ndarray, template_mask: np.ndarray, scales: list[float]
) -> tuple[float, float] | None:
    """Return ``(diff, scale)`` for the best candidate match, or None if no
    candidate fits inside the frame.

    Uses TM_SQDIFF_NORMED, not TM_CCORR_NORMED: OpenCV only supports
    masking with SQDIFF/CCORR (not CCOEFF), and CCORR is not mean-centered,
    so it can report a high match against flat or unrelated content. SQDIFF
    measures pixel difference directly.
    """
    best: tuple[float, float] | None = None
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
        min_val, _, _, _ = cv2.minMaxLoc(result)
        if best is None or min_val < best[0]:
            best = (float(min_val), scale)
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
    somewhere in `frame`, at whatever scale it's actually rendered at.

    `max_diff` is a TM_SQDIFF_NORMED threshold (lower = more similar; 0 is
    a pixel-perfect match). 0.2 comfortably separates a real match (~0.15,
    measured against the crate button template earlier) from unrelated
    content (~0.34+) with room to spare.

    Returns None if the template can't be confidently matched at any
    scale -- this only confirms the game is actually on screen and
    measures its render scale, it doesn't need multiple instances the way
    an earlier, vector-inference-based approach to tile geometry did.
    """
    scales = scales or [0.5 + 0.05 * i for i in range(31)]  # 0.5x - 2.0x
    template_bgr = template_bgra[:, :, :3]
    template_mask = template_bgra[:, :, 3]

    picked = _best_scale(frame, template_bgr, template_mask, scales)
    if picked is None or picked[0] > max_diff:
        return None
    _diff, scale = picked
    return GridCalibration(scale=scale)
