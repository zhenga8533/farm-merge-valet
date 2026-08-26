"""Canonical global shortcut values shared by configuration and the GUI."""

from __future__ import annotations

import re
from dataclasses import dataclass

import keyboard

_MODIFIER_ORDER = ("ctrl", "alt", "shift", "windows")
_MODIFIERS = frozenset(_MODIFIER_ORDER)
_FUNCTION_KEY = re.compile(r"f(?:[1-9]|1\d|2[0-4])$")


def normalize_hotkey(value: str) -> str:
    raw_parts = [part.strip() for part in value.split("+")]
    if not raw_parts or any(not part for part in raw_parts):
        raise ValueError("hotkey must contain a key")
    parts = [keyboard.normalize_name(part.casefold()) for part in raw_parts]
    parts = [
        "windows" if part in {"left windows", "right windows", "win", "meta"} else part
        for part in parts
    ]
    if "fn" in parts:
        raise ValueError("Fn is handled by the keyboard firmware and cannot be used as a hotkey")
    if len(parts) != len(set(parts)):
        raise ValueError("hotkey cannot contain the same key more than once")
    keys = [part for part in parts if part not in _MODIFIERS]
    if len(keys) != 1:
        raise ValueError("hotkey must contain exactly one non-modifier key")
    key = keys[0]
    modifiers = [modifier for modifier in _MODIFIER_ORDER if modifier in parts]
    if not modifiers and (len(key) == 1 or not _FUNCTION_KEY.fullmatch(key)):
        raise ValueError("use a function key or include Ctrl, Alt, Shift, or Windows")
    return "+".join((*modifiers, key))


def display_hotkey(value: str | None) -> str:
    if value is None:
        return "Not set"
    labels = {
        "ctrl": "Ctrl",
        "alt": "Alt",
        "shift": "Shift",
        "windows": "Win",
        "page up": "Page Up",
        "page down": "Page Down",
    }
    return "+".join(
        labels.get(part, part.upper() if _FUNCTION_KEY.fullmatch(part) else part.title())
        for part in value.split("+")
    )


@dataclass(frozen=True)
class HotkeyBindings:
    start_stop: str | None
    pause_resume: str | None
    quit_application: str | None

    @classmethod
    def from_values(
        cls,
        start_stop: str | None,
        pause_resume: str | None,
        quit_application: str | None,
    ) -> HotkeyBindings:
        bindings = cls(
            start_stop=normalize_hotkey(start_stop) if start_stop is not None else None,
            pause_resume=(
                normalize_hotkey(pause_resume) if pause_resume is not None else None
            ),
            quit_application=(
                normalize_hotkey(quit_application) if quit_application is not None else None
            ),
        )
        configured = tuple(value for value in bindings.values() if value is not None)
        if len(set(configured)) != len(configured):
            raise ValueError("start/stop, pause/resume, and quit hotkeys must be different")
        return bindings

    def values(self) -> tuple[str | None, str | None, str | None]:
        return self.start_stop, self.pause_resume, self.quit_application
