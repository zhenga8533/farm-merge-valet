from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

import pytest

import farm_merge_valet


def _checkout(root: Path, *, name: str = "farm-merge-valet", version: str = "1.2.3") -> Path:
    package = root / "src" / "farm_merge_valet"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8"
    )
    return package


def _installed_as(monkeypatch, version: str) -> None:
    monkeypatch.setattr(importlib.metadata, "version", lambda name: version)


def test_source_checkout_prefers_pyproject_over_stale_install_metadata(
    tmp_path, monkeypatch
) -> None:
    _installed_as(monkeypatch, "0.1.1")

    assert farm_merge_valet._resolve_version(_checkout(tmp_path)) == "1.2.3"


def test_installed_package_uses_distribution_metadata(tmp_path, monkeypatch) -> None:
    _installed_as(monkeypatch, "9.8.7")
    site_packages_copy = tmp_path / "site-packages" / "farm_merge_valet"

    assert farm_merge_valet._resolve_version(site_packages_copy) == "9.8.7"


def test_pyproject_belonging_to_another_project_is_ignored(tmp_path, monkeypatch) -> None:
    _installed_as(monkeypatch, "9.8.7")

    assert farm_merge_valet._resolve_version(_checkout(tmp_path, name="other")) == "9.8.7"


def test_unreadable_pyproject_falls_back_to_distribution_metadata(tmp_path, monkeypatch) -> None:
    _installed_as(monkeypatch, "9.8.7")
    package = _checkout(tmp_path)
    (tmp_path / "pyproject.toml").write_text("not [valid toml", encoding="utf-8")

    assert farm_merge_valet._resolve_version(package) == "9.8.7"


def test_missing_pyproject_falls_back_to_distribution_metadata(tmp_path, monkeypatch) -> None:
    _installed_as(monkeypatch, "9.8.7")
    package = tmp_path / "src" / "farm_merge_valet"
    package.mkdir(parents=True)

    assert farm_merge_valet._resolve_version(package) == "9.8.7"


def test_unknown_version_when_neither_source_is_available(tmp_path, monkeypatch) -> None:
    def unavailable(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", unavailable)

    assert farm_merge_valet._resolve_version(tmp_path / "farm_merge_valet") == "0.0.0+unknown"


def test_running_checkout_reports_its_own_pyproject_version() -> None:
    root = Path(farm_merge_valet.__file__).resolve().parents[2]
    if not (root / "pyproject.toml").is_file():
        pytest.skip("Not running from a source checkout")
    expected = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]

    assert farm_merge_valet.__version__ == expected
