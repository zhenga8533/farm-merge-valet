from types import SimpleNamespace

from farm_merge_valet.automation.action_control import ActionCoordinator
from farm_merge_valet.automation.phases import Phase
from farm_merge_valet.automation.runtime import ActionResult, ActionStatus, RuntimeHealth
from farm_merge_valet.automation.workflows.land_expansion import LandExpansionWorkflow
from farm_merge_valet.core.land_expansion import ExpansionRequirement, LandExpansionCandidate


def candidate(*, premium: bool = False, affordable: bool = True) -> LandExpansionCandidate:
    return LandExpansionCandidate(
        "P1" if premium else "A19",
        premium,
        11,
        (ExpansionRequirement("gems" if premium else "coins", 500),),
        affordable,
    )


class Runtime:
    def __init__(self) -> None:
        self.submitted: list[LandExpansionCandidate] = []

    def submit_land_expansion(self, expansion: LandExpansionCandidate) -> ActionResult:
        self.submitted.append(expansion)
        return ActionResult(ActionStatus.SUBMITTED)


class Bot:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            land_expansion_automation_enabled=True,
            land_expansion_max_coin_cost=500,
            land_expansion_max_gem_cost=0,
        )
        self.runtime = Runtime()
        self.actions = ActionCoordinator()
        self.now = 1.0
        self.phase = Phase.CLAIM_CRATES

    def _actions(self) -> ActionCoordinator:
        return self.actions

    def _now(self) -> float:
        return self.now

    def _ensure_capability(self, health, capability) -> bool:
        return health.supports(capability)

    def _set_phase(self, phase: Phase) -> None:
        self.phase = phase

    def _report_wait(self, _reason: str) -> None:
        pass

    def _record_action_no_progress(self, _action: str, **_context: object) -> None:
        raise AssertionError("unexpected no-progress result")


def health() -> RuntimeHealth:
    return RuntimeHealth(
        True,
        7,
        True,
        True,
        True,
        True,
        1,
        0.0,
        True,
        land_expansion_available=True,
    )


def test_workflow_submits_permitted_standard_expansion_and_verifies_it() -> None:
    bot = Bot()
    workflow = LandExpansionWorkflow()
    expansion = candidate()

    assert workflow.step(bot, health(), (expansion, candidate(premium=True)))
    assert bot.runtime.submitted == [expansion]
    assert bot.phase is Phase.LAND_EXPANSION
    assert workflow.pending is not None

    assert workflow.verify_pending(bot, health(), (candidate(premium=True),))
    assert workflow.pending is None
    assert bot.actions.active is None


def test_workflow_does_not_spend_above_or_without_currency_ceiling() -> None:
    bot = Bot()
    workflow = LandExpansionWorkflow()
    too_expensive = LandExpansionCandidate(
        "A19",
        False,
        11,
        (ExpansionRequirement("coins", 501),),
        True,
    )

    assert not workflow.step(bot, health(), (too_expensive, candidate(premium=True)))
    assert bot.runtime.submitted == []
