from __future__ import annotations

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

        def exec(self) -> int:
            events.append("qt-exec")
            return 0

    app = FakeApp()

    class FakeApplication:
        @staticmethod
        def instance() -> FakeApp:
            return app

    class FakeWindow:
        def append_log(self, _message: str) -> None:
            pass

        def show(self) -> None:
            events.append("show")

        def _update_click_through(self) -> None:
            pass

    class FakeLogBridge:
        def __init__(self) -> None:
            self.new_line = _Signal()

    class FakeAppBridge:
        def __init__(self) -> None:
            self.bot_finished = _Signal()

    class FakeHandler:
        def __init__(self, _bridge) -> None:
            pass

        def setFormatter(self, _formatter) -> None:
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
    assert events[-2:] == ["bot-stop", "worker-join"]
