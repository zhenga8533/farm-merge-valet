from __future__ import annotations

import logging

from farm_merge_valet.gui import overlay


class _Signal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in self._callbacks:
            callback(*args)


def test_run_overlay_keeps_qt_in_caller_and_bot_in_worker(monkeypatch) -> None:
    events: list[str] = []

    class FakeApp:
        def __init__(self) -> None:
            self.aboutToQuit = _Signal()

        def quit(self) -> None:
            events.append("quit")
            self.aboutToQuit.emit()

        def exec(self) -> int:
            events.append("qt-exec")
            return 0

    app = FakeApp()

    class FakeApplication:
        @staticmethod
        def instance() -> FakeApp:
            return app

    class FakeTimer:
        def __init__(self) -> None:
            self.timeout = _Signal()

        def setInterval(self, interval: int) -> None:
            assert interval == 100

        def start(self) -> None:
            events.append("timer-start")

        def stop(self) -> None:
            events.append("timer-stop")

    class FakeWindow:
        def append_log(
            self, _timestamp: str, _level_name: str, _message: str, _levelno: int
        ) -> None:
            pass

        def show(self) -> None:
            events.append("show")

        def _update_click_through(self) -> None:
            pass

    class FakeLogBridge:
        def __init__(self) -> None:
            self.new_record = _Signal()

    class FakeAppBridge:
        def __init__(self) -> None:
            self.bot_finished = _Signal()

    class FakeHandler:
        def __init__(self, _bridge) -> None:
            pass

        def setFormatter(self, _formatter) -> None:
            pass

        def setLevel(self, _level) -> None:
            pass

    class FakeThread:
        def __init__(self, *, target, daemon: bool, name: str) -> None:
            assert daemon
            assert name == "fmv-bot"
            self.target = target

        def start(self) -> None:
            events.append("worker-start")
            self.target()

        def join(self, timeout: int) -> None:
            assert timeout == 5
            events.append("worker-join")

    monkeypatch.setattr(overlay, "QApplication", FakeApplication)
    monkeypatch.setattr(overlay, "QTimer", FakeTimer)
    monkeypatch.setattr(overlay, "OverlayWindow", FakeWindow)
    monkeypatch.setattr(overlay, "_LogBridge", FakeLogBridge)
    monkeypatch.setattr(overlay, "_AppBridge", FakeAppBridge)
    monkeypatch.setattr(overlay, "QtLogHandler", FakeHandler)
    monkeypatch.setattr(overlay.threading, "Thread", FakeThread)

    result = overlay.run_overlay(
        lambda: events.append("bot-run"),
        lambda: events.append("bot-stop"),
    )

    assert result == 0
    assert events.index("worker-start") < events.index("qt-exec")
    assert "bot-run" in events
    assert events.count("bot-stop") == 1
    assert events[-1] == "worker-join"


def test_log_handler_forwards_structured_level_information() -> None:
    class Bridge:
        def __init__(self) -> None:
            self.new_record = _Signal()

    bridge = Bridge()
    received = []
    bridge.new_record.connect(lambda *parts: received.append(parts))
    handler = overlay.QtLogHandler(bridge)
    handler.setFormatter(logging.Formatter("%(message)s"))
    record = logging.LogRecord("test", logging.WARNING, "", 0, "Watch out", (), None)
    record.created = 0

    handler.emit(record)

    assert len(received) == 1
    assert received[0][1:] == ("WARNING", "Watch out", logging.WARNING)


def test_gui_log_level_styles_match_rich_level_semantics() -> None:
    assert overlay._log_level_style(logging.NOTSET).foreground == "gray"
    assert overlay._log_level_style(logging.DEBUG).foreground == "green"
    assert overlay._log_level_style(logging.INFO).foreground == "blue"
    assert overlay._log_level_style(logging.WARNING).foreground == "yellow"
    assert overlay._log_level_style(logging.ERROR) == overlay._LogLevelStyle("red", bold=True)
    assert overlay._log_level_style(logging.CRITICAL) == overlay._LogLevelStyle(
        "#e0e0e0", bold=True, background="red"
    )
