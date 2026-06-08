"""Logging setup for quant_data."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from quant_data.paths import log_dir

_INITIALIZED_LOG_PATH: Path | None = None


def setup_logging(level: int = logging.INFO) -> None:
    """Initialize root logger with both stream and rotating-file handlers.

    Idempotent *for the same log file path*. If LOG_DIR changes (e.g. across
    pytest tests that monkeypatch it), the handlers are re-attached to the
    new file.
    """
    global _INITIALIZED_LOG_PATH

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    log_path: Path = log_dir() / "quant_data.log"
    if _INITIALIZED_LOG_PATH == log_path:
        return

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)

    fh = RotatingFileHandler(log_path, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(sh)
    root.addHandler(fh)
    root.setLevel(level)

    _INITIALIZED_LOG_PATH = log_path
