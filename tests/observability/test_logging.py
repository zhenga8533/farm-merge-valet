from __future__ import annotations

import logging

from rich.logging import RichHandler

from farm_merge_valet.observability.logging import (
    FMV_CONTEXT_ATTRIBUTE,
    FMV_EVENT_ATTRIBUTE,
    configure_logging,
    log_event,
    logging_sink,
)


class _CollectingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_configure_logging_quiets_routine_transport_messages() -> None:
    configure_logging()

    assert logging.getLogger().level == logging.DEBUG
    rich_handler = next(
        handler for handler in logging.getLogger().handlers if isinstance(handler, RichHandler)
    )
    assert rich_handler.level == logging.INFO
    assert logging.getLogger("httpcore").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("websockets").level == logging.WARNING


def test_configure_logging_does_not_duplicate_console_sink() -> None:
    configure_logging()
    configure_logging()

    rich_handlers = [
        handler for handler in logging.getLogger().handlers if isinstance(handler, RichHandler)
    ]
    assert len(rich_handlers) == 1


def test_log_event_preserves_message_and_structured_context(caplog) -> None:
    logger = logging.getLogger("farm_merge_valet.tests.observability")

    with caplog.at_level(logging.INFO):
        log_event(
            logger,
            logging.INFO,
            "action.submitted",
            "Submitted %s.",
            "move",
            effect="move",
            start=(1, 2),
        )

    record = next(record for record in caplog.records if record.message == "Submitted move.")
    assert getattr(record, FMV_EVENT_ATTRIBUTE) == "action.submitted"
    assert getattr(record, FMV_CONTEXT_ATTRIBUTE) == {"effect": "move", "start": (1, 2)}


def test_logging_sink_is_attached_once_for_its_lifetime() -> None:
    handler = logging.NullHandler()
    root_logger = logging.getLogger()

    with logging_sink(handler):
        assert root_logger.handlers.count(handler) == 1
        with logging_sink(handler):
            assert root_logger.handlers.count(handler) == 1
        assert root_logger.handlers.count(handler) == 1

    assert handler not in root_logger.handlers


def test_structured_event_fans_out_to_each_sink_exactly_once() -> None:
    first = _CollectingHandler()
    second = _CollectingHandler()
    logger = logging.getLogger("farm_merge_valet.tests.fanout")
    logger.setLevel(logging.INFO)

    with logging_sink(first), logging_sink(second):
        log_event(logger, logging.INFO, "runtime.ready", "Runtime ready.", scene_id=7)

    assert len(first.records) == 1
    assert len(second.records) == 1
    assert first.records[0] is second.records[0]
    assert first.records[0].fmv_event == "runtime.ready"
    assert first.records[0].fmv_context == {"scene_id": 7}
