from __future__ import annotations

from farm_merge_valet.cdp.item_catalog import read_runtime_atlas_urls


def test_runtime_atlas_urls_are_deduplicated_and_type_checked(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.item_catalog.list_game_frame_resources",
        lambda *_args, **_kwargs: [
            "https://cdn.test/atlases/map.png",
            "https://cdn.test/atlases/map.png",
            "https://cdn.test/spines/animal.png",
            "https://cdn.test/audio/theme.mp3",
        ],
    )

    assert read_runtime_atlas_urls(9222, "game") == [
        "https://cdn.test/atlases/map.png",
        "https://cdn.test/spines/animal.png",
    ]
