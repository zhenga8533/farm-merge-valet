from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox


@pytest.fixture(autouse=True)
def fail_on_unexpected_message_box(monkeypatch: pytest.MonkeyPatch) -> None:
    # A modal box has no one to dismiss it in a test run, so it would hang the suite.
    def unexpected(_parent: object, title: str, text: str, *_args: object) -> None:
        raise AssertionError(f"Unexpected message box {title!r}: {text}")

    for name in ("critical", "information", "question", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(unexpected))
