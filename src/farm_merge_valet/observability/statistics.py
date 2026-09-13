"""Persistent aggregate statistics for GUI, diagnostics, and notifications."""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue
from threading import Event, Lock, Thread
from typing import Any, cast

from farm_merge_valet.config.paths import user_data_root
from farm_merge_valet.observability.logging import FMV_CONTEXT_ATTRIBUTE, FMV_EVENT_ATTRIBUTE
from farm_merge_valet.observability.metrics import MetricUpdate, metrics_for_event

_DEFAULT_PATH = user_data_root() / "state" / "statistics.sqlite3"
_RANGES = {
    "session": None,
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}


@dataclass(frozen=True)
class StatisticsRow:
    metric: str
    value: float
    dimensions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class TrendBucket:
    started_at: datetime
    activity: float
    reliability: float


@dataclass(frozen=True)
class StatisticsSnapshot:
    range_key: str
    rows: tuple[StatisticsRow, ...]
    trend: tuple[TrendBucket, ...]
    session_started_at: datetime | None = None
    range_start_at: datetime | None = None
    range_end_at: datetime | None = None

    def total(self, *prefixes: str) -> float:
        return sum(row.value for row in self.rows if row.metric.startswith(prefixes))


class StatisticsService(logging.Handler):
    """Record bounded metric aggregates and expose range-based snapshots."""

    def __init__(self, path: Path = _DEFAULT_PATH) -> None:
        super().__init__(logging.DEBUG)
        self.path = path
        self._lock = Lock()
        self._session_id: str | None = None
        self._session_started_at: float | None = None
        self._last_session_id: str | None = None
        self._last_session_started_at: float | None = None
        self._state: str | None = None
        self._state_since: float | None = None
        self._commands: Queue[tuple[str, object]] = Queue()
        self._writer_error: BaseException | None = None
        self._closed = False
        self._initialize()
        self._writer = Thread(target=self._run_writer, name="fmv-statistics", daemon=True)
        self._writer.start()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    started_at REAL NOT NULL,
                    ended_at REAL
                );
                CREATE TABLE IF NOT EXISTS metric_buckets (
                    bucket_start INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    dimensions TEXT NOT NULL,
                    value REAL NOT NULL,
                    PRIMARY KEY (bucket_start, session_id, metric, dimensions)
                );
                CREATE INDEX IF NOT EXISTS metric_buckets_session
                    ON metric_buckets(session_id, bucket_start);
                """
            )

    def _run_writer(self) -> None:
        try:
            with self._connect() as connection:
                while True:
                    command, payload = self._commands.get()
                    try:
                        if command == "stop":
                            return
                        if command == "updates":
                            connection.executemany(
                                """
                                INSERT INTO metric_buckets(
                                    bucket_start, session_id, metric, dimensions, value
                                ) VALUES (?, ?, ?, ?, ?)
                                ON CONFLICT(bucket_start, session_id, metric, dimensions)
                                DO UPDATE SET value = value + excluded.value
                                """,
                                cast(Any, payload),
                            )
                        elif command == "start":
                            connection.execute(
                                "INSERT INTO sessions(id, started_at) VALUES (?, ?)",
                                cast(Any, payload),
                            )
                        elif command == "end":
                            connection.execute(
                                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                                cast(Any, payload),
                            )
                        elif command == "reset":
                            connection.execute("DELETE FROM metric_buckets")
                            connection.execute("DELETE FROM sessions")
                            if payload is not None:
                                connection.execute(
                                    "INSERT INTO sessions(id, started_at) VALUES (?, ?)",
                                    cast(Any, payload),
                                )
                        connection.commit()
                    finally:
                        if command == "barrier":
                            assert isinstance(payload, Event)
                            payload.set()
        except BaseException as exc:
            self._writer_error = exc
            while not self._commands.empty():
                command, payload = self._commands.get_nowait()
                if command == "barrier" and isinstance(payload, Event):
                    payload.set()

    def _flush(self) -> None:
        if self._closed:
            raise OSError("Statistics service is closed.")
        barrier = Event()
        self._commands.put(("barrier", barrier))
        barrier.wait(5)
        if self._writer_error is not None:
            raise OSError(f"Statistics writer failed: {type(self._writer_error).__name__}")
        if not barrier.is_set():
            raise OSError("Statistics writer did not flush in time.")

    @property
    def session_id(self) -> str | None:
        with self._lock:
            return self._session_id

    def start_session(self, now: float | None = None) -> str:
        timestamp = now if now is not None else time.time()
        session_id = uuid.uuid4().hex
        with self._lock:
            self._session_id = session_id
            self._session_started_at = timestamp
            self._last_session_id = session_id
            self._last_session_started_at = timestamp
            self._state = "starting"
            self._state_since = timestamp
        self._commands.put(("start", (session_id, timestamp)))
        return session_id

    def end_session(self, now: float | None = None) -> None:
        timestamp = now if now is not None else time.time()
        with self._lock:
            session_id = self._session_id
            self._close_state_locked(timestamp)
            self._session_id = None
            self._session_started_at = None
            self._state = None
            self._state_since = None
        if session_id is not None:
            self._commands.put(("end", (timestamp, session_id)))

    def emit(self, record: logging.LogRecord) -> None:
        event = getattr(record, FMV_EVENT_ATTRIBUTE, None)
        if not isinstance(event, str) or event.startswith("statistics."):
            return
        context = getattr(record, FMV_CONTEXT_ATTRIBUTE, {})
        if not isinstance(context, dict):
            context = {}
        try:
            self.record(event, context, record.levelno, record.created)
        except (OSError, sqlite3.Error):
            self.handleError(record)

    def record(
        self,
        event: str,
        context: dict[str, object],
        level: int,
        timestamp: float | None = None,
    ) -> None:
        occurred_at = timestamp if timestamp is not None else time.time()
        with self._lock:
            if self._session_id is None:
                return
            self._transition_state_locked(event, occurred_at)
            self._write_updates_locked(metrics_for_event(event, context, level), occurred_at)

    def _transition_state_locked(self, event: str, timestamp: float) -> None:
        target = None
        if event in {"bot.paused", "bot.started_paused", "bot.resume_cancelled"}:
            target = "paused"
        elif event == "bot.idle":
            target = "idle"
        elif event == "bot.waiting":
            target = "waiting"
        elif event in {"bot.resume_requested", "runtime.connected"}:
            target = "starting"
        elif event in {
            "runtime.ready",
            "action.confirmed",
            "interaction.confirmed",
            "crate.claim_completed",
            "shop.order_started",
            "shop.order_claimed",
        }:
            target = "running"
        if target is None or target == self._state:
            return
        self._close_state_locked(timestamp)
        self._state = target
        self._state_since = timestamp

    def _close_state_locked(self, timestamp: float) -> None:
        if self._state is None or self._state_since is None or timestamp <= self._state_since:
            return
        self._write_updates_locked(
            (
                MetricUpdate(
                    "duration.seconds", timestamp - self._state_since, (("state", self._state),)
                ),
            ),
            timestamp,
        )
        self._state_since = timestamp

    def _write_updates_locked(self, updates: tuple[MetricUpdate, ...], timestamp: float) -> None:
        session_id = self._session_id
        if session_id is None or not updates:
            return
        bucket = int(timestamp // 3600) * 3600
        rows = []
        for update in updates:
            dimensions = json.dumps(dict(update.dimensions), sort_keys=True, separators=(",", ":"))
            rows.append((bucket, session_id, update.name, dimensions, update.value))
        self._commands.put(("updates", rows))

    def query(self, range_key: str) -> StatisticsSnapshot:
        if range_key not in _RANGES:
            raise ValueError(f"Unknown statistics range: {range_key}")
        now = time.time()
        with self._lock:
            active_session = self._session_id is not None
            session_id = self._session_id or self._last_session_id
            session_started = self._session_started_at or self._last_session_started_at
            active_state = self._state
            active_since = self._state_since
        clauses: list[str] = []
        parameters: list[object] = []
        if range_key == "session":
            if session_id is None:
                return StatisticsSnapshot(range_key, (), ())
            clauses.append("session_id = ?")
            parameters.append(session_id)
        elif duration := _RANGES[range_key]:
            clauses.append("bucket_start >= ?")
            parameters.append(int((now - duration.total_seconds()) // 3600) * 3600)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        self._flush()
        with self._connect() as connection:
            aggregate_rows = connection.execute(
                f"""
                SELECT metric, dimensions, SUM(value)
                FROM metric_buckets {where}
                GROUP BY metric, dimensions
                ORDER BY metric, dimensions
                """,
                parameters,
            ).fetchall()
            trend_rows = connection.execute(
                f"""
                SELECT bucket_start,
                    SUM(CASE WHEN metric LIKE 'action.%' OR metric LIKE 'interaction.%'
                        OR metric LIKE 'workflow.%' OR metric = 'crates' THEN value ELSE 0 END),
                    SUM(CASE WHEN metric LIKE 'reliability.%' OR metric IN ('warnings', 'errors')
                        THEN value ELSE 0 END)
                FROM metric_buckets {where}
                GROUP BY bucket_start ORDER BY bucket_start
                """,
                parameters,
            ).fetchall()
        totals = {
            (metric, tuple(sorted(json.loads(dimensions).items()))): value
            for metric, dimensions, value in aggregate_rows
        }
        if active_session and active_state is not None and active_since is not None:
            live_since = active_since
            if duration := _RANGES[range_key]:
                live_since = max(live_since, now - duration.total_seconds())
            live_duration = max(0.0, now - live_since)
            key = ("duration.seconds", (("state", active_state),))
            totals[key] = totals.get(key, 0.0) + live_duration
        rows = tuple(
            StatisticsRow(metric, value, dimensions)
            for (metric, dimensions), value in sorted(totals.items())
        )
        trend = tuple(
            TrendBucket(datetime.fromtimestamp(bucket, UTC), activity, reliability)
            for bucket, activity, reliability in trend_rows
        )
        started = (
            datetime.fromtimestamp(session_started, UTC)
            if range_key == "session" and session_started is not None
            else None
        )
        if range_key == "session":
            range_start = session_started
        elif duration := _RANGES[range_key]:
            range_start = now - duration.total_seconds()
        else:
            range_start = trend_rows[0][0] if trend_rows else now
        return StatisticsSnapshot(
            range_key,
            rows,
            trend,
            started,
            datetime.fromtimestamp(range_start, UTC) if range_start is not None else None,
            datetime.fromtimestamp(now, UTC),
        )

    def export(self, path: Path, range_key: str) -> Path:
        snapshot = self.query(range_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.casefold() == ".json":
            payload = {
                "range": range_key,
                "rows": [
                    {"metric": row.metric, "value": row.value, "dimensions": dict(row.dimensions)}
                    for row in snapshot.rows
                ],
            }
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        else:
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(("metric", "value", "dimensions"))
                for row in snapshot.rows:
                    writer.writerow((row.metric, row.value, json.dumps(dict(row.dimensions))))
        return path

    def reset(self) -> None:
        now = time.time()
        with self._lock:
            session_id = self._session_id
            self._session_started_at = now if session_id is not None else None
            self._last_session_id = session_id
            self._last_session_started_at = now if session_id is not None else None
            self._state_since = now if session_id is not None else None
        payload = (session_id, now) if session_id is not None else None
        self._commands.put(("reset", payload))
        self._flush()

    def close(self) -> None:
        if self._closed:
            return
        self.end_session()
        self._flush()
        self._commands.put(("stop", None))
        self._writer.join(timeout=5)
        if self._writer.is_alive():
            raise OSError("Statistics writer did not stop in time.")
        self._closed = True
