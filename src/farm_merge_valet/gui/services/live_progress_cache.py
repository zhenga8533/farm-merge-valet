"""Persist the last authoritative building and upgrade progress for GUI startup."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from farm_merge_valet.automation.runtime import BuildingRepairState, BuildingRequirement
from farm_merge_valet.core.upgrade_progress import UpgradeProgress, UpgradeTargetProgress

_CACHE_VERSION = 1
_cache_lock = Lock()


@dataclass(frozen=True)
class CachedLiveProgress:
    upgrades: UpgradeProgress | None = None
    buildings: tuple[BuildingRepairState, ...] | None = None


def load_live_progress_cache(catalog_dir: Path) -> CachedLiveProgress:
    path = _cache_path(catalog_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CachedLiveProgress()
    if not isinstance(value, dict) or value.get("version") != _CACHE_VERSION:
        return CachedLiveProgress()
    return CachedLiveProgress(
        upgrades=_parse_upgrades(value.get("upgrades")),
        buildings=_parse_buildings(value.get("buildings")),
    )


def write_live_progress_cache(
    catalog_dir: Path,
    upgrades: UpgradeProgress | None,
    buildings: tuple[BuildingRepairState, ...] | None,
) -> None:
    path = _cache_path(catalog_dir)
    payload = {
        "version": _CACHE_VERSION,
        "upgrades": _serialize_upgrades(upgrades),
        "buildings": _serialize_buildings(buildings),
    }
    with _cache_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
        temporary.replace(path)


def _cache_path(catalog_dir: Path) -> Path:
    return catalog_dir.parent / f".{catalog_dir.name}.live-progress.json"


def _serialize_upgrades(progress: UpgradeProgress | None) -> list[dict[str, object]] | None:
    if progress is None:
        return None
    return [
        {
            "targetID": target.target_id,
            "producerBlueprintID": target.producer_blueprint_id,
            "appliedTier": target.applied_tier,
        }
        for target in progress.targets
    ]


def _parse_upgrades(value: object) -> UpgradeProgress | None:
    if not isinstance(value, list):
        return None
    targets: list[UpgradeTargetProgress] = []
    for entry in value:
        if not isinstance(entry, dict):
            return None
        target_id = entry.get("targetID")
        producer_id = entry.get("producerBlueprintID")
        applied_tier = entry.get("appliedTier")
        if (
            not isinstance(target_id, str)
            or not isinstance(producer_id, str)
            or not isinstance(applied_tier, int)
            or isinstance(applied_tier, bool)
            or applied_tier < 0
        ):
            return None
        targets.append(UpgradeTargetProgress(target_id, producer_id, applied_tier))
    return UpgradeProgress(tuple(targets))


def _serialize_buildings(
    buildings: tuple[BuildingRepairState, ...] | None,
) -> list[dict[str, object]] | None:
    if buildings is None:
        return None
    return [
        {
            "buildingID": state.building_id,
            "level": state.level,
            "workshop": state.workshop,
            "placed": state.placed,
            "active": state.active,
            "upgrading": state.upgrading,
            "requirements": [
                {
                    "blueprintID": requirement.blueprint_id,
                    "amount": requirement.amount,
                    "available": requirement.available,
                }
                for requirement in state.requirements
            ],
        }
        for state in buildings
    ]


def _parse_buildings(value: object) -> tuple[BuildingRepairState, ...] | None:
    if not isinstance(value, list):
        return None
    states: list[BuildingRepairState] = []
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("buildingID"), str):
            return None
        requirements: list[BuildingRequirement] = []
        for requirement in entry.get("requirements", []):
            if not isinstance(requirement, dict):
                return None
            blueprint_id = requirement.get("blueprintID")
            amount = requirement.get("amount")
            available = requirement.get("available")
            if (
                not isinstance(blueprint_id, str)
                or not isinstance(amount, int)
                or isinstance(amount, bool)
                or amount <= 0
                or not isinstance(available, int)
                or isinstance(available, bool)
                or available < 0
            ):
                return None
            requirements.append(BuildingRequirement(blueprint_id, amount, available))
        level = entry.get("level")
        states.append(
            BuildingRepairState(
                entry["buildingID"],
                level if isinstance(level, int) and not isinstance(level, bool) else 0,
                entry.get("workshop") is True,
                entry.get("placed") is True,
                entry.get("active") is True,
                entry.get("upgrading") is True,
                tuple(requirements),
            )
        )
    return tuple(states)
