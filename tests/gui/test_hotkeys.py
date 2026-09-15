from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.config.hotkeys import HotkeyBindings
from farm_merge_valet.gui.components.hotkey_edit import HotkeyEdit
from farm_merge_valet.gui.controller import (
    ApplicationController,
    ApplicationState,
)
from farm_merge_valet.gui.services.hotkeys import HotkeyManager


def test_hotkey_manager_owns_only_its_registration_handles(monkeypatch) -> None:
    registrations: list[tuple[str, object]] = []
    release_registrations: list[tuple[str, object]] = []
    removed_hotkeys: list[object] = []
    removed_releases: list[object] = []

    def add_hotkey(hotkey, callback):
        handle = object()
        registrations.append((hotkey, callback))
        return handle

    def on_release_key(key, callback):
        handle = object()
        release_registrations.append((key, callback))
        return handle

    monkeypatch.setattr("farm_merge_valet.gui.services.hotkeys.keyboard.add_hotkey", add_hotkey)
    monkeypatch.setattr(
        "farm_merge_valet.gui.services.hotkeys.keyboard.on_release_key", on_release_key
    )
    monkeypatch.setattr(
        "farm_merge_valet.gui.services.hotkeys.keyboard.remove_hotkey", removed_hotkeys.append
    )
    monkeypatch.setattr(
        "farm_merge_valet.gui.services.hotkeys.keyboard.unhook", removed_releases.append
    )
    manager = HotkeyManager()
    starts: list[bool] = []
    manager.start_stop_requested.connect(lambda: starts.append(True))

    assert manager.start(HotkeyBindings("f8", "f9", "f10"))
    assert [registration[0] for registration in registrations] == ["f8", "f9", "f10"]
    registrations[0][1]()
    registrations[0][1]()
    assert starts == [True]
    release_registrations[0][1](object())
    registrations[0][1]()
    assert starts == [True, True]

    manager.set_suspended(True)
    assert len(removed_hotkeys) == 3
    assert len(removed_releases) == 3
    manager.set_suspended(False)
    assert len(registrations) == 6
    manager.stop()
    assert len(removed_hotkeys) == 6
    assert len(removed_releases) == 6


def test_failed_rebind_keeps_existing_hotkeys(monkeypatch) -> None:
    registrations = 0
    releases = 0
    removed_hotkeys: list[object] = []
    removed_releases: list[object] = []

    def add_hotkey(_hotkey, _callback):
        nonlocal registrations
        registrations += 1
        if registrations == 5:
            raise OSError("shortcut unavailable")
        return f"handle-{registrations}"

    def on_release_key(_key, _callback):
        nonlocal releases
        releases += 1
        return f"release-{releases}"

    monkeypatch.setattr("farm_merge_valet.gui.services.hotkeys.keyboard.add_hotkey", add_hotkey)
    monkeypatch.setattr(
        "farm_merge_valet.gui.services.hotkeys.keyboard.on_release_key", on_release_key
    )
    monkeypatch.setattr(
        "farm_merge_valet.gui.services.hotkeys.keyboard.remove_hotkey", removed_hotkeys.append
    )
    monkeypatch.setattr(
        "farm_merge_valet.gui.services.hotkeys.keyboard.unhook", removed_releases.append
    )
    manager = HotkeyManager()
    errors: list[str] = []
    manager.registration_failed.connect(errors.append)
    assert manager.start(HotkeyBindings("f8", "f9", "f10"))

    assert not manager.rebind(HotkeyBindings("ctrl+f8", "ctrl+f9", "ctrl+f10"))

    assert removed_hotkeys == ["handle-4"]
    assert removed_releases == ["release-5", "release-4"]
    assert errors == ["Could not register global hotkeys: shortcut unavailable"]


def test_hotkey_recorder_captures_combinations_and_can_clear() -> None:
    app = QApplication.instance() or QApplication([])
    editor = HotkeyEdit("f9", "Pause or resume global hotkey")
    values: list[object] = []
    recording: list[bool] = []
    editor.value_changed.connect(values.append)
    editor.recording_changed.connect(recording.append)

    editor._toggle_recording()
    editor.keyPressEvent(
        QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_X,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
            "x",
        )
    )
    assert editor.recording
    editor.keyReleaseEvent(
        QKeyEvent(
            QKeyEvent.Type.KeyRelease,
            Qt.Key.Key_X,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
            "x",
        )
    )

    assert editor.value == "ctrl+shift+x"
    assert editor.display.text() == "Ctrl+Shift+X"
    assert values == ["ctrl+shift+x"]
    assert recording == [True, False]

    editor.clear()
    assert editor.value is None
    assert values[-1] is None
    app.processEvents()


def test_application_hotkeys_work_while_bot_is_stopped(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig())
    controller = ApplicationController(store)
    starts: list[bool] = []
    stops: list[bool] = []
    quits: list[bool] = []
    monkeypatch.setattr(controller, "start_bot", lambda: starts.append(True))
    monkeypatch.setattr(controller, "stop_bot", lambda: stops.append(True))
    controller.application_quit_requested.connect(lambda: quits.append(True))

    controller.hotkeys.start_stop_requested.emit()
    controller.hotkeys.quit_requested.emit()
    controller.status = controller.status.__class__(state=ApplicationState.RUNNING)
    controller.hotkeys.start_stop_requested.emit()

    assert starts == [True]
    assert stops == [True]
    assert quits == [True]
    controller.shutdown()
    app.processEvents()


def test_start_stop_hotkey_can_cancel_startup(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    controller = ApplicationController(store)
    controller._worker = type("Worker", (), {"is_alive": lambda self: True})()
    controller._set_status(state=ApplicationState.STARTING)

    controller.toggle_running()

    assert controller.status.state is ApplicationState.STOPPING
    assert controller._stopping
    controller._worker = None
    controller.shutdown()
    app.processEvents()
