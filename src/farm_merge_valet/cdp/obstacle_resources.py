"""Shared JavaScript helpers for authoritative obstacle resource state."""

_OBSTACLE_RESOURCE_HELPERS = r"""
  const resolveObstacleCost = (handler, gate) => {
    let costs = null;
    if (window.__fmvFarmSceneKind === 'event') {
      if (typeof handler?._getEventCost !== 'function') return null;
      costs = handler._getEventCost(gate);
    } else {
      costs = typeof handler?._getTotalCost === 'function'
        ? handler._getTotalCost(gate) : gate?._data?.cost;
    }
    if (!Array.isArray(costs)) return null;
    const eventItem = window.__fmvEventHud?._eventEnergyCounter?._eventEnergyItem;
    const expectedKey = window.__fmvFarmSceneKind === 'event'
      ? eventItem?._key : 'energy';
    if (typeof expectedKey !== 'string' || !expectedKey) return null;
    const cost = costs.find((item) => item?.key === expectedKey);
    return Number.isInteger(cost?.amount) && cost.amount >= 0
      ? {key: expectedKey, amount: cost.amount} : null;
  };
  const resolveObstacleResourceItem = (cost) => {
    if (!cost) return null;
    const item = window.__fmvFarmSceneKind === 'event'
      ? window.__fmvEventHud?._eventEnergyCounter?._eventEnergyItem
      : window.__fmvEnergyInventoryItem;
    return item?._key === cost.key && Number.isInteger(item.amount) && item.amount >= 0
      ? item : null;
  };
"""
