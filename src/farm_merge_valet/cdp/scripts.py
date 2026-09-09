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
  const visitorScene = systemsOwner?._options?.startOnTrainStation === true &&
    Boolean(systemsOwner?._commonFriendEvents);
  const trainHandler = gameplaySystems.find((candidate) =>
    candidate?._services === services &&
    typeof candidate._openTrainstationPopup === 'function') || null;
  const visitorActionHandler = gameplaySystems.find((candidate) =>
    candidate?._services === services && candidate._visitorActionsFamily &&
    typeof candidate._onActivityTapped === 'function') || null;
  const returnHud = visitorActionHandler?._notificationEvent?._subscribers
    ?.map((subscriber) => subscriber?.context)
    .find((candidate) => typeof candidate?._returnButtonClicked === 'function') || null;
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
    candidate._isActive !== false &&
    typeof candidate._attemptPayment === 'function' &&
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
  const marketplace = services?.marketplaceService;
  const validMarketplace = marketplace?._isActive !== false &&
    typeof marketplace?.getMarketplacePopupData === 'function' &&
    typeof marketplace?.getItemConfigsByShop === 'function' &&
    typeof marketplace?.getStockItem === 'function';
  const validLandExpansion = [services?.mapAreaService, services?.premiumAreaService]
    .every((service) => service?._services === services && service._isActive !== false &&
      typeof service.getNextAreaToUnlock === 'function' &&
      typeof service.canUnlockArea === 'function' && typeof service.unlockArea === 'function');

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
  window.__fmvFarmSceneKind = visitorScene ? 'visitor' : 'own';
  window.__fmvTrainHandler = trainHandler;
  window.__fmvVisitorActionHandler = visitorActionHandler;
  window.__fmvVisitorReturnHud = returnHud;
  window.__fmvTrainPopup = null;
  window.__fmvFarmVisitPanelExpected = false;
  window.__fmvFarmVisitTransitionStartedAt = null;
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
    marketplace: Boolean(validMarketplace),
    landExpansion: validLandExpansion,
    farmVisit: Boolean(visitorScene ? visitorActionHandler && returnHud : trainHandler),
    farmScene: visitorScene ? 'visitor' : 'own',
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
    marketplace: Boolean(validMarketplace),
    landExpansion: validLandExpansion,
    farmVisit: Boolean(visitorScene ? visitorActionHandler && returnHud : trainHandler),
    farmScene: visitorScene ? 'visitor' : 'own',
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

_RUNTIME_BOOTSTRAP_EXPRESSION = r"""
(() => {
  const canvas = document.querySelector('canvas');
  return document.readyState === 'complete' &&
    Array.isArray(window.webpackChunkfarm_merge_game) &&
    window.webpackChunkfarm_merge_game.length > 0 &&
    canvas instanceof HTMLCanvasElement && canvas.width > 0 && canvas.height > 0;
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
  shopOrders: false, marketplace: false,
  farmVisit: false, farmScene: null,
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


def _overlay_context_expression() -> str:
    return r"""let stage = scene;
  while (stage?.parent) stage = stage.parent;
  const layerRoot = stage?.children?.[0];
  const popupLayer = layerRoot?.children?.find((child) => child?.name === 'popup');
  const activePopup = popupLayer?.children?.find((child) =>
    child?.visible !== false && child?.renderable !== false && child?._destroyed !== true);"""


_HEALTH_EXPRESSION = (
    r"""
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
  const farmScene = window.__fmvFarmSceneKind;
  const trainHandler = window.__fmvTrainHandler;
  const visitorActionHandler = window.__fmvVisitorActionHandler;
  const visitorReturnHud = window.__fmvVisitorReturnHud;
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
    obstacleClearHandler._isActive !== false &&
    typeof obstacleClearHandler._attemptPayment === 'function' &&
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
  const autoSaveService = services?.ordersService?._autoSaveService;
  const sharedServices = autoSaveService?.services || autoSaveService?._services;
  const backendConnection = sharedServices?.connection;
  const currentBackendConnection = currentBoard &&
    typeof backendConnection?.isConnected === 'boolean' &&
    typeof backendConnection?.attemptReconnection === 'function' &&
    typeof backendConnection?.onDisconnect?.fire === 'function';
  const backendHangingPings = Number.isInteger(
    backendConnection?._consecutiveHangingPings
  ) ? backendConnection._consecutiveHangingPings : null;
  const backendMaxHangingPings = Number.isInteger(
    backendConnection?._maxConsecutiveHangingPings
  ) ? backendConnection._maxConsecutiveHangingPings : null;
  const backendPingLimitReached = backendHangingPings !== null &&
    backendMaxHangingPings !== null && backendMaxHangingPings > 0 &&
    backendHangingPings >= backendMaxHangingPings;
  const marketplace = services?.marketplaceService;
  const currentMarketplace = currentBoard && marketplace?._isActive !== false &&
    typeof marketplace?.getMarketplacePopupData === 'function' &&
    typeof marketplace?.getItemConfigsByShop === 'function' &&
    typeof marketplace?.getStockItem === 'function';
  const currentLandExpansion = currentBoard &&
    [services?.mapAreaService, services?.premiumAreaService].every((service) =>
      service?._services === services && service._isActive !== false &&
      typeof service.getNextAreaToUnlock === 'function' &&
      typeof service.canUnlockArea === 'function' && typeof service.unlockArea === 'function');
  const currentFarmVisit = currentBoard && (
    farmScene === 'own'
      ? trainHandler?._services === services &&
        typeof trainHandler._openTrainstationPopup === 'function'
      : farmScene === 'visitor' && visitorActionHandler?._services === services &&
        visitorActionHandler._isActive !== false &&
        typeof visitorActionHandler._onActivityTapped === 'function' &&
        typeof visitorReturnHud?._returnButtonClicked === 'function');
"""
    + _overlay_context_expression()
    + r"""
  const trainPopup = activePopup?._name === 'TrainstationPopup' &&
    activePopup?._state === 4 &&
    typeof activePopup._onVisitButtonPressed === 'function' ? activePopup : null;
  if (trainPopup) window.__fmvTrainPopup = trainPopup;
  const levelUpPopup = activePopup?._name === 'LevelUpPopup' &&
    typeof activePopup.close === 'function' ? activePopup : null;
  const stickerNavigation = stage?.children?.find((child) =>
    Array.isArray(child?._viewStack) && child?._packOpeningView);
  const currentStickerView = stickerNavigation?._viewStack?.at(-1);
  const packOpeningView = stickerNavigation?._packOpeningView;
  const stickerController = packOpeningView?._currentPackAnimation;
  const stickerPackViewActive = Boolean(stickerNavigation && packOpeningView &&
    stickerNavigation.visible !== false &&
    packOpeningView?.visible !== false && packOpeningView?.parent === stickerNavigation &&
    currentStickerView === packOpeningView && packOpeningView?._destroyed !== true);
  const stickerPackActive = stickerPackViewActive &&
    stickerController?.parent === packOpeningView && stickerController?._isAnimating === true;
  const stickerSpineView = stickerPackActive ? stickerController._spineAnimation : null;
  const stickerRevealView = stickerPackActive ? stickerController._revealAnimation : null;
  const stickerRaffleFlow = stickerPackActive
    ? stickerController.children?.find((child) =>
        typeof child?.startRaffle === 'function' && typeof child?._start === 'function')
    : null;
  const stickerRaffleProposal = stickerRaffleFlow?.children?.find((child) =>
    child?.parent === stickerRaffleFlow && child?._destroyed !== true &&
    typeof child?.startRaffleProposal === 'function' &&
    typeof child?._close === 'function' && Array.isArray(child?._raffleButtons) &&
    Array.isArray(child?._duplicate4PlusStarsStickers));
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
  const stickerSetPanel = currentStickerView?.children?.find((child) =>
    typeof child?._onButtonPressed === 'function' &&
    typeof child?._animationResolve === 'function' &&
    child?._data?.config?.reward);
  const stickerSetButton = stickerSetPanel?.children?.find((child) =>
    child?.name === 'dailyBonusButton' && child?.visible !== false &&
    child?.renderable !== false && child?._isEnabled !== false &&
    child?._destroyed !== true);
  const stickerSetActive = stickerNavigation?.visible !== false &&
    currentStickerView && currentStickerView !== packOpeningView &&
    currentStickerView.parent === stickerNavigation && currentStickerView.visible !== false &&
    currentStickerView._destroyed !== true && Boolean(stickerSetPanel);
  const stickerSetSubmitted = stickerSetActive &&
    window.__fmvStickerSetCompletionSubmission === stickerSetPanel;
  const stickerAlbumTransition = stickerNavigation?.visible !== false &&
    currentStickerView && currentStickerView !== packOpeningView &&
    currentStickerView.parent === stickerNavigation && currentStickerView.visible !== false &&
    currentStickerView._destroyed !== true && !stickerSetActive &&
    typeof currentStickerView.setRewards === 'function' &&
    typeof currentStickerView.playAnimation === 'function';
  const dailyChallengePopup = activePopup?._name === 'DailyChallengePopup' &&
    activePopup === services?.dailyChallenge?._popup &&
    typeof activePopup.close === 'function';
  const timedEventPopup = activePopup?._name === 'TimedEventPopup' &&
    typeof activePopup.close === 'function' &&
    typeof activePopup.onClosed?.listenOnce === 'function' &&
    Boolean(activePopup._options?.content);
  const timedEventSubmitted = timedEventPopup &&
    window.__fmvTimedEventPopupSubmission === activePopup;
  const dailyBonusPopup = activePopup?._name === 'DailyBonusPopup' &&
    typeof activePopup.close === 'function' &&
    typeof activePopup._claimReward === 'function' &&
    typeof activePopup._rewardCollected === 'boolean';
  const albumStartedPopup = activePopup?._name === 'AlbumStartedPopup' &&
    typeof activePopup.close === 'function' &&
    typeof activePopup.awaitPopupClosed === 'function';
  const activePopupService = activePopup?.service || activePopup?._service;
  const rewardPopup = activePopup && activePopup !== levelUpPopup &&
    typeof activePopup.close === 'function' && (
      activePopup?.rewardService === services?.rewardService ||
      activePopup?._rewardService === services?.rewardService);
  const travelSummaryRewardPopup = activePopup?._name === 'TravelSummaryRewardPopup' &&
    typeof activePopup.close === 'function';
  const pushNotificationOptInPopup =
    activePopup?._name === 'PushNotificationOptInPopup' &&
    typeof activePopup._onDismiss === 'function' &&
    typeof activePopup.close === 'function';
  const promotionalPopup = activePopup && activePopup !== levelUpPopup &&
    typeof activePopup.close === 'function' && (
      [services?.specialOfferService, services?.recurringConversionService]
        .includes(activePopupService) ||
      'upsellPopupOptions' in activePopup || '_upsellPopupOptions' in activePopup);
  const activeBlockingLayer = (layer) => layer?.children?.some((child) =>
    child?.visible !== false && child?.renderable !== false && child?._destroyed !== true &&
    (child?.interactive === true || child?.children?.length > 0));
  const sceneTransitionLayer = layerRoot?.children?.find((layer) =>
    layer?.name === 'transition' && activeBlockingLayer(layer));
  const blockingLayer = layerRoot?.children?.find((layer) =>
    ['disconnection', 'onboarding', 'fake_ad'].includes(layer?.name) &&
    activeBlockingLayer(layer));
  const onboardingLayer = blockingLayer?.name === 'onboarding';
  const farmVisitTransitionAge = Number.isFinite(window.__fmvFarmVisitTransitionStartedAt)
    ? performance.now() - window.__fmvFarmVisitTransitionStartedAt : null;
  const sceneTransitionActive = Boolean(sceneTransitionLayer) ||
    (!currentBoard && farmVisitTransitionAge !== null && farmVisitTransitionAge < 5000);
  const unknownStageView = stage?.children?.slice(1).find((child) =>
    child !== stickerNavigation && child !== window.__fmvTrainPopup &&
    child?.visible !== false &&
    child?.renderable !== false && child?._destroyed !== true);
  const unsupportedOverlayDetail = activePopup && activePopup !== levelUpPopup &&
      activePopup !== window.__fmvTrainPopup && activePopup !== trainPopup &&
      !dailyChallengePopup && !timedEventPopup && !dailyBonusPopup &&
      !albumStartedPopup && !rewardPopup && !travelSummaryRewardPopup &&
      !pushNotificationOptInPopup && !promotionalPopup
    ? `popup:${activePopup._name || activePopup.name || activePopup.constructor?.name || 'unknown'}`
    : blockingLayer
      ? `layer:${blockingLayer.name || 'unknown'}`
      : unknownStageView
        ? `stage:${unknownStageView._name || unknownStageView.name ||
          unknownStageView.constructor?.name || 'unknown'}`
        : stickerNavigation && currentStickerView && !stickerPackActive &&
            !stickerSetActive && !stickerAlbumTransition
          ? `sticker-view:${currentStickerView.constructor?.name || 'unknown'}`
          : null;
  const transientOverlay = levelUpPopup ? 'level-up'
    : dailyChallengePopup ? 'daily-challenge'
    : timedEventSubmitted ? 'timed-event-transition'
    : timedEventPopup ? 'timed-event'
    : dailyBonusPopup && activePopup._rewardCollected ? 'daily-bonus-transition'
    : dailyBonusPopup ? 'daily-bonus-collect'
    : albumStartedPopup ? 'sticker-album-started'
    : travelSummaryRewardPopup ? 'travel-summary-reward'
    : pushNotificationOptInPopup ? 'push-notification-opt-in'
    : rewardPopup ? 'reward-popup'
    : promotionalPopup ? 'promotional-popup'
    : stickerSkip ? 'sticker-pack-skip'
    : collectPending ? 'sticker-pack-collect'
    : stickerRaffleProposal ? 'sticker-raffle-proposal'
    : stickerPackViewActive ? 'sticker-pack-transition'
    : stickerSetSubmitted ? 'sticker-set-transition'
    : stickerSetActive && stickerSetButton ? 'sticker-set-collect'
    : stickerSetActive ? 'sticker-set-transition'
    : stickerAlbumTransition ? 'sticker-album-transition'
    : onboardingLayer ? 'onboarding'
    : unsupportedOverlayDetail ? 'unsupported'
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
    marketplace: Boolean(currentMarketplace),
    landExpansion: Boolean(currentLandExpansion),
    farmVisit: Boolean(currentFarmVisit),
    farmScene,
    backendConnected: currentBackendConnection
      ? backendConnection.isConnected && !backendPingLimitReached : null,
    backendConnectivityState: currentBackendConnection &&
      Number.isInteger(backendConnection._lastConnectivityState)
      ? backendConnection._lastConnectivityState : null,
    backendConsecutiveHangingPings: currentBackendConnection
      ? backendHangingPings : null,
    sceneTransitionActive,
    heartbeat: beat ? beat.frame : null,
    heartbeatAgeMs: beat ? Math.max(0, performance.now() - beat.timestamp) : null,
    heartbeatInstalled: Boolean(window.__fmvHeartbeatInstalled && beat),
    transientOverlay,
    transientOverlayDetail: unsupportedOverlayDetail || transientOverlay,
  };
})()
"""
)

_READ_FARM_VISIT_EXPRESSION = r"""
(() => {
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  if (!(board instanceof Map) || services?.mapGrid?._cells !== board) return null;
  const scene = window.__fmvFarmSceneKind;
  if (scene === 'visitor') {
    const actions = [];
    for (const cell of board.values()) {
      const object = cell?.content;
      const behavior = object?.getBehavior?.('visitorAction');
      if (!behavior || typeof behavior.actionType !== 'string') continue;
      actions.push({
        column: cell.column,
        row: cell.row,
        blueprintID: object.getBlueprintID?.(),
        objectID: Number.isInteger(object.id) ? object.id :
          (Number.isInteger(object._id) ? object._id : null),
        actionType: behavior.actionType,
      });
    }
    actions.sort((left, right) => left.column - right.column || left.row - right.row);
    return {scene, tickets: null, panelOpen: false, destinationAvailable: false, actions};
  }
  if (scene !== 'own') return null;
  let popup = window.__fmvTrainPopup;
  if ((!popup || popup._destroyed === true || popup._state !== 4) &&
      window.__fmvFarmVisitPanelExpected === true) {
    let root = window.__fmvGameplayMapScreen;
    while (root?.parent) root = root.parent;
    const queue = [root];
    const seen = new Set();
    popup = null;
    while (queue.length && seen.size < 15000) {
      const candidate = queue.shift();
      if (!candidate || seen.has(candidate)) continue;
      seen.add(candidate);
      if (typeof candidate._onVisitButtonPressed === 'function' &&
          candidate._state === 4 && candidate._destroyed !== true) {
        popup = candidate;
        break;
      }
      for (const child of candidate.children || []) queue.push(child);
    }
    window.__fmvTrainPopup = popup;
  }
  const tickets = services?.ordersService?._inventory?.getInventoryItem?.('tickets');
  const destinationAvailable = Boolean(popup?._chosenPlayerDestination &&
    popup?._mainScreen?._visitButton?._isEnabled !== false &&
    popup?._creatingContext !== true);
  return {
    scene,
    tickets: Number.isInteger(tickets?.amount) ? tickets.amount : null,
    panelOpen: Boolean(popup),
    destinationAvailable,
    actions: [],
  };
})()
"""


def _farm_visit_action_expression(kind: str, scene_id: int | None, **expected: object) -> str:
    payload = json.dumps({"kind": kind, "sceneID": scene_id, **expected})
    return f"""
(() => {{
  const expected = {payload};
  if (window.__fmvRuntimeSceneIdentity !== expected.sceneID)
    return {{status: 'stale-source', detail: 'runtime-scene-changed'}};
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  if (!(board instanceof Map) || services?.mapGrid?._cells !== board)
    return {{status: 'unavailable', detail: 'farm-scene-not-current'}};
  try {{
    if (expected.kind === 'open') {{
      const handler = window.__fmvTrainHandler;
      const tickets = services?.ordersService?._inventory?.getInventoryItem?.('tickets');
      if (window.__fmvFarmSceneKind !== 'own' ||
          handler?._services !== services || typeof handler._openTrainstationPopup !== 'function')
        return {{status: 'unavailable', detail: 'train-handler-not-current'}};
      if (!Number.isInteger(tickets?.amount) || tickets.amount < 1)
        return {{status: 'rejected', detail: 'no-train-tickets'}};
      handler._openTrainstationPopup();
      window.__fmvFarmVisitPanelExpected = true;
      return {{status: 'submitted'}};
    }}
    if (expected.kind === 'start') {{
      const popup = window.__fmvTrainPopup;
      if (window.__fmvFarmSceneKind !== 'own' || !popup || popup._state !== 4)
        return {{status: 'unavailable', detail: 'train-popup-not-current'}};
      if (!popup._chosenPlayerDestination || popup._creatingContext === true ||
          popup?._mainScreen?._visitButton?._isEnabled === false)
        return {{status: 'busy', detail: 'visit-destination-unavailable'}};
      window.__fmvFarmVisitTransitionStartedAt = performance.now();
      popup._onVisitButtonPressed(popup._chosenPlayerDestination);
      return {{status: 'submitted'}};
    }}
    if (expected.kind === 'close') {{
      const popup = window.__fmvTrainPopup;
      if (window.__fmvFarmSceneKind !== 'own' || !popup ||
          typeof popup.close !== 'function')
        return {{status: 'unavailable', detail: 'train-popup-not-current'}};
      popup.close();
      window.__fmvTrainPopup = null;
      window.__fmvFarmVisitPanelExpected = false;
      return {{status: 'submitted'}};
    }}
    if (expected.kind === 'claim') {{
      const handler = window.__fmvVisitorActionHandler;
      const cell = services.mapGrid.getCell?.(expected.column, expected.row);
      const object = cell?.content;
      const objectID = Number.isInteger(object?.id) ? object.id : object?._id;
      const behavior = object?.getBehavior?.('visitorAction');
      if (window.__fmvFarmSceneKind !== 'visitor' || handler?._services !== services)
        return {{status: 'unavailable', detail: 'visitor-handler-not-current'}};
      if (!object || object.getBlueprintID?.() !== expected.blueprintID ||
          objectID !== expected.objectID || behavior?.actionType !== expected.actionType)
        return {{status: 'stale-source', detail: 'visitor-action-changed'}};
      handler._onActivityTapped(object);
      return {{status: 'submitted'}};
    }}
    if (expected.kind === 'return') {{
      const hud = window.__fmvVisitorReturnHud;
      if (window.__fmvFarmSceneKind !== 'visitor' ||
          typeof hud?._returnButtonClicked !== 'function')
        return {{status: 'unavailable', detail: 'return-handler-not-current'}};
      window.__fmvFarmVisitTransitionStartedAt = performance.now();
      hud._returnButtonClicked();
      return {{status: 'submitted'}};
    }}
    return {{status: 'rejected', detail: 'unknown-farm-visit-action'}};
  }} catch (error) {{
    if (expected.kind === 'start' || expected.kind === 'return')
      window.__fmvFarmVisitTransitionStartedAt = null;
    return {{status: 'rejected', detail: String(error?.message || error)}};
  }}
}})()
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


def _runtime_scene_identity(handler: str) -> str:
    return f"""const identity = window.__fmvGameplayServices?.mapGrid ||
    window.__fmvGameplayMapScreen || {handler} || board;
  const currentSceneId = identity && window.__fmvRuntimeSceneIds
    ? window.__fmvRuntimeSceneIds.get(identity) : null;"""


def _storage_bubble_pop_expression(object_id: int, scene_id: int | None) -> str:
    return f"""
(() => {{
  const expectedObjectID = {json.dumps(object_id)};
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  const interactionHandler = window.__fmvStorageBubbleInteractionHandler;
  const popHandler = window.__fmvStorageBubblePopHandler;
  {_runtime_scene_identity("window.__fmvItemInteractionHandler")}
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
  {_runtime_scene_identity("window.__fmvItemInteractionHandler")}
  if (!(board instanceof Map) || window.__fmvRuntimeBoard !== board ||
      currentSceneId !== {json.dumps(scene_id)})
    return {{status: 'unavailable', detail: 'runtime-scene-changed'}};

  {_overlay_context_expression()}
  const popupAnimationBusy = (popup) =>
    popup?._baseAnimationContent?.isAnimationPlaying?.('open') === true ||
    popup?._baseAnimationBackground?.isAnimationPlaying?.('open') === true ||
    popup?._baseAnimationContent?.isAnimationPlaying?.('close') === true ||
    popup?._baseAnimationBackground?.isAnimationPlaying?.('close') === true;
  const levelUpPopup = activePopup?._name === 'LevelUpPopup' &&
    typeof activePopup.close === 'function' ? activePopup : null;
  if (levelUpPopup) {{
    if (levelUpPopup._popupLocked === true ||
        levelUpPopup._popupInteractionsLocked === true ||
        popupAnimationBusy(levelUpPopup))
      return {{status: 'busy', detail: 'level-up'}};
    const inputBlocker = levelUpPopup.children?.find((child) => child?._events?.pointerup);
    const pointerUp = inputBlocker?._events?.pointerup;
    const listeners = Array.isArray(pointerUp) ? pointerUp : pointerUp ? [pointerUp] : [];
    if (!listeners.some((listener) =>
        listener?.fn === levelUpPopup.close && listener?.context === levelUpPopup))
      return {{status: 'busy', detail: 'level-up'}};
    try {{
      void levelUpPopup.close();
      return {{status: 'submitted', detail: 'level-up'}};
    }} catch (error) {{
      return {{status: 'rejected', detail: String(error?.message || error)}};
    }}
  }}

  const dailyChallengePopup = activePopup?._name === 'DailyChallengePopup' &&
    activePopup === services?.dailyChallenge?._popup &&
    typeof activePopup.close === 'function';
  const timedEventPopup = activePopup?._name === 'TimedEventPopup' &&
    typeof activePopup.close === 'function' &&
    typeof activePopup.onClosed?.listenOnce === 'function' &&
    Boolean(activePopup._options?.content);
  const dailyBonusPopup = activePopup?._name === 'DailyBonusPopup' &&
    typeof activePopup.close === 'function' &&
    typeof activePopup._claimReward === 'function' &&
    typeof activePopup._rewardCollected === 'boolean';
  const albumStartedPopup = activePopup?._name === 'AlbumStartedPopup' &&
    typeof activePopup.close === 'function' &&
    typeof activePopup.awaitPopupClosed === 'function';
  const travelSummaryRewardPopup = activePopup?._name === 'TravelSummaryRewardPopup' &&
    typeof activePopup.close === 'function';
  const pushNotificationOptInPopup =
    activePopup?._name === 'PushNotificationOptInPopup' &&
    typeof activePopup._onDismiss === 'function' &&
    typeof activePopup.close === 'function';
  if (pushNotificationOptInPopup) {{
    const detail = 'push-notification-opt-in';
    if (activePopup._popupLocked === true ||
        activePopup._popupInteractionsLocked === true || popupAnimationBusy(activePopup))
      return {{status: 'busy', detail}};
    try {{
      activePopup._onDismiss();
      return {{status: 'submitted', detail}};
    }} catch (error) {{
      return {{status: 'rejected', detail: String(error?.message || error)}};
    }}
  }}
  if (dailyChallengePopup || timedEventPopup || dailyBonusPopup || albumStartedPopup ||
      travelSummaryRewardPopup) {{
    const detail = dailyChallengePopup ? 'daily-challenge'
      : timedEventPopup ? 'timed-event'
      : dailyBonusPopup ? 'daily-bonus-collect'
      : albumStartedPopup ? 'sticker-album-started' : 'travel-summary-reward';
    if (timedEventPopup && window.__fmvTimedEventPopupSubmission === activePopup)
      return {{status: 'busy', detail: 'timed-event-transition'}};
    if (dailyBonusPopup && activePopup._rewardCollected)
      return {{status: 'busy', detail: 'daily-bonus-transition'}};
    if (activePopup._popupLocked === true ||
        activePopup._popupInteractionsLocked === true || popupAnimationBusy(activePopup))
      return {{status: 'busy', detail}};
    try {{
      if (timedEventPopup) window.__fmvTimedEventPopupSubmission = activePopup;
      void activePopup.close();
      return {{status: 'submitted', detail}};
    }} catch (error) {{
      if (window.__fmvTimedEventPopupSubmission === activePopup)
        window.__fmvTimedEventPopupSubmission = null;
      return {{status: 'rejected', detail: String(error?.message || error)}};
    }}
  }}
  const activePopupService = activePopup?.service || activePopup?._service;
  const rewardPopup = activePopup && typeof activePopup.close === 'function' && (
    activePopup?.rewardService === services?.rewardService ||
    activePopup?._rewardService === services?.rewardService);
  const promotionalPopup = activePopup && typeof activePopup.close === 'function' && (
    [services?.specialOfferService, services?.recurringConversionService]
      .includes(activePopupService) ||
    'upsellPopupOptions' in activePopup || '_upsellPopupOptions' in activePopup);
  if (rewardPopup || promotionalPopup) {{
    const detail = rewardPopup ? 'reward-popup' : 'promotional-popup';
    if (activePopup._popupLocked === true ||
        activePopup._popupInteractionsLocked === true || popupAnimationBusy(activePopup))
      return {{status: 'busy', detail}};
    try {{
      void activePopup.close();
      return {{status: 'submitted', detail}};
    }} catch (error) {{
      return {{status: 'rejected', detail: String(error?.message || error)}};
    }}
  }}

  const stickerNavigation = stage?.children?.find((child) =>
    Array.isArray(child?._viewStack) && child?._packOpeningView);
  const currentStickerView = stickerNavigation?._viewStack?.at(-1);
  const packOpeningView = stickerNavigation?._packOpeningView;
  const stickerController = packOpeningView?._currentPackAnimation;
  const stickerPackViewActive = Boolean(stickerNavigation && packOpeningView &&
    stickerNavigation.visible !== false &&
    packOpeningView?.visible !== false && packOpeningView?.parent === stickerNavigation &&
    currentStickerView === packOpeningView && packOpeningView?._destroyed !== true);
  const stickerPackActive = stickerPackViewActive &&
    stickerController?.parent === packOpeningView && stickerController?._isAnimating === true;
  if (!stickerPackActive) {{
    if (stickerPackViewActive)
      return {{status: 'busy', detail: 'sticker-pack-transition'}};
    const stickerAlbumTransition = stickerNavigation?.visible !== false &&
      currentStickerView && currentStickerView !== packOpeningView &&
      currentStickerView.parent === stickerNavigation && currentStickerView.visible !== false &&
      currentStickerView._destroyed !== true &&
      typeof currentStickerView.setRewards === 'function' &&
      typeof currentStickerView.playAnimation === 'function';
    if (stickerAlbumTransition)
      return {{status: 'busy', detail: 'sticker-album-transition'}};
    const stickerSetPanel = currentStickerView?.children?.find((child) =>
      typeof child?._onButtonPressed === 'function' &&
      typeof child?._animationResolve === 'function' &&
      child?._data?.config?.reward);
    const stickerSetButton = stickerSetPanel?.children?.find((child) =>
      child?.name === 'dailyBonusButton' && child?.visible !== false &&
      child?.renderable !== false && child?._isEnabled !== false &&
      child?._destroyed !== true);
    const stickerSetActive = stickerNavigation?.visible !== false &&
      currentStickerView && currentStickerView !== packOpeningView &&
      currentStickerView.parent === stickerNavigation && currentStickerView.visible !== false &&
      currentStickerView._destroyed !== true && Boolean(stickerSetPanel);
    if (!stickerSetActive)
      return {{status: 'stale-source', detail: 'no-supported-overlay'}};
    if (window.__fmvStickerSetCompletionSubmission === stickerSetPanel || !stickerSetButton)
      return {{status: 'busy', detail: 'sticker-set-transition'}};
    try {{
      window.__fmvStickerSetCompletionSubmission = stickerSetPanel;
      void stickerSetPanel._onButtonPressed();
      return {{status: 'submitted', detail: 'sticker-set-collect'}};
    }} catch (error) {{
      if (window.__fmvStickerSetCompletionSubmission === stickerSetPanel)
        window.__fmvStickerSetCompletionSubmission = null;
      return {{status: 'rejected', detail: String(error?.message || error)}};
    }}
  }}

  const stickerSpineView = stickerController._spineAnimation;
  const stickerRevealView = stickerController._revealAnimation;
  const stickerRaffleFlow = stickerController.children?.find((child) =>
    typeof child?.startRaffle === 'function' && typeof child?._start === 'function');
  const stickerRaffleProposal = stickerRaffleFlow?.children?.find((child) =>
    child?.parent === stickerRaffleFlow && child?._destroyed !== true &&
    typeof child?.startRaffleProposal === 'function' &&
    typeof child?._close === 'function' && Array.isArray(child?._raffleButtons) &&
    Array.isArray(child?._duplicate4PlusStarsStickers));
  if (stickerRaffleProposal) {{
    const proposalResolve = stickerRaffleProposal._animationResolve;
    if (typeof proposalResolve !== 'function')
      return {{status: 'busy', detail: 'sticker-raffle-proposal-initializing'}};
    if (stickerRaffleProposal._closing === true) {{
      try {{
        proposalResolve(undefined);
        return {{status: 'submitted', detail: 'sticker-raffle-proposal-recovery'}};
      }} catch (error) {{
        return {{status: 'rejected', detail: String(error?.message || error)}};
      }}
    }}
    const notNowLink = stickerRaffleProposal._notNowLink;
    const dismissEvent = ['pointertap', 'pointerup', 'click'].find((event) =>
      notNowLink?._events?.[event]);
    if (notNowLink?._destroyed === true || notNowLink?.interactive !== true ||
        typeof notNowLink?.emit !== 'function' || !dismissEvent)
      return {{status: 'busy', detail: 'sticker-raffle-proposal-initializing'}};
    try {{
      notNowLink.emit(dismissEvent);
      return {{status: 'submitted', detail: 'sticker-raffle-proposal'}};
    }} catch (error) {{
      return {{status: 'rejected', detail: String(error?.message || error)}};
    }}
  }}
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
  {_runtime_scene_identity("handler")}
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
  {_runtime_scene_identity("window.__fmvItemInteractionHandler")}
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
  {_runtime_scene_identity("handler")}
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
  const targetValidators = {{
    'immediate': () => content.hasBehavior?.('collectable'),
    'reward': () =>
      content.hasBehavior?.('collectable') && content.hasBehavior?.('currency') &&
        Array.isArray(content.getBehavior?.('collectable')?.reward) &&
        content.getBehavior('collectable').reward.length > 0,
    'upgrade': () =>
      content.hasBehavior?.('upgradeCard') && Number.isInteger(content.getTier?.()) &&
        typeof content.getBehavior('upgradeCard')?._data?.targetObjectTreeIngredient === 'string',
    'reward-container': () =>
      content.hasBehavior?.('crateReward') && content.hasBehavior?.('cooldown') &&
        Array.isArray(content.getBehavior('crateReward')?._data?.rewards) &&
        content.getBehavior('crateReward')._data.rewards.length > 0,
    'clear': () =>
      content.hasBehavior?.('mapSource') && content.hasBehavior?.('hitpoints') &&
        content.hasBehavior?.('resourceGate'),
    'obstacle-loot': () =>
      content.hasBehavior?.('mapSource') && content.hasBehavior?.('hitpoints') &&
        content.hasBehavior?.('lootable') &&
        Array.isArray(content.getBehavior?.('lootable')?.loot) &&
        content.getBehavior('lootable').loot.length > 0,
    'producer': () =>
      producer && !content.hasBehavior?.('cooldown') && !content.hasBehavior?.('depleted'),
    'depleted-producer': () => producer && content.hasBehavior?.('depleted'),
  }};
  const validateTarget = targetValidators[expectedKind];
  if (typeof validateTarget !== 'function')
    return {{status: 'invalid-target', detail: 'unsupported-interaction-kind'}};
  if (!validateTarget())
    return {{status: 'invalid-target', detail: `${{expectedKind}}-state-invalid`}};
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
      if (obstacleHandler?._services !== services || obstacleHandler._isActive === false ||
          typeof obstacleHandler._attemptPayment !== 'function' || !obstacleHandler._popoutStore)
        return {{status: 'unavailable', detail: 'obstacle-clear-handler-not-found'}};
      const gate = content.getBehavior('resourceGate');
      const position = content.getBehavior('gridPosition');
      const effectiveCost = typeof obstacleHandler._getTotalCost === 'function'
        ? obstacleHandler._getTotalCost(gate) : gate?._data?.cost;
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
  {_runtime_scene_identity("itemHandler")}
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
  {_runtime_scene_identity("window.__fmvItemInteractionHandler")}
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
  {_runtime_scene_identity("window.__fmvItemInteractionHandler")}
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
