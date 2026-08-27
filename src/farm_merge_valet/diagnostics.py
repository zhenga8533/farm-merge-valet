"""Reusable capture and support-diagnostic operations."""

from __future__ import annotations

import json
import platform
import sys
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from farm_merge_valet import __version__
from farm_merge_valet.browser import BrowserManager
from farm_merge_valet.cdp.board_store import arm_board_store, inspect_board_maps, read_board_state
from farm_merge_valet.cdp.client import capture_game_frame, read_background_flag_status
from farm_merge_valet.cdp.runtime import GameRuntimeAdapter
from farm_merge_valet.cdp.scene_geometry import read_scene_calibration
from farm_merge_valet.config import AppConfig, settings
from farm_merge_valet.core.board import CellKind
from farm_merge_valet.core.bot import Bot


def capture_game_image(config: AppConfig) -> np.ndarray:
    encoded = np.frombuffer(
        capture_game_frame(config.cdp_port, config.window_title),
        np.uint8,
    )
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("Could not decode the browser's game screenshot.")
    return image


def build_position_visualization(
    config: AppConfig,
) -> tuple[np.ndarray, bool, Counter[str]]:
    settings.replace(config)
    frame = capture_game_image(config)
    bot = Bot()
    arm_board_store(config.cdp_port, config.window_title)
    synced = bot.sync_board_from_live_state()
    calibration = read_scene_calibration(config.cdp_port, config.window_title)
    if calibration is None:
        raise RuntimeError("Could not derive live scene geometry.")
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
    cv2.rectangle(
        frame,
        (6, 6),
        (min(frame.shape[1] - 6, 14 + len(legend) * 8), 32),
        (0, 0, 0),
        -1,
    )
    cv2.putText(
        frame,
        legend,
        (10, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (255, 255, 255),
        1,
    )
    return frame, synced, counts


def collect_live_state_report(
    config: AppConfig,
    *,
    include_heap_candidates: bool = False,
) -> dict[str, Any]:
    adapter = GameRuntimeAdapter(config.cdp_port, config.window_title)
    health = adapter.discover()
    runtime_discovery = adapter.inspect_runtime()
    candidates = (
        inspect_board_maps(config.cdp_port, config.window_title)
        if include_heap_candidates
        else []
    )
    board_state = read_board_state(config.cdp_port, config.window_title)
    shop_orders = adapter.read_shop_orders() if health.shop_available else None
    calibration = read_scene_calibration(config.cdp_port, config.window_title)
    return {
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
        "browser_background_flags": read_background_flag_status(config.cdp_port),
        "arm_status": "already-armed" if health.board_available else "not-found",
        "candidate_maps": candidates,
        "selected_board": {
            "reported_cells": len(board_state) if board_state is not None else None,
            "open_cells": sum(not value.has_content for value in (board_state or {}).values()),
            "common_blueprints": Counter(
                value.blueprint_id
                for value in (board_state or {}).values()
                if value.blueprint_id
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


def write_live_state_report(
    config: AppConfig,
    output: Path,
    *,
    include_heap_candidates: bool = False,
) -> None:
    report = collect_live_state_report(
        config,
        include_heap_candidates=include_heap_candidates,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")


def export_support_bundle(
    config: AppConfig,
    output: Path,
    *,
    logs: str = "",
    include_screenshot: bool = False,
) -> Path:
    """Create a sanitized support archive, retaining errors as diagnostic data."""
    browser = BrowserManager(config)
    browser_status = browser.status()
    browser_summary = browser.status_dict(browser_status)
    browser_summary.pop("executable", None)
    browser_summary.pop("profile_dir", None)
    summary: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "application_version": __version__,
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "configuration": {
            "browser": config.browser,
            "browser_auto_launch": config.browser_auto_launch,
            "cdp_port": config.cdp_port,
            "theme": config.theme,
            "log_level": config.log_level,
            "catalog_available": (config.catalog_dir / "catalog.json").is_file(),
            "cached_atlas_count": len(tuple(config.atlas_cache_dir.glob("*.png"))),
        },
        "browser": browser_summary,
    }
    try:
        summary["live_state"] = collect_live_state_report(config)
    except Exception as exc:
        summary["live_state_error"] = str(exc)

    output = output.with_suffix(".zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json", json.dumps(summary, indent=2, default=str))
        archive.writestr("application.log", logs)
        if include_screenshot:
            try:
                success, encoded = cv2.imencode(".png", capture_game_image(config))
                if success:
                    archive.writestr("game.png", encoded.tobytes())
            except Exception as exc:
                archive.writestr("screenshot-error.txt", str(exc))
    return output
