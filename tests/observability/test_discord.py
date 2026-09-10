from __future__ import annotations

import json
import logging
from collections import Counter
from threading import Lock

import pytest

from farm_merge_valet.observability.discord import handler as discord
from farm_merge_valet.observability.discord.charts import ActivityChart, _relative_time_label
from farm_merge_valet.observability.logging import log_event, logging_sink


class _FakeResponse:
    def __init__(self, body: dict[str, object] | None = None) -> None:
        self._body = body or {}

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, object]:
        return self._body


class _FakeClient:
    requests: list[dict[str, object]] = []
    next_id = 100

    def __init__(self, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.requests.append({"method": "POST", "url": url, **kwargs})
        message_id = str(self.next_id)
        type(self).next_id += 1
        return _FakeResponse({"id": message_id})

    def patch(self, url: str, **kwargs: object) -> _FakeResponse:
        self.requests.append({"method": "PATCH", "url": url, **kwargs})
        return _FakeResponse()


def _request_payload(request: dict[str, object]) -> dict[str, object]:
    payload = request.get("json")
    if isinstance(payload, dict):
        return payload
    data = request.get("data")
    assert isinstance(data, dict)
    return json.loads(str(data["payload_json"]))

    def delete(self, url: str, **kwargs: object) -> _FakeResponse:
        self.requests.append({"method": "DELETE", "url": url, **kwargs})
        return _FakeResponse()


def test_webhook_rejects_insecure_url_without_echoing_it() -> None:
    with pytest.raises(ValueError) as error:
        discord.DiscordWebhookHandler("http://example.test/private-token", 3600)

    assert "private-token" not in str(error.value)


def test_activity_report_is_consumed_only_after_delivery() -> None:
    chart = ActivityChart(3600)
    chart.record(1000.0, Counter({"action.merge": 2}))

    assert chart.render(1000.0) is not None
    assert chart.render(1000.0) is not None

    chart.record(1000.0, Counter({"action.merge": 1}))
    chart.commit()

    assert chart.render(1000.0) is not None
    chart.commit()
    assert chart.render(1000.0) is None


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "now"), (600, "-10m"), (3600, "-1h"), (5400, "-1.5h")],
)
def test_activity_report_formats_relative_time_axis(seconds: float, expected: str) -> None:
    assert _relative_time_label(seconds) == expected


def test_failed_summary_restores_metrics_and_chart_history(monkeypatch) -> None:
    handler = object.__new__(discord.DiscordWebhookHandler)
    handler._metrics = Counter()
    handler._metrics_lock = Lock()
    handler._include_charts = True
    handler._screenshot_provider = None
    handler._activity_chart = ActivityChart(3600)
    handler._activity_chart.record(1000.0, Counter({"action.merge": 1}))
    monkeypatch.setattr(handler, "_send", lambda *_args: None)
    summary_metrics = Counter({"action.merge": 1})

    assert not handler._deliver_summary(object(), ({}, summary_metrics), 1000.0)
    assert handler._metrics == summary_metrics
    assert handler._activity_chart.render(1000.0) is not None


def test_webhook_routes_lifecycle_and_summarizes_actions(monkeypatch, tmp_path) -> None:
    _FakeClient.requests = []
    _FakeClient.next_id = 100
    monkeypatch.setattr(discord.httpx, "Client", _FakeClient)
    handler = discord.DiscordWebhookHandler(
        "https://example.test/webhook",
        3600,
        status_state_path=tmp_path / "status.json",
        screenshot_provider=lambda: b"\xff\xd8\xffgame",
    )
    event_logger = logging.getLogger("farm_merge_valet.tests.discord")
    event_logger.setLevel(logging.DEBUG)

    with logging_sink(handler):
        log_event(
            event_logger,
            logging.DEBUG,
            "action.confirmed",
            "Merge confirmed.",
            effect="merge",
        )
        log_event(
            event_logger,
            logging.INFO,
            "crate.claim_completed",
            "Spawned 2 crates.",
            spawned=2,
        )
        log_event(
            event_logger,
            logging.DEBUG,
            "interaction.confirmed",
            "Immediate interaction confirmed.",
            interaction_kind="immediate",
            blueprint_id="ticket",
        )
        log_event(
            event_logger,
            logging.DEBUG,
            "shop.order_started",
            "Shop order started.",
            shop_id="market",
            recipe_id="recipe_flour",
        )
        log_event(
            event_logger,
            logging.DEBUG,
            "shop.order_claimed",
            "Shop order claimed.",
            shop_id="bakery",
            recipe_id="recipe_bread",
        )
        log_event(event_logger, logging.INFO, "runtime.ready", "Runtime ready.")
    handler.shutdown()

    posts = [request for request in _FakeClient.requests if request["method"] == "POST"]
    embeds = [_request_payload(post)["embeds"][0] for post in posts]
    assert any(embed["title"] == "Game runtime ready" for embed in embeds)
    assert any(
        any(
            field["name"] == "Item actions" and "Merges 1" in field["value"]
            for field in embed.get("fields", [])
        )
        for embed in embeds
    )
    assert any(
        any(
            field["name"] == "Shop activity"
            and "Started 1" in field["value"]
            and "Claimed 1" in field["value"]
            for field in embed.get("fields", [])
        )
        for embed in embeds
    )
    assert any(
        any(
            field["name"] == "Board activity"
            and "Tile interactions 1" in field["value"]
            and "Crates 2" in field["value"]
            for field in embed.get("fields", [])
        )
        for embed in embeds
    )
    assert not any(
        embed.get("footer", {}).get("text") == "INFO · crate.claim_completed" for embed in embeds
    )
    assert all(_request_payload(post)["allowed_mentions"] == {"parse": []} for post in posts)
    notification_index = next(
        index
        for index, request in enumerate(_FakeClient.requests)
        if _request_payload(request).get("embeds", [{}])[0].get("title") == "Game runtime ready"
    )
    replacement = _FakeClient.requests[notification_index + 1]
    assert replacement["method"] == "PATCH"
    assert replacement["json"]["embeds"][0]["footer"]["text"] == "farm-merge-valet.status"
    assert "Actions 1" in replacement["json"]["embeds"][0]["fields"][3]["value"]
    assert "Crates 2" in replacement["json"]["embeds"][0]["fields"][3]["value"]
    chart_posts = [post for post in posts if "files" in post]
    assert chart_posts
    assert chart_posts[0]["files"]["files[0]"][0] == "session-report.png"
    assert chart_posts[0]["files"]["files[1]"][0] == "game-view.jpg"
    summary_embeds = _request_payload(chart_posts[0])["embeds"]
    assert summary_embeds[1]["image"]["url"] == "attachment://game-view.jpg"


def test_webhook_rate_limits_duplicate_warning_delivery(monkeypatch, tmp_path) -> None:
    _FakeClient.requests = []
    _FakeClient.next_id = 100
    monkeypatch.setattr(discord.httpx, "Client", _FakeClient)
    monkeypatch.setattr(discord.time, "monotonic", lambda: 1000.0)
    handler = discord.DiscordWebhookHandler(
        "https://example.test/webhook",
        3600,
        status_state_path=tmp_path / "status.json",
    )
    event_logger = logging.getLogger("farm_merge_valet.tests.discord.warning")
    event_logger.setLevel(logging.DEBUG)

    with logging_sink(handler):
        for _ in range(2):
            log_event(
                event_logger,
                logging.WARNING,
                "runtime.unavailable",
                "Runtime unavailable.",
            )
    handler.shutdown()

    immediate = [
        post
        for post in _FakeClient.requests
        if post["method"] == "POST"
        if _request_payload(post)["embeds"][0].get("footer", {}).get("text")
        == "WARNING · runtime.unavailable"
    ]
    assert len(immediate) == 1


def test_status_only_change_edits_without_reposting(monkeypatch, tmp_path) -> None:
    _FakeClient.requests = []
    _FakeClient.next_id = 100
    monkeypatch.setattr(discord.httpx, "Client", _FakeClient)
    handler = discord.DiscordWebhookHandler(
        "https://example.test/webhook",
        3600,
        status_state_path=tmp_path / "status.json",
    )
    event_logger = logging.getLogger("farm_merge_valet.tests.discord.status")
    event_logger.setLevel(logging.DEBUG)

    with logging_sink(handler):
        log_event(event_logger, logging.INFO, "bot.idle", "No action is available.")
    handler.shutdown()

    assert not any(request["method"] == "DELETE" for request in _FakeClient.requests)
    status_edits = [
        request
        for request in _FakeClient.requests
        if request["method"] == "PATCH"
        and request["json"]["embeds"][0]["footer"]["text"] == "farm-merge-valet.status"
    ]
    assert status_edits
    assert "username" not in status_edits[-1]["json"]
    assert status_edits[-1]["json"]["embeds"][0]["title"] == "Current status · Idle"


def test_status_message_id_is_reused_across_runs(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "status.json"
    _FakeClient.requests = []
    _FakeClient.next_id = 100
    monkeypatch.setattr(discord.httpx, "Client", _FakeClient)

    first = discord.DiscordWebhookHandler(
        "https://example.test/webhook",
        3600,
        status_state_path=state_path,
    )
    first.shutdown()
    persisted_id = json.loads(state_path.read_text(encoding="utf-8"))["message_id"]

    _FakeClient.requests = []
    second = discord.DiscordWebhookHandler(
        "https://example.test/webhook",
        3600,
        status_state_path=state_path,
    )
    second.shutdown()

    assert _FakeClient.requests[0]["method"] == "PATCH"
    assert _FakeClient.requests[0]["url"].endswith(f"/messages/{persisted_id}")
