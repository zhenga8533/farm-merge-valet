from __future__ import annotations

import importlib
import importlib.metadata

import farm_merge_valet


def test_version_comes_from_distribution_metadata(monkeypatch) -> None:
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.8.7")
    assert importlib.reload(farm_merge_valet).__version__ == "9.8.7"


def test_version_has_source_tree_fallback(monkeypatch) -> None:
    def unavailable(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", unavailable)
    assert importlib.reload(farm_merge_valet).__version__ == "0.0.0+unknown"
