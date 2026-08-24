from __future__ import annotations

from threading import Event

import numpy as np

from farm_merge_valet.capture.window import WindowRegion
from farm_merge_valet.cdp.board_store import LiveCellState
from farm_merge_valet.core.board import BoardGrid, Cell, CellKind, ItemRef
from farm_merge_valet.core.bot import Bot, Phase
from farm_merge_valet.vision.matcher import Match


def _bare_bot() -> Bot:
    bot = Bot.__new__(Bot)
    bot.board = BoardGrid()
    bot.phase = Phase.CLAIM_CRATES
    bot.paused = False
    bot._quit_requested = False
    bot._interrupt_event = Event()
    bot._resume_requested = Event()
    bot._crate_inventory_armed = False
    bot._last_crate_inventory_status = None
    return bot


def test_merge_five_work_uses_the_reserve_before_the_last_empty_is_consumed(
    monkeypatch,
) -> None:
    bot = _bare_bot()
    item = ItemRef("crops", "wheat", 1)
    for coord in [(x, 0) for x in range(1, 6)]:
        bot.board.set_cell(coord, Cell(CellKind.ITEM, item))
    bot.board.set_cell((0, 0), Cell(CellKind.EMPTY))
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.prefer_merge_five", True)

    assert bot._board_needs_merge()

    bot.board.set_cell((6, 0), Cell(CellKind.EMPTY))
    assert not bot._board_needs_merge()


def test_merge_three_does_not_use_the_reserve_when_merge_five_is_enabled(monkeypatch) -> None:
    bot = _bare_bot()
    item = ItemRef("crops", "wheat", 1)
    for coord in [(x, 0) for x in range(3)]:
        bot.board.set_cell(coord, Cell(CellKind.ITEM, item))
    bot.board.set_cell((10, 10), Cell(CellKind.EMPTY))
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.prefer_merge_five", True)

    assert not bot._board_needs_merge()
    assert not bot._merge_actions_for_policy()


def test_board_store_found_status_marks_bot_as_armed(monkeypatch) -> None:
    bot = _bare_bot()
    bot._board_store_armed = False
    bot._last_board_store_status = None
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.arm_board_store",
        lambda *_args: "found (42/100 cells with content, 40 with live bounds)",
    )

    bot._ensure_board_store_armed()

    assert bot._board_store_armed


def test_initialize_discards_live_references(monkeypatch) -> None:
    bot = _bare_bot()
    bot._board_store_armed = True
    bot._last_board_store_status = "found"
    bot._crate_inventory_armed = True
    bot._last_crate_inventory_status = "found (40 available)"
    bot._scene_calibration = object()
    monkeypatch.setattr("farm_merge_valet.core.bot.initialize_environment", lambda _event: True)

    assert bot.initialize()

    assert not bot._board_store_armed
    assert bot._last_board_store_status is None
    assert not bot._crate_inventory_armed
    assert bot._last_crate_inventory_status is None
    assert bot._scene_calibration is None


def test_missing_live_state_triggers_board_store_rearming(monkeypatch) -> None:
    bot = _bare_bot()
    bot._board_store_armed = True
    monkeypatch.setattr("farm_merge_valet.core.bot.read_board_state", lambda *_args: None)

    assert not bot._sync_board_from_live_state()
    assert not bot._board_store_armed


def test_claim_crates_preserves_merge_space(monkeypatch) -> None:
    bot = _bare_bot()
    bot._supply_crate_template = np.zeros((1, 1, 3), dtype=np.uint8)
    empty_coords = [(0, 0), (1, 0), (2, 0)]
    for coord in empty_coords:
        bot.board.set_cell(coord, Cell(CellKind.EMPTY))
    item = ItemRef("crops", "wheat", 1)
    for coord in [(x, 0) for x in range(3, 8)]:
        bot.board.set_cell(coord, Cell(CellKind.ITEM, item))
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.prefer_merge_five", True)
    bot._crate_inventory_armed = True
    monkeypatch.setattr("farm_merge_valet.core.bot.read_crate_count", lambda *_args: 1000)

    clicks = 0

    def fake_click(*_args, **_kwargs) -> None:
        nonlocal clicks
        bot.board.set_cell(empty_coords[clicks], Cell(CellKind.PRODUCT))
        clicks += 1

    monkeypatch.setattr("farm_merge_valet.core.bot.click", fake_click)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.find_best_scaled_match",
        lambda *_args, **_kwargs: Match(0, 0, 1, 1, 1.0),
    )
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.capture_region", lambda _region: np.zeros((1, 1, 3))
    )
    bot._sync_board_from_live_state = lambda: True

    bot._step_claim_crates(
        WindowRegion(0, 0, 100, 100),
        np.zeros((1, 1, 3), dtype=np.uint8),
        board_needs_merge=False,
    )

    assert clicks == 2
    assert bot.phase is Phase.MERGE
    assert len(bot.board.find_empty()) == 1


def test_claim_crates_stops_at_live_inventory_count(monkeypatch) -> None:
    bot = _bare_bot()
    bot._crate_inventory_armed = True
    bot._supply_crate_template = np.zeros((1, 1, 3), dtype=np.uint8)
    for x in range(5):
        bot.board.set_cell((x, 0), Cell(CellKind.EMPTY))

    clicks = 0

    def fake_click(*_args, **_kwargs) -> None:
        nonlocal clicks
        clicks += 1

    monkeypatch.setattr("farm_merge_valet.core.bot.read_crate_count", lambda *_args: 2)
    monkeypatch.setattr("farm_merge_valet.core.bot.click", fake_click)
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.find_best_scaled_match",
        lambda *_args, **_kwargs: Match(0, 0, 1, 1, 1.0),
    )
    bot._sync_board_from_live_state = lambda: True

    bot._step_claim_crates(
        WindowRegion(0, 0, 100, 100),
        np.zeros((1, 1, 3), dtype=np.uint8),
        board_needs_merge=False,
    )

    assert clicks == 2
    assert bot.phase is Phase.CLAIM_CRATES


def test_claim_crates_takes_no_action_when_inventory_is_empty(monkeypatch) -> None:
    bot = _bare_bot()
    bot._crate_inventory_armed = True
    monkeypatch.setattr("farm_merge_valet.core.bot.read_crate_count", lambda *_args: 0)
    monkeypatch.setattr(
        bot,
        "_find_supply_crate",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not search for crate button")),
    )

    bot._step_claim_crates(
        WindowRegion(0, 0, 100, 100),
        np.zeros((1, 1, 3), dtype=np.uint8),
        board_needs_merge=False,
    )


def test_step_takes_no_action_without_live_board_state(monkeypatch) -> None:
    bot = _bare_bot()
    acted = []
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.find_window", lambda *_args: WindowRegion(0, 0, 100, 100)
    )
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.capture_region", lambda *_args: np.zeros((100, 100, 3))
    )
    bot._ensure_board_store_armed = lambda: None
    bot._sync_board_from_live_state = lambda: False
    bot._step_claim_crates = lambda *_args, **_kwargs: acted.append(True)

    bot.step()

    assert not acted


def test_step_does_not_read_board_after_interrupted_discovery(monkeypatch) -> None:
    bot = _bare_bot()
    reads = []
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.find_window", lambda *_args: WindowRegion(0, 0, 100, 100)
    )
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.capture_region", lambda *_args: np.zeros((100, 100, 3))
    )

    def interrupt_during_discovery() -> None:
        bot._interrupt_event.set()

    bot._ensure_board_store_armed = interrupt_during_discovery
    bot._sync_board_from_live_state = lambda: reads.append(True) or True

    bot.step()

    assert not reads


def test_supply_crate_search_is_limited_to_bottom_center_region(monkeypatch) -> None:
    bot = _bare_bot()
    bot._supply_crate_template = np.zeros((10, 10, 3), dtype=np.uint8)
    observed_shape = None

    def fake_match(frame, *_args):
        nonlocal observed_shape
        observed_shape = frame.shape
        return Match(10, 20, 30, 40, 0.99)

    monkeypatch.setattr("farm_merge_valet.core.bot.find_best_scaled_match", fake_match)

    match = bot._find_supply_crate(np.zeros((1080, 1920, 3), dtype=np.uint8))

    assert observed_shape == (227, 192, 3)
    assert match is not None
    assert (match.x, match.y) == (874, 873)


def test_full_board_without_a_merge_action_pauses_for_manual_recovery(monkeypatch) -> None:
    bot = _bare_bot()
    bot.phase = Phase.MERGE
    bot._scene_calibration = object()
    bot.board.set_cell((0, 0), Cell(CellKind.PRODUCT))
    monkeypatch.setattr(bot, "_try_merge", lambda *_args, **_kwargs: False)

    bot._step_merge(
        WindowRegion(0, 0, 100, 100),
        np.zeros((1, 1, 3), dtype=np.uint8),
        board_needs_merge=True,
    )

    assert bot.paused


def test_merge_five_policy_prioritizes_five_over_available_three(monkeypatch) -> None:
    bot = _bare_bot()
    wheat = ItemRef("crops", "wheat", 1)
    chicken = ItemRef("animals", "chicken", 1)
    for coord in [(x, 0) for x in range(5)]:
        bot.board.set_cell(coord, Cell(CellKind.ITEM, wheat))
    for coord in [(x, 2) for x in range(3)]:
        bot.board.set_cell(coord, Cell(CellKind.ITEM, chicken))
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.prefer_merge_five", True)

    actions = bot._merge_actions_for_policy()

    assert actions
    assert {action.target_size for action in actions} == {5}
    assert {action.item for action in actions} == {wheat}


def test_merge_five_policy_uses_three_only_when_the_board_is_full(monkeypatch) -> None:
    bot = _bare_bot()
    wheat = ItemRef("crops", "wheat", 1)
    for coord in [(x, 0) for x in range(3)]:
        bot.board.set_cell(coord, Cell(CellKind.ITEM, wheat))
    bot.board.set_cell((10, 10), Cell(CellKind.EMPTY))
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.prefer_merge_five", True)

    assert not bot._merge_actions_for_policy()

    bot.board.set_cell((10, 10), Cell(CellKind.PRODUCT))
    fallback = bot._merge_actions_for_policy()
    assert fallback
    assert {action.target_size for action in fallback} == {3}


def test_live_sync_distinguishes_open_cells_from_structure_placeholders(monkeypatch) -> None:
    bot = _bare_bot()
    bot._blueprint_items = {}
    raw = {
        (5, 5): LiveCellState(True, "market"),
        (4, 4): LiveCellState(True, "empty"),
        (6, 6): LiveCellState(False, None),
    }
    monkeypatch.setattr("farm_merge_valet.core.bot.read_board_state", lambda *_args: raw)

    assert bot._sync_board_from_live_state()
    assert bot.board.get_cell((4, 4)).kind is CellKind.STRUCTURE
    assert bot.board.get_cell((6, 6)).kind is CellKind.EMPTY


def test_run_forever_retries_when_target_window_is_temporarily_missing(monkeypatch) -> None:
    bot = _bare_bot()
    attempts = 0

    def step() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LookupError("window missing")
        bot._quit_requested = True

    bot.step = step
    bot.initialize = lambda: True
    monkeypatch.setattr("farm_merge_valet.core.bot.keyboard.add_hotkey", lambda *_args: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.keyboard.unhook_all", lambda: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.start_paused", False)

    bot.run_forever()

    assert attempts == 2


def test_resume_hotkey_only_signals_the_main_loop() -> None:
    bot = _bare_bot()
    bot.paused = True
    bot._interrupt_event.set()

    bot.initialize = lambda: (_ for _ in ()).throw(AssertionError("must not run in callback"))
    bot._toggle_pause()

    assert bot.paused
    assert bot._resume_requested.is_set()


def test_second_pause_press_cancels_pending_resume() -> None:
    bot = _bare_bot()
    bot.paused = True
    bot._interrupt_event.set()

    bot._toggle_pause()
    bot._toggle_pause()

    assert bot.paused
    assert not bot._resume_requested.is_set()


def test_quit_interrupts_actions_and_cancels_resume() -> None:
    bot = _bare_bot()
    bot.paused = True
    bot._resume_requested.set()

    bot.request_quit()

    assert bot._quit_requested
    assert bot.paused
    assert bot._interrupt_event.is_set()
    assert not bot._resume_requested.is_set()


def test_scroll_direction_follows_offscreen_target(monkeypatch) -> None:
    region = WindowRegion(0, 0, 1920, 1080)

    def assert_direction(initial_y: int, expected_toward_bottom: bool) -> None:
        bot = _bare_bot()
        bot._scene_calibration = object()
        current_y = initial_y
        directions = []
        bot._refresh_calibration = lambda _region: None
        bot._grid_to_pixel = lambda _target: (960, current_y)

        def fake_pan(_region, *, toward_bottom: bool, repeats: int, stop_event: Event) -> bool:
            nonlocal current_y
            directions.append(toward_bottom)
            current_y = 540
            return True

        monkeypatch.setattr("farm_merge_valet.core.bot.pan", fake_pan)

        assert bot._scroll_to_reveal(region, (0, 0))
        assert directions == [expected_toward_bottom]

    assert_direction(1000, True)
    assert_direction(100, False)


def test_run_forever_retries_initialization_when_window_is_missing(monkeypatch) -> None:
    bot = _bare_bot()
    initialization_attempts = 0

    def initialize() -> bool:
        nonlocal initialization_attempts
        initialization_attempts += 1
        if initialization_attempts == 1:
            raise LookupError("window missing")
        return True

    bot.initialize = initialize
    bot.step = lambda: bot.request_quit()
    monkeypatch.setattr("farm_merge_valet.core.bot.keyboard.add_hotkey", lambda *_args: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.keyboard.unhook_all", lambda: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.start_paused", False)

    bot.run_forever()

    assert initialization_attempts == 2
