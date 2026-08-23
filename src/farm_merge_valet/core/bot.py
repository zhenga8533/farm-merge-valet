"""Stateful crate-claiming and merge automation loop."""

from __future__ import annotations

import logging
import time
from enum import Enum, auto
from threading import Event

import keyboard
import numpy as np

from farm_merge_valet.actions.input import click, drag
from farm_merge_valet.capture.screen import capture_region
from farm_merge_valet.capture.window import WindowActivationError, WindowRegion, find_window
from farm_merge_valet.cdp.board_store import arm_board_store, read_board_state
from farm_merge_valet.cdp.client import CdpConnectionError
from farm_merge_valet.cdp.scene_geometry import SceneCalibration, read_scene_calibration
from farm_merge_valet.config import settings
from farm_merge_valet.core.board import (
    BoardGrid,
    Cell,
    CellKind,
    GridCoord,
    ItemRef,
    MergeAction,
    MergeActionKind,
    plan_merge_action,
)
from farm_merge_valet.core.board_scan import discover_blueprint_items
from farm_merge_valet.core.environment import initialize_environment, pan
from farm_merge_valet.core.viewport import viewport_layout
from farm_merge_valet.vision.matcher import Match, find_best_scaled_match, load_template

logger = logging.getLogger(__name__)

# Non-item `blueprintID` values from the game's live board state (see
# cdp/board_store.py) that map to a known CellKind rather than an ItemRef.
_LIVE_STATE_CELL_KIND = {
    "empty": CellKind.EMPTY,
    "area_cloud": CellKind.CLOUD,
    "premium_cloud": CellKind.CLOUD,
}

# Structure anchors do not describe their full footprint in the cell map.
# These observed offsets are relative to each anchor; explicit footprints
# avoid hiding unrelated empty cells around one-cell structures.
_TWO_BY_TWO_FOOTPRINT = frozenset({(-1, -1), (0, -1), (-1, 0), (0, 0)})
_STRUCTURE_FOOTPRINTS = {
    "bakery": _TWO_BY_TWO_FOOTPRINT,
    "market": _TWO_BY_TWO_FOOTPRINT,
    "trainstation": _TWO_BY_TWO_FOOTPRINT,
    "likes_billboard": _TWO_BY_TWO_FOOTPRINT,
    "traintrack_stop": frozenset({(0, 0)}),
    "delivery_truck": frozenset({(0, 0)}),
    "delivery_cargo": frozenset({(0, 0)}),
}


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
        self._merge_five_overrides = merge_five_overrides or {}
        self.paused = False
        self._quit_requested = False
        self._interrupt_event = Event()
        self._resume_requested = Event()
        self.phase = Phase.CLAIM_CRATES
        # Fixed UI templates are matched directly across plausible scales.
        self._supply_crate_template = load_template(
            settings.templates_dir / "ui" / "supply_crate.png"
        )

        # blueprintID (e.g. "wheat_1") -> ItemRef for interpreting live
        # board state. Item images are not used to scan board contents.
        self._blueprint_items = discover_blueprint_items(settings.templates_dir / "items")
        self._board_store_armed = False
        self._last_board_store_status: str | None = None

        # Recomputed from live rendering and DOM state during merge steps.
        # None means there is not enough current state to fit the mapping.
        self._scene_calibration: SceneCalibration | None = None
        self.board = BoardGrid()

    def _set_phase(self, phase: Phase) -> None:
        if phase != self.phase:
            logger.info("Phase %s -> %s", self.phase.name, phase.name)
            self.phase = phase

    def initialize(self) -> bool:
        """Normalize the view and discard board knowledge from before a
        startup or pause. Blocking; takes a few seconds."""
        if not initialize_environment(self._interrupt_event):
            return False
        self.board = BoardGrid()
        self._board_store_armed = False
        self._last_board_store_status = None
        self._scene_calibration = None
        return True

    def _refresh_calibration(self, region: WindowRegion) -> None:
        """Recompute `_scene_calibration` for the current frame -- see its
        docstring in `__init__`."""
        try:
            self._scene_calibration = read_scene_calibration(
                settings.cdp_port, region, settings.window_title
            )
        except CdpConnectionError as exc:
            logger.debug("Could not read scene geometry: %s", exc)
            self._scene_calibration = None

    def _ensure_board_store_armed(self) -> None:
        """Best-effort, idempotent: locate and retain the active board map."""
        if self._board_store_armed:
            return
        try:
            result = arm_board_store(settings.cdp_port, settings.window_title)
        except CdpConnectionError as exc:
            logger.debug("Could not arm live board-state capture: %s", exc)
            return
        if result != self._last_board_store_status:
            logger.info("Live board-state capture: %s", result)
            self._last_board_store_status = result
        if result.startswith("found") or result == "already-captured":
            self._board_store_armed = True

    def _sync_board_from_live_state(self) -> bool:
        """Replace `self.board` outright with the game's own live cell
        data (see `cdp/board_store.py`) -- exact and per-player-current,
        the sole board-content source. Returns False (no board knowledge
        touched) if a live reference hasn't been captured yet.
        """
        try:
            raw = read_board_state(settings.cdp_port, settings.window_title)
        except CdpConnectionError as exc:
            logger.debug("Could not read live board state: %s", exc)
            self._board_store_armed = False
            return False
        if raw is None:
            self._board_store_armed = False
            return False

        board = BoardGrid()
        structure_cells: set[GridCoord] = set()
        empty_coords: set[GridCoord] = set()

        for coord, blueprint_id in raw.items():
            item = self._blueprint_items.get(blueprint_id)
            if item is not None:
                board.set_cell(coord, Cell(kind=CellKind.ITEM, item=item))
                continue
            if blueprint_id == "empty":
                # Deferred to the second pass below, once every
                # structure's anchor cell (found later in this same
                # iteration) is known.
                empty_coords.add(coord)
                continue
            kind = _LIVE_STATE_CELL_KIND.get(blueprint_id)
            if kind is not None:
                board.set_cell(coord, Cell(kind=kind))
            elif footprint := _STRUCTURE_FOOTPRINTS.get(blueprint_id):
                board.set_cell(coord, Cell(kind=CellKind.STRUCTURE))
                col, row = coord
                structure_cells.update((col + dc, row + dr) for dc, dr in footprint)
            else:
                # Not a known item, marker, or building -- assumed a
                # transient product (e.g. "egg") waiting to be collected.
                board.set_cell(coord, Cell(kind=CellKind.PRODUCT))

        for coord in empty_coords:
            kind = CellKind.STRUCTURE if coord in structure_cells else CellKind.EMPTY
            board.set_cell(coord, Cell(kind=kind))

        self.board = board
        logger.debug("Synced %d cell(s) from live game state.", len(board.known_coords()))
        return True

    def _prefers_five(self, item: ItemRef) -> bool:
        return self._merge_five_overrides.get(item, settings.prefer_merge_five)

    def _grid_to_pixel(self, coord: GridCoord) -> tuple[int, int]:
        assert self._scene_calibration is not None
        return self._scene_calibration.to_pixel(coord)

    # Actions are chosen in this order across every item type present:
    # complete a ready merge before fixing an over-sized cluster, and fix
    # that before spending a step just gathering toward a future merge --
    # see `core/board.plan_merge_action`.
    _MERGE_ACTION_PRIORITY = {
        MergeActionKind.TRIGGER: 0,
        MergeActionKind.DEGROUP: 1,
        MergeActionKind.GATHER: 2,
    }

    def _plan_merge_actions(self) -> list[MergeAction]:
        return [
            action
            for item in self.board.items_present()
            if (action := plan_merge_action(self.board, item, prefer_five=self._prefers_five(item)))
            is not None
        ]

    def _is_action_visible(self, action: MergeAction, frame_shape: tuple[int, ...]) -> bool:
        """Whether both ends of `action`'s drag land within the currently
        visible, non-UI-chrome region of the captured frame.

        Live board state includes cells outside the viewport, so both drag
        endpoints must be inside the configured clickable board area.
        """
        height, width = frame_shape[:2]
        layout = viewport_layout(width, height)
        for coord in (action.start, action.end):
            x, y = self._grid_to_pixel(coord)
            if not layout.is_actionable(x, y):
                return False
        return True

    # Each attempt is one measured drag. This bounds failures while allowing
    # enough attempts to cross the observed scrollable range.
    _MAX_SCROLL_ATTEMPTS = 12

    def _try_merge(self, region: WindowRegion, frame: np.ndarray) -> bool:
        """Execute the best visible action, or pan toward an off-screen one."""
        actions = self._plan_merge_actions()
        if not actions:
            return False

        visible = [a for a in actions if self._is_action_visible(a, frame.shape)]
        if visible:
            best = min(visible, key=lambda a: self._MERGE_ACTION_PRIORITY[a.kind])
            return self._execute_merge_action(region, best)

        best_offscreen = min(actions, key=lambda a: self._MERGE_ACTION_PRIORITY[a.kind])
        col = (best_offscreen.start[0] + best_offscreen.end[0]) // 2
        row = (best_offscreen.start[1] + best_offscreen.end[1]) // 2
        return self._scroll_to_reveal(region, (col, row))

    def _scroll_to_reveal(self, region: WindowRegion, target: GridCoord) -> bool:
        """Pan until the target is actionable, remeasuring after every drag.

        The drag-to-camera relationship is not assumed to be linear. If a drag
        does not reduce the distance, the next attempt reverses direction.
        """
        toward_bottom: bool | None = None
        best_distance: float | None = None
        for _ in range(self._MAX_SCROLL_ATTEMPTS):
            if self._quit_requested or self.paused:
                return False
            self._refresh_calibration(region)
            if self._scene_calibration is None:
                return False

            x, y = self._grid_to_pixel(target)
            layout = viewport_layout(region.width, region.height)
            if layout.is_actionable(x, y):
                return True

            desired_y = (layout.board.top + layout.board.bottom) / 2
            distance = abs(y - desired_y)
            if toward_bottom is None:
                toward_bottom = y < desired_y
            elif best_distance is not None and distance >= best_distance:
                toward_bottom = not toward_bottom
            best_distance = distance
            if not pan(
                region,
                toward_bottom=toward_bottom,
                repeats=1,
                stop_event=self._interrupt_event,
            ):
                return False
        logger.warning(
            "Could not scroll %s into view within %d attempts.",
            target,
            self._MAX_SCROLL_ATTEMPTS,
        )
        return False

    def _execute_merge_action(self, region: WindowRegion, action: MergeAction) -> bool:
        if self._interrupt_event.is_set():
            return False
        item = action.item
        if action.kind is MergeActionKind.TRIGGER:
            logger.info(
                "Merging %d x %s (%s tier %d)",
                len(action.cluster),
                item.name,
                item.category,
                item.tier,
            )
        elif action.kind is MergeActionKind.DEGROUP:
            logger.info(
                "Degrouping 1 x %s (%s tier %d) to avoid an over-sized merge",
                item.name,
                item.category,
                item.tier,
            )
        else:
            logger.info(
                "Gathering 1 x %s (%s tier %d) toward a merge", item.name, item.category, item.tier
            )
        drag(region, self._grid_to_pixel(action.start), self._grid_to_pixel(action.end))
        if action.kind is MergeActionKind.TRIGGER:
            for coord in action.cluster:
                self.board.clear_cell(coord)
        else:
            # Relocation results are intentionally unknown until the next live sync.
            self.board.clear_cell(action.start)
            self.board.clear_cell(action.end)
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

        # Arming is a no-op after the first success. The live board read stays
        # current on every phase because decisions depend on authoritative data.
        self._ensure_board_store_armed()
        live_synced = self._sync_board_from_live_state()
        if not live_synced:
            logger.warning("Live board state is unavailable; skipping this step.")
            return

        # Only merge actions need grid-to-screen geometry.
        if self.phase is Phase.MERGE:
            self._refresh_calibration(region)

        board_needs_merge = self._board_needs_merge()

        if self.phase is Phase.CLAIM_CRATES:
            self._step_claim_crates(
                region,
                frame,
                board_needs_merge=board_needs_merge,
            )
        elif self.phase is Phase.MERGE:
            self._step_merge(region, frame, board_needs_merge=board_needs_merge)

    def _board_needs_merge(self) -> bool:
        """Whether crate claiming should yield to merge-space recovery.

        The caller must have synchronized authoritative live board state.
        """
        empty_count = len(self.board.find_empty())
        if empty_count == 0:
            return True
        return empty_count <= settings.merge_empty_cell_reserve and bool(self._plan_merge_actions())

    # The crate icon can remain visible at zero supply, so bound each burst.
    _MAX_CRATE_CLICKS_PER_STEP = 50

    def _find_supply_crate(self, frame: np.ndarray) -> Match | None:
        """Verify the crate button inside its fixed bottom-center region."""
        height, width = frame.shape[:2]
        crate = viewport_layout(width, height).crate
        crop = frame[crate.top : crate.bottom, crate.left : crate.right]
        match = find_best_scaled_match(
            crop,
            self._supply_crate_template,
            settings.match_confidence,
        )
        if match is None:
            return None
        return Match(
            x=crate.left + match.x,
            y=crate.top + match.y,
            width=match.width,
            height=match.height,
            confidence=match.confidence,
        )

    def _step_claim_crates(
        self,
        region: WindowRegion,
        frame: np.ndarray,
        *,
        board_needs_merge: bool,
    ) -> None:
        """Claim crates back-to-back until the crate icon stops matching or
        the board fills up, instead of one click per outer step.

        The crate button is a fixed UI element, not board content, so its
        on-screen position only needs (re-)finding once per batch of
        `crate_click_batch_size` clicks, not before every single one --
        it isn't going to have moved a click later.
        """
        if board_needs_merge:
            logger.info("Board reached the merge-space reserve; switching to merge phase.")
            self._set_phase(Phase.MERGE)
            return

        total_clicks = 0
        while total_clicks < self._MAX_CRATE_CLICKS_PER_STEP:
            # Keep pause and quit responsive during a multi-click burst.
            if self._quit_requested or self.paused:
                return
            match = self._find_supply_crate(frame)
            if match is None:
                return
            batch_size = min(
                settings.crate_click_batch_size,
                self._MAX_CRATE_CLICKS_PER_STEP - total_clicks,
            )
            reserve = settings.merge_empty_cell_reserve if self._plan_merge_actions() else 0
            available = len(self.board.find_empty()) - reserve
            batch_size = min(batch_size, max(0, available))
            if batch_size == 0:
                self._set_phase(Phase.MERGE)
                return
            logger.info(
                "Clicking supply crate up to %d times (confidence=%.2f)",
                batch_size,
                match.confidence,
            )
            for _ in range(batch_size):
                if self._quit_requested or self.paused:
                    return
                click(region, *match.center)
                total_clicks += 1
                if self._interrupt_event.wait(settings.crate_click_settle):
                    return
                if total_clicks >= self._MAX_CRATE_CLICKS_PER_STEP:
                    break

            frame = capture_region(region)
            if not self._sync_board_from_live_state():
                logger.warning("Live board state became unavailable; stopping crate claims.")
                return
            if self._board_needs_merge():
                logger.info("Board reached the merge-space reserve; switching to merge phase.")
                self._set_phase(Phase.MERGE)
                return
        logger.warning(
            "Hit the %d-click burst cap without running out of crates or filling the "
            "board; pausing until the next step.",
            self._MAX_CRATE_CLICKS_PER_STEP,
        )

    def _step_merge(
        self, region: WindowRegion, frame: np.ndarray, *, board_needs_merge: bool
    ) -> None:
        if self._scene_calibration is None:
            # No grid geometry to act against this step (e.g. Chrome's CDP
            # endpoint wasn't readable, or nothing is on screen yet to
            # derive geometry from) -- nothing safe to do but wait for the
            # next step.
            return
        merged = self._try_merge(region, frame)
        if not merged and not board_needs_merge:
            logger.info("Board has space again; switching back to claim-crates phase.")
            self._set_phase(Phase.CLAIM_CRATES)
        elif not merged and not self.board.find_empty():
            logger.error(
                "Board is full and no merge action is available; pausing so a cell can be "
                "freed manually."
            )
            self.paused = True

    def _toggle_pause(self) -> None:
        if self.paused:
            if self._resume_requested.is_set():
                self._resume_requested.clear()
                logger.warning("Resume canceled (hotkey)")
            else:
                self._resume_requested.set()
                logger.warning("Resume requested (hotkey)")
        else:
            self.paused = True
            self._interrupt_event.set()
            self._resume_requested.clear()
            logger.warning("Paused (hotkey)")

    def request_quit(self) -> None:
        logger.warning("Quit requested (hotkey)")
        self._quit_requested = True
        self.paused = True
        self._interrupt_event.set()
        self._resume_requested.clear()

    def run_forever(self) -> None:
        # Global hotkeys remain reachable while Chrome has focus. If OS hook
        # registration fails, Ctrl+C remains the terminal-focused fallback.
        try:
            keyboard.add_hotkey(settings.pause_hotkey, self._toggle_pause)
            keyboard.add_hotkey(settings.quit_hotkey, self.request_quit)
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

        needs_initialization = not settings.start_paused
        if settings.start_paused:
            self.paused = True
            self._interrupt_event.set()
            logger.warning(
                "Starting paused -- press %s to begin (this defers environment "
                "setup too, so nothing touches the game until then).",
                settings.pause_hotkey,
            )
        logger.info("Starting bot loop (interval=%.1fs)", settings.loop_interval)
        try:
            while not self._quit_requested:
                if self.paused:
                    if self._resume_requested.is_set():
                        self._resume_requested.clear()
                        self._interrupt_event.clear()
                        self.paused = False
                        logger.warning("Resuming (hotkey) -- re-initializing environment.")
                        try:
                            initialized = self.initialize()
                        except (CdpConnectionError, LookupError, WindowActivationError) as exc:
                            logger.warning(
                                "%s Still paused; press the resume hotkey to retry.", exc
                            )
                            initialized = False
                        if not initialized or self._interrupt_event.is_set():
                            self.paused = True
                            self._interrupt_event.set()
                            continue
                        needs_initialization = False
                        logger.warning("Resumed")
                        continue
                    time.sleep(0.1)
                    continue
                if needs_initialization:
                    try:
                        initialized = self.initialize()
                    except (CdpConnectionError, LookupError, WindowActivationError) as exc:
                        logger.warning("%s Retrying initialization next iteration.", exc)
                        time.sleep(settings.loop_interval)
                        continue
                    if not initialized:
                        continue
                    needs_initialization = False
                try:
                    self.step()
                except (LookupError, WindowActivationError) as exc:
                    logger.warning("%s Retrying next iteration.", exc)
                self._interrupt_event.wait(settings.loop_interval)
        except KeyboardInterrupt:
            logger.info("Stopped by user.")
        finally:
            keyboard.unhook_all()
