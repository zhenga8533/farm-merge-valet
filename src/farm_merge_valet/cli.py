"""Command-line entry point for Farm Merge Valet."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import typer
from rich import print as rprint

from farm_merge_valet.browser import BrowserKind, BrowserManager, BrowserManagerError
from farm_merge_valet.cdp.profiling import profile_runtime, write_profile
from farm_merge_valet.cdp.runtime import GameRuntimeAdapter
from farm_merge_valet.composition import create_catalog_synchronizer
from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.observability.logging import configure_logging

app = typer.Typer(help="Automation tool for Farm Merge Valley.", invoke_without_command=True)
browser_app = typer.Typer(help="Manage the dedicated Chromium-family browser.")
assets_app = typer.Typer(help="Synchronize and rebuild cached game assets.")
diagnostics_app = typer.Typer(help="Run read-only runtime diagnostics.")
app.add_typer(browser_app, name="browser")
app.add_typer(assets_app, name="assets")
app.add_typer(diagnostics_app, name="diagnostics")

logger = logging.getLogger(__name__)
_config_store = ConfigStore()


@app.callback()
def main(ctx: typer.Context) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    if ctx.invoked_subcommand is None:
        from farm_merge_valet.gui.application import run_application

        raise typer.Exit(run_application(_config_store))
    if any(argument in {"--help", "-h"} for argument in sys.argv[1:]):
        return
    config = _config_store.load()
    configure_logging(config.log_level)


def _browser_manager(browser: str | None) -> BrowserManager:
    try:
        return BrowserManager(_config(), BrowserKind(browser) if browser else None)
    except (ValueError, BrowserManagerError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--browser") from exc


def _catalog_synchronizer():
    return create_catalog_synchronizer(_config())


def _config() -> AppConfig:
    return _config_store.current


def _print_browser_status(manager: BrowserManager) -> None:
    typer.echo(json.dumps(manager.status_dict(manager.status()), indent=2))


@browser_app.command("list")
def browser_list_cmd() -> None:
    """List installed supported and experimental browser candidates."""
    installations = BrowserManager(_config()).installations()
    if not installations:
        typer.echo("No compatible Chromium-family browser was detected.")
        return
    for installation in installations:
        typer.echo(
            f"{installation.kind.value:<9} {installation.support.value:<12} "
            f"{installation.executable}"
        )


@browser_app.command("status")
def browser_status_cmd(
    browser: str | None = typer.Option(None, "--browser"),  # noqa: B008
) -> None:
    """Inspect the browser currently exposed on the configured CDP port."""
    _print_browser_status(_browser_manager(browser))


@browser_app.command("launch")
def browser_launch_cmd(
    browser: str | None = typer.Option(None, "--browser"),  # noqa: B008
) -> None:
    """Launch or reuse a compatible dedicated browser profile."""
    manager = _browser_manager(browser)
    try:
        status = manager.launch()
    except BrowserManagerError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(manager.status_dict(status), indent=2))


@browser_app.command("restart")
def browser_restart_cmd(
    browser: str | None = typer.Option(None, "--browser"),  # noqa: B008
) -> None:
    """Restart only a verified Farm Merge Valet-managed browser."""
    manager = _browser_manager(browser)
    try:
        status = manager.restart()
    except BrowserManagerError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(manager.status_dict(status), indent=2))


@browser_app.command("stop")
def browser_stop_cmd(
    browser: str | None = typer.Option(None, "--browser"),  # noqa: B008
) -> None:
    """Stop only a verified Farm Merge Valet-managed browser."""
    manager = _browser_manager(browser)
    try:
        manager.stop()
    except BrowserManagerError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    rprint("[green]Managed browser stopped.[/green]")


@assets_app.command("extract")
def extract_templates_cmd(
    har: Path = typer.Argument(...),  # noqa: B008
    force: bool = False,
) -> None:
    try:
        _catalog_synchronizer().extract(har, force=force)
    except RuntimeError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@assets_app.command("compile")
def compile_assets_cmd() -> None:
    """Rebuild the item catalog and assets from the cached game atlases."""
    try:
        _catalog_synchronizer().compile_cached()
    except RuntimeError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@assets_app.command("sync")
def sync_assets_cmd(force: bool = False) -> None:
    """Discover and compile current game assets into the per-user cache."""
    try:
        _catalog_synchronizer().sync(force=force)
    except RuntimeError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command()
def version() -> None:
    from farm_merge_valet import __version__

    rprint(__version__)


@diagnostics_app.command("profile-runtime")
def profile_runtime_cmd(
    duration: float = typer.Option(120.0, min=1.0),  # noqa: B008
    output: Path = typer.Option(Path(".tmp/runtime-profile.json")),  # noqa: B008
) -> None:
    """Profile cached live-state reads without discovery or game actions."""
    runtime = GameRuntimeAdapter(_config().cdp_port, _config().window_title)
    try:
        report = profile_runtime(runtime, duration_seconds=duration)
    except RuntimeError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    serialized = json.dumps(report, indent=2)
    write_profile(output, serialized)
    typer.echo(serialized)


if __name__ == "__main__":
    app()
