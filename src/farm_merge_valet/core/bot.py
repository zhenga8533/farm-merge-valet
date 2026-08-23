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
from farm_merge_valet.cdp.client import CdpConnectionError, read_camera_data
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
from farm_merge_valet.core.board_map import BoardMap, load_board_map
from farm_merge_valet.core.board_scan import (
    discover_background_templates,
    discover_blueprint_items,
    discover_item_templates,
    discover_locked_templates,
    scale_templates,
    scan_frame,
    visible_grid_coords,
)
from farm_merge_valet.core.environment import initialize_environment
from farm_merge_valet.core.stats import RunStats
from farm_merge_valet.vision.camera_calibration import CameraCalibration, calibrate_from_camera
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
# above: the static map (core/board_map.py) can't be used for this since
# it only reflects the *pristine* layout, and a building a player has
# actually built can sit on a tile the static data still calls LOCKED,
# not DECORATION -- confirmed live with a real bakery). Anything not in
# this set and not otherwise recognized is assumed to be a transient
# product (e.g. "egg") rather than a building -- low-consequence if
# wrong, since PRODUCT cells aren't acted on yet either way; extend this
# set as new building types are observed.
_KNOWN_STRUCTURE_BLUEPRINTS = {
    "bakery",
    "market",
    "trainstation",
    "traintrack_stop",
    "delivery_truck",
    "delivery_cargo",
    "likes_billboard",
}


def _scale_template(template: np.ndarray, scale: float) -> np.ndarray:
    """Resize a single BGRA template by `scale`, preserving its alpha
    channel -- same idea as `board_scan.scale_templates`, just for the
    one-off UI templates (`supply_crate`, `error_need_space`) rather than
    the item-template dict."""
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

        # Scanning checks occupancy against `_background_templates` before
        # ever classifying identity against `_item_templates_classify` --
        # see core/board_scan.py. `_item_templates_classify` is built once
        # `_ensure_item_scale` succeeds, not here.
        self._item_templates_full = discover_item_templates(settings.templates_dir / "items")
        self._item_templates_classify: dict[ItemRef, np.ndarray] = {}
        # blueprintID (e.g. "wheat_1") -> ItemRef, for interpreting the
        # game's own live board state (see cdp/board_store.py) -- the
        # primary, vision-free board-content source; `_item_templates_*`
        # above remain as the fallback for whenever that isn't armed yet.
        self._blueprint_items = discover_blueprint_items(self._item_templates_full)
        self._board_store_armed = False
        self._last_board_store_status: str | None = None
        self._background_templates = discover_background_templates(
            settings.templates_dir / "backgrounds"
        )
        # Level-gated tiles show a fixed padlock+required-level badge --
        # real board content the static map has no way to know about
        # (level-gating is a player-progress mechanic, not part of the
        # map's fixed layout), so without this they fell through to full
        # item classification and produced false positives (confirmed
        # live: the badge was misidentified as several different crops).
        # See core/board_scan.py's discover_locked_templates.
        self._locked_templates = discover_locked_templates(settings.templates_dir / "locked")
        self._item_scale: float | None = None

        # The game's own extracted level data (see
        # tools/template_extraction.py's extract_board_map) -- every
        # locked/premium/decorated coordinate on this map, known up front
        # without any vision call at all. `core/board_scan.py`'s scan only
        # ever runs against the remaining, unclassified coordinates (plain
        # farmable ground). See core/board_map.py.
        self._board_map: BoardMap = load_board_map(
            settings.board_map_dir / f"{settings.board_map_name}.json"
        )

        # Recomputed fresh every step() from a live camera read (see
        # `vision/camera_calibration.py`) -- unlike the old vision-based
        # anchor match, this is cheap enough that there's no reason to
        # cache it across steps; caching it was what made stale board
        # knowledge after a scroll/zoom a real bug before.
        self._calibration: CameraCalibration | None = None
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

        Unlike the old wheat-anchor calibration, grid alignment itself no
        longer depends on this known state -- it's derived fresh from a
        live camera read every step (see `_read_calibration`) and works at
        any scroll position or zoom level. This only still exists to
        maximize the visible board area and to drop stale board state.
        """
        region = find_window(settings.window_title)
        initialize_environment(region)
        self.board = BoardGrid()

    def _ensure_item_scale(self, frame: np.ndarray) -> None:
        """Best-effort, non-blocking: measure the board's actual on-screen
        item render scale by matching the cloud background template (an
        always-present, robust anchor, unlike any one item) against
        `frame`, at a range of candidate scales. Not derivable from camera
        zoom alone -- that proportionality was never independently
        confirmed, see `vision/camera_calibration.py` -- so this measures
        it directly instead.

        Cheap enough to run every step until it first succeeds, then never
        again -- `_item_templates_classify` doesn't need rebuilding once
        the render scale is known, and re-running gains nothing.
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
        self._item_templates_classify = scale_templates(self._item_templates_full, self._item_scale)
        self._supply_crate_template_scaled = _scale_template(
            self._supply_crate_template, self._item_scale
        )
        self._need_space_template_scaled = _scale_template(
            self._need_space_template, self._item_scale
        )

    def _read_calibration(self) -> CameraCalibration | None:
        """Read the live camera state via CDP and turn it into exact
        board-to-screen geometry. Returns None (rather than raising) if
        Chrome's remote debugging endpoint isn't reachable or the camera
        state hasn't been persisted yet -- both are expected, recoverable
        conditions (e.g. the game just loaded), not something that should
        crash the loop; see `cdp/client.py`.
        """
        if self._item_scale is None:
            return None
        try:
            camera_data = read_camera_data(settings.cdp_port)
        except CdpConnectionError as exc:
            logger.warning("Could not read camera data: %s", exc)
            return None
        if camera_data is None:
            return None
        return calibrate_from_camera(camera_data, item_scale=self._item_scale)

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
        data (see `cdp/board_store.py`) -- exact, per-player-current, and
        free of vision's classification uncertainty, unlike
        `_scan_board`. Returns False (no board knowledge touched) if a
        live reference hasn't been captured yet, so callers know to fall
        back to vision instead.
        """
        try:
            raw = read_board_state(settings.cdp_port)
        except CdpConnectionError as exc:
            logger.debug("Could not read live board state: %s", exc)
            return False
        if raw is None:
            return False
        board = BoardGrid()
        for coord, blueprint_id in raw.items():
            item = self._blueprint_items.get(blueprint_id)
            if item is not None:
                board.set_cell(coord, Cell(kind=CellKind.ITEM, item=item))
                continue
            kind = _LIVE_STATE_CELL_KIND.get(blueprint_id)
            if kind is not None:
                board.set_cell(coord, Cell(kind=kind))
                continue
            # Not a known item or marker -- a fixed building
            # (`_KNOWN_STRUCTURE_BLUEPRINTS`, left unrecorded: not real
            # mergeable content) or, assumed otherwise, a harvested
            # product (e.g. "egg") waiting to be collected.
            if blueprint_id not in _KNOWN_STRUCTURE_BLUEPRINTS:
                board.set_cell(coord, Cell(kind=CellKind.PRODUCT))
        self.board = board
        logger.debug("Synced %d cell(s) from live game state.", len(board.known_coords()))
        return True

    def _scan_board(self, frame: np.ndarray, calibration: CameraCalibration) -> None:
        coords = visible_grid_coords(
            frame.shape,
            calibration.origin_pixel,
            calibration.origin_coord,
            calibration.grid,
            min_pixel_x=settings.board_min_pixel_x,
            max_pixel_x=settings.board_max_pixel_x,
            min_pixel_y=settings.board_min_pixel_y,
        )
        # Locked/premium/decorated coordinates (and cells right next to
        # decoration -- see BoardMap.is_scan_candidate) are known for free
        # from the static map -- only cells it says nothing about need the
        # vision-based occupancy/classify pass at all.
        farmable = {coord for coord in coords if self._board_map.is_scan_candidate(coord)}
        if not farmable:
            return
        updated = scan_frame(
            self.board,
            frame,
            self._item_templates_classify,
            self._background_templates,
            calibration.grid,
            calibration.origin_pixel,
            calibration.origin_coord,
            farmable,
            locked_templates=self._locked_templates,
            min_confidence=settings.match_confidence,
            occupancy_threshold=settings.occupancy_threshold,
            max_workers=settings.board_scan_workers,
        )
        logger.debug("Board scan recorded %d cell(s)", updated)

    def _prefers_five(self, item: ItemRef) -> bool:
        return self._merge_five_overrides.get(item, settings.prefer_merge_five)

    def _grid_to_pixel(self, coord: GridCoord) -> tuple[int, int]:
        assert self._calibration is not None
        origin_pixel, origin_coord = self._calibration.origin_pixel, self._calibration.origin_coord
        dx, dy = self._calibration.grid.grid_to_pixel_delta(
            coord[0] - origin_coord[0], coord[1] - origin_coord[1]
        )
        return (int(origin_pixel[0] + dx), int(origin_pixel[1] + dy))

    # Actions are chosen in this order across every item type present:
    # complete a ready merge before fixing an over-sized cluster, and fix
    # that before spending a step just gathering toward a future merge --
    # see `core/board.plan_merge_action`.
    _MERGE_ACTION_PRIORITY = {
        MergeActionKind.TRIGGER: 0,
        MergeActionKind.DEGROUP: 1,
        MergeActionKind.GATHER: 2,
    }

    def _try_merge(self, region: WindowRegion) -> bool:
        """Plan the best next action for every item type present, and
        execute the single highest-priority one. Returns whether an
        action was taken."""
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
        best = min(actions, key=lambda a: self._MERGE_ACTION_PRIORITY[a.kind])
        return self._execute_merge_action(region, best)

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
            # next step regardless, this only matters for the vision
            # fallback's own persistent board.
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

        # Calibration and vision-based board scanning are only ever
        # consumed by the merge phase (`_grid_to_pixel`, cluster-finding).
        # Calibration is still needed even when live state supplies board
        # *content*, since it's what maps a (col, row) to the screen
        # pixel a merge drag actually targets; the vision scan is only a
        # fallback for whenever live state hasn't been captured yet.
        # Running either unconditionally, every phase, was confirmed live
        # to stretch a crate-claiming step to several seconds against a
        # 1s `loop_interval` -- not worth it when claiming crates needs
        # neither.
        if self.phase is Phase.MERGE:
            self._calibration = self._read_calibration()
            if self._calibration is not None and not live_synced:
                self._scan_board(frame, self._calibration)

        board_full = self._is_board_full(frame, live_synced=live_synced)

        if self.phase is Phase.CLAIM_CRATES:
            self._step_claim_crates(region, frame, board_full=board_full)
        elif self.phase is Phase.MERGE:
            self._step_merge(region, board_full=board_full)

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

    # Between crate clicks, just long enough for the game to register the
    # click and update the crate/space state before the next capture --
    # much shorter than `loop_interval`, since there's no board scanning
    # to justify a full second here (see `step`).
    _CRATE_CLICK_SETTLE = 0.4
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
        board-scan-throttled) outer step -- see `step`."""
        if board_full:
            logger.warning("Board is full; switching to merge phase.")
            self._set_phase(Phase.MERGE)
            return

        clicks_since_check = 0
        for _ in range(self._MAX_CRATE_CLICKS_PER_STEP):
            match = find_best_match(
                frame, self._supply_crate_template_scaled, settings.match_confidence
            )
            if match is None:
                return
            logger.info("Clicking supply crate (confidence=%.2f)", match.confidence)
            click(region, *match.center)
            self.stats.actions_taken += 1
            time.sleep(self._CRATE_CLICK_SETTLE)
            clicks_since_check += 1

            frame = capture_region(region)
            if clicks_since_check < settings.crate_click_batch_size:
                continue
            clicks_since_check = 0
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

    def _step_merge(self, region: WindowRegion, *, board_full: bool) -> None:
        if self._calibration is None:
            # No grid geometry to act against this step (e.g. Chrome's CDP
            # endpoint or the camera state wasn't readable) -- nothing safe
            # to do but wait for the next step.
            return
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
