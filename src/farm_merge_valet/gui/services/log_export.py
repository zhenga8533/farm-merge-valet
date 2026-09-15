"""Persist the visible GUI log safely."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def save_visible_log(path: Path, text: str) -> Path:
    """Atomically save exactly the text displayed by the Logs page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"{text}\n" if text and not text.endswith("\n") else text
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path
