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
    template = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if template is None:
        raise FileNotFoundError(f"Could not read template image: {path}")
    return template


def find_best_match(frame: np.ndarray, template: np.ndarray, min_confidence: float) -> Match | None:
    """Locate the best occurrence of `template` within `frame`.

    Returns None if the best match's confidence is below `min_confidence`.
    """
    result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < min_confidence:
        return None
    height, width = template.shape[:2]
    x, y = max_loc
    return Match(x=x, y=y, width=width, height=height, confidence=float(max_val))
