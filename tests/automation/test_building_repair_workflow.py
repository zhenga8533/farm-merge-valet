from types import SimpleNamespace

from farm_merge_valet.automation.action_control import ActionCoordinator, OperationKind
from farm_merge_valet.automation.runtime import (
    ActionResult,
    ActionStatus,
    BuildingRepairState,
    BuildingRequirement,
    FarmSceneKind,
    RuntimeHealth,
)
from farm_merge_valet.automation.workflows.building_repairs import BuildingRepairWorkflow


def repair_state(*, active: bool = False, available: int = 2) -> BuildingRepairState:
    return BuildingRepairState(
        "bbq",
        1,
        True,
        True,
        active,
        not active,
        () if active else (BuildingRequirement("wood_7", 2, available),),
    )


def health(scene: FarmSceneKind = FarmSceneKind.OWN) -> RuntimeHealth:
    return RuntimeHealth(True, 1, True, True, True, True, 2, 0.0, True, farm_scene=scene)


class Runtime:
    def __init__(self, result: ActionResult) -> None:
        self.result = result
        self.repairs: list[BuildingRepairState] = []

    def submit_building_repair(self, state: BuildingRepairState) -> ActionResult:
        self.repairs.append(state)
        return self.result


class Bot:
    def __init__(self, result: ActionResult) -> None:
        self.runtime = Runtime(result)
        self.config = SimpleNamespace()
        self.phase = None
        self.now = 10.0
        self.actions = ActionCoordinator()
        self.waits: list[str] = []
        self.no_progress: list[str] = []

    def _now(self) -> float:
        return self.now

    def _actions(self) -> ActionCoordinator:
        return self.actions

    def _set_phase(self, phase) -> None:
        self.phase = phase

    def _report_wait(self, reason: str, **_context: object) -> None:
        self.waits.append(reason)

    def _record_action_no_progress(self, action: str, **_context: object) -> None:
        self.no_progress.append(action)


def test_repair_submits_only_when_all_resources_are_available() -> None:
    bot = Bot(ActionResult(ActionStatus.SUBMITTED))
    workflow = BuildingRepairWorkflow()

    assert not workflow.step(bot, health(), repair_state(available=1))  # type: ignore[arg-type]
    assert not workflow.step(bot, health(FarmSceneKind.EVENT), repair_state())  # type: ignore[arg-type]
    assert workflow.step(bot, health(), repair_state())  # type: ignore[arg-type]
    assert bot.runtime.repairs == [repair_state()]
    assert bot.actions.active is not None


def test_repair_completion_is_verified_from_live_building_state() -> None:
    bot = Bot(ActionResult(ActionStatus.SUBMITTED))
    workflow = BuildingRepairWorkflow()
    pending = repair_state()
    assert workflow.step(bot, health(), pending)  # type: ignore[arg-type]

    assert workflow.verify_pending(bot, health(), (repair_state(active=True),))  # type: ignore[arg-type]
    assert workflow.pending is None
    assert bot.actions.available(OperationKind.BUILDING_REPAIR, ("bbq", 1), bot.now)
