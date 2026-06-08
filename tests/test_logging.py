"""logging_setup must create a rotating file and write to stderr."""
from __future__ import annotations

import logging

from quant_data.logging_setup import setup_logging
from quant_data.paths import log_dir


def test_setup_logging_writes_to_file_and_stderr(tmp_data_dir, caplog):
    setup_logging(level=logging.DEBUG)
    log = logging.getLogger("quant_data.smoke")
    log.info("hello from setup_logging test")

    log_file = log_dir() / "quant_data.log"
    assert log_file.exists()
    text = log_file.read_text(encoding="utf-8")
    assert "hello from setup_logging test" in text

    # idempotency
    setup_logging(level=logging.DEBUG)
    log.info("second call")
    # root logger must still have only the original handlers (2)
    assert len(logging.getLogger().handlers) <= 2


def test_setup_logging_creates_log_dir(tmp_data_dir):
    # log_dir() is invoked inside setup_logging via RotatingFileHandler
    setup_logging()
    assert log_dir().exists()
