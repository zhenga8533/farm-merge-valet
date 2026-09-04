from farm_merge_valet.automation.action_control import ActionCoordinator, OperationKind


def test_action_coordinator_enforces_one_global_pending_operation() -> None:
    actions = ActionCoordinator()

    assert actions.begin(OperationKind.INTERACTION, ("first",), 0.0)
    assert not actions.begin(OperationKind.SHOP, ("second",), 0.0)

    actions.complete(OperationKind.INTERACTION, ("first",))
    assert actions.begin(OperationKind.SHOP, ("second",), 0.0)


def test_retry_cooldown_is_target_specific_and_bounded() -> None:
    actions = ActionCoordinator()
    failed_key = ("failed",)

    assert actions.begin(OperationKind.INTERACTION, failed_key, 0.0)
    actions.fail(OperationKind.INTERACTION, failed_key, 0.0, base_delay=10.0)

    assert not actions.available(OperationKind.INTERACTION, failed_key, 9.0)
    assert actions.available(OperationKind.INTERACTION, ("other",), 9.0)

    for now in (10.0, 30.0, 70.0):
        assert actions.begin(OperationKind.INTERACTION, failed_key, now)
        actions.fail(OperationKind.INTERACTION, failed_key, now, base_delay=10.0)

    assert actions.retry_at(OperationKind.INTERACTION, failed_key) == 130.0


def test_success_clears_target_failure_history() -> None:
    actions = ActionCoordinator()
    key = ("target",)

    assert actions.begin(OperationKind.MERGE, key, 0.0)
    actions.fail(OperationKind.MERGE, key, 0.0, base_delay=10.0)
    assert actions.begin(OperationKind.MERGE, key, 10.0)
    actions.complete(OperationKind.MERGE, key)

    assert actions.retry_at(OperationKind.MERGE, key) == 0.0
