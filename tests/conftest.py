"""Shared pytest fixtures."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(monkeypatch, tmp_path: Path) -> Path:
    """Point DATA_DIR at a fresh tmp dir for the duration of the test."""
    d = tmp_path / "quant_data"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", str(d))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    return d


@pytest.fixture
def no_data_dir(monkeypatch, tmp_path: Path) -> Path:
    """Unset DATA_DIR; ``paths.data_dir()`` should return the project default.

    We *don't* want the test to actually write to ``~/Code/quant-meta-team/quant_data/data``,
    so we patch LOCAL_DEFAULT to a tmp path.
    """
    monkeypatch.delenv("DATA_DIR", raising=False)
    fake = tmp_path / "fake_local"
    fake.mkdir(parents=True, exist_ok=True)
    from quant_data import paths as _paths
    monkeypatch.setattr(_paths, "LOCAL_DEFAULT", fake)
    return fake


@pytest.fixture
def external_drive_unmounted(monkeypatch, tmp_path: Path) -> Path:
    """Set DATA_DIR to a /Volumes/... path that does not exist; verify fallback."""
    monkeypatch.setenv("DATA_DIR", "/Volumes/THIS_DRIVE_DOES_NOT_EXIST_9999/quant_data")
    fake = tmp_path / "fake_local"
    fake.mkdir(parents=True, exist_ok=True)
    from quant_data import paths as _paths
    monkeypatch.setattr(_paths, "LOCAL_DEFAULT", fake)
    return fake
