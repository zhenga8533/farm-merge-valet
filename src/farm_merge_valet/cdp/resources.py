"""Loaded game-frame resource enumeration and retrieval."""

from __future__ import annotations

import base64
import json
from threading import Event
from typing import Any

from farm_merge_valet.cdp.evaluation import evaluate
from farm_merge_valet.cdp.targets import run_game_frame_operation
from farm_merge_valet.cdp.transport import _CDP_COMMAND_TIMEOUT, _command_target


def list_game_frame_resources(
    port: int,
    page_title: str | None = None,
    *,
    cancel_event: Event | None = None,
) -> list[str]:
    """Return resource URLs retained by the loaded game frame's page tree."""
    result = run_game_frame_operation(
        port,
        page_title,
        lambda ws_url: _command_target(
            ws_url,
            "Page.getResourceTree",
            timeout=_CDP_COMMAND_TIMEOUT,
            cancel_event=cancel_event,
        ),
    )
    root = result.get("frameTree")
    if not isinstance(root, dict):
        return []
    urls: set[str] = set()
    pending = [root]
    while pending:
        frame = pending.pop()
        resources = frame.get("resources")
        if isinstance(resources, list):
            urls.update(
                value
                for resource in resources
                if isinstance(resource, dict) and isinstance(value := resource.get("url"), str)
            )
        children = frame.get("childFrames")
        if isinstance(children, list):
            pending.extend(child for child in children if isinstance(child, dict))
    return sorted(urls)


def read_game_frame_resources(
    port: int,
    urls: list[str],
    page_title: str | None = None,
    *,
    cancel_event: Event | None = None,
) -> dict[str, bytes]:
    """Read already-loaded resources from Chrome's page cache through CDP."""

    def read(ws_url: str) -> dict[str, bytes]:
        _command_target(ws_url, "Page.enable", cancel_event=cancel_event)
        result = _command_target(ws_url, "Page.getResourceTree", cancel_event=cancel_event)
        root = result.get("frameTree")
        if not isinstance(root, dict):
            return {}
        frame_ids = _resource_frame_ids(root)
        contents: dict[str, bytes] = {}
        for url in urls:
            frame_id = frame_ids.get(url)
            if frame_id is None:
                continue
            value = _command_target(
                ws_url,
                "Page.getResourceContent",
                {"frameId": frame_id, "url": url},
                timeout=15,
                cancel_event=cancel_event,
            )
            content = value.get("content")
            if not isinstance(content, str):
                continue
            contents[url] = (
                base64.b64decode(content)
                if value.get("base64Encoded") is True
                else content.encode()
            )
        return contents

    return run_game_frame_operation(port, page_title, read)


def _resource_frame_ids(root: dict[str, Any]) -> dict[str, str]:
    frame_ids: dict[str, str] = {}
    pending = [root]
    while pending:
        frame_tree = pending.pop()
        frame = frame_tree.get("frame")
        frame_id = frame.get("id") if isinstance(frame, dict) else None
        resources = frame_tree.get("resources")
        if isinstance(frame_id, str) and isinstance(resources, list):
            for resource in resources:
                if isinstance(resource, dict) and isinstance(resource.get("url"), str):
                    frame_ids[resource["url"]] = frame_id
        children = frame_tree.get("childFrames")
        if isinstance(children, list):
            pending.extend(child for child in children if isinstance(child, dict))
    return frame_ids


def read_game_frame_text_resources(
    port: int,
    urls: list[str],
    page_title: str | None = None,
) -> dict[str, str]:
    """Fetch text resources inside the game frame's origin and session."""
    expression = f"""
    (async () => {{
      const urls = {json.dumps(urls)};
      const values = await Promise.all(urls.map(async url => {{
        try {{
          const response = await fetch(url);
          return response.ok ? [url, await response.text()] : null;
        }} catch (_) {{
          return null;
        }}
      }}));
      return Object.fromEntries(values.filter(Boolean));
    }})()
    """
    raw = evaluate(port, expression, page_title, timeout=30)
    if not isinstance(raw, dict):
        return {}
    return {
        url: content
        for url, content in raw.items()
        if isinstance(url, str) and isinstance(content, str)
    }
