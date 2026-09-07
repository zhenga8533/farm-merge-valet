"""Compact Discord-friendly activity charts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from threading import Lock

import cv2
import numpy as np


@dataclass(frozen=True)
class DiscordAttachment:
    filename: str
    content: bytes
    content_type: str = "image/png"


class ActivityChart:
    """Collect bounded activity buckets and render them without a plotting dependency."""

    def __init__(self, interval_seconds: float, *, bucket_count: int = 24) -> None:
        self._bucket_seconds = max(60.0, interval_seconds / bucket_count)
        self._bucket_count = bucket_count
        self._buckets: dict[int, Counter[str]] = {}
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
            populated = dict(self._buckets)
            self._buckets.clear()
        if not populated:
            return None

        series = [populated.get(index, Counter()) for index in range(start, current + 1)]
        width, height = 1000, 500
        image = np.full((height, width, 3), (35, 37, 42), dtype=np.uint8)
        cv2.putText(
            image,
            "Activity timeline",
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
        maximum = max(1, max((sum(parts) for parts in zip(*values, strict=True)), default=0))
        left, right, top, bottom = 70, width - 38, 90, height - 78
        cv2.line(image, (left, bottom), (right, bottom), (102, 105, 113), 1)
        bar_width = max(4, int((right - left) / len(series)) - 3)
        for index in range(len(series)):
            x = left + int(index * (right - left) / len(series))
            y = bottom
            for label_index, (_label, _prefix, color) in enumerate(labels):
                value = values[label_index][index]
                if not value:
                    continue
                segment = max(2, int((bottom - top) * value / maximum))
                cv2.rectangle(image, (x, y - segment), (x + bar_width, y), color, -1)
                y -= segment
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
        return DiscordAttachment("activity-timeline.png", data.tobytes())
