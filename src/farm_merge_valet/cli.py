"""Command-line entry point for Farm Merge Valet."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import typer
from rich import print as rprint

from farm_merge_valet.capture.screen import capture_region
from farm_merge_valet.capture.window import WindowActivationError, find_window, list_window_titles
from farm_merge_valet.cdp.board_store import arm_board_store, inspect_board_maps, read_board_state
from farm_merge_valet.cdp.client import CdpConnectionError
from farm_merge_valet.cdp.scene_geometry import read_scene_calibration
from farm_merge_valet.config import settings
from farm_merge_valet.core.board import CellKind
from farm_merge_valet.core.bot import Bot
from farm_merge_valet.core.viewport import viewport_layout
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
    bot = Bot()
    if settings.gui_enabled:
        from farm_merge_valet.gui.overlay import run_overlay

        run_overlay(bot.run_forever, bot.request_quit)
    else:
        bot.run_forever()


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
    """Visualize actionable positions and viewport safety zones.

    Dead zones are shaded red, the usable board is outlined green, and the
    pan anchor is marked blue. Item circles are drawn only when their computed
    positions are on-screen and outside every dead zone.
    """
    try:
        region = find_window(settings.window_title)
    except (LookupError, WindowActivationError) as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    frame = capture_region(region)
    bot = Bot()
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
    layout = viewport_layout(frame.shape[1], frame.shape[0])
    dead_zone_overlay = annotated.copy()
    for dead_zone in layout.dead_zones:
        cv2.rectangle(
            dead_zone_overlay,
            (dead_zone.left, dead_zone.top),
            (dead_zone.right - 1, dead_zone.bottom - 1),
            (0, 0, 255),
            -1,
        )
    cv2.addWeighted(dead_zone_overlay, 0.18, annotated, 0.82, 0, annotated)
    cv2.rectangle(
        annotated,
        (layout.board.left, layout.board.top),
        (layout.board.right - 1, layout.board.bottom - 1),
        (0, 255, 0),
        2,
    )
    cv2.drawMarker(
        annotated,
        layout.pan_anchor,
        (255, 128, 0),
        cv2.MARKER_CROSS,
        24,
        2,
    )
    drawn = 0
    classified_out = 0
    outside_actionable_area = 0
    for coord in sorted(bot._scene_calibration.rendered_coords):
        cell = bot.board.get_cell(coord)
        if cell is not None and cell.kind in (CellKind.EMPTY, CellKind.CLOUD):
            classified_out += 1
            continue
        x, y = bot._grid_to_pixel(coord)
        if not layout.is_actionable(x, y):
            outside_actionable_area += 1
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
        f"{drawn} marked position(s) to {output} "
        f"(rendered_cells={len(bot._scene_calibration.rendered_coords)}, "
        f"classified_out={classified_out}, outside={outside_actionable_area}, "
        f"live_synced={live_synced})"
    )


@app.command("diagnose-live-state")
def diagnose_live_state_cmd(
    output: Path = typer.Option(  # noqa: B008
        None,
        "--output",
        "-o",
        help="Where to save the JSON report (default: captures/<timestamp>_live-state.json).",
    ),
) -> None:
    """Capture read-only board-map and coordinate-transform diagnostics."""
    try:
        region = find_window(settings.window_title)
    except (LookupError, WindowActivationError) as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    frame = capture_region(region)
    try:
        arm_status = arm_board_store(settings.cdp_port, settings.window_title)
        candidates = inspect_board_maps(settings.cdp_port, settings.window_title)
        board_state = read_board_state(settings.cdp_port, settings.window_title)
        calibration = read_scene_calibration(settings.cdp_port, region, settings.window_title)
    except CdpConnectionError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    layout = viewport_layout(frame.shape[1], frame.shape[0])

    rendered_coords = sorted(calibration.rendered_coords) if calibration else []
    projected = [calibration.to_pixel(coord) for coord in rendered_coords] if calibration else []

    def value_range(values: list[int]) -> dict[str, int] | None:
        return {"min": min(values), "max": max(values)} if values else None

    report = {
        "capture": {"width": frame.shape[1], "height": frame.shape[0]},
        "window_region": {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        },
        "arm_status": arm_status,
        "candidate_maps": candidates,
        "selected_board": {
            "reported_cells": len(board_state) if board_state is not None else None,
            "open_cells": (
                sum(not state.has_content for state in board_state.values())
                if board_state is not None
                else None
            ),
            "common_blueprints": Counter(
                state.blueprint_id
                for state in (board_state or {}).values()
                if state.blueprint_id is not None
            ).most_common(20),
        },
        "scene": {
            "rendered_cells": len(rendered_coords),
            "origin": calibration.origin if calibration else None,
            "column_step": calibration.col_step if calibration else None,
            "row_step": calibration.row_step if calibration else None,
            "canvas_scale": calibration.canvas_scale if calibration else None,
            "canvas_offset": calibration.canvas_offset if calibration else None,
            "projected_x_range": value_range([point[0] for point in projected]),
            "projected_y_range": value_range([point[1] for point in projected]),
            "actionable_projected_cells": sum(layout.is_actionable(*point) for point in projected),
        },
    }

    if output is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = Path("captures") / f"{timestamp}_live-state.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    rprint(f"[green]Saved live-state diagnostics[/green] to {output}")


@app.command("extract-templates")
def extract_templates_cmd(
    har: Path = typer.Argument(  # noqa: B008
        ...,
        help="Path to a HAR capture of the game's network traffic (DevTools -> Network -> "
        "Img filter -> 'Save all as HAR').",
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
