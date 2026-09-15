"""Central logging configuration and structured application events."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit, urlunsplit

from rich.logging import RichHandler

from farm_merge_valet.config.paths import user_data_root

FMV_EVENT_ATTRIBUTE = "fmv_event"
FMV_CONTEXT_ATTRIBUTE = "fmv_context"

_configuration_lock = Lock()
_console_handler: RichHandler | None = None
_file_handler: RotatingFileHandler | None = None

_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "password", "secret", "token", "webhook")
_URL_PATTERN = re.compile(r"(?:https?|wss?)://[^\s]+", re.IGNORECASE)


def diagnostic_log_path() -> Path:
    return user_data_root() / "logs" / "farm-merge-valet.log"


def _redact(value: object, key: str = "") -> object:
    if any(part in key.casefold() for part in _SENSITIVE_KEY_PARTS):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(child_key): _redact(child, str(child_key)) for child_key, child in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact(child) for child in value]
    if isinstance(value, str):

        def strip_url(match: re.Match[str]) -> str:
            parts = urlsplit(match.group(0))
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

        return _URL_PATTERN.sub(strip_url, value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class _JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, FMV_EVENT_ATTRIBUTE, None),
            "message": _redact(record.getMessage()),
            "context": _redact(getattr(record, FMV_CONTEXT_ATTRIBUTE, {})),
        }
        if record.exc_info:
            payload["exception"] = _redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """Configure the process-wide console sink without duplicating handlers.

    The root accepts diagnostic events so non-console sinks can aggregate them;
    each presentation sink applies its own display threshold.
    """
    global _console_handler, _file_handler

    root_logger = logging.getLogger()
    with _configuration_lock:
        root_logger.setLevel(logging.DEBUG)
        if _console_handler is None:
            _console_handler = RichHandler(rich_tracebacks=True, show_path=False)
            _console_handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        _console_handler.setLevel(level)
        if _console_handler not in root_logger.handlers:
            root_logger.addHandler(_console_handler)

        if _file_handler is None:
            path = diagnostic_log_path()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                _file_handler = RotatingFileHandler(
                    path,
                    maxBytes=5 * 1024 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
                _file_handler.setLevel(logging.DEBUG)
                _file_handler.setFormatter(_JsonLineFormatter())
            except OSError:
                _file_handler = None
        if _file_handler is not None and _file_handler not in root_logger.handlers:
            root_logger.addHandler(_file_handler)

        for logger_name in ("httpcore", "httpx", "websockets"):
            logging.getLogger(logger_name).setLevel(logging.WARNING)


@contextmanager
def logging_sink(handler: logging.Handler) -> Iterator[logging.Handler]:
    """Attach a sink to the shared pipeline for a bounded lifetime."""
    root_logger = logging.getLogger()
    already_attached = handler in root_logger.handlers
    if not already_attached:
        root_logger.addHandler(handler)
    try:
        yield handler
    finally:
        if not already_attached:
            root_logger.removeHandler(handler)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    *args: object,
    **context: object,
) -> None:
    """Emit readable prose plus stable metadata for non-console consumers."""
    exc_info = context.pop("_exc_info", False) is True
    logger.log(
        level,
        message,
        *args,
        exc_info=exc_info,
        extra={FMV_EVENT_ATTRIBUTE: event, FMV_CONTEXT_ATTRIBUTE: context},
    )
