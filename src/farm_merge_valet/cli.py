"""Command-line entry point for Farm Merge Valet."""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import typer
from rich import print as rprint

from farm_merge_valet.browser import BrowserKind, BrowserManager, BrowserManagerError
from farm_merge_valet.cdp.board_store import arm_board_store, inspect_board_maps, read_board_state
from farm_merge_valet.cdp.client import (
    CdpConnectionError,
    capture_game_frame,
    list_targets,
    read_background_flag_status,
)
from farm_merge_valet.cdp.runtime import GameRuntimeAdapter
from farm_merge_valet.cdp.scene_geometry import read_scene_calibration
from farm_merge_valet.config import settings
from farm_merge_valet.core.board import CellKind
from farm_merge_valet.core.bot import Bot
from farm_merge_valet.logging_setup import configure_logging, log_event
from farm_merge_valet.observability.discord import discord_webhook_sink
from farm_merge_valet.tools.template_extraction import (
    compile_cached_assets,
    extract_templates,
    sync_runtime_assets,
)

app = typer.Typer(help="Automation tool for Farm Merge Valley.")
browser_app = typer.Typer(help="Manage the dedicated Chromium-family browser.")
app.add_typer(browser_app, name="browser")

logger = logging.getLogger(__name__)


@app.callback()
def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    configure_logging(settings.log_level)


def _default_output(suffix: str) -> Path:
    return Path("captures") / f"{datetime.now():%Y%m%d-%H%M%S}{suffix}"


def _capture_image() -> np.ndarray:
    encoded = np.frombuffer(capture_game_frame(settings.cdp_port, settings.window_title), np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise CdpConnectionError("Could not decode the browser's game screenshot.")
    return image


@app.command()
def run() -> None:
    webhook_url = (
        settings.discord_webhook_url.get_secret_value() if settings.discord_webhook_url else None
    )
    if webhook_url is not None and not webhook_url.startswith("https://"):
        log_event(
            logger,
            logging.ERROR,
            "webhook.invalid_url",
            "Discord webhook URL must use HTTPS; refusing to start.",
        )
        raise typer.Exit(code=2)
    with discord_webhook_sink(
        webhook_url,
        settings.webhook_summary_interval,
        settings.webhook_status_interval,
    ):
        _run_bot()


def _run_bot() -> None:
    if settings.browser_auto_launch:
        try:
            status = BrowserManager(settings).ensure_running()
        except BrowserManagerError as exc:
            log_event(
                logger,
                logging.ERROR,
                "browser.startup_failed",
                "Managed browser startup failed: %s",
                exc,
                detail=str(exc),
            )
            raise typer.Exit(code=1) from exc
        browser_name = status.kind.value if status.kind else "browser"
        log_event(
            logger,
            logging.INFO,
            "browser.ready",
            "Managed browser ready: %s (game_loaded=%s).",
            browser_name,
            status.game_loaded,
            browser=browser_name,
            game_loaded=status.game_loaded,
            managed=status.managed,
        )
    bot = Bot()
    if settings.gui_enabled:
        from farm_merge_valet.gui.overlay import run_overlay

        run_overlay(bot.run_forever, bot.request_quit)
    else:
        bot.run_forever()


def _browser_manager(browser: str | None) -> BrowserManager:
    try:
        return BrowserManager(settings, BrowserKind(browser) if browser else None)
    except (ValueError, BrowserManagerError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--browser") from exc


def _print_browser_status(manager: BrowserManager) -> None:
    typer.echo(json.dumps(manager.status_dict(manager.status()), indent=2))


@browser_app.command("list")
def browser_list_cmd() -> None:
    """List installed supported and experimental browser candidates."""
    installations = BrowserManager(settings).installations()
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


@app.command("list-targets")
def list_targets_cmd() -> None:
    """List browser DevTools targets available on the configured port."""
    try:
        for target in list_targets(settings.cdp_port):
            typer.echo(f"{target['type']:<10} {target['title'] or '-'}\n  {target['url'] or '-'}")
    except CdpConnectionError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command()
def capture(output: Path | None = typer.Option(None, "--output", "-o")) -> None:  # noqa: B008
    """Save a CDP screenshot cropped to the game iframe."""
    try:
        frame = _capture_image()
    except CdpConnectionError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    output = output or _default_output(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), frame)
    rprint(f"[green]Saved[/green] {frame.shape[1]}x{frame.shape[0]} game capture to {output}")


@app.command("visualize-positions")
def visualize_positions_cmd(
    output: Path | None = typer.Option(None, "--output", "-o"),  # noqa: B008
) -> None:
    """Mark rendered cells by their current live-state classification."""
    try:
        frame = _capture_image()
        bot = Bot()
        arm_board_store(settings.cdp_port, settings.window_title)
        synced = bot._sync_board_from_live_state()
        calibration = read_scene_calibration(settings.cdp_port, settings.window_title)
    except CdpConnectionError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if calibration is None:
        rprint("[red]Could not derive live scene geometry.[/red]")
        raise typer.Exit(code=1)
    colors = {
        CellKind.ITEM: (40, 220, 40),
        CellKind.EMPTY: (220, 180, 40),
        CellKind.CLOUD: (200, 200, 200),
        CellKind.STRUCTURE: (180, 80, 220),
        CellKind.CLAIMABLE: (40, 160, 255),
        CellKind.OTHER: (100, 100, 160),
    }
    counts: Counter[str] = Counter()
    for coord in sorted(calibration.rendered_coords):
        cell = bot.board.get_cell(coord)
        kind = cell.kind if cell is not None else CellKind.OTHER
        x, y = calibration.to_pixel(coord)
        if 0 <= x < frame.shape[1] and 0 <= y < frame.shape[0]:
            cv2.circle(frame, (x, y), 8, colors[kind], 2)
            counts[kind.name.lower()] += 1
    legend = "  ".join(f"{name}:{count}" for name, count in sorted(counts.items()))
    cv2.rectangle(frame, (6, 6), (min(frame.shape[1] - 6, 14 + len(legend) * 8), 32), (0, 0, 0), -1)
    cv2.putText(frame, legend, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    output = output or _default_output("_positions.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), frame)
    rprint(f"[green]Saved[/green] classified positions to {output} (live_synced={synced})")


@app.command("diagnose-live-state")
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
        adapter = GameRuntimeAdapter(settings.cdp_port, settings.window_title)
        health = adapter.discover()
        runtime_discovery = adapter.inspect_runtime()
        arm_status = "already-armed" if health.board_available else "not-found"
        candidates = (
            inspect_board_maps(settings.cdp_port, settings.window_title)
            if include_heap_candidates
            else []
        )
        board_state = read_board_state(settings.cdp_port, settings.window_title)
        shop_orders = adapter.read_shop_orders() if health.shop_available else None
        calibration = read_scene_calibration(settings.cdp_port, settings.window_title)
    except CdpConnectionError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    report = {
        "runtime": {
            "available": health.available,
            "scene_id": health.scene_id,
            "board": health.board_available,
            "item_drop": health.item_drop_available,
            "crate_spawn": health.crate_spawn_available,
            "inventory": health.inventory_available,
            "claim": health.claim_available,
            "shop_orders": health.shop_available,
            "heartbeat": health.heartbeat,
            "heartbeat_age_ms": health.heartbeat_age_ms,
            "heartbeat_advancing": health.heartbeat_advancing,
            "heartbeat_installed": health.heartbeat_installed,
            "detail": health.detail,
            "discovery": runtime_discovery,
        },
        "browser_background_flags": read_background_flag_status(settings.cdp_port),
        "arm_status": arm_status,
        "candidate_maps": candidates,
        "selected_board": {
            "reported_cells": len(board_state) if board_state is not None else None,
            "open_cells": sum(not value.has_content for value in (board_state or {}).values()),
            "common_blueprints": Counter(
                value.blueprint_id for value in (board_state or {}).values() if value.blueprint_id
            ).most_common(20),
            "collectible_products": sum(
                value.collectable_ingredient for value in (board_state or {}).values()
            ),
            "collectable_tiles": sum(value.collectable for value in (board_state or {}).values()),
            "producers": Counter(
                value.producer_state.value
                for value in (board_state or {}).values()
                if value.producer_state is not None
            ),
        },
        "shop_orders": (
            [
                {
                    "shop_id": order.shop_id,
                    "recipe_id": order.recipe_id,
                    "state": order.state.value,
                    "duration_seconds": order.duration_seconds,
                    "remaining_seconds": order.remaining_seconds,
                    "affordable": order.affordable,
                    "ingredients": [
                        {
                            "item_id": ingredient.item_id,
                            "required": ingredient.required,
                            "available": ingredient.available,
                        }
                        for ingredient in order.ingredients
                    ],
                    "reward_ids": list(order.reward_ids),
                }
                for order in shop_orders
            ]
            if shop_orders is not None
            else None
        ),
        "scene": {
            "rendered_cells": len(calibration.rendered_coords) if calibration else 0,
            "origin": calibration.origin if calibration else None,
            "column_step": calibration.col_step if calibration else None,
            "row_step": calibration.row_step if calibration else None,
            "canvas_scale": calibration.canvas_scale if calibration else None,
            "canvas_offset": calibration.canvas_offset if calibration else None,
        },
    }
    output = output or _default_output("_live-state.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    rprint(f"[green]Saved live-state diagnostics[/green] to {output}")


@app.command("extract-templates")
def extract_templates_cmd(
    har: Path = typer.Argument(...),  # noqa: B008
    force: bool = False,
) -> None:
    try:
        extract_templates(har, force=force)
    except RuntimeError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command("compile-assets")
def compile_assets_cmd() -> None:
    """Rebuild the item catalog and assets from the cached game atlases."""
    try:
        compile_cached_assets()
    except RuntimeError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command("sync-assets")
def sync_assets_cmd(force: bool = False) -> None:
    """Discover and compile current game assets into the per-user cache."""
    try:
        sync_runtime_assets(force=force)
    except RuntimeError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command()
def version() -> None:
    from farm_merge_valet import __version__

    rprint(__version__)


if __name__ == "__main__":
    app()
