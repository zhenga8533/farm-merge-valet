import pytest

from farm_merge_valet.automation.scheduler import AdaptiveScheduler


def test_scheduler_enforces_floor_and_uses_latency_ewma() -> None:
    scheduler = AdaptiveScheduler()

    assert scheduler.active_delay(0.01) == 0.25
    scheduler.record_snapshot(0.2)
    assert scheduler.active_delay(0.01) == 0.8
    scheduler.record_snapshot(0.1)
    assert scheduler.latency_ewma == pytest.approx(0.175)
    assert scheduler.active_delay(0.01) == pytest.approx(0.7)


def test_scheduler_caps_adaptive_delay() -> None:
    scheduler = AdaptiveScheduler()
    scheduler.record_snapshot(10.0)

    assert scheduler.active_delay(0.25) == 5.0
