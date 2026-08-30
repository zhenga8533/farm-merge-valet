"""CDP implementation of the adapter-neutral automation runtime."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from threading import Event

from farm_merge_valet.automation.runtime import (
    ActionResult,
    ActionStatus,
    CrateSpawnResult,
    RuntimeHealth,
)
from farm_merge_valet.cdp.board_store import arm_board_store, read_board_state
from farm_merge_valet.cdp.evaluation import apply_background_overrides, evaluate
from farm_merge_valet.cdp.inventory_store import arm_crate_inventory, read_energy
from farm_merge_valet.cdp.scripts import (
    _BOARD_ARMED_EXPRESSION,
    _DISCOVER_EXPRESSION,
    _DISCOVERY_DIAGNOSTICS_EXPRESSION,
    _HEALTH_EXPRESSION,
    _HEARTBEAT_EXPRESSION,
    _READ_SHOP_ORDERS_EXPRESSION,
    _READ_WORKERS_EXPRESSION,
    _crate_expression,
    _drop_expression,
    _interaction_expression,
    _removal_expression,
    _shop_claim_expression,
    _shop_start_expression,
)
from farm_merge_valet.cdp.targets import read_background_flag_status
from farm_merge_valet.core.items import GridCoord, InteractionTargetKind
from farm_merge_valet.core.obstacles import WorkerState
from farm_merge_valet.core.shops import ShopIngredient, ShopOrder, ShopOrderState
from farm_merge_valet.observability.logging import log_event

logger = logging.getLogger(__name__)


class GameRuntimeAdapter:
    """Discovers and calls the active game's own interaction pipeline."""

    def __init__(
        self,
        port: int,
        page_title: str | None = None,
        *,
        crate_delay_min: float = 0.05,
        crate_delay_max: float = 0.2,
    ) -> None:
        self.port = port
        self.page_title = page_title
        self._crate_delay_min = crate_delay_min
        self._crate_delay_max = crate_delay_max
        self._scene_id: int | None = None
        self._last_heartbeat: int | None = None
        self._discovery_detail: str | None = "runtime-discovery-not-run"
        self._cancel_event: Event | None = None

    def set_cancel_event(self, cancel_event: Event) -> None:
        self._cancel_event = cancel_event

    def configure_crate_delays(self, minimum: float, maximum: float) -> None:
        if minimum < 0 or maximum < minimum:
            raise ValueError("crate delay range is invalid")
        self._crate_delay_min = minimum
        self._crate_delay_max = maximum

    def read_board_state(self):
        return read_board_state(
            self.port,
            self.page_title,
            cancel_event=self._cancel_event,
        )

    def read_energy(self) -> int | None:
        return read_energy(self.port, self.page_title, cancel_event=self._cancel_event)

    def read_workers(self) -> WorkerState | None:
        raw = self._evaluate(_READ_WORKERS_EXPRESSION)
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

    def read_background_flag_status(self) -> dict[str, object]:
        return read_background_flag_status(self.port, cancel_event=self._cancel_event)

    def _evaluate(self, expression: str, *, timeout: float = 5.0) -> object:
        return evaluate(
            self.port,
            expression,
            self.page_title,
            timeout=timeout,
            cancel_event=self._cancel_event,
        )

    def discover(self, cancelled: Callable[[], bool] | None = None) -> RuntimeHealth:
        started = time.monotonic()

        def is_cancelled() -> bool:
            return bool(
                (cancelled is not None and cancelled())
                or (self._cancel_event is not None and self._cancel_event.is_set())
            )

        if is_cancelled():
            return self.read_runtime_health()
        apply_background_overrides(self.port, self.page_title, cancel_event=self._cancel_event)
        if is_cancelled():
            return self.read_runtime_health()
        cached_board_is_current = self._evaluate(_BOARD_ARMED_EXPRESSION) is True
        if not cached_board_is_current:
            log_event(
                logger,
                logging.INFO,
                "runtime.board_cache_stale",
                "Cached board is absent or stale; locating the active board map.",
            )
            arm_board_store(self.port, self.page_title, cancel_event=self._cancel_event)
        if is_cancelled():
            return self.read_runtime_health()
        if (
            self._evaluate(
                "Boolean(window.__fmvCrateInventoryItem && window.__fmvEnergyInventoryItem)"
            )
            is not True
        ):
            arm_crate_inventory(self.port, self.page_title, cancel_event=self._cancel_event)
        if is_cancelled():
            return self.read_runtime_health()
        raw = self._evaluate(_DISCOVER_EXPRESSION)
        heartbeat_sample = self._evaluate(_HEARTBEAT_EXPRESSION)
        if isinstance(heartbeat_sample, dict) and isinstance(heartbeat_sample.get("frame"), int):
            self._last_heartbeat = heartbeat_sample["frame"]
        if isinstance(raw, dict):
            if isinstance(raw.get("sceneId"), int):
                self._scene_id = raw["sceneId"]
            detail = raw.get("detail")
            self._discovery_detail = detail if isinstance(detail, str) else None
        else:
            self._discovery_detail = "invalid-runtime-discovery-response"
        # A second sample makes the initial health result meaningful when the
        # animation loop is running at normal foreground cadence.
        if not is_cancelled():
            time.sleep(0.05)
        health = self.read_runtime_health()
        elapsed = time.monotonic() - started
        level = logging.DEBUG if cached_board_is_current and elapsed < 1.0 else logging.INFO
        log_event(
            logger,
            level,
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

    def inspect_runtime(self) -> dict[str, object]:
        """Return the bounded discovery stages without traversing the JS heap."""
        raw = self._evaluate(_DISCOVERY_DIAGNOSTICS_EXPRESSION)
        return raw if isinstance(raw, dict) else {"strategy": "invalid-response"}

    def read_runtime_health(self) -> RuntimeHealth:
        raw = self._evaluate(_HEALTH_EXPRESSION)
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
        advancing = (
            heartbeat is not None
            and self._last_heartbeat is not None
            and (heartbeat > self._last_heartbeat)
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
        crate_spawn = raw.get("crateSpawn") is True
        board = raw.get("board") is True
        return RuntimeHealth(
            available=board
            and (item_drop or interaction or removal or reward_interaction or crate_spawn)
            and scene_id is not None,
            scene_id=scene_id,
            board_available=board,
            item_drop_available=item_drop,
            crate_spawn_available=crate_spawn,
            inventory_available=raw.get("inventory") is True,
            heartbeat=heartbeat,
            heartbeat_age_ms=(
                float(raw["heartbeatAgeMs"])
                if isinstance(raw.get("heartbeatAgeMs"), int | float)
                else None
            ),
            heartbeat_advancing=advancing,
            heartbeat_installed=raw.get("heartbeatInstalled") is True,
            detail=self._discovery_detail,
            item_action_busy=raw.get("itemActionBusy") is True,
            interaction_available=interaction,
            shop_available=raw.get("shopOrders") is True,
            removal_available=removal,
            reward_interaction_available=reward_interaction,
            obstacle_clear_available=raw.get("obstacleClear") is True,
        )

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
        return self._action_result(self._evaluate(_drop_expression(start, end, self._scene_id)))

    def submit_board_interaction(
        self,
        coord: GridCoord,
        expected_kind: InteractionTargetKind,
        expected_blueprint_id: str,
        expected_object_id: int | None,
    ) -> ActionResult:
        return self._action_result(
            self._evaluate(
                _interaction_expression(
                    coord,
                    expected_kind,
                    expected_blueprint_id,
                    expected_object_id,
                    self._scene_id,
                )
            )
        )

    def submit_item_removal(
        self,
        coord: GridCoord,
        expected_blueprint_id: str,
        expected_object_id: int | None,
    ) -> ActionResult:
        return self._action_result(
            self._evaluate(
                _removal_expression(
                    coord,
                    expected_blueprint_id,
                    expected_object_id,
                    self._scene_id,
                )
            )
        )

    def spawn_supply_crates(self, limit: int) -> CrateSpawnResult:
        spawned = 0
        remaining: int | None = None
        last_status = ActionStatus.REJECTED
        detail: str | None = None
        claim_limit = max(0, int(limit))
        for claim_index in range(claim_limit):
            raw = self._evaluate(_crate_expression(1, self._scene_id))
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
        )

    def read_shop_orders(self) -> tuple[ShopOrder, ...] | None:
        raw = self._evaluate(_READ_SHOP_ORDERS_EXPRESSION)
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

    def _shop_action_result(self, expression: str) -> ActionResult:
        raw = self._evaluate(expression)
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
