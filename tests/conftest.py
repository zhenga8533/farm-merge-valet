from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def use_isolated_runtime_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for variable in list(os.environ):
        if variable.upper().startswith("FMV_"):
            monkeypatch.delenv(variable)
