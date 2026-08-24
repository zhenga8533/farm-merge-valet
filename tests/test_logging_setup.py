from __future__ import annotations

import logging

from farm_merge_valet.logging_setup import configure_logging


def test_configure_logging_quiets_routine_transport_messages() -> None:
    configure_logging()

    assert logging.getLogger("httpcore").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("websockets").level == logging.WARNING
