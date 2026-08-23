"""Command-line entry point for Farm Merge Valet."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import cv2
import typer
from rich import print as rprint

from farm_merge_valet.capture.screen import capture_region
from farm_merge_valet.capture.window import WindowActivationError, find_window, list_window_titles
from farm_merge_valet.config import settings
from farm_merge_valet.core.board import CellKind
from farm_merge_valet.core.bot import Bot
from farm_merge_valet.logging_setup import configure_logging
from farm_merge_valet.tools.template_extraction import extract_templates

app = typer.Typer(help="Automation tool for Farm Merge Valley.")


@app.callback()
def main() -> None:
    # Window titles (and other OS-provided strings we echo back, e.g. in
    # error messages) can contain characters the Windows console's legacy
    # codepage can't encode, which otherwise crashes the CLI on a plain
    # print. Replacing unencodable characters keeps the message readable
    # instead of losing it to a traceback.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    configure_logging(settings.log_level)


@app.command()
def run() -> None:
    """Start the automation loop."""
    if settings.gui_enabled:
        from farm_merge_valet.gui.overlay import start_overlay

        start_overlay()
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


@app.command("visualize-positions")
def visualize_positions_cmd(
    output: Path = typer.Option(  # noqa: B008
        None,
        "--output",
        "-o",
        help="Where to save the annotated PNG (default: captures/<timestamp>_positions.png).",
    ),
) -> None:
    """Capture the current viewport and mark every known non-empty,
    non-cloud cell's computed screen position with a red circle -- a
    visual sanity check of item detection (`cdp/board_store.py`) and
    grid-to-pixel calibration (`cdp/scene_geometry.py`) against what's
    actually rendered.
    """
    try:
        region = find_window(settings.window_title)
    except (LookupError, WindowActivationError) as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    frame = capture_region(region)
    bot = Bot()
    bot._ensure_item_scale(frame)
    bot._ensure_board_store_armed()
    live_synced = bot._sync_board_from_live_state()
    bot._refresh_calibration(region)
    if bot._scene_calibration is None:
        rprint(
            "[red]Could not read scene calibration (CDP unreachable, live board-cell map "
            "not armed yet, or nothing on screen yet to derive geometry from).[/red]"
        )
        raise typer.Exit(code=1)

    annotated = frame.copy()
    drawn = 0
    for coord in bot.board.known_coords():
        cell = bot.board.get_cell(coord)
        if cell is None or cell.kind in (CellKind.EMPTY, CellKind.CLOUD):
            continue
        x, y = bot._grid_to_pixel(coord)
        if not (0 <= x < annotated.shape[1] and 0 <= y < annotated.shape[0]):
            continue
        cv2.circle(annotated, (x, y), 12, (0, 0, 255), 2)
        drawn += 1

    if output is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = Path("captures") / f"{timestamp}_positions.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), annotated)
    rprint(
        f"[green]Saved[/green] {frame.shape[1]}x{frame.shape[0]} capture with "
        f"{drawn} marked position(s) to {output} (live_synced={live_synced})"
    )


@app.command("extract-templates")
def extract_templates_cmd(
    har: Path = typer.Argument(  # noqa: B008
        ..., help="Path to a HAR capture of the game's network traffic (DevTools -> Network -> "
        "Img filter -> 'Save all as HAR')."
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-download atlases even if already cached."
    ),
) -> None:
    """Rebuild vision templates from the game's own sprite atlases.

    Re-run whenever the game updates and templates need refreshing; capture
    a fresh HAR first since the CDN URLs are tied to the current game
    instance/session and won't stay valid indefinitely.
    """
    try:
        extract_templates(har, force=force)
    except RuntimeError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command()
def version() -> None:
    from farm_merge_valet import __version__

    rprint(__version__)


if __name__ == "__main__":
    app()
