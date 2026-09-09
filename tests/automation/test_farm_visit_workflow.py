from types import SimpleNamespace

from farm_merge_valet.automation.action_control import ActionCoordinator
from farm_merge_valet.automation.bot import Bot as AutomationBot
from farm_merge_valet.automation.bot import Phase
from farm_merge_valet.automation.runtime import (
    ActionResult,
    ActionStatus,
    CrateSpawnResult,
    FarmSceneKind,
    FarmVisitState,
    LiveCellState,
    RuntimeHealth,
    RuntimeSnapshot,
    VisitorActionState,
)
from farm_merge_valet.automation.workflows.farm_visits import FarmVisitWorkflow
from farm_merge_valet.catalog.models import ItemCatalog
from farm_merge_valet.config import AppConfig


def health(scene_id: int) -> RuntimeHealth:
    return RuntimeHealth(
        True,
        scene_id,
        True,
        False,
        False,
        False,
        1,
        0.0,
        True,
        farm_visit_available=True,
    )


class Runtime:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def _submit(self, value: object) -> ActionResult:
        self.calls.append(value)
        return ActionResult(ActionStatus.SUBMITTED)

    def open_farm_visit(self) -> ActionResult:
        return self._submit("open")

    def start_farm_visit(self) -> ActionResult:
        return self._submit("start")

    def close_farm_visit(self) -> ActionResult:
        return self._submit("close")

    def submit_visitor_action(self, action: VisitorActionState) -> ActionResult:
        return self._submit(action)

    def return_from_farm_visit(self) -> ActionResult:
        return self._submit("return")


class Bot:
    def __init__(self) -> None:
        self.config = SimpleNamespace(farm_visit_automation_enabled=True)
        self.runtime = Runtime()
        self._action_control = ActionCoordinator()
        self._last_wait_reason = None
        self._idle_active = False
        self.now = 1.0

    def _actions(self) -> ActionCoordinator:
        return self._action_control

    def _now(self) -> float:
        return self.now

    def _report_wait(self, reason: str) -> None:
        self._last_wait_reason = reason

    def clear_wait_state(self) -> None:
        self._last_wait_reason = None
        self._idle_active = False

    def _record_action_no_progress(self, _action: str) -> None:
        raise AssertionError("unexpected no-progress result")


def test_farm_visit_runs_native_navigation_claim_and_return_sequence() -> None:
    bot = Bot()
    workflow = FarmVisitWorkflow()
    own = FarmVisitState(FarmSceneKind.OWN, tickets=1)

    assert workflow.step(bot, health(1), own)
    assert bot.runtime.calls == ["open"]
    assert workflow.verify_pending(
        bot,
        health(1),
        FarmVisitState(FarmSceneKind.OWN, tickets=1, panel_open=True),
    )

    panel = FarmVisitState(
        FarmSceneKind.OWN,
        tickets=1,
        destination_available=True,
        panel_open=True,
    )
    assert workflow.step(bot, health(1), panel)
    visitor_action = VisitorActionState((4, 5), "carrot_4", 9, "water_crop")
    visitor = FarmVisitState(FarmSceneKind.VISITOR, actions=(visitor_action,))
    assert workflow.verify_pending(bot, health(2), visitor)

    assert workflow.step(bot, health(2), visitor)
    assert workflow.verify_pending(bot, health(2), FarmVisitState(FarmSceneKind.VISITOR))
    assert workflow.step(bot, health(2), FarmVisitState(FarmSceneKind.VISITOR))
    assert workflow.verify_pending(bot, health(3), FarmVisitState(FarmSceneKind.OWN))
    assert bot.runtime.calls == ["open", "start", visitor_action, "return"]


def test_disabled_farm_visits_still_return_from_a_visitor_scene() -> None:
    bot = Bot()
    bot.config.farm_visit_automation_enabled = False
    workflow = FarmVisitWorkflow()

    assert not workflow.step(bot, health(1), FarmVisitState(FarmSceneKind.OWN, tickets=1))
    assert workflow.step(bot, health(2), FarmVisitState(FarmSceneKind.VISITOR))
    assert bot.runtime.calls == ["return"]


class PlanningRuntime:
    def __init__(self, tickets: int) -> None:
        self.tickets = tickets
        self.calls: list[str] = []

    def set_cancel_event(self, _cancel_event) -> None:
        pass

    def read_snapshot(self, _options) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            RuntimeHealth(
                True,
                1,
                True,
                True,
                True,
                True,
                1,
                0.0,
                True,
                farm_visit_available=True,
                farm_scene=FarmSceneKind.OWN,
            ),
            {(0, 0): LiveCellState(False, None)},
            storage_bubbles=(),
            shop_orders=(),
            marketplace_offers=(),
            farm_visit=FarmVisitState(FarmSceneKind.OWN, tickets=self.tickets),
        )

    def open_farm_visit(self) -> ActionResult:
        self.calls.append("open-visit")
        return ActionResult(ActionStatus.SUBMITTED)

    def spawn_supply_crates(self, limit: int) -> CrateSpawnResult:
        self.calls.append("open-crate")
        return CrateSpawnResult(ActionStatus.SUBMITTED, limit, 0, available_before=limit)


class CatalogProvider:
    def load(self) -> ItemCatalog:
        return ItemCatalog({})


def planning_bot(runtime: PlanningRuntime) -> AutomationBot:
    return AutomationBot(
        AppConfig(
            farm_visit_automation_enabled=True,
            item_automation_enabled=False,
            auto_pop_storage_bubbles=False,
            shop_automation_enabled=False,
            marketplace_automation_enabled=False,
        ),
        runtime,
        CatalogProvider(),
    )


def test_held_ticket_is_spent_before_a_supply_crate_is_opened() -> None:
    runtime = PlanningRuntime(tickets=1)
    bot = planning_bot(runtime)

    bot.step()

    assert runtime.calls == ["open-visit"]
    assert bot.phase is Phase.FARM_VISITS


def test_supply_crate_is_opened_when_no_ticket_is_held() -> None:
    runtime = PlanningRuntime(tickets=0)
    bot = planning_bot(runtime)

    bot.step()

    assert runtime.calls == ["open-crate"]
    assert bot.phase is Phase.CLAIM_CRATES
