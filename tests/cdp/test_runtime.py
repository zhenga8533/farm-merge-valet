import logging
from threading import Event, Thread

from farm_merge_valet.automation.runtime import (
    ActionResult,
    ActionStatus,
    EventRewardState,
    GameRuntime,
    RuntimeCapability,
    RuntimeHealth,
    SnapshotOptions,
    TransientOverlayKind,
)
from farm_merge_valet.cdp.runtime import GameRuntimeAdapter
from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION
from farm_merge_valet.cdp.transport import CdpTimeoutError
from farm_merge_valet.core.items import InteractionTargetKind
from farm_merge_valet.core.obstacles import WorkerState
from farm_merge_valet.core.shops import ShopOrderState


def test_runtime_health_exposes_adapter_neutral_capabilities() -> None:
    health = RuntimeHealth(
        True,
        1,
        True,
        True,
        True,
        True,
        1,
        0.0,
        True,
        interaction_available=True,
        shop_available=True,
        removal_available=True,
        reward_interaction_available=True,
        obstacle_clear_available=True,
        upgrade_interaction_available=True,
        reward_container_available=True,
        storage_bubble_available=True,
        marketplace_available=True,
        farm_visit_available=True,
        land_expansion_available=True,
    )

    assert all(health.supports(capability) for capability in RuntimeCapability)


def test_runtime_health_tracks_heartbeat_advancement(monkeypatch) -> None:
    samples = iter(
        [
            {
                "sceneId": 3,
                "board": True,
                "itemDrop": True,
                "interaction": True,
                "rewardInteraction": True,
                "crateSpawn": True,
                "inventory": True,
                "heartbeat": 10,
                "heartbeatAgeMs": 2,
            },
            {
                "sceneId": 3,
                "board": True,
                "itemDrop": True,
                "interaction": True,
                "rewardInteraction": True,
                "crateSpawn": True,
                "inventory": True,
                "heartbeat": 11,
                "heartbeatAgeMs": 1,
                "itemActionBusy": True,
                "transientOverlay": "sticker-pack-collect",
                "transientOverlayDetail": "sticker-pack-collect",
                "backendConnected": False,
                "backendConnectivityState": 3,
                "backendConsecutiveHangingPings": 3,
            },
        ]
    )
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", lambda *_, **__: next(samples))
    adapter = GameRuntimeAdapter(9222, "Farm", crate_delay_min=0, crate_delay_max=0)

    assert not adapter.read_runtime_health().heartbeat_advancing
    health = adapter.read_runtime_health()
    assert health.heartbeat_advancing
    assert health.item_action_busy
    assert health.interaction_available
    assert health.reward_interaction_available
    assert health.transient_overlay is TransientOverlayKind.STICKER_PACK_COLLECT
    assert health.transient_overlay_detail == "sticker-pack-collect"
    assert health.backend_connected is False
    assert health.backend_connectivity_state == 3
    assert health.backend_consecutive_hanging_pings == 3


def test_runtime_health_accepts_background_frame_cadence_but_rejects_stale_frame(
    monkeypatch,
) -> None:
    samples = iter(
        [
            {"heartbeat": 10, "heartbeatAgeMs": 900},
            {"heartbeat": 11, "heartbeatAgeMs": 1100},
            {"heartbeat": 12, "heartbeatAgeMs": 1600},
        ]
    )
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", lambda *_, **__: next(samples))
    adapter = GameRuntimeAdapter(9222, "Farm")

    assert not adapter.read_runtime_health().heartbeat_advancing
    assert adapter.read_runtime_health().heartbeat_advancing
    assert not adapter.read_runtime_health().heartbeat_advancing


def test_unknown_transient_overlay_kind_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_, **__: {
            "sceneId": 3,
            "board": True,
            "itemDrop": True,
            "heartbeat": 10,
            "heartbeatAgeMs": 1,
            "transientOverlay": "future-overlay",
            "transientOverlayDetail": "popup:FuturePopup",
        },
    )
    adapter = GameRuntimeAdapter(9222, "Farm")

    health = adapter.read_runtime_health()

    assert health.transient_overlay is TransientOverlayKind.UNSUPPORTED
    assert health.transient_overlay_detail == "popup:FuturePopup"


def test_health_parses_visit_transition_and_travel_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_, **__: {
            "sceneId": 3,
            "board": True,
            "itemDrop": True,
            "heartbeat": 10,
            "heartbeatAgeMs": 1,
            "sceneTransitionActive": True,
            "transientOverlay": "travel-summary-reward",
        },
    )

    health = GameRuntimeAdapter(9222, "Farm").read_runtime_health()

    assert health.scene_transition_active
    assert health.transient_overlay is TransientOverlayKind.TRAVEL_SUMMARY_REWARD


def test_health_parses_building_upgrade_overlay(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_, **__: {
            "sceneId": 3,
            "board": True,
            "itemDrop": True,
            "heartbeat": 10,
            "heartbeatAgeMs": 1,
            "transientOverlay": "building-upgrade",
        },
    )

    health = GameRuntimeAdapter(9222, "Farm").read_runtime_health()

    assert health.transient_overlay is TransientOverlayKind.BUILDING_UPGRADE


def test_health_parses_disconnection_layer(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_, **__: {
            "sceneId": 3,
            "board": True,
            "itemDrop": True,
            "heartbeat": 10,
            "heartbeatAgeMs": 1,
            "transientOverlay": "disconnected",
            "transientOverlayDetail": "layer:disconnection",
        },
    )

    health = GameRuntimeAdapter(9222, "Farm").read_runtime_health()

    assert health.transient_overlay is TransientOverlayKind.DISCONNECTED
    assert health.transient_overlay_detail == "layer:disconnection"


def test_transient_overlay_submission_uses_native_known_handlers(monkeypatch) -> None:
    expressions: list[str] = []

    def evaluate_expression(_port, expression, _title, **_kwargs):
        expressions.append(expression)
        return {"status": "submitted", "detail": "sticker-pack-skip"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", evaluate_expression)
    adapter = GameRuntimeAdapter(9222, "Farm")
    adapter._scene_id = 4

    result = adapter.dismiss_transient_overlay()

    assert result.status is ActionStatus.SUBMITTED
    assert result.detail == "sticker-pack-skip"
    assert "levelUpPopup.close()" in expressions[0]
    assert "skip.call(stickerSpineView)" in expressions[0]
    assert "collectButton.destroy()" in expressions[0]
    assert "stickerRevealView._animationResolve()" in expressions[0]
    assert "void stickerSetPanel._onButtonPressed()" in expressions[0]
    assert "submitOverlayAction(stickerSetPanel, 'dismiss'" in expressions[0]
    assert "void activePopup.close()" in expressions[0]
    assert "activePopup?._name === 'DailyBonusPopup'" in expressions[0]
    assert "activePopup?._name === 'DailyChallengePopup'" in expressions[0]
    assert "activePopup === services?.dailyChallenge?._popup" in expressions[0]
    assert "activePopup?._name === 'TimedEventPopup'" in expressions[0]
    assert "submitOverlayAction(activePopup, 'dismiss'" in expressions[0]
    assert "timed-event-transition" in expressions[0]
    assert "popup?._baseAnimationContent?.isAnimationPlaying?.('open')" in expressions[0]
    assert "popupAnimationBusy(levelUpPopup)" in expressions[0]
    assert "listener?.fn === levelUpPopup.close" in expressions[0]
    assert "level-up-handler-not-current" not in expressions[0]
    assert "activePopup?._name === 'AlbumStartedPopup'" in expressions[0]
    assert "activePopup?._name === 'TravelSummaryRewardPopup'" in expressions[0]
    assert "travel-summary-reward" in expressions[0]
    assert "activePopup?._name === 'LikeClaimOverlay'" in expressions[0]
    assert "likeClaimPopup ? 'like-claim'" in expressions[0]
    assert "activePopup?._name === 'BuildingUpgradePopup'" in expressions[0]
    assert "buildingUpgradePopup ? 'building-upgrade'" in expressions[0]
    assert "activePopup?._name === 'PushNotificationOptInPopup'" in expressions[0]
    assert "activePopup._onDismiss()" in expressions[0]
    assert "push-notification-opt-in" in expressions[0]
    assert "activePopup._rewardCollected" in expressions[0]
    assert "sticker-album-transition" in expressions[0]
    assert "typeof proposalResolve !== 'function'" in expressions[0]
    assert "proposalResolve(undefined)" in expressions[0]
    assert "notNowLink.emit(dismissEvent)" in expressions[0]
    assert "submitOverlayAction(stickerRaffleProposal, 'recovery'" in expressions[0]
    assert "sticker-raffle-proposal-closing" in expressions[0]
    assert "window.__fmvOverlaySubmissionTimes" in expressions[0]
    assert "performance.now() - previous >= 10000" in expressions[0]
    assert "`${waitingDetail}-timeout`" in expressions[0]
    assert "stickerRaffleProposal._close(undefined)" not in expressions[0]
    assert "services?.specialOfferService" in expressions[0]
    assert "services?.recurringConversionService" in expressions[0]
    assert "currentSceneId !== 4" in expressions[0]


def test_health_detects_each_supported_reward_overlay_phase() -> None:
    from farm_merge_valet.cdp.scripts import _HEALTH_EXPRESSION

    assert "activePopup?._name === 'LevelUpPopup'" in _HEALTH_EXPRESSION
    assert "Array.isArray(child?._viewStack)" in _HEALTH_EXPRESSION
    assert "child?._packOpeningView" in _HEALTH_EXPRESSION
    assert "typeof stickerSpineView._onSkippedPressed === 'function'" in _HEALTH_EXPRESSION
    assert "child?.name === 'ConsentButton'" in _HEALTH_EXPRESSION
    assert "stickerController?._isAnimating === true" in _HEALTH_EXPRESSION
    assert "Boolean(stickerNavigation && packOpeningView" in _HEALTH_EXPRESSION
    assert "sticker-pack-transition" in _HEALTH_EXPRESSION
    assert "sticker-pack-collect" in _HEALTH_EXPRESSION
    assert "sticker-raffle-proposal" in _HEALTH_EXPRESSION
    assert "stickerRaffleProposal._closing" not in _HEALTH_EXPRESSION
    assert "Array.isArray(child?._duplicate4PlusStarsStickers)" in _HEALTH_EXPRESSION
    assert "sticker-set-transition" in _HEALTH_EXPRESSION
    assert "sticker-set-collect" in _HEALTH_EXPRESSION
    assert "daily-bonus-collect" in _HEALTH_EXPRESSION
    assert "daily-bonus-transition" in _HEALTH_EXPRESSION
    assert "daily-challenge" in _HEALTH_EXPRESSION
    assert "activePopup === services?.dailyChallenge?._popup" in _HEALTH_EXPRESSION
    assert "activePopup?._name === 'TimedEventPopup'" in _HEALTH_EXPRESSION
    assert "overlaySubmitted(activePopup)" in _HEALTH_EXPRESSION
    assert "timed-event-transition" in _HEALTH_EXPRESSION
    assert "child?._name === 'DailyBonusPopup'" not in _HEALTH_EXPRESSION
    assert "activePopup?._name === 'DailyBonusPopup'" in _HEALTH_EXPRESSION
    assert "activePopup?._name === 'AlbumStartedPopup'" in _HEALTH_EXPRESSION
    assert "activePopup?._name === 'TravelSummaryRewardPopup'" in _HEALTH_EXPRESSION
    assert "travel-summary-reward" in _HEALTH_EXPRESSION
    assert "activePopup?._name === 'LikeClaimOverlay'" in _HEALTH_EXPRESSION
    assert "likeClaimPopup ? 'like-claim'" in _HEALTH_EXPRESSION
    assert "activePopup?._name === 'BuildingUpgradePopup'" in _HEALTH_EXPRESSION
    assert "buildingUpgradePopup ? 'building-upgrade'" in _HEALTH_EXPRESSION
    assert "activePopup?._name === 'PushNotificationOptInPopup'" in _HEALTH_EXPRESSION
    assert "push-notification-opt-in" in _HEALTH_EXPRESSION
    assert "activePopup?._name === 'TrainstationPopup'" in _HEALTH_EXPRESSION
    assert "sceneTransitionActive" in _HEALTH_EXPRESSION
    assert "sticker-album-started" in _HEALTH_EXPRESSION
    assert "sticker-album-transition" in _HEALTH_EXPRESSION
    assert "reward-popup" in _HEALTH_EXPRESSION
    assert "promotional-popup" in _HEALTH_EXPRESSION
    assert "activePopup?._rewardService === services?.rewardService" in _HEALTH_EXPRESSION
    assert "'upsellPopupOptions' in activePopup" in _HEALTH_EXPRESSION
    assert "_userDisconnectBySingleSocket" in _HEALTH_EXPRESSION
    assert "_singleSocketDisconnect" in _HEALTH_EXPRESSION
    assert "activePopup?._name === 'ConnectedOnDifferentDevicePopup'" in _HEALTH_EXPRESSION
    assert "session-replaced" in _HEALTH_EXPRESSION
    assert "blockingLayer?.name === 'disconnection' ? 'disconnected'" in _HEALTH_EXPRESSION
    assert "unsupportedOverlayDetail" in _HEALTH_EXPRESSION
    assert "['disconnection', 'onboarding', 'fake_ad']" in _HEALTH_EXPRESSION
    assert "backendConnection?.isConnected" in _HEALTH_EXPRESSION
    assert "backendConnection?._consecutiveHangingPings" in _HEALTH_EXPRESSION


def test_scene_change_invalidates_cached_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_, **__: {
            "sceneId": 9,
            "board": True,
            "itemDrop": True,
            "crateSpawn": False,
            "inventory": True,
            "heartbeat": 1,
            "heartbeatAgeMs": 0,
        },
    )
    adapter = GameRuntimeAdapter(9222, "Farm", crate_delay_min=0, crate_delay_max=0)
    adapter._scene_id = 8

    result = adapter.read_runtime_health()

    assert adapter._scene_id is None
    assert result.detail == "runtime-scene-changed"


def test_structured_drop_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_, **__: {"status": "stale-source", "detail": "gone"},
    )
    adapter = GameRuntimeAdapter(9222, "Farm")

    result = adapter.submit_item_drop((100, 200), (-3, 4))

    assert result.status is ActionStatus.STALE_SOURCE
    assert result.detail == "gone"


def test_read_workers_returns_total_and_available_counts(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_args, **_kwargs: {"total": 3, "available": 2},
    )

    assert GameRuntimeAdapter(9222, "Farm").read_workers() == WorkerState(3, 2)


def test_crate_result_reports_partial_count(monkeypatch) -> None:
    responses = iter(
        [
            {
                "status": "submitted",
                "spawned": 2,
                "remaining": 5,
                "availableBefore": 7,
            },
            {
                "status": "rejected",
                "spawned": 0,
                "remaining": 5,
                "availableBefore": 5,
            },
        ]
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_, **__: next(responses),
    )
    adapter = GameRuntimeAdapter(9222, "Farm", crate_delay_min=0, crate_delay_max=0)

    result = adapter.spawn_supply_crates(10)

    assert result.status is ActionStatus.SUBMITTED
    assert result.spawned == 2
    assert result.remaining == 5
    assert result.available_before == 7


def test_crate_submission_waits_for_authoritative_state_change(monkeypatch) -> None:
    expressions = []
    responses = iter(
        [
            {"status": "submitted", "spawned": 1, "remaining": 1},
            {"status": "submitted", "spawned": 1, "remaining": 0},
        ]
    )

    def evaluate_expression(_port, expression, _title, **_kwargs):
        expressions.append(expression)
        return next(responses)

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", evaluate_expression)

    result = GameRuntimeAdapter(
        9222, "Farm", crate_delay_min=0, crate_delay_max=0
    ).spawn_supply_crates(10)

    assert result.spawned == 2
    assert result.remaining == 0
    assert all("waitForChange" in expression for expression in expressions)
    assert all("no-authoritative-crate-change" in expression for expression in expressions)
    assert all("crate-signal-not-current" in expression for expression in expressions)
    assert all("signal.fire({})" in expression for expression in expressions)


def test_crate_pacing_stops_promptly_when_cancelled(monkeypatch) -> None:
    cancel = Event()
    cancel.set()
    calls = 0

    def evaluate_expression(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"status": "submitted", "spawned": 1, "remaining": 5}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", evaluate_expression)
    adapter = GameRuntimeAdapter(9222, "Farm", crate_delay_min=1, crate_delay_max=1)
    adapter.set_cancel_event(cancel)

    result = adapter.spawn_supply_crates(5)

    assert result.spawned == 1
    assert calls == 1


def test_discovery_uses_active_gameplay_crate_signal() -> None:
    from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "hudService?._commonEvents?.spawnCrates" in _DISCOVER_EXPRESSION
    assert "crateSubscribers.length > 0" in _DISCOVER_EXPRESSION
    assert "_commonEvents?.spawnCrates === crateSignal" in _HEALTH_EXPRESSION
    assert "subscribers(crateSignal).length > 0" in _HEALTH_EXPRESSION
    assert "orders?._inventory?.getInventoryItem?.('crates')" in _DISCOVER_EXPRESSION
    assert "services?.ordersService?._inventory?.getInventoryItem?.('crates')" in (
        _HEALTH_EXPRESSION
    )
    assert "inventory?.onAnimateChanges" not in _DISCOVER_EXPRESSION


def test_event_discovery_replaces_destroyed_scene_references() -> None:
    from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _READ_EVENT_EXPRESSION

    for expression in (_DISCOVER_EXPRESSION, _READ_EVENT_EXPRESSION):
        assert "candidate._destroyed !== true" in expression
        assert "mapGridView?._view" in expression
    assert "window.__fmvGameplayMapScreen = scene" in _READ_EVENT_EXPRESSION


def test_event_discovery_can_use_active_event_service_without_launcher() -> None:
    from farm_merge_valet.cdp.scripts import _READ_EVENT_EXPRESSION, _event_action_expression

    assert "gameplayServices?.mapGrid?._isActive === false" in _READ_EVENT_EXPRESSION
    assert "eventInstance?._eventActive === true" in _READ_EVENT_EXPRESSION
    assert "typeof eventService?._goToEventMap === 'function'" in _READ_EVENT_EXPRESSION
    assert "typeof eventService?.goToEventMap === 'function'" in _READ_EVENT_EXPRESSION
    assert "const sharedInventory = gameplayServices?.ordersService?._inventory" in (
        _READ_EVENT_EXPRESSION
    )
    assert "launcher || directEnter" in _READ_EVENT_EXPRESSION
    assert "eventService.goToEventMap.bind(eventService)" in _event_action_expression(
        "enter", "jungle"
    )


def test_discovery_does_not_require_marketplace_ui_purchase_notifier() -> None:
    from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    for expression in (_DISCOVER_EXPRESSION, _HEALTH_EXPRESSION):
        assert "getMarketplacePopupData" in expression
        assert "getItemConfigsByShop" in expression
        assert "getStockItem" in expression
        assert "purchaseItem" not in expression


def test_crate_claim_rebinds_inventory_from_active_scene(monkeypatch) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {
            "status": "rejected",
            "spawned": 0,
            "remaining": 0,
            "availableBefore": 0,
            "detail": "no-supply-crates",
        }

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    result = GameRuntimeAdapter(9222, "Farm").spawn_supply_crates(5)

    assert result.available_before == 0
    assert "services?.ordersService?._inventory?.getInventoryItem?.('crates')" in expression
    assert "window.__fmvCrateInventoryItem = inventory" in expression


def test_discovery_validates_internal_interaction_handler() -> None:
    from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "typeof value._simulateClick === 'function'" in _DISCOVER_EXPRESSION
    assert "window.__fmvInteractionHandler" in _DISCOVER_EXPRESSION
    assert "onGestureTap" in _HEALTH_EXPRESSION


def test_discovery_validates_shovel_handler() -> None:
    from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "typeof candidate._onContentRemove === 'function'" in _DISCOVER_EXPRESSION
    assert "window.__fmvShovelHandler" in _DISCOVER_EXPRESSION
    assert "services?.shovelService" in _HEALTH_EXPRESSION


def test_discovery_validates_reward_interaction_handler() -> None:
    from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "typeof candidate._collectReward === 'function'" in _DISCOVER_EXPRESSION
    assert "window.__fmvRewardInteractionHandler" in _DISCOVER_EXPRESSION
    assert "rewardInteractionHandler?._services === services" in _HEALTH_EXPRESSION


def test_discovery_validates_obstacle_clear_handler() -> None:
    from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "typeof candidate._attemptPayment === 'function'" in _DISCOVER_EXPRESSION
    assert "typeof candidate._getTotalCost === 'function'" not in _DISCOVER_EXPRESSION
    assert "candidate._isActive !== false" in _DISCOVER_EXPRESSION
    assert "window.__fmvObstacleClearHandler" in _DISCOVER_EXPRESSION
    assert "obstacleClearHandler?._services === services" in _HEALTH_EXPRESSION


def test_discovery_validates_upgrade_card_service() -> None:
    from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "typeof upgradeCard.upgradeItemGrade === 'function'" in _DISCOVER_EXPRESSION
    assert "upgradeCard?._services === services" in _HEALTH_EXPRESSION


def test_discovery_validates_reward_container_handler() -> None:
    from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "typeof candidate._getCrateUnlockCostObjects === 'function'" in _DISCOVER_EXPRESSION
    assert "window.__fmvRewardContainerHandler" in _DISCOVER_EXPRESSION
    assert "rewardContainerHandler?._services === services" in _HEALTH_EXPRESSION


def test_discovery_validates_storage_bubble_handlers() -> None:
    from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "typeof candidate._onBubblePointerDown === 'function'" in _DISCOVER_EXPRESSION
    assert "typeof candidate._onStorageBubbleTapped === 'function'" in _DISCOVER_EXPRESSION
    assert "window.__fmvStorageBubbleInteractionHandler" in _DISCOVER_EXPRESSION
    assert "storageBubbleInteractionHandler?._services === services" in _HEALTH_EXPRESSION


def test_storage_bubble_reader_preserves_contents(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_, **__: [
            {"objectID": 41, "contentIDs": ["energy_1", "coin_1"]},
            {"objectID": "invalid", "contentIDs": []},
        ],
    )

    bubbles = GameRuntimeAdapter(9222, "Farm").read_storage_bubbles()

    assert bubbles is not None
    assert len(bubbles) == 1
    assert bubbles[0].object_id == 41
    assert bubbles[0].content_ids == ("energy_1", "coin_1")


def test_storage_bubble_pop_uses_native_handler_and_revalidates_space(monkeypatch) -> None:
    expressions: list[str] = []

    def evaluate_expression(_port, expression, _title, **_kwargs):
        expressions.append(expression)
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", evaluate_expression)
    adapter = GameRuntimeAdapter(9222, "Farm")
    adapter._scene_id = 4

    result = adapter.submit_storage_bubble_pop(41)

    assert result.status is ActionStatus.SUBMITTED
    assert "candidate?.id === expectedObjectID" in expressions[0]
    assert "services.gridFilter?.getEmptyCells?.().length === 0" in expressions[0]
    assert "popHandler._onStorageBubbleTapped(bubble)" in expressions[0]
    assert "currentSceneId !== 4" in expressions[0]


def test_discovery_validates_shop_order_service() -> None:
    from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "typeof orders?.getCurrentOrders === 'function'" in _DISCOVER_EXPRESSION
    assert "typeof orders?.startOrder === 'function'" in _DISCOVER_EXPRESSION
    assert "window.__fmvOrdersService" in _DISCOVER_EXPRESSION
    assert "services?.ordersService === orders" in _HEALTH_EXPRESSION


def test_cached_discovery_is_a_debug_diagnostic(monkeypatch, caplog) -> None:
    responses = iter(
        [
            True,
            {
                "status": "unavailable",
                "sceneId": 4,
                "detail": "item-interaction-handler-not-found",
            },
            {"frame": 1, "ageMs": 0},
            {
                "sceneId": 4,
                "board": True,
                "itemDrop": False,
                "crateSpawn": True,
                "inventory": True,
                "heartbeat": 2,
                "heartbeatAgeMs": 1,
                "heartbeatInstalled": True,
            },
        ]
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.apply_background_overrides", lambda *_, **__: None
    )
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", lambda *_, **__: next(responses))
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.time.sleep", lambda *_: None)
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.time.monotonic", lambda: 10.0)
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.arm_board_store",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("cached board should be reused")),
    )
    with caplog.at_level(logging.DEBUG):
        health = GameRuntimeAdapter(9222, "Farm").discover()

    assert health.board_available
    assert health.available
    assert health.crate_spawn_available
    assert not health.item_drop_available
    assert health.heartbeat_installed
    assert health.heartbeat_advancing
    assert health.detail == "item-interaction-handler-not-found"
    record = next(
        record for record in caplog.records if record.message.startswith("Runtime discovery")
    )
    assert record.levelno == logging.DEBUG
    timing_events = {
        record.fmv_event: record.fmv_context
        for record in caplog.records
        if hasattr(record, "fmv_event")
    }
    assert timing_events["runtime.cache_validation"]["elapsed_seconds"] == 0.0
    assert timing_events["runtime.handler_refresh"]["elapsed_seconds"] == 0.0


def test_drop_uses_confirmed_gesture_pipeline(monkeypatch) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    result = GameRuntimeAdapter(9222, "Farm").submit_item_drop((-9, 400), (88, -31))

    assert result.status is ActionStatus.SUBMITTED
    assert "handler._onGesturePick" in expression
    assert "handler._onGestureDrag" in expression
    assert "handler._onGestureDrop" in expression
    assert "interaction.onGesturePick" in expression
    assert "handler._onGestureDrag({" in expression
    assert "handler._onGestureDrop({" in expression
    assert "await nextFrame()" in expression
    assert "interaction.onGestureDrag" not in expression
    assert "interaction.onGestureDrop" not in expression
    assert "dragType: interaction._dragType" in expression
    assert "projection.getWorldPosition" in expression
    assert "nextColumn.x + nextRow.x - 2 * origin.x" in expression
    assert "destination-projection-mismatch" in expression
    assert expression.index("await nextFrame()") < expression.index(
        "const destinationPosition = screenPosition(destination)"
    )
    assert "handler._getGestureTargetData = ()" in expression
    assert "const pickedObject = source._content" in expression
    assert "handler._currentObject !== pickedObject" in expression
    assert "restorePickedObject" in expression
    assert "delete handler._getGestureTargetData" in expression
    assert "submitItemDrop" not in expression


def test_interaction_uses_internal_click_pipeline_without_screen_coordinates(monkeypatch) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    result = GameRuntimeAdapter(9222, "Farm").submit_board_interaction(
        (69, 67), InteractionTargetKind.IMMEDIATE, "milk", 3832
    )

    assert result.status is ActionStatus.SUBMITTED
    assert "handler._simulateClick(content)" in expression
    assert "collectable" in expression
    assert "'immediate': () => content.hasBehavior?.('collectable')" in expression
    assert "hasBehavior?.('ingredient')" not in expression
    assert "hasBehavior?.('cooldown')" in expression
    assert "hasBehavior?.('cooldownPreview')" not in expression
    assert "3832" in expression
    assert "getWorldToScreenPosition" not in expression


def test_interaction_returns_structured_invalid_target(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_, **__: {
            "status": "invalid-target",
            "detail": "producer-state-invalid",
        },
    )

    result = GameRuntimeAdapter(9222, "Farm").submit_board_interaction(
        (1, 2), InteractionTargetKind.PRODUCER, "cow_4", 9
    )

    assert result.status is ActionStatus.INVALID_TARGET
    assert result.detail == "producer-state-invalid"


def test_interaction_submission_defines_minimum_contract_for_every_kind(monkeypatch) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    GameRuntimeAdapter(9222, "Farm").submit_board_interaction(
        (1, 2), InteractionTargetKind.IMMEDIATE, "milk", 9
    )

    submission_kinds = set(InteractionTargetKind) - {InteractionTargetKind.REMOVE}
    for kind in submission_kinds:
        assert f"'{kind.value}': () =>" in expression
    assert "detail: `${expectedKind}-state-invalid`" in expression
    assert "detail: 'unsupported-interaction-kind'" in expression


def test_like_claim_uses_live_billboard_handler_and_unclaimed_count() -> None:
    from farm_merge_valet.cdp.scripts import _DISCOVER_EXPRESSION, _interaction_expression

    expression = _interaction_expression(
        (3, 4), InteractionTargetKind.LIKE_REWARD, "likes_billboard", 81, 7
    )

    assert "window.__fmvLikesHandler = likesHandler" in _DISCOVER_EXPRESSION
    assert "content.hasBehavior?.('likesBillboard')" in expression
    assert "likesHandler?._popoutMap?.has?.(content)" in expression
    assert "farmLike.unclaimedLikes > 0" in expression
    assert "window.__fmvLikesHandler._claimLikes(content)" in expression


def test_reward_interaction_uses_claim_callback_without_opening_popout(monkeypatch) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    result = GameRuntimeAdapter(9222, "Farm").submit_board_interaction(
        (66, 62), InteractionTargetKind.REWARD, "gem_1", 730
    )

    assert result.status is ActionStatus.SUBMITTED
    assert "content.hasBehavior?.('currency')" in expression
    assert "content.getBehavior?.('collectable')?.reward" in expression
    assert "rewardHandler._collectReward(content)" in expression
    assert "rewardHandler._collectObject(content)" not in expression
    assert "showPopout" not in expression


def test_obstacle_clear_uses_resource_gate_payment_handler(monkeypatch) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    result = GameRuntimeAdapter(9222, "Farm").submit_board_interaction(
        (61, 58), InteractionTargetKind.CLEAR, "rock_medium", 1681
    )

    assert result.status is ActionStatus.SUBMITTED
    assert "content.hasBehavior?.('mapSource')" in expression
    assert "content.hasBehavior?.('resourceGate')" in expression
    assert "typeof handler?._getTotalCost === 'function'" in expression
    assert "typeof handler?._getEventCost !== 'function'" in expression
    assert "gate?._data?.cost" in expression
    assert "energy.amount < energyCost" in expression
    assert "gameWorkers.hasEnoughWorkers(requiredWorkers)" in expression
    assert "insufficient-workers" in expression
    assert "await obstacleHandler._attemptPayment(content, position, gate)" in expression
    assert "clear-did-not-start" not in expression
    assert "showPopout" not in expression


def test_obstacle_loot_accepts_lootable_target_without_gate_markers(monkeypatch) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    result = GameRuntimeAdapter(9222, "Farm").submit_board_interaction(
        (61, 60), InteractionTargetKind.OBSTACLE_LOOT, "rock_medium", 688
    )

    assert result.status is ActionStatus.SUBMITTED
    assert "resourceGatePaid" not in expression
    assert "content.hasBehavior?.('lootable')" in expression
    assert "content.getBehavior('lootable').loot.length > 0" in expression
    assert "handler._simulateClick(content)" in expression


def test_upgrade_card_uses_authoritative_progress_and_game_service(monkeypatch) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    result = GameRuntimeAdapter(9222, "Farm").submit_board_interaction(
        (66, 58), InteractionTargetKind.UPGRADE, "upgrade_card_1", 1167
    )

    assert result.status is ActionStatus.SUBMITTED
    assert "content.hasBehavior?.('upgradeCard')" in expression
    assert "upgradeHandler._model.getItemTier(target)" in expression
    assert "appliedTier >= cardTier" in expression
    assert "upgrade-tier-already-applied" in expression
    assert "upgradeHandler._cellWithCard = cell" in expression
    assert "upgradeHandler.upgradeItemGrade(target, cardTier)" in expression


def test_reward_container_validates_full_space_and_requirements_before_opening(
    monkeypatch,
) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    result = GameRuntimeAdapter(9222, "Farm").submit_board_interaction(
        (60, 59), InteractionTargetKind.REWARD_CONTAINER, "reward_crate_bronze", 1232
    )

    assert result.status is ActionStatus.SUBMITTED
    assert "content.hasBehavior?.('crateReward')" in expression
    assert "empty < reward.rewards.length" in expression
    assert "services.gridFilter.hasEnoughItems(requirements)" in expression
    assert "reward-requirements-changed" in expression
    assert "object.removeBehaviorByType('mergeable')" in expression
    assert "rewardContainerHandler._openCrate(content, costObjects)" in expression


def test_removal_uses_game_shovel_callback_without_confirmation_popup(monkeypatch) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    result = GameRuntimeAdapter(9222, "Farm").submit_item_removal((8, 13), "rock_2", 144, 2)

    assert result.status is ActionStatus.SUBMITTED
    assert "content.hasBehavior?.('shovelable')" in expression
    assert "const previousContentToRemove = handler._contentToRemove" in expression
    assert "handler._contentToRemove = content" in expression
    assert "handler._onContentRemove()" in expression
    assert "handler._contentToRemove = previousContentToRemove" in expression
    assert "handler._contentToRemove ||" not in expression
    assert "rock_2" in expression
    assert "144" in expression
    assert "const minimumRemaining = 2" in expression
    assert "matchingCount <= minimumRemaining" in expression
    assert "candidateContent?.getBlueprintID?.()" in expression
    assert "showPopup" not in expression
    assert "shovel_confirmation" not in expression


def test_shop_orders_are_read_with_inventory_and_timer_state(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_args, **_kwargs: [
            {
                "shopID": "market",
                "recipeID": "recipe_flour",
                "state": "producing",
                "durationSeconds": 60,
                "remainingSeconds": 42.5,
                "ingredients": [{"itemID": "wheat", "required": 3, "available": 7}],
                "rewardIDs": ["coin_1"],
            }
        ],
    )

    orders = GameRuntimeAdapter(9222, "Farm").read_shop_orders()

    assert orders is not None
    assert len(orders) == 1
    assert orders[0].state is ShopOrderState.PRODUCING
    assert orders[0].remaining_seconds == 42.5
    assert orders[0].ingredients[0].available == 7


def test_shop_start_uses_public_order_handler_and_exact_current_order(monkeypatch) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    result = GameRuntimeAdapter(9222, "Farm").start_shop_order("market", "recipe_flour")

    assert result.status is ActionStatus.SUBMITTED
    assert "orders.startOrder(shopID)" in expression
    assert "order.recipe !== recipeID" in expression
    assert "orders._canAffordOrder?.(order)" in expression
    assert "camera" not in expression.lower()


def test_shop_claim_uses_reward_signal_without_camera_pan(monkeypatch) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    result = GameRuntimeAdapter(9222, "Farm").claim_shop_order("bakery", "recipe_bread")

    assert result.status is ActionStatus.SUBMITTED
    assert "orders.onOrderRewarded.fire(order)" in expression
    assert "const rootServices = orders._recipes?._services" in expression
    assert "rootServices.discovery.onOrderRewarded.fire(order)" in expression
    assert "rootServices.actions.emit(`order.rewarded.${shopID}.${recipeID}`)" in expression
    assert "shop-progression-handler-not-found" in expression
    assert "services.actions" not in expression
    assert "_spawnOrderReward" in expression
    assert "emptyCount < rewards.length" in expression
    assert "rewardOrder(" not in expression
    assert "camera" not in expression.lower()


def test_discovery_rearms_a_stale_cached_board(monkeypatch) -> None:
    responses = iter(
        [
            False,
            False,
            True,
            False,
            "page-load",
            True,
            {"status": "found", "sceneId": 5, "detail": None},
            {"frame": 20, "ageMs": 0},
            {
                "sceneId": 5,
                "board": True,
                "itemDrop": True,
                "crateSpawn": True,
                "inventory": True,
                "heartbeat": 21,
                "heartbeatAgeMs": 1,
                "heartbeatInstalled": True,
            },
        ]
    )
    armed = []
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.apply_background_overrides", lambda *_, **__: None
    )
    monkeypatch.setattr("farm_merge_valet.cdp.runtime._RUNTIME_BOOTSTRAP_SETTLE_SECONDS", 0)
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", lambda *_, **__: next(responses))
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.time.sleep", lambda *_: None)
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.arm_board_store",
        lambda *_, **__: armed.append(True) or "found (100/120 cells with content)",
    )

    health = GameRuntimeAdapter(9222, "Farm").discover()

    assert armed == [True]
    assert health.available


def test_discovery_defers_heap_recovery_until_runtime_bootstrap_settles(monkeypatch) -> None:
    responses = iter(
        [
            False,
            False,
            True,
            {"frame": 1, "ageMs": 0},
            {
                "sceneId": None,
                "board": False,
                "heartbeat": 2,
                "heartbeatAgeMs": 1,
                "heartbeatInstalled": True,
            },
        ]
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.apply_background_overrides", lambda *_, **__: None
    )
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", lambda *_, **__: next(responses))
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.time.sleep", lambda *_: None)
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.arm_board_store",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("heap scan started too early")),
    )

    health = GameRuntimeAdapter(9222, "Farm").discover()

    assert not health.available
    assert health.detail == "game-runtime-initializing"


def test_failed_board_recovery_preserves_the_specific_status(monkeypatch) -> None:
    responses = iter(
        [
            False,
            False,
            True,
            False,
            "page-load",
            True,
            {"frame": 20, "ageMs": 0},
            {
                "sceneId": None,
                "board": False,
                "heartbeat": 21,
                "heartbeatAgeMs": 1,
                "heartbeatInstalled": True,
            },
        ]
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.apply_background_overrides", lambda *_, **__: None
    )
    monkeypatch.setattr("farm_merge_valet.cdp.runtime._RUNTIME_BOOTSTRAP_SETTLE_SECONDS", 0)
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", lambda *_, **__: next(responses))
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.time.sleep", lambda *_: None)
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.arm_board_store", lambda *_, **__: "not-found"
    )

    health = GameRuntimeAdapter(9555, "Visit transition recovery").discover()

    assert health.detail == "not-found"


def test_concurrent_heap_recovery_is_single_flight_per_target(monkeypatch) -> None:
    recovered = Event()
    scan_started = Event()
    release_scan = Event()
    scans: list[int] = []

    monkeypatch.setattr(
        GameRuntimeAdapter,
        "_evaluate",
        lambda _self, expression, **_kwargs: (
            True if "requestAnimationFrame" in expression else recovered.is_set()
        ),
    )

    def arm(*_args, **_kwargs):
        scans.append(1)
        scan_started.set()
        assert release_scan.wait(1)
        recovered.set()
        return "found (100/120 cells with content)"

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.arm_board_store", arm)
    adapters = (
        GameRuntimeAdapter(9333, "Single flight"),
        GameRuntimeAdapter(9333, "Single flight"),
    )
    results: list[bool] = []
    first = Thread(target=lambda: results.append(adapters[0]._recover_board_from_heap()))
    second = Thread(target=lambda: results.append(adapters[1]._recover_board_from_heap()))

    first.start()
    assert scan_started.wait(1)
    second.start()
    release_scan.set()
    first.join(1)
    second.join(1)

    assert scans == [1]
    assert results == [True, True]


def test_failed_heap_recovery_uses_cooldown(monkeypatch) -> None:
    scans: list[int] = []
    adapter = GameRuntimeAdapter(9444, "Recovery cooldown")
    monkeypatch.setattr(
        adapter,
        "_evaluate",
        lambda expression, **_kwargs: "requestAnimationFrame" in expression,
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.arm_board_store",
        lambda *_args, **_kwargs: scans.append(1) or "cells-map-not-found",
    )

    assert not adapter._recover_board_from_heap()
    assert not adapter._recover_board_from_heap()
    assert scans == [1]


def test_heap_recovery_cooldown_restarts_for_a_reloaded_page(monkeypatch) -> None:
    scans: list[str] = []
    page_load_id = "first-load"
    adapter = GameRuntimeAdapter(9446, "Recovery reload cooldown")

    def evaluate(expression, **_kwargs):
        if "__fmvPageLoadId" in expression:
            return page_load_id
        return "requestAnimationFrame" in expression

    monkeypatch.setattr(adapter, "_evaluate", evaluate)
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.arm_board_store",
        lambda *_args, **_kwargs: scans.append(page_load_id) or "cells-map-not-found",
    )

    assert not adapter._recover_board_from_heap()
    assert not adapter._recover_board_from_heap()
    page_load_id = "second-load"
    assert not adapter._recover_board_from_heap()
    assert scans == ["first-load", "second-load"]


def test_timed_out_heap_recovery_uses_cooldown(monkeypatch) -> None:
    scans: list[int] = []
    adapter = GameRuntimeAdapter(9445, "Recovery timeout cooldown")
    monkeypatch.setattr(
        adapter,
        "_evaluate",
        lambda expression, **_kwargs: "requestAnimationFrame" in expression,
    )

    def time_out(*_args, **_kwargs):
        scans.append(1)
        raise CdpTimeoutError("CDP Runtime.queryObjects timed out after 30s.")

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.arm_board_store", time_out)

    assert not adapter._recover_board_from_heap()
    assert not adapter._recover_board_from_heap()
    assert scans == [1]
    assert adapter._discovery_detail == "board-recovery-cooldown"


def test_cdp_adapter_satisfies_runtime_protocol() -> None:
    assert isinstance(GameRuntimeAdapter(9222), GameRuntime)


def test_observation_runtime_rejects_actions_without_evaluation(monkeypatch) -> None:
    adapter = GameRuntimeAdapter(9222, "CrazyGames", observation_only=True)

    def fail(*_args, **_kwargs):
        raise AssertionError("observation-only runtime evaluated an action")

    monkeypatch.setattr(adapter, "_evaluate", fail)

    drop = adapter.submit_item_drop((0, 0), (1, 0))
    overlay = adapter.dismiss_transient_overlay()
    crates = adapter.spawn_supply_crates(1)

    assert drop == ActionResult(ActionStatus.REJECTED, "observation-only-runtime")
    assert overlay == ActionResult(ActionStatus.REJECTED, "observation-only-runtime")
    assert crates.status is ActionStatus.REJECTED
    assert crates.spawned == 0
    assert crates.detail == "observation-only-runtime"


def test_observation_runtime_maintains_portal_session_on_bounded_interval(monkeypatch) -> None:
    adapter = GameRuntimeAdapter(9222, "Pogo", observation_only=True)
    calls = []
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.maintain_portal_session",
        lambda port, title: calls.append((port, title)) or None,
    )
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.time.monotonic", lambda: 100.0)

    adapter._maintain_portal_session()
    adapter._maintain_portal_session()

    assert calls == [(9222, "Pogo")]


def test_atomic_snapshot_reads_requested_state_once_without_retry(monkeypatch) -> None:
    calls = []

    def evaluate(_port, expression, _title, **kwargs):
        calls.append((expression, kwargs))
        return {
            "health": {
                "sceneId": 7,
                "board": True,
                "itemDrop": True,
                "crateSpawn": True,
                "inventory": True,
                "heartbeat": 10,
                "heartbeatAgeMs": 1,
                "heartbeatInstalled": True,
            },
            "cells": [{"column": 1, "row": 2, "hasContent": True, "blueprintID": "wheat_1"}],
            "energy": None,
            "workers": None,
            "storageBubbles": None,
            "shopOrders": None,
            "upgradeProgress": [
                {
                    "targetID": "milk",
                    "producerBlueprintID": "cow_4",
                    "appliedTier": 2,
                }
            ],
            "rendererDurationMs": 2.5,
            "cellCount": 1,
            "occupiedCellCount": 1,
        }

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", evaluate)
    adapter = GameRuntimeAdapter(9222, "Farm")
    adapter._scene_id = 7
    adapter._last_heartbeat = 9

    snapshot = adapter.read_snapshot(SnapshotOptions(False, False, False))

    assert snapshot is not None
    assert snapshot.health.heartbeat_advancing
    assert snapshot.cells is not None
    assert snapshot.cells[(1, 2)].blueprint_id == "wheat_1"
    assert snapshot.metrics is not None
    assert snapshot.metrics.renderer_duration_ms == 2.5
    assert snapshot.upgrade_progress is not None
    assert snapshot.upgrade_progress.target("milk") is not None
    assert snapshot.upgrade_progress.target("milk").applied_tier == 2
    assert len(calls) == 1
    assert calls[0][1]["retry"] is False
    assert "const energy = false" in calls[0][0]
    assert calls[0][0].index("const farmVisit =") < calls[0][0].index("const health =")


def test_event_state_parses_structured_runtime_data() -> None:
    state = GameRuntimeAdapter._parse_event(
        {
            "key": "jungle",
            "displayName": "Jungle",
            "active": True,
            "supported": True,
            "current": True,
            "introductionOpen": True,
            "canEnter": False,
            "canReturn": True,
            "energy": 150,
            "canExplore": True,
            "explorationAreaID": "A1",
            "explorationCellCount": 50,
            "explorationRequiredLevel": 2,
            "explorationCurrentLevel": 2,
            "detail": "event-map",
        }
    )

    assert state is not None
    assert state.key == "jungle"
    assert state.display_name == "Jungle"
    assert state.active
    assert state.supported
    assert state.current
    assert state.introduction_open
    assert not state.can_enter
    assert state.can_return
    assert state.energy == 150
    assert state.can_explore
    assert state.exploration_area_id == "A1"
    assert state.exploration_cell_count == 50
    assert state.exploration_required_level == 2
    assert state.exploration_current_level == 2
    assert state.detail == "event-map"


def test_event_rewards_parse_free_and_paid_tracks() -> None:
    rewards = GameRuntimeAdapter._parse_event_rewards(
        [
            {
                "eventKey": "jungle",
                "eventType": "time-limited-event",
                "track": "free",
                "level": 2,
                "rewardKey": "tool_3",
                "rewardAmount": 2,
            },
            {
                "eventKey": "seasonal_event_pass_cinema",
                "eventType": "seasonal-event-pass",
                "track": "vip",
                "level": 1,
                "rewardKey": "energy",
                "rewardAmount": 100,
            },
        ]
    )

    assert rewards == (
        EventRewardState("jungle", "time-limited-event", "free", 2, "tool_3", 2),
        EventRewardState(
            "seasonal_event_pass_cinema",
            "seasonal-event-pass",
            "vip",
            1,
            "energy",
            100,
        ),
    )


def test_event_actions_use_guarded_native_handlers(monkeypatch) -> None:
    expressions: list[str] = []

    def evaluate_expression(_port, expression, _title, **_kwargs):
        expressions.append(expression)
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", evaluate_expression)
    adapter = GameRuntimeAdapter(9222, "Farm")
    adapter._scene_id = 4

    assert adapter.dismiss_event_introduction("jungle").submitted
    assert adapter.enter_event("jungle").submitted
    assert adapter._scene_refresh_required
    assert adapter._scene_refresh_origin_id == 4
    assert adapter.explore_event("jungle", "A1", 2).submitted
    assert adapter.return_from_event("jungle").submitted
    assert "popup.close()" in expressions[0]
    assert "eventService._goToEventMap.bind(eventService)" in expressions[1]
    assert "eventService.goToEventMap.bind(eventService)" in expressions[1]
    assert "service.unlockArea(area)" in expressions[2]
    assert '"areaID": "A1"' in expressions[2]
    assert '"requiredLevel": 2' in expressions[2]
    assert "transition.returnFromEventMap()" in expressions[3]
    assert "EventPassPopup" not in expressions[3]
    assert all('"eventKey": "jungle"' in expression for expression in expressions)


def test_event_transition_refreshes_scene_before_next_snapshot(monkeypatch) -> None:
    discoveries = 0

    def discover():
        nonlocal discoveries
        discoveries += 1
        adapter._scene_id = 5
        return RuntimeHealth(True, 5, True, True, True, True, 1, 0.0, True)

    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_args, **_kwargs: {
            "health": {
                "sceneId": 5,
                "board": True,
                "itemDrop": True,
                "crateSpawn": True,
                "inventory": True,
                "heartbeat": 2,
                "heartbeatAgeMs": 0,
            },
            "cellCount": 0,
            "occupiedCellCount": 0,
        },
    )
    adapter = GameRuntimeAdapter(9222, "Farm")
    adapter._scene_id = 4
    adapter._scene_refresh_origin_id = 4
    adapter._scene_refresh_required = True
    monkeypatch.setattr(adapter, "discover", discover)

    assert adapter.read_snapshot(SnapshotOptions(False, False, False)) is not None
    assert discoveries == 1
    assert not adapter._scene_refresh_required
    assert adapter._scene_refresh_origin_id is None


def test_event_reward_claim_uses_guarded_native_handler(monkeypatch) -> None:
    expressions: list[str] = []

    def evaluate_expression(_port, expression, _title, **_kwargs):
        expressions.append(expression)
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", evaluate_expression)
    adapter = GameRuntimeAdapter(9222, "Farm")
    reward = EventRewardState(
        "seasonal_event_pass_cinema",
        "seasonal-event-pass",
        "free",
        3,
        "milk",
        3,
    )

    assert adapter.claim_event_reward(reward).submitted
    assert "pass.requestRewardClaim(expected.track, expected.level)" in expressions[0]
    assert "event._grantAndConfirmReward" in expressions[0]
    assert '"rewardKey": "milk"' in expressions[0]


def test_farm_visit_keeps_required_destination_popup_but_returns_directly(monkeypatch) -> None:
    expressions: list[str] = []

    def evaluate_expression(_port, expression, _title, **_kwargs):
        expressions.append(expression)
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", evaluate_expression)
    adapter = GameRuntimeAdapter(9222, "Farm")

    assert adapter.open_farm_visit().submitted
    assert adapter.start_farm_visit().submitted
    assert adapter.return_from_farm_visit().submitted
    assert "handler._openTrainstationPopup()" in expressions[0]
    assert "popup._chosenPlayerDestination" in expressions[1]
    assert "goToFriendsFarm" not in expressions[1]
    assert "transition.goToOwnFarm()" in expressions[2]
    assert "returnSignal.fire()" in expressions[2]
    assert "_returnButtonClicked()" not in expressions[2]


def test_discovery_anchors_services_to_recovered_board() -> None:
    assert "candidate?.mapGrid?._cells === board" in _DISCOVER_EXPRESSION
    assert "Boolean(rootServices && farmVisitTransition &&" in _DISCOVER_EXPRESSION


def test_farm_visit_health_uses_direct_return_transition() -> None:
    assert "Boolean(sharedServices && farmVisitTransition &&" in _HEALTH_EXPRESSION
    assert "farmVisitTransition._services === sharedServices" in _HEALTH_EXPRESSION
    assert "typeof farmVisitTransition.goToOwnFarm === 'function'" in _HEALTH_EXPRESSION
    assert "currentVisitorReturnSignal" in _HEALTH_EXPRESSION
    assert "returnButtonClickedEvent === visitorReturnSignal" in _HEALTH_EXPRESSION
    assert "__fmvVisitorReturnHud" not in _HEALTH_EXPRESSION
