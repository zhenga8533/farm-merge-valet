"""Main automation loop.

The bot tracks which phase of play it's in (see docs/automation-methodology.md)
and persists that across steps, rather than re-deciding from scratch each
frame: e.g. once the board fills up it switches to the merge phase and
stays there until space frees up again, instead of re-attempting crate
clicks (and re-triggering the "need more space" banner) every iteration.
"""

from __future__ import annotations

import logging
from enum import Enum, auto

import numpy as np

from farm_merge_valet.actions.input import click
from farm_merge_valet.capture.screen import capture_region
from farm_merge_valet.capture.window import WindowActivationError, WindowRegion, find_window
from farm_merge_valet.config import settings
from farm_merge_valet.core.stats import RunStats
from farm_merge_valet.vision.matcher import find_best_match, load_template

logger = logging.getLogger(__name__)


class Phase(Enum):
    """Which stage of the play loop the bot is currently in.

    See docs/automation-methodology.md for the full planned phase order;
    only the first two are implemented so far.
    """

    CLAIM_CRATES = auto()
    MERGE = auto()


class Bot:
    def __init__(self) -> None:
        self.stats = RunStats()
        self.phase = Phase.CLAIM_CRATES
        self._supply_crate_template = load_template(
            settings.templates_dir / "ui" / "supply_crate.png"
        )
        self._need_space_template = load_template(
            settings.templates_dir / "ui" / "error_need_space.png"
        )

    def _set_phase(self, phase: Phase) -> None:
        if phase != self.phase:
            logger.info("Phase %s -> %s", self.phase.name, phase.name)
            self.phase = phase

    def step(self) -> None:
        """Run a single perceive-decide-act iteration for the current phase.

        The target window is (re-)located and brought to the foreground on
        every step, not just once at startup: capture and actions are both
        screen-coordinate based, so if a different window has since become
        foreground, acting without re-checking would silently operate on
        the wrong application.
        """
        region = find_window(settings.window_title)
        frame = capture_region(region)
        logger.debug("Captured frame: %sx%s", frame.shape[1], frame.shape[0])

        board_full = (
            find_best_match(frame, self._need_space_template, settings.match_confidence)
            is not None
        )

        if self.phase is Phase.CLAIM_CRATES:
            self._step_claim_crates(region, frame, board_full=board_full)
        elif self.phase is Phase.MERGE:
            self._step_merge(board_full=board_full)

        self.stats.iterations += 1

    def _step_claim_crates(
        self, region: WindowRegion, frame: np.ndarray, *, board_full: bool
    ) -> None:
        if board_full:
            logger.warning("Board is full; switching to merge phase.")
            self._set_phase(Phase.MERGE)
            return

        match = find_best_match(frame, self._supply_crate_template, settings.match_confidence)
        if match is not None:
            logger.info("Clicking supply crate (confidence=%.2f)", match.confidence)
            click(region, *match.center)
            self.stats.actions_taken += 1
        # NOTE: no reliable way yet to detect "out of crates" (as opposed
        # to "board full") -- the crate icon appears to remain on screen
        # regardless of the held count. See automation-methodology.md.

    def _step_merge(self, *, board_full: bool) -> None:
        # Merge automation isn't implemented yet (see
        # automation-methodology.md phase 2 open design questions), so this
        # phase currently just waits for the board to free up on its own
        # (e.g. the player manually merges) and then resumes crate-claiming.
        if not board_full:
            logger.info("Board has space again; switching back to claim-crates phase.")
            self._set_phase(Phase.CLAIM_CRATES)

    def run_forever(self) -> None:
        import time

        logger.info("Starting bot loop (interval=%.1fs)", settings.loop_interval)
        try:
            while True:
                try:
                    self.step()
                except WindowActivationError as exc:
                    logger.warning("%s Retrying next iteration.", exc)
                    self.stats.errors += 1
                time.sleep(settings.loop_interval)
        except KeyboardInterrupt:
            logger.info("Stopped by user.")
