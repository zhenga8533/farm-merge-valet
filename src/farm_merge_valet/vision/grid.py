"""Derive the isometric board's tile-to-pixel geometry from a live capture.

Screen pixel size per grid tile depends on window size, OS display scaling,
and browser zoom -- none of which are stable across machines or even across
a single session (a resize changes it). So instead of hardcoding a pixel
measurement, this finds the real geometry from whatever's actually on
screen: locate several instances of the same known item template (any
common tier-1 icon will do) at whatever scale they're actually rendered at,
then measure the pixel distance between the closest same-item pairs. Two
identical items can only sit that close together if they're on adjacent
tiles, so that vector *is* one grid step -- no separate calibration
procedure, no assumptions about window geometry.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

Vector = tuple[float, float]


@dataclass(frozen=True)
class GridCalibration:
    """Screen-pixel offset per +1 step along each grid axis, plus the scale
    factor the template had to be resized by to match the live capture."""

    col_step: Vector
    row_step: Vector
    scale: float

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
) -> tuple[float, float] | None:
    """Returns (scale, diff) for whichever candidate scale gets the single
    best (lowest-difference) match somewhere in `frame`, or None if the
    template never fits within the frame at any candidate scale.

    Uses TM_SQDIFF_NORMED, not TM_CCORR_NORMED: OpenCV only supports
    masking with SQDIFF/CCORR (not CCOEFF), and CCORR isn't mean-centered,
    so it can report a deceptively high "match" against completely
    unrelated or even flat/empty content -- confirmed against the crate
    button template earlier and reproduced here against synthetic content
    (see tests/test_grid.py). SQDIFF actually measures pixel difference.
    """
    best: tuple[float, float] | None = None  # (diff, scale)
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


def _dedupe_centers(centers: list[np.ndarray], min_separation: float) -> list[np.ndarray]:
    deduped: list[np.ndarray] = []
    for c in centers:
        if not any(np.linalg.norm(c - d) < min_separation for d in deduped):
            deduped.append(c)
    return deduped


def calibrate_grid(
    frame: np.ndarray,
    template_bgra: np.ndarray,
    *,
    scales: list[float] | None = None,
    max_diff: float = 0.2,
) -> GridCalibration | None:
    """Find the isometric grid's step vectors from wherever `template_bgra`
    (a BGRA item template -- alpha channel required, used as the
    `cv2.matchTemplate` mask) actually appears in `frame`.

    `max_diff` is a TM_SQDIFF_NORMED threshold (lower = more similar; 0 is
    a pixel-perfect match). 0.2 comfortably separates a real match (~0.15,
    measured against the crate button template earlier) from unrelated
    content (~0.34+) with room to spare.

    Returns None if the template can't be confidently matched at any scale,
    or if fewer than two confident, non-overlapping instances are found (at
    least two are needed to measure a step at all).
    """
    scales = scales or [0.5 + 0.05 * i for i in range(31)]  # 0.5x - 2.0x
    template_bgr = template_bgra[:, :, :3]
    template_mask = template_bgra[:, :, 3]

    picked = _best_scale(frame, template_bgr, template_mask, scales)
    if picked is None or picked[0] > max_diff:
        return None
    diff, scale = picked

    resized_bgr = cv2.resize(template_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    resized_mask = cv2.resize(
        template_mask, (resized_bgr.shape[1], resized_bgr.shape[0]), interpolation=cv2.INTER_AREA
    )
    h, w = resized_mask.shape[:2]

    result = cv2.matchTemplate(frame, resized_bgr, cv2.TM_SQDIFF_NORMED, mask=resized_mask)
    result = np.nan_to_num(result, nan=1.0)
    ys, xs = np.where(result <= max_diff)
    if len(xs) < 2:
        return None

    centers = [np.array([x + w / 2, y + h / 2], dtype=float) for x, y in zip(xs, ys, strict=True)]
    deduped = _dedupe_centers(centers, min_separation=min(w, h) * 0.5)
    if len(deduped) < 2:
        return None

    vectors = [
        deduped[j] - deduped[i]
        for i in range(len(deduped))
        for j in range(len(deduped))
        if i != j
    ]
    vectors.sort(key=lambda v: float(np.linalg.norm(v)))

    col_step: np.ndarray | None = None
    row_step: np.ndarray | None = None
    for v in vectors:
        if np.linalg.norm(v) < 2:
            continue
        # A genuine single isometric step always moves diagonally (nonzero
        # x-component); a vector with x~0 is always col_step + row_step
        # summed together (two steps, straight down), never a real single
        # step -- and for a typical ~2:1 isometric tile ratio, that summed
        # vector is actually *shorter* than either true step alone, so it
        # would otherwise be picked first by pure distance sorting. See
        # tests/test_grid.py for the synthetic case that caught this.
        if abs(v[0]) < 2:
            continue
        if col_step is None:
            col_step = v
            continue
        # Accept as the other axis only if it's not (nearly) parallel to
        # col_step -- otherwise it's just a longer step along the same axis.
        cos_angle = abs(np.dot(v, col_step) / (np.linalg.norm(v) * np.linalg.norm(col_step)))
        if cos_angle < 0.9:
            row_step = v
            break
    if col_step is None or row_step is None:
        return None

    return GridCalibration(
        col_step=(float(col_step[0]), float(col_step[1])),
        row_step=(float(row_step[0]), float(row_step[1])),
        scale=scale,
    )
