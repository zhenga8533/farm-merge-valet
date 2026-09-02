"""Adaptive cadence for renderer-backed runtime snapshots."""

from __future__ import annotations

from dataclasses import dataclass

MINIMUM_ACTIVE_INTERVAL = 0.250
MAXIMUM_ADAPTIVE_INTERVAL = 5.0
_EWMA_ALPHA = 0.25
_TARGET_IDLE_RATIO = 4.0


@dataclass
class AdaptiveScheduler:
    latency_ewma: float | None = None

    def record_snapshot(self, elapsed_seconds: float) -> None:
        sample = max(0.0, elapsed_seconds)
        if self.latency_ewma is None:
            self.latency_ewma = sample
        else:
            self.latency_ewma = (
                _EWMA_ALPHA * sample + (1.0 - _EWMA_ALPHA) * self.latency_ewma
            )

    def reset(self) -> None:
        self.latency_ewma = None

    def active_delay(self, configured_interval: float) -> float:
        adaptive = (
            min(MAXIMUM_ADAPTIVE_INTERVAL, _TARGET_IDLE_RATIO * self.latency_ewma)
            if self.latency_ewma is not None
            else 0.0
        )
        return max(MINIMUM_ACTIVE_INTERVAL, configured_interval, adaptive)
