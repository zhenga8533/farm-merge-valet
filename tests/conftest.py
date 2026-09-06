from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live-game",
        action="store_true",
        default=False,
        help="run opt-in tests against the configured live game browser",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_game: requires the configured managed browser and a loaded game",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live-game"):
        return
    skip_live = pytest.mark.skip(reason="use --live-game to run live game integration tests")
    for item in items:
        if item.get_closest_marker("live_game") is not None:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def use_isolated_runtime_environment(monkeypatch, tmp_path, request: pytest.FixtureRequest):
    if request.node.get_closest_marker("live_game") is not None:
        return
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for variable in list(os.environ):
        if variable.upper().startswith("FMV_"):
            monkeypatch.delenv(variable)
