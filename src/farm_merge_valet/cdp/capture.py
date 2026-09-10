"""Privacy-scoped captures of the verified game renderer."""

from __future__ import annotations

import base64
import binascii

from farm_merge_valet.cdp.targets import _load_targets, discover_game_targets
from farm_merge_valet.cdp.transport import CdpConnectionError, _command_target

_MAX_CAPTURE_BYTES = 8 * 1024 * 1024


def capture_game_screenshot(port: int, page_title: str | None = None) -> bytes:
    """Capture only the uniquely matched game target, excluding its portal page."""
    matches = discover_game_targets(_load_targets(port), page_title)
    if len(matches) != 1:
        raise CdpConnectionError(
            f"Expected exactly one game target for screenshot capture, found {len(matches)}."
        )
    result = _command_target(
        matches[0].game_ws_url,
        "Page.captureScreenshot",
        {
            "format": "jpeg",
            "quality": 75,
            "fromSurface": True,
            "captureBeyondViewport": False,
        },
        timeout=10.0,
    )
    encoded = result.get("data")
    if not isinstance(encoded, str):
        raise CdpConnectionError("The game target returned no screenshot data.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise CdpConnectionError("The game target returned invalid screenshot data.") from exc
    if not content.startswith(b"\xff\xd8\xff"):
        raise CdpConnectionError("The game target returned an invalid JPEG screenshot.")
    if len(content) > _MAX_CAPTURE_BYTES:
        raise CdpConnectionError("The game screenshot exceeds the attachment size limit.")
    return content
