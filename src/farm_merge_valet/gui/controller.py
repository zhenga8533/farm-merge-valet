"""Qt-facing application controller and worker lifecycle."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from farm_merge_valet.browser import BrowserManager, BrowserManagerError
from farm_merge_valet.config import AppConfig, ConfigStore, settings
from farm_merge_valet.core.bot import Bot
from farm_merge_valet.logging_setup import FMV_CONTEXT_ATTRIBUTE, FMV_EVENT_ATTRIBUTE, log_event
from farm_merge_valet.observability.discord import discord_webhook_sink

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApplicationStatus:
    mode: str = "Stopped"
    browser: str = "Not checked"
    runtime: str = "Waiting"
    phase: str = "—"
    last_activity: str = "No activity yet"


class _LogBridge(QObject):
    record_received = Signal(object)


class GuiEventHandler(logging.Handler):
    def __init__(self, bridge: _LogBridge) -> None:
        super().__init__(logging.DEBUG)
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        self._bridge.record_received.emit(record)


class ApplicationController(QObject):
    status_changed = Signal(object)
    config_changed = Signal(object)
    running_changed = Signal(bool, bool)
    log_received = Signal(str, str, str, int)
    error = Signal(str)
    application_quit_requested = Signal()

    def __init__(self, store: ConfigStore) -> None:
        super().__init__()
        self.store = store
        self.config = store.current
        settings.replace(self.config)
        self.status = ApplicationStatus()
        self._bot: Bot | None = None
        self._worker: threading.Thread | None = None
        self._stopping = False
        self._bridge = _LogBridge()
        self._bridge.record_received.connect(self._on_record)
        self.log_handler = GuiEventHandler(self._bridge)
        self._unsubscribe = store.subscribe(self._config_updated)

    def _config_updated(self, config: AppConfig) -> None:
        previous = self.config
        self.config = config
        settings.replace(config)
        browser_changed = any(
            (
                previous.browser != config.browser,
                previous.browser_executable != config.browser_executable,
                previous.browser_profile_dir != config.browser_profile_dir,
                previous.cdp_port != config.cdp_port,
                previous.game_url != config.game_url,
                previous.window_title != config.window_title,
            )
        )
        if browser_changed:
            self._set_status(
                browser="Restart required",
                last_activity="Browser settings saved · restart to apply",
            )
        if self._bot is not None:
            configure_delays = getattr(self._bot.runtime, "configure_crate_delays", None)
            if callable(configure_delays):
                configure_delays(config.crate_delay_min, config.crate_delay_max)
            restart_fields = (
                previous.pause_hotkey != config.pause_hotkey,
                previous.quit_hotkey != config.quit_hotkey,
                previous.discord_webhook_url != config.discord_webhook_url,
                previous.webhook_summary_interval != config.webhook_summary_interval,
                previous.webhook_status_interval != config.webhook_status_interval,
            )
            if any(restart_fields):
                self._set_status(last_activity="Settings saved · restart bot to apply controls")
        self.config_changed.emit(config)

    def update_config(self, **changes: object) -> None:
        try:
            self.store.update(**changes)
        except (OSError, ValueError) as exc:
            self.error.emit(f"Could not save settings: {exc}")

    def start_bot(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stopping = False
        self._set_status(mode="Starting", last_activity="Starting managed browser")
        self.running_changed.emit(True, False)
        self._worker = threading.Thread(target=self._run_bot, daemon=True, name="fmv-bot")
        self._worker.start()

    def _run_bot(self) -> None:
        config = self.store.current
        settings.replace(config)
        try:
            manager = BrowserManager(config)
            browser_status = (
                manager.ensure_running() if config.browser_auto_launch else manager.status()
            )
            if not browser_status.running or not browser_status.compatible:
                raise BrowserManagerError(
                    browser_status.detail or "A compatible managed browser is not running."
                )
            browser_name = browser_status.kind.value if browser_status.kind else "browser"
            log_event(
                logger,
                logging.INFO,
                "browser.ready",
                "Managed browser ready: %s (game_loaded=%s).",
                browser_name,
                browser_status.game_loaded,
                browser=browser_name,
                game_loaded=browser_status.game_loaded,
                managed=browser_status.managed,
            )
            webhook = (
                config.discord_webhook_url.get_secret_value()
                if config.discord_webhook_url is not None
                else None
            )
            self._bot = Bot()
            with discord_webhook_sink(
                webhook,
                config.webhook_summary_interval,
                config.webhook_status_interval,
            ):
                self._bot.run_forever()
        except BrowserManagerError as exc:
            log_event(
                logger,
                logging.ERROR,
                "browser.startup_failed",
                "Managed browser startup failed: %s",
                exc,
                detail=str(exc),
            )
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                "app.worker_failed",
                "Automation worker stopped unexpectedly: %s",
                exc,
                detail=str(exc),
                _exc_info=True,
            )
        finally:
            quit_from_inbound_control = bool(
                self._bot is not None and self._bot.quit_requested and not self._stopping
            )
            self._bot = None
            self._stopping = False
            self._set_status(mode="Stopped", phase="—")
            self.running_changed.emit(False, False)
            if quit_from_inbound_control:
                self.application_quit_requested.emit()

    def toggle_pause(self) -> None:
        if self._bot is None:
            return
        self._bot.toggle_pause()

    def stop_bot(self) -> None:
        if self._bot is None or self._stopping:
            return
        self._stopping = True
        self._set_status(mode="Stopping")
        self._bot.request_quit()

    def shutdown(self) -> None:
        self.stop_bot()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=5)
        self._unsubscribe()

    def browser_status(self) -> str:
        status = BrowserManager(self.store.current).status()
        if not status.running:
            return status.detail or "Managed browser is not running"
        loaded = "game loaded" if status.game_loaded else "waiting for game"
        return f"{status.kind.value if status.kind else 'Browser'} ready · {loaded}"

    def restart_browser(self) -> None:
        if self._bot is not None:
            self.error.emit("Stop the bot before restarting the managed browser.")
            return
        try:
            status = BrowserManager(self.store.current).restart()
        except BrowserManagerError as exc:
            self.error.emit(str(exc))
            return
        self._set_status(
            browser=f"{status.kind.value if status.kind else 'Browser'} ready",
            last_activity="Managed browser restarted",
        )

    def _on_record(self, record: logging.LogRecord) -> None:
        formatter = self.log_handler.formatter or logging.Formatter()
        timestamp = formatter.formatTime(record, "%H:%M:%S")
        message = record.getMessage()
        self.log_received.emit(timestamp, record.levelname, message, record.levelno)
        event = getattr(record, FMV_EVENT_ATTRIBUTE, None)
        context = getattr(record, FMV_CONTEXT_ATTRIBUTE, {})
        updates: dict[str, str] = {}
        if event == "browser.ready":
            updates["browser"] = f"{context.get('browser', 'Browser')} ready"
        elif event == "runtime.ready":
            updates.update(mode="Running", runtime=f"Scene {context.get('scene_id', '—')} ready")
        elif event in {"bot.paused", "bot.started_paused", "bot.resume_cancelled"}:
            updates["mode"] = "Paused"
        elif event == "bot.resume_requested":
            updates["mode"] = "Resuming"
        elif event == "bot.idle":
            updates.update(mode="Idle", last_activity=message)
        elif event == "planner.phase_changed":
            updates["phase"] = str(context.get("phase", "—")).replace("_", " ").title()
        elif event in {"item_action.confirmed", "claim.confirmed", "crate.claim_completed"}:
            updates.update(mode="Running", last_activity=message)
        elif record.levelno >= logging.ERROR:
            updates.update(mode="Error", last_activity=message)
        if updates:
            self._set_status(**updates)

    def _set_status(self, **changes: str) -> None:
        values = vars(self.status) | changes
        self.status = ApplicationStatus(**values)
        self.status_changed.emit(self.status)
