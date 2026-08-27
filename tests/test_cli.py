from typer.testing import CliRunner

from farm_merge_valet.cli import _config_store, app
from farm_merge_valet.config import AppConfig


def test_help_groups_user_and_diagnostic_workflows() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "browser" in result.stdout
    assert "assets" in result.stdout
    assert "diagnostics" in result.stdout
    assert "sync-assets" not in result.stdout
    assert "diagnose-live-state" not in result.stdout


def test_legacy_run_alias_remains_available(monkeypatch) -> None:
    monkeypatch.setattr(_config_store, "load", AppConfig)
    monkeypatch.setattr(
        "farm_merge_valet.gui.application.run_application",
        lambda _store: 0,
    )

    result = CliRunner().invoke(app, ["run"])

    assert result.exit_code == 0
    assert "deprecated" in result.stderr
