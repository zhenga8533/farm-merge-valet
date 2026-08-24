from __future__ import annotations

import os

import pytest

from farm_merge_valet.config import Settings, settings


@pytest.fixture(autouse=True)
def use_default_runtime_settings(monkeypatch):
    for variable in list(os.environ):
        if variable.upper().startswith("FMV_"):
            monkeypatch.delenv(variable)
    defaults = Settings(_env_file=None)
    for field_name in Settings.model_fields:
        monkeypatch.setattr(settings, field_name, getattr(defaults, field_name))
