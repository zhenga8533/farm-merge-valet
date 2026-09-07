"""Read-only summaries of persisted structured application events."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class SessionHistorySummary:
    since: datetime
    events: int
    metrics: Counter[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "since": self.since.isoformat(),
            "events": self.events,
            "metrics": dict(sorted(self.metrics.items())),
        }


def summarize_history(path: Path, since: datetime) -> SessionHistorySummary:
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    metrics: Counter[str] = Counter()
    events = 0
    paths = sorted(path.parent.glob(f"{path.name}*"), reverse=True)
    for candidate in paths:
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                payload = json.loads(line)
                timestamp = datetime.fromisoformat(payload["timestamp"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            if timestamp < since:
                continue
            event = payload.get("event")
            if not isinstance(event, str) or not event:
                continue
            events += 1
            metrics[event] += 1
            context = payload.get("context")
            if event == "crate.claim_completed" and isinstance(context, dict):
                spawned = context.get("spawned")
                if isinstance(spawned, int) and not isinstance(spawned, bool):
                    metrics["items.crates_spawned"] += spawned
    return SessionHistorySummary(since, events, metrics)
