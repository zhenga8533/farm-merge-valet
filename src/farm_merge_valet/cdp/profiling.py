"""Read-only live runtime profiling."""

from __future__ import annotations

import statistics
import time
from pathlib import Path

from farm_merge_valet.automation.runtime import SnapshotOptions
from farm_merge_valet.cdp.runtime import GameRuntimeAdapter


def profile_runtime(
    runtime: GameRuntimeAdapter,
    *,
    duration_seconds: float,
    interval_seconds: float = 0.25,
) -> dict[str, object]:
    deadline = time.monotonic() + max(0.25, duration_seconds)
    wall_samples: list[float] = []
    renderer_samples: list[float] = []
    heartbeat_ages: list[float] = []
    response_sizes: list[int] = []
    cell_count = 0
    occupied_count = 0
    while time.monotonic() < deadline:
        snapshot = runtime.read_snapshot(SnapshotOptions(False, False, False))
        if snapshot is None or snapshot.cells is None or snapshot.metrics is None:
            raise RuntimeError(
                "The cached runtime is unavailable. Start the bot, wait for runtime ready, "
                "pause it, and retry."
            )
        metrics = snapshot.metrics
        wall_samples.append(metrics.wall_duration_ms)
        if metrics.renderer_duration_ms is not None:
            renderer_samples.append(metrics.renderer_duration_ms)
        if snapshot.health.heartbeat_age_ms is not None:
            heartbeat_ages.append(snapshot.health.heartbeat_age_ms)
        response_sizes.append(metrics.response_bytes)
        cell_count = metrics.cell_count
        occupied_count = metrics.occupied_cell_count
        time.sleep(max(0.25, interval_seconds))

    def percentile(values: list[float], percentile_value: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile_value))
        return ordered[index]

    return {
        "samples": len(wall_samples),
        "cell_count": cell_count,
        "occupied_cell_count": occupied_count,
        "wall_ms": {
            "mean": statistics.fmean(wall_samples),
            "p95": percentile(wall_samples, 0.95),
            "max": max(wall_samples),
        },
        "renderer_ms": {
            "mean": statistics.fmean(renderer_samples) if renderer_samples else None,
            "p95": percentile(renderer_samples, 0.95),
            "max": max(renderer_samples) if renderer_samples else None,
        },
        "heartbeat_age_ms_max": max(heartbeat_ages) if heartbeat_ages else None,
        "response_bytes_max": max(response_sizes),
    }


def write_profile(path: Path, profile: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile + "\n", encoding="utf-8")
