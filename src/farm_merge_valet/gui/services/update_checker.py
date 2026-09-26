"""Asynchronous checks for newer public GitHub releases."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from PySide6.QtCore import QObject, Signal

_RELEASES_URL = "https://api.github.com/repos/zhenga8533/farm-merge-valet/releases"
_RELEASE_PATH_PREFIX = "/zhenga8533/farm-merge-valet/releases/"
_RELEASES_PER_PAGE = 30
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
# The release workflow appends GitHub's generated pull request list and compare link
# after the CHANGELOG entry; those are maintainer-facing and omitted from the notes.
_GENERATED_NOTES_PATTERN = re.compile(
    r"^(?:## What's Changed|## New Contributors|\*\*Full Changelog\*\*)", re.MULTILINE
)


@dataclass(frozen=True)
class ReleaseNotes:
    version: str
    url: str
    body: str


@dataclass(frozen=True)
class AvailableUpdate:
    version: str
    url: str
    releases: tuple[ReleaseNotes, ...]

    def notes_markdown(self) -> str:
        return "\n\n".join(
            f"## {release.version}\n\n{release.body or '_No release notes._'}"
            for release in self.releases
        )


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def changelog_notes(body: str) -> str:
    match = _GENERATED_NOTES_PATTERN.search(body)
    return (body[: match.start()] if match else body).strip()


def _trusted_release_url(url: str) -> bool:
    parsed_url = urlparse(url)
    return (
        parsed_url.scheme == "https"
        and parsed_url.hostname == "github.com"
        and parsed_url.path.startswith(_RELEASE_PATH_PREFIX)
    )


def available_update(payload: object, current_version: str) -> AvailableUpdate | None:
    if not isinstance(payload, list):
        raise ValueError("Releases response was not a list")
    current = _version_tuple(current_version)
    if current is None:
        return None
    newer: list[tuple[tuple[int, int, int], ReleaseNotes]] = []
    for release in payload:
        if not isinstance(release, dict):
            raise ValueError("Releases response contained a non-object entry")
        tag = release.get("tag_name")
        url = release.get("html_url")
        if not isinstance(tag, str) or not isinstance(url, str):
            raise ValueError("Release entry is missing its tag or URL")
        if release.get("draft") or release.get("prerelease"):
            continue
        version = _version_tuple(tag)
        if version is None or version <= current or not _trusted_release_url(url):
            continue
        body = release.get("body")
        notes = changelog_notes(body) if isinstance(body, str) else ""
        newer.append((version, ReleaseNotes(".".join(map(str, version)), url, notes)))
    if not newer:
        return None
    newer.sort(key=lambda entry: entry[0], reverse=True)
    releases = tuple(notes for _, notes in newer)
    return AvailableUpdate(releases[0].version, releases[0].url, releases)


def fetch_available_update(current_version: str) -> AvailableUpdate | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"farm-merge-valet/{current_version}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = httpx.get(
        _RELEASES_URL,
        params={"per_page": _RELEASES_PER_PAGE},
        headers=headers,
        timeout=5,
        follow_redirects=False,
    )
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
