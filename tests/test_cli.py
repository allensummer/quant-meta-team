"""Smoke tests for ``python -m quant_data.cli``."""
from __future__ import annotations

from click.testing import CliRunner

from quant_data.cli import main
from quant_data.store.meta_sqlite import MetaSQLite


def test_cli_init_creates_duckdb_and_views(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    # views must exist
    from quant_data.store.duckdb_store import DuckDBStore
    db = DuckDBStore()
    views = [
        r[0] for r in db.con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
        ).fetchall()
    ]
    assert {"mv_daily_v1", "mv_daily_qfq", "mv_daily_hfq", "mv_trade_cal"} <= set(views)


def test_cli_report_returns_json(tmp_data_dir):
    runner = CliRunner()
    # init first so report has something to summarize
    runner.invoke(main, ["init"])
    result = runner.invoke(main, ["report"])
    assert result.exit_code == 0, result.output
    import json
    parsed = json.loads(result.output)
    assert "data_dir" in parsed
    assert "raw_rows" in parsed
    assert "view_rows" in parsed
    assert "cursors" in parsed
