import logging
from threading import Event

from farm_merge_valet.cdp.runtime import ActionStatus, GameRuntimeAdapter


def test_runtime_health_tracks_heartbeat_advancement(monkeypatch) -> None:
    samples = iter(
        [
            {
                "sceneId": 3,
                "board": True,
                "itemDrop": True,
                "crateSpawn": True,
                "inventory": True,
                "heartbeat": 10,
                "heartbeatAgeMs": 2,
            },
            {
                "sceneId": 3,
                "board": True,
                "itemDrop": True,
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
    assert all("['_onClick'" in expression for expression in expressions)


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


def test_discovery_uses_active_crate_state_handler() -> None:
    from farm_merge_valet.cdp.runtime import _DISCOVER_EXPRESSION, _HEALTH_EXPRESSION

    assert "typeof context._onClick === 'function'" in _DISCOVER_EXPRESSION
    assert "crateHandler = context" in _DISCOVER_EXPRESSION
    assert "context === crate" in _HEALTH_EXPRESSION
    assert "cratesButton) === crate" not in _HEALTH_EXPRESSION


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
