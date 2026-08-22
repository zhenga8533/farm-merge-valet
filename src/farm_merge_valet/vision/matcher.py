"""Template matching: locate known UI elements within a captured frame."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class Match:
    """A located template match, in coordinates relative to the source frame."""

    x: int
    y: int
    width: int
    height: int
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)


def load_template(path: Path) -> np.ndarray:
    """Load a template, preserving its alpha channel (BGRA) if it has one --
    see `find_best_match` for why that matters."""
    template = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if template is None:
        raise FileNotFoundError(f"Could not read template image: {path}")
    return template


def find_best_match(frame: np.ndarray, template: np.ndarray, min_confidence: float) -> Match | None:
    """Locate the best occurrence of `template` within `frame`.

    If `template` has an alpha channel (BGRA, as everything under
    assets/templates/ now is), matching uses only its opaque pixels -- the
    alpha channel passed as a `cv2.matchTemplate` mask -- via
    TM_SQDIFF_NORMED. OpenCV only supports masking with SQDIFF/CCORR, not
    CCOEFF, and CCORR isn't mean-centered so it can report a deceptively
    high "match" against unrelated or even flat content (confirmed against
    both the crate button template and synthetic data -- see
    `vision/grid.py`). A template without an alpha channel (BGR) falls
    back to plain, unmasked TM_CCOEFF_NORMED matching.

    `min_confidence` and the returned `Match.confidence` are always on a
    higher-is-better 0-1 scale regardless of which method ran underneath
    (SQDIFF_NORMED's raw 0 (identical) - 1 (no match) score is inverted to
    `1 - diff` so callers don't need to care which path a template took).

    Returns None if the best match's confidence is below `min_confidence`.
    """
    height, width = template.shape[:2]
    if template.ndim == 3 and template.shape[2] == 4:
        bgr, mask = template[:, :, :3], template[:, :, 3]
        result = cv2.matchTemplate(frame, bgr, cv2.TM_SQDIFF_NORMED, mask=mask)
        # TM_SQDIFF_NORMED can divide by zero (-> NaN) at candidate
        # positions where the masked region sums to zero in both the
        # template and the frame window (e.g. a flat/empty area) --
        # cv2.minMaxLoc doesn't skip NaN, so left alone it can spuriously
        # "win" as the minimum. 1.0 (worst possible score) is a safe
        # stand-in: a real match should score far below that anyway.
        result = np.nan_to_num(result, nan=1.0)
        min_val, _, min_loc, _ = cv2.minMaxLoc(result)
        confidence = 1.0 - min_val
        x, y = min_loc
    else:
        result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        confidence = float(max_val)
        x, y = max_loc

    if confidence < min_confidence:
        return None
    return Match(x=x, y=y, width=width, height=height, confidence=confidence)


def find_all_matches(frame: np.ndarray, template: np.ndarray, min_confidence: float) -> list[Match]:
    """Like `find_best_match`, but returns every non-overlapping match above
    `min_confidence` instead of just the single best one -- e.g. finding
    every board tile holding a given item, not just confirming one exists.

    Same masked-vs-unmasked method selection as `find_best_match`.
    """
    height, width = template.shape[:2]
    confidence_map: np.ndarray
    if template.ndim == 3 and template.shape[2] == 4:
        bgr, mask = template[:, :, :3], template[:, :, 3]
        result = cv2.matchTemplate(frame, bgr, cv2.TM_SQDIFF_NORMED, mask=mask)
        result = np.nan_to_num(result, nan=1.0)
        confidence_map = 1.0 - result
    else:
        confidence_map = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)

    ys, xs = np.where(confidence_map >= min_confidence)
    candidates = sorted(
        zip(xs.tolist(), ys.tolist(), strict=True),
        key=lambda p: -confidence_map[p[1], p[0]],
    )

    matches: list[Match] = []
    for x, y in candidates:
        # Non-max suppression: a true match produces a whole blob of
        # pixels above threshold clustered around it, not a single point,
        # so skip anything overlapping an already-accepted match.
        if any(abs(x - m.x) < width * 0.5 and abs(y - m.y) < height * 0.5 for m in matches):
            continue
        matches.append(
            Match(x=x, y=y, width=width, height=height, confidence=float(confidence_map[y, x]))
        )
    return matches
