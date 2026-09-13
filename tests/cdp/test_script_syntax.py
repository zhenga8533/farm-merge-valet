from __future__ import annotations

import shutil
import subprocess

import pytest

from farm_merge_valet.automation.runtime import (
    InteractionTargetKind,
    TransientOverlayKind,
)
from farm_merge_valet.cdp.land_expansion import (
    _READ_LAND_EXPANSION_EXPRESSION,
    land_expansion_action_expression,
)
from farm_merge_valet.cdp.scripts import (
    _HEALTH_EXPRESSION,
    _READ_EVENT_EXPRESSION,
    _READ_EVENT_REWARDS_EXPRESSION,
    _crate_expression,
    _dismiss_overlay_expression,
    _drop_expression,
    _event_action_expression,
    _event_reward_claim_expression,
    _farm_visit_action_expression,
    _interaction_expression,
    _overlay_context_expression,
    _removal_expression,
    _shop_claim_expression,
    _shop_start_expression,
    _storage_bubble_pop_expression,
)


@pytest.mark.parametrize(
    "expression",
    (
        _HEALTH_EXPRESSION,
        _READ_EVENT_EXPRESSION,
        _READ_EVENT_REWARDS_EXPRESSION,
        _READ_LAND_EXPANSION_EXPRESSION,
        _event_action_expression("dismiss", "jungle"),
        _event_action_expression("enter", "jungle"),
        _event_action_expression("explore", "jungle", areaID="A1", requiredLevel=2),
        _event_action_expression("return", "jungle"),
        _event_reward_claim_expression(
            {
                "eventKey": "jungle",
                "eventType": "time-limited-event",
                "track": "free",
                "level": 1,
                "rewardKey": "energy",
                "rewardAmount": 50,
            }
        ),
        land_expansion_action_expression("A1", False, (("coins", 10),), 0, 7),
        _farm_visit_action_expression("open", 7),
        _storage_bubble_pop_expression(11, 7),
        _dismiss_overlay_expression(7),
        _drop_expression((0, 0), (1, 0), 7),
        _crate_expression(1, 7),
        _interaction_expression((0, 0), InteractionTargetKind.IMMEDIATE, "milk", 11, 7),
        _interaction_expression((0, 0), InteractionTargetKind.FRIEND_REWARD, "building_bbq", 11, 7),
        _removal_expression((0, 0), "rock", 11, 7),
        _shop_start_expression("bakery", "bread", 7),
        _shop_claim_expression("bakery", "bread", 7),
    ),
)
def test_generated_action_expression_is_valid_javascript(expression: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for generated JavaScript syntax checks")
    completed = subprocess.run(
        [node, "--check", "-"],
        input=expression,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_health_and_dismissal_share_overlay_contract() -> None:
    dismissal = _dismiss_overlay_expression(7)
    shared_context = _overlay_context_expression()
    assert shared_context in _HEALTH_EXPRESSION
    assert shared_context in dismissal
    for kind in TransientOverlayKind:
        if kind in {
            TransientOverlayKind.SESSION_REPLACED,
            TransientOverlayKind.ONBOARDING,
            TransientOverlayKind.UNSUPPORTED,
        }:
            continue
        assert kind.value in _HEALTH_EXPRESSION
        assert kind.value in dismissal


def test_reward_popup_is_submitted_once_per_instance() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for overlay execution checks")
    expression = _dismiss_overlay_expression(7)
    script = f"""
const board = new Map();
const mapGrid = {{}};
const rewardService = {{}};
const services = {{mapGrid, rewardService}};
const popupLayer = {{name: 'popup', children: []}};
const scene = {{children: [{{children: [popupLayer]}}]}};
global.window = {{
  __fmvBoardCells: board,
  __fmvRuntimeBoard: board,
  __fmvGameplayServices: services,
  __fmvGameplayMapScreen: scene,
  __fmvRuntimeSceneIds: new WeakMap([[mapGrid, 7]]),
}};
let closes = 0;
const popup = () => ({{visible: true, renderable: true, rewardService,
  close() {{ closes += 1; }}}});
popupLayer.children.push(popup());
const run = () => {expression};
const first = run();
const second = run();
popupLayer.children[0] = popup();
const third = run();
console.log(JSON.stringify({{first, second, third, closes}}));
"""
    completed = subprocess.run(
        [node, "-"], input=script, text=True, capture_output=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert '"status":"submitted"' in completed.stdout
    assert '"status":"busy"' in completed.stdout
    assert '"closes":2' in completed.stdout


def test_passive_popup_filter_keeps_interactive_popups_blocking() -> None:
    context = _overlay_context_expression()
    assert "popup?.eventMode === 'none'" in context
    assert "popup?.interactiveChildren === false" in context
    assert "typeof popup?.close !== 'function'" in context
    assert "child?.eventMode === 'static'" in context
    assert "child?.eventMode === 'dynamic'" in context
    assert "!passivePopup(child)" in context
