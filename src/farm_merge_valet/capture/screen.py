"""Screen capture utilities built on `mss`."""

from __future__ import annotations

import numpy as np
from mss import mss

from farm_merge_valet.capture.window import WindowRegion


def capture_region(region: WindowRegion) -> np.ndarray:
    """Capture a screen region and return it as a BGR numpy array (OpenCV format)."""
    with mss() as sct:
        raw = sct.grab(region.as_mss_region)
        # mss gives BGRA; drop the alpha channel for OpenCV-style BGR.
        frame = np.array(raw)[:, :, :3]
    return frame
