"""Pure item identities and policy-key helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

GridCoord = tuple[int, int]
_ITEM_TIER_POLICY_SEGMENT = "/tier/"


def item_tier_policy_key(policy_key: str, tier: int) -> str:
    return f"{policy_key}{_ITEM_TIER_POLICY_SEGMENT}{tier}"


def item_family_policy_key(policy_key: str) -> str:
    return policy_key.partition(_ITEM_TIER_POLICY_SEGMENT)[0]


def item_base_policy_key(policy_key: str) -> str:
    category, separator, family_and_variant = policy_key.partition("/")
    family = family_and_variant.partition("/")[0]
    return f"{category}{separator}{family}"


class InteractionTargetKind(StrEnum):
    IMMEDIATE = "immediate"
    REWARD = "reward"
    REMOVE = "remove"
    PRODUCER = "producer"
    DEPLETED_PRODUCER = "depleted-producer"


class ProducerKind(StrEnum):
    ANIMAL = "animal"
    CROP = "crop"


class ProducerState(StrEnum):
    READY = "ready"
    COOLING = "cooling"
    DEPLETED = "depleted"


@dataclass(frozen=True)
class ItemRef:
    """Identifies one tier in a cataloged merge family."""

    category: str
    name: str
    tier: int
    variant: str | None = None

    @property
    def policy_key(self) -> str:
        key = f"{self.category}/{self.name}"
        return f"{key}/{self.variant}" if self.variant is not None else key

    @property
    def identity(self) -> tuple[str, str, str | None]:
        return self.category, self.name, self.variant

    @property
    def tier_policy_key(self) -> str:
        return item_tier_policy_key(self.policy_key, self.tier)
