"""Bounded marketplace reads and strict adapter-boundary parsing."""

from __future__ import annotations

import json

from farm_merge_valet.automation.runtime import ActionResult, ActionStatus
from farm_merge_valet.core.marketplace import MarketplaceAction, MarketplaceLiveOffer

_READ_MARKETPLACE_EXPRESSION = r"""
(() => {
  const service = window.__fmvGameplayServices?.marketplaceService;
  if (!service || typeof service.getMarketplacePopupData !== 'function' ||
      typeof service.getItemConfigsByShop !== 'function') return null;
  const flash = service.flashDealsService || service._flashDealsService;
  const popup = service.getMarketplacePopupData();
  const shopValues = Array.isArray(popup) ? popup
    : Array.isArray(popup?.shops) ? popup.shops
    : popup?.shops && typeof popup.shops === 'object' ? Object.values(popup.shops)
    : popup && typeof popup === 'object' ? Object.values(popup) : [];
  const values = (value) => Array.isArray(value) ? value
    : value && typeof value === 'object' ? Object.values(value) : [];
  const scalar = (value) => Number.isFinite(value) ? value
    : Number.isFinite(value?.amount) ? value.amount : null;
  const normalize = (item, shopId, slotId = null) => {
    const payment = item?.payment || item?.price || item?._payment;
    const reward = item?.reward || item?.rewards?.[0] || item?._reward;
    const rewardData = reward?.data;
    const candidate = item?.candidateKey || item?.candidateId || item?.weightedData?.key ||
      reward?.key || reward?.id || reward?.itemId ||
      (slotId && Array.isArray(rewardData) ? rewardData[0] : null) ||
      (slotId ? item?.key || item?.id : null);
    const offerId = slotId || item?.id || item?.key || item?._key;
    const paymentType = payment?.type || item?.paymentType;
    const paymentKey = paymentType === 'free'
      ? null : payment?.key || payment?.id || payment?.itemId || null;
    const paymentAmount = paymentType === 'free'
      ? 0 : scalar(payment?.amount ?? payment?.value ?? item?.price);
    const rewardKey = reward?.key || reward?.id || reward?.itemId ||
      (Array.isArray(rewardData) ? rewardData[0] : rewardData?.key) || item?.rewardKey;
    const rewardAmount = Array.isArray(rewardData) ? rewardData.length
      : scalar(rewardData?.amount ?? reward?.amount ?? reward?.value ?? item?.rewardAmount);
    const stockItem = typeof service.getStockItem === 'function'
      ? service.getStockItem(offerId) : null;
    const stock = scalar(stockItem?.amount ?? stockItem?._amount ?? stockItem?.stock ??
      item?.renewableStock?.amount ?? item?.stock ?? item?.limit);
    if (![offerId, paymentType, rewardKey].every((value) => typeof value === 'string') ||
        !Number.isInteger(paymentAmount) || !Number.isInteger(rewardAmount) ||
        !Number.isInteger(stock)) return null;
    const policyKey = slotId ? `flash:${slotId}:${candidate}` : `free:${offerId}`;
    const inventory = service._inventory || service._services?.inventory ||
      window.__fmvGameplayServices?.ordersService?._inventory;
    const balanceItem = paymentKey && inventory?.getInventoryItem?.(paymentKey);
    const remainingMs = flash?._flashDealsTimer?._remaining;
    return {
      policyKey, offerId, slotId, candidateKey: slotId ? candidate : null,
      rewardKey, rewardAmount, paymentType, paymentKey, paymentAmount,
      remainingStock: stock, available: true, shopId,
      balance: Number.isInteger(balanceItem?.amount) ? balanceItem.amount : null,
      refreshRemainingSeconds: Number.isFinite(remainingMs) ? remainingMs / 1000
        : scalar(flash?.remainingSeconds ?? flash?.timeRemaining),
    };
  };
  const offers = [];
  for (const shop of shopValues) {
    const shopId = typeof shop === 'string' ? shop
      : shop?.name || shop?.id || shop?.key || shop?._key;
    const shopItems = typeof shop === 'string'
      ? service.getItemConfigsByShop(shop) : shop?.items;
    for (const item of values(shopItems)) {
      const id = item?.id || item?.key || item?._key;
      if (typeof id === 'string' && id.startsWith('flash_deal_')) {
        const selected = flash?.getFlashDealItem?.(id);
        const normalized = normalize(selected, shopId, id);
        if (normalized) offers.push(normalized);
      } else if ((item?.payment || item?.price || item?._payment)?.type === 'free') {
        const normalized = normalize(item, shopId);
        if (normalized) offers.push(normalized);
      }
    }
  }
  return {offers};
})()
"""


def parse_marketplace_offers(raw: object) -> tuple[MarketplaceLiveOffer, ...] | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("offers"), list):
        return None
    parsed: list[MarketplaceLiveOffer] = []
    for value in raw["offers"]:
        if not isinstance(value, dict):
            continue
        try:
            stock = _integer(value.get("remainingStock"))
            reward_amount = _integer(value.get("rewardAmount"))
            payment_amount = _integer(value.get("paymentAmount"))
            if stock < 0 or reward_amount <= 0 or payment_amount < 0:
                continue
            parsed.append(
                MarketplaceLiveOffer(
                    policy_key=_string(value.get("policyKey")),
                    offer_id=_string(value.get("offerId")),
                    slot_id=_optional_string(value.get("slotId")),
                    candidate_key=_optional_string(value.get("candidateKey")),
                    reward_key=_string(value.get("rewardKey")),
                    reward_amount=reward_amount,
                    payment_type=_string(value.get("paymentType")),
                    payment_key=_optional_string(value.get("paymentKey")),
                    payment_amount=payment_amount,
                    remaining_stock=stock,
                    available=value.get("available") is True,
                    balance=_optional_integer(value.get("balance")),
                    refresh_remaining_seconds=_optional_number(
                        value.get("refreshRemainingSeconds")
                    ),
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(parsed)


def marketplace_purchase_expression(action: MarketplaceAction, scene_id: int | None) -> str:
    """Build a fail-closed purchase using the exact live offer and game services."""
    expected = json.dumps(
        {
            "sceneId": scene_id,
            "policyKey": action.policy_key,
            "offerId": action.offer_id,
            "slotId": action.slot_id,
            "candidateKey": action.candidate_key,
            "rewardKey": action.reward_key,
            "rewardAmount": action.reward_amount,
            "paymentType": action.payment_type,
            "paymentKey": action.payment_key,
            "paymentAmount": action.payment_amount,
            "stock": action.expected_stock,
        }
    )
    return f"""
(async () => {{
  const expected = {expected};
  if (window.__fmvRuntimeSceneIdentity !== expected.sceneId)
    return {{status: 'stale-source', detail: 'runtime-scene-changed'}};
  const service = window.__fmvGameplayServices?.marketplaceService;
  if (!service || typeof service.getMarketplacePopupData !== 'function' ||
      typeof service.getStockItem !== 'function')
    return {{status: 'unavailable', detail: 'marketplace-service-unavailable'}};
  if (window.__fmvMarketplacePurchasePending)
    return {{status: 'busy', detail: 'marketplace-purchase-pending'}};
  const handler = window.__fmvItemInteractionHandler;
  if (handler && (handler.busy || handler.isBusy?.() || handler.dragging ||
      handler._dragging || handler._currentObject || handler._originCell))
    return {{status: 'busy', detail: 'game-action-in-progress'}};
  const beat = window.__fmvHeartbeat;
  if (!beat || performance.now() - beat.timestamp > 1500)
    return {{status: 'busy', detail: 'game-heartbeat-stale'}};
  let stage = window.__fmvGameplayMapScreen;
  while (stage?.parent) stage = stage.parent;
  const popupLayer = stage?.children?.[0]?.children?.find((child) => child?.name === 'popup');
  const activePopup = popupLayer?.children?.some((child) =>
    child?.visible !== false && child?.renderable !== false && child?._destroyed !== true);
  if (activePopup) return {{status: 'busy', detail: 'game-popup-open'}};
  const popup = service.getMarketplacePopupData?.();
  const shops = Array.isArray(popup) ? popup : Array.isArray(popup?.shops) ? popup.shops
    : popup?.shops && typeof popup.shops === 'object' ? Object.values(popup.shops) : [];
  const shopEntries = shops.map((shop) => ({{
    id: typeof shop === 'string' ? shop : shop?.name || shop?.id || shop?.key || shop?._key,
    items: typeof shop === 'string' ? service.getItemConfigsByShop?.(shop) : shop?.items,
  }})).filter((shop) => shop.id);
  let live = null;
  let configured = null;
  for (const shop of shopEntries) {{
    const items = Array.isArray(shop.items) ? shop.items : shop.items &&
      typeof shop.items === 'object' ? Object.values(shop.items) : [];
    configured = items.find((item) =>
      (item?.id || item?.key || item?._key) === expected.offerId);
    if (!configured) continue;
    live = expected.slotId
      ? (service.flashDealsService || service._flashDealsService)?.getFlashDealItem?.(
          expected.slotId)
      : configured;
    if (live) break;
  }}
  if (!live) return {{status: 'unavailable', detail: 'offer-not-live'}};
  const payment = live.payment || live.price || live._payment;
  const reward = live.reward || live.rewards?.[0] || live._reward;
  const rewardData = reward?.data;
  const value = (raw) => Number.isFinite(raw) ? raw
    : Number.isFinite(raw?.amount) ? raw.amount : null;
  const candidate = live.candidateKey || live.candidateId || reward?.key || reward?.id ||
    reward?.itemId || (expected.slotId ? live.key || live.id : null);
  const stockItem = service.getStockItem?.(expected.offerId);
  const actual = {{
    candidateKey: expected.slotId ? (live.weightedData?.key || candidate) : null,
    rewardKey: reward?.key || reward?.id || reward?.itemId ||
      (Array.isArray(rewardData) ? rewardData[0] : rewardData?.key) || live.rewardKey,
    rewardAmount: Array.isArray(rewardData) ? rewardData.length
      : value(rewardData?.amount ?? reward?.amount ?? reward?.value ?? live.rewardAmount),
    paymentType: payment?.type || live.paymentType,
    paymentKey: (payment?.type || live.paymentType) === 'free'
      ? null : payment?.key || payment?.id || payment?.itemId || null,
    paymentAmount: (payment?.type || live.paymentType) === 'free'
      ? 0 : value(payment?.amount ?? payment?.value ?? live.price),
    stock: value(stockItem?.amount ?? stockItem?._amount ?? stockItem?.stock ??
      live.renewableStock?.amount ?? live.stock ?? live.limit),
  }};
  if (actual.candidateKey !== expected.candidateKey ||
      actual.rewardKey !== expected.rewardKey || actual.rewardAmount !== expected.rewardAmount ||
      actual.paymentType !== expected.paymentType || actual.paymentKey !== expected.paymentKey ||
      actual.paymentAmount !== expected.paymentAmount || actual.stock !== expected.stock ||
      !Number.isInteger(actual.stock) || actual.stock <= 0)
    return {{status: 'stale-source', detail: 'live-offer-mismatch'}};
  if (!((expected.slotId && actual.paymentType === 'inventory' &&
          ['coins', 'gems'].includes(actual.paymentKey)) ||
        (!expected.slotId && actual.paymentType === 'free' && actual.paymentKey === null &&
          actual.paymentAmount === 0)))
    return {{status: 'rejected', detail: 'ineligible-payment'}};
  const inventory = service._inventory || service._services?.inventory ||
    window.__fmvGameplayServices?.ordersService?._inventory;
  const balance = actual.paymentKey && inventory?.getInventoryItem?.(actual.paymentKey)?.amount;
  if (expected.slotId && (!Number.isInteger(balance) || balance < actual.paymentAmount))
    return {{status: 'rejected', detail: 'insufficient-funds'}};
  const services = service._services || window.__fmvGameplayServices;
  const rewardService = services?.rewardService;
  const storageBubble = services?.storageBubble;
  const autoSaveService = service._autoSaveService;
  if (!autoSaveService || typeof autoSaveService.forceSave !== 'function')
    return {{status: 'unavailable', detail: 'autosave-service-unavailable'}};
  let objectContent = null;
  if (reward?.type === 'inventory') {{
    if (rewardData?.key !== expected.rewardKey ||
        rewardData?.amount !== expected.rewardAmount ||
        typeof rewardService?.giveInventoryReward !== 'function' ||
        !services?.cameraService?.cameraView)
      return {{status: 'stale-source', detail: 'inventory-reward-unavailable'}};
  }} else if (reward?.type === 'object') {{
    if (!Array.isArray(rewardData) || rewardData.length !== expected.rewardAmount ||
        rewardData.some((key) => key !== expected.rewardKey))
      return {{status: 'stale-source', detail: 'object-reward-mismatch'}};
    const blueprintCollection = services?.ordersService?._recipes?._services
      ?.blueprintCollection;
    if (typeof blueprintCollection?.getBlueprint !== 'function' ||
        typeof storageBubble?.createBubbleAndShowContent !== 'function')
      return {{status: 'unavailable', detail: 'object-reward-service-unavailable'}};
    objectContent = rewardData.map((key) => {{
      const blueprint = blueprintCollection.getBlueprint(key);
      return blueprint?.components == null
        ? null : {{blueprint: key, data: blueprint.components}};
    }});
    if (objectContent.some((item) => item === null))
      return {{status: 'unavailable', detail: 'object-reward-blueprint-unavailable'}};
  }} else {{
    return {{status: 'rejected', detail: 'unsupported-reward-type'}};
  }}
  if (expected.slotId && typeof inventory?.spendAmount !== 'function')
    return {{status: 'unavailable', detail: 'inventory-spend-unavailable'}};
  window.__fmvMarketplacePurchasePending = true;
  let mutationStarted = false;
  try {{
    if (expected.slotId) {{
      mutationStarted = true;
      await inventory.spendAmount(actual.paymentKey, actual.paymentAmount);
    }}
    mutationStarted = true;
    stockItem.amount = actual.stock - 1;
    if (reward.type === 'inventory') {{
      await rewardService.giveInventoryReward({{
        reward: rewardData,
        parent: services.cameraService.cameraView,
      }});
    }} else {{
      storageBubble.createBubbleAndShowContent(objectContent);
    }}
    await autoSaveService.forceSave();
    return {{status: 'submitted'}};
  }} catch (error) {{
    return {{
      status: mutationStarted ? 'submitted' : 'rejected',
      detail: String(error?.message || error),
    }};
  }} finally {{
    window.__fmvMarketplacePurchasePending = false;
  }}
}})()
"""


def parse_action_result(raw: object) -> ActionResult:
    if not isinstance(raw, dict):
        return ActionResult(ActionStatus.REJECTED, "invalid-marketplace-action-response")
    status_value = raw.get("status")
    try:
        status = (
            ActionStatus(status_value) if isinstance(status_value, str) else ActionStatus.REJECTED
        )
    except ValueError:
        status = ActionStatus.REJECTED
    detail = raw.get("detail") if isinstance(raw.get("detail"), str) else None
    return ActionResult(status, detail)


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError
    return value


def _optional_string(value: object) -> str | None:
    return None if value is None else _string(value)


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError
    return value


def _optional_integer(value: object) -> int | None:
    return None if value is None else _integer(value)


def _optional_number(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        raise TypeError
    return float(value)
