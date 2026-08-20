"""Post run statistics to a Discord channel via an incoming webhook."""

from __future__ import annotations

import logging

import httpx

from farm_merge_valet.core.stats import RunStats

logger = logging.getLogger(__name__)


def post_stats(webhook_url: str, stats: RunStats) -> None:
    """Send a stats summary embed to the configured Discord webhook."""
    payload = {
        "embeds": [
            {
                "title": "Farm Merge Valet — Run Stats",
                "fields": [
                    {"name": key, "value": str(value), "inline": True}
                    for key, value in stats.as_dict().items()
                ],
            }
        ]
    }
    response = httpx.post(webhook_url, json=payload, timeout=10.0)
    response.raise_for_status()
    logger.info("Posted stats to Discord webhook.")
