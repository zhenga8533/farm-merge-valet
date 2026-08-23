from __future__ import annotations

import logging

from farm_merge_valet.logging_setup import configure_logging


def test_configure_logging_quiets_routine_http_requests() -> None:
    configure_logging()

    assert logging.getLogger("httpx").level == logging.WARNING
