from __future__ import annotations

from threading import Event
from types import SimpleNamespace

import pytest

from farm_merge_valet.automation.runtime import ActionResult, ActionStatus, LiveCellState
from farm_merge_valet.automation.timing import next_recovery_delay
from farm_merge_valet.automation.workflows import lucky_merge
from farm_merge_valet.core.items import ItemRef
from farm_merge_valet.core.merge_planner import MergeAction, MergeActionKind, MoveEffect


def merge_action() -> MergeAction:
    return MergeAction(
        MergeActionKind.TRIGGER,
        ItemRef("crops", "wheat", 1),
        (0, 0),
        (1, 0),
        frozenset({(0, 0), (1, 0), (2, 0)}),
        3,
        MoveEffect.MERGE,
    )


def board(*, next_tier: int = 0) -> dict[tuple[int, int], LiveCellState]:
    cells = {
        (0, 0): LiveCellState(True, "wheat_1", 1),
        (1, 0): LiveCellState(True, "wheat_1", 2),
        (2, 0): LiveCellState(True, "wheat_1", 3),
        (3, 0): LiveCellState(False, None),
        (4, 0): LiveCellState(False, None),
        (5, 0): LiveCellState(True, "wheat_2", 6),
    }
    if next_tier:
        for coord in ((0, 0), (3, 0))[:next_tier]:
            cells[coord] = LiveCellState(True, "wheat_2", coord[0] + 10)
        for coord in ((1, 0), (2, 0)):
            cells[coord] = LiveCellState(False, None)
    return cells


ITEMS = {"wheat_1": ItemRef("crops", "wheat", 1), "wheat_2": ItemRef("crops", "wheat", 2)}


def test_lucky_merge_classifies_only_new_next_tier_items() -> None:
    action = merge_action()
    assert lucky_merge.classify_lucky_merge(board(), board(next_tier=1), action, ITEMS) == "normal"
    assert lucky_merge.classify_lucky_merge(board(), board(next_tier=2), action, ITEMS) == "lucky"
    incomplete = board(next_tier=2)
    incomplete.pop((4, 0))
    assert lucky_merge.classify_lucky_merge(board(), incomplete, action, ITEMS) == "unknown"
    assert lucky_merge.classify_lucky_merge(board(), board(), action, ITEMS) == "unknown"


def test_reload_discovery_uses_shared_backoff_and_logs_wait_once(monkeypatch, caplog) -> None:
    elapsed = 0.0
    waits: list[float] = []
    discoveries = 0

    class WaitEvent:
        def is_set(self) -> bool:
            return False

        def wait(self, seconds: float) -> None:
            nonlocal elapsed
            waits.append(seconds)
            elapsed += seconds

    def discover():
        nonlocal discoveries
        discoveries += 1
        return SimpleNamespace(available=discoveries >= 5)

    runtime = SimpleNamespace(discover=discover, read_board_state=board)
    bot = SimpleNamespace(runtime=runtime, _interrupt_event=WaitEvent())
    monkeypatch.setattr(lucky_merge.time, "monotonic", lambda: elapsed)

    with caplog.at_level("INFO", logger=lucky_merge.logger.name):
        assert lucky_merge._wait_for_board(bot) == board()

    assert waits == [2.0, 4.0, 8.0, 10.0]
    assert caplog.text.count("Waiting for the saved game board") == 1
    assert next_recovery_delay(2.0, 2.0) == 4.0


def test_reload_discovery_fails_if_saved_board_never_returns(monkeypatch) -> None:
    elapsed = 0.0

    class WaitEvent:
        def is_set(self) -> bool:
            return False

        def wait(self, seconds: float) -> None:
            nonlocal elapsed
            elapsed += seconds

    bot = SimpleNamespace(
        runtime=SimpleNamespace(discover=lambda: SimpleNamespace(available=False)),
        _interrupt_event=WaitEvent(),
    )
    monkeypatch.setattr(lucky_merge.time, "monotonic", lambda: elapsed)

    with pytest.raises(lucky_merge.LuckyMergeError, match="did not become available"):
        lucky_merge._wait_for_board(bot, timeout=25.0)

    assert elapsed == 25.0


class FakeNetwork:
    def __init__(self) -> None:
        self.offline = False
        self.discarded = 0
        self.restored = 0
        self.reloaded = 0

    def set_offline(self) -> None:
        self.offline = True

    def discard_and_reload(self) -> None:
        self.offline = False
        self.discarded += 1

    def restore_online(self, *, game_target_required: bool = True) -> None:
        self.offline = False
        self.restored += 1

    def reload_online(self) -> None:
        self.reloaded += 1


def test_lucky_merge_discards_submitted_attempt_on_unknown_result(monkeypatch) -> None:
    network = FakeNetwork()
    runtime = SimpleNamespace(
        save_game=lambda: True,
        read_board_state=board,
        lucky_merge_network=lambda: network,
        submit_item_drop=lambda _start, _end: ActionResult(ActionStatus.SUBMITTED),
    )
    bot = SimpleNamespace(runtime=runtime, _interrupt_event=Event(), _blueprint_items=ITEMS)
    monkeypatch.setattr(lucky_merge, "_verify_offline", lambda _bot: None)

    def unknown(*_args):
        raise lucky_merge.LuckyMergeError("unknown result")

    monkeypatch.setattr(lucky_merge, "_wait_for_result", unknown)

    with pytest.raises(lucky_merge.LuckyMergeError, match="unknown result"):
        lucky_merge.run_lucky_merge(bot, merge_action())

    assert network.discarded == 1
    assert not network.offline


def test_lucky_merge_restores_network_if_disconnection_is_unverified(monkeypatch) -> None:
    network = FakeNetwork()
    runtime = SimpleNamespace(
        save_game=lambda: True,
        read_board_state=board,
        lucky_merge_network=lambda: network,
    )
    bot = SimpleNamespace(runtime=runtime, _interrupt_event=Event(), _blueprint_items=ITEMS)

    def not_offline(_bot):
        raise lucky_merge.LuckyMergeError("still online")

    monkeypatch.setattr(lucky_merge, "_verify_offline", not_offline)

    with pytest.raises(lucky_merge.LuckyMergeError, match="still online"):
        lucky_merge.run_lucky_merge(bot, merge_action())

    assert network.restored == 1
    assert network.discarded == 0


def test_ambiguous_offline_submission_is_discarded(monkeypatch) -> None:
    network = FakeNetwork()

    def submit(_start, _end):
        raise TimeoutError("submission outcome unknown")

    runtime = SimpleNamespace(
        save_game=lambda: True,
        read_board_state=board,
        lucky_merge_network=lambda: network,
        submit_item_drop=submit,
    )
    bot = SimpleNamespace(runtime=runtime, _interrupt_event=Event(), _blueprint_items=ITEMS)
    monkeypatch.setattr(lucky_merge, "_verify_offline", lambda _bot: None)

    with pytest.raises(lucky_merge.LuckyMergeError, match="recovery failed"):
        lucky_merge.run_lucky_merge(bot, merge_action())

    assert network.discarded == 1
    assert not network.offline


def test_normal_attempt_retries_until_lucky_result_persists(monkeypatch) -> None:
    first, second = FakeNetwork(), FakeNetwork()
    networks = [first, second]
    attempts = iter(("normal", "lucky"))
    drops: list[tuple[tuple[int, int], tuple[int, int]]] = []
    runtime = SimpleNamespace(
        save_game=lambda: True,
        read_board_state=board,
        lucky_merge_network=lambda: networks.pop(0),
        submit_item_drop=lambda start, end: (
            drops.append((start, end)),
            ActionResult(ActionStatus.SUBMITTED),
        )[1],
    )
    bot = SimpleNamespace(
        runtime=runtime,
        _interrupt_event=Event(),
        _blueprint_items=ITEMS,
        _event_state=None,
    )
    monkeypatch.setattr(lucky_merge, "_verify_offline", lambda _bot: None)
    monkeypatch.setattr(lucky_merge, "_wait_for_result", lambda *_args: next(attempts))
    monkeypatch.setattr(lucky_merge, "_wait_for_board", lambda *_args, **_kwargs: board())
    monkeypatch.setattr(
        lucky_merge, "_wait_for_settled_board", lambda *_args, **_kwargs: board(next_tier=2)
    )

    lucky_merge.run_lucky_merge(bot, merge_action())

    assert len(drops) == 2
    assert first.discarded == 1
    assert second.restored == 1
    assert second.reloaded == 1
    assert not first.offline and not second.offline


def test_lucky_result_requires_saved_board_confirmation(monkeypatch) -> None:
    network = FakeNetwork()
    saves = 0

    def save_game():
        nonlocal saves
        saves += 1
        return True

    runtime = SimpleNamespace(
        save_game=save_game,
        read_board_state=board,
        lucky_merge_network=lambda: network,
        submit_item_drop=lambda _start, _end: ActionResult(ActionStatus.SUBMITTED),
    )
    bot = SimpleNamespace(runtime=runtime, _interrupt_event=Event(), _blueprint_items=ITEMS)
    monkeypatch.setattr(lucky_merge, "_verify_offline", lambda _bot: None)
    monkeypatch.setattr(lucky_merge, "_wait_for_result", lambda *_args: "lucky")
    monkeypatch.setattr(lucky_merge, "_wait_for_settled_board", lambda *_args, **_kwargs: board())

    with pytest.raises(lucky_merge.LuckyMergeError, match="did not persist"):
        lucky_merge.run_lucky_merge(bot, merge_action())

    assert saves == 2
    assert network.reloaded == 1
    assert not network.offline


def test_settled_board_trusts_the_confirmation_read_over_the_initial_one(monkeypatch) -> None:
    runtime = SimpleNamespace(read_board_state=lambda: board(next_tier=2))
    bot = SimpleNamespace(runtime=runtime, _interrupt_event=Event())
    monkeypatch.setattr(lucky_merge, "_wait_for_board", lambda *_args, **_kwargs: board())

    assert lucky_merge._wait_for_settled_board(bot) == board(next_tier=2)
