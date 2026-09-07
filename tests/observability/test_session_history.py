from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from farm_merge_valet.observability.session_history import summarize_history


def test_session_history_aggregates_rotated_structured_logs(tmp_path) -> None:
    path = tmp_path / "farm-merge-valet.log"
    now = datetime.now(UTC)
    records = [
        {
            "timestamp": now.isoformat(),
            "event": "action.confirmed",
            "context": {},
        },
        {
            "timestamp": now.isoformat(),
            "event": "crate.claim_completed",
            "context": {"spawned": 3},
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    (tmp_path / "farm-merge-valet.log.1").write_text(
        json.dumps(
            {
                "timestamp": (now - timedelta(days=2)).isoformat(),
                "event": "old.event",
                "context": {},
            }
        ),
        encoding="utf-8",
    )

    summary = summarize_history(path, now - timedelta(hours=1))

    assert summary.events == 2
    assert summary.metrics["action.confirmed"] == 1
    assert summary.metrics["items.crates_spawned"] == 3
    assert "old.event" not in summary.metrics
