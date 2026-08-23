"""Template matching: locate known UI elements within a captured frame."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

DEFAULT_TEMPLATE_SCALES = tuple(0.5 + 0.05 * index for index in range(31))


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
    high "match" against unrelated or flat content. A template without an
    alpha channel (BGR) falls back to plain, unmasked TM_CCOEFF_NORMED matching.

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


def find_best_scaled_match(
    frame: np.ndarray,
    template: np.ndarray,
    min_confidence: float,
    scales: tuple[float, ...] = DEFAULT_TEMPLATE_SCALES,
) -> Match | None:
    """Locate a fixed UI template without assuming the game's current scale."""
    best: Match | None = None
    frame_height, frame_width = frame.shape[:2]
    for scale in scales:
        if scale <= 0:
            raise ValueError("template scales must be positive")
        if scale == 1.0:
            candidate = template
        else:
            candidate = cv2.resize(
                template,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA,
            )
        height, width = candidate.shape[:2]
        if height > frame_height or width > frame_width:
            continue
        match = find_best_match(frame, candidate, min_confidence=0.0)
        if match is not None and (best is None or match.confidence > best.confidence):
            best = match
    return best if best is not None and best.confidence >= min_confidence else None
