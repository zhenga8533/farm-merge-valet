from pathlib import Path

import pytest

from farm_merge_valet.gui.services.log_export import save_visible_log


def test_save_visible_log_writes_utf8_atomically(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "farm-merge-valet.log"

    assert save_visible_log(output, "Started · ready") == output
    assert output.read_text(encoding="utf-8") == "Started · ready\n"
    assert not tuple(output.parent.glob("*.tmp"))


def test_save_visible_log_preserves_empty_content(tmp_path: Path) -> None:
    output = tmp_path / "empty.log"

    save_visible_log(output, "")

    assert output.read_bytes() == b""


def test_save_visible_log_cleans_temporary_file_on_failure(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "application.log"
    monkeypatch.setattr(Path, "replace", lambda *_args: (_ for _ in ()).throw(OSError("full")))

    with pytest.raises(OSError, match="full"):
        save_visible_log(output, "message")

    assert not tuple(tmp_path.glob("*.tmp"))
