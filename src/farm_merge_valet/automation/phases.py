"""Planner phases shared by orchestration and workflows."""

from enum import Enum, auto


class Phase(Enum):
    INTERACT_TILES = auto()
    SHOPS = auto()
    MARKETPLACE = auto()
    CLAIM_CRATES = auto()
    MERGE = auto()
    FARM_VISITS = auto()
    LAND_EXPANSION = auto()
    BUILDING_REPAIRS = auto()
