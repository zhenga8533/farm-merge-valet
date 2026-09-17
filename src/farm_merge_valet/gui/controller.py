"""Qt-facing application controller and worker lifecycle."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from farm_merge_valet.automation.bot import Bot
from farm_merge_valet.automation.runtime import BuildingRepairState, RuntimeRecoveryRequired
from farm_merge_valet.browser import BrowserManager, BrowserManagerError, BrowserStatus
from farm_merge_valet.cdp.capture import capture_game_screenshot
from farm_merge_valet.composition import create_bot, create_catalog_sync_service
from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.config.change_policy import BOT_RESTART_FIELDS, BROWSER_RESTART_FIELDS
from farm_merge_valet.config.hotkeys import HotkeyBindings
from farm_merge_valet.core.upgrade_progress import UpgradeProgress
from farm_merge_valet.gui.services.background_operation import (
    BackgroundOperationCancelled,
    BackgroundOperationRunner,
)
from farm_merge_valet.gui.services.catalog_freshness import (
    CatalogFreshnessState,
    inspect_catalog_freshness,
)
from farm_merge_valet.gui.services.catalog_sync import CatalogSyncCallbacks, CatalogSyncService
from farm_merge_valet.gui.services.hotkeys import HotkeyManager
from farm_merge_valet.gui.services.live_progress_cache import (
    load_live_progress_cache,
    write_live_progress_cache,
)
from farm_merge_valet.gui.services.statistics_operations import StatisticsOperations
from farm_merge_valet.observability.discord import discord_webhook_sink
from farm_merge_valet.observability.logging import (
    FMV_CONTEXT_ATTRIBUTE,
    FMV_EVENT_ATTRIBUTE,
    log_event,
)
from farm_merge_valet.observability.statistics import StatisticsService

logger = logging.getLogger(__name__)

# A run that stays healthy this long before needing recovery again is treated
# as a fresh start rather than a continuation of a prior failure streak.
_RECOVERY_HEALTHY_RESET_SECONDS = 3600.0


class ApplicationState(StrEnum):
    STOPPED = "Stopped"
    STARTING = "Starting"
    RUNNING = "Running"
    IDLE = "Idle"
    PAUSED = "Paused"
    RESUMING = "Resuming"
    STOPPING = "Stopping"
    ERROR = "Error"

    @property
    def active(self) -> bool:
        return self not in {ApplicationState.STOPPED, ApplicationState.ERROR}


@dataclass(frozen=True)
class ApplicationStatus:
    state: ApplicationState = ApplicationState.STOPPED
    browser: str = "Not checked"
    runtime: str = "Waiting"
    phase: str = "—"
    last_activity: str = "No activity yet"

    @property
    def mode(self) -> str:
        return self.state.value


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
    browser_operation_changed = Signal(bool)
    browser_status_changed = Signal(str)
    browser_state_changed = Signal(object)
    game_sync_operation_changed = Signal(bool)
    game_sync_status_changed = Signal(str)
    catalog_setup_failed = Signal(str)
    catalog_refreshed = Signal(int)
    assets_refreshed = Signal()
    upgrade_progress_changed = Signal(object)
    building_repairs_changed = Signal(object)
    catalog_freshness_changed = Signal(object)
    statistics_changed = Signal(object)
    statistics_exported = Signal(object)
    statistics_reset = Signal(object)
    shutdown_complete = Signal()
    _utility_operation_finished = Signal(str, object)
    _bot_finished = Signal(object)

    def __init__(self, store: ConfigStore, statistics: StatisticsService | None = None) -> None:
        super().__init__()
        self.store = store
        self.config = store.current
        self.status = ApplicationStatus()
        self.statistics = statistics
        self._statistics_operations = (
            StatisticsOperations(statistics, self) if statistics is not None else None
        )
        if self._statistics_operations is not None:
            self._statistics_operations.snapshot_ready.connect(self.statistics_changed)
            self._statistics_operations.export_finished.connect(self.statistics_exported)
            self._statistics_operations.reset_finished.connect(self.statistics_reset)
            self._statistics_operations.failed.connect(self.error)
        self._bot: Bot | None = None
        self._worker: threading.Thread | None = None
        self._operations = BackgroundOperationRunner(self._utility_operation_finished.emit)
        self._stopping = False
        self._shutting_down = False
        self._unsubscribed = False
        self._shutdown_browser_worker: threading.Thread | None = None
        self._shutdown_browser_error: str | None = None
        self._shutdown_browser_complete = False
        cached_progress = load_live_progress_cache(self.config.catalog_dir)
        self._last_upgrade_progress = cached_progress.upgrades
        self._last_building_repairs = cached_progress.buildings
        self.hotkeys = HotkeyManager()
        self.hotkeys.setParent(self)
        self.hotkeys.start_stop_requested.connect(self.toggle_running)
        self.hotkeys.pause_resume_requested.connect(self.toggle_pause)
        self.hotkeys.quit_requested.connect(self.application_quit_requested)
        self.hotkeys.registration_failed.connect(self._hotkey_registration_failed)
        self._bridge = _LogBridge(self)
        self._bridge.record_received.connect(self._on_record)
        self.log_handler = GuiEventHandler(self._bridge)
        self._unsubscribe = store.subscribe(self._config_updated)
        self._utility_operation_finished.connect(self._finish_utility_operation)
        self._bot_finished.connect(self._finish_bot)
        self._state_timer = QTimer(self)
        self._state_timer.setInterval(100)
        self._state_timer.timeout.connect(self._poll_workers)
        self._state_timer.start()

    def _config_updated(self, config: AppConfig) -> None:
        previous = self.config
        self.config = config
        if previous.catalog_dir != config.catalog_dir:
            cached_progress = load_live_progress_cache(config.catalog_dir)
            self._last_upgrade_progress = cached_progress.upgrades
            self._last_building_repairs = cached_progress.buildings
            self.upgrade_progress_changed.emit(self._last_upgrade_progress)
            self.building_repairs_changed.emit(self._last_building_repairs)
        browser_changed = any(
            getattr(previous, name) != getattr(config, name) for name in BROWSER_RESTART_FIELDS
        )
        if browser_changed:
            self._set_status(
                browser="Restart required",
                last_activity="Browser settings saved · restart to apply",
            )
        bot = self._bot
        if bot is not None:
            bot.update_config(config)
            if any(getattr(previous, name) != getattr(config, name) for name in BOT_RESTART_FIELDS):
                self._set_status(last_activity="Settings saved · restart bot to apply")
        hotkeys_changed = any(
            (
                previous.start_stop_hotkey != config.start_stop_hotkey,
                previous.pause_hotkey != config.pause_hotkey,
                previous.quit_hotkey != config.quit_hotkey,
            )
        )
        if hotkeys_changed and self.hotkeys.active:
            self._rebind_hotkeys(config)
        self.config_changed.emit(config)

    def start_hotkeys(self) -> None:
        try:
            bindings = HotkeyBindings.from_values(
                self.config.start_stop_hotkey,
                self.config.pause_hotkey,
                self.config.quit_hotkey,
            )
        except ValueError as exc:
            self._hotkey_registration_failed(f"Invalid global hotkey configuration: {exc}")
            return
        self.hotkeys.start(bindings)

    def set_hotkey_recording(self, recording: bool) -> None:
        self.hotkeys.set_suspended(recording)

    def _rebind_hotkeys(self, config: AppConfig) -> None:
        try:
            bindings = HotkeyBindings.from_values(
                config.start_stop_hotkey,
                config.pause_hotkey,
                config.quit_hotkey,
            )
        except ValueError as exc:
            self._hotkey_registration_failed(f"Invalid global hotkey configuration: {exc}")
            return
        self.hotkeys.rebind(bindings)

    def _hotkey_registration_failed(self, message: str) -> None:
        logger.error(message)
        self._set_status(last_activity=message)
        self.error.emit(message)

    def update_config(self, **changes: object) -> None:
        try:
            self.store.update(**changes)
        except (OSError, ValueError) as exc:
            self.error.emit(f"Could not save settings: {exc}")

    def start_bot(self) -> None:
        if self._shutting_down or (self._worker is not None and self._worker.is_alive()):
            return
        self._stopping = False
        if self.statistics is not None:
            try:
                self.statistics.start_session()
            except (OSError, sqlite3.Error) as exc:
                self.error.emit(f"Could not start statistics collection: {exc}")
        self._set_status(
            state=ApplicationState.STARTING,
            last_activity="Starting managed browser",
        )
        self._worker = threading.Thread(target=self._run_bot, daemon=True, name="fmv-bot")
        self._worker.start()

    def _run_bot(self) -> None:
        config = self.store.current
        cached_freshness = inspect_catalog_freshness(config.catalog_dir / "catalog.json")
        failure: str | None = None
        try:
            manager = BrowserManager(config)
            browser_status = (
                manager.ensure_running() if config.browser_auto_launch else manager.status()
            )
            if not browser_status.running or not browser_status.compatible:
                raise BrowserManagerError(
                    browser_status.detail or "A compatible managed browser is not running."
                )
            if config.browser_auto_launch and not browser_status.game_frame_available:
                browser_status = manager.ensure_game_open()
            self.browser_status_changed.emit(self._browser_status_text(browser_status))
            self.browser_state_changed.emit(browser_status)
            browser_name = browser_status.kind.value if browser_status.kind else "browser"
            log_event(
                logger,
                logging.INFO,
                "browser.ready",
                "Managed browser ready: %s (game_frame_available=%s).",
                browser_name,
                browser_status.game_frame_available,
                browser=browser_name,
                game_frame_available=browser_status.game_frame_available,
                managed=browser_status.managed,
            )
            if self._stopping:
                return
            webhook = (
                config.discord_webhook_url.get_secret_value()
                if config.discord_webhook_url is not None
                else None
            )

            def refresh_startup_data() -> None:
                service = self._catalog_sync_service(config)
                service.check_catalog_freshness(cached_freshness)
                service.refresh_live_progress(source="bot startup")

            with discord_webhook_sink(
                webhook,
                config.webhook_summary_interval,
                config.webhook_status_interval,
                notification_profile=config.webhook_notification_profile,
                include_charts=config.webhook_include_charts,
                screenshot_provider=(
                    lambda: capture_game_screenshot(config.cdp_port, config.window_title)
                )
                if config.webhook_include_screenshots
                else None,
            ):
                recovery_attempts = 0
                while not self._shutting_down and not self._stopping:
                    self._bot = create_bot(config)
                    latest_config = self.store.current
                    if latest_config != config:
                        self._bot.update_config(latest_config)
                    run_started_at = time.monotonic()
                    try:
                        self._bot.run_forever(on_initialized=refresh_startup_data)
                        break
                    except RuntimeRecoveryRequired as exc:
                        if time.monotonic() - run_started_at >= _RECOVERY_HEALTHY_RESET_SECONDS:
                            recovery_attempts = 0
                        max_attempts = self.store.current.max_game_recovery_attempts
                        if not self.store.current.auto_recover_game or (
                            max_attempts and recovery_attempts >= max_attempts
                        ):
                            raise
                        recovery_attempts += 1
                        log_event(
                            logger,
                            logging.WARNING,
                            "runtime.recovery_started",
                            "Recovering the managed game after an unresponsive action "
                            "pipeline (attempt %d%s).",
                            recovery_attempts,
                            f"/{max_attempts}" if max_attempts else "",
                            detail=str(exc),
                            attempt=recovery_attempts,
                            attempt_limit=max_attempts or None,
                        )
                        manager.recover_game()
        except BrowserManagerError as exc:
            failure = str(exc)
            log_event(
                logger,
                logging.ERROR,
                "browser.startup_failed",
                "Managed browser startup failed: %s",
                exc,
                detail=str(exc),
            )
        except Exception as exc:
            failure = str(exc)
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
            self._bot = None
            self._bot_finished.emit(failure)

    def toggle_pause(self) -> None:
        if self._bot is None:
            return
        was_paused = self._bot.paused
        self._bot.toggle_pause()
        if not was_paused or self.status.state is ApplicationState.RESUMING:
            self._set_status(state=ApplicationState.PAUSED)
        else:
            self._set_status(state=ApplicationState.RESUMING)

    def toggle_running(self) -> None:
        if self.status.state in {ApplicationState.STOPPED, ApplicationState.ERROR}:
            self.start_bot()
        elif self.status.state in {
            ApplicationState.STARTING,
            ApplicationState.RUNNING,
            ApplicationState.IDLE,
            ApplicationState.PAUSED,
            ApplicationState.RESUMING,
        }:
            self.stop_bot()

    def stop_bot(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._set_status(state=ApplicationState.STOPPING)
        if self._bot is not None:
            self._bot.request_quit()
        elif self._worker is None or not self._worker.is_alive():
            self._stopping = False
            self._set_status(state=ApplicationState.STOPPED)

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        if self._statistics_operations is not None:
            self._statistics_operations.close()
        self._operations.cancel()
        self.hotkeys.stop()
        if self._bot is None and self._worker is not None and self._worker.is_alive():
            self._stopping = True
            self._set_status(state=ApplicationState.STOPPING)
        else:
            self.stop_bot()
        self._poll_workers()

    def _finish_bot(self, failure: object) -> None:
        if self.statistics is not None:
            try:
                self.statistics.end_session()
            except (OSError, sqlite3.Error) as exc:
                self.error.emit(f"Could not finish statistics collection: {exc}")
        self._stopping = False
        state = ApplicationState.ERROR if failure is not None else ApplicationState.STOPPED
        self._set_status(state=state, phase="—")

    def refresh_statistics(self, range_key: str) -> None:
        if self._statistics_operations is None:
            self.statistics_changed.emit(None)
            return
        self._statistics_operations.refresh(range_key)

    def export_statistics(self, path: Path, range_key: str) -> None:
        if self._statistics_operations is None:
            self.error.emit("Statistics storage is unavailable.")
            return
        self._statistics_operations.export(path, range_key)

    def reset_statistics(self, range_key: str = "session") -> None:
        if self._statistics_operations is None:
            return
        self._statistics_operations.reset(range_key)

    @staticmethod
    def _browser_status_text(status: BrowserStatus) -> str:
        if not status.running:
            return status.detail or "Managed browser is not running"
        loaded = "game frame available" if status.game_frame_available else "waiting for game"
        return f"{status.kind.value if status.kind else 'Browser'} ready · {loaded}"

    def refresh_browser(self) -> None:
        self._run_browser_operation("refresh")

    def launch_browser(self) -> None:
        self._run_browser_operation("launch")

    def stop_browser(self) -> None:
        self._run_browser_operation("stop")

    def restart_browser(self) -> None:
        self._run_browser_operation("restart")

    def _run_browser_operation(self, operation: str) -> None:
        if operation != "refresh" and self.status.state.active:
            self.error.emit("Stop the bot before changing the managed browser.")
            return
        messages = {
            "refresh": "Checking browser…",
            "launch": "Launching managed browser…",
            "restart": "Restarting managed browser…",
            "stop": "Stopping managed browser…",
        }
        self.browser_status_changed.emit(messages[operation])

        def work() -> BrowserStatus:
            config = self.store.current
            manager = BrowserManager(config)
            if operation == "launch":
                status = manager.launch()
            elif operation == "restart":
                status = manager.restart()
            elif operation == "stop":
                manager.stop()
                status = manager.status()
            else:
                status = manager.status()
            return status

        self._start_utility_operation(f"browser:{operation}", work)

    def synchronize_game_data(self) -> None:
        if self.status.state.active:
            self.error.emit("Stop the bot before synchronizing game data and assets.")
            return
        self.game_sync_status_changed.emit("Synchronizing game data and assets…")
        service = self._catalog_sync_service(self.store.current)
        self._start_utility_operation("game-sync:manual", service.synchronize)

    def clear_game_cache(self) -> None:
        if self.status.state.active:
            self.error.emit("Stop the bot before clearing cached game data and assets.")
            return
        config = self.store.current
        self.game_sync_status_changed.emit("Clearing cached game data and assets...")

        def work() -> str:
            targets = tuple(path.resolve() for path in (config.catalog_dir, config.atlas_cache_dir))
            self._validate_cache_targets(targets)
            for target in targets:
                if target.exists():
                    shutil.rmtree(target)
            return "Local game data and asset cache cleared"

        self._start_utility_operation("game-sync:clear-cache", work)

    @staticmethod
    def _validate_cache_targets(targets: tuple[Path, ...]) -> None:
        home = Path.home().resolve()
        for target in targets:
            if target == home or target == Path(target.anchor) or len(target.parts) < 3:
                raise ValueError(f"Refusing to clear unsafe cache directory: {target}")
            if target.exists() and not target.is_dir():
                raise ValueError(f"Cache path is not a directory: {target}")
        first, second = targets
        if first == second or first in second.parents or second in first.parents:
            raise ValueError("Catalog and atlas cache directories must not overlap.")

    def setup_catalog(self) -> None:
        if self.status.state.active:
            self.error.emit("Stop the bot before synchronizing the game catalog.")
            return
        service = self._catalog_sync_service(self.store.current)
        self._start_utility_operation("game-sync:onboard", service.set_up)

    def _catalog_sync_service(self, config: AppConfig) -> CatalogSyncService:
        return create_catalog_sync_service(
            config,
            CatalogSyncCallbacks(
                raise_if_cancelled=self._raise_if_utility_cancelled,
                wait=self._wait_for_utility_poll,
                status_changed=self.game_sync_status_changed.emit,
                browser_changed=self._catalog_browser_changed,
                catalog_refreshed=self.catalog_refreshed.emit,
                upgrade_progress_changed=self._upgrade_progress_updated,
                catalog_freshness_changed=self.catalog_freshness_changed.emit,
                building_repairs_changed=self._building_repairs_updated,
            ),
        )

    @property
    def cached_upgrade_progress(self) -> UpgradeProgress | None:
        return self._last_upgrade_progress

    @property
    def cached_building_repairs(self) -> tuple[BuildingRepairState, ...] | None:
        return self._last_building_repairs

    def _upgrade_progress_updated(self, progress: UpgradeProgress | None) -> None:
        if progress is None:
            return
        self._last_upgrade_progress = progress
        self._persist_live_progress()
        self.upgrade_progress_changed.emit(progress)

    def _building_repairs_updated(self, repairs: tuple[BuildingRepairState, ...] | None) -> None:
        if repairs is None:
            return
        self._last_building_repairs = repairs
        self._persist_live_progress()
        self.building_repairs_changed.emit(repairs)

    def _persist_live_progress(self) -> None:
        try:
            write_live_progress_cache(
                self.config.catalog_dir,
                self._last_upgrade_progress,
                self._last_building_repairs,
            )
        except OSError as exc:
            log_event(
                logger,
                logging.WARNING,
                "live_progress.cache_failed",
                "Could not cache live building and upgrade progress: %s",
                exc,
                detail=str(exc),
            )

    def _catalog_browser_changed(self, status: BrowserStatus) -> None:
        self.browser_status_changed.emit(self._browser_status_text(status))
        self.browser_state_changed.emit(status)

    def _start_utility_operation(
        self,
        operation: str,
        work: Callable[[], object],
        *,
        report_busy_error: bool = True,
    ) -> bool:
        if self._shutting_down:
            return False
        if self._operations.running:
            if report_busy_error:
                self.error.emit("Another background operation is already in progress.")
            return False
        category = operation.partition(":")[0]
        self._set_utility_busy(category, True)
        return self._operations.start(operation, work)

    def _raise_if_utility_cancelled(self) -> None:
        self._operations.raise_if_cancelled()

    def _wait_for_utility_poll(self, seconds: float) -> None:
        self._operations.wait(seconds)

    def _set_utility_busy(self, category: str, busy: bool) -> None:
        if category == "browser":
            self.browser_operation_changed.emit(busy)
        elif category == "game-sync":
            self.game_sync_operation_changed.emit(busy)

    def _finish_utility_operation(self, operation: str, result: object) -> None:
        category, _, action = operation.partition(":")
        self._operations.mark_completed()
        self._set_utility_busy(category, False)
        if isinstance(result, BackgroundOperationCancelled):
            self._poll_workers()
            return
        if isinstance(result, Exception):
            message = str(result)
            if category == "browser":
                self.browser_status_changed.emit(message)
            elif category == "game-sync":
                self.game_sync_status_changed.emit(message)
                if action == "onboard":
                    self.catalog_setup_failed.emit(message)
            if not self._shutting_down:
                self.error.emit(message)
            return
        if category == "browser" and isinstance(result, BrowserStatus):
            message = self._browser_status_text(result)
            self.browser_status_changed.emit(message)
            self.browser_state_changed.emit(result)
            if action != "refresh":
                activity = {
                    "launch": "Managed browser launched",
                    "restart": "Managed browser restarted",
                    "stop": "Managed browser stopped",
                }[action]
                self._set_status(browser=message, last_activity=activity)
            if result.game_frame_available:
                self._check_catalog_freshness()
        elif category == "game-sync":
            self.game_sync_status_changed.emit(str(result))
            self.assets_refreshed.emit()

    def _check_catalog_freshness(self) -> None:
        config = self.store.current
        cached = inspect_catalog_freshness(config.catalog_dir / "catalog.json")
        if cached.state in {CatalogFreshnessState.MISSING, CatalogFreshnessState.INVALID}:
            self.catalog_freshness_changed.emit(cached)
            return
        service = self._catalog_sync_service(config)
        self._start_utility_operation(
            "catalog:freshness",
            lambda: service.check_catalog_freshness(cached),
            report_busy_error=False,
        )

    def _on_record(self, record: logging.LogRecord) -> None:
        formatter = self.log_handler.formatter or logging.Formatter()
        timestamp = formatter.formatTime(record, "%H:%M:%S")
        message = formatter.format(record)
        status_message = record.getMessage()
        self.log_received.emit(timestamp, record.levelname, message, record.levelno)
        event = getattr(record, FMV_EVENT_ATTRIBUTE, None)
        context = getattr(record, FMV_CONTEXT_ATTRIBUTE, {})
        updates: dict[str, str] = {}
        if event == "browser.ready":
            updates["browser"] = f"{context.get('browser', 'Browser')} ready"
        elif event == "runtime.ready":
            updates["runtime"] = f"Scene {context.get('scene_id', '—')} ready"
        elif event in {
            "bot.idle",
            "action.confirmed",
            "interaction.confirmed",
            "crate.claim_completed",
        }:
            updates["last_activity"] = status_message
        elif event == "planner.phase_changed":
            updates["phase"] = str(context.get("phase", "—")).replace("_", " ").title()
        elif record.levelno >= logging.ERROR:
            updates["last_activity"] = status_message
        if updates:
            self._set_status(**updates)

    def _poll_workers(self) -> None:
        bot = self._bot
        if bot is not None and not self._stopping:
            upgrade_progress = getattr(bot, "upgrade_progress", None)
            if upgrade_progress is not None and upgrade_progress != self._last_upgrade_progress:
                self._upgrade_progress_updated(upgrade_progress)
            repairs = bot.building_repairs
            if repairs is not None and repairs != self._last_building_repairs:
                self._building_repairs_updated(repairs)
            if bot.paused and self.status.state not in {
                ApplicationState.PAUSED,
                ApplicationState.RESUMING,
            }:
                self._set_status(state=ApplicationState.PAUSED)
            elif not bot.paused and self.status.state is ApplicationState.PAUSED:
                self._set_status(state=ApplicationState.RESUMING)
            elif not bot.paused and self.status.state in {
                ApplicationState.STARTING,
                ApplicationState.RESUMING,
            }:
                self._set_status(state=ApplicationState.RUNNING)
        if not self._shutting_down:
            return
        bot_running = self._worker is not None and self._worker.is_alive()
        if bot_running or self._operations.running or self._operations.completion_pending:
            return
        if self.config.close_managed_browser_on_exit and not self._shutdown_browser_complete:
            worker = self._shutdown_browser_worker
            if worker is None:
                self._shutdown_browser_worker = threading.Thread(
                    target=self._close_managed_browser_for_shutdown,
                    daemon=True,
                    name="fmv-browser-shutdown",
                )
                self._shutdown_browser_worker.start()
                return
            if worker.is_alive():
                return
            self._shutdown_browser_complete = True
            if self._shutdown_browser_error is not None:
                log_event(
                    logger,
                    logging.WARNING,
                    "browser.shutdown_failed",
                    "Could not close the managed browser during shutdown: %s",
                    self._shutdown_browser_error,
                    detail=self._shutdown_browser_error,
                )
        self._state_timer.stop()
        if not self._unsubscribed:
            self._unsubscribe()
            self._unsubscribed = True
        self.shutdown_complete.emit()
        self.deleteLater()

    def _close_managed_browser_for_shutdown(self) -> None:
        try:
            manager = BrowserManager(self.store.current)
            status = manager.status()
            if status.running and status.managed:
                manager.stop()
        except (OSError, RuntimeError) as exc:
            self._shutdown_browser_error = str(exc)

    def _set_status(self, **changes: object) -> None:
        values = vars(self.status) | changes
        self.status = ApplicationStatus(**values)
        self.status_changed.emit(self.status)
        state = self.status.state
        self.running_changed.emit(state.active, state is ApplicationState.PAUSED)
