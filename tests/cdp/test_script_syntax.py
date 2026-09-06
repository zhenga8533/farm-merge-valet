from __future__ import annotations

import shutil
import subprocess

import pytest

from farm_merge_valet.automation.runtime import (
    InteractionTargetKind,
    TransientOverlayKind,
)
from farm_merge_valet.cdp.scripts import (
    _HEALTH_EXPRESSION,
    _crate_expression,
    _dismiss_overlay_expression,
    _drop_expression,
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
        _farm_visit_action_expression("open", 7),
        _storage_bubble_pop_expression(11, 7),
        _dismiss_overlay_expression(7),
        _drop_expression((0, 0), (1, 0), 7),
        _crate_expression(1, 7),
        _interaction_expression(
            (0, 0), InteractionTargetKind.IMMEDIATE, "milk", 11, 7
        ),
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
        if kind is TransientOverlayKind.UNSUPPORTED:
            continue
        assert kind.value in _HEALTH_EXPRESSION
        assert kind.value in dismissal
