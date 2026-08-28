"""JavaScript evaluation and browser lifecycle overrides."""

from __future__ import annotations

import logging
from threading import Event

from websockets.exceptions import WebSocketException

from farm_merge_valet.cdp.targets import (
    _run_top_page_operation,
    _target_pair,
    run_game_frame_operation,
)
from farm_merge_valet.cdp.transport import (
    _CDP_COMMAND_TIMEOUT,
    CdpCancelledError,
    CdpConnectionError,
    CdpTimeoutError,
    _command_target,
)
from farm_merge_valet.observability.logging import log_event

logger = logging.getLogger(__name__)


def _evaluate_target(
    ws_url: str,
    expression: str,
    *,
    timeout: float = _CDP_COMMAND_TIMEOUT,
    cancel_event: Event | None = None,
) -> object:
    try:
        command_result = _command_target(
            ws_url,
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout=timeout,
            cancel_event=cancel_event,
        )
        if exception := command_result.get("exceptionDetails"):
            description = exception.get("exception", {}).get("description")
            raise CdpConnectionError(
                f"CDP JavaScript evaluation failed: {description or exception.get('text')}"
            )
        result = command_result.get("result", {})
        return result.get("value") if isinstance(result, dict) else None
    except (CdpConnectionError, CdpCancelledError, CdpTimeoutError):
        raise
    except (OSError, ValueError, WebSocketException, TimeoutError) as exc:
        raise CdpConnectionError("Lost the browser DevTools WebSocket connection.") from exc


def evaluate(
    port: int,
    expression: str,
    page_title: str | None = None,
    *,
    timeout: float = _CDP_COMMAND_TIMEOUT,
    cancel_event: Event | None = None,
) -> object:
    """Evaluate `expression` in the game iframe's JS context and return its
    value (must be JSON-serializable -- CDP's `returnByValue` requirement).
    """
    return run_game_frame_operation(
        port,
        page_title,
        lambda ws_url: _evaluate_target(
            ws_url, expression, timeout=timeout, cancel_event=cancel_event
        ),
    )


def evaluate_top_page(
    port: int,
    expression: str,
    page_title: str | None = None,
    *,
    timeout: float = _CDP_COMMAND_TIMEOUT,
    cancel_event: Event | None = None,
) -> object:
    """Evaluate an expression in the top-level Reddit page's JS context."""
    return _run_top_page_operation(
        port,
        page_title,
        lambda ws_url: _evaluate_target(
            ws_url, expression, timeout=timeout, cancel_event=cancel_event
        ),
    )


def apply_background_overrides(
    port: int, page_title: str | None = None, *, cancel_event: Event | None = None
) -> None:
    """Apply supported lifecycle/focus overrides without activating a window."""
    game_ws, page_ws = _target_pair(port, page_title)
    for ws_url in (page_ws, game_ws):
        # Unsupported methods are deliberately ignored individually: protocol
        # support differs by browser version, while the launch flags remain the
        # primary protection against background throttling.
        for method, params in (
            ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
            ("Emulation.setIdleOverride", {"isUserActive": True, "isScreenUnlocked": True}),
            ("Page.setWebLifecycleState", {"state": "active"}),
        ):
            try:
                _command_target(ws_url, method, params, cancel_event=cancel_event)
            except CdpCancelledError:
                raise
            except CdpConnectionError:
                log_event(
                    logger,
                    logging.DEBUG,
                    "cdp.override_unavailable",
                    "CDP override %s is unavailable for this target.",
                    method,
                    method=method,
                )
