"""Farm Merge Valley portal integration metadata and recognition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class GamePortal(StrEnum):
    REDDIT = "reddit"
    CRAZY_GAMES = "crazygames"
    POGO = "pogo"
    MSN = "msn"
    DISCORD = "discord"
    AGAME = "agame"
    FACEBOOK = "facebook"
    YAHOO = "yahoo"


class PortalSupportLevel(StrEnum):
    OBSERVATION = "observation"
    AUTOMATION = "automation"


class PortalStartupStrategy(StrEnum):
    DIRECT = "direct"
    REDDIT_LAUNCHER = "reddit-launcher"


@dataclass(frozen=True)
class PortalDefinition:
    kind: GamePortal
    display_name: str
    page_hosts: tuple[str, ...]
    support_level: PortalSupportLevel
    startup_strategy: PortalStartupStrategy
    canonical_url: str
    game_frame_matcher: Callable[[str, str], bool]

    def matches_page_url(self, value: object) -> bool:
        host = _hostname(value)
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in self.page_hosts)

    def matches_game_frame_url(self, value: object) -> bool:
        parts = urlsplit(str(value))
        host = (parts.hostname or "").casefold()
        path = parts.path.casefold()
        return self.game_frame_matcher(host, path)


def _hostname(value: object) -> str:
    return (urlsplit(str(value)).hostname or "").casefold()


PORTALS = (
    PortalDefinition(
        GamePortal.REDDIT,
        "Reddit",
        ("reddit.com",),
        PortalSupportLevel.AUTOMATION,
        PortalStartupStrategy.REDDIT_LAUNCHER,
        "https://www.reddit.com/r/FarmMergeValley/",
        lambda host, path: (
            host.startswith("playfmv-")
            and host.endswith(".devvit.net")
            and path.endswith("/index.html")
        ),
    ),
    PortalDefinition(
        GamePortal.CRAZY_GAMES,
        "CrazyGames",
        ("crazygames.com",),
        PortalSupportLevel.AUTOMATION,
        PortalStartupStrategy.DIRECT,
        "https://www.crazygames.com/game/farm-merge-valley",
        lambda host, path: (
            host == "farm-merge-valley.game-files.crazygames.com"
            and path.startswith("/farm-merge-valley/")
            and path.endswith("/index.html")
        ),
    ),
    PortalDefinition(
        GamePortal.POGO,
        "Pogo",
        ("pogo.com",),
        PortalSupportLevel.AUTOMATION,
        PortalStartupStrategy.DIRECT,
        "https://www.pogo.com/games/farm-merge-valley/play",
        lambda host, path: (
            host == "cdn-h5farmvalley-prod.pogospike.com"
            and path.count("/") == 2
            and path.endswith("/index.html")
            and path.split("/")[1].isdigit()
        ),
    ),
    PortalDefinition(
        GamePortal.MSN,
        "MSN",
        ("msn.com",),
        PortalSupportLevel.AUTOMATION,
        PortalStartupStrategy.DIRECT,
        "https://www.msn.com/en-nz/play/games/farm-merge-valley/cg-9nf2hg8fnlts",
        lambda host, path: (
            host == "cdn.games.mobinozer.com" and path.startswith("/ext/farm_merge_microsoft/prod/")
        ),
    ),
    PortalDefinition(
        GamePortal.DISCORD,
        "Discord Activities",
        ("discord.com",),
        PortalSupportLevel.AUTOMATION,
        PortalStartupStrategy.DIRECT,
        "https://discord.com/activities/1187013846746005515",
        lambda host, path: host == "1187013846746005515.discordsays.com" and path == "/",
    ),
    PortalDefinition(
        GamePortal.AGAME,
        "Agame",
        ("agame.com",),
        PortalSupportLevel.AUTOMATION,
        PortalStartupStrategy.DIRECT,
        "https://www.agame.com/game/farm-merge-valley",
        lambda host, path: (
            host == "cdn.games.mobinozer.com"
            and path.startswith("/ext/farm_merge_azerion/production/")
        ),
    ),
    PortalDefinition(
        GamePortal.FACEBOOK,
        "Facebook Gaming",
        ("facebook.com",),
        PortalSupportLevel.AUTOMATION,
        PortalStartupStrategy.DIRECT,
        "https://www.facebook.com/gaming/play/farm_merge_valley",
        lambda host, path: (
            host == "apps-158098743030140.apps.fbsbx.com"
            and path.startswith("/instant-bundle/")
            and path.endswith("/index.html")
        ),
    ),
    PortalDefinition(
        GamePortal.YAHOO,
        "Yahoo Games",
        ("yahoo.com",),
        PortalSupportLevel.AUTOMATION,
        PortalStartupStrategy.DIRECT,
        "https://www.yahoo.com/games/play/farm-merge-valley",
        lambda host, path: (
            host == "farm-merge-valley-yhus.mobinozer.com" and path.startswith("/production/")
        ),
    ),
)


def portal_definition(kind: GamePortal) -> PortalDefinition:
    return next(portal for portal in PORTALS if portal.kind is kind)


def portal_for_page_url(value: object) -> PortalDefinition | None:
    return next((portal for portal in PORTALS if portal.matches_page_url(value)), None)
