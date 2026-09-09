"""Dispatch portal lifecycle behavior without coupling browser or runtime orchestration."""

from __future__ import annotations

from collections.abc import Callable

from farm_merge_valet.cdp.targets import (
    _load_targets,
    discover_game_targets,
    dismiss_pogo_inactivity_prompt,
    try_start_game,
)
from farm_merge_valet.integrations import GamePortal, PortalDefinition, PortalStartupStrategy

StartupHandler = Callable[[int, str | None, GamePortal], bool]
MaintenanceHandler = Callable[[int, str | None], bool]


def _no_startup(_port: int, _page_title: str | None, _portal: GamePortal) -> bool:
    return False


_STARTUP_HANDLERS: dict[PortalStartupStrategy, StartupHandler] = {
    PortalStartupStrategy.DIRECT: _no_startup,
    PortalStartupStrategy.REDDIT_LAUNCHER: try_start_game,
}

_MAINTENANCE_HANDLERS: dict[GamePortal, MaintenanceHandler] = {
    GamePortal.POGO: dismiss_pogo_inactivity_prompt,
}


def request_game_start(port: int, page_title: str | None, portal: PortalDefinition) -> bool:
    """Run the startup behavior declared by a portal definition."""
    return _STARTUP_HANDLERS[portal.startup_strategy](port, page_title, portal.kind)


def maintain_portal_session(port: int, page_title: str | None) -> tuple[GamePortal, bool] | None:
    """Run maintenance for the single selected portal when it declares a handler."""
    discovered = discover_game_targets(_load_targets(port), page_title)
    if len(discovered) != 1:
        return None
    portal = discovered[0].portal
    handler = _MAINTENANCE_HANDLERS.get(portal)
    if handler is None:
        return None
    return portal, handler(port, page_title)
