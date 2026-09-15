"""Discord webhook delivery and persisted status-message identity."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Literal

import httpx

from farm_merge_valet.observability.discord.charts import DiscordAttachment
from farm_merge_valet.observability.logging import log_event

logger = logging.getLogger(__name__)


class DiscordWebhookTransportMixin:
    _url: str
    _status_state_path: Path
    _webhook_fingerprint: str
    _status_message_id: str | None

    def _status_payload(self, now: float) -> dict[str, object]:
        raise NotImplementedError

    def _load_status_message_id(self) -> str | None:
        if not self._status_state_path.exists():
            return None
        try:
            raw = json.loads(self._status_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log_event(
                logger,
                logging.WARNING,
                "webhook.status_state_read_failed",
                "Could not read Discord status state: %s.",
                type(exc).__name__,
                detail=type(exc).__name__,
            )
            return None
        if not isinstance(raw, dict) or raw.get("webhook") != self._webhook_fingerprint:
            return None
        message_id = raw.get("message_id")
        return message_id if isinstance(message_id, str) and message_id.isdigit() else None

    def _persist_status_message_id(self, message_id: str | None) -> None:
        try:
            if message_id is None:
                self._status_state_path.unlink(missing_ok=True)
                return
            self._status_state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._status_state_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"webhook": self._webhook_fingerprint, "message_id": message_id}),
                encoding="utf-8",
            )
            temporary.replace(self._status_state_path)
        except OSError as exc:
            log_event(
                logger,
                logging.WARNING,
                "webhook.status_state_write_failed",
                "Could not persist Discord status state: %s.",
                type(exc).__name__,
                detail=type(exc).__name__,
            )

    @staticmethod
    def _log_delivery_failure(exc: httpx.HTTPError) -> None:
        detail = (
            f"HTTP {exc.response.status_code}"
            if isinstance(exc, httpx.HTTPStatusError)
            else type(exc).__name__
        )
        log_event(
            logger,
            logging.WARNING,
            "webhook.delivery_failed",
            "Discord webhook delivery failed: %s",
            detail,
            detail=detail,
        )

    def _send(
        self,
        client: httpx.Client,
        payload: dict[str, object],
        attachments: tuple[DiscordAttachment, ...] = (),
    ) -> httpx.Response | None:
        if not attachments:
            data = None
            files = None
        else:
            embeds = payload.get("embeds")
            if isinstance(embeds, list) and embeds and isinstance(embeds[0], dict):
                embeds[0]["image"] = {"url": f"attachment://{attachments[0].filename}"}
                embeds.extend(
                    {"image": {"url": f"attachment://{attachment.filename}"}}
                    for attachment in attachments[1:]
                )
            data = {"payload_json": json.dumps(payload)}
            files = {
                f"files[{index}]": (
                    attachment.filename,
                    attachment.content,
                    attachment.content_type,
                )
                for index, attachment in enumerate(attachments)
            }
        last_error: httpx.HTTPError | None = None
        for attempt in range(3):
            try:
                if not attachments:
                    response = client.post(self._url, params={"wait": "true"}, json=payload)
                else:
                    response = client.post(
                        self._url,
                        params={"wait": "true"},
                        data=data,
                        files=files,
                    )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code == 429:
                    try:
                        retry_after = float(exc.response.json().get("retry_after", 1.0))
                    except (AttributeError, TypeError, ValueError):
                        retry_after = 1.0
                elif exc.response.status_code >= 500:
                    retry_after = 0.25 * (2**attempt)
                else:
                    break
                if attempt < 2:
                    time.sleep(min(retry_after, 5.0))
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.25 * (2**attempt))
        if last_error is not None:
            self._log_delivery_failure(last_error)
        return None

    def _status_message_url(self) -> str | None:
        if self._status_message_id is None:
            return None
        return f"{self._url.rstrip('/')}/messages/{self._status_message_id}"

    def _create_status(self, client: httpx.Client, now: float) -> bool:
        response = self._send(client, self._status_payload(now))
        if response is None:
            return False
        try:
            body = response.json()
        except ValueError:
            body = None
        message_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(message_id, str) or not message_id.isdigit():
            log_event(
                logger,
                logging.WARNING,
                "webhook.status_id_missing",
                "Discord did not return an ID for the status message.",
            )
            return False
        self._status_message_id = message_id
        self._persist_status_message_id(message_id)
        return True

    def _edit_status(
        self, client: httpx.Client, now: float
    ) -> Literal["updated", "missing", "failed"]:
        status_url = self._status_message_url()
        if status_url is None:
            return "missing"
        payload = self._status_payload(now)
        payload.pop("username", None)
        last_error: httpx.HTTPError | None = None
        for attempt in range(3):
            try:
                response = client.patch(status_url, json=payload)
                response.raise_for_status()
                return "updated"
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    self._status_message_id = None
                    self._persist_status_message_id(None)
                    return "missing"
                last_error = exc
                if exc.response.status_code < 500 and exc.response.status_code != 429:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(0.25 * (2**attempt))
        if last_error is not None:
            self._log_delivery_failure(last_error)
        return "failed"

    def _refresh_status(self, client: httpx.Client, now: float) -> None:
        edit_result = self._edit_status(client, now)
        if edit_result == "missing":
            self._create_status(client, now)
