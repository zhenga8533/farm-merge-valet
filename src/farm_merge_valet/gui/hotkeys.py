"""Application-lifetime global hotkey registration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Lock
from typing import Any

import keyboard
from PySide6.QtCore import QObject, Signal

from farm_merge_valet.hotkeys import HotkeyBindings

logger = logging.getLogger(__name__)


class HotkeyManager(QObject):
    start_stop_requested = Signal()
    pause_resume_requested = Signal()
    quit_requested = Signal()
    registration_failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._handles: list[tuple[Callable[[Any], None], Any]] = []
        self._bindings: HotkeyBindings | None = None
        self._active = False
        self._suspended = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self, bindings: HotkeyBindings) -> bool:
        self._active = True
        self._bindings = bindings
        return self._apply_bindings()

    def rebind(self, bindings: HotkeyBindings) -> bool:
        self._bindings = bindings
        if not self._active or self._suspended:
            return True
        return self._apply_bindings()

    def set_suspended(self, suspended: bool) -> None:
        if self._suspended == suspended:
            return
        self._suspended = suspended
        if suspended:
            self._remove_handles()
        elif self._active:
            self._apply_bindings()

    def stop(self) -> None:
        self._active = False
        self._remove_handles()

    def _apply_bindings(self) -> bool:
        bindings = self._bindings
        if bindings is None:
            return True
        callbacks = (
            (bindings.start_stop, self.start_stop_requested.emit),
            (bindings.pause_resume, self.pause_resume_requested.emit),
            (bindings.quit_application, self.quit_requested.emit),
        )
        new_handles: list[tuple[Callable[[Any], None], Any]] = []
        try:
            for hotkey, callback in callbacks:
                if hotkey is None:
                    continue
                new_handles.extend(self._register_binding(hotkey, callback))
        except Exception as exc:
            self._remove(new_handles)
            self.registration_failed.emit(f"Could not register global hotkeys: {exc}")
            return False
        old_handles = self._handles
        self._handles = new_handles
        self._remove(old_handles)
        return True

    @staticmethod
    def _register_binding(
        hotkey: str, callback: Callable[[], None]
    ) -> list[tuple[Callable[[Any], None], Any]]:
        latch = Lock()
        held = False

        def activate() -> None:
            nonlocal held
            with latch:
                if held:
                    return
                held = True
            callback()

        def release(_event: object) -> None:
            nonlocal held
            with latch:
                held = False

        release_handle = keyboard.on_release_key(hotkey.rsplit("+", 1)[-1], release)
        try:
            hotkey_handle = keyboard.add_hotkey(hotkey, activate)
        except Exception:
            keyboard.unhook(release_handle)
            raise
        return [
            (keyboard.unhook, release_handle),
            (keyboard.remove_hotkey, hotkey_handle),
        ]

    def _remove_handles(self) -> None:
        handles = self._handles
        self._handles = []
        self._remove(handles)

    @staticmethod
    def _remove(handles: list[tuple[Callable[[Any], None], Any]]) -> None:
        for remove, handle in reversed(handles):
            try:
                remove(handle)
            except Exception:
                logger.warning("Could not remove global hotkey registration", exc_info=True)
