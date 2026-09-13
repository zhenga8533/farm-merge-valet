"""Read-only summaries of persisted structured application events."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from farm_merge_valet.observability.metrics import metrics_for_event


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
            context = payload.get("context")
            level_name = payload.get("level", "INFO")
            level = logging.getLevelNamesMapping().get(level_name, logging.INFO)
            updates = metrics_for_event(event, context if isinstance(context, dict) else {}, level)
            events += 1
            for update in updates:
                metrics[update.name] += int(update.value)
    return SessionHistorySummary(since, events, metrics)
