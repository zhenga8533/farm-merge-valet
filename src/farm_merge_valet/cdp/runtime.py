"""Typed access to Farm Merge Valley's in-page interaction runtime."""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import Event
from typing import Protocol

from farm_merge_valet.cdp.board_store import arm_board_store
from farm_merge_valet.cdp.client import apply_background_overrides, evaluate
from farm_merge_valet.cdp.inventory_store import arm_crate_inventory
from farm_merge_valet.core.board import ClaimTargetKind, GridCoord
from farm_merge_valet.logging_setup import log_event

logger = logging.getLogger(__name__)


class ActionStatus(StrEnum):
    SUBMITTED = "submitted"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"
    STALE_SOURCE = "stale-source"
    INVALID_DESTINATION = "invalid-destination"
    INVALID_TARGET = "invalid-target"


@dataclass(frozen=True)
class ActionResult:
    status: ActionStatus
    detail: str | None = None

    @property
    def submitted(self) -> bool:
        return self.status is ActionStatus.SUBMITTED


@dataclass(frozen=True)
class CrateSpawnResult:
    status: ActionStatus
    spawned: int
    remaining: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class RuntimeHealth:
    available: bool
    scene_id: int | None
    board_available: bool
    item_drop_available: bool
    crate_spawn_available: bool
    inventory_available: bool
    heartbeat: int | None
    heartbeat_age_ms: float | None
    heartbeat_advancing: bool
    heartbeat_installed: bool = False
    detail: str | None = None
    item_action_busy: bool = False
    claim_available: bool = False


_DISCOVER_EXPRESSION = r"""
(() => {
  const board = window.__fmvBoardCells;
  const inventory = window.__fmvCrateInventoryItem;
  if (!(board instanceof Map)) return {status: 'unavailable', detail: 'board-map-not-found'};
  const subscribers = (signal) => Array.isArray(signal?._subscribers)
    ? signal._subscribers : [];
  const validItemHandler = (value) => value &&
    typeof value._onGesturePick === 'function' &&
    typeof value._onGestureDrop === 'function' &&
    value._services?.mapGrid && value._services?.interactionService;
  const validClaimHandler = (value) => validItemHandler(value) &&
    typeof value._simulateClick === 'function';

  // Board-cell signals are owned by the active map-grid service. This is a
  // bounded path into the scene's service container and does not walk the heap.
  let services = null;
  for (const cell of board.values()) {
    for (const key of ['onContentAdded', 'onContentRemoved', 'onChanged']) {
      for (const subscriber of subscribers(cell?.[key])) {
        const candidate = subscriber?.context?._services;
        if (candidate?.mapGrid && candidate?.interactionService) {
          services = candidate;
          break;
        }
      }
      if (services) break;
    }
    if (services) break;
  }

  const interaction = services?.interactionService;
  const pickContexts = subscribers(interaction?.onGesturePick)
    .map((subscriber) => subscriber?.context).filter(validItemHandler);
  const dropContexts = new Set(subscribers(interaction?.onGestureDrop)
    .map((subscriber) => subscriber?.context));
  const itemHandler = pickContexts.find((candidate) => dropContexts.has(candidate)) || null;
  const claimHandler = pickContexts.find(validClaimHandler) || null;

  // The gameplay HUD owns the authoritative crate event. Visual button states
  // can remain subscribed to inventory updates after their private event set
  // has been disconnected, so require a live gameplay subscriber here.
  const crateSignal = services?.hudService?._commonEvents?.spawnCrates;
  const crateSubscribers = subscribers(crateSignal);
  const validCrateSignal = typeof crateSignal?.fire === 'function' &&
    crateSubscribers.length > 0 ? crateSignal : null;

  const screen = services?.hudService?._screen || services?.mapGridView?._view || itemHandler;
  window.__fmvGameplayServices = services;
  window.__fmvGameplayMapScreen = screen;
  window.__fmvItemInteractionHandler = itemHandler;
  window.__fmvClaimInteractionHandler = claimHandler;
  window.__fmvCrateSpawnSignal = validCrateSignal;
  window.__fmvRuntimeBoard = board;
  window.__fmvRuntimeSceneIds ||= new WeakMap();
  window.__fmvNextRuntimeSceneId ||= 1;
  const identity = services?.mapGrid || screen || itemHandler || board;
  if (!window.__fmvRuntimeSceneIds.has(identity)) {
    window.__fmvRuntimeSceneIds.set(identity, window.__fmvNextRuntimeSceneId++);
  }
  window.__fmvRuntimeSceneIdentity = window.__fmvRuntimeSceneIds.get(identity);
  const missing = [];
  if (!services) missing.push('gameplay-services');
  if (!itemHandler) missing.push('item-interaction-handler');
  if (!claimHandler) missing.push('claim-interaction-handler');
  if (!validCrateSignal) missing.push('crate-spawn-signal');
  if (!inventory) missing.push('crate-inventory');
  window.__fmvRuntimeDiscovery = {
    strategy: 'bounded-signal-anchors',
    services: Boolean(services),
    pickSubscribers: subscribers(interaction?.onGesturePick).length,
    dropSubscribers: subscribers(interaction?.onGestureDrop).length,
    itemDrop: Boolean(itemHandler),
    claim: Boolean(claimHandler),
    crateSpawn: Boolean(validCrateSignal),
    crateSubscribers: crateSubscribers.length,
    inventory: Boolean(inventory),
    missing,
  };
  return {
    status: itemHandler ? 'found' : 'unavailable',
    sceneId: window.__fmvRuntimeSceneIdentity,
    board: true,
    itemDrop: Boolean(itemHandler),
    claim: Boolean(claimHandler),
    crateSpawn: Boolean(validCrateSignal),
    inventory: Boolean(inventory),
    detail: missing.length ? `${missing.join(',')}-not-found` : null,
    discovery: window.__fmvRuntimeDiscovery,
  };
})()
"""

_BOARD_ARMED_EXPRESSION = r"""
(() => {
  const board = window.__fmvBoardCells;
  const mapGrid = window.__fmvGameplayServices?.mapGrid;
  if (!(board instanceof Map) || window.__fmvRuntimeBoard !== board ||
      mapGrid?._cells !== board || mapGrid._isActive === false) return false;
  const first = board.values().next().value;
  return Boolean(first && ['column', 'row', '_content', '_neighbors']
    .every((key) => key in first));
})()
"""

_DISCOVERY_DIAGNOSTICS_EXPRESSION = r"""
(() => window.__fmvRuntimeDiscovery || {
  strategy: 'not-run', services: false, pickSubscribers: 0,
  dropSubscribers: 0, itemDrop: false, crateSpawn: false,
  claim: false,
  inventory: Boolean(window.__fmvCrateInventoryItem), missing: ['discovery-not-run'],
})()
"""

_HEARTBEAT_EXPRESSION = r"""
(() => {
  if (!window.__fmvHeartbeatInstalled) {
    window.__fmvHeartbeatInstalled = true;
    window.__fmvHeartbeat = {frame: 0, timestamp: performance.now()};
    const tick = (timestamp) => {
      window.__fmvHeartbeat.frame += 1;
      window.__fmvHeartbeat.timestamp = timestamp;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  const beat = window.__fmvHeartbeat;
  return {frame: beat.frame, ageMs: Math.max(0, performance.now() - beat.timestamp)};
})()
"""

_HEALTH_EXPRESSION = r"""
(() => {
  const board = window.__fmvBoardCells;
  const handler = window.__fmvItemInteractionHandler;
  const claimHandler = window.__fmvClaimInteractionHandler;
  const crateSignal = window.__fmvCrateSpawnSignal;
  const beat = window.__fmvHeartbeat;
  const services = window.__fmvGameplayServices;
  const scene = window.__fmvGameplayMapScreen;
  const subscribers = (signal) => Array.isArray(signal?._subscribers)
    ? signal._subscribers : [];
  const firstCell = board instanceof Map ? board.values().next().value : null;
  const boardShapeIsValid = firstCell &&
    ['column', 'row', '_content', '_neighbors'].every((key) => key in firstCell);
  const currentBoard = boardShapeIsValid && window.__fmvRuntimeBoard === board &&
    services?.mapGrid?._cells === board && services.mapGrid._isActive !== false;
  const interaction = services?.interactionService;
  const currentItemHandler = currentBoard && handler?._services === services &&
    subscribers(interaction?.onGesturePick).some((entry) => entry?.context === handler) &&
    subscribers(interaction?.onGestureDrop).some((entry) => entry?.context === handler);
  const currentClaimHandler = currentBoard && claimHandler?._services === services &&
    typeof claimHandler._simulateClick === 'function' &&
    subscribers(interaction?.onGestureTap).some((entry) => entry?.context === claimHandler);
  const currentCrateSignal = currentBoard &&
    services?.hudService?._commonEvents?.spawnCrates === crateSignal &&
    typeof crateSignal?.fire === 'function' && subscribers(crateSignal).length > 0;
  const identity = currentBoard && (services.mapGrid || scene || handler || board);
  const sceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;
  return {
    sceneId,
    board: board instanceof Map && currentBoard,
    itemDrop: Boolean(currentItemHandler),
    claim: Boolean(currentClaimHandler),
    itemActionBusy: Boolean(currentItemHandler && (
      handler.busy || handler.isBusy?.() || handler.dragging || handler._dragging ||
      handler._currentObject || handler._originCell
    )),
    crateSpawn: Boolean(currentCrateSignal),
    inventory: Boolean(window.__fmvCrateInventoryItem),
    heartbeat: beat ? beat.frame : null,
    heartbeatAgeMs: beat ? Math.max(0, performance.now() - beat.timestamp) : null,
    heartbeatInstalled: Boolean(window.__fmvHeartbeatInstalled && beat),
  };
})()
"""


def _coord(value: GridCoord) -> str:
    return json.dumps([int(value[0]), int(value[1])])


def _drop_expression(start: GridCoord, end: GridCoord, scene_id: int | None) -> str:
    return f"""
(async () => {{
  const startCoord = {_coord(start)};
  const endCoord = {_coord(end)};
  const board = window.__fmvBoardCells;
  const handler = window.__fmvItemInteractionHandler;
  const identity = window.__fmvGameplayServices?.mapGrid ||
    window.__fmvGameplayMapScreen || handler || board;
  const currentSceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;
  if (!board || window.__fmvRuntimeBoard !== board || !handler ||
      currentSceneId !== {json.dumps(scene_id)})
    return {{status: 'unavailable', detail: 'runtime-scene-changed'}};
  const findCell = ([column, row]) => {{
    for (const cell of board.values())
      if (cell.column === column && cell.row === row) return cell;
    return null;
  }};
  const source = findCell(startCoord);
  const destination = findCell(endCoord);
  if (!source || !source._content || !source._content.hasBehavior?.('movable'))
    return {{status: 'stale-source'}};
  const pickedObject = source._content;
  if (!destination || destination === source)
    return {{status: 'invalid-destination'}};
  if (destination._content && !destination._content.hasBehavior?.('dropable'))
    return {{status: 'invalid-destination'}};
  if (handler.busy || handler.isBusy?.() || handler.dragging || handler._dragging ||
      handler._currentObject || handler._originCell)
    return {{status: 'busy'}};
  let restorePickedObject = () => {{}};
  try {{
    const services = handler._services;
    const projection = services?.axonometricProjection;
    const camera = services?.cameraService;
    const interaction = services?.interactionService;
    if (!projection || !camera || !interaction ||
        typeof projection.getWorldPosition !== 'function' ||
        typeof camera.getWorldToScreenPosition !== 'function' ||
        typeof handler._onGesturePick !== 'function' ||
        typeof handler._onGestureDrop !== 'function')
      return {{status: 'unavailable', detail: 'interaction-pipeline-incomplete'}};

    const worldPosition = (cell) => {{
      const origin = projection.getWorldPosition(cell.column, cell.row);
      const nextColumn = projection.getWorldPosition(cell.column + 1, cell.row);
      const nextRow = projection.getWorldPosition(cell.column, cell.row + 1);
      return {{
        x: origin.x + (nextColumn.x + nextRow.x - 2 * origin.x) / 4,
        y: origin.y + (nextColumn.y + nextRow.y - 2 * origin.y) / 4,
      }};
    }};
    const screenPosition = (cell) => camera.getWorldToScreenPosition(worldPosition(cell));
    const sourceWorldPosition = worldPosition(source);
    const sourcePosition = camera.getWorldToScreenPosition(sourceWorldPosition);
    if (!sourcePosition)
      return {{status: 'rejected', detail: 'cell-projection-failed'}};

    const hadOwnTargetResolver = Object.hasOwn(handler, '_getGestureTargetData');
    const originalTargetResolver = handler._getGestureTargetData;
    const emit = (signal, fallback, event) => {{
      if (typeof signal?.fire === 'function') signal.fire(event);
      else fallback(event);
    }};
    const nextFrame = () => new Promise((resolve) => requestAnimationFrame(resolve));
    restorePickedObject = () => {{
      if (handler._currentObject !== pickedObject || handler._originCell !== source) return;
      try {{
        const restorePosition = screenPosition(source);
        handler._onGestureDrop({{
          startPosition: restorePosition,
          position: restorePosition,
          dragType: interaction._dragType,
          gesture: interaction._gesture,
        }});
      }} catch {{}}
    }};
    try {{
      handler._getGestureTargetData = () => ({{
        position: {{column: source.column, row: source.row}},
        worldPosition: sourceWorldPosition,
        gameObject: source._content,
      }});
      emit(interaction.onGesturePick, (event) => handler._onGesturePick(event), {{
        startPosition: sourcePosition,
        position: sourcePosition,
        gesture: interaction._gesture,
      }});
    }} finally {{
      if (hadOwnTargetResolver) handler._getGestureTargetData = originalTargetResolver;
      else delete handler._getGestureTargetData;
    }}
    if (handler._currentObject !== pickedObject || handler._originCell !== source)
      return {{status: 'rejected', detail: 'source-pick-rejected'}};
    await nextFrame();
    if (handler._currentObject !== pickedObject || handler._originCell !== source)
      return {{status: 'rejected', detail: 'source-pick-cancelled'}};
    const destinationPosition = screenPosition(destination);
    if (!destinationPosition)
      return {{status: 'rejected', detail: 'cell-projection-failed'}};
    const destinationWorldCheck = camera.getScreenToWorldPosition(destinationPosition);
    const destinationIndexCheck = projection.getProjectedIndices(destinationWorldCheck);
    if (destinationIndexCheck?.column !== destination.column ||
        destinationIndexCheck?.row !== destination.row) {{
      restorePickedObject();
      return {{status: 'rejected', detail: 'destination-projection-mismatch'}};
    }}
    handler._onGestureDrag({{
      startPosition: sourcePosition,
      position: destinationPosition,
      dragType: interaction._dragType,
      gesture: interaction._gesture,
    }});
    if (handler._currentObject !== pickedObject || handler._originCell !== source) {{
      restorePickedObject();
      return {{status: 'rejected', detail: 'source-drag-cancelled'}};
    }}
    handler._onGestureDrop({{
      startPosition: sourcePosition,
      position: destinationPosition,
      dragType: interaction._dragType,
      gesture: interaction._gesture,
    }});
    return {{status: 'submitted'}};
  }} catch (error) {{
    restorePickedObject();
    return {{status: 'rejected', detail: String(error?.message || error)}};
  }}
}})()
"""


def _crate_expression(limit: int, scene_id: int | None) -> str:
    return f"""
(async () => {{
  const board = window.__fmvBoardCells;
  const signal = window.__fmvCrateSpawnSignal;
  const inventory = window.__fmvCrateInventoryItem;
  const identity = window.__fmvGameplayServices?.mapGrid ||
    window.__fmvGameplayMapScreen || window.__fmvItemInteractionHandler || board;
  const currentSceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;
  if (!board || window.__fmvRuntimeBoard !== board || !signal || !inventory ||
      currentSceneId !== {json.dumps(scene_id)})
    return {{status: 'unavailable', spawned: 0, detail: 'runtime-scene-changed'}};
  if (window.__fmvGameplayServices?.hudService?._commonEvents?.spawnCrates !== signal)
    return {{status: 'unavailable', spawned: 0, remaining: inventory.amount,
      detail: 'crate-signal-not-current'}};
  const subscribers = Array.isArray(signal._subscribers) ? signal._subscribers : [];
  if (!subscribers.length)
    return {{status: 'unavailable', spawned: 0, remaining: inventory.amount,
      detail: 'crate-signal-has-no-subscribers'}};
  if (subscribers.some((entry) => entry?.context?._executing))
    return {{status: 'busy', spawned: 0, remaining: inventory.amount}};
  if (typeof signal.fire !== 'function')
    return {{status: 'unavailable', spawned: 0, detail: 'crate-signal-not-found'}};
  let spawned = 0;
  const countEmpty = () => {{
    let count = 0;
    for (const cell of board.values()) if (!cell._content) count += 1;
    return count;
  }};
  const waitForChange = (beforeAmount, beforeEmpty) => new Promise((resolve) => {{
    const deadline = performance.now() + 4000;
    const check = () => {{
      const empty = countEmpty();
      if (inventory.amount < beforeAmount || empty < beforeEmpty) return resolve(true);
      if (performance.now() >= deadline) return resolve(false);
      setTimeout(check, 25);
    }};
    check();
  }});
  try {{
    while (spawned < {max(0, int(limit))}) {{
      const amount = inventory.amount;
      const empty = countEmpty();
      if (!(amount > 0))
        return {{status: 'rejected', spawned, remaining: amount,
          detail: 'no-supply-crates'}};
      if (empty === 0)
        return {{status: 'rejected', spawned, remaining: amount,
          detail: 'no-open-cells'}};
      signal.fire({{}});
      if (!await waitForChange(amount, empty))
        return {{status: 'rejected', spawned, remaining: inventory.amount,
          detail: 'no-authoritative-crate-change'}};
      spawned += 1;
    }}
    return {{status: spawned ? 'submitted' : 'rejected', spawned, remaining: inventory.amount}};
  }} catch (error) {{
    return {{status: 'rejected', spawned, remaining: inventory.amount,
      detail: String(error?.message || error)}};
  }}
}})()
"""


def _claim_expression(
    coord: GridCoord,
    expected_kind: ClaimTargetKind,
    expected_blueprint_id: str,
    expected_object_id: int | None,
    scene_id: int | None,
) -> str:
    return f"""
(() => {{
  const coord = {_coord(coord)};
  const expectedKind = {json.dumps(expected_kind.value)};
  const expectedBlueprintID = {json.dumps(expected_blueprint_id)};
  const expectedObjectID = {json.dumps(expected_object_id)};
  const board = window.__fmvBoardCells;
  const handler = window.__fmvClaimInteractionHandler;
  const identity = window.__fmvGameplayServices?.mapGrid ||
    window.__fmvGameplayMapScreen || handler || board;
  const currentSceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;
  if (!board || window.__fmvRuntimeBoard !== board || !handler ||
      currentSceneId !== {json.dumps(scene_id)})
    return {{status: 'unavailable', detail: 'runtime-scene-changed'}};
  let cell = null;
  for (const candidate of board.values()) {{
    if (candidate.column === coord[0] && candidate.row === coord[1]) {{
      cell = candidate;
      break;
    }}
  }}
  const content = cell?._content;
  if (!content || content._blueprintID !== expectedBlueprintID ||
      (expectedObjectID !== null && content.id !== expectedObjectID))
    return {{status: 'stale-source'}};
  const producer = content.hasBehavior?.('harvestable') &&
    ['animal', 'crop'].includes(content.getBehavior?.('harvestable')?._data?.harvestableType);
  const valid = expectedKind === 'immediate'
    ? content.hasBehavior?.('collectable')
    : expectedKind === 'producer'
      ? producer && !content.hasBehavior?.('cooldown') &&
        !content.hasBehavior?.('depleted')
      : expectedKind === 'depleted-producer'
        ? producer && content.hasBehavior?.('depleted')
        : false;
  if (!valid) return {{status: 'invalid-target'}};
  if (handler.busy || handler.isBusy?.() || handler.dragging || handler._dragging ||
      handler._currentObject || handler._originCell)
    return {{status: 'busy'}};
  try {{
    handler._simulateClick(content);
    return {{status: 'submitted'}};
  }} catch (error) {{
    return {{status: 'rejected', detail: String(error?.message || error)}};
  }}
}})()
"""


class GameRuntime(Protocol):
    """Action and health boundary consumed by the automation loop."""

    def set_cancel_event(self, cancel_event: Event) -> None: ...

    def discover(self, cancelled: Callable[[], bool] | None = None) -> RuntimeHealth: ...

    def read_runtime_health(self) -> RuntimeHealth: ...

    def submit_item_drop(self, start: GridCoord, end: GridCoord) -> ActionResult: ...

    def submit_board_claim(
        self,
        coord: GridCoord,
        expected_kind: ClaimTargetKind,
        expected_blueprint_id: str,
        expected_object_id: int | None,
    ) -> ActionResult: ...

    def spawn_supply_crates(self, limit: int) -> CrateSpawnResult: ...


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
        if self._evaluate("Boolean(window.__fmvCrateInventoryItem)") is not True:
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
        claim = raw.get("claim") is True
        crate_spawn = raw.get("crateSpawn") is True
        board = raw.get("board") is True
        return RuntimeHealth(
            available=board and (item_drop or claim or crate_spawn) and scene_id is not None,
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
            claim_available=claim,
        )

    def submit_item_drop(self, start: GridCoord, end: GridCoord) -> ActionResult:
        raw = self._evaluate(_drop_expression(start, end, self._scene_id))
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

    def submit_board_claim(
        self,
        coord: GridCoord,
        expected_kind: ClaimTargetKind,
        expected_blueprint_id: str,
        expected_object_id: int | None,
    ) -> ActionResult:
        raw = self._evaluate(
            _claim_expression(
                coord,
                expected_kind,
                expected_blueprint_id,
                expected_object_id,
                self._scene_id,
            )
        )
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
