"""Central logging configuration and structured application events."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from rich.logging import RichHandler

FMV_EVENT_ATTRIBUTE = "fmv_event"
FMV_CONTEXT_ATTRIBUTE = "fmv_context"

_configuration_lock = Lock()
_console_handler: RichHandler | None = None


def configure_logging(level: str = "INFO") -> None:
    """Configure the process-wide console sink without duplicating handlers.

    The root accepts diagnostic events so non-console sinks can aggregate them;
    each presentation sink applies its own display threshold.
    """
    global _console_handler

    root_logger = logging.getLogger()
    with _configuration_lock:
        root_logger.setLevel(logging.DEBUG)
        if _console_handler is None:
            _console_handler = RichHandler(rich_tracebacks=True, show_path=False)
            _console_handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        _console_handler.setLevel(level)
        if _console_handler not in root_logger.handlers:
            root_logger.addHandler(_console_handler)

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
