from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from farm_merge_valet.observability.statistics import StatisticsService


def test_statistics_persist_dimensions_durations_and_session_history(tmp_path) -> None:
    path = tmp_path / "statistics.sqlite3"
    service = StatisticsService(path)
    service.start_session(1000.0)
    service.record(
        "action.confirmed",
        {
            "effect": "merge",
            "planner_action": "trigger",
            "item_category": "crops",
            "item_name": "wheat",
            "item_tier": 2,
            "target_size": 5,
        },
        logging.DEBUG,
        1001.0,
    )
    service.record("bot.waiting", {"reason": "board"}, logging.DEBUG, 1003.0)
    service.end_session(1005.0)

    snapshot = service.query("session")

    merge = next(row for row in snapshot.rows if row.metric == "action.merge")
    assert merge.value == 1
    assert dict(merge.dimensions) == {
        "category": "crops",
        "item": "wheat",
        "planner": "trigger",
        "target_size": "5",
        "tier": "2",
    }
    durations = {
        dict(row.dimensions)["state"]: row.value
        for row in snapshot.rows
        if row.metric == "duration.seconds"
    }
    assert durations == {"running": 2, "starting": 1, "waiting": 2}

    reopened = StatisticsService(path)
    assert reopened.query("all").total("action.") == 1
    reopened.close()
    service.close()


def test_statistics_export_and_reset(tmp_path) -> None:
    service = StatisticsService(tmp_path / "statistics.sqlite3")
    service.start_session()
    service.record("crate.claim_completed", {"spawned": 3}, logging.INFO)

    json_path = service.export(tmp_path / "statistics.json", "session")
    csv_path = service.export(tmp_path / "statistics.csv", "session")

    assert json.loads(json_path.read_text(encoding="utf-8"))["rows"][0]["metric"] == "crates"
    assert "crates,3.0" in csv_path.read_text(encoding="utf-8")
    service.reset()
    assert service.query("session").total("crates") == 0
    service.close()


def test_statistics_track_workflows_resources_and_reliability(tmp_path) -> None:
    service = StatisticsService(tmp_path / "statistics.sqlite3")
    service.start_session(datetime.now(UTC).timestamp())
    service.record(
        "interaction.confirmed",
        {"interaction_kind": "clear", "blueprint_id": "rock", "energy_cost": 10},
        logging.DEBUG,
    )
    service.record(
        "event.reward_claimed",
        {"event_key": "jungle", "track": "free", "reward_key": "coins", "reward_amount": 5},
        logging.INFO,
    )
    service.record("runtime.recovery_started", {}, logging.WARNING)

    snapshot = service.query("session")

    assert snapshot.total("interaction.clear") == 1
    assert snapshot.total("spent.energy") == 10
    assert snapshot.total("workflow.event_reward") == 1
    assert snapshot.total("received.coins") == 5
    assert snapshot.total("reliability.recovery") == 1
    assert snapshot.total("warnings") == 1
    service.close()


def test_statistics_include_elapsed_active_state_without_persisting_it(
    tmp_path, monkeypatch
) -> None:
    service = StatisticsService(tmp_path / "statistics.sqlite3")
    monkeypatch.setattr("farm_merge_valet.observability.statistics.time.time", lambda: 1005.0)
    service.start_session(1000.0)

    snapshot = service.query("session")

    assert snapshot.total("duration.seconds") == 5
    assert service.query("session").total("duration.seconds") == 5
    service.close()
