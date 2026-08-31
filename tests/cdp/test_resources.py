from __future__ import annotations

import base64

from farm_merge_valet.cdp.resources import read_game_frame_resources


def test_binary_resource_reader_fetches_resources_missing_from_page_cache(monkeypatch) -> None:
    expressions = []
    monkeypatch.setattr(
        "farm_merge_valet.cdp.resources.run_game_frame_operation",
        lambda *_args, **_kwargs: {},
    )

    def evaluate(_port, expression, _title, **_kwargs):
        expressions.append(expression)
        return {"https://cdn.test/atlases/high/map.png": base64.b64encode(b"high").decode()}

    monkeypatch.setattr("farm_merge_valet.cdp.resources.evaluate", evaluate)

    result = read_game_frame_resources(
        9222,
        ["https://cdn.test/atlases/high/map.png"],
        "Farm",
    )

    assert result == {"https://cdn.test/atlases/high/map.png": b"high"}
    assert "fetch(url)" in expressions[0]
