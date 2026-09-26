"""Asynchronous checks for newer public GitHub releases."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from PySide6.QtCore import QObject, Signal

_LATEST_RELEASE_URL = "https://api.github.com/repos/zhenga8533/farm-merge-valet/releases/latest"
_RELEASE_PATH_PREFIX = "/zhenga8533/farm-merge-valet/releases/"
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


@dataclass(frozen=True)
class AvailableUpdate:
    version: str
    url: str


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def available_update(payload: object, current_version: str) -> AvailableUpdate | None:
    if not isinstance(payload, dict):
        raise ValueError("Latest release response was not an object")
    tag = payload.get("tag_name")
    url = payload.get("html_url")
    if not isinstance(tag, str) or not isinstance(url, str):
        raise ValueError("Latest release response is missing its tag or URL")
    current = _version_tuple(current_version)
    latest = _version_tuple(tag)
    parsed_url = urlparse(url)
    if (
        current is None
        or latest is None
        or parsed_url.scheme != "https"
        or parsed_url.hostname != "github.com"
        or not parsed_url.path.startswith(_RELEASE_PATH_PREFIX)
    ):
        return None
    if latest <= current:
        return None
    return AvailableUpdate(".".join(map(str, latest)), url)


def fetch_available_update(current_version: str) -> AvailableUpdate | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"farm-merge-valet/{current_version}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = httpx.get(_LATEST_RELEASE_URL, headers=headers, timeout=5, follow_redirects=False)
    response.raise_for_status()
    return available_update(response.json(), current_version)


class UpdateChecker(QObject):
    update_available = Signal(object)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._started = False

    def start(self, current_version: str) -> bool:
        if self._started:
            return False
        self._started = True

        def run() -> None:
            try:
                update = fetch_available_update(current_version)
            except (httpx.HTTPError, ValueError) as exc:
                self.failed.emit(str(exc))
                return
            if update is not None:
                self.update_available.emit(update)

        threading.Thread(target=run, daemon=True, name="fmv-update-check").start()
        return True
