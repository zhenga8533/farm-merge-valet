"""Atomic configuration persistence."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from threading import RLock

from farm_merge_valet.config.files import replace_file
from farm_merge_valet.config.models import AppConfig
from farm_merge_valet.config.paths import user_config_path
from farm_merge_valet.config.secret_store import SecretStore

logger = logging.getLogger(__name__)

ConfigListener = Callable[[AppConfig], None]


class ConfigStore:
    """Own a validated snapshot and persist successful updates atomically."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_config_path()
        self._lock = RLock()
        self._write_lock = RLock()
        self._listeners: list[ConfigListener] = []
        self._current = AppConfig()

    @property
    def current(self) -> AppConfig:
        with self._lock:
            return self._current.model_copy(deep=True)

    def load(self) -> AppConfig:
        with self._lock:
            if self.path.exists():
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                legacy_webhook = payload.get("discord_webhook_url")
                stored_webhook = self._read_stored_webhook()
                payload["discord_webhook_url"] = stored_webhook or legacy_webhook
                self._current = AppConfig.model_validate(payload)
                if legacy_webhook:
                    self._persist(self._current)
            else:
                self._persist(self._current)
            return self._current.model_copy(deep=True)

    def _read_stored_webhook(self) -> str | None:
        try:
            return SecretStore(self.path).read()
        except (OSError, ValueError) as exc:
            logger.warning(
                "Could not read the stored Discord webhook secret; treating it as unset: %s", exc
            )
            return None

    def replace(self, config: AppConfig) -> AppConfig:
        snapshot = AppConfig.model_validate(config.model_dump())
        with self._lock:
            unchanged = snapshot == self._current and self.path.exists()
        if unchanged:
            return snapshot.model_copy(deep=True)
        self.persist(snapshot)
        return self.publish(snapshot)

    def persist(self, config: AppConfig) -> AppConfig:
        """Durably write a validated snapshot without notifying listeners."""

        snapshot = AppConfig.model_validate(config.model_dump())
        with self._write_lock:
            self._persist(snapshot)
        return snapshot.model_copy(deep=True)

    def publish(self, config: AppConfig) -> AppConfig:
        """Make an already-persisted snapshot current and notify listeners."""

        snapshot = AppConfig.model_validate(config.model_dump())
        with self._lock:
            self._current = snapshot
            listeners = tuple(self._listeners)
        delivered = snapshot.model_copy(deep=True)
        for listener in listeners:
            listener(delivered)
        return delivered

    def update(self, **changes: object) -> AppConfig:
        with self._lock:
            values = self._current.model_dump()
        values.update(changes)
        return self.replace(AppConfig.model_validate(values))

    def reset(self) -> AppConfig:
        return self.replace(AppConfig())

    def subscribe(self, listener: ConfigListener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def _persist(self, config: AppConfig) -> None:
        payload = config.model_dump(mode="json")
        webhook = (
            config.discord_webhook_url.get_secret_value()
            if config.discord_webhook_url is not None
            else None
        )
        payload["discord_webhook_url"] = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp", text=True
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            replace_file(temporary, self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        SecretStore(self.path).write(webhook)
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
