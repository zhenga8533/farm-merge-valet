from __future__ import annotations

import logging

import pytest

from farm_merge_valet.cdp import evaluation
from farm_merge_valet.cdp.transport import CdpCancelledError, CdpConnectionError


def test_background_overrides_are_attempted_once_per_target(monkeypatch, caplog) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(evaluation, "_background_override_attempts", set())
    monkeypatch.setattr(
        evaluation,
        "_target_pair",
        lambda *_args, **_kwargs: ("ws://game/unique", "ws://page/unique"),
    )

    def command(ws_url, method, _params, **_kwargs):
        calls.append((ws_url, method))
        if method == "Page.setWebLifecycleState":
            raise CdpConnectionError("unsupported")

    monkeypatch.setattr(evaluation, "_command_target", command)

    with caplog.at_level(logging.DEBUG):
        evaluation.apply_background_overrides(9222, "Farm")
        evaluation.apply_background_overrides(9222, "Farm")

    assert len(calls) == 6
    assert sum(record.fmv_event == "cdp.override_unavailable" for record in caplog.records) == 2


def test_cancelled_background_override_can_be_retried(monkeypatch) -> None:
    calls = 0
    monkeypatch.setattr(evaluation, "_background_override_attempts", set())
    monkeypatch.setattr(
        evaluation,
        "_target_pair",
        lambda *_args, **_kwargs: ("ws://game/cancelled", "ws://page/cancelled"),
    )

    def cancel(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise CdpCancelledError("cancelled")

    monkeypatch.setattr(evaluation, "_command_target", cancel)

    for _ in range(2):
        with pytest.raises(CdpCancelledError):
            evaluation.apply_background_overrides(9222, "Farm")

    assert calls == 2
