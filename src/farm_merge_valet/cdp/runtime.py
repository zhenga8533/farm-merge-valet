"""CDP implementation of the adapter-neutral automation runtime."""

from __future__ import annotations

import json
import logging
import random
import time
from collections import deque
from collections.abc import Callable
from threading import Event, Lock

from farm_merge_valet.automation.runtime import (
    ActionResult,
    ActionStatus,
    CrateSpawnResult,
    EventRewardState,
    EventState,
    FarmSceneKind,
    FarmVisitState,
    RuntimeHealth,
    RuntimeReadMetrics,
    RuntimeSnapshot,
    SnapshotOptions,
    StorageBubbleState,
    TransientOverlayKind,
    VisitorActionState,
)
from farm_merge_valet.cdp.board_store import (
    _ARM_BOARD_FROM_REGISTRY_EXPRESSION,
    arm_board_store,
    parse_board_state,
    read_board_state,
)
from farm_merge_valet.cdp.buildings import parse_building_repairs
from farm_merge_valet.cdp.evaluation import apply_background_overrides, evaluate
from farm_merge_valet.cdp.inventory_store import read_energy
from farm_merge_valet.cdp.land_expansion import land_expansion_action_expression
from farm_merge_valet.cdp.lucky_merge import LuckyMergeNetwork
from farm_merge_valet.cdp.marketplace import (
    _READ_MARKETPLACE_EXPRESSION,
    marketplace_purchase_expression,
    parse_marketplace_offers,
)
from farm_merge_valet.cdp.marketplace import (
    parse_action_result as parse_marketplace_action_result,
)
from farm_merge_valet.cdp.portal_lifecycle import maintain_portal_session
from farm_merge_valet.cdp.scripts import (
    _BOARD_ARMED_EXPRESSION,
    _DISCOVER_EXPRESSION,
    _DISCOVERY_DIAGNOSTICS_EXPRESSION,
    _HEALTH_EXPRESSION,
    _HEARTBEAT_EXPRESSION,
    _READ_SHOP_ORDERS_EXPRESSION,
    _READ_STORAGE_BUBBLES_EXPRESSION,
    _READ_WORKERS_EXPRESSION,
    _RUNTIME_BOOTSTRAP_EXPRESSION,
    _crate_expression,
    _dismiss_overlay_expression,
    _drop_expression,
    _event_action_expression,
    _event_reward_claim_expression,
    _farm_visit_action_expression,
    _interaction_expression,
    _removal_expression,
    _shop_claim_expression,
    _shop_start_expression,
    _storage_bubble_pop_expression,
)
from farm_merge_valet.cdp.snapshot import snapshot_expression
from farm_merge_valet.cdp.targets import (
    read_background_flag_status,
)
from farm_merge_valet.cdp.transport import CdpConnectionError
from farm_merge_valet.cdp.upgrade_progress import parse_upgrade_progress
from farm_merge_valet.core.items import GridCoord, InteractionTargetKind
from farm_merge_valet.core.land_expansion import (
    ExpansionRequirement,
    LandExpansionCandidate,
)
from farm_merge_valet.core.marketplace import MarketplaceAction, MarketplaceLiveOffer
from farm_merge_valet.core.obstacles import WorkerState
from farm_merge_valet.core.shops import ShopIngredient, ShopOrder, ShopOrderState
from farm_merge_valet.observability.logging import log_event

logger = logging.getLogger(__name__)

_RECOVERY_FRAME_PROBE_EXPRESSION = r"""
new Promise((resolve) => {
  let settled = false;
  const finish = (value) => {
    if (settled) return;
    settled = true;
    resolve(value);
  };
  requestAnimationFrame(() => finish(true));
  setTimeout(() => finish(false), 500);
})
"""
_RECOVERY_COOLDOWNS = (5.0, 15.0, 60.0, 300.0)
_MAX_HEARTBEAT_AGE_MS = 1500.0
_RUNTIME_BOOTSTRAP_SETTLE_SECONDS = 2.0
_PORTAL_MAINTENANCE_INTERVAL_SECONDS = 15.0


class GameRuntimeAdapter:
    """Discovers and calls the active game's own interaction pipeline."""

    _recovery_state_lock = Lock()
    _recovery_locks: dict[tuple[int, str | None], Lock] = {}
    _recovery_failures: dict[tuple[int, str | None], tuple[int, float]] = {}

    def __init__(
        self,
        port: int,
        page_title: str | None = None,
        *,
        crate_delay_min: float = 0.05,
        crate_delay_max: float = 0.2,
        observation_only: bool = False,
    ) -> None:
        self.port = port
        self.page_title = page_title
        self._crate_delay_min = crate_delay_min
        self._crate_delay_max = crate_delay_max
        self.observation_only = observation_only
        self._scene_id: int | None = None
        self._last_heartbeat: int | None = None
        self._discovery_detail: str | None = "runtime-discovery-not-run"
        self._cancel_event: Event | None = None
        self._snapshot_metrics: deque[RuntimeReadMetrics] = deque(maxlen=240)
        self._last_snapshot_summary_at = time.monotonic()
        self._heartbeat_stalled = False
        self._runtime_bootstrap_ready_at: float | None = None
        self._next_portal_maintenance_at = 0.0
        self._scene_refresh_origin_id: int | None = None
        self._scene_refresh_required = False

    def set_cancel_event(self, cancel_event: Event) -> None:
        self._cancel_event = cancel_event

    def configure_crate_delays(self, minimum: float, maximum: float) -> None:
        if minimum < 0 or maximum < minimum:
            raise ValueError("crate delay range is invalid")
        self._crate_delay_min = minimum
        self._crate_delay_max = maximum

    def save_game(self) -> bool:
        return (
            evaluate(
                self.port,
                """(async () => {
              const save = window.__fmvGameplayServices?.ordersService?._autoSaveService;
              if (typeof save?.forceSave !== 'function') return false;
              await save.forceSave();
              return true;
            })()""",
                self.page_title,
                timeout=15,
                cancel_event=self._cancel_event,
            )
            is True
        )

    def lucky_merge_network(self) -> LuckyMergeNetwork:
        if self.observation_only:
            raise CdpConnectionError("Lucky merge is unavailable in observation mode.")
        return LuckyMergeNetwork.for_game(self.port, self.page_title, self._cancel_event)

    def read_board_state(self):
        return read_board_state(
            self.port,
            self.page_title,
            cancel_event=self._cancel_event,
            allow_observation=self.observation_only,
        )

    def read_energy(self) -> int | None:
        return read_energy(
            self.port,
            self.page_title,
            cancel_event=self._cancel_event,
            allow_observation=self.observation_only,
        )

    def read_workers(self) -> WorkerState | None:
        raw = self._evaluate(_READ_WORKERS_EXPRESSION)
        return self._parse_workers(raw)

    @staticmethod
    def _parse_workers(raw: object) -> WorkerState | None:
        if not isinstance(raw, dict):
            return None
        total = raw.get("total")
        available = raw.get("available")
        if (
            not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
            or not isinstance(available, int)
            or isinstance(available, bool)
            or not 0 <= available <= total
        ):
            return None
        return WorkerState(total=total, available=available)

    def read_storage_bubbles(self) -> tuple[StorageBubbleState, ...] | None:
        raw = self._evaluate(_READ_STORAGE_BUBBLES_EXPRESSION)
        return self._parse_storage_bubbles(raw)

    @staticmethod
    def _parse_storage_bubbles(raw: object) -> tuple[StorageBubbleState, ...] | None:
        if not isinstance(raw, list):
            return None
        bubbles: list[StorageBubbleState] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            object_id = entry.get("objectID")
            content_ids = entry.get("contentIDs")
            if (
                not isinstance(object_id, int)
                or isinstance(object_id, bool)
                or not isinstance(content_ids, list)
            ):
                continue
            bubbles.append(
                StorageBubbleState(
                    object_id,
                    tuple(value for value in content_ids if isinstance(value, str)),
                )
            )
        return tuple(bubbles)

    def read_background_flag_status(self) -> dict[str, object]:
        return read_background_flag_status(self.port, cancel_event=self._cancel_event)

    def _evaluate(self, expression: str, *, timeout: float = 5.0, retry: bool = True) -> object:
        return evaluate(
            self.port,
            expression,
            self.page_title,
            timeout=timeout,
            cancel_event=self._cancel_event,
            retry=retry,
            allow_observation=self.observation_only,
        )

    def _evaluate_action(self, expression: str) -> object:
        if self.observation_only:
            return {"status": ActionStatus.REJECTED.value, "detail": "observation-only-runtime"}
        return self._evaluate(expression, retry=False)

    def discover(self, cancelled: Callable[[], bool] | None = None) -> RuntimeHealth:
        started = time.monotonic()

        def is_cancelled() -> bool:
            return bool(
                (cancelled is not None and cancelled())
                or (self._cancel_event is not None and self._cancel_event.is_set())
            )

        if is_cancelled():
            return self.read_runtime_health()
        apply_background_overrides(
            self.port,
            self.page_title,
            cancel_event=self._cancel_event,
            allow_observation=self.observation_only,
        )
        if is_cancelled():
            return self.read_runtime_health()
        cache_started = time.monotonic()
        cached_board_is_current = self._evaluate(_BOARD_ARMED_EXPRESSION) is True
        cache_elapsed = time.monotonic() - cache_started
        log_event(
            logger,
            logging.DEBUG,
            "runtime.cache_validation",
            "Runtime board cache validation finished in %.3fs (current=%s).",
            cache_elapsed,
            cached_board_is_current,
            elapsed_seconds=cache_elapsed,
            cached_board=cached_board_is_current,
        )
        if is_cancelled():
            return self.read_runtime_health()
        raw: object = None
        discovery_attempted = False
        if not cached_board_is_current and not is_cancelled():
            cached_board_is_current = (
                self._evaluate(_ARM_BOARD_FROM_REGISTRY_EXPRESSION, retry=False) is True
            )
        if not cached_board_is_current and not is_cancelled():
            bootstrap_ready = self._runtime_bootstrap_ready()
            recovered = False
            if bootstrap_ready:
                self._discovery_detail = "board-recovery-pending"
                recovered = self._recover_board_from_heap()
            else:
                self._discovery_detail = "game-runtime-initializing"
            if recovered and not is_cancelled():
                refresh_started = time.monotonic()
                discovery_attempted = True
                raw = self._evaluate(_DISCOVER_EXPRESSION)
                log_event(
                    logger,
                    logging.DEBUG,
                    "runtime.handler_refresh",
                    "Post-recovery runtime handler refresh finished in %.3fs.",
                    time.monotonic() - refresh_started,
                    elapsed_seconds=time.monotonic() - refresh_started,
                    post_recovery=True,
                )
        elif cached_board_is_current:
            refresh_started = time.monotonic()
            discovery_attempted = True
            raw = self._evaluate(_DISCOVER_EXPRESSION)
            refresh_elapsed = time.monotonic() - refresh_started
            log_event(
                logger,
                logging.DEBUG,
                "runtime.handler_refresh",
                "Bounded runtime handler refresh finished in %.3fs.",
                refresh_elapsed,
                elapsed_seconds=refresh_elapsed,
            )
        heartbeat_sample = self._evaluate(_HEARTBEAT_EXPRESSION)
        if isinstance(heartbeat_sample, dict) and isinstance(heartbeat_sample.get("frame"), int):
            self._last_heartbeat = heartbeat_sample["frame"]
        if isinstance(raw, dict):
            if isinstance(raw.get("sceneId"), int):
                self._scene_id = raw["sceneId"]
            detail = raw.get("detail")
            self._discovery_detail = detail if isinstance(detail, str) else None
        elif discovery_attempted:
            self._discovery_detail = "invalid-runtime-discovery-response"
        # A second sample makes the initial health result meaningful when the
        # animation loop is running at normal foreground cadence.
        if not is_cancelled():
            time.sleep(0.05)
        health = self.read_runtime_health()
        elapsed = time.monotonic() - started
        log_event(
            logger,
            logging.DEBUG,
            "runtime.discovery_completed",
            "Runtime discovery finished in %.1fs (cached board=%s, scene=%s).",
            elapsed,
            cached_board_is_current,
            health.scene_id,
            elapsed_seconds=elapsed,
            cached_board=cached_board_is_current,
            scene_id=health.scene_id,
        )
        return health

    def _runtime_bootstrap_ready(self) -> bool:
        if self._evaluate(_RUNTIME_BOOTSTRAP_EXPRESSION) is not True:
            self._runtime_bootstrap_ready_at = None
            return False
        now = time.monotonic()
        if self._runtime_bootstrap_ready_at is None:
            self._runtime_bootstrap_ready_at = now
            return _RUNTIME_BOOTSTRAP_SETTLE_SECONDS <= 0
        return now - self._runtime_bootstrap_ready_at >= _RUNTIME_BOOTSTRAP_SETTLE_SECONDS

    def _recover_board_from_heap(self) -> bool:
        key = (self.port, self.page_title)
        with self._recovery_state_lock:
            recovery_lock = self._recovery_locks.setdefault(key, Lock())
        with recovery_lock:
            if self._evaluate(_BOARD_ARMED_EXPRESSION) is True:
                return True
            now = time.monotonic()
            with self._recovery_state_lock:
                failures, retry_at = self._recovery_failures.get(key, (0, 0.0))
            if now < retry_at:
                self._discovery_detail = "board-recovery-cooldown"
                log_event(
                    logger,
                    logging.DEBUG,
                    "runtime.heap_recovery_deferred",
                    "Board heap recovery is cooling down for %.1fs after a failed search.",
                    retry_at - now,
                    retry_after_seconds=retry_at - now,
                    failures=failures,
                )
                return False
            frame_ready = self._evaluate(_RECOVERY_FRAME_PROBE_EXPRESSION, timeout=1.0, retry=False)
            if frame_ready is not True:
                self._discovery_detail = "game-loop-not-advancing"
                log_event(
                    logger,
                    logging.WARNING,
                    "runtime.heap_recovery_deferred",
                    "Board heap recovery deferred because the game loop is not advancing.",
                    failures=failures,
                )
                return False
            log_event(
                logger,
                logging.DEBUG,
                "runtime.heap_recovery_started",
                "Cached board is absent or stale; scanning the heap for the active board.",
            )
            scan_started = time.monotonic()
            raw_status = arm_board_store(
                self.port,
                self.page_title,
                cancel_event=self._cancel_event,
                allow_observation=self.observation_only,
            )
            status = raw_status if isinstance(raw_status, str) else "invalid-board-search-response"
            elapsed = time.monotonic() - scan_started
            recovered = status.startswith("found")
            if not recovered:
                self._discovery_detail = status
            with self._recovery_state_lock:
                if recovered:
                    self._recovery_failures.pop(key, None)
                else:
                    failures += 1
                    cooldown = _RECOVERY_COOLDOWNS[min(failures - 1, len(_RECOVERY_COOLDOWNS) - 1)]
                    self._recovery_failures[key] = (failures, time.monotonic() + cooldown)
            log_event(
                logger,
                logging.INFO if recovered else logging.DEBUG,
                "runtime.heap_recovery_completed",
                "Board heap recovery finished in %.1fs: %s.",
                elapsed,
                status,
                elapsed_seconds=elapsed,
                status=status,
                recovered=recovered,
                failures=0 if recovered else failures,
            )
            return recovered

    def inspect_runtime(self) -> dict[str, object]:
        """Return the bounded discovery stages without traversing the JS heap."""
        raw = self._evaluate(_DISCOVERY_DIAGNOSTICS_EXPRESSION)
        return raw if isinstance(raw, dict) else {"strategy": "invalid-response"}

    def read_runtime_health(self) -> RuntimeHealth:
        raw = self._evaluate(_HEALTH_EXPRESSION)
        return self._parse_runtime_health(raw)

    def _parse_runtime_health(self, raw: object) -> RuntimeHealth:
        if not isinstance(raw, dict):
            return RuntimeHealth(
                False,
                None,
                False,
                False,
                False,
                False,
                None,
                None,
                False,
                detail="invalid-runtime-health-response",
            )
        heartbeat = raw.get("heartbeat") if isinstance(raw.get("heartbeat"), int) else None
        heartbeat_age = (
            float(raw["heartbeatAgeMs"])
            if isinstance(raw.get("heartbeatAgeMs"), int | float)
            else None
        )
        advancing = (
            heartbeat is not None
            and self._last_heartbeat is not None
            and (heartbeat > self._last_heartbeat)
            and heartbeat_age is not None
            and heartbeat_age <= _MAX_HEARTBEAT_AGE_MS
        )
        self._last_heartbeat = heartbeat
        scene_id = raw.get("sceneId") if isinstance(raw.get("sceneId"), int) else None
        if self._scene_id is not None and scene_id != self._scene_id:
            self._scene_id = None
            self._discovery_detail = "runtime-scene-changed"
        item_drop = raw.get("itemDrop") is True
        interaction = raw.get("interaction") is True
        removal = raw.get("removal") is True
        reward_interaction = raw.get("rewardInteraction") is True
        reward_container = raw.get("rewardContainer") is True
        storage_bubble = raw.get("storageBubble") is True
        upgrade_interaction = raw.get("upgradeInteraction") is True
        crate_spawn = raw.get("crateSpawn") is True
        board = raw.get("board") is True
        transient_overlay = None
        farm_scene = None
        raw_farm_scene = raw.get("farmScene")
        if isinstance(raw_farm_scene, str):
            try:
                farm_scene = FarmSceneKind(raw_farm_scene)
            except ValueError:
                pass
        raw_transient_overlay = raw.get("transientOverlay")
        if isinstance(raw_transient_overlay, str):
            try:
                transient_overlay = TransientOverlayKind(raw_transient_overlay)
            except ValueError:
                transient_overlay = TransientOverlayKind.UNSUPPORTED
        return RuntimeHealth(
            available=board
            and (
                item_drop
                or interaction
                or removal
                or reward_interaction
                or reward_container
                or upgrade_interaction
                or crate_spawn
                or raw.get("farmVisit") is True
            )
            and scene_id is not None,
            scene_id=scene_id,
            board_available=board,
            item_drop_available=item_drop,
            crate_spawn_available=crate_spawn,
            inventory_available=raw.get("inventory") is True,
            heartbeat=heartbeat,
            heartbeat_age_ms=heartbeat_age,
            heartbeat_advancing=advancing,
            heartbeat_installed=raw.get("heartbeatInstalled") is True,
            detail=self._discovery_detail,
            item_action_busy=raw.get("itemActionBusy") is True,
            interaction_available=interaction,
            shop_available=raw.get("shopOrders") is True,
            marketplace_available=raw.get("marketplace") is True,
            land_expansion_available=raw.get("landExpansion") is True,
            removal_available=removal,
            reward_interaction_available=reward_interaction,
            obstacle_clear_available=raw.get("obstacleClear") is True,
            upgrade_interaction_available=upgrade_interaction,
            reward_container_available=reward_container,
            storage_bubble_available=storage_bubble,
            farm_visit_available=raw.get("farmVisit") is True,
            farm_scene=farm_scene,
            scene_transition_active=raw.get("sceneTransitionActive") is True,
            backend_connected=(
                raw["backendConnected"] if isinstance(raw.get("backendConnected"), bool) else None
            ),
            backend_connectivity_state=(
                raw["backendConnectivityState"]
                if isinstance(raw.get("backendConnectivityState"), int)
                and not isinstance(raw.get("backendConnectivityState"), bool)
                else None
            ),
            backend_consecutive_hanging_pings=(
                raw["backendConsecutiveHangingPings"]
                if isinstance(raw.get("backendConsecutiveHangingPings"), int)
                and not isinstance(raw.get("backendConsecutiveHangingPings"), bool)
                else None
            ),
            transient_overlay=transient_overlay,
            transient_overlay_detail=(
                raw["transientOverlayDetail"]
                if isinstance(raw.get("transientOverlayDetail"), str)
                else None
            ),
        )

    def read_snapshot(self, options: SnapshotOptions) -> RuntimeSnapshot | None:
        self._maintain_portal_session()
        if self._scene_refresh_required:
            health = self.discover()
            if health.board_available and (
                self._scene_refresh_origin_id is None
                or health.scene_id != self._scene_refresh_origin_id
            ):
                self._scene_refresh_required = False
                self._scene_refresh_origin_id = None
        started = time.monotonic()
        raw = self._evaluate(snapshot_expression(options), retry=False)
        wall_duration_ms = (time.monotonic() - started) * 1000.0
        if not isinstance(raw, dict):
            return None
        health = self._parse_runtime_health(raw.get("health"))
        cells = parse_board_state(raw.get("cells"))
        energy = raw.get("energy")
        if not isinstance(energy, int) or isinstance(energy, bool) or energy < 0:
            energy = None
        renderer_duration = raw.get("rendererDurationMs")
        metrics = RuntimeReadMetrics(
            wall_duration_ms=wall_duration_ms,
            renderer_duration_ms=(
                float(renderer_duration)
                if isinstance(renderer_duration, int | float)
                and not isinstance(renderer_duration, bool)
                else None
            ),
            response_bytes=len(json.dumps(raw, separators=(",", ":")).encode("utf-8")),
            cell_count=max(0, int(raw.get("cellCount", 0))),
            occupied_cell_count=max(0, int(raw.get("occupiedCellCount", 0))),
        )
        self._record_snapshot_metrics(metrics)
        stalled = (
            health.heartbeat_installed
            and health.heartbeat is not None
            and not health.heartbeat_advancing
            and health.heartbeat_age_ms is not None
            and health.heartbeat_age_ms > _MAX_HEARTBEAT_AGE_MS
        )
        if stalled != self._heartbeat_stalled:
            log_event(
                logger,
                logging.WARNING if stalled else logging.INFO,
                "runtime.heartbeat_stalled" if stalled else "runtime.heartbeat_recovered",
                "Game heartbeat %s (age %.1fms).",
                "stalled" if stalled else "recovered",
                health.heartbeat_age_ms or 0.0,
                heartbeat_age_ms=health.heartbeat_age_ms,
            )
            self._heartbeat_stalled = stalled
        if wall_duration_ms >= 250.0:
            log_event(
                logger,
                logging.WARNING,
                "runtime.snapshot_slow",
                "Slow runtime snapshot: %.1fms wall, %s renderer.",
                wall_duration_ms,
                (
                    f"{metrics.renderer_duration_ms:.1f}ms"
                    if metrics.renderer_duration_ms is not None
                    else "unknown"
                ),
                wall_duration_ms=wall_duration_ms,
                renderer_duration_ms=metrics.renderer_duration_ms,
                response_bytes=metrics.response_bytes,
                cell_count=metrics.cell_count,
                occupied_cell_count=metrics.occupied_cell_count,
            )
        return RuntimeSnapshot(
            health=health,
            cells=cells,
            energy=energy,
            workers=self._parse_workers(raw.get("workers")),
            storage_bubbles=self._parse_storage_bubbles(raw.get("storageBubbles")),
            shop_orders=self._parse_shop_orders(raw.get("shopOrders")),
            marketplace_offers=parse_marketplace_offers(raw.get("marketplace")),
            farm_visit=self._parse_farm_visit(raw.get("farmVisit")),
            land_expansions=self._parse_land_expansions(raw.get("landExpansions")),
            building_repairs=parse_building_repairs(raw.get("buildingRepairs")),
            event=self._parse_event(raw.get("event")),
            event_rewards=self._parse_event_rewards(raw.get("eventRewards")),
            upgrade_progress=parse_upgrade_progress(raw.get("upgradeProgress")),
            metrics=metrics,
        )

    @staticmethod
    def _parse_event(raw: object) -> EventState | None:
        if not isinstance(raw, dict) or not isinstance(raw.get("key"), str):
            return None
        event_key = raw["key"]
        display_name = raw.get("displayName")
        energy_key = raw.get("energyKey")
        energy = raw.get("energy")
        exploration_area_id = raw.get("explorationAreaID")
        exploration_cell_count = raw.get("explorationCellCount")
        exploration_required_level = raw.get("explorationRequiredLevel")
        exploration_current_level = raw.get("explorationCurrentLevel")
        return EventState(
            key=event_key,
            display_name=display_name if isinstance(display_name, str) else event_key,
            active=raw.get("active") is True,
            supported=raw.get("supported") is True,
            current=raw.get("current") is True,
            introduction_open=raw.get("introductionOpen") is True,
            can_enter=raw.get("canEnter") is True,
            can_return=raw.get("canReturn") is True,
            energy_key=energy_key if isinstance(energy_key, str) else None,
            energy=energy if isinstance(energy, int) and not isinstance(energy, bool) else None,
            can_explore=raw.get("canExplore") is True,
            exploration_area_id=(
                exploration_area_id if isinstance(exploration_area_id, str) else None
            ),
            exploration_cell_count=(
                exploration_cell_count
                if isinstance(exploration_cell_count, int)
                and not isinstance(exploration_cell_count, bool)
                else None
            ),
            exploration_required_level=(
                exploration_required_level
                if isinstance(exploration_required_level, int)
                and not isinstance(exploration_required_level, bool)
                else None
            ),
            exploration_current_level=(
                exploration_current_level
                if isinstance(exploration_current_level, int)
                and not isinstance(exploration_current_level, bool)
                else None
            ),
            detail=raw.get("detail") if isinstance(raw.get("detail"), str) else None,
        )

    @staticmethod
    def _parse_event_rewards(raw: object) -> tuple[EventRewardState, ...]:
        if not isinstance(raw, list):
            return ()
        rewards: list[EventRewardState] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            event_key = entry.get("eventKey")
            event_type = entry.get("eventType")
            track = entry.get("track")
            level = entry.get("level")
            reward_key = entry.get("rewardKey")
            reward_amount = entry.get("rewardAmount")
            if (
                all(
                    isinstance(value, str) and value
                    for value in (event_key, event_type, track, reward_key)
                )
                and isinstance(level, int)
                and not isinstance(level, bool)
                and level > 0
                and isinstance(reward_amount, int)
                and not isinstance(reward_amount, bool)
                and reward_amount > 0
            ):
                assert isinstance(event_key, str)
                assert isinstance(event_type, str)
                assert isinstance(track, str)
                assert isinstance(reward_key, str)
                rewards.append(
                    EventRewardState(event_key, event_type, track, level, reward_key, reward_amount)
                )
        return tuple(rewards)

    def _maintain_portal_session(self) -> None:
        now = time.monotonic()
        if now < self._next_portal_maintenance_at:
            return
        self._next_portal_maintenance_at = now + _PORTAL_MAINTENANCE_INTERVAL_SECONDS
        try:
            result = maintain_portal_session(self.port, self.page_title)
        except CdpConnectionError as exc:
            log_event(
                logger,
                logging.DEBUG,
                "runtime.portal_maintenance_failed",
                "Portal session maintenance could not inspect the page.",
                detail=str(exc),
            )
            return
        if result is not None and result[1]:
            portal, _ = result
            log_event(
                logger,
                logging.INFO,
                "runtime.portal_prompt_dismissed",
                "Dismissed a portal inactivity prompt.",
                portal=portal.value,
            )

    @staticmethod
    def _parse_land_expansions(raw: object) -> tuple[LandExpansionCandidate, ...] | None:
        if not isinstance(raw, list):
            return None
        candidates: list[LandExpansionCandidate] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            area_id = entry.get("areaID")
            premium = entry.get("premium")
            cell_count = entry.get("cellCount")
            affordable = entry.get("affordable")
            raw_requirements = entry.get("requirements")
            if (
                not isinstance(area_id, str)
                or not isinstance(premium, bool)
                or not isinstance(cell_count, int)
                or isinstance(cell_count, bool)
                or cell_count <= 0
                or not isinstance(affordable, bool)
                or not isinstance(raw_requirements, list)
            ):
                continue
            requirements: list[ExpansionRequirement] = []
            for requirement in raw_requirements:
                if not isinstance(requirement, dict):
                    break
                key, amount = requirement.get("key"), requirement.get("amount")
                available = requirement.get("available")
                if (
                    not isinstance(key, str)
                    or not isinstance(amount, int)
                    or isinstance(amount, bool)
                    or amount < 0
                    or (
                        available is not None
                        and (
                            not isinstance(available, int)
                            or isinstance(available, bool)
                            or available < 0
                        )
                    )
                ):
                    break
                requirements.append(ExpansionRequirement(key, amount, available))
            else:
                candidates.append(
                    LandExpansionCandidate(
                        area_id,
                        premium,
                        cell_count,
                        tuple(requirements),
                        affordable,
                    )
                )
        return tuple(candidates)

    @staticmethod
    def _parse_farm_visit(raw: object) -> FarmVisitState | None:
        if not isinstance(raw, dict):
            return None
        scene_value = raw.get("scene")
        if not isinstance(scene_value, str):
            return None
        try:
            scene = FarmSceneKind(scene_value)
        except ValueError:
            return None
        actions = []
        for entry in raw.get("actions", []):
            if not isinstance(entry, dict):
                continue
            blueprint_id = entry.get("blueprintID")
            action_type = entry.get("actionType")
            column, row = entry.get("column"), entry.get("row")
            if (
                isinstance(blueprint_id, str)
                and isinstance(action_type, str)
                and isinstance(column, int)
                and isinstance(row, int)
            ):
                object_id = entry.get("objectID")
                actions.append(
                    VisitorActionState(
                        (column, row),
                        blueprint_id,
                        object_id if isinstance(object_id, int) else None,
                        action_type,
                    )
                )
        tickets = raw.get("tickets")
        return FarmVisitState(
            scene,
            tickets if isinstance(tickets, int) else None,
            raw.get("destinationAvailable") is True,
            raw.get("panelOpen") is True,
            tuple(actions),
        )

    def _record_snapshot_metrics(self, metrics: RuntimeReadMetrics) -> None:
        self._snapshot_metrics.append(metrics)
        now = time.monotonic()
        if now - self._last_snapshot_summary_at < 60.0:
            return
        wall = sorted(sample.wall_duration_ms for sample in self._snapshot_metrics)
        renderer = sorted(
            sample.renderer_duration_ms
            for sample in self._snapshot_metrics
            if sample.renderer_duration_ms is not None
        )
        percentile_index = max(0, round((len(wall) - 1) * 0.95))
        renderer_index = max(0, round((len(renderer) - 1) * 0.95)) if renderer else 0
        log_event(
            logger,
            logging.DEBUG,
            "runtime.snapshot_summary",
            "Runtime snapshots: %d samples, %.1fms p95 wall.",
            len(wall),
            wall[percentile_index],
            samples=len(wall),
            wall_p95_ms=wall[percentile_index],
            renderer_p95_ms=renderer[renderer_index] if renderer else None,
            maximum_response_bytes=max(sample.response_bytes for sample in self._snapshot_metrics),
        )
        self._last_snapshot_summary_at = now

    @staticmethod
    def _action_result(raw: object) -> ActionResult:
        if not isinstance(raw, dict):
            return ActionResult(ActionStatus.UNAVAILABLE, "invalid-runtime-response")
        status_value = raw.get("status")
        try:
            status = (
                ActionStatus(status_value)
                if isinstance(status_value, str)
                else ActionStatus.UNAVAILABLE
            )
        except ValueError:
            status = ActionStatus.UNAVAILABLE
        return ActionResult(
            status, raw.get("detail") if isinstance(raw.get("detail"), str) else None
        )

    def submit_item_drop(self, start: GridCoord, end: GridCoord) -> ActionResult:
        return self._action_result(
            self._evaluate_action(_drop_expression(start, end, self._scene_id))
        )

    def dismiss_transient_overlay(self) -> ActionResult:
        return self._action_result(
            self._evaluate_action(_dismiss_overlay_expression(self._scene_id))
        )

    def dismiss_event_introduction(self, event_key: str) -> ActionResult:
        return self._action_result(
            self._evaluate_action(_event_action_expression("dismiss", event_key))
        )

    def enter_event(self, event_key: str) -> ActionResult:
        result = self._action_result(
            self._evaluate_action(_event_action_expression("enter", event_key))
        )
        if result.submitted:
            self._scene_refresh_origin_id = self._scene_id
            self._scene_refresh_required = True
        return result

    def explore_event(self, event_key: str, area_id: str, required_level: int) -> ActionResult:
        return self._action_result(
            self._evaluate_action(
                _event_action_expression(
                    "explore",
                    event_key,
                    areaID=area_id,
                    requiredLevel=required_level,
                )
            )
        )

    def return_from_event(self, event_key: str) -> ActionResult:
        result = self._action_result(
            self._evaluate_action(_event_action_expression("return", event_key))
        )
        if result.submitted:
            self._scene_refresh_origin_id = self._scene_id
            self._scene_refresh_required = True
        return result

    def claim_event_reward(self, reward: EventRewardState) -> ActionResult:
        return self._action_result(
            self._evaluate_action(
                _event_reward_claim_expression(
                    {
                        "eventKey": reward.event_key,
                        "eventType": reward.event_type,
                        "track": reward.track,
                        "level": reward.level,
                        "rewardKey": reward.reward_key,
                        "rewardAmount": reward.reward_amount,
                    }
                )
            )
        )

    def submit_storage_bubble_pop(self, expected_object_id: int) -> ActionResult:
        return self._action_result(
            self._evaluate_action(
                _storage_bubble_pop_expression(expected_object_id, self._scene_id)
            )
        )

    def submit_board_interaction(
        self,
        coord: GridCoord,
        expected_kind: InteractionTargetKind,
        expected_blueprint_id: str,
        expected_object_id: int | None,
    ) -> ActionResult:
        return self._action_result(
            self._evaluate_action(
                _interaction_expression(
                    coord,
                    expected_kind,
                    expected_blueprint_id,
                    expected_object_id,
                    self._scene_id,
                ),
            )
        )

    def submit_item_removal(
        self,
        coord: GridCoord,
        expected_blueprint_id: str,
        expected_object_id: int | None,
    ) -> ActionResult:
        return self._action_result(
            self._evaluate_action(
                _removal_expression(
                    coord,
                    expected_blueprint_id,
                    expected_object_id,
                    self._scene_id,
                ),
            )
        )

    def spawn_supply_crates(self, limit: int) -> CrateSpawnResult:
        spawned = 0
        remaining: int | None = None
        available_before: int | None = None
        last_status = ActionStatus.REJECTED
        detail: str | None = None
        claim_limit = max(0, int(limit))
        for claim_index in range(claim_limit):
            raw = self._evaluate_action(_crate_expression(1, self._scene_id))
            if not isinstance(raw, dict):
                return CrateSpawnResult(
                    ActionStatus.UNAVAILABLE,
                    spawned,
                    remaining,
                    "invalid-runtime-response",
                )
            status_value = raw.get("status")
            try:
                last_status = (
                    ActionStatus(status_value)
                    if isinstance(status_value, str)
                    else ActionStatus.UNAVAILABLE
                )
            except ValueError:
                last_status = ActionStatus.UNAVAILABLE
            spawned += max(0, int(raw.get("spawned", 0)))
            remaining = raw.get("remaining") if isinstance(raw.get("remaining"), int) else None
            raw_available_before = raw.get("availableBefore")
            if (
                available_before is None
                and isinstance(raw_available_before, int)
                and not isinstance(raw_available_before, bool)
            ):
                available_before = raw_available_before
            detail = raw.get("detail") if isinstance(raw.get("detail"), str) else None
            if last_status is not ActionStatus.SUBMITTED or remaining == 0:
                break
            if claim_index + 1 < claim_limit:
                delay = random.uniform(self._crate_delay_min, self._crate_delay_max)
                if self._cancel_event is not None:
                    if self._cancel_event.wait(delay):
                        break
                else:
                    time.sleep(delay)
        return CrateSpawnResult(
            ActionStatus.SUBMITTED if spawned else last_status,
            spawned,
            remaining,
            detail,
            available_before,
        )

    def read_shop_orders(self) -> tuple[ShopOrder, ...] | None:
        raw = self._evaluate(_READ_SHOP_ORDERS_EXPRESSION)
        return self._parse_shop_orders(raw)

    @staticmethod
    def _parse_shop_orders(raw: object) -> tuple[ShopOrder, ...] | None:
        if not isinstance(raw, list):
            return None
        orders: list[ShopOrder] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            state_value = entry.get("state")
            if not isinstance(state_value, str):
                continue
            try:
                state = ShopOrderState(state_value)
            except ValueError:
                continue
            shop_id = entry.get("shopID")
            recipe_id = entry.get("recipeID")
            if not isinstance(shop_id, str) or not isinstance(recipe_id, str):
                continue
            ingredients = tuple(
                ShopIngredient(item["itemID"], item["required"], item["available"])
                for item in entry.get("ingredients", [])
                if isinstance(item, dict)
                and isinstance(item.get("itemID"), str)
                and isinstance(item.get("required"), int)
                and isinstance(item.get("available"), int)
            )
            reward_ids = tuple(
                reward_id for reward_id in entry.get("rewardIDs", []) if isinstance(reward_id, str)
            )
            duration = entry.get("durationSeconds")
            remaining = entry.get("remainingSeconds")
            orders.append(
                ShopOrder(
                    shop_id,
                    recipe_id,
                    state,
                    ingredients,
                    reward_ids,
                    duration if isinstance(duration, int) else 0,
                    float(remaining) if isinstance(remaining, int | float) else None,
                )
            )
        return tuple(orders)

    def start_shop_order(self, shop_id: str, recipe_id: str) -> ActionResult:
        return self._shop_action_result(_shop_start_expression(shop_id, recipe_id, self._scene_id))

    def claim_shop_order(self, shop_id: str, recipe_id: str) -> ActionResult:
        return self._shop_action_result(_shop_claim_expression(shop_id, recipe_id, self._scene_id))

    def read_marketplace_offers(self) -> tuple[MarketplaceLiveOffer, ...] | None:
        return parse_marketplace_offers(self._evaluate(_READ_MARKETPLACE_EXPRESSION, retry=False))

    def submit_marketplace_purchase(self, action: MarketplaceAction) -> ActionResult:
        raw = self._evaluate_action(marketplace_purchase_expression(action, self._scene_id))
        return parse_marketplace_action_result(raw)

    def submit_land_expansion(
        self, candidate: LandExpansionCandidate, minimum_balance_after: int
    ) -> ActionResult:
        raw = self._evaluate_action(
            land_expansion_action_expression(
                candidate.area_id,
                candidate.premium,
                tuple(
                    (requirement.key, requirement.amount) for requirement in candidate.requirements
                ),
                minimum_balance_after,
                self._scene_id,
            ),
        )
        return self._action_result(raw)

    def open_farm_visit(self) -> ActionResult:
        return self._action_result(
            self._evaluate_action(_farm_visit_action_expression("open", self._scene_id))
        )

    def start_farm_visit(self) -> ActionResult:
        return self._action_result(
            self._evaluate_action(_farm_visit_action_expression("start", self._scene_id))
        )

    def close_farm_visit(self) -> ActionResult:
        return self._action_result(
            self._evaluate_action(_farm_visit_action_expression("close", self._scene_id))
        )

    def submit_visitor_action(self, action: VisitorActionState) -> ActionResult:
        return self._action_result(
            self._evaluate_action(
                _farm_visit_action_expression(
                    "claim",
                    self._scene_id,
                    column=action.coord[0],
                    row=action.coord[1],
                    blueprintID=action.blueprint_id,
                    objectID=action.object_id,
                    actionType=action.action_type,
                ),
            )
        )

    def return_from_farm_visit(self) -> ActionResult:
        return self._action_result(
            self._evaluate_action(_farm_visit_action_expression("return", self._scene_id))
        )

    def _shop_action_result(self, expression: str) -> ActionResult:
        raw = self._evaluate_action(expression)
        if not isinstance(raw, dict):
            return ActionResult(ActionStatus.UNAVAILABLE, "invalid-runtime-response")
        status_value = raw.get("status")
        if not isinstance(status_value, str):
            return ActionResult(ActionStatus.UNAVAILABLE, "invalid-runtime-status")
        try:
            status = ActionStatus(status_value)
        except ValueError:
            status = ActionStatus.UNAVAILABLE
        detail = raw.get("detail")
        return ActionResult(status, detail if isinstance(detail, str) else None)
