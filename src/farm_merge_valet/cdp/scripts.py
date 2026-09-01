"""JavaScript expressions used by the CDP game runtime adapter."""

from __future__ import annotations

import json

from farm_merge_valet.core.items import GridCoord, InteractionTargetKind

_DISCOVER_EXPRESSION = r"""
(() => {
  const board = window.__fmvBoardCells;
  if (!(board instanceof Map)) return {status: 'unavailable', detail: 'board-map-not-found'};
  const subscribers = (signal) => Array.isArray(signal?._subscribers)
    ? signal._subscribers : [];
  const validItemHandler = (value) => value &&
    typeof value._onGesturePick === 'function' &&
    typeof value._onGestureDrop === 'function' &&
    value._services?.mapGrid && value._services?.interactionService;
  const validInteractionHandler = (value) => validItemHandler(value) &&
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
  const interactionHandler = pickContexts.find(validInteractionHandler) || null;

  const screen = services?.hudService?._screen ||
    services?.mapGridView?._view || itemHandler;
  let systemsOwner = screen;
  for (let depth = 0; systemsOwner && !Array.isArray(systemsOwner._systems) && depth < 8;
       depth += 1) {
    systemsOwner = systemsOwner.parent || systemsOwner._parent || null;
  }
  const gameplaySystems = Array.isArray(systemsOwner?._systems) ? systemsOwner._systems : [];
  const shovelHandler = gameplaySystems.find((candidate) =>
        candidate?._services === services &&
        candidate._services?.shovelService === services?.shovelService &&
        typeof candidate._onContentRemove === 'function' &&
        typeof candidate._resetShovelSystem === 'function') || null;
  const rewardInteractionHandler = gameplaySystems.find((candidate) =>
    candidate?._services === services &&
    typeof candidate._collectObject === 'function' &&
    typeof candidate._collectReward === 'function' &&
    typeof candidate.onItemCollect?.fire === 'function') || null;
  const obstacleClearHandler = gameplaySystems.find((candidate) =>
    candidate?._services === services &&
    typeof candidate._attemptPayment === 'function' &&
    typeof candidate._getTotalCost === 'function' &&
    candidate._popoutStore) || null;
  const rewardContainerHandler = gameplaySystems.find((candidate) =>
    candidate?._services === services &&
    candidate._isActive !== false &&
    typeof candidate._getCrateUnlockCostObjects === 'function' &&
    typeof candidate._openCrate === 'function' &&
    typeof candidate._canSpawnRewards === 'function' &&
    candidate._crateFamily && candidate._keysFamily) || null;
  const storageBubbleInteractionHandler = gameplaySystems.find((candidate) =>
    candidate?._services === services &&
    typeof candidate._onBubblePointerDown === 'function' &&
    typeof candidate._onBubblePointerUp === 'function' &&
    candidate._family?._filter?._behaviorTypes?.includes?.('storageBubble') &&
    candidate._family?._filter?._behaviorTypes?.includes?.('storageBubbleInteractable')) || null;
  const storageBubblePopHandler = gameplaySystems.find((candidate) =>
    candidate?._services === services &&
    typeof candidate._onStorageBubbleTapped === 'function' &&
    typeof candidate._spawnObject === 'function') || null;
  const upgradeCard = services?.upgradeCard;
  const validUpgradeInteraction = upgradeCard?._services === services &&
    upgradeCard._isActive !== false &&
    typeof upgradeCard.upgradeItemGrade === 'function' &&
    typeof upgradeCard._model?.getItemTier === 'function';

  // The gameplay HUD owns the authoritative crate event. Visual button states
  // can remain subscribed to inventory updates after their private event set
  // has been disconnected, so require a live gameplay subscriber here.
  const crateSignal = services?.hudService?._commonEvents?.spawnCrates;
  const crateSubscribers = subscribers(crateSignal);
  const validCrateSignal = typeof crateSignal?.fire === 'function' &&
    crateSubscribers.length > 0 ? crateSignal : null;
  const orders = services?.ordersService;
  const inventory = orders?._inventory?.getInventoryItem?.('crates');
  const energy = orders?._inventory?.getInventoryItem?.('energy');
  const validInventory = inventory?._key === 'crates' &&
    Number.isInteger(inventory.amount) && inventory.amount >= 0 ? inventory : null;
  const validShopOrders = orders?._isActive !== false &&
    typeof orders?.getCurrentOrders === 'function' &&
    typeof orders?.getOrderByBuilding === 'function' &&
    typeof orders?.startOrder === 'function' &&
    typeof orders?.onOrderRewarded?.fire === 'function';

  window.__fmvGameplayServices = services;
  window.__fmvGameplayMapScreen = screen;
  window.__fmvItemInteractionHandler = itemHandler;
  window.__fmvInteractionHandler = interactionHandler;
  window.__fmvShovelHandler = shovelHandler;
  window.__fmvRewardInteractionHandler = rewardInteractionHandler;
  window.__fmvObstacleClearHandler = obstacleClearHandler;
  window.__fmvRewardContainerHandler = rewardContainerHandler;
  window.__fmvStorageBubbleInteractionHandler = storageBubbleInteractionHandler;
  window.__fmvStorageBubblePopHandler = storageBubblePopHandler;
  window.__fmvCrateSpawnSignal = validCrateSignal;
  window.__fmvCrateInventoryItem = validInventory;
  window.__fmvEnergyInventoryItem = energy?._key === 'energy' &&
    Number.isInteger(energy.amount) && energy.amount >= 0 ? energy : null;
  window.__fmvOrdersService = validShopOrders ? orders : null;
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
  if (!interactionHandler) missing.push('tile-interaction-handler');
  if (!shovelHandler) missing.push('shovel-handler');
  if (!rewardInteractionHandler) missing.push('reward-interaction-handler');
  if (!obstacleClearHandler) missing.push('obstacle-clear-handler');
  if (!rewardContainerHandler) missing.push('reward-container-handler');
  if (!storageBubbleInteractionHandler || !storageBubblePopHandler)
    missing.push('storage-bubble-handler');
  if (!validUpgradeInteraction) missing.push('upgrade-card-handler');
  if (!validCrateSignal) missing.push('crate-spawn-signal');
  if (!validInventory) missing.push('crate-inventory');
  window.__fmvRuntimeDiscovery = {
    strategy: 'bounded-signal-anchors',
    services: Boolean(services),
    pickSubscribers: subscribers(interaction?.onGesturePick).length,
    dropSubscribers: subscribers(interaction?.onGestureDrop).length,
    itemDrop: Boolean(itemHandler),
    interaction: Boolean(interactionHandler),
    removal: Boolean(shovelHandler),
    rewardInteraction: Boolean(rewardInteractionHandler),
    obstacleClear: Boolean(obstacleClearHandler),
    rewardContainer: Boolean(rewardContainerHandler),
    storageBubble: Boolean(storageBubbleInteractionHandler && storageBubblePopHandler),
    upgradeInteraction: Boolean(validUpgradeInteraction),
    crateSpawn: Boolean(validCrateSignal),
    crateSubscribers: crateSubscribers.length,
    inventory: Boolean(validInventory),
    shopOrders: Boolean(validShopOrders),
    missing,
  };
  return {
    status: itemHandler ? 'found' : 'unavailable',
    sceneId: window.__fmvRuntimeSceneIdentity,
    board: true,
    itemDrop: Boolean(itemHandler),
    interaction: Boolean(interactionHandler),
    removal: Boolean(shovelHandler),
    rewardInteraction: Boolean(rewardInteractionHandler),
    obstacleClear: Boolean(obstacleClearHandler),
    rewardContainer: Boolean(rewardContainerHandler),
    storageBubble: Boolean(storageBubbleInteractionHandler && storageBubblePopHandler),
    upgradeInteraction: Boolean(validUpgradeInteraction),
    crateSpawn: Boolean(validCrateSignal),
    inventory: Boolean(validInventory),
    shopOrders: Boolean(validShopOrders),
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
  interaction: false, removal: false, rewardInteraction: false,
  obstacleClear: false,
  rewardContainer: false,
  storageBubble: false,
  upgradeInteraction: false,
  shopOrders: false,
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
  const interactionHandler = window.__fmvInteractionHandler;
  const shovelHandler = window.__fmvShovelHandler;
  const rewardInteractionHandler = window.__fmvRewardInteractionHandler;
  const obstacleClearHandler = window.__fmvObstacleClearHandler;
  const rewardContainerHandler = window.__fmvRewardContainerHandler;
  const storageBubbleInteractionHandler = window.__fmvStorageBubbleInteractionHandler;
  const storageBubblePopHandler = window.__fmvStorageBubblePopHandler;
  const crateSignal = window.__fmvCrateSpawnSignal;
  const orders = window.__fmvOrdersService;
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
  const currentInteractionHandler = currentBoard && interactionHandler?._services === services &&
    typeof interactionHandler._simulateClick === 'function' &&
    subscribers(interaction?.onGestureTap).some((entry) => entry?.context === interactionHandler);
  const currentShovelHandler = currentBoard && shovelHandler?._services === services &&
    shovelHandler._services?.shovelService === services?.shovelService &&
    typeof shovelHandler._onContentRemove === 'function' &&
    typeof shovelHandler._resetShovelSystem === 'function';
  const currentRewardInteractionHandler = currentBoard &&
    rewardInteractionHandler?._services === services &&
    typeof rewardInteractionHandler._collectObject === 'function' &&
    typeof rewardInteractionHandler._collectReward === 'function' &&
    typeof rewardInteractionHandler.onItemCollect?.fire === 'function';
  const currentObstacleClearHandler = currentBoard &&
    obstacleClearHandler?._services === services &&
    typeof obstacleClearHandler._attemptPayment === 'function' &&
    typeof obstacleClearHandler._getTotalCost === 'function' &&
    obstacleClearHandler._popoutStore;
  const currentRewardContainerHandler = currentBoard &&
    rewardContainerHandler?._services === services &&
    rewardContainerHandler._isActive !== false &&
    typeof rewardContainerHandler._getCrateUnlockCostObjects === 'function' &&
    typeof rewardContainerHandler._openCrate === 'function' &&
    typeof rewardContainerHandler._canSpawnRewards === 'function' &&
    rewardContainerHandler._crateFamily && rewardContainerHandler._keysFamily;
  const currentStorageBubbleHandler = currentBoard &&
    storageBubbleInteractionHandler?._services === services &&
    storageBubbleInteractionHandler._isActive !== false &&
    typeof storageBubbleInteractionHandler._onBubblePointerDown === 'function' &&
    typeof storageBubbleInteractionHandler._onBubblePointerUp === 'function' &&
    storageBubbleInteractionHandler._family?._filter?._behaviorTypes?.includes?.(
      'storageBubbleInteractable') &&
    storageBubblePopHandler?._services === services &&
    storageBubblePopHandler._isActive !== false &&
    typeof storageBubblePopHandler._onStorageBubbleTapped === 'function';
  const upgradeCard = services?.upgradeCard;
  const currentUpgradeInteraction = currentBoard && upgradeCard?._services === services &&
    upgradeCard._isActive !== false &&
    typeof upgradeCard.upgradeItemGrade === 'function' &&
    typeof upgradeCard._model?.getItemTier === 'function';
  const currentCrateSignal = currentBoard &&
    services?.hudService?._commonEvents?.spawnCrates === crateSignal &&
    typeof crateSignal?.fire === 'function' && subscribers(crateSignal).length > 0;
  const inventory = services?.ordersService?._inventory?.getInventoryItem?.('crates');
  const currentInventory = currentBoard && inventory?._key === 'crates' &&
    Number.isInteger(inventory.amount) && inventory.amount >= 0;
  if (currentInventory) window.__fmvCrateInventoryItem = inventory;
  const currentShopOrders = currentBoard && services?.ordersService === orders &&
    orders?._isActive !== false && typeof orders?.getCurrentOrders === 'function' &&
    typeof orders?.startOrder === 'function' &&
    typeof orders?.onOrderRewarded?.fire === 'function';
  let stage = scene;
  while (stage?.parent) stage = stage.parent;
  const layerRoot = stage?.children?.[0];
  const popupLayer = layerRoot?.children?.find((child) => child?.name === 'popup');
  const levelUpPopup = popupLayer?.children?.find((child) =>
    child?._name === 'LevelUpPopup' && typeof child.close === 'function');
  const stickerNavigation = stage?.children?.find((child) =>
    Array.isArray(child?._viewStack) && child?._packOpeningView);
  const packOpeningView = stickerNavigation?._packOpeningView;
  const stickerController = packOpeningView?._currentPackAnimation;
  const stickerPackActive = stickerNavigation?.visible !== false &&
    packOpeningView?.visible !== false && packOpeningView?.parent === stickerNavigation &&
    stickerController?.parent === packOpeningView && stickerController?._isAnimating === true;
  const stickerSpineView = stickerPackActive ? stickerController._spineAnimation : null;
  const stickerRevealView = stickerPackActive ? stickerController._revealAnimation : null;
  const liveSkipText = stickerSpineView?._skipText &&
    !stickerSpineView._skipText._destroyed;
  const stickerSkip = liveSkipText && (
    typeof stickerSpineView._onSkippedPressed === 'function' ||
    typeof stickerSpineView._onSkipPressed === 'function');
  const collectButton = stickerRevealView?.children?.find((child) =>
    (child?.name === 'ConsentButton' || child?._name === 'ConsentButton') &&
    child?.visible !== false && child?.renderable !== false &&
    typeof child.destroy === 'function');
  const collectPending = stickerRevealView && !liveSkipText &&
    typeof stickerRevealView._animationResolve === 'function' &&
    Boolean(collectButton);
  const transientOverlay = levelUpPopup ? 'level-up'
    : stickerSkip ? 'sticker-pack-skip'
    : collectPending ? 'sticker-pack-collect'
    : stickerPackActive ? 'sticker-pack-transition'
    : null;
  const identity = currentBoard && (services.mapGrid || scene || handler || board);
  const sceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;
  return {
    sceneId,
    board: board instanceof Map && currentBoard,
    itemDrop: Boolean(currentItemHandler),
    interaction: Boolean(currentInteractionHandler),
    removal: Boolean(currentShovelHandler),
    rewardInteraction: Boolean(currentRewardInteractionHandler),
    obstacleClear: Boolean(currentObstacleClearHandler),
    rewardContainer: Boolean(currentRewardContainerHandler),
    storageBubble: Boolean(currentStorageBubbleHandler),
    upgradeInteraction: Boolean(currentUpgradeInteraction),
    itemActionBusy: Boolean(currentItemHandler && (
      handler.busy || handler.isBusy?.() || handler.dragging || handler._dragging ||
      handler._currentObject || handler._originCell
    )),
    crateSpawn: Boolean(currentCrateSignal),
    inventory: Boolean(currentInventory),
    shopOrders: Boolean(currentShopOrders),
    heartbeat: beat ? beat.frame : null,
    heartbeatAgeMs: beat ? Math.max(0, performance.now() - beat.timestamp) : null,
    heartbeatInstalled: Boolean(window.__fmvHeartbeatInstalled && beat),
    transientOverlay,
  };
})()
"""

_READ_STORAGE_BUBBLES_EXPRESSION = r"""
(() => {
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  const handler = window.__fmvStorageBubbleInteractionHandler;
  if (!(board instanceof Map) || window.__fmvRuntimeBoard !== board ||
      handler?._services !== services || handler._isActive === false ||
      !Array.isArray(handler._family?._gameObjects)) return null;
  const out = [];
  for (const bubble of handler._family._gameObjects) {
    const content = bubble?.getBehavior?.('storageBubble')?.content;
    if (!Number.isInteger(bubble?.id) || !Array.isArray(content)) continue;
    out.push({
      objectID: bubble.id,
      contentIDs: content.map((item) => item?.blueprint)
        .filter((blueprintID) => typeof blueprintID === 'string'),
    });
  }
  return out;
})()
"""


def _storage_bubble_pop_expression(object_id: int, scene_id: int | None) -> str:
    return f"""
(() => {{
  const expectedObjectID = {json.dumps(object_id)};
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  const interactionHandler = window.__fmvStorageBubbleInteractionHandler;
  const popHandler = window.__fmvStorageBubblePopHandler;
  const identity = services?.mapGrid || window.__fmvGameplayMapScreen ||
    window.__fmvItemInteractionHandler || board;
  const currentSceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;
  if (!(board instanceof Map) || window.__fmvRuntimeBoard !== board ||
      currentSceneId !== {json.dumps(scene_id)} ||
      interactionHandler?._services !== services || popHandler?._services !== services)
    return {{status: 'unavailable', detail: 'runtime-scene-changed'}};
  const bubble = [...(interactionHandler._family?._gameObjects || [])]
    .find((candidate) => candidate?.id === expectedObjectID);
  const storage = bubble?.getBehavior?.('storageBubble');
  if (!bubble || !bubble.hasBehavior?.('storageBubbleInteractable') ||
      !Array.isArray(storage?.content) || storage.content.length === 0)
    return {{status: 'stale-source'}};
  if (services.gridFilter?.getEmptyCells?.().length === 0)
    return {{status: 'rejected', detail: 'insufficient-board-space'}};
  if (popHandler._isActive === false ||
      typeof popHandler._onStorageBubbleTapped !== 'function')
    return {{status: 'unavailable', detail: 'storage-bubble-handler-not-current'}};
  try {{
    popHandler._onStorageBubbleTapped(bubble);
    return {{status: 'submitted'}};
  }} catch (error) {{
    return {{status: 'rejected', detail: String(error?.message || error)}};
  }}
}})()
"""


def _dismiss_overlay_expression(scene_id: int | None) -> str:
    return f"""
(() => {{
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  const scene = window.__fmvGameplayMapScreen;
  const identity = services?.mapGrid || scene || window.__fmvItemInteractionHandler || board;
  const currentSceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;
  if (!(board instanceof Map) || window.__fmvRuntimeBoard !== board ||
      currentSceneId !== {json.dumps(scene_id)})
    return {{status: 'unavailable', detail: 'runtime-scene-changed'}};

  let stage = scene;
  while (stage?.parent) stage = stage.parent;
  const layerRoot = stage?.children?.[0];
  const popupLayer = layerRoot?.children?.find((child) => child?.name === 'popup');
  const levelUpPopup = popupLayer?.children?.find((child) =>
    child?._name === 'LevelUpPopup' && typeof child.close === 'function');
  if (levelUpPopup) {{
    if (levelUpPopup._popupLocked === true ||
        levelUpPopup._popupInteractionsLocked === true)
      return {{status: 'busy', detail: 'level-up'}};
    const inputBlocker = levelUpPopup.children?.find((child) => child?._events?.pointerup);
    const pointerUp = inputBlocker?._events?.pointerup;
    const listeners = Array.isArray(pointerUp) ? pointerUp : pointerUp ? [pointerUp] : [];
    if (!listeners.some((listener) =>
        listener?.fn === levelUpPopup.close && listener?.context === levelUpPopup))
      return {{status: 'unavailable', detail: 'level-up-handler-not-current'}};
    try {{
      void levelUpPopup.close();
      return {{status: 'submitted', detail: 'level-up'}};
    }} catch (error) {{
      return {{status: 'rejected', detail: String(error?.message || error)}};
    }}
  }}

  const stickerNavigation = stage?.children?.find((child) =>
    Array.isArray(child?._viewStack) && child?._packOpeningView);
  const packOpeningView = stickerNavigation?._packOpeningView;
  const stickerController = packOpeningView?._currentPackAnimation;
  const stickerPackActive = stickerNavigation?.visible !== false &&
    packOpeningView?.visible !== false && packOpeningView?.parent === stickerNavigation &&
    stickerController?.parent === packOpeningView && stickerController?._isAnimating === true;
  if (!stickerPackActive)
    return {{status: 'stale-source', detail: 'no-supported-overlay'}};

  const stickerSpineView = stickerController._spineAnimation;
  const stickerRevealView = stickerController._revealAnimation;
  const liveSkipText = stickerSpineView?._skipText &&
    !stickerSpineView._skipText._destroyed;
  const skip = typeof stickerSpineView?._onSkippedPressed === 'function'
    ? stickerSpineView._onSkippedPressed
    : typeof stickerSpineView?._onSkipPressed === 'function'
      ? stickerSpineView._onSkipPressed : null;
  if (liveSkipText && skip) {{
    try {{
      skip.call(stickerSpineView);
      return {{status: 'submitted', detail: 'sticker-pack-skip'}};
    }} catch (error) {{
      return {{status: 'rejected', detail: String(error?.message || error)}};
    }}
  }}

  const collectButton = stickerRevealView?.children?.find((child) =>
    (child?.name === 'ConsentButton' || child?._name === 'ConsentButton') &&
    child?.visible !== false && child?.renderable !== false &&
    typeof child.destroy === 'function');
  if (!collectButton || typeof stickerRevealView._animationResolve !== 'function')
    return {{status: 'busy', detail: 'sticker-pack-transition'}};
  try {{
    collectButton.destroy();
    stickerRevealView._animationResolve();
    return {{status: 'submitted', detail: 'sticker-pack-collect'}};
  }} catch (error) {{
    return {{status: 'rejected', detail: String(error?.message || error)}};
  }}
}})()
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
  const services = window.__fmvGameplayServices;
  const identity = services?.mapGrid ||
    window.__fmvGameplayMapScreen || window.__fmvItemInteractionHandler || board;
  const currentSceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;
  if (!board || window.__fmvRuntimeBoard !== board || !signal ||
      currentSceneId !== {json.dumps(scene_id)})
    return {{status: 'unavailable', spawned: 0, detail: 'runtime-scene-changed'}};
  const inventory = services?.ordersService?._inventory?.getInventoryItem?.('crates');
  if (inventory?._key !== 'crates' ||
      !Number.isInteger(inventory.amount) || inventory.amount < 0)
    return {{status: 'unavailable', spawned: 0, detail: 'crate-inventory-not-current'}};
  window.__fmvCrateInventoryItem = inventory;
  const availableBefore = inventory.amount;
  if (services?.hudService?._commonEvents?.spawnCrates !== signal)
    return {{status: 'unavailable', spawned: 0, remaining: inventory.amount,
      availableBefore, detail: 'crate-signal-not-current'}};
  const subscribers = Array.isArray(signal._subscribers) ? signal._subscribers : [];
  if (!subscribers.length)
    return {{status: 'unavailable', spawned: 0, remaining: inventory.amount,
      availableBefore, detail: 'crate-signal-has-no-subscribers'}};
  if (subscribers.some((entry) => entry?.context?._executing))
    return {{status: 'busy', spawned: 0, remaining: inventory.amount, availableBefore}};
  if (typeof signal.fire !== 'function')
    return {{status: 'unavailable', spawned: 0, availableBefore,
      detail: 'crate-signal-not-found'}};
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
          availableBefore, detail: 'no-supply-crates'}};
      if (empty === 0)
        return {{status: 'rejected', spawned, remaining: amount,
          availableBefore, detail: 'no-open-cells'}};
      signal.fire({{}});
      if (!await waitForChange(amount, empty))
        return {{status: 'rejected', spawned, remaining: inventory.amount,
          availableBefore, detail: 'no-authoritative-crate-change'}};
      spawned += 1;
    }}
    return {{status: spawned ? 'submitted' : 'rejected', spawned,
      remaining: inventory.amount, availableBefore}};
  }} catch (error) {{
    return {{status: 'rejected', spawned, remaining: inventory.amount,
      availableBefore, detail: String(error?.message || error)}};
  }}
}})()
"""


def _interaction_expression(
    coord: GridCoord,
    expected_kind: InteractionTargetKind,
    expected_blueprint_id: str,
    expected_object_id: int | None,
    scene_id: int | None,
) -> str:
    return f"""
(async () => {{
  const coord = {_coord(coord)};
  const expectedKind = {json.dumps(expected_kind.value)};
  const expectedBlueprintID = {json.dumps(expected_blueprint_id)};
  const expectedObjectID = {json.dumps(expected_object_id)};
  const board = window.__fmvBoardCells;
  const handler = window.__fmvInteractionHandler;
  const rewardHandler = window.__fmvRewardInteractionHandler;
  const obstacleHandler = window.__fmvObstacleClearHandler;
  const rewardContainerHandler = window.__fmvRewardContainerHandler;
  const services = window.__fmvGameplayServices;
  const upgradeHandler = services?.upgradeCard;
  const identity = window.__fmvGameplayServices?.mapGrid ||
    window.__fmvGameplayMapScreen || handler || board;
  const currentSceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;
  if (!board || window.__fmvRuntimeBoard !== board ||
      (!['reward', 'reward-container', 'clear', 'upgrade'].includes(expectedKind) && !handler) ||
      (expectedKind === 'clear' && !obstacleHandler) ||
      (expectedKind === 'reward-container' && !rewardContainerHandler) ||
      (expectedKind === 'upgrade' && !upgradeHandler) ||
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
    : expectedKind === 'reward'
      ? content.hasBehavior?.('collectable') && content.hasBehavior?.('currency') &&
        Array.isArray(content.getBehavior?.('collectable')?.reward) &&
        content.getBehavior('collectable').reward.length > 0
    : expectedKind === 'upgrade'
      ? content.hasBehavior?.('upgradeCard') && Number.isInteger(content.getTier?.()) &&
        typeof content.getBehavior('upgradeCard')?._data?.targetObjectTreeIngredient === 'string'
    : expectedKind === 'reward-container'
      ? content.hasBehavior?.('crateReward') && content.hasBehavior?.('cooldown') &&
        Array.isArray(content.getBehavior('crateReward')?._data?.rewards) &&
        content.getBehavior('crateReward')._data.rewards.length > 0
    : expectedKind === 'clear'
      ? content.hasBehavior?.('mapSource') && content.hasBehavior?.('hitpoints') &&
        content.hasBehavior?.('resourceGate')
    : expectedKind === 'obstacle-loot'
      ? content.hasBehavior?.('mapSource') && content.hasBehavior?.('hitpoints') &&
        content.hasBehavior?.('resourceGatePaid') && content.hasBehavior?.('lootable') &&
        Array.isArray(content.getBehavior?.('lootable')?.loot) &&
        content.getBehavior('lootable').loot.length > 0
    : expectedKind === 'producer'
      ? producer && !content.hasBehavior?.('cooldown') &&
        !content.hasBehavior?.('depleted')
      : expectedKind === 'depleted-producer'
        ? producer && content.hasBehavior?.('depleted')
        : false;
  if (!valid) return {{status: 'invalid-target'}};
  if (handler?.busy || handler?.isBusy?.() || handler?.dragging || handler?._dragging ||
      handler?._currentObject || handler?._originCell)
    return {{status: 'busy'}};
  try {{
    if (expectedKind === 'reward') {{
      if (rewardHandler?._services !== services ||
          typeof rewardHandler._collectReward !== 'function' ||
          typeof rewardHandler.onItemCollect?.fire !== 'function')
        return {{status: 'unavailable', detail: 'reward-interaction-handler-not-found'}};
      rewardHandler._collectReward(content);
    }} else if (expectedKind === 'reward-container') {{
      if (rewardContainerHandler?._services !== services ||
          rewardContainerHandler._isActive === false ||
          typeof rewardContainerHandler._getCrateUnlockCostObjects !== 'function' ||
          typeof rewardContainerHandler._openCrate !== 'function')
        return {{status: 'unavailable', detail: 'reward-container-handler-not-found'}};
      if (rewardContainerHandler._currentlySpawningRewards)
        return {{status: 'busy', detail: 'reward-container-handler-busy'}};
      const reward = content.getBehavior('crateReward')._data;
      const requirements = reward.crateRewardUnlockRequirement;
      if (requirements != null && (!Array.isArray(requirements) ||
          !requirements.every((item) => typeof item?.blueprintID === 'string' &&
            Number.isInteger(item.amount) && item.amount > 0)))
        return {{status: 'invalid-target', detail: 'reward-container-requirements-invalid'}};
      let empty = 0;
      for (const candidate of board.values()) if (!candidate._content) empty += 1;
      if (empty < reward.rewards.length)
        return {{status: 'rejected', detail: 'insufficient-reward-space'}};
      if (requirements?.length) {{
        if (typeof services?.gridFilter?.hasEnoughItems !== 'function')
          return {{status: 'unavailable', detail: 'reward-requirement-state-unavailable'}};
        if (!services.gridFilter.hasEnoughItems(requirements))
          return {{status: 'rejected', detail: 'missing-reward-requirements'}};
      }}
      const costObjects = rewardContainerHandler._getCrateUnlockCostObjects(content);
      const requiredObjectCount = (requirements || [])
        .reduce((total, item) => total + item.amount, 0);
      if (!Array.isArray(costObjects) || costObjects.length !== requiredObjectCount)
        return {{status: 'rejected', detail: 'reward-requirements-changed'}};
      for (const object of costObjects) object.removeBehaviorByType('mergeable');
      rewardContainerHandler._openCrate(content, costObjects);
    }} else if (expectedKind === 'upgrade') {{
      if (upgradeHandler?._services !== services || upgradeHandler._isActive === false ||
          typeof upgradeHandler.upgradeItemGrade !== 'function' ||
          typeof upgradeHandler._model?.getItemTier !== 'function')
        return {{status: 'unavailable', detail: 'upgrade-card-handler-not-found'}};
      const target = content.getBehavior('upgradeCard')._data.targetObjectTreeIngredient;
      const cardTier = content.getTier();
      const appliedTier = upgradeHandler._model.getItemTier(target);
      if (!Number.isInteger(appliedTier) || appliedTier < 0)
        return {{status: 'unavailable', detail: 'upgrade-progress-unavailable'}};
      if (appliedTier >= cardTier)
        return {{status: 'rejected', detail: 'upgrade-tier-already-applied'}};
      const previousCellWithCard = upgradeHandler._cellWithCard;
      try {{
        upgradeHandler._cellWithCard = cell;
        upgradeHandler.upgradeItemGrade(target, cardTier);
      }} finally {{
        if (upgradeHandler._cellWithCard === cell)
          upgradeHandler._cellWithCard = previousCellWithCard;
      }}
    }} else if (expectedKind === 'clear') {{
      if (obstacleHandler?._services !== services)
        return {{status: 'unavailable', detail: 'obstacle-clear-handler-not-found'}};
      const gate = content.getBehavior('resourceGate');
      const position = content.getBehavior('gridPosition');
      const effectiveCost = obstacleHandler._getTotalCost(gate);
      const energyCost = effectiveCost?.find((item) => item?.key === 'energy')?.amount;
      const requiredWorkers = gate?._data?.workers;
      const energy = window.__fmvEnergyInventoryItem;
      const gameWorkers = services?.gameWorkers;
      if (!position || !Number.isInteger(energyCost) || energyCost < 0 ||
          !Number.isInteger(requiredWorkers) || requiredWorkers < 0)
        return {{status: 'invalid-target', detail: 'obstacle-state-invalid'}};
      if (!energy || !Number.isInteger(energy.amount))
        return {{status: 'unavailable', detail: 'energy-state-unavailable'}};
      if (energy.amount < energyCost)
        return {{status: 'rejected', detail: 'insufficient-energy'}};
      if (typeof gameWorkers?.hasEnoughWorkers !== 'function')
        return {{status: 'unavailable', detail: 'worker-state-unavailable'}};
      if (!gameWorkers.hasEnoughWorkers(requiredWorkers))
        return {{status: 'rejected', detail: 'insufficient-workers'}};
      await obstacleHandler._attemptPayment(content, position, gate);
    }} else {{
      handler._simulateClick(content);
    }}
    return {{status: 'submitted'}};
  }} catch (error) {{
    return {{status: 'rejected', detail: String(error?.message || error)}};
  }}
}})()
"""


_READ_WORKERS_EXPRESSION = r"""
(() => {
  const gameWorkers = window.__fmvGameplayServices?.gameWorkers;
  const roster = gameWorkers?._model?.workers;
  if (!Array.isArray(roster) || typeof gameWorkers.hasEnoughWorkers !== 'function') return null;
  try {
    let available = 0;
    while (available < roster.length && gameWorkers.hasEnoughWorkers(available + 1)) {
      available += 1;
    }
    return {total: roster.length, available};
  } catch (_error) {
    return null;
  }
})()
"""


def _removal_expression(
    coord: GridCoord,
    expected_blueprint_id: str,
    expected_object_id: int | None,
    scene_id: int | None,
) -> str:
    return f"""
(() => {{
  const coord = {_coord(coord)};
  const expectedBlueprintID = {json.dumps(expected_blueprint_id)};
  const expectedObjectID = {json.dumps(expected_object_id)};
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  const handler = window.__fmvShovelHandler;
  const itemHandler = window.__fmvItemInteractionHandler;
  const identity = services?.mapGrid || window.__fmvGameplayMapScreen || itemHandler || board;
  const currentSceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;
  if (!(board instanceof Map) || window.__fmvRuntimeBoard !== board ||
      handler?._services !== services ||
      handler._services?.shovelService !== services?.shovelService ||
      typeof handler._onContentRemove !== 'function' ||
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
  if (!content.hasBehavior?.('shovelable'))
    return {{status: 'invalid-target'}};
  if (itemHandler?.busy || itemHandler?.isBusy?.() || itemHandler?.dragging ||
      itemHandler?._dragging || itemHandler?._currentObject || itemHandler?._originCell)
    return {{status: 'busy'}};
  const previousContentToRemove = handler._contentToRemove;
  try {{
    handler._contentToRemove = content;
    handler._onContentRemove();
    return {{status: 'submitted'}};
  }} catch (error) {{
    return {{status: 'rejected', detail: String(error?.message || error)}};
  }} finally {{
    if (handler._contentToRemove === content)
      handler._contentToRemove = previousContentToRemove;
  }}
}})()
"""


_READ_SHOP_ORDERS_EXPRESSION = r"""
(() => {
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  const orders = window.__fmvOrdersService;
  if (!(board instanceof Map) || window.__fmvRuntimeBoard !== board ||
      services?.ordersService !== orders || orders?._isActive === false)
    return null;
  const recipes = orders._recipes;
  const inventory = orders._inventory;
  if (typeof recipes?.getRecipe !== 'function' ||
      typeof inventory?.getAmount !== 'function') return null;
  const states = {1: 'available', 2: 'producing', 3: 'ready'};
  const out = [];
  for (const order of orders.getCurrentOrders()) {
    const state = states[order?.state];
    const recipe = recipes.getRecipe(order?.recipe);
    if (!state || !recipe || recipe.ID !== order.recipe ||
        !orders._buildings?.isWorkshop?.(order.buildingID)) continue;
    const timer = order.timerId == null
      ? null : orders._timerService?.getTimerByID?.(order.timerId);
    const remainingMs = Number(timer?.remainingTime);
    out.push({
      shopID: order.buildingID,
      recipeID: order.recipe,
      state,
      durationSeconds: Number.isFinite(recipe.duration) ? recipe.duration : 0,
      remainingSeconds: Number.isFinite(remainingMs) ? Math.max(0, remainingMs / 1000) : null,
      ingredients: Array.isArray(recipe.ingredients) ? recipe.ingredients.map((item) => ({
        itemID: item.key,
        required: item.amount,
        available: inventory.getAmount(item.key),
      })) : [],
      rewardIDs: Array.isArray(recipe.reward) ? [...recipe.reward] : [],
    });
  }
  return out;
})()
"""


def _shop_start_expression(shop_id: str, recipe_id: str, scene_id: int | None) -> str:
    return f"""
(() => {{
  const shopID = {json.dumps(shop_id)};
  const recipeID = {json.dumps(recipe_id)};
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  const orders = window.__fmvOrdersService;
  const identity = services?.mapGrid || window.__fmvGameplayMapScreen ||
    window.__fmvItemInteractionHandler || board;
  const currentSceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;
  if (!(board instanceof Map) || window.__fmvRuntimeBoard !== board ||
      services?.ordersService !== orders || currentSceneId !== {json.dumps(scene_id)})
    return {{status: 'unavailable', detail: 'runtime-scene-changed'}};
  const order = orders.getOrderByBuilding?.(shopID);
  if (!order || order.recipe !== recipeID || order.state !== 1)
    return {{status: 'stale-source', detail: 'shop-order-changed'}};
  if (!orders._buildings?.isWorkshop?.(shopID) ||
      !orders._buildings?.isBuildingActive?.(shopID))
    return {{status: 'invalid-target', detail: 'shop-not-active'}};
  if (!orders._canAffordOrder?.(order))
    return {{status: 'rejected', detail: 'insufficient-ingredients'}};
  try {{
    orders.startOrder(shopID);
    const updated = orders.getOrderByBuilding?.(shopID);
    return updated?.recipe === recipeID && updated?.state === 2
      ? {{status: 'submitted'}}
      : {{status: 'rejected', detail: 'order-did-not-start'}};
  }} catch (error) {{
    return {{status: 'rejected', detail: String(error?.message || error)}};
  }}
}})()
"""


def _shop_claim_expression(shop_id: str, recipe_id: str, scene_id: int | None) -> str:
    return f"""
(() => {{
  const shopID = {json.dumps(shop_id)};
  const recipeID = {json.dumps(recipe_id)};
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  const orders = window.__fmvOrdersService;
  const identity = services?.mapGrid || window.__fmvGameplayMapScreen ||
    window.__fmvItemInteractionHandler || board;
  const currentSceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;
  if (!(board instanceof Map) || window.__fmvRuntimeBoard !== board ||
      services?.ordersService !== orders || currentSceneId !== {json.dumps(scene_id)})
    return {{status: 'unavailable', detail: 'runtime-scene-changed'}};
  const order = orders.getOrderByBuilding?.(shopID);
  if (!order || order.recipe !== recipeID || order.state !== 3)
    return {{status: 'stale-source', detail: 'shop-order-changed'}};
  if (orders._isClaiming) return {{status: 'busy'}};
  const recipe = orders._recipes?.getRecipe?.(recipeID);
  const rewards = recipe?.reward;
  if (!Array.isArray(rewards))
    return {{status: 'invalid-target', detail: 'recipe-rewards-unavailable'}};
  const emptyCount = [...board.values()].filter((cell) => !cell._content).length;
  if (emptyCount < rewards.length)
    return {{status: 'rejected', detail: 'insufficient-board-space'}};
  const rewardSubscribers = Array.isArray(orders.onOrderRewarded?._subscribers)
    ? orders.onOrderRewarded._subscribers : [];
  if (!rewardSubscribers.some((entry) =>
      typeof entry?.context?._spawnOrderReward === 'function'))
    return {{status: 'unavailable', detail: 'shop-reward-handler-not-found'}};
  const rootServices = orders._recipes?._services;
  if (typeof rootServices?.discovery?.onOrderRewarded?.fire !== 'function' ||
      typeof rootServices?.actions?.emit !== 'function')
    return {{status: 'unavailable', detail: 'shop-progression-handler-not-found'}};
  try {{
    orders.onOrderRewarded.fire(order);
    rootServices.discovery.onOrderRewarded.fire(order);
    rootServices.actions.emit(`order.rewarded.${{shopID}}.${{recipeID}}`);
    return {{status: 'submitted'}};
  }} catch (error) {{
    const current = orders.getOrderByBuilding?.(shopID);
    return !current || current.recipe !== recipeID
      ? {{status: 'submitted', detail: String(error?.message || error)}}
      : {{status: 'rejected', detail: String(error?.message || error)}};
  }}
}})()
"""
