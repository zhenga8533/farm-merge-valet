"""Asynchronous Discord notifications and periodic activity summaries."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock, Thread
from typing import Literal

import httpx

from farm_merge_valet.config import PROJECT_ROOT
from farm_merge_valet.logging_setup import (
    FMV_CONTEXT_ATTRIBUTE,
    FMV_EVENT_ATTRIBUTE,
    log_event,
    logging_sink,
)

logger = logging.getLogger(__name__)

_QUEUE_CAPACITY = 256
_DELIVERY_TIMEOUT_SECONDS = 10.0
_WARNING_REPEAT_SECONDS = 900.0
_STOP = object()
_DEFAULT_STATUS_STATE_PATH = PROJECT_ROOT / ".fmv-state" / "discord-status.json"

_INFO_COLOR = 0x3498DB
_WARNING_COLOR = 0xF39C12
_ERROR_COLOR = 0xE74C3C
_SUMMARY_COLOR = 0x2ECC71

_EVENT_TITLES = {
    "browser.ready": "Managed browser ready",
    "bot.interrupted": "Bot interrupted",
    "bot.paused": "Bot paused",
    "bot.quit_requested": "Shutdown requested",
    "bot.resume_requested": "Resume requested",
    "bot.stopped": "Bot stopped",
    "runtime.connected": "Game runtime connected",
    "runtime.ready": "Game runtime ready",
}

_LIFECYCLE_EVENTS = frozenset(
    {
        "browser.ready",
        "bot.interrupted",
        "bot.paused",
        "bot.quit_requested",
        "bot.resume_requested",
        "bot.stopped",
        "runtime.connected",
        "runtime.ready",
    }
)

_IMMEDIATE_STATUS_EVENTS = _LIFECYCLE_EVENTS | {
    "bot.idle",
    "bot.resume_cancelled",
    "bot.started_paused",
    "runtime.partially_ready",
    "runtime.unavailable",
}


@dataclass(frozen=True)
class _PostMessage:
    payload: dict[str, object]


@dataclass(frozen=True)
class _RefreshStatus:
    replace: bool = False


@dataclass
class _StatusState:
    mode: str = "Starting"
    browser: str = "Starting"
    runtime: str = "Waiting for game"
    phase: str = "Unknown"
    last_activity: str = "No successful action yet"
    remaining_crates: int | None = None


class DiscordWebhookHandler(logging.Handler):
    """Route important events immediately and aggregate routine activity."""

    def __init__(
        self,
        url: str,
        summary_interval: float,
        status_interval: float = 60.0,
        *,
        status_state_path: Path = _DEFAULT_STATUS_STATE_PATH,
    ) -> None:
        if not url.startswith("https://"):
            raise ValueError("Discord webhook URL must use HTTPS.")
        super().__init__(level=logging.DEBUG)
        self._url = url
        self._summary_interval = summary_interval
        self._status_interval = status_interval
        self._status_state_path = status_state_path
        self._webhook_fingerprint = hashlib.sha256(url.encode()).hexdigest()[:16]
        self._status_message_id = self._load_status_message_id()
        self._queue: Queue[_PostMessage | _RefreshStatus | object] = Queue(maxsize=_QUEUE_CAPACITY)
        self._metrics: Counter[str] = Counter()
        self._metrics_lock = Lock()
        self._status = _StatusState()
        self._session_metrics: Counter[str] = Counter()
        self._status_lock = Lock()
        self._last_warning_sent: dict[tuple[str, str], float] = {}
        self._started_at = time.monotonic()
        self._next_summary_at = self._started_at + summary_interval
        self._next_status_at = (
            self._started_at + status_interval if status_interval > 0 else float("inf")
        )
        self._thread = Thread(target=self._run, name="fmv-discord-webhook", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        event = getattr(record, FMV_EVENT_ATTRIBUTE, None)
        if not isinstance(event, str) or event.startswith("webhook."):
            return
        context = getattr(record, FMV_CONTEXT_ATTRIBUTE, {})
        if not isinstance(context, dict):
            context = {}
        self._record_metric(event, context, record.levelno)
        self._update_status(record, event, context)
        should_notify = self._should_notify(record, event)
        if should_notify:
            self._enqueue(_PostMessage(self._format_notification(record, event)))
        elif event in _IMMEDIATE_STATUS_EVENTS:
            self._enqueue(_RefreshStatus())

    def _load_status_message_id(self) -> str | None:
        if not self._status_state_path.exists():
            return None
        try:
            raw = json.loads(self._status_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log_event(
                logger,
                logging.WARNING,
                "webhook.status_state_read_failed",
                "Could not read Discord status state: %s.",
                type(exc).__name__,
                detail=type(exc).__name__,
            )
            return None
        if not isinstance(raw, dict) or raw.get("webhook") != self._webhook_fingerprint:
            return None
        message_id = raw.get("message_id")
        return message_id if isinstance(message_id, str) and message_id.isdigit() else None

    def _persist_status_message_id(self, message_id: str | None) -> None:
        try:
            if message_id is None:
                self._status_state_path.unlink(missing_ok=True)
                return
            self._status_state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._status_state_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"webhook": self._webhook_fingerprint, "message_id": message_id}),
                encoding="utf-8",
            )
            temporary.replace(self._status_state_path)
        except OSError as exc:
            log_event(
                logger,
                logging.WARNING,
                "webhook.status_state_write_failed",
                "Could not persist Discord status state: %s.",
                type(exc).__name__,
                detail=type(exc).__name__,
            )

    def _update_status(
        self,
        record: logging.LogRecord,
        event: str,
        context: dict[str, object],
    ) -> None:
        with self._status_lock:
            if event == "browser.ready":
                browser = context.get("browser")
                self._status.browser = f"Ready ({browser})" if browser else "Ready"
            elif event == "runtime.ready":
                scene_id = context.get("scene_id")
                self._status.runtime = f"Ready (scene {scene_id})"
                if self._status.mode not in {"Paused", "Stopping", "Stopped"}:
                    self._status.mode = "Running"
            elif event == "runtime.connected":
                self._status.runtime = "Connected; waiting for heartbeat"
                if self._status.mode not in {"Paused", "Stopping", "Stopped"}:
                    self._status.mode = "Waiting"
            elif event in {
                "runtime.board_unavailable",
                "runtime.discovery_retry",
                "runtime.partially_ready",
                "runtime.unavailable",
            }:
                self._status.runtime = "Degraded"
                if self._status.mode not in {"Paused", "Stopping", "Stopped"}:
                    self._status.mode = "Waiting"
            elif event in {"bot.paused", "bot.resume_cancelled", "bot.started_paused"}:
                self._status.mode = "Paused"
            elif event == "bot.resume_requested":
                self._status.mode = "Resuming"
            elif event == "bot.idle":
                self._status.mode = "Idle"
            elif event == "bot.quit_requested":
                self._status.mode = "Stopping"
            elif event in {"bot.interrupted", "bot.stopped"}:
                self._status.mode = "Stopped"
            elif event == "planner.phase_changed":
                phase = context.get("phase")
                if isinstance(phase, str):
                    self._status.phase = phase.replace("_", " ").title()
            elif event == "bot.waiting":
                self._status.mode = "Waiting"
                reason = context.get("reason")
                if isinstance(reason, str) and (
                    "heartbeat" in reason or "runtime" in reason or "board state" in reason
                ):
                    self._status.runtime = reason.capitalize()
            elif event.startswith("action."):
                self._status.phase = "Merge"
                if event in {"action.planned", "action.submitted"}:
                    self._status.mode = "Running"
            if event == "action.confirmed":
                effect = str(context.get("effect", "action")).title()
                item = str(context.get("item_name", "item"))
                tier = context.get("item_tier")
                self._status.last_activity = f"{effect}: {item} tier {tier}"
                self._status.mode = "Running"
            elif event.startswith("claim."):
                self._status.phase = "Claim Produce"
                if event in {"claim.planned", "claim.submitted"}:
                    self._status.mode = "Running"
            if event == "claim.confirmed":
                kind = str(context.get("claim_kind", "board claim")).replace("-", " ")
                blueprint = str(context.get("blueprint_id", "item"))
                self._status.last_activity = f"Claimed {kind}: {blueprint}"
                self._status.mode = "Running"
            elif event.startswith("crate."):
                self._status.phase = "Claim Crates"
            if event == "crate.claim_completed":
                spawned = context.get("spawned", 0)
                remaining = context.get("remaining")
                self._status.last_activity = f"Spawned {spawned} supply crates"
                self._status.remaining_crates = remaining if isinstance(remaining, int) else None
                self._status.mode = "Running"
            if record.levelno >= logging.ERROR:
                self._status.mode = "Error"

    def _record_metric(self, event: str, context: dict[str, object], level: int) -> None:
        metric_updates: Counter[str] = Counter()
        if event == "action.confirmed":
            effect = str(context.get("effect", "item_action"))
            metric_updates[f"action.{effect}"] += 1
        elif event == "claim.confirmed":
            kind = str(context.get("claim_kind", "board_claim"))
            metric_updates[f"claim.{kind}"] += 1
        elif event == "crate.claim_completed":
            spawned = context.get("spawned", 0)
            if isinstance(spawned, int):
                metric_updates["crates"] += spawned
        if logging.WARNING <= level < logging.ERROR:
            metric_updates["warnings"] += 1
        if level >= logging.ERROR:
            metric_updates["errors"] += 1
        with self._metrics_lock:
            self._metrics.update(metric_updates)
        with self._status_lock:
            self._session_metrics.update(metric_updates)

    def _should_notify(self, record: logging.LogRecord, event: str) -> bool:
        if event in _LIFECYCLE_EVENTS or record.levelno >= logging.ERROR:
            return True
        if record.levelno < logging.WARNING:
            return False
        now = time.monotonic()
        key = (event, record.getMessage())
        last_sent = self._last_warning_sent.get(key, 0.0)
        if now - last_sent < _WARNING_REPEAT_SECONDS:
            return False
        self._last_warning_sent[key] = now
        return True

    @staticmethod
    def _format_notification(record: logging.LogRecord, event: str) -> dict[str, object]:
        if record.levelno >= logging.ERROR:
            color = _ERROR_COLOR
        elif record.levelno >= logging.WARNING:
            color = _WARNING_COLOR
        else:
            color = _INFO_COLOR
        title = _EVENT_TITLES.get(event, event.replace(".", " ").replace("_", " ").title())
        return {
            "username": "Farm Merge Valet",
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": title,
                    "description": record.getMessage()[:4096],
                    "color": color,
                    "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                    "footer": {"text": f"{record.levelname} · {event}"},
                }
            ],
        }

    def _enqueue(self, command: _PostMessage | _RefreshStatus) -> None:
        try:
            self._queue.put_nowait(command)
        except Full:
            log_event(
                logger,
                logging.WARNING,
                "webhook.queue_full",
                "Discord webhook queue is full; dropping a notification.",
            )

    def _take_summary(self, now: float, *, final: bool = False) -> dict[str, object] | None:
        with self._metrics_lock:
            metrics = self._metrics
            self._metrics = Counter()
        if not metrics and final:
            return None
        elapsed = max(0.0, now - self._started_at)
        period = "Final" if final else "Hourly"
        actions = sum(metrics[f"action.{effect}"] for effect in ("move", "swap", "merge"))
        products = metrics["claim.product"]
        producers = metrics["claim.producer"] + metrics["claim.depleted-producer"]
        hours, remainder = divmod(int(elapsed), 3600)
        minutes = remainder // 60
        uptime = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
        return {
            "username": "Farm Merge Valet",
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": f"{period} activity summary",
                    "description": f"Session uptime: **{uptime}**",
                    "color": _SUMMARY_COLOR,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "fields": [
                        {
                            "name": "Item actions",
                            "value": (
                                f"**{actions} total**\n"
                                f"Merges {metrics['action.merge']} · "
                                f"Moves {metrics['action.move']} · "
                                f"Swaps {metrics['action.swap']}"
                            ),
                            "inline": False,
                        },
                        {
                            "name": "Board activity",
                            "value": (
                                f"Products {products} · Producers {producers} · "
                                f"Crates {metrics['crates']}"
                            ),
                            "inline": False,
                        },
                        {
                            "name": "Reliability",
                            "value": (
                                f"Warnings {metrics['warnings']} · Errors {metrics['errors']}"
                            ),
                            "inline": False,
                        },
                    ],
                    "footer": {"text": "farm-merge-valet.summary"},
                }
            ],
        }

    def _status_payload(self, now: float) -> dict[str, object]:
        with self._status_lock:
            status = _StatusState(**vars(self._status))
            metrics = self._session_metrics.copy()
        if status.mode == "Error":
            color = _ERROR_COLOR
        elif status.mode in {"Idle", "Paused", "Stopping", "Waiting"}:
            color = _WARNING_COLOR
        elif status.mode == "Stopped":
            color = 0x95A5A6
        elif status.mode in {"Running", "Resuming"}:
            color = _SUMMARY_COLOR
        else:
            color = _INFO_COLOR
        elapsed = max(0.0, now - self._started_at)
        hours, remainder = divmod(int(elapsed), 3600)
        minutes = remainder // 60
        uptime = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
        actions = sum(metrics[f"action.{effect}"] for effect in ("move", "swap", "merge"))
        claims = (
            metrics["claim.product"]
            + metrics["claim.producer"]
            + metrics["claim.depleted-producer"]
        )
        board_activity = f"Claims {claims} · Crates {metrics['crates']}"
        if status.remaining_crates is not None:
            board_activity += f" · Supply remaining {status.remaining_crates}"
        return {
            "username": "Farm Merge Valet",
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": f"Current status · {status.mode}",
                    "description": f"Session uptime: **{uptime}**",
                    "color": color,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "fields": [
                        {"name": "Browser", "value": status.browser, "inline": True},
                        {"name": "Runtime", "value": status.runtime, "inline": True},
                        {"name": "Phase", "value": status.phase, "inline": True},
                        {
                            "name": "Session activity",
                            "value": f"Actions {actions} · {board_activity}",
                            "inline": False,
                        },
                        {
                            "name": "Last successful activity",
                            "value": status.last_activity,
                            "inline": False,
                        },
                    ],
                    "footer": {"text": "farm-merge-valet.status"},
                }
            ],
        }

    @staticmethod
    def _log_delivery_failure(exc: httpx.HTTPError) -> None:
        detail = (
            f"HTTP {exc.response.status_code}"
            if isinstance(exc, httpx.HTTPStatusError)
            else type(exc).__name__
        )
        log_event(
            logger,
            logging.WARNING,
            "webhook.delivery_failed",
            "Discord webhook delivery failed: %s",
            detail,
            detail=detail,
        )

    def _send(self, client: httpx.Client, payload: dict[str, object]) -> httpx.Response | None:
        try:
            response = client.post(
                self._url,
                params={"wait": "true"},
                json=payload,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            self._log_delivery_failure(exc)
            return None

    def _status_message_url(self) -> str | None:
        if self._status_message_id is None:
            return None
        return f"{self._url.rstrip('/')}/messages/{self._status_message_id}"

    def _create_status(self, client: httpx.Client, now: float) -> bool:
        response = self._send(client, self._status_payload(now))
        if response is None:
            return False
        try:
            body = response.json()
        except ValueError:
            body = None
        message_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(message_id, str) or not message_id.isdigit():
            log_event(
                logger,
                logging.WARNING,
                "webhook.status_id_missing",
                "Discord did not return an ID for the status message.",
            )
            return False
        self._status_message_id = message_id
        self._persist_status_message_id(message_id)
        return True

    def _edit_status(
        self, client: httpx.Client, now: float
    ) -> Literal["updated", "missing", "failed"]:
        status_url = self._status_message_url()
        if status_url is None:
            return "missing"
        try:
            payload = self._status_payload(now)
            payload.pop("username", None)
            response = client.patch(status_url, json=payload)
            response.raise_for_status()
            return "updated"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                self._status_message_id = None
                self._persist_status_message_id(None)
                return "missing"
            self._log_delivery_failure(exc)
            return "failed"
        except httpx.HTTPError as exc:
            self._log_delivery_failure(exc)
            return "failed"

    def _delete_status(self, client: httpx.Client) -> bool:
        status_url = self._status_message_url()
        if status_url is None:
            return True
        try:
            response = client.delete(status_url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                self._log_delivery_failure(exc)
                return False
        except httpx.HTTPError as exc:
            self._log_delivery_failure(exc)
            return False
        self._status_message_id = None
        self._persist_status_message_id(None)
        return True

    def _refresh_status(self, client: httpx.Client, now: float, *, replace: bool) -> None:
        if replace and self._status_message_id is not None:
            if self._delete_status(client):
                self._create_status(client, now)
            else:
                self._edit_status(client, now)
            return
        edit_result = self._edit_status(client, now)
        if edit_result == "missing":
            self._create_status(client, now)

    def _run(self) -> None:
        with httpx.Client(timeout=_DELIVERY_TIMEOUT_SECONDS) as client:
            self._refresh_status(client, time.monotonic(), replace=False)
            while True:
                now = time.monotonic()
                timeout = max(0.0, min(self._next_summary_at, self._next_status_at) - now)
                try:
                    item = self._queue.get(timeout=timeout)
                except Empty:
                    item = None
                now = time.monotonic()
                if now >= self._next_summary_at:
                    summary = self._take_summary(now)
                    if summary is not None and self._send(client, summary) is not None:
                        self._refresh_status(client, now, replace=True)
                    self._next_summary_at = now + self._summary_interval
                    self._next_status_at = (
                        now + self._status_interval if self._status_interval > 0 else float("inf")
                    )
                elif now >= self._next_status_at:
                    self._refresh_status(client, now, replace=False)
                    self._next_status_at = now + self._status_interval
                if item is _STOP:
                    final_summary = self._take_summary(now, final=True)
                    if final_summary is not None and self._send(client, final_summary) is not None:
                        self._refresh_status(client, now, replace=True)
                    else:
                        self._refresh_status(client, now, replace=False)
                    return
                if isinstance(item, _PostMessage):
                    if self._send(client, item.payload) is not None:
                        self._refresh_status(client, now, replace=True)
                        self._next_status_at = (
                            now + self._status_interval
                            if self._status_interval > 0
                            else float("inf")
                        )
                elif isinstance(item, _RefreshStatus):
                    self._refresh_status(client, now, replace=item.replace)

    def shutdown(self, timeout: float = 5.0) -> None:
        try:
            self._queue.put(_STOP, timeout=timeout)
        except Full:
            return
        self._thread.join(timeout=timeout)


@contextmanager
def discord_webhook_sink(
    url: str | None,
    summary_interval: float,
    status_interval: float = 60.0,
) -> Iterator[None]:
    """Attach the Discord sink only for the bot run command."""
    if url is None:
        yield
        return
    handler = DiscordWebhookHandler(url, summary_interval, status_interval)
    with logging_sink(handler):
        try:
            log_event(
                logger,
                logging.INFO,
                "webhook.enabled",
                "Discord webhook notifications enabled.",
                summary_interval_seconds=summary_interval,
                status_interval_seconds=status_interval,
            )
            yield
        finally:
            handler.shutdown()
