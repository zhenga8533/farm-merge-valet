"""Exclusive offline retries for an explicitly enabled merge-3 policy."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Literal

from farm_merge_valet.automation.runtime import (
    LiveCellState,
    LuckyMergeNetworkControl,
    RuntimeConnectionError,
    SnapshotOptions,
)
from farm_merge_valet.automation.timing import DEFAULT_ACTION_TIMING, next_recovery_delay
from farm_merge_valet.core.items import GridCoord, ItemRef
from farm_merge_valet.core.merge_planner import MergeAction, MergeActionKind, MoveEffect
from farm_merge_valet.observability.logging import log_event

if TYPE_CHECKING:
    from farm_merge_valet.automation.context import WorkflowContext as Bot

logger = logging.getLogger(__name__)
_INITIAL_RELOAD_DISCOVERY_INTERVAL_SECONDS = 2.0
_BOARD_SETTLE_SECONDS = 1.0


class LuckyMergeError(RuntimeError):
    """The offline attempt cannot be completed without risking an unintended save."""


def _matching_count(
    cells: dict[GridCoord, LiveCellState],
    items: dict[str, ItemRef],
    item: ItemRef,
) -> int:
    return sum(
        items.get(state.blueprint_id) == item
        for state in cells.values()
        if state.blueprint_id is not None
    )


def classify_lucky_merge(
    before: dict[GridCoord, LiveCellState],
    after: dict[GridCoord, LiveCellState],
    action: MergeAction,
    items: dict[str, ItemRef],
) -> Literal["lucky", "normal", "unknown"]:
    if before.keys() != after.keys():
        return "unknown"
    for coord in action.cluster:
        state = after.get(coord)
        if state is None or items.get(state.blueprint_id or "") == action.item:
            return "unknown"
    next_item = ItemRef(
        action.item.category,
        action.item.name,
        action.item.tier + 1,
        action.item.variant,
    )
    gained = _matching_count(after, items, next_item) - _matching_count(before, items, next_item)
    if gained >= 2:
        return "lucky"
    if gained == 1:
        return "normal"
    return "unknown"


def _wait_for_board(
    bot: Bot, timeout: float = 45.0, *, event_key: str | None = None
) -> dict[GridCoord, LiveCellState]:
    started = time.monotonic()
    deadline = started + timeout
    entering_event = False
    waiting_logged = False
    retry_delay = _INITIAL_RELOAD_DISCOVERY_INTERVAL_SECONDS
    while time.monotonic() < deadline and not bot._interrupt_event.is_set():
        try:
            health = bot.runtime.discover()
            if health.available:
                ready_for_board = True
                if event_key is not None:
                    snapshot = bot.runtime.read_snapshot(
                        SnapshotOptions(
                            include_obstacle_resources=False,
                            include_storage_bubbles=False,
                            include_shop_orders=False,
                        )
                    )
                    event = snapshot.event if snapshot is not None else None
                    ready_for_board = event is not None and event.key == event_key and event.current
                    if event is not None and event.key == event_key and not event.current:
                        if event.introduction_open:
                            bot.runtime.dismiss_event_introduction(event_key)
                        elif event.can_enter and not entering_event:
                            entering_event = bot.runtime.enter_event(event_key).submitted
                if ready_for_board:
                    cells = bot.runtime.read_board_state()
                    if cells is not None:
                        return cells
        except RuntimeConnectionError:
            pass
        if not waiting_logged and time.monotonic() - started >= 10.0:
            logger.info("Waiting for the saved game board after lucky-merge reload.")
            waiting_logged = True
        bot._interrupt_event.wait(min(retry_delay, max(0.0, deadline - time.monotonic())))
        retry_delay = next_recovery_delay(retry_delay, _INITIAL_RELOAD_DISCOVERY_INTERVAL_SECONDS)
    raise LuckyMergeError("Saved game did not become available after reload.")


def _wait_for_settled_board(
    bot: Bot, timeout: float = 45.0, *, event_key: str | None = None
) -> dict[GridCoord, LiveCellState]:
    """Like _wait_for_board, but confirms the read is unchanged after a short
    follow-up delay so a board caught mid-recovery isn't trusted as final."""
    board = _wait_for_board(bot, timeout=timeout, event_key=event_key)
    bot._interrupt_event.wait(_BOARD_SETTLE_SECONDS)
    settled = bot.runtime.read_board_state()
    return settled if settled is not None else board


def _wait_for_result(
    bot: Bot,
    before: dict[GridCoord, LiveCellState],
    action: MergeAction,
) -> Literal["lucky", "normal"]:
    deadline = time.monotonic() + DEFAULT_ACTION_TIMING.maximum_pending_seconds
    observed: Literal["lucky", "normal", "unknown"] = "unknown"
    stable_since = time.monotonic()
    while time.monotonic() < deadline and not bot._interrupt_event.is_set():
        cells = bot.runtime.read_board_state()
        result = (
            classify_lucky_merge(before, cells, action, bot._blueprint_items)
            if cells is not None
            else "unknown"
        )
        now = time.monotonic()
        if result != observed:
            observed = result
            stable_since = now
        if result != "unknown" and now - stable_since >= DEFAULT_ACTION_TIMING.settle_seconds:
            return result
        bot._interrupt_event.wait(0.2)
    raise LuckyMergeError("Offline merge result was not authoritative before timeout.")


def _verify_offline(bot: Bot) -> None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not bot._interrupt_event.is_set():
        if bot.runtime.read_runtime_health().backend_connected is False:
            return
        bot._interrupt_event.wait(0.2)
    raise LuckyMergeError("Game backend did not confirm disconnection; merge was not attempted.")


def run_lucky_merge(bot: Bot, action: MergeAction) -> None:
    if (
        action.kind is not MergeActionKind.TRIGGER
        or action.effect is not MoveEffect.MERGE
        or action.target_size != 3
        or len(action.cluster) != 3
    ):
        raise LuckyMergeError("Force lucky merge requires an ordinary merge-3 trigger.")
    next_item = ItemRef(
        action.item.category, action.item.name, action.item.tier + 1, action.item.variant
    )
    if next_item not in bot._blueprint_items.values():
        raise LuckyMergeError("Next-tier item is absent from the synchronized catalog.")
    logger.info("Preparing force lucky merge for %s.", action.item.tier_policy_key)
    if not bot.runtime.save_game():
        raise LuckyMergeError("Could not save the online board before an offline attempt.")
    baseline = bot.runtime.read_board_state()
    if baseline is None:
        raise LuckyMergeError("Could not read the complete board before an offline attempt.")
    current_event = getattr(bot, "_event_state", None)
    event_key = current_event.key if current_event is not None and current_event.current else None
    attempts = 0
    while not bot._interrupt_event.is_set():
        logger.info("Lucky merge attempt %d: isolating the game.", attempts + 1)
        network: LuckyMergeNetworkControl = bot.runtime.lucky_merge_network()
        submitted = False
        try:
            network.set_offline()
            _verify_offline(bot)
            submitted = True
            result = bot.runtime.submit_item_drop(action.start, action.end)
            if not result.submitted:
                raise LuckyMergeError("Offline merge was not accepted by the game.")
            logger.info("Lucky merge attempt %d submitted; checking the result.", attempts + 1)
            outcome = _wait_for_result(bot, baseline, action)
            attempts += 1
            if outcome == "lucky":
                logger.info("Lucky result detected; saving and verifying it after reload.")
                network.restore_online()
                if not bot.runtime.save_game():
                    raise LuckyMergeError("Lucky merge could not be saved after reconnecting.")
                network.reload_online()
                persisted = _wait_for_settled_board(bot, event_key=event_key)
                if (
                    classify_lucky_merge(baseline, persisted, action, bot._blueprint_items)
                    != "lucky"
                ):
                    raise LuckyMergeError("Lucky merge did not persist after reload.")
                log_event(
                    logger,
                    logging.INFO,
                    "lucky_merge.confirmed",
                    "Lucky merge confirmed after %d attempt(s).",
                    attempts,
                    item_policy_key=action.item.tier_policy_key,
                    attempts=attempts,
                )
                return
            logger.info("Normal merge detected; discarding attempt %d and reloading.", attempts)
            network.discard_and_reload()
            logger.info("Saved game reloaded; recovering the board before retrying.")
            restored = _wait_for_board(bot, event_key=event_key)
            if restored.keys() != baseline.keys() or any(
                restored[coord].blueprint_id != previous.blueprint_id
                for coord, previous in baseline.items()
            ):
                raise LuckyMergeError("Saved board changed during lucky-merge retry.")
            baseline = restored
            log_event(
                logger,
                logging.DEBUG,
                "lucky_merge.retry",
                "Normal merge discarded; retrying offline.",
                item_policy_key=action.item.tier_policy_key,
                attempts=attempts,
            )
        except Exception as exc:
            if bot._interrupt_event.is_set():
                return
            if isinstance(exc, LuckyMergeError):
                raise
            raise LuckyMergeError(f"Lucky merge recovery failed: {exc}") from exc
        finally:
            if network.offline:
                if submitted:
                    network.discard_and_reload()
                else:
                    network.restore_online()
    return
