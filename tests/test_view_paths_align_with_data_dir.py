"""Regression: DuckDB view bodies must reference the *active* DATA_DIR.

Background (v0.7, 2026-06-11)
-----------------------------
When we migrated data from the project-local DATA_DIR to ``/Volumes/RSS_DATA``,
``rsync -a`` copied the .duckdb file byte-for-byte — but the view bodies inside
it (mv_daily_v1 / qfq / hfq / trade_cal / mv_daily_basic) were created during
``DuckDBStore.bootstrap_views()`` and contain *baked-in* ``read_parquet(...)``
globs pointing at the original path. The result: tables queryable, views
broken. ``cli report`` exposed this via the ``view_rows`` field.

Fix
---
``bootstrap_views`` now uses ``CREATE OR REPLACE VIEW`` and re-substitutes
``@<topic>_tushare@`` placeholders at every call, so re-running it after a
DATA_DIR change re-bakes the new path.

This test pins the behavior: any view in the active DuckDB that uses
``read_parquet(...)`` must point at the active DATA_DIR, never at any other
DATA_DIR observed in the process environment or at hard-coded
``/Users/allenwang/...`` paths from older runs.
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest

from quant_data.paths import data_dir
from quant_data.store.duckdb_store import DuckDBStore


def test_view_paths_align_with_active_data_dir(tmp_data_dir: Path):
    """Bootstrap views against tmp DATA_DIR, then read their SQL back and
    assert every ``read_parquet(...)`` glob points inside ``tmp_data_dir``."""
    # 1. Bootstrap views against the tmp DATA_DIR.
    store = DuckDBStore()  # writes under tmp_data_dir
    store.bootstrap_views()
    del store  # DuckDBStore doesn't expose .close() — rely on GC

    # 2. Open read-only and inspect every view body.
    con = duckdb.connect(str(tmp_data_dir / "quant.duckdb"), read_only=True)
    try:
        view_rows = con.execute(
            "SELECT view_name, sql FROM duckdb_views() "
            "WHERE view_name LIKE 'mv\\_%' ESCAPE '\\'"
        ).fetchall()
    finally:
        con.close()

    assert view_rows, "expected at least one mv_* view after bootstrap"

    for name, sql in view_rows:
        # Every read_parquet('...') call must use the active DATA_DIR.
        for glob in _extract_parquet_globs(sql):
            assert str(tmp_data_dir) in glob, (
                f"view {name!r} has baked-in glob {glob!r} that does not "
                f"reference active DATA_DIR {tmp_data_dir}. Re-run "
                f"DuckDBStore.bootstrap_views() after changing DATA_DIR."
            )


def _extract_parquet_globs(sql: str) -> list[str]:
    """Return every ``read_parquet('<path>')`` argument from a view's SQL."""
    import re
    return re.findall(r"read_parquet\(\s*'([^']+)'\s*\)", sql)


def test_cli_report_view_rows_no_io_error(tmp_data_dir: Path):
    """End-to-end: bootstrap views then run ``cli report`` and assert all
    ``view_rows`` are ints (no ``err:`` strings).

    This is the smoke test for the v0.7 bug: previously, view_rows was an
    ``err: IO Error: No files found that match the pattern /Users/...`` string.
    """
    from click.testing import CliRunner
    from quant_data.cli import main

    store = DuckDBStore()
    store.bootstrap_views()
    del store  # rely on GC; DuckDBStore doesn't expose .close()

    runner = CliRunner()
    result = runner.invoke(main, ["report"], env={**os.environ, "DATA_DIR": str(tmp_data_dir)})
    assert result.exit_code == 0, f"cli report failed: {result.output}"
    import json
    payload = json.loads(result.output)
    for name, val in payload["view_rows"].items():
        assert isinstance(val, int), (
            f"view_rows[{name}] is {val!r}, expected int. "
            f"This is the v0.7 view-path bug — bootstrap_views was not re-run."
        )
