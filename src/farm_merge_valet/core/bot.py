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

import keyboard
import numpy as np

from farm_merge_valet.actions.input import click, drag
from farm_merge_valet.capture.screen import capture_region
from farm_merge_valet.capture.window import WindowActivationError, WindowRegion, find_window
from farm_merge_valet.config import settings
from farm_merge_valet.core.board import BoardGrid, GridCoord, ItemRef, adjacent_pair
from farm_merge_valet.core.board_scan import discover_item_templates, scale_templates, scan_frame
from farm_merge_valet.core.environment import initialize_environment
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
        self.paused = False
        self._quit_requested = False
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

        # Scanning is two-pass (see core/board_scan.py): a cheap downscaled
        # sweep locates candidate cells using `_item_templates_scaled`,
        # then each candidate is classified for real using
        # `_item_templates_classify`. Both need the board's true on-screen
        # render scale (`calibration.scale`) folded in, not just
        # `board_scan_scale` -- the captured frame always shows items at
        # their true render scale, so a template scaled only by
        # `board_scan_scale` (implicitly assuming a 1.0 render scale) is
        # the wrong size to match against it. Confirmed live: pass 2
        # classification silently undershot confidence almost everywhere
        # once render scale drifted from 1.0, and pass 1 localization had
        # the same mismatch for the same reason, just less severe. Neither
        # can be built until calibration succeeds (see
        # `_ensure_grid_calibration`), since `calibration.scale` isn't
        # known before that.
        self._item_templates_full = discover_item_templates(settings.templates_dir / "items")
        self._item_templates_scaled: dict[ItemRef, np.ndarray] = {}
        self._item_templates_classify: dict[ItemRef, np.ndarray] = {}
        self.board = BoardGrid()

    def _set_phase(self, phase: Phase) -> None:
        if phase != self.phase:
            logger.info("Phase %s -> %s", self.phase.name, phase.name)
            self.phase = phase

    def invalidate_grid_calibration(self) -> None:
        """Drop the cached grid calibration and any board knowledge tied to
        it, so the next step re-derives everything from scratch. Called by
        `initialize` after re-establishing the known environment state
        (see `core/environment.py`) -- stale cell positions from before a
        pause/resize wouldn't necessarily correspond to the same physical
        tiles anymore, since the origin anchor can land on a different
        real match after the board's contents have changed.
        """
        self.grid_calibration = None
        self._grid_origin = None
        self.board = BoardGrid()

    def initialize(self) -> None:
        """Force the game into the known environment state (fully zoomed
        out, panned to the bottom -- see `core/environment.py`) and
        calibrate against it. Blocking; takes a few seconds. Called once
        before `run_forever`'s loop starts, and again on resume from pause,
        since that's an explicit opportunity for the user to have
        scrolled/zoomed the game themselves.
        """
        region = find_window(settings.window_title)
        initialize_environment(region)
        self.invalidate_grid_calibration()
        frame = capture_region(region)
        self._ensure_grid_calibration(frame)

    def _ensure_grid_calibration(self, frame: np.ndarray) -> None:
        """Best-effort, non-blocking: try to confirm the board's grid
        geometry against whatever's on screen this step. Calibration
        doesn't gate anything else in step() -- it just isn't available
        for phases that need it (e.g. merge planning) until it succeeds,
        which requires the anchor template to be confidently visible.
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
        self._item_templates_classify = scale_templates(
            self._item_templates_full, calibration.scale
        )
        # See __init__: the coarse pass matches against a downscaled
        # *frame*, which already shows items at their true render scale --
        # so the coarse templates need both factors, not just
        # `board_scan_scale` alone.
        self._item_templates_scaled = scale_templates(
            self._item_templates_full, settings.board_scan_scale * calibration.scale
        )
        # (0, 0) is an arbitrary label for wherever the anchor match landed
        # -- `calibrate_grid` already found this position (at the correct
        # render scale) while confirming the template is visible, so no
        # second search is needed here.
        self._grid_origin = (calibration.anchor, (0, 0))

    def _scan_board(self, frame: np.ndarray) -> None:
        if self.grid_calibration is None or self._grid_origin is None:
            return
        origin_pixel, origin_coord = self._grid_origin
        updated = scan_frame(
            self.board,
            frame,
            self._item_templates_scaled,
            self._item_templates_classify,
            self.grid_calibration,
            origin_pixel,
            origin_coord,
            min_confidence=settings.match_confidence,
            coarse_confidence=settings.board_scan_coarse_confidence,
            scale=settings.board_scan_scale,
            max_workers=settings.board_scan_workers,
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

    def _toggle_pause(self) -> None:
        if self.paused:
            # Resuming: re-initialize *before* clearing `paused`, so the
            # main loop doesn't start stepping against a possibly-changed
            # environment (the user pausing is exactly the moment they
            # might have scrolled/zoomed the game themselves) until it's
            # back in the known state. Runs on the hotkey callback thread;
            # blocking here is fine since the main loop is parked.
            logger.warning("Resuming (hotkey) -- re-initializing environment.")
            self.initialize()
            self.paused = False
            logger.warning("Resumed")
        else:
            self.paused = True
            logger.warning("Paused (hotkey)")

    def _request_quit(self) -> None:
        logger.warning("Quit requested (hotkey)")
        self._quit_requested = True

    def run_forever(self) -> None:
        import time

        # Global hotkeys work even when the terminal isn't focused, since
        # the bot spends most of its time driving mouse input into a
        # different window -- Ctrl+C alone wouldn't be reachable then.
        # Registration can fail (e.g. insufficient OS permissions for a
        # low-level keyboard hook); that's a real loss of the pause/quit
        # safety net, not something to silently continue past, but it
        # shouldn't stop the bot from running at all if you'd rather
        # proceed and rely on Ctrl+C instead.
        try:
            keyboard.add_hotkey(settings.pause_hotkey, self._toggle_pause)
            keyboard.add_hotkey(settings.quit_hotkey, self._request_quit)
            logger.info(
                "Hotkeys active: %s to pause/resume, %s to quit.",
                settings.pause_hotkey,
                settings.quit_hotkey,
            )
        except Exception:
            logger.exception(
                "Could not register hotkeys; only Ctrl+C (terminal must be focused) "
                "will stop the bot."
            )

        self.initialize()

        logger.info("Starting bot loop (interval=%.1fs)", settings.loop_interval)
        try:
            while not self._quit_requested:
                if self.paused:
                    time.sleep(0.1)
                    continue
                try:
                    self.step()
                except WindowActivationError as exc:
                    logger.warning("%s Retrying next iteration.", exc)
                    self.stats.errors += 1
                time.sleep(settings.loop_interval)
        except KeyboardInterrupt:
            logger.info("Stopped by user.")
        finally:
            keyboard.unhook_all()
