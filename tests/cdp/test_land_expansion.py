from farm_merge_valet.automation.runtime import ActionStatus
from farm_merge_valet.cdp.land_expansion import (
    _READ_LAND_EXPANSION_EXPRESSION,
    land_expansion_action_expression,
)
from farm_merge_valet.cdp.runtime import GameRuntimeAdapter
from farm_merge_valet.core.land_expansion import ExpansionRequirement, LandExpansionCandidate


def test_live_land_expansion_state_is_parsed_strictly() -> None:
    candidates = GameRuntimeAdapter._parse_land_expansions(
        [
            {
                "areaID": "A19",
                "premium": False,
                "cellCount": 11,
                "requirements": [
                    {"key": "level", "amount": 18},
                    {"key": "coins", "amount": 3545},
                ],
                "affordable": True,
            },
            {"areaID": "bad", "premium": False, "cellCount": 0},
        ]
    )

    assert candidates is not None and len(candidates) == 1
    assert candidates[0].area_id == "A19"
    assert candidates[0].requirements[1].amount == 3545


def test_land_expansion_expressions_use_native_revalidated_handler() -> None:
    action = land_expansion_action_expression(
        "A19", False, (("level", 18), ("coins", 3545)), 7
    )

    assert "service.getNextAreaToUnlock()" in _READ_LAND_EXPANSION_EXPRESSION
    assert "service.canUnlockArea(area)" in action
    assert "service.unlockArea(area)" in action
    assert "land-expansion-cost-changed" in action
    assert '"sceneID": 7' in action


def test_runtime_submits_land_expansion_with_scene_and_exact_requirements(monkeypatch) -> None:
    expressions: list[str] = []

    def evaluate_expression(_port, expression, _title, **_kwargs):
        expressions.append(expression)
        return {"status": "submitted"}

    monkeypatch.setattr("farm_merge_valet.cdp.runtime.evaluate", evaluate_expression)
    adapter = GameRuntimeAdapter(9222, "Farm")
    adapter._scene_id = 7
    candidate = LandExpansionCandidate(
        "A19",
        False,
        11,
        (ExpansionRequirement("level", 18), ExpansionRequirement("coins", 3545)),
        True,
    )

    result = adapter.submit_land_expansion(candidate)

    assert result.status is ActionStatus.SUBMITTED
    assert '"areaID": "A19"' in expressions[0]
    assert '"amount": 3545' in expressions[0]
    assert '"sceneID": 7' in expressions[0]
