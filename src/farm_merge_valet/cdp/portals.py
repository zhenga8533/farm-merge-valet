"""Portal-specific page and game-frame recognition."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class GamePortal(StrEnum):
    REDDIT = "reddit"
    CRAZY_GAMES = "crazygames"


@dataclass(frozen=True)
class PortalDefinition:
    kind: GamePortal
    display_name: str
    page_hosts: tuple[str, ...]
    automation_supported: bool

    def matches_page_url(self, value: object) -> bool:
        host = _hostname(value)
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in self.page_hosts)

    def matches_game_frame_url(self, value: object) -> bool:
        parts = urlsplit(str(value))
        host = (parts.hostname or "").casefold()
        path = parts.path.casefold()
        if self.kind is GamePortal.REDDIT:
            return host.startswith("playfmv-") and path.endswith("/index.html")
        if self.kind is GamePortal.CRAZY_GAMES:
            return (
                host == "farm-merge-valley.game-files.crazygames.com"
                and path.startswith("/farm-merge-valley/")
                and path.endswith("/index.html")
            )
        return False


def _hostname(value: object) -> str:
    return (urlsplit(str(value)).hostname or "").casefold()


PORTALS = (
    PortalDefinition(
        GamePortal.REDDIT,
        "Reddit",
        ("reddit.com",),
        automation_supported=True,
    ),
    PortalDefinition(
        GamePortal.CRAZY_GAMES,
        "CrazyGames",
        ("crazygames.com",),
        automation_supported=False,
    ),
)


def portal_definition(kind: GamePortal) -> PortalDefinition:
    return next(portal for portal in PORTALS if portal.kind is kind)
