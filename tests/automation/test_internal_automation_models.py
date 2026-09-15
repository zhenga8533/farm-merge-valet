from farm_merge_valet.automation.arbiter import WorkflowArbiter
from farm_merge_valet.automation.state import AutomationState
from farm_merge_valet.automation.timing import (
    DEFAULT_ACTION_TIMING,
    FARM_VISIT_ACTION_TIMING,
    MARKETPLACE_ACTION_TIMING,
)


def test_action_timing_defaults_and_overrides_are_explicit() -> None:
    assert DEFAULT_ACTION_TIMING.settle_seconds == 3.0
    assert DEFAULT_ACTION_TIMING.stability_seconds == 0.75
    assert DEFAULT_ACTION_TIMING.maximum_pending_seconds == 8.0
    assert DEFAULT_ACTION_TIMING.retry_seconds == 10.0
    assert DEFAULT_ACTION_TIMING.failure_limit == 3
    assert MARKETPLACE_ACTION_TIMING.maximum_pending_seconds == 10.0
    assert MARKETPLACE_ACTION_TIMING.retry_seconds == 60.0
    assert FARM_VISIT_ACTION_TIMING.maximum_pending_seconds == 30.0
    assert FARM_VISIT_ACTION_TIMING.retry_seconds == 5.0


def test_snapshot_state_instances_do_not_share_mutable_data() -> None:
    first = AutomationState()
    second = AutomationState()
    first.live_cells[(1, 2)] = object()  # type: ignore[assignment]
    assert second.live_cells == {}


def test_workflow_arbiter_uses_declared_precedence() -> None:
    arbiter = WorkflowArbiter()
    assert arbiter.select({"marketplace", "interactions", "shops"}) == "interactions"
    assert arbiter.select(set()) is None
