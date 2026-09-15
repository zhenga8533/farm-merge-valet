import pytest

from farm_merge_valet.config.hotkeys import display_hotkey, normalize_hotkey


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("Ctrl + Shift + X", "ctrl+shift+x"),
        ("F9", "f9"),
        ("Meta+Page Up", "windows+page up"),
    ),
)
def test_hotkeys_are_normalized(value: str, expected: str) -> None:
    assert normalize_hotkey(value) == expected


@pytest.mark.parametrize("value", ("x", "enter", "fn+f9", "ctrl+shift"))
def test_unsafe_or_unobservable_hotkeys_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_hotkey(value)


def test_hotkey_display_is_human_readable() -> None:
    assert display_hotkey("ctrl+shift+x") == "Ctrl+Shift+X"
    assert display_hotkey("f9") == "F9"
    assert display_hotkey(None) == "Not set"
