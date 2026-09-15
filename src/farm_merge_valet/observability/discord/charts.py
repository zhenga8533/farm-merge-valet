"""Compact Discord-friendly activity charts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import ceil
from threading import Lock

import cv2
import numpy as np


@dataclass(frozen=True)
class DiscordAttachment:
    filename: str
    content: bytes
    content_type: str = "image/png"


def _relative_time_label(seconds: float) -> str:
    if seconds < 30:
        return "now"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"-{minutes}m"
    hours = minutes / 60
    return f"-{hours:g}h"


class ActivityChart:
    """Collect bounded activity buckets and render them without a plotting dependency."""

    def __init__(self, interval_seconds: float, *, bucket_count: int = 24) -> None:
        self._bucket_seconds = max(60.0, interval_seconds / bucket_count)
        self._bucket_count = bucket_count
        self._buckets: dict[int, Counter[str]] = {}
        self._rendered: dict[int, Counter[str]] = {}
        self._lock = Lock()

    def record(self, now: float, metrics: Counter[str]) -> None:
        if not metrics:
            return
        bucket = int(now // self._bucket_seconds)
        with self._lock:
            self._buckets.setdefault(bucket, Counter()).update(metrics)
            cutoff = bucket - self._bucket_count + 1
            self._buckets = {key: value for key, value in self._buckets.items() if key >= cutoff}

    def render(self, now: float) -> DiscordAttachment | None:
        current = int(now // self._bucket_seconds)
        start = current - self._bucket_count + 1
        with self._lock:
            populated = {key: value.copy() for key, value in self._buckets.items() if key >= start}
            self._rendered = populated
        if not populated:
            return None

        series = [populated.get(index, Counter()) for index in range(start, current + 1)]
        width, height = 1200, 720
        image = np.full((height, width, 3), (35, 37, 42), dtype=np.uint8)
        cv2.putText(
            image,
            "Session activity report",
            (42, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.05,
            (245, 245, 245),
            2,
            cv2.LINE_AA,
        )
        labels = (
            ("Actions", "action.", (87, 242, 129)),
            ("Interactions", "interaction.", (250, 166, 26)),
            ("Workflows", "workflow.", (235, 130, 76)),
            ("Warnings", "warnings", (18, 181, 243)),
            ("Errors", "errors", (60, 76, 231)),
        )
        values: list[list[int]] = []
        for _label, prefix, _color in labels:
            values.append(
                [
                    sum(value for key, value in bucket.items() if key.startswith(prefix))
                    if prefix.endswith(".")
                    else bucket[prefix]
                    for bucket in series
                ]
            )
        totals = [sum(series_values) for series_values in values]
        card_width = 208
        for index, ((label, _prefix, color), value) in enumerate(zip(labels, totals, strict=True)):
            card_left = 42 + index * 229
            cv2.rectangle(image, (card_left, 72), (card_left + card_width, 156), (48, 51, 58), -1)
            cv2.rectangle(image, (card_left, 72), (card_left + 6, 156), color, -1)
            cv2.putText(
                image,
                label,
                (card_left + 22, 103),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (185, 188, 194),
                1,
                cv2.LINE_AA,
            )
            cv2.putText(
                image,
                str(value),
                (card_left + 22, 140),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (245, 245, 245),
                2,
                cv2.LINE_AA,
            )
        maximum = max(1, max((sum(parts) for parts in zip(*values, strict=True)), default=0))
        tick_step = max(1, ceil(maximum / 4))
        scale_maximum = tick_step * ceil(maximum / tick_step)
        left, right, top, bottom = 105, width - 38, 205, height - 140
        cv2.putText(
            image,
            "Events per bucket",
            (left, top - 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (185, 188, 194),
            1,
            cv2.LINE_AA,
        )
        for tick in range(0, scale_maximum + 1, tick_step):
            y = bottom - round((bottom - top) * tick / scale_maximum)
            cv2.line(image, (left, y), (right, y), (63, 66, 73), 1)
            text = str(tick)
            text_width = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.44, 1)[0][0]
            cv2.putText(
                image,
                text,
                (left - text_width - 12, y + 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.44,
                (185, 188, 194),
                1,
                cv2.LINE_AA,
            )
        slot_width = (right - left) / len(series)
        bar_width = max(4, int(slot_width) - 3)
        for index in range(len(series)):
            x = left + round(index * slot_width)
            y = bottom
            for label_index, (_label, _prefix, color) in enumerate(labels):
                value = values[label_index][index]
                if not value:
                    continue
                segment = max(2, int((bottom - top) * value / scale_maximum))
                cv2.rectangle(image, (x, y - segment), (x + bar_width, y), color, -1)
                y -= segment
        tick_indices = sorted({round(index * (len(series) - 1) / 4) for index in range(5)})
        for index in tick_indices:
            x = left + round((index + 0.5) * slot_width)
            label = _relative_time_label((len(series) - 1 - index) * self._bucket_seconds)
            text_width = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)[0][0]
            cv2.putText(
                image,
                label,
                (x - text_width // 2, bottom + 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (185, 188, 194),
                1,
                cv2.LINE_AA,
            )
        cv2.putText(
            image,
            "Time before report",
            (left, bottom + 52),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (185, 188, 194),
            1,
            cv2.LINE_AA,
        )
        legend_x = left
        for label, _prefix, color in labels:
            cv2.rectangle(image, (legend_x, height - 42), (legend_x + 16, height - 26), color, -1)
            cv2.putText(
                image,
                label,
                (legend_x + 23, height - 27),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (220, 221, 222),
                1,
                cv2.LINE_AA,
            )
            legend_x += 155
        encoded, data = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 7])
        if not encoded:
            return None
        return DiscordAttachment("session-report.png", data.tobytes())

    def commit(self) -> None:
        """Discard activity included in a successfully delivered report."""
        with self._lock:
            for key, rendered in self._rendered.items():
                bucket = self._buckets.get(key)
                if bucket is None:
                    continue
                bucket.subtract(rendered)
                remaining = Counter({name: count for name, count in bucket.items() if count > 0})
                if remaining:
                    self._buckets[key] = remaining
                else:
                    self._buckets.pop(key, None)
            self._rendered = {}
