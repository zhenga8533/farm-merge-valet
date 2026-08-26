from __future__ import annotations

from farm_merge_valet.gui import native_window


class _FakeUser32:
    def __init__(self, style: int) -> None:
        self.style = style
        self.updated: tuple[int, int, int] | None = None

    def GetWindowLongW(self, hwnd: int, index: int) -> int:
        assert hwnd == 42
        assert index == native_window._GWL_EXSTYLE
        return self.style

    def SetWindowLongW(self, hwnd: int, index: int, style: int) -> None:
        self.updated = hwnd, index, style


def test_click_through_preserves_unrelated_native_window_flags(monkeypatch) -> None:
    unrelated = 0x100
    user32 = _FakeUser32(unrelated)
    monkeypatch.setattr(native_window, "_user32", lambda: user32)

    native_window.set_click_through(42, True)

    assert user32.updated == (
        42,
        native_window._GWL_EXSTYLE,
        unrelated | native_window._WS_EX_LAYERED | native_window._WS_EX_TRANSPARENT,
    )


def test_disabling_click_through_only_removes_transparent_flag(monkeypatch) -> None:
    initial = 0x100 | native_window._WS_EX_LAYERED | native_window._WS_EX_TRANSPARENT
    user32 = _FakeUser32(initial)
    monkeypatch.setattr(native_window, "_user32", lambda: user32)

    native_window.set_click_through(42, False)

    assert user32.updated == (
        42,
        native_window._GWL_EXSTYLE,
        initial & ~native_window._WS_EX_TRANSPARENT,
    )
