from __future__ import annotations

import numpy as np

from farm_merge_valet.capture.window import WindowRegion
from farm_merge_valet.core.board import BoardGrid, Cell, CellKind, ItemRef
from farm_merge_valet.core.bot import Bot, Phase
from farm_merge_valet.vision.matcher import Match


def _bare_bot() -> Bot:
    bot = Bot.__new__(Bot)
    bot.board = BoardGrid()
    bot.phase = Phase.CLAIM_CRATES
    bot.paused = False
    bot._quit_requested = False
    return bot


def test_board_needs_merge_before_the_last_empty_cell_is_consumed() -> None:
    bot = _bare_bot()
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    item = ItemRef("crops", "wheat", 1)
    bot._merge_five_overrides = {}
    bot.board.set_cell((1, 0), Cell(CellKind.ITEM, item))
    bot.board.set_cell((2, 0), Cell(CellKind.ITEM, item))
    bot.board.set_cell((5, 0), Cell(CellKind.ITEM, item))
    bot.board.set_cell((0, 0), Cell(CellKind.EMPTY))

    assert bot._board_needs_merge(frame, live_synced=True)

    bot.board.set_cell((6, 0), Cell(CellKind.EMPTY))
    assert not bot._board_needs_merge(frame, live_synced=True)


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


def test_claim_crates_limits_batch_to_preserve_merge_space(monkeypatch) -> None:
    bot = _bare_bot()
    bot._merge_five_overrides = {}
    bot._supply_crate_template_scaled = np.zeros((1, 1, 3), dtype=np.uint8)
    empty_coords = [(0, 0), (1, 0), (2, 0)]
    for coord in empty_coords:
        bot.board.set_cell(coord, Cell(CellKind.EMPTY))
    item = ItemRef("crops", "wheat", 1)
    bot.board.set_cell((3, 0), Cell(CellKind.ITEM, item))
    bot.board.set_cell((4, 0), Cell(CellKind.ITEM, item))
    bot.board.set_cell((6, 0), Cell(CellKind.ITEM, item))

    clicks = 0

    def fake_click(*_args, **_kwargs) -> None:
        nonlocal clicks
        bot.board.set_cell(empty_coords[clicks], Cell(CellKind.PRODUCT))
        clicks += 1

    monkeypatch.setattr("farm_merge_valet.core.bot.click", fake_click)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.find_best_match",
        lambda *_args, **_kwargs: Match(0, 0, 1, 1, 1.0),
    )
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.capture_region", lambda _region: np.zeros((1, 1, 3))
    )
    bot._sync_board_from_live_state = lambda: True

    bot._step_claim_crates(
        WindowRegion(0, 0, 100, 100),
        np.zeros((1, 1, 3), dtype=np.uint8),
        live_synced=True,
        board_needs_merge=False,
    )

    assert clicks == 2
    assert bot.phase is Phase.MERGE
    assert len(bot.board.find_empty()) == 1


def test_supply_crate_search_is_limited_to_bottom_center_region(monkeypatch) -> None:
    bot = _bare_bot()
    bot._supply_crate_template_scaled = np.zeros((10, 10, 3), dtype=np.uint8)
    observed_shape = None

    def fake_match(frame, *_args):
        nonlocal observed_shape
        observed_shape = frame.shape
        return Match(10, 20, 30, 40, 0.99)

    monkeypatch.setattr("farm_merge_valet.core.bot.find_best_match", fake_match)

    match = bot._find_supply_crate(np.zeros((1080, 1920, 3), dtype=np.uint8))

    assert observed_shape == (227, 192, 3)
    assert match is not None
    assert (match.x, match.y) == (874, 873)


def test_full_board_without_a_merge_action_pauses_for_manual_recovery(monkeypatch) -> None:
    bot = _bare_bot()
    bot.phase = Phase.MERGE
    bot._scene_calibration = object()
    bot.board.set_cell((0, 0), Cell(CellKind.PRODUCT))
    monkeypatch.setattr(bot, "_try_merge", lambda *_args: False)

    bot._step_merge(
        WindowRegion(0, 0, 100, 100),
        np.zeros((1, 1, 3), dtype=np.uint8),
        board_needs_merge=True,
    )

    assert bot.paused


def test_live_sync_applies_only_explicit_structure_footprints(monkeypatch) -> None:
    bot = _bare_bot()
    bot._blueprint_items = {}
    raw = {
        (5, 5): "market",
        (4, 4): "empty",
        (5, 4): "empty",
        (4, 5): "empty",
        (6, 5): "empty",
        (6, 6): "empty",
    }
    monkeypatch.setattr("farm_merge_valet.core.bot.read_board_state", lambda *_args: raw)

    assert bot._sync_board_from_live_state()
    assert bot.board.get_cell((4, 4)).kind is CellKind.STRUCTURE
    assert bot.board.get_cell((5, 4)).kind is CellKind.STRUCTURE
    assert bot.board.get_cell((4, 5)).kind is CellKind.STRUCTURE
    assert bot.board.get_cell((6, 5)).kind is CellKind.EMPTY
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
    bot.initialize = lambda: None
    monkeypatch.setattr("farm_merge_valet.core.bot.keyboard.add_hotkey", lambda *_args: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.keyboard.unhook_all", lambda: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.start_paused", False)

    bot.run_forever()

    assert attempts == 2


def test_resume_stays_paused_when_window_is_missing() -> None:
    bot = _bare_bot()
    bot.paused = True

    def missing_window() -> None:
        raise LookupError("window missing")

    bot.initialize = missing_window
    bot._toggle_pause()

    assert bot.paused


def test_run_forever_retries_initialization_when_window_is_missing(monkeypatch) -> None:
    bot = _bare_bot()
    initialization_attempts = 0

    def initialize() -> None:
        nonlocal initialization_attempts
        initialization_attempts += 1
        if initialization_attempts == 1:
            raise LookupError("window missing")

    bot.initialize = initialize
    bot.step = lambda: bot.request_quit()
    monkeypatch.setattr("farm_merge_valet.core.bot.keyboard.add_hotkey", lambda *_args: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.keyboard.unhook_all", lambda: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.start_paused", False)

    bot.run_forever()

    assert initialization_attempts == 2
