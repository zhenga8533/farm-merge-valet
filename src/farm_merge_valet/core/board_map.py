"""Static board layout lookup -- locked/premium/decorated tile positions,
extracted once from the game's own level data (see
`tools/template_extraction.extract_board_map`) since the map is the same
for every player. Checking a coordinate against this is essentially free,
unlike determining the same information via screen-capture vision (see
`core/board_scan.py`'s much more expensive `discover_board_coords`, which
this replaces for every tile the static data actually covers).
"""

from __future__ import annotations

import json
from enum import Enum, auto
from pathlib import Path

from farm_merge_valet.core.board import GridCoord


class StaticTileKind(Enum):
    """What the game's own level data says about a tile -- independent of
    whether *this player* has unlocked it yet (the static map represents
    the full original layout, not per-player progress; a tile classified
    LOCKED here may already be plain farmland for an account that's
    unlocked it -- confirmed live)."""

    LOCKED = auto()
    PREMIUM = auto()
    DECORATION = auto()


class BoardMap:
    def __init__(
        self,
        locked_tiles: set[GridCoord],
        premium_tiles: set[GridCoord],
        decoration_tiles: set[GridCoord],
    ) -> None:
        self._locked = locked_tiles
        self._premium = premium_tiles
        self._decoration = decoration_tiles

    def classify(self, coord: GridCoord) -> StaticTileKind | None:
        """Returns None for a coordinate the static data says nothing
        special about -- either plain, always-farmable ground, or simply
        outside the map's actual extent. Decoration takes priority over
        locked/premium: a tile can be marked locked in the base layout
        and still have a permanent object (tree, rock) placed on it,
        which makes it non-farmable regardless of unlock state.
        """
        if coord in self._decoration:
            return StaticTileKind.DECORATION
        if coord in self._locked:
            return StaticTileKind.LOCKED
        if coord in self._premium:
            return StaticTileKind.PREMIUM
        return None

    def is_scan_candidate(self, coord: GridCoord) -> bool:
        """True if `coord` is safe to hand to vision-based item scanning:
        `classify` says nothing special about it, *and* it isn't
        orthogonally adjacent to a DECORATION tile.

        Large decoration sprites (a barn, silos, a windmill in the game's
        home-base compound) are visually wider than one grid tile, so
        their art spills into a directly-adjacent cell's classification
        crop; `_classify_cell`'s overlap-tolerance check (see
        core/board_scan.py) doesn't reliably reject that spillover --
        confirmed live, every false item match found this way had a
        DECORATION neighbor. Excluding those neighbors too is blunt (it
        can also skip genuinely empty farmland next to decoration), but a
        missed empty cell costs nothing, where a merge acted on a phantom
        item destroys real board content.
        """
        if self.classify(coord) is not None:
            return False
        col, row = coord
        neighbors = ((col + 1, row), (col - 1, row), (col, row + 1), (col, row - 1))
        return not any(self.classify(n) is StaticTileKind.DECORATION for n in neighbors)


def _coord_set(raw: list[list[int]]) -> set[GridCoord]:
    return {(c[0], c[1]) for c in raw}


def load_board_map(path: Path) -> BoardMap:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return BoardMap(
        _coord_set(data["locked_tiles"]),
        _coord_set(data["premium_tiles"]),
        _coord_set(data["decoration_tiles"]),
    )
