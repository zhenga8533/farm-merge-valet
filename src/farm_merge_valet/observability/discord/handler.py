"""Asynchronous Discord notifications and periodic activity summaries."""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock, Thread
from typing import Literal

import httpx

from farm_merge_valet.config import user_data_root
from farm_merge_valet.observability.discord.charts import ActivityChart, DiscordAttachment
from farm_merge_valet.observability.discord.status import DiscordStatusMixin, StatusState
from farm_merge_valet.observability.discord.webhook import DiscordWebhookTransportMixin
from farm_merge_valet.observability.logging import (
    FMV_CONTEXT_ATTRIBUTE,
    FMV_EVENT_ATTRIBUTE,
    log_event,
    logging_sink,
)

logger = logging.getLogger(__name__)

_QUEUE_CAPACITY = 256
_DELIVERY_TIMEOUT_SECONDS = 10.0
_STOP = object()
_DEFAULT_STATUS_STATE_PATH = user_data_root() / "state" / "discord-status.json"

_IMMEDIATE_STATUS_EVENTS = frozenset(
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
) | {
    "bot.idle",
    "bot.resume_cancelled",
    "bot.started_paused",
    "runtime.partially_ready",
    "runtime.unavailable",
}


@dataclass(frozen=True)
class _PostMessage:
    payload: dict[str, object]
    attachments: tuple[DiscordAttachment, ...] = ()


@dataclass(frozen=True)
class _RefreshStatus:
    pass


class DiscordWebhookHandler(DiscordStatusMixin, DiscordWebhookTransportMixin, logging.Handler):
    """Route important events immediately and aggregate routine activity."""

    def __init__(
        self,
        url: str,
        summary_interval: float,
        status_interval: float = 60.0,
        *,
        status_state_path: Path = _DEFAULT_STATUS_STATE_PATH,
        notification_profile: Literal["minimal", "balanced", "detailed"] = "balanced",
        include_charts: bool = True,
        screenshot_provider: Callable[[], bytes | None] | None = None,
    ) -> None:
        if not url.startswith("https://"):
            raise ValueError("Discord webhook URL must use HTTPS.")
        super().__init__(level=logging.DEBUG)
        self._url = url
        self._summary_interval = summary_interval
        self._status_interval = status_interval
        self._status_state_path = status_state_path
        self._notification_profile = notification_profile
        self._include_charts = include_charts
        self._screenshot_provider = screenshot_provider
        self._activity_chart = ActivityChart(summary_interval)
        self._webhook_fingerprint = hashlib.sha256(url.encode()).hexdigest()[:16]
        self._status_message_id = self._load_status_message_id()
        self._queue: Queue[_PostMessage | _RefreshStatus | object] = Queue(maxsize=_QUEUE_CAPACITY)
        self._metrics: Counter[str] = Counter()
        self._metrics_lock = Lock()
        self._status = StatusState()
        self._session_metrics: Counter[str] = Counter()
        self._status_lock = Lock()
        self._last_warning_sent: dict[tuple[str, str], float] = {}
        self._clock = time.monotonic
        self._started_at = self._clock()
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

    def _run(self) -> None:
        with httpx.Client(timeout=_DELIVERY_TIMEOUT_SECONDS) as client:
            self._refresh_status(client, time.monotonic())
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
                    if summary is not None:
                        self._deliver_summary(client, summary, now)
                        self._refresh_status(client, now)
                    self._next_summary_at = now + self._summary_interval
                    self._next_status_at = (
                        now + self._status_interval if self._status_interval > 0 else float("inf")
                    )
                elif now >= self._next_status_at:
                    self._refresh_status(client, now)
                    self._next_status_at = now + self._status_interval
                if item is _STOP:
                    final_summary = self._take_summary(now, final=True)
                    if final_summary is not None:
                        self._deliver_summary(client, final_summary, now)
                        self._refresh_status(client, now)
                    else:
                        self._refresh_status(client, now)
                    return
                if isinstance(item, _PostMessage):
                    if self._send(client, item.payload, item.attachments) is not None:
                        self._refresh_status(client, now)
                        self._next_status_at = (
                            now + self._status_interval
                            if self._status_interval > 0
                            else float("inf")
                        )
                elif isinstance(item, _RefreshStatus):
                    self._refresh_status(client, now)

    def shutdown(self, timeout: float = 5.0) -> None:
        try:
            self._queue.put(_STOP, timeout=timeout)
        except Full:
            return
        self._thread.join(timeout=timeout)

    def _summary_attachments(self, now: float) -> tuple[DiscordAttachment, ...]:
        attachments: list[DiscordAttachment] = []
        if self._include_charts:
            chart = self._activity_chart.render(now)
            if chart is not None:
                attachments.append(chart)
        if self._screenshot_provider is not None:
            try:
                screenshot = self._screenshot_provider()
            except (OSError, RuntimeError, ValueError) as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "webhook.screenshot_failed",
                    "Could not capture the game for the Discord summary: %s.",
                    type(exc).__name__,
                    detail=type(exc).__name__,
                )
            else:
                if screenshot is not None:
                    attachments.append(DiscordAttachment("game-view.jpg", screenshot, "image/jpeg"))
        return tuple(attachments)

    def _deliver_summary(
        self,
        client: httpx.Client,
        summary: tuple[dict[str, object], Counter[str]],
        now: float,
    ) -> bool:
        payload, metrics = summary
        attachments = self._summary_attachments(now)
        if self._send(client, payload, attachments) is None:
            self._restore_summary_metrics(metrics)
            return False
        if any(attachment.filename == "session-report.png" for attachment in attachments):
            self._activity_chart.commit()
        return True


@contextmanager
def discord_webhook_sink(
    url: str | None,
    summary_interval: float,
    status_interval: float = 60.0,
    *,
    notification_profile: Literal["minimal", "balanced", "detailed"] = "balanced",
    include_charts: bool = True,
    screenshot_provider: Callable[[], bytes | None] | None = None,
) -> Iterator[None]:
    """Attach the Discord sink only for the bot run command."""
    if url is None:
        yield
        return
    handler = DiscordWebhookHandler(
        url,
        summary_interval,
        status_interval,
        notification_profile=notification_profile,
        include_charts=include_charts,
        screenshot_provider=screenshot_provider,
    )
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
