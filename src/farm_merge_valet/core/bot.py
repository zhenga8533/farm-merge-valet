"""Main automation loop.

The bot tracks which phase of play it's in (see docs/automation-methodology.md)
and persists that across steps, rather than re-deciding from scratch each
frame: e.g. once the board fills up it switches to the merge phase and
stays there until space frees up again, instead of re-attempting crate
clicks (and re-triggering the "need more space" banner) every iteration.
"""

from __future__ import annotations

import logging
import time
from enum import Enum, auto

import cv2
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
from farm_merge_valet.core.board_scan import (
    discover_background_templates,
    discover_blueprint_items,
    discover_item_templates,
)
from farm_merge_valet.core.environment import initialize_environment, pan
from farm_merge_valet.core.stats import RunStats
from farm_merge_valet.vision.grid import calibrate_grid
from farm_merge_valet.vision.matcher import find_best_match, load_template

logger = logging.getLogger(__name__)

# Non-item `blueprintID` values from the game's live board state (see
# cdp/board_store.py) that map to a known CellKind rather than an ItemRef.
_LIVE_STATE_CELL_KIND = {
    "empty": CellKind.EMPTY,
    "area_cloud": CellKind.CLOUD,
    "premium_cloud": CellKind.CLOUD,
}

# Fixed buildings/decoration blueprint IDs observed live -- necessarily
# incomplete (there's no live signal that distinguishes "permanent
# building" from "harvested product" the way there is for the markers
# above). Anything not in this set and not otherwise recognized is
# assumed to be a transient product (e.g. "egg") rather than a building
# -- low-consequence if wrong, since PRODUCT cells aren't acted on yet
# either way; extend this set as new building types are observed.
_KNOWN_STRUCTURE_BLUEPRINTS = {
    "bakery",
    "market",
    "trainstation",
    "traintrack_stop",
    "delivery_truck",
    "delivery_cargo",
    "likes_billboard",
}

# 8-connected (including diagonals) -- used only to detect whether an
# "empty" cell is actually part of a multi-cell structure's footprint
# (see `_sync_board_from_live_state`). Merge adjacency elsewhere stays
# 4-connected (`core.board.NEIGHBOR_OFFSETS`); footprint and
# merge-touching are different, unrelated relationships.
_FOOTPRINT_NEIGHBOR_OFFSETS = tuple(
    (dc, dr) for dc in (-1, 0, 1) for dr in (-1, 0, 1) if (dc, dr) != (0, 0)
)


def _scale_template(template: np.ndarray, scale: float) -> np.ndarray:
    """Resize a single BGRA template by `scale`, preserving its alpha
    channel -- for the fixed-size UI templates (`supply_crate`,
    `error_need_space`)."""
    if scale == 1.0:
        return template
    h, w = template.shape[:2]
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return cv2.resize(template, new_size, interpolation=cv2.INTER_AREA)


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
        # UI templates render at the board's on-screen scale too, same as
        # items (see `_ensure_item_scale`) -- confirmed live, matching the
        # need-space banner at native scale scored 0.838, just under
        # `match_confidence`. Rescaled once `_item_scale` is known; until
        # then, matching falls back to the native, unscaled template.
        self._supply_crate_template = load_template(
            settings.templates_dir / "ui" / "supply_crate.png"
        )
        self._supply_crate_template_scaled = self._supply_crate_template
        self._need_space_template = load_template(
            settings.templates_dir / "ui" / "error_need_space.png"
        )
        self._need_space_template_scaled = self._need_space_template

        # blueprintID (e.g. "wheat_1") -> ItemRef, for interpreting the
        # game's own live board state (see cdp/board_store.py) -- the sole
        # board-content source; item templates otherwise only feed
        # `_ensure_item_scale`'s render-scale measurement below, not any
        # vision-based board scanning.
        self._item_templates_full = discover_item_templates(settings.templates_dir / "items")
        self._blueprint_items = discover_blueprint_items(self._item_templates_full)
        self._board_store_armed = False
        self._last_board_store_status: str | None = None
        # Only the "cloud" entry is actually used (see `_ensure_item_scale`,
        # an always-present, robust template-matching anchor for measuring
        # render scale) -- not for any vision-based board content scanning.
        self._background_templates = discover_background_templates(
            settings.templates_dir / "backgrounds"
        )
        self._item_scale: float | None = None

        # Recomputed fresh every step() -- caching it across steps was
        # what made stale board knowledge after a scroll/zoom a real bug
        # before. Derived live from the game's own rendered item
        # positions and real browser/window DOM state every time (see
        # `cdp/scene_geometry.py`), so it's never stale and never needs
        # hand-correcting; None only for the brief window after the game
        # loads before the live board-cell map has anything on it yet to
        # derive geometry from (see `cdp/board_store.py`).
        self._scene_calibration: SceneCalibration | None = None
        self.board = BoardGrid()

    def _set_phase(self, phase: Phase) -> None:
        if phase != self.phase:
            logger.info("Phase %s -> %s", self.phase.name, phase.name)
            self.phase = phase

    def initialize(self) -> None:
        """Zoom the game out to maximize visible board area, and reset any
        board knowledge from a previous session/pause. Blocking; takes a
        few seconds. Called once before `run_forever`'s loop starts, and
        again on resume from pause, since that's an explicit opportunity
        for the user to have scrolled/zoomed the game themselves.

        Grid alignment itself doesn't depend on this known state -- it's
        derived fresh from the game's own live rendering/DOM state every
        step (see `_refresh_calibration`) and works at any scroll position
        or zoom level. This only still exists to maximize the visible
        board area and to drop stale board state.
        """
        region = find_window(settings.window_title)
        initialize_environment(region)
        self.board = BoardGrid()

    def _ensure_item_scale(self, frame: np.ndarray) -> None:
        """Best-effort, non-blocking: measure the board's actual on-screen
        item render scale by matching the cloud background template (an
        always-present, robust anchor, unlike any one item) against
        `frame`, at a range of candidate scales -- used only to rescale
        the fixed-size UI templates (`supply_crate`, `error_need_space`)
        that render at the same scale, not for any board-content
        detection (see `cdp/board_store.py`/`cdp/scene_geometry.py` for
        that).

        Cheap enough to run every step until it first succeeds, then never
        again -- nothing needs rebuilding once the render scale is known,
        and re-running gains nothing.
        """
        if self._item_scale is not None:
            return
        cloud_bgr = self._background_templates.get("cloud")
        if cloud_bgr is None:
            logger.warning("No 'cloud' background template found; can't measure item scale.")
            return
        cloud_bgra = cv2.cvtColor(cloud_bgr, cv2.COLOR_BGR2BGRA)
        calibration = calibrate_grid(frame, cloud_bgra)
        if calibration is None:
            return
        logger.info("Item render scale measured: %.3f", calibration.scale)
        self._item_scale = calibration.scale
        self._supply_crate_template_scaled = _scale_template(
            self._supply_crate_template, self._item_scale
        )
        self._need_space_template_scaled = _scale_template(
            self._need_space_template, self._item_scale
        )

    def _refresh_calibration(self, region: WindowRegion) -> None:
        """Recompute `_scene_calibration` for the current frame -- see its
        docstring in `__init__`."""
        try:
            self._scene_calibration = read_scene_calibration(settings.cdp_port, region)
        except CdpConnectionError as exc:
            logger.debug("Could not read scene geometry: %s", exc)
            self._scene_calibration = None

    def _ensure_board_store_armed(self) -> None:
        """Best-effort, idempotent: try to patch the game's board-store
        class so a real content change (its own or a merge/crate-claim)
        stashes a live reference -- see `cdp/board_store.py`. Cheap enough
        to call every step until armed; a no-op after."""
        if self._board_store_armed:
            return
        try:
            result = arm_board_store(settings.cdp_port)
        except CdpConnectionError as exc:
            logger.debug("Could not arm live board-state capture: %s", exc)
            return
        if result != self._last_board_store_status:
            logger.info("Live board-state capture: %s", result)
            self._last_board_store_status = result
        if result in ("patched", "already-captured", "already-patched"):
            self._board_store_armed = True

    def _sync_board_from_live_state(self) -> bool:
        """Replace `self.board` outright with the game's own live cell
        data (see `cdp/board_store.py`) -- exact and per-player-current,
        the sole board-content source. Returns False (no board knowledge
        touched) if a live reference hasn't been captured yet.
        """
        try:
            raw = read_board_state(settings.cdp_port)
        except CdpConnectionError as exc:
            logger.debug("Could not read live board state: %s", exc)
            return False
        if raw is None:
            return False

        board = BoardGrid()
        structure_anchors: set[GridCoord] = set()
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
            elif blueprint_id in _KNOWN_STRUCTURE_BLUEPRINTS:
                board.set_cell(coord, Cell(kind=CellKind.STRUCTURE))
                structure_anchors.add(coord)
            else:
                # Not a known item, marker, or building -- assumed a
                # transient product (e.g. "egg") waiting to be collected.
                board.set_cell(coord, Cell(kind=CellKind.PRODUCT))

        # A cell reporting "empty" is only genuinely open ground if it
        # isn't part of a structure's footprint -- confirmed live, a 2x2
        # shop's game data only records content on one anchor cell, with
        # its other 3 cells reporting "empty" identically to real open
        # ground. There's no footprint/size data to read directly (see
        # cdp/board_store.py), so this is inferred from 8-connected
        # adjacency to a known structure anchor instead.
        for coord in empty_coords:
            col, row = coord
            neighbors = {(col + dc, row + dr) for dc, dr in _FOOTPRINT_NEIGHBOR_OFFSETS}
            kind = CellKind.STRUCTURE if neighbors & structure_anchors else CellKind.EMPTY
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

    def _is_action_visible(self, action: MergeAction, frame_shape: tuple[int, ...]) -> bool:
        """Whether both ends of `action`'s drag land within the currently
        visible, non-UI-chrome region of the captured frame.

        Board content now comes from live game state, not vision, so
        `plan_merge_action` can select cells anywhere on the whole map --
        including ones currently scrolled out of view. Dragging to an
        off-screen pixel position is a silent no-op at best: confirmed
        live, the same "ready" cluster kept getting reselected and
        re-attempted step after step, since the drag never actually
        landed on real board content.
        """
        height, _width = frame_shape[:2]
        for coord in (action.start, action.end):
            x, y = self._grid_to_pixel(coord)
            if not (
                settings.board_min_pixel_x <= x <= settings.board_max_pixel_x
                and settings.board_min_pixel_y <= y <= height
            ):
                return False
        return True

    # Circuit breaker on `_scroll_to_reveal` below: each attempt is one
    # bounded drag, re-measuring the real camera position afterward
    # rather than calculating the exact distance needed (see that
    # method's docstring for why), so a genuinely far-off target can take
    # several attempts to reach. Comfortably enough to cross the whole
    # scrollable range in the worst case; hitting it just means giving up
    # on this particular target for now and trying again next step.
    _MAX_SCROLL_ATTEMPTS = 12

    def _try_merge(self, region: WindowRegion, frame: np.ndarray) -> bool:
        """Plan the best next action for every item type present. If the
        best one is actually visible on screen (see `_is_action_visible`),
        execute it. Otherwise, since board content comes from live game
        state and can reference cells anywhere on the whole map, spend
        this step instead scrolling toward the best *off-screen*
        candidate so it (or something else) becomes actionable next step.
        Returns whether either kind of action was taken.
        """
        actions = [
            action
            for item in self.board.items_present()
            if (
                action := plan_merge_action(
                    self.board, item, prefer_five=self._prefers_five(item)
                )
            )
            is not None
        ]
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
        """Pan the view (one drag at a time, re-measuring the real
        board-to-screen geometry after each one -- see `core/environment
        .pan`) until `target` lands within the visible, non-UI-chrome
        region.

        Direction is chosen adaptively rather than calculated from a
        fixed pixels-per-scroll-drag distance: that relationship was only
        validated over a small drag, not extrapolated over however far
        this target might be, so trusting a calculated distance here
        risks badly over- or under-shooting. Try a direction, measure
        whether the real result actually got closer, and flip if it
        didn't -- the same "never trust a predicted outcome without
        re-observing it" approach used everywhere else in this project.

        Returns whether `target` ended up visible.
        """
        toward_bottom = True
        best_distance: float | None = None
        for _ in range(self._MAX_SCROLL_ATTEMPTS):
            if self._quit_requested or self.paused:
                return False
            self._refresh_calibration(region)
            if self._scene_calibration is None:
                return False

            x, y = self._grid_to_pixel(target)
            if (
                settings.board_min_pixel_x <= x <= settings.board_max_pixel_x
                and settings.board_min_pixel_y <= y <= region.height
            ):
                return True

            desired_y = (settings.board_min_pixel_y + region.height) / 2
            distance = abs(y - desired_y)
            if best_distance is not None and distance >= best_distance:
                toward_bottom = not toward_bottom
            best_distance = distance
            pan(region, toward_bottom=toward_bottom, repeats=1)
        logger.warning(
            "Could not scroll %s into view within %d attempts.",
            target,
            self._MAX_SCROLL_ATTEMPTS,
        )
        return False

    def _execute_merge_action(self, region: WindowRegion, action: MergeAction) -> bool:
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
            # A relocation, not a merge -- both the vacated source and the
            # (now possibly wrong) destination need re-observing rather
            # than assumed; live-state sync (see `step`) does that fresh
            # next step regardless, this just avoids acting on stale
            # knowledge in between.
            self.board.clear_cell(action.start)
            self.board.clear_cell(action.end)
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

        # Cheap (a no-op once it's succeeded once) and needed regardless
        # of phase -- both the crate/need-space UI templates and item
        # templates need the board's actual render scale folded in, see
        # `_ensure_item_scale`. Likewise arming/reading the live board
        # state (see `cdp/board_store.py`) is just a couple of CDP round
        # trips, not a vision scan, so it's cheap enough to keep current
        # every step regardless of phase too.
        self._ensure_item_scale(frame)
        self._ensure_board_store_armed()
        live_synced = self._sync_board_from_live_state()

        # Calibration is only ever consumed by the merge phase
        # (`_grid_to_pixel`, cluster-finding) -- running it unconditionally,
        # every phase, was confirmed live to stretch a crate-claiming step
        # to several seconds against a 1s `loop_interval`, not worth it
        # when claiming crates needs neither.
        if self.phase is Phase.MERGE:
            self._refresh_calibration(region)

        board_full = self._is_board_full(frame, live_synced=live_synced)

        if self.phase is Phase.CLAIM_CRATES:
            self._step_claim_crates(region, frame, board_full=board_full)
        elif self.phase is Phase.MERGE:
            self._step_merge(region, frame, board_full=board_full)

        self.stats.iterations += 1

    def _is_board_full(self, frame: np.ndarray, *, live_synced: bool) -> bool:
        """Whether there's anywhere left for a crate to spawn something.

        With live state synced, this is the game's own truth --
        `self.board` was just replaced wholesale from it, so "no EMPTY
        cell anywhere known" really does mean full. Falls back to the
        on-screen "Need more empty space!" banner otherwise -- confirmed
        live to be unreliable on its own (it never appeared even once
        across dozens of crate clicks against a genuinely full board),
        which is exactly why the live-state check is preferred whenever
        it's available.
        """
        if live_synced:
            return not self.board.find_empty()
        return (
            find_best_match(frame, self._need_space_template_scaled, settings.need_space_confidence)
            is not None
        )

    # Circuit breaker on the burst-click loop below: there's no reliable
    # "out of crates" signal (the crate icon can stay on screen regardless
    # of held count -- see automation-methodology.md), so if `board_full`
    # never trips either, the crate template matching every single time
    # would otherwise spin here forever. Comfortably above any realistic
    # single-session crate count; hitting it just means falling back to
    # the normal step cadence and trying again next iteration.
    _MAX_CRATE_CLICKS_PER_STEP = 50

    def _step_claim_crates(
        self, region: WindowRegion, frame: np.ndarray, *, board_full: bool
    ) -> None:
        """Claim crates back-to-back until the crate icon stops matching or
        the board fills up, instead of one click per (much longer,
        board-scan-throttled) outer step -- see `step`.

        The crate button is a fixed UI element, not board content, so its
        on-screen position only needs (re-)finding once per batch of
        `crate_click_batch_size` clicks, not before every single one --
        it isn't going to have moved a click later.
        """
        if board_full:
            logger.warning("Board is full; switching to merge phase.")
            self._set_phase(Phase.MERGE)
            return

        total_clicks = 0
        while total_clicks < self._MAX_CRATE_CLICKS_PER_STEP:
            # Checked inside this loop, not just between `step()` calls
            # (see `run_forever`) -- otherwise a quit/pause request has to
            # wait out the rest of a potentially many-click burst before
            # it's even noticed, confirmed live to take several seconds
            # longer than expected to actually stop.
            if self._quit_requested or self.paused:
                return
            match = find_best_match(
                frame, self._supply_crate_template_scaled, settings.match_confidence
            )
            if match is None:
                return
            logger.info(
                "Clicking supply crate up to %d times (confidence=%.2f)",
                settings.crate_click_batch_size,
                match.confidence,
            )
            for _ in range(settings.crate_click_batch_size):
                if self._quit_requested or self.paused:
                    return
                click(region, *match.center)
                self.stats.actions_taken += 1
                total_clicks += 1
                time.sleep(settings.crate_click_settle)
                if total_clicks >= self._MAX_CRATE_CLICKS_PER_STEP:
                    break

            frame = capture_region(region)
            live_synced = self._sync_board_from_live_state()
            if self._is_board_full(frame, live_synced=live_synced):
                logger.warning("Board is full; switching to merge phase.")
                self._set_phase(Phase.MERGE)
                return
        logger.warning(
            "Hit the %d-click burst cap without running out of crates or filling the "
            "board; pausing until the next step.",
            self._MAX_CRATE_CLICKS_PER_STEP,
        )

    def _step_merge(self, region: WindowRegion, frame: np.ndarray, *, board_full: bool) -> None:
        if self._scene_calibration is None:
            # No grid geometry to act against this step (e.g. Chrome's CDP
            # endpoint wasn't readable, or nothing is on screen yet to
            # derive geometry from) -- nothing safe to do but wait for the
            # next step.
            return
        merged = self._try_merge(region, frame)
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
            # Resuming: re-initialize *before* clearing `paused`, so stale
            # board knowledge from before the pause (the user pausing is
            # exactly the moment they might have scrolled/zoomed, or
            # otherwise changed, the board themselves) is dropped before
            # the main loop starts stepping again. Runs on the hotkey
            # callback thread; blocking here is fine since the main loop
            # is parked.
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

        if settings.start_paused:
            self.paused = True
            logger.warning(
                "Starting paused -- press %s to begin (this defers environment "
                "setup too, so nothing touches the game until then).",
                settings.pause_hotkey,
            )
        else:
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
