from __future__ import annotations

import json
from pathlib import Path

from farm_merge_valet.core.board_map import StaticTileKind, load_board_map


def _write_map(path: Path) -> None:
    data = {
        "width": 10,
        "height": 10,
        "tilewidth": 320,
        "tileheight": 160,
        "locked_tiles": [[1, 1], [2, 2]],
        "premium_tiles": [[3, 3]],
        "decoration_tiles": [[2, 2], [4, 4]],  # (2,2) deliberately overlaps locked
    }
    path.write_text(json.dumps(data))


def test_load_board_map_classifies_tiles(tmp_path: Path) -> None:
    map_path = tmp_path / "test_map.json"
    _write_map(map_path)
    board_map = load_board_map(map_path)

    assert board_map.classify((1, 1)) is StaticTileKind.LOCKED
    assert board_map.classify((3, 3)) is StaticTileKind.PREMIUM
    assert board_map.classify((4, 4)) is StaticTileKind.DECORATION
    assert board_map.classify((0, 0)) is None


def test_load_board_map_decoration_takes_priority_over_locked(tmp_path: Path) -> None:
    map_path = tmp_path / "test_map.json"
    _write_map(map_path)
    board_map = load_board_map(map_path)

    # (2, 2) is in both locked_tiles and decoration_tiles.
    assert board_map.classify((2, 2)) is StaticTileKind.DECORATION


def test_is_scan_candidate_excludes_cells_adjacent_to_decoration(tmp_path: Path) -> None:
    map_path = tmp_path / "test_map.json"
    _write_map(map_path)
    board_map = load_board_map(map_path)

    # (4, 4) is DECORATION; its orthogonal neighbors are plain (unlisted)
    # coords, but should still be excluded -- large decoration sprites
    # visually bleed into a directly-adjacent cell's classification crop.
    assert board_map.is_scan_candidate((5, 4)) is False
    assert board_map.is_scan_candidate((3, 4)) is False
    assert board_map.is_scan_candidate((4, 5)) is False
    assert board_map.is_scan_candidate((4, 3)) is False
    # A diagonal neighbor doesn't share a crop edge, so it isn't excluded.
    assert board_map.is_scan_candidate((5, 5)) is True
    # A coordinate itself classified as something isn't a scan candidate.
    assert board_map.is_scan_candidate((1, 1)) is False
    # Plain, unrelated ground stays a scan candidate.
    assert board_map.is_scan_candidate((0, 0)) is True
