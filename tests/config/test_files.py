from __future__ import annotations

from pathlib import Path

import pytest

from farm_merge_valet.config import AppConfig, ConfigStore, files


@pytest.fixture
def windows_replace(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []
    monkeypatch.setattr(files.os, "name", "nt")
    monkeypatch.setattr(files.time, "sleep", delays.append)
    return delays


def _locked_for(monkeypatch: pytest.MonkeyPatch, attempts: int) -> None:
    original = Path.replace
    failures = iter(range(attempts))

    def replace(self: Path, target: Path) -> Path:
        if next(failures, None) is not None:
            raise PermissionError(5, "Access is denied")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", replace)


def test_briefly_locked_config_is_still_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows_replace: list[float]
) -> None:
    store = ConfigStore(tmp_path / "config.json")
    _locked_for(monkeypatch, 2)

    store.replace(AppConfig(loop_interval=2.0))

    assert ConfigStore(store.path).load().loop_interval == 2.0
    assert windows_replace == [0.01, 0.05]
    assert not list(tmp_path.glob("*.tmp"))


def test_persistently_locked_config_reports_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows_replace: list[float]
) -> None:
    store = ConfigStore(tmp_path / "config.json")
    _locked_for(monkeypatch, 100)

    with pytest.raises(PermissionError):
        store.replace(AppConfig(loop_interval=2.0))

    assert len(windows_replace) == len(files._WINDOWS_RETRY_DELAYS)
    assert not list(tmp_path.glob("*.tmp"))
