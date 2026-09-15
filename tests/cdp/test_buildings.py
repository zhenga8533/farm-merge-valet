from farm_merge_valet.automation.runtime import BuildingRepairState, BuildingRequirement
from farm_merge_valet.cdp.buildings import (
    _READ_BUILDING_REPAIRS_EXPRESSION,
    building_repair_action_expression,
    parse_building_repairs,
    read_building_repairs,
)


def test_building_repairs_preserve_placement_status_and_requirements() -> None:
    states = parse_building_repairs(
        [
            {
                "buildingID": "bakery",
                "level": 2,
                "workshop": True,
                "placed": True,
                "active": False,
                "upgrading": False,
                "requirements": [
                    {"blueprintID": "wood_2", "amount": 4, "available": 1},
                ],
            },
        ]
    )

    assert states is not None
    assert states[0].building_id == "bakery"
    assert states[0].placed
    assert states[0].requirements[0].missing == 3


def test_invalid_building_requirements_fail_closed() -> None:
    states = parse_building_repairs(
        [
            {"buildingID": "bakery", "requirements": [{"blueprintID": "wood", "amount": -1}]},
        ]
    )

    assert states is not None
    assert states[0].requirements == ()


def test_repairable_buildings_keep_requirements_in_the_upgrading_visual_state() -> None:
    assert "const requirements = active ? []" in _READ_BUILDING_REPAIRS_EXPRESSION
    assert "active || upgrading ? []" not in _READ_BUILDING_REPAIRS_EXPRESSION


def test_building_reader_arms_board_when_cached_reference_is_missing(monkeypatch) -> None:
    reads = iter((None, []))
    armed: list[tuple[int, str | None]] = []
    monkeypatch.setattr(
        "farm_merge_valet.cdp.buildings.evaluate",
        lambda *_args: next(reads),
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.buildings.arm_board_store",
        lambda port, title: armed.append((port, title)) or "found",
    )

    assert read_building_repairs(9222, "Farm") == ()
    assert armed == [(9222, "Farm")]


def test_building_repair_action_is_bound_to_authoritative_state() -> None:
    expression = building_repair_action_expression(
        BuildingRepairState(
            "bbq",
            1,
            True,
            True,
            False,
            True,
            (BuildingRequirement("wood_7", 2, 2), BuildingRequirement("tool_6", 2, 2)),
        ),
        17,
    )

    assert "window.__fmvRuntimeSceneIdentity !== 17" in expression
    assert "buildings.getBuildingLevel" in expression
    assert "building-cost-changed" in expression
    assert "gridFilter.hasEnoughItems(cost)" in expression
    assert "candidate?._requirements" in expression
    assert "handler._onRepairPopoutPressed(content, popout)" in expression
