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

from farm_merge_valet.actions.input import click, drag
from farm_merge_valet.capture.screen import capture_region
from farm_merge_valet.capture.window import WindowActivationError, WindowRegion, find_window
from farm_merge_valet.config import settings
from farm_merge_valet.core.board import BoardGrid, GridCoord, ItemRef, adjacent_pair
from farm_merge_valet.core.board_scan import discover_item_templates, scan_frame
from farm_merge_valet.core.stats import RunStats
from farm_merge_valet.vision.grid import GridCalibration, calibrate_grid
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
    def __init__(self, merge_five_overrides: dict[ItemRef, bool] | None = None) -> None:
        """`merge_five_overrides`: per-item/tier merge-5 preference,
        overriding `settings.prefer_merge_five` for specific items -- e.g.
        `{ItemRef("crops", "wheat", 2): True}` to prefer merge-5 for wheat
        tier 2 specifically regardless of the global default. Not yet
        env-var configurable; pass directly for now.
        """
        self.stats = RunStats()
        self._merge_five_overrides = merge_five_overrides or {}
        self.phase = Phase.CLAIM_CRATES
        self._supply_crate_template = load_template(
            settings.templates_dir / "ui" / "supply_crate.png"
        )
        self._need_space_template = load_template(
            settings.templates_dir / "ui" / "error_need_space.png"
        )
        # Wheat tier 1 as the calibration anchor: it's the very first crop
        # unlocked and one of the most common early spawns, so it's likely
        # to appear more than once on screen. See vision/grid.py.
        self._grid_calibration_template = load_template(
            settings.templates_dir / "items" / "crops" / "wheat" / "tier_1.png"
        )
        self.grid_calibration: GridCalibration | None = None
        # Anchors the pixel<->grid-coordinate conversion: one real match
        # found during calibration, arbitrarily assigned grid coordinate
        # (0, 0) the moment calibration first succeeds. "Grid coordinates"
        # are therefore only meaningful relative to this session's own
        # arbitrary origin, not tied to any absolute board position.
        self._grid_origin: tuple[tuple[float, float], GridCoord] | None = None

        self._item_templates = discover_item_templates(settings.templates_dir / "items")
        self.board = BoardGrid()

    def _set_phase(self, phase: Phase) -> None:
        if phase != self.phase:
            logger.info("Phase %s -> %s", self.phase.name, phase.name)
            self.phase = phase

    def invalidate_grid_calibration(self) -> None:
        """Drop the cached grid calibration so the next step re-derives it.

        Nothing calls this yet -- it's here for when merge logic can detect
        that an action landed somewhere the calibration didn't predict
        (e.g. after a window resize mid-session), rather than continuing to
        act on stale geometry until the bot is restarted.
        """
        self.grid_calibration = None
        self._grid_origin = None

    def _ensure_grid_calibration(self, frame: np.ndarray) -> None:
        """Best-effort, non-blocking: try to derive the board's grid
        geometry from whatever's on screen this step. Calibration doesn't
        gate anything else in step() -- it just isn't available for phases
        that need it (e.g. merge planning) until it succeeds, which
        requires at least two visible wheat-tier-1 tiles.
        """
        if self.grid_calibration is not None:
            return
        calibration = calibrate_grid(frame, self._grid_calibration_template)
        if calibration is None:
            return
        logger.info(
            "Grid calibrated: col_step=%s row_step=%s scale=%.2f",
            calibration.col_step,
            calibration.row_step,
            calibration.scale,
        )
        self.grid_calibration = calibration

        # Establish the origin anchor from the same template, now that we
        # know it's confidently visible -- any single match works equally
        # well, since (0, 0) is arbitrary.
        anchor = find_best_match(frame, self._grid_calibration_template, settings.match_confidence)
        if anchor is not None:
            self._grid_origin = (anchor.center, (0, 0))

    def _scan_board(self, frame: np.ndarray) -> None:
        if self.grid_calibration is None or self._grid_origin is None:
            return
        origin_pixel, origin_coord = self._grid_origin
        updated = scan_frame(
            self.board,
            frame,
            self._item_templates,
            self.grid_calibration,
            origin_pixel,
            origin_coord,
            min_confidence=settings.match_confidence,
        )
        logger.debug("Board scan recorded %d cell(s)", updated)

    def _prefers_five(self, item: ItemRef) -> bool:
        return self._merge_five_overrides.get(item, settings.prefer_merge_five)

    def _grid_to_pixel(self, coord: GridCoord) -> tuple[int, int]:
        assert self.grid_calibration is not None
        assert self._grid_origin is not None
        origin_pixel, origin_coord = self._grid_origin
        dx, dy = self.grid_calibration.grid_to_pixel_delta(
            coord[0] - origin_coord[0], coord[1] - origin_coord[1]
        )
        return (int(origin_pixel[0] + dx), int(origin_pixel[1] + dy))

    def _try_merge(self, region: WindowRegion) -> bool:
        """Find one actionable cluster and merge it. Returns whether an
        action was taken.

        NOTE: assumes merging cascades -- dragging one cluster member onto
        an adjacent same-item member merges the *entire* connected cluster
        at once, not just the two dragged tiles. This is unconfirmed (see
        docs/automation-methodology.md); if merging turns out to be
        selective instead, a cluster bigger than the target size needs to
        be worked down via repeated smaller merges rather than one drag
        consuming everything connected.
        """
        for item in self.board.items_present():
            prefer_five = self._prefers_five(item)
            for cluster in self.board.find_clusters(item):
                if len(cluster) < 3:
                    continue
                # Merging fewer than 5 wastes the merge-5 bonus, and
                # waiting here (rather than merging the 3-4 now) is what
                # keeps the cluster from ever needing to be split -- it
                # either reaches 5 and gets merged then, or stays smaller
                # and just sits there until it does.
                if prefer_five and len(cluster) < 5:
                    continue
                if self._merge_cluster(region, item, cluster):
                    return True
        return False

    def _merge_cluster(self, region: WindowRegion, item: ItemRef, cluster: set[GridCoord]) -> bool:
        pair = adjacent_pair(cluster)
        if pair is None:
            return False
        start, end = pair
        logger.info(
            "Merging %d x %s (%s tier %d)", len(cluster), item.name, item.category, item.tier
        )
        drag(region, self._grid_to_pixel(start), self._grid_to_pixel(end))
        for coord in cluster:
            self.board.clear_cell(coord)
        self.stats.actions_taken += 1
        return True

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

        self._ensure_grid_calibration(frame)
        self._scan_board(frame)

        board_full = (
            find_best_match(frame, self._need_space_template, settings.match_confidence)
            is not None
        )

        if self.phase is Phase.CLAIM_CRATES:
            self._step_claim_crates(region, frame, board_full=board_full)
        elif self.phase is Phase.MERGE:
            self._step_merge(region, board_full=board_full)

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

    def _step_merge(self, region: WindowRegion, *, board_full: bool) -> None:
        merged = self._try_merge(region)
        if not merged and not board_full:
            logger.info("Board has space again; switching back to claim-crates phase.")
            self._set_phase(Phase.CLAIM_CRATES)
        # If board_full and nothing was merged (no known cluster ready to
        # merge yet -- board knowledge is partial, see core/board.py),
        # just stay in this phase and try again next step; there's nothing
        # else productive to do until either a merge becomes possible or
        # space frees up some other way.

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
