"""File replacement shared by atomic configuration writes."""

from __future__ import annotations

import os
import time
from pathlib import Path

# Windows refuses to replace a file while another process, such as antivirus or the
# search indexer, briefly holds it open. Those locks clear within milliseconds.
_WINDOWS_RETRY_DELAYS = (0.01, 0.05, 0.1, 0.25, 0.5)


def replace_file(source: Path, target: Path) -> None:
    if os.name == "nt":
        for delay in _WINDOWS_RETRY_DELAYS:
            try:
                source.replace(target)
            except PermissionError:
                time.sleep(delay)
            else:
                return
    source.replace(target)
