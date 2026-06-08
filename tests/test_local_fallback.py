"""Verify the DATA_DIR fallback policy (v0.4 §6.5).

- DATA_DIR unset -> local default + info log; no failure.
- DATA_DIR set to /Volumes/... and missing -> fallback to local + warn.
"""
from __future__ import annotations

import logging

from quant_data.paths import data_dir


def test_data_dir_unset_falls_back_to_local(no_data_dir, caplog):
    caplog.set_level(logging.INFO, logger="quant_data.paths")
    p = data_dir()
    assert p == no_data_dir
    assert any("DATA_DIR 未设置" in r.message for r in caplog.records), \
        "expected info log when DATA_DIR is unset"


def test_data_dir_volumes_unmounted_falls_back(external_drive_unmounted, caplog):
    caplog.set_level(logging.WARNING, logger="quant_data.paths")
    p = data_dir()
    assert p == external_drive_unmounted
    msgs = [r.message for r in caplog.records]
    assert any("外挂盘但挂载缺失" in m for m in msgs), \
        f"expected warn log about missing /Volumes mount, got: {msgs}"


def test_data_dir_existing_path_is_used(tmp_data_dir):
    p = data_dir()
    assert p == tmp_data_dir
    assert p.exists()
