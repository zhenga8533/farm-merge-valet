"""Command-line entry point for Farm Merge Valet."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import cv2
import typer
from rich import print as rprint

from farm_merge_valet.browser import BrowserKind, BrowserManager, BrowserManagerError
from farm_merge_valet.cdp.client import CdpConnectionError, list_targets
from farm_merge_valet.config import ConfigStore, settings
from farm_merge_valet.diagnostics import (
    build_position_visualization,
    capture_game_image,
    export_support_bundle,
    write_live_state_report,
)
from farm_merge_valet.logging_setup import configure_logging
from farm_merge_valet.tools.template_extraction import (
    compile_cached_assets,
    extract_templates,
    sync_runtime_assets,
)

app = typer.Typer(help="Automation tool for Farm Merge Valley.", invoke_without_command=True)
browser_app = typer.Typer(help="Manage the dedicated Chromium-family browser.")
assets_app = typer.Typer(help="Synchronize and rebuild cached game assets.")
diagnostics_app = typer.Typer(help="Capture runtime information for troubleshooting.")
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
    settings.replace(config)
    configure_logging(config.log_level)


def _default_output(suffix: str) -> Path:
    return Path("captures") / f"{datetime.now():%Y%m%d-%H%M%S}{suffix}"


@app.command(hidden=True)
def run() -> None:
    """Launch the desktop application (deprecated compatibility alias)."""
    typer.echo("The 'run' alias is deprecated; use 'farm-merge-valet'.", err=True)
    from farm_merge_valet.gui.application import run_application

    raise typer.Exit(run_application(_config_store))


def _browser_manager(browser: str | None) -> BrowserManager:
    try:
        return BrowserManager(settings.snapshot(), BrowserKind(browser) if browser else None)
    except (ValueError, BrowserManagerError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--browser") from exc


def _print_browser_status(manager: BrowserManager) -> None:
    typer.echo(json.dumps(manager.status_dict(manager.status()), indent=2))


@browser_app.command("list")
def browser_list_cmd() -> None:
    """List installed supported and experimental browser candidates."""
    installations = BrowserManager(settings.snapshot()).installations()
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


@diagnostics_app.command("targets")
@app.command("list-targets", hidden=True)
def list_targets_cmd() -> None:
    """List browser DevTools targets available on the configured port."""
    try:
        for target in list_targets(settings.cdp_port):
            typer.echo(f"{target['type']:<10} {target['title'] or '-'}\n  {target['url'] or '-'}")
    except CdpConnectionError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@diagnostics_app.command("capture")
@app.command("capture", hidden=True)
def capture(output: Path | None = typer.Option(None, "--output", "-o")) -> None:  # noqa: B008
    """Save a CDP screenshot cropped to the game iframe."""
    try:
        frame = capture_game_image(settings.snapshot())
    except (CdpConnectionError, RuntimeError) as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    output = output or _default_output(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), frame)
    rprint(f"[green]Saved[/green] {frame.shape[1]}x{frame.shape[0]} game capture to {output}")


@diagnostics_app.command("positions")
@app.command("visualize-positions", hidden=True)
def visualize_positions_cmd(
    output: Path | None = typer.Option(None, "--output", "-o"),  # noqa: B008
) -> None:
    """Mark rendered cells by their current live-state classification."""
    try:
        frame, synced, _counts = build_position_visualization(settings.snapshot())
    except (CdpConnectionError, RuntimeError) as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    output = output or _default_output("_positions.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), frame)
    rprint(f"[green]Saved[/green] classified positions to {output} (live_synced={synced})")


@diagnostics_app.command("live-state")
@app.command("diagnose-live-state", hidden=True)
def diagnose_live_state_cmd(
    output: Path | None = typer.Option(None, "--output", "-o"),  # noqa: B008
    include_heap_candidates: bool = typer.Option(
        False,
        "--include-heap-candidates",
        help="Run the slower heap-wide board-map candidate inspection.",
    ),
) -> None:
    """Write board, runtime-capability, heartbeat, and scene diagnostics."""
    try:
        output = output or _default_output("_live-state.json")
        write_live_state_report(
            settings.snapshot(),
            output,
            include_heap_candidates=include_heap_candidates,
        )
    except (CdpConnectionError, RuntimeError) as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    rprint(f"[green]Saved live-state diagnostics[/green] to {output}")


@assets_app.command("extract")
@app.command("extract-templates", hidden=True)
def extract_templates_cmd(
    har: Path = typer.Argument(...),  # noqa: B008
    force: bool = False,
) -> None:
    try:
        extract_templates(har, force=force)
    except RuntimeError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@assets_app.command("compile")
@app.command("compile-assets", hidden=True)
def compile_assets_cmd() -> None:
    """Rebuild the item catalog and assets from the cached game atlases."""
    try:
        compile_cached_assets()
    except RuntimeError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@assets_app.command("sync")
@app.command("sync-assets", hidden=True)
def sync_assets_cmd(force: bool = False) -> None:
    """Discover and compile current game assets into the per-user cache."""
    try:
        sync_runtime_assets(force=force)
    except RuntimeError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@diagnostics_app.command("export")
def export_diagnostics_cmd(
    output: Path | None = typer.Option(None, "--output", "-o"),  # noqa: B008
    include_screenshot: bool = typer.Option(False, "--include-screenshot"),
) -> None:
    """Create a sanitized support bundle that remains useful when the game is offline."""
    output = output or _default_output("_diagnostics.zip")
    try:
        saved = export_support_bundle(
            settings.snapshot(),
            output,
            include_screenshot=include_screenshot,
        )
    except (OSError, RuntimeError) as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    rprint(f"[green]Saved support diagnostics[/green] to {saved}")


@app.command()
def version() -> None:
    from farm_merge_valet import __version__

    rprint(__version__)


if __name__ == "__main__":
    app()
