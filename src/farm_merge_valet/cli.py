"""Command-line entry point for Farm Merge Valet."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import cv2
import typer
from rich import print as rprint

from farm_merge_valet.capture.screen import capture_region
from farm_merge_valet.capture.window import WindowActivationError, find_window, list_window_titles
from farm_merge_valet.config import settings
from farm_merge_valet.core.bot import Bot
from farm_merge_valet.logging_setup import configure_logging

app = typer.Typer(help="Automation tool for Farm Merge Valley.")


@app.callback()
def main() -> None:
    configure_logging(settings.log_level)


@app.command()
def run() -> None:
    """Start the automation loop."""
    Bot().run_forever()


@app.command("list-windows")
def list_windows() -> None:
    """List open window titles, to help pick a value for FMV_WINDOW_TITLE."""
    for title in list_window_titles():
        typer.echo(title)


@app.command()
def calibrate() -> None:
    """Locate the configured target window and report its region."""
    try:
        region = find_window(settings.window_title)
    except (LookupError, WindowActivationError) as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    rprint(f"[green]Found window[/green]: {region}")


@app.command()
def capture(
    output: Path = typer.Option(  # noqa: B008
        None, "--output", "-o", help="Where to save the PNG (default: captures/<timestamp>.png)."
    ),
) -> None:
    """Save a screenshot of the configured target window, e.g. for building templates."""
    try:
        region = find_window(settings.window_title)
    except (LookupError, WindowActivationError) as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if output is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = Path("captures") / f"{timestamp}.png"
    output.parent.mkdir(parents=True, exist_ok=True)

    frame = capture_region(region)
    cv2.imwrite(str(output), frame)
    rprint(f"[green]Saved[/green] {frame.shape[1]}x{frame.shape[0]} capture to {output}")


@app.command()
def version() -> None:
    from farm_merge_valet import __version__

    rprint(__version__)


if __name__ == "__main__":
    app()
