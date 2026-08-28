from typer.testing import CliRunner

from farm_merge_valet.cli import app


def test_help_groups_user_workflows() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "browser" in result.stdout
    assert "assets" in result.stdout
    assert "diagnostics" not in result.stdout
    assert "sync-assets" not in result.stdout
    assert "diagnose-live-state" not in result.stdout


def test_removed_flat_command_aliases_are_not_commands() -> None:
    for command in (
        "run",
        "diagnostics",
        "list-targets",
        "capture",
        "visualize-positions",
        "diagnose-live-state",
        "extract-templates",
        "compile-assets",
        "sync-assets",
    ):
        result = CliRunner().invoke(app, [command])
        assert result.exit_code != 0, command
