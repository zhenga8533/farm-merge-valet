from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from farm_merge_valet.browser.manager import BrowserKind, BrowserStatus
from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.gui.controller import ApplicationController


def test_browser_refresh_runs_off_the_gui_thread(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig())
    controller = ApplicationController(store)
    release = threading.Event()
    worker_threads: list[int] = []
    busy_states: list[bool] = []
    statuses: list[str] = []

    class BrowserManagerStub:
        def __init__(self, _config: AppConfig) -> None:
            pass

        def status(self) -> BrowserStatus:
            worker_threads.append(threading.get_ident())
            release.wait(2)
            return BrowserStatus(
                running=True,
                compatible=True,
                managed=True,
                kind=BrowserKind.CHROME,
                game_loaded=True,
            )

    monkeypatch.setattr(
        "farm_merge_valet.gui.controller.BrowserManager",
        BrowserManagerStub,
    )
    controller.browser_operation_changed.connect(busy_states.append)
    controller.browser_status_changed.connect(statuses.append)

    started = time.monotonic()
    controller.refresh_browser()
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert busy_states == [True]
    release.set()
    deadline = time.monotonic() + 2
    while len(busy_states) < 2 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert worker_threads and worker_threads[0] != threading.get_ident()
    assert busy_states == [True, False]
    assert statuses[-1] == "chrome ready · game loaded"
    controller.shutdown()
    app.processEvents()
