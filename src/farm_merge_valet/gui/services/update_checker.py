"""Asynchronous checks for newer public GitHub releases and their notes."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from PySide6.QtCore import QObject, Signal, SignalInstance

RELEASES_PAGE_URL = "https://github.com/zhenga8533/farm-merge-valet/releases"
_RELEASES_URL = "https://api.github.com/repos/zhenga8533/farm-merge-valet/releases"
_RELEASE_PATH_PREFIX = "/zhenga8533/farm-merge-valet/releases/"
_RELEASES_PER_PAGE = 30
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
# The release workflow appends GitHub's generated pull request list and compare link
# after the CHANGELOG entry; those are maintainer-facing and omitted from the notes.
_GENERATED_NOTES_PATTERN = re.compile(
    r"^(?:## What's Changed|## New Contributors|\*\*Full Changelog\*\*)", re.MULTILINE
)

_Version = tuple[int, int, int]


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


def _version_tuple(value: str) -> _Version | None:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def changelog_notes(body: str) -> str:
    match = _GENERATED_NOTES_PATTERN.search(body)
    return (body[: match.start()] if match else body).strip()


def notes_markdown(releases: Sequence[ReleaseNotes]) -> str:
    return "\n\n".join(
        f"## {release.version}\n\n{release.body or '_No release notes._'}" for release in releases
    )


def _trusted_release_url(url: str) -> bool:
    parsed_url = urlparse(url)
    return (
        parsed_url.scheme == "https"
        and parsed_url.hostname == "github.com"
        and parsed_url.path.startswith(_RELEASE_PATH_PREFIX)
    )


def _published_releases(
    payload: object, include: Callable[[_Version], bool]
) -> tuple[ReleaseNotes, ...]:
    if not isinstance(payload, list):
        raise ValueError("Releases response was not a list")
    selected: list[tuple[_Version, ReleaseNotes]] = []
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
        if version is None or not include(version) or not _trusted_release_url(url):
            continue
        body = release.get("body")
        notes = changelog_notes(body) if isinstance(body, str) else ""
        selected.append((version, ReleaseNotes(".".join(map(str, version)), url, notes)))
    selected.sort(key=lambda entry: entry[0], reverse=True)
    return tuple(notes for _, notes in selected)


def available_update(payload: object, current_version: str) -> AvailableUpdate | None:
    current = _version_tuple(current_version)
    if current is None:
        return None
    releases = _published_releases(payload, lambda version: version > current)
    if not releases:
        return None
    return AvailableUpdate(releases[0].version, releases[0].url, releases)


def releases_since(payload: object, current_version: str) -> tuple[ReleaseNotes, ...]:
    current = _version_tuple(current_version)
    if current is None:
        return ()
    return _published_releases(payload, lambda version: version >= current)


def _fetch_releases(current_version: str) -> object:
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
    return response.json()


def fetch_available_update(current_version: str) -> AvailableUpdate | None:
    return available_update(_fetch_releases(current_version), current_version)


def fetch_releases_since(current_version: str) -> tuple[ReleaseNotes, ...]:
    return releases_since(_fetch_releases(current_version), current_version)


def _run_in_background(
    name: str,
    work: Callable[[], object],
    succeeded: SignalInstance,
    failed: SignalInstance,
) -> None:
    def run() -> None:
        try:
            result = work()
        except (httpx.HTTPError, ValueError) as exc:
            failed.emit(str(exc))
            return
        if result is not None:
            succeeded.emit(result)

    threading.Thread(target=run, daemon=True, name=name).start()


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
        _run_in_background(
            "fmv-update-check",
            lambda: fetch_available_update(current_version),
            self.update_available,
            self.failed,
        )
        return True


class ReleaseNotesLoader(QObject):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.loading = False
        self.loaded.connect(self._finished)
        self.failed.connect(self._finished)

    def load(self, current_version: str) -> bool:
        if self.loading:
            return False
        self.loading = True
        _run_in_background(
            "fmv-release-notes",
            lambda: fetch_releases_since(current_version),
            self.loaded,
            self.failed,
        )
        return True

    def _finished(self, *_args: object) -> None:
        self.loading = False
