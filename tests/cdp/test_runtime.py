import logging
from threading import Event

from farm_merge_valet.cdp.runtime import ActionStatus, GameRuntimeAdapter
from farm_merge_valet.core.board import InteractionTargetKind
from farm_merge_valet.core.shops import ShopOrderState


def test_runtime_health_tracks_heartbeat_advancement(monkeypatch) -> None:
    samples = iter(
        [
            {
                "sceneId": 3,
                "board": True,
                "itemDrop": True,
                "interaction": True,
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
                "crateSpawn": True,
                "inventory": True,
                "heartbeat": 11,
                "heartbeatAgeMs": 1,
                "itemActionBusy": True,
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


def test_crate_result_reports_partial_count(monkeypatch) -> None:
    responses = iter(
        [
            {"status": "submitted", "spawned": 2, "remaining": 5},
            {"status": "rejected", "spawned": 0, "remaining": 5},
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
    from farm_merge_valet.cdp.runtime import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "hudService?._commonEvents?.spawnCrates" in _DISCOVER_EXPRESSION
    assert "crateSubscribers.length > 0" in _DISCOVER_EXPRESSION
    assert "_commonEvents?.spawnCrates === crateSignal" in _HEALTH_EXPRESSION
    assert "subscribers(crateSignal).length > 0" in _HEALTH_EXPRESSION
    assert "inventory?.onAnimateChanges" not in _DISCOVER_EXPRESSION


def test_discovery_validates_internal_interaction_handler() -> None:
    from farm_merge_valet.cdp.runtime import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "typeof value._simulateClick === 'function'" in _DISCOVER_EXPRESSION
    assert "window.__fmvInteractionHandler" in _DISCOVER_EXPRESSION
    assert "onGestureTap" in _HEALTH_EXPRESSION


def test_discovery_validates_shovel_handler() -> None:
    from farm_merge_valet.cdp.runtime import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "typeof candidate._onContentRemove === 'function'" in _DISCOVER_EXPRESSION
    assert "window.__fmvShovelHandler" in _DISCOVER_EXPRESSION
    assert "services?.shovelService" in _HEALTH_EXPRESSION


def test_discovery_validates_shop_order_service() -> None:
    from farm_merge_valet.cdp.runtime import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "typeof orders?.getCurrentOrders === 'function'" in _DISCOVER_EXPRESSION
    assert "typeof orders?.startOrder === 'function'" in _DISCOVER_EXPRESSION
    assert "window.__fmvOrdersService" in _DISCOVER_EXPRESSION
    assert "services?.ordersService === orders" in _HEALTH_EXPRESSION


def test_cached_discovery_is_a_debug_diagnostic(monkeypatch, caplog) -> None:
    responses = iter(
        [
            True,
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
    monotonic = iter((10.0, 10.1))
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.time.monotonic", lambda: next(monotonic))
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.arm_board_store",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("cached board should be reused")),
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.arm_crate_inventory",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("cached inventory should be reused")),
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
    assert "expectedKind === 'immediate'" in expression
    assert "hasBehavior?.('ingredient')" not in expression
    assert "hasBehavior?.('cooldown')" in expression
    assert "hasBehavior?.('cooldownPreview')" not in expression
    assert "3832" in expression
    assert "getWorldToScreenPosition" not in expression


def test_interaction_returns_structured_invalid_target(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.evaluate",
        lambda *_, **__: {"status": "invalid-target"},
    )

    result = GameRuntimeAdapter(9222, "Farm").submit_board_interaction(
        (1, 2), InteractionTargetKind.PRODUCER, "cow_4", 9
    )

    assert result.status is ActionStatus.INVALID_TARGET


def test_removal_uses_game_shovel_callback_without_confirmation_popup(monkeypatch) -> None:
    expression = ""

    def capture_expression(_port, value, _title, **_kwargs):
        nonlocal expression
        expression = value
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", capture_expression)

    result = GameRuntimeAdapter(9222, "Farm").submit_item_removal((8, 13), "rock_2", 144)

    assert result.status is ActionStatus.SUBMITTED
    assert "content.hasBehavior?.('shovelable')" in expression
    assert "const previousContentToRemove = handler._contentToRemove" in expression
    assert "handler._contentToRemove = content" in expression
    assert "handler._onContentRemove()" in expression
    assert "handler._contentToRemove = previousContentToRemove" in expression
    assert "handler._contentToRemove ||" not in expression
    assert "rock_2" in expression
    assert "144" in expression
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
    assert "_spawnOrderReward" in expression
    assert "emptyCount < rewards.length" in expression
    assert "rewardOrder(" not in expression
    assert "camera" not in expression.lower()


def test_discovery_rearms_a_stale_cached_board(monkeypatch) -> None:
    responses = iter(
        [
            False,
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
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", lambda *_, **__: next(responses))
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.time.sleep", lambda *_: None)
    monkeypatch.setattr(
        "farm_merge_valet.cdp.runtime.arm_board_store", lambda *_, **__: armed.append(True)
    )

    health = GameRuntimeAdapter(9222, "Farm").discover()

    assert armed == [True]
    assert health.available
