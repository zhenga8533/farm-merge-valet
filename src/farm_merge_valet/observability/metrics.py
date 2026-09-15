"""Stable metric definitions derived from structured application events."""

from __future__ import annotations

import logging
from dataclasses import dataclass


@dataclass(frozen=True)
class MetricUpdate:
    name: str
    value: float = 1.0
    dimensions: tuple[tuple[str, str], ...] = ()


def _dimensions(**values: object) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (name, str(value))
            for name, value in values.items()
            if value is not None and isinstance(value, (str, int, bool))
        )
    )


def metrics_for_event(
    event: str, context: dict[str, object], level: int
) -> tuple[MetricUpdate, ...]:
    updates: list[MetricUpdate] = []
    if event == "action.confirmed":
        effect = str(context.get("effect", "item_action"))
        updates.append(
            MetricUpdate(
                f"action.{effect}",
                dimensions=_dimensions(
                    category=context.get("item_category"),
                    item=context.get("item_name"),
                    tier=context.get("item_tier"),
                    planner=context.get("planner_action"),
                    target_size=context.get("target_size"),
                ),
            )
        )
    elif event == "interaction.confirmed":
        kind = str(context.get("interaction_kind", "board_interaction"))
        updates.append(
            MetricUpdate(
                f"interaction.{kind}",
                dimensions=_dimensions(
                    blueprint=context.get("blueprint_id"),
                    producer=context.get("producer_kind"),
                ),
            )
        )
        energy_cost = context.get("energy_cost")
        if kind == "clear" and isinstance(energy_cost, int) and not isinstance(energy_cost, bool):
            updates.append(MetricUpdate("spent.energy", energy_cost))
    elif event == "crate.claim_completed":
        spawned = context.get("spawned")
        if isinstance(spawned, int) and not isinstance(spawned, bool):
            updates.append(MetricUpdate("crates", spawned))
    elif event in {"shop.order_started", "shop.order_claimed"}:
        action = "started" if event.endswith("started") else "claimed"
        updates.append(
            MetricUpdate(
                f"shop.{action}",
                dimensions=_dimensions(
                    shop=context.get("shop_id"), recipe=context.get("recipe_id")
                ),
            )
        )
    elif event == "marketplace.purchase_confirmed":
        updates.append(
            MetricUpdate(
                "workflow.marketplace",
                dimensions=_dimensions(offer=context.get("policy_key")),
            )
        )
        payment_key = context.get("payment_key")
        payment_amount = context.get("payment_amount")
        if isinstance(payment_key, str) and isinstance(payment_amount, int):
            updates.append(MetricUpdate(f"spent.{payment_key}", payment_amount))
    elif event.startswith("farm_visit.") and event.endswith("_confirmed"):
        action = event.removeprefix("farm_visit.").removesuffix("_confirmed")
        if action in {"claim", "return"}:
            updates.append(MetricUpdate(f"workflow.farm_visit_{action}"))
    elif event == "land_expansion.confirmed":
        cells = context.get("cell_count")
        updates.append(MetricUpdate("workflow.land_expansion"))
        if isinstance(cells, int):
            updates.append(MetricUpdate("progress.land_cells", cells))
        currency = context.get("currency")
        cost = context.get("cost")
        if isinstance(currency, str) and isinstance(cost, int):
            updates.append(MetricUpdate(f"spent.{currency}", cost))
    elif event == "storage_bubble.confirmed":
        updates.append(MetricUpdate("workflow.storage_bubble"))
        popped = context.get("popped_items")
        if isinstance(popped, int):
            updates.append(MetricUpdate("items.storage_bubble", popped))
    elif event == "event.reward_claimed":
        updates.append(
            MetricUpdate(
                "workflow.event_reward",
                dimensions=_dimensions(
                    event=context.get("event_key"),
                    track=context.get("track"),
                    reward=context.get("reward_key"),
                ),
            )
        )
        amount = context.get("reward_amount")
        reward = context.get("reward_key")
        if isinstance(reward, str) and isinstance(amount, int):
            updates.append(MetricUpdate(f"received.{reward}", amount))
    elif event.startswith("event.") and event.endswith("_confirmed"):
        action = event.removeprefix("event.").removesuffix("_confirmed")
        updates.append(
            MetricUpdate(
                f"workflow.event_{action}",
                dimensions=_dimensions(event=context.get("event_key")),
            )
        )
    elif event == "overlay.dismissed":
        updates.append(
            MetricUpdate(
                "workflow.overlay_dismissed",
                dimensions=_dimensions(overlay=context.get("overlay")),
            )
        )
    elif event == "runtime.recovery_started":
        updates.append(MetricUpdate("reliability.recovery"))
    elif event == "planner.board_blocked":
        updates.append(MetricUpdate("reliability.board_blocked"))
    elif event in {
        "action.not_accepted",
        "action.submission_rejected",
        "interaction.not_accepted",
        "interaction.rejected",
        "shop.action_not_accepted",
        "shop.action_rejected",
        "storage_bubble.not_accepted",
        "storage_bubble.rejected",
    }:
        updates.append(
            MetricUpdate("reliability.action_failure", dimensions=_dimensions(event=event))
        )
    if logging.WARNING <= level < logging.ERROR:
        updates.append(MetricUpdate("warnings", dimensions=_dimensions(event=event)))
    elif level >= logging.ERROR:
        updates.append(MetricUpdate("errors", dimensions=_dimensions(event=event)))
    return tuple(updates)
