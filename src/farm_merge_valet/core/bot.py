"""Main automation loop.

This is intentionally a thin skeleton: locate the game window, capture a
frame, decide on an action via vision matching, act, repeat. Game-specific
strategy (what to merge, when to expand, etc.) should be layered on top of
`step()` as the project grows.
"""

from __future__ import annotations

import logging

from farm_merge_valet.capture.screen import capture_region
from farm_merge_valet.capture.window import WindowRegion, find_window
from farm_merge_valet.config import settings
from farm_merge_valet.core.stats import RunStats

logger = logging.getLogger(__name__)


class Bot:
    def __init__(self) -> None:
        self.stats = RunStats()
        self._region: WindowRegion | None = None

    def locate_window(self) -> WindowRegion:
        self._region = find_window(settings.window_title)
        logger.info("Located target window: %s", self._region)
        return self._region

    def step(self) -> None:
        """Run a single perceive-decide-act iteration."""
        if self._region is None:
            self.locate_window()
        assert self._region is not None

        frame = capture_region(self._region)
        logger.debug("Captured frame: %sx%s", frame.shape[1], frame.shape[0])

        # TODO: run vision matching against known templates and dispatch
        # to game-specific action handlers. Left as a stub for now.

        self.stats.iterations += 1

    def run_forever(self) -> None:
        import time

        logger.info("Starting bot loop (interval=%.1fs)", settings.loop_interval)
        try:
            while True:
                self.step()
                time.sleep(settings.loop_interval)
        except KeyboardInterrupt:
            logger.info("Stopped by user.")
