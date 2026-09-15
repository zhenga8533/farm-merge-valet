"""Discord status reduction, metrics, and payload formatting."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock

from farm_merge_valet.observability.discord.charts import ActivityChart
from farm_merge_valet.observability.metrics import metrics_for_event

_INFO_COLOR = 0x3498DB
_WARNING_COLOR = 0xF39C12
_ERROR_COLOR = 0xE74C3C
_SUMMARY_COLOR = 0x2ECC71
_WARNING_REPEAT_SECONDS = 900.0
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
_LIFECYCLE_EVENTS = frozenset(_EVENT_TITLES)
_DETAILED_EVENTS = frozenset(
    {
        "farm_visit.return_confirmed",
        "land_expansion.confirmed",
        "marketplace.purchase_confirmed",
    }
)


def _metric_line(metrics: Counter[str], values: tuple[tuple[str, str], ...]) -> str:
    parts = [f"{label} {metrics[key]}" for label, key in values if metrics[key]]
    return " · ".join(parts) if parts else "None recorded"


@dataclass
class StatusState:
    mode: str = "Starting"
    browser: str = "Starting"
    runtime: str = "Waiting for game"
    phase: str = "Unknown"
    last_activity: str = "No successful action yet"
    remaining_crates: int | None = None
    wait_reason: str | None = None


class DiscordStatusMixin:
    _status: StatusState
    _status_lock: Lock
    _metrics: Counter[str]
    _metrics_lock: Lock
    _session_metrics: Counter[str]
    _last_warning_sent: dict[tuple[str, str], float]
    _started_at: float
    _clock: Callable[[], float]
    _notification_profile: str
    _activity_chart: ActivityChart

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
                self._status.wait_reason = reason if isinstance(reason, str) else None
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
                self._status.wait_reason = None
            elif event.startswith("interaction."):
                self._status.phase = "Interact with Tiles"
                if event in {"interaction.planned", "interaction.submitted"}:
                    self._status.mode = "Running"
            if event == "interaction.confirmed":
                kind = str(context.get("interaction_kind", "board interaction")).replace("-", " ")
                blueprint = str(context.get("blueprint_id", "item"))
                self._status.last_activity = f"Interacted with {kind}: {blueprint}"
                self._status.mode = "Running"
                self._status.wait_reason = None
            elif event.startswith("crate."):
                self._status.phase = "Claim Crates"
            elif event.startswith("shop."):
                self._status.phase = "Shops"
            if event == "crate.claim_completed":
                spawned = context.get("spawned", 0)
                remaining = context.get("remaining")
                self._status.last_activity = f"Spawned {spawned} supply crates"
                self._status.remaining_crates = remaining if isinstance(remaining, int) else None
                self._status.mode = "Running"
            if event in {"shop.order_started", "shop.order_claimed"}:
                recipe_id = str(context.get("recipe_id", "recipe"))
                activity = "Started" if event.endswith("started") else "Claimed"
                self._status.last_activity = f"{activity} shop recipe: {recipe_id}"
                self._status.mode = "Running"
            if event == "marketplace.purchase_confirmed":
                self._status.phase = "Marketplace"
                offer = context.get("policy_key", "offer")
                self._status.last_activity = f"Purchased marketplace offer: {offer}"
                self._status.mode = "Running"
            elif event == "farm_visit.return_confirmed":
                self._status.phase = "Farm Visits"
                self._status.last_activity = "Completed a farm visit"
                self._status.mode = "Running"
            elif event == "land_expansion.confirmed":
                self._status.phase = "Land Expansion"
                cells = context.get("cell_count", 0)
                self._status.last_activity = f"Expanded the board by {cells} cells"
                self._status.mode = "Running"
            elif event == "storage_bubble.confirmed":
                self._status.phase = "Storage Bubbles"
                self._status.last_activity = "Popped a storage bubble"
                self._status.mode = "Running"
            if record.levelno >= logging.ERROR:
                self._status.mode = "Error"

    def _record_metric(self, event: str, context: dict[str, object], level: int) -> None:
        metric_updates = Counter[str]()
        for update in metrics_for_event(event, context, level):
            metric_updates[update.name] += int(update.value)
        with self._metrics_lock:
            self._metrics.update(metric_updates)
        with self._status_lock:
            self._session_metrics.update(metric_updates)
        record_chart = getattr(self._activity_chart, "record", None)
        if callable(record_chart):
            record_chart(self._clock(), metric_updates)

    def _should_notify(self, record: logging.LogRecord, event: str) -> bool:
        if event in _LIFECYCLE_EVENTS or record.levelno >= logging.ERROR:
            return True
        if self._notification_profile == "detailed" and event in _DETAILED_EVENTS:
            return True
        if self._notification_profile == "minimal":
            return False
        if record.levelno < logging.WARNING:
            return False
        now = self._clock()
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

    def _take_summary(
        self, now: float, *, final: bool = False
    ) -> tuple[dict[str, object], Counter[str]] | None:
        with self._metrics_lock:
            metrics = self._metrics
            self._metrics = Counter()
        if not metrics:
            return None
        elapsed = max(0.0, now - self._started_at)
        period = "Final" if final else "Periodic"
        actions = sum(metrics[f"action.{effect}"] for effect in ("move", "swap", "merge"))
        interactions = sum(
            value for key, value in metrics.items() if key.startswith("interaction.")
        )
        producers = metrics["interaction.producer"] + metrics["interaction.depleted-producer"]
        activity = _metric_line(
            metrics,
            (
                ("Merges", "action.merge"),
                ("Moves", "action.move"),
                ("Swaps", "action.swap"),
                ("Crates", "crates"),
            ),
        )
        if interactions:
            activity += f"\nTile interactions {interactions}"
            if producers:
                activity += f" · Producers {producers}"
        progress = _metric_line(
            metrics,
            (
                ("Shop starts", "shop.started"),
                ("Shop claims", "shop.claimed"),
                ("Marketplace", "workflow.marketplace"),
                ("Farm visits", "workflow.farm_visit_return"),
                ("Visitor actions", "workflow.farm_visit_claim"),
                ("Event trips", "workflow.event_enter"),
                ("Event rewards", "workflow.event_reward"),
                ("Land expansions", "workflow.land_expansion"),
                ("Storage bubbles", "workflow.storage_bubble"),
            ),
        )
        resources = _metric_line(
            metrics,
            (
                ("Energy spent", "spent.energy"),
                ("Coins spent", "spent.coins"),
                ("Gems spent", "spent.gems"),
            ),
        )
        reliability = _metric_line(
            metrics,
            (
                ("Warnings", "warnings"),
                ("Errors", "errors"),
                ("Recoveries", "reliability.recovery"),
                ("Board blocks", "reliability.board_blocked"),
            ),
        )
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
                            "name": "Activity",
                            "value": (
                                f"**{actions + interactions + metrics['crates']} completed**\n"
                                f"{activity}"
                            ),
                            "inline": False,
                        },
                        {
                            "name": "Progress",
                            "value": progress,
                            "inline": False,
                        },
                        {
                            "name": "Resources",
                            "value": resources,
                            "inline": False,
                        },
                        {
                            "name": "Reliability",
                            "value": reliability,
                            "inline": False,
                        },
                    ],
                    "footer": {"text": "farm-merge-valet.summary"},
                }
            ],
        }, metrics

    def _restore_summary_metrics(self, metrics: Counter[str]) -> None:
        with self._metrics_lock:
            self._metrics.update(metrics)

    def _status_payload(self, now: float) -> dict[str, object]:
        with self._status_lock:
            status = StatusState(**vars(self._status))
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
        interactions = sum(
            value for key, value in metrics.items() if key.startswith("interaction.")
        )
        board_activity = f"Interactions {interactions} · Crates {metrics['crates']}"
        if status.remaining_crates is not None:
            board_activity += f" · Supply remaining {status.remaining_crates}"
        mode_detail = status.mode
        if status.wait_reason and status.mode == "Waiting":
            mode_detail = f"Waiting — {status.wait_reason[:120]}"
        health = _metric_line(
            metrics,
            (
                ("Warnings", "warnings"),
                ("Errors", "errors"),
                ("Recoveries", "reliability.recovery"),
            ),
        )
        return {
            "username": "Farm Merge Valet",
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": f"Current status · {mode_detail}",
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
                        {
                            "name": "Session health",
                            "value": health,
                            "inline": False,
                        },
                    ],
                    "footer": {"text": "farm-merge-valet.status"},
                }
            ],
        }
