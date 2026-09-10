"""Per-user storage for configuration credentials."""

from __future__ import annotations

import base64
import ctypes
import os
import tempfile
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]


_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _protect(value: bytes) -> bytes:
    if os.name != "nt":
        return value
    source, source_buffer = _blob(value)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)
        del source_buffer


def _unprotect(value: bytes) -> bytes:
    if os.name != "nt":
        return value
    source, source_buffer = _blob(value)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)
        del source_buffer


class SecretStore:
    """Store one credential beside a config file, protected for the current user on Windows."""

    def __init__(self, config_path: Path) -> None:
        self.path = config_path.with_suffix(f"{config_path.suffix}.secrets")

    def read(self) -> str | None:
        if not self.path.exists():
            return None
        payload = base64.b64decode(self.path.read_bytes(), validate=True)
        return _unprotect(payload).decode("utf-8")

    def write(self, value: str | None) -> None:
        if value is None:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = base64.b64encode(_protect(value.encode("utf-8")))
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                temporary.chmod(0o600)
            temporary.replace(self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
