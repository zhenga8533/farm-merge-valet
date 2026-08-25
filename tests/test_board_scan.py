from __future__ import annotations

import json
from pathlib import Path

from farm_merge_valet.core.board import ItemRef
from farm_merge_valet.core.board_scan import load_blueprint_items


def test_load_blueprint_items_uses_catalog_metadata(tmp_path: Path) -> None:
    (tmp_path / "catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "items": {
                    "upgrade_card_1": {
                        "family_id": "upgrade_card",
                        "policy_key": "upgrade_cards/upgrade_card",
                        "category": "upgrade_cards",
                        "display_name": "Upgrade Card",
                        "tier": 1,
                        "mergeable": True,
                        "merge_target": "upgrade_card_2",
                        "asset_alias": "obj_upgradecard_bg01",
                        "asset_path": "upgrade_cards/upgrade_card/upgrade_card_1.png",
                        "capabilities": ["mergeable", "shovelable"],
                    },
                    "upgrade_card_2": {
                        "family_id": "upgrade_card",
                        "policy_key": "upgrade_cards/upgrade_card",
                        "category": "upgrade_cards",
                        "display_name": "Upgrade Card",
                        "tier": 2,
                        "mergeable": False,
                        "merge_target": None,
                        "asset_alias": "obj_upgradecard_bg02",
                        "asset_path": "upgrade_cards/upgrade_card/upgrade_card_2.png",
                        "capabilities": ["merge-result", "shovelable"],
                    },
                    "decorative_well": {
                        "family_id": "decorative_well",
                        "policy_key": "repairable_buildings/decorative_well",
                        "category": "repairable_buildings",
                        "display_name": "Well",
                        "tier": None,
                        "mergeable": False,
                        "merge_target": None,
                        "asset_alias": "obj_decorateive_well_broken",
                        "asset_path": ("repairable_buildings/decorative_well/decorative_well.png"),
                        "capabilities": ["building"],
                    },
                },
                "variants": {},
            }
        ),
        encoding="utf-8",
    )

    assert load_blueprint_items(tmp_path) == {
        "upgrade_card_1": ItemRef("upgrade_cards", "upgrade_card", 1),
        "upgrade_card_2": ItemRef("upgrade_cards", "upgrade_card", 2),
    }
