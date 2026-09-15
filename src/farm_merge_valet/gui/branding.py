"""Application-owned branding assets."""

from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QIcon

_ICON_PATH = Path(__file__).with_name("assets") / "app-icon.png"


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    icon = QIcon(str(_ICON_PATH))
    if icon.isNull():
        raise RuntimeError(f"Application icon could not be loaded from {_ICON_PATH}")
    return icon
