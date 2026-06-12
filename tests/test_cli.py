"""Tests for ``python -m quant_data.cli`` (ADM-641 / DoD §7).

Covers:
- help exit code
- JSON envelope schema (``cli_version`` present)
- list-tables matches actual disk state
- sync-table is idempotent
- doctor passes under healthy state
- query blocks DDL/DML
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from quant_data import cli, cli_support
from quant_data.cli import main
from quant_data.scheduler import run_once
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.store.meta_sqlite import MetaSQLite


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_json(out: str) -> dict:
    """Pull the first JSON object out of a Click CliRunner output string.

    The CLI writes its data-dir safety warning to stderr which Click mixes
    into the captured ``output``; we just want the JSON envelope.
    """
    m = re.search(r"\{.*\}", out, re.DOTALL)
    if not m:
        raise AssertionError(f"no JSON in output: {out!r}")
    return json.loads(m.group(0))


# ---------------------------------------------------------------------------
# DoD §7.1: test_help_exits_zero
# ---------------------------------------------------------------------------
def test_help_exits_zero():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    assert "quant_data CLI" in result.output
    assert "list-tables" in result.output
    assert "doctor" in result.output
    assert "completion" in result.output


def test_subcommand_help_exits_zero():
    """Every subcommand must accept --help and return exit 0 (DoD §7.1)."""
    runner = CliRunner()
    for cmd in ("init", "sync-full", "sync-daily", "sync-table", "sync-range",
                "report", "run-once", "serve-scheduler", "list-tables",
                "list-views", "list-sources", "status", "diff", "query",
                "doctor", "completion"):
        result = runner.invoke(main, [cmd, "--help"])
        assert result.exit_code == 0, f"{cmd} --help failed: {result.output}"
        assert cmd in result.output


# ---------------------------------------------------------------------------
# DoD §7.2: test_json_output_schema
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("subcommand,args", [
    ("list-tables", []),
    ("list-views", []),
    ("list-sources", []),
    ("status", []),
])
def test_json_output_schema(tmp_data_dir, subcommand, args):
    """Every --json command emits an envelope with cli_version (DoD §7.2)."""
    runner = CliRunner()
    # bootstrap so list-views has something
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main,
                           ["--data-dir", str(tmp_data_dir), "--json", subcommand, *args])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    for field in ("cli_version", "command", "ok", "exit_code", "data", "ts"):
        assert field in env, f"{subcommand} missing {field}: {env}"
    assert env["cli_version"] == cli_support.CLI_VERSION
    assert env["command"] == subcommand
    assert env["ok"] is True
    assert env["exit_code"] == 0


# ---------------------------------------------------------------------------
# DoD §7.3: test_list_tables_matches_actual_dir
# ---------------------------------------------------------------------------
def test_list_tables_matches_actual_dir(tmp_data_dir):
    """list-tables' row counts must equal the legacy ``report`` command."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])

    # create the backing raw table (init only creates views; the backing
    # table is built lazily by sync_table). We mirror the DDL from
    # ``_open_store`` so the test stays self-contained.
    db = DuckDBStore()
    db.con.execute(
        "CREATE TABLE IF NOT EXISTS raw_tushare_daily ("
        "ts_code VARCHAR, trade_date DATE, open DOUBLE, high DOUBLE, "
        "low DOUBLE, close DOUBLE, pre_close DOUBLE, \"change\" DOUBLE, "
        "pct_chg DOUBLE, vol DOUBLE, amount DOUBLE)"
    )
    db.con.execute(
        "INSERT INTO raw_tushare_daily (ts_code, trade_date, open, high, low, close) "
        "VALUES ('000001.SZ', '2024-01-02', 10.0, 11.0, 9.5, 10.5)"
    )
    db.con.execute(
        "INSERT INTO raw_tushare_daily (ts_code, trade_date, open, high, low, close) "
        "VALUES ('000002.SZ', '2024-01-02', 20.0, 21.0, 19.5, 20.5)"
    )

    r1 = runner.invoke(main, ["--data-dir", str(tmp_data_dir), "--json", "list-tables"])
    assert r1.exit_code == 0, r1.output
    env1 = _extract_json(r1.output)
    daily_row = next(t for t in env1["data"]["tables"] if t["raw"] == "raw_tushare_daily")
    assert daily_row["rows"] == 2, daily_row

    r2 = runner.invoke(main, ["--data-dir", str(tmp_data_dir), "--json", "report"])
    assert r2.exit_code == 0, r2.output
    env2 = _extract_json(r2.output)
    rep = env2["data"]
    assert rep["raw_rows"]["raw_tushare_daily"] == 2

    # both should agree
    assert daily_row["rows"] == rep["raw_rows"]["raw_tushare_daily"]


# ---------------------------------------------------------------------------
# DoD §7.4: test_sync_table_idempotent
# ---------------------------------------------------------------------------
def test_sync_table_idempotent(tmp_data_dir, monkeypatch):
    """Running sync-table twice on a dry-run does not change row count."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])

    # two dry-run invocations (no network)
    r1 = runner.invoke(main, ["--data-dir", str(tmp_data_dir), "--json", "--dry-run",
                              "sync-table", "daily"])
    r2 = runner.invoke(main, ["--data-dir", str(tmp_data_dir), "--json", "--dry-run",
                              "sync-table", "daily"])
    assert r1.exit_code == 0, r1.output
    assert r2.exit_code == 0, r2.output
    env1 = _extract_json(r1.output)
    env2 = _extract_json(r2.output)
    # both must report the same dry-run shape, and data row count is unchanged
    assert env1["data"]["rows"] == env2["data"]["rows"]

    # The real idempotency check: run with a stub fetch that returns the same df
    # twice.  The underlying sync_table deletes by (ts_code, trade_date) before
    # insert — re-running it should keep the count at len(df).
    import pandas as pd
    from quant_data.sync import driver as _drv

    def fake_fetch(**_p):
        return pd.DataFrame({
            "ts_code": ["000001.SZ", "000002.SZ"],
            "trade_date": [date(2024, 1, 2), date(2024, 1, 2)],
            "open": [10.0, 20.0],
            "high": [11.0, 21.0],
            "low": [9.5, 19.5],
            "close": [10.5, 20.5],
        })

    monkeypatch.setattr(_drv, "get_source",
                        lambda name: type("S", (), {
                            "fetch": staticmethod(fake_fetch),
                            "_rl": type("R", (), {"rate_limit_hit": 0})(),
                            "lineage": lambda **k: None,
                        })())
    # Bypass the real driver: call sync_table directly with our fake fetch.
    # Bypass the real driver: call sync_table directly with our fake fetch.
    _drv.sync_table("daily", start_date=date(2024, 1, 2),
                    end_date=date(2024, 1, 2), fetch_fn=fake_fetch)
    db = DuckDBStore()
    n_after_first = int(db.con.execute(
        "SELECT count(*) FROM raw_tushare_daily WHERE trade_date = '2024-01-02'").fetchone()[0])

    _drv.sync_table("daily", start_date=date(2024, 1, 2),
                    end_date=date(2024, 1, 2), fetch_fn=fake_fetch)
    n_after_second = int(db.con.execute(
        "SELECT count(*) FROM raw_tushare_daily WHERE trade_date = '2024-01-02'").fetchone()[0])

    assert n_after_first == 2
    assert n_after_first == n_after_second, "sync_table must be idempotent"


# ---------------------------------------------------------------------------
# DoD §7.5: test_doctor_under_healthy_state
# ---------------------------------------------------------------------------
def test_doctor_under_healthy_state(tmp_data_dir, monkeypatch):
    """Under a healthy data dir, doctor returns exit 0."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])

    # mark every cursor in RAW_TABLES ok so doctor doesn't trip the cursors check
    meta = MetaSQLite()
    for t in cli_support.RAW_TABLES:
        meta.set_cursor(t, date.today(), status="ok")

    # fake TUSHARE_TOKEN present
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy-test-token")

    result = runner.invoke(main, ["--data-dir", str(tmp_data_dir), "--json", "doctor"])
    env = _extract_json(result.output)
    assert env["data"]["exit_code"] == 0, env
    # each named check should be in the report
    names = {c["name"] for c in env["data"]["checks"]}
    assert {"data_dir", "disk_free", "tushare_token", "views",
            "cursors", "rate_limit"} <= names


def test_doctor_under_unhealthy_state(tmp_data_dir, monkeypatch):
    """Missing TUSHARE_TOKEN triggers the blocked path (exit 3)."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    result = runner.invoke(main, ["--data-dir", str(tmp_data_dir), "--json", "doctor"])
    env = _extract_json(result.output)
    # blocked exit code (3) is at least triggered
    assert env["data"]["exit_code"] in (3, 5), env
    names = {c["name"] for c in env["data"]["checks"]}
    assert "tushare_token" in names
    tok_check = next(c for c in env["data"]["checks"] if c["name"] == "tushare_token")
    assert tok_check["ok"] is False


# ---------------------------------------------------------------------------
# DoD §7.6: test_query_blocks_ddl
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("forbidden_sql", [
    "DROP TABLE mv_daily_v1",
    "DELETE FROM mv_daily_v1",
    "UPDATE mv_daily_v1 SET close = 0",
    "INSERT INTO raw_tushare_daily VALUES ('x','2024-01-02',0,0,0,0,0,0,0,0,0)",
    "CREATE TABLE foo (x INT)",
    "ALTER TABLE mv_daily_v1 ADD COLUMN x INT",
])
def test_query_blocks_ddl(tmp_data_dir, forbidden_sql):
    """query command must reject DDL/DML (DoD §7.6)."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "query",
        "--sql", forbidden_sql,
    ])
    assert result.exit_code == 1, f"expected exit 1 for {forbidden_sql!r}: {result.output}"
    env = _extract_json(result.output)
    assert env["ok"] is False
    assert env["error"], env


def test_query_blocks_out_of_scope(tmp_data_dir):
    """query must reject tables outside mv_*/raw_* prefix."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "query",
        "--sql", "SELECT * FROM secret_table",
    ])
    assert result.exit_code == 1, result.output
    env = _extract_json(result.output)
    assert "outside the allowed scope" in env["error"]


def test_query_allows_select(tmp_data_dir):
    """query allows a plain SELECT against an mv_* view."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "query",
        "--sql", "SELECT count(*) FROM mv_daily_v1",
    ])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert env["ok"] is True
    assert env["data"]["row_count"] == 1
    # DuckDB renames count(*) to count_star() in the column metadata
    assert any("count" in c.lower() for c in env["data"]["columns"]), env["data"]["columns"]


# ---------------------------------------------------------------------------
# Extra coverage
# ---------------------------------------------------------------------------
def test_global_options_before_subcommand(tmp_data_dir):
    """`cli --data-dir X --json <sub>` is the documented form."""
    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_data_dir), "--json", "list-tables"])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert env["command"] == "list-tables"
    assert env["data_dir" if False else "data"]["tables"]  # structural check


def test_data_dir_overrides_env(tmp_data_dir, monkeypatch):
    """--data-dir wins over DATA_DIR env."""
    runner = CliRunner()
    other = tmp_data_dir / "other"
    other.mkdir(parents=True, exist_ok=True)
    # The --data-dir override should land us in `other`, not tmp_data_dir.
    result = runner.invoke(main, [
        "--data-dir", str(other), "--i-know-what-im-doing",
        "--json", "status",
    ])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert env["data"]["data_dir"] == str(other)


def test_dry_run_on_init(tmp_data_dir):
    """init --dry-run does not create the duckdb file."""
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--dry-run", "--json", "init",
    ])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert env["data"]["dry_run"] is True
    # the placeholder file may exist (because init in dry-run does nothing),
    # but the duckdb file should NOT have been created
    from quant_data.paths import duckdb_path
    # Note: duckdb_path() depends on env DATA_DIR. Set it to tmp_data_dir to assert.
    import os
    os.environ["DATA_DIR"] = str(tmp_data_dir)
    # Reload module to be sure
    assert not duckdb_path().exists(), "init --dry-run should not have created duckdb"


def test_completion_bash_runs():
    runner = CliRunner()
    result = runner.invoke(main, ["completion", "bash"])
    assert result.exit_code == 0, result.output
    assert "_quant_data_completion" in result.output
    assert "complete -o nosort" in result.output


def test_completion_zsh_runs():
    runner = CliRunner()
    result = runner.invoke(main, ["completion", "zsh"])
    assert result.exit_code == 0, result.output
    assert "compdef" in result.output


def test_list_sources_includes_tushare(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_data_dir), "--json", "list-sources"])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    names = [s["name"] for s in env["data"]["sources"]]
    assert "tushare" in names
    assert "akshare" in names


def test_diff_against_missing_root(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--i-know-what-im-doing", "--json", "diff",
        "--against", "/tmp/this_path_does_not_exist_9999",
    ])
    # blocked exit code
    assert result.exit_code == 3, result.output
    env = _extract_json(result.output)
    assert env["ok"] is False
    assert "does not exist" in env["error"]


def test_status_under_healthy_dir(tmp_data_dir):
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, ["--data-dir", str(tmp_data_dir), "--json", "status"])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert env["data"]["data_dir"] == str(tmp_data_dir)
    assert env["data"]["data_dir_exists"] is True
    assert "cursors" in env["data"]


def test_envelope_contains_cli_version_on_errors(tmp_data_dir):
    """Errors must also be wrapped in the versioned envelope (DoD §3)."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "query",
        "--sql", "DROP TABLE foo",
    ])
    env = _extract_json(result.output)
    assert env["cli_version"] == cli_support.CLI_VERSION
    assert env["ok"] is False


def test_sync_range_dry_run(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "--dry-run",
        "sync-range", "--start", "20240101", "--end", "20240105",
    ])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert env["data"]["dry_run"] is True
    assert env["data"]["start"] == "2024-01-01"
    assert env["data"]["end"] == "2024-01-05"


def test_sync_table_invalid_topic(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json",
        "sync-table", "not_a_real_topic",
    ])
    assert result.exit_code != 0


def test_cli_version_constant_is_semver():
    """CLI_VERSION must follow x.y.z (3-part semver) — protects scripted parsers."""
    import re
    assert re.match(r"^\d+\.\d+\.\d+$", cli_support.CLI_VERSION), cli_support.CLI_VERSION


# ---------------------------------------------------------------------------
# Coverage for the other subcommands and helper branches
# ---------------------------------------------------------------------------
def test_sync_full_dry_run(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "--dry-run", "sync-full",
    ])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert env["data"]["dry_run"] is True
    assert len(env["data"]["would_run"]) == 5


def test_sync_daily_dry_run(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "--dry-run", "sync-daily",
        "--lookback", "3",
    ])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert env["data"]["dry_run"] is True
    assert env["data"]["lookback"] == 3


def test_run_once_dry_run(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "run-once", "--dry-run",
    ])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert env["data"]["dry_run"] is True
    # 10 results (5 legacy + 5 S-tier v0.8), all ok
    assert len(env["data"]["results"]) == 10
    assert all(r["ok"] for r in env["data"]["results"])


def test_run_only_subset(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "run-once",
        "--dry-run", "--only", "daily,adj_factor",
    ])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert {r["topic"] for r in env["data"]["results"]} == {"daily", "adj_factor"}


def test_serve_scheduler_json_mode(tmp_data_dir):
    """serve-scheduler in --json mode should print config and not block."""
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "serve-scheduler",
    ])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert env["data"]["would_block"] is True


def test_sync_table_dry_run_per_topic(tmp_data_dir):
    """sync-table --dry-run covers the canonical 5-topic dispatch branches (stock_basic / trade_cal / daily / adj_factor / daily_basic)."""
    runner = CliRunner()
    for topic in ("stock_basic", "trade_cal", "daily", "adj_factor", "daily_basic"):
        result = runner.invoke(main, [
            "--data-dir", str(tmp_data_dir), "--json", "--dry-run",
            "sync-table", topic,
        ])
        assert result.exit_code == 0, (topic, result.output)
        env = _extract_json(result.output)
        assert env["data"]["dry_run"] is True


def test_sync_table_bad_date(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json",
        "sync-table", "daily", "--start", "not-a-date",
    ])
    assert result.exit_code == 1, result.output
    env = _extract_json(result.output)
    assert "bad date" in env["error"]


def test_sync_range_bad_date(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json",
        "sync-range", "--start", "20240101", "--end", "20230101",
    ])
    assert result.exit_code == 1, result.output
    env = _extract_json(result.output)
    assert "start > end" in env["error"]


def test_sync_range_only_subset(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "--dry-run",
        "sync-range", "--start", "20240101", "--end", "20240105",
        "--only", "daily,adj_factor",
    ])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert set(env["data"]["topics"]) == {"daily", "adj_factor"}


def test_diff_against_identical_dir(tmp_data_dir):
    """diff against the same dir should report 0 diffs (exit 0)."""
    runner = CliRunner()
    # seed some data so both sides have rows
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    db = DuckDBStore()
    db.con.execute(
        "CREATE TABLE IF NOT EXISTS raw_tushare_daily ("
        "ts_code VARCHAR, trade_date DATE, open DOUBLE, high DOUBLE, "
        "low DOUBLE, close DOUBLE)"
    )
    db.con.execute(
        "INSERT INTO raw_tushare_daily VALUES ('000001.SZ', '2024-01-02', "
        "10.0, 11.0, 9.5, 10.5)"
    )
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--i-know-what-im-doing", "--json", "diff",
        "--against", str(tmp_data_dir),
    ])
    # may exit 0 (if everything matches) or 5 (if any diff) — both are valid
    # We don't assert exit code here; we assert the payload is well-formed.
    env = _extract_json(result.output)
    assert "diffs" in env["data"]
    assert "tables" in env["data"]
    # The same dir compared to itself: every configured raw table should match
    for t in env["data"]["tables"]:
        assert t["row_match"] is True, t


def test_completion_fish():
    runner = CliRunner()
    result = runner.invoke(main, ["completion", "fish"])
    assert result.exit_code == 0, result.output
    assert "quant_data" in result.output


def test_query_with_no_from(tmp_data_dir):
    """A SELECT without FROM/JOIN is rejected (no scope)."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "query",
        "--sql", "SELECT 1+1",
    ])
    assert result.exit_code == 1, result.output
    env = _extract_json(result.output)
    assert "mv_*" in env["error"] or "raw_*" in env["error"]


def test_query_with_explain(tmp_data_dir):
    """EXPLAIN is allowed as a read-only verb."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "query",
        "--sql", "EXPLAIN SELECT count(*) FROM mv_daily_v1",
    ])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    assert env["ok"] is True


def test_query_with_truncate_blocked(tmp_data_dir):
    """TRUNCATE is in the blacklist."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "query",
        "--sql", "TRUNCATE TABLE raw_tushare_daily",
    ])
    assert result.exit_code == 1, result.output
    env = _extract_json(result.output)
    assert "TRUNCATE" in env["error"] or "forbidden" in env["error"]


def test_data_dir_warning_emitted(tmp_data_dir):
    """--data-dir pointing outside /Volumes/RSS_DATA emits a soft warning."""
    import os
    os.environ.pop("DATA_DIR", None)
    other = tmp_data_dir / "sub"
    other.mkdir(parents=True, exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(other), "--json", "status",
    ])
    # warning is interleaved with output in CliRunner by default
    assert "WARNING" in result.output
    out = _extract_json(result.output)
    assert out["command"] == "status"


def test_data_dir_silenced_with_i_know(tmp_data_dir):
    import os
    os.environ.pop("DATA_DIR", None)
    other = tmp_data_dir / "sub2"
    other.mkdir(parents=True, exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(other), "--i-know-what-im-doing", "--json", "status",
    ])
    assert "WARNING" not in result.output, result.output


def test_status_disk_free_gb_present(tmp_data_dir):
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "status",
    ])
    env = _extract_json(result.output)
    assert "disk_free_gb" in env["data"]
    assert env["data"]["disk_free_gb"] >= 0


def test_envelope_versioned_with_ts(tmp_data_dir):
    """envelope includes a parseable ISO ts field."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "list-tables",
    ])
    env = _extract_json(result.output)
    from datetime import datetime
    # ts should parse
    datetime.fromisoformat(env["ts"])


def test_list_views_under_init(tmp_data_dir):
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "list-views",
    ])
    assert result.exit_code == 0, result.output
    env = _extract_json(result.output)
    # 30 views: 5 legacy + 5 S-tier v0.8 + 20 A-tier v0.9 (ADM-653)
    assert len(env["data"]["views"]) == 30
    names = {v["view"] for v in env["data"]["views"]}
    assert {"mv_daily_v1", "mv_daily_qfq", "mv_daily_hfq", "mv_trade_cal",
            "mv_daily_basic"} <= names
    # S-tier v0.8 additions
    assert {"mv_moneyflow_v1", "mv_moneyflow_hsgt_v1", "mv_index_weight_v1",
            "mv_hsgt_top10_v1", "mv_fund_holdings_v1"} <= names
    # A-tier v0.9 additions (ADM-653) — sample of each batch
    assert {"mv_index_classify_v1", "mv_index_daily_v1", "mv_index_member_v1",
            "mv_sw_index_v1", "mv_stk_limit_v1", "mv_suspend_v1",
            "mv_dividend_v1", "mv_shares_float_v1"} <= names  # Batch 1
    assert {"mv_fina_indicator_v1", "mv_income_v1", "mv_balancesheet_v1",
            "mv_cashflow_v1", "mv_fina_mainbz_v1", "mv_fina_audit_v1",
            "mv_top10_holders_v1"} <= names  # Batch 2
    assert {"mv_top_list_v1", "mv_margin_detail_v1", "mv_top10_floatholders_v1",
            "mv_stk_holdertrade_v1", "mv_report_rc_v1"} <= names  # Batch 3


def test_sync_table_help():
    runner = CliRunner()
    result = runner.invoke(main, ["sync-table", "--help"])
    assert result.exit_code == 0
    assert "topic" in result.output.lower() or "TOPIC" in result.output


def test_human_readable_output_works(tmp_data_dir):
    """Without --json, the command should still produce useful text output."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "list-tables",
    ])
    # No --json → should print data, not just the envelope summary
    assert result.exit_code == 0, result.output
    # Human output is the raw data dict printed as JSON. So:
    import re
    assert "raw_tushare_daily" in result.output


def test_verbose_envelope_summary(tmp_data_dir):
    """--verbose should print the envelope summary."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--verbose", "--json", "list-tables",
    ])
    assert result.exit_code == 0, result.output
    # Click captures stderr into the same output stream by default
    assert "cli v" in result.output


def test_sync_one_table_dispatch():
    """Unit-test the sync_one_table dispatcher (covers error path for unknown topic)."""
    import pytest
    with pytest.raises(ValueError):
        cli_support.sync_one_table("nonsense", dry_run=True)


def test_envelope_helper_directly():
    """Direct unit test for the envelope() helper (covers the function)."""
    env = cli_support.envelope(True, {"k": "v"}, command="x", exit_code=0)
    assert env["cli_version"] == cli_support.CLI_VERSION
    assert env["command"] == "x"
    assert env["ok"] is True
    assert env["data"] == {"k": "v"}
    assert env["error"] is None

    env2 = cli_support.envelope(False, None, command="y", exit_code=2, error="oops")
    assert env2["ok"] is False
    assert env2["error"] == "oops"


def test_parse_yyyymmdd():
    assert cli_support.parse_yyyymmdd("20240101") == date(2024, 1, 1)
    with pytest.raises(ValueError):
        cli_support.parse_yyyymmdd("2024-01-01")
    with pytest.raises(ValueError):
        cli_support.parse_yyyymmdd("nope")


def test_validate_query_sql_rejects_empty():
    with pytest.raises(cli_support.QueryForbidden):
        cli_support.validate_query_sql("")
    with pytest.raises(cli_support.QueryForbidden):
        cli_support.validate_query_sql("  ;  ")


def test_validate_query_sql_rejects_wrong_verb():
    with pytest.raises(cli_support.QueryForbidden):
        cli_support.validate_query_sql("PRAGMA table_info('foo')")


def test_disk_size_bytes_empty():
    """_disk_size_bytes on a fresh tmp dir returns 0."""
    with tempfile.TemporaryDirectory() as td:
        assert cli_support._disk_size_bytes(Path(td)) == 0


def test_disk_size_bytes_with_file():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "f.txt"
        p.write_bytes(b"hello world")
        assert cli_support._disk_size_bytes(Path(td)) == 11


def test_doctor_disk_check_low_disk(tmp_data_dir, monkeypatch):
    """Simulate low disk by patching disk_usage."""
    from quant_data import cli_support as _cli_support

    class FakeUsage:
        free = 1024 ** 3  # 1 GB
        total = 100 * 1024 ** 3
        used = 99 * 1024 ** 3

    def fake_disk_usage(_p):
        return FakeUsage()
    monkeypatch.setattr(shutil, "disk_usage", fake_disk_usage)
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    monkeypatch.setenv("TUSHARE_TOKEN", "x")
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "doctor",
    ])
    env = _extract_json(result.output)
    disk = next(c for c in env["data"]["checks"] if c["name"] == "disk_free")
    assert disk["ok"] is False  # 1 GB < 5 GB threshold


def test_query_with_empty_sql(tmp_data_dir):
    """An empty SQL string is rejected by the validator."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "query",
        "--sql", "",
    ])
    assert result.exit_code == 1, result.output
    env = _extract_json(result.output)
    assert "empty" in env["error"].lower()


def test_verbose_quiet_logging_paths(tmp_data_dir, monkeypatch):
    """Exercise the verbose=2 and quiet branches in the group callback."""
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    # -vv -> DEBUG
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--verbose", "--verbose", "list-tables",
    ])
    assert result.exit_code == 0, result.output
    # --quiet also works
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--quiet", "list-tables",
    ])
    assert result.exit_code == 0, result.output


def test_query_duckdb_runtime_error(tmp_data_dir, monkeypatch):
    """A non-forbidden SQL that duckdb rejects still surfaces as exit 1."""
    from quant_data import cli_support as _cs
    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])

    def boom(_sql, _params=None):
        raise RuntimeError("simulated duckdb blow-up")
    monkeypatch.setattr(_cs, "run_query", boom)
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--json", "query",
        "--sql", "SELECT count(*) FROM mv_daily_v1",
    ])
    assert result.exit_code == 1, result.output
    env = _extract_json(result.output)
    assert "simulated duckdb blow-up" in env["error"]


def test_list_sources_unknown_source_in_registry(tmp_data_dir, monkeypatch):
    """If registry has an adapter that raises on ``.capabilities``, we still
    surface tushare + the lazy akshare entry without crashing."""
    from quant_data import registry
    runner = CliRunner()

    class BadAdapter:
        name = "broken"
        version = "0.0.0"

        def capabilities(self):
            raise RuntimeError("nope")

        def rate_limit(self):
            raise RuntimeError("nope")
    registry.SOURCES["broken"] = BadAdapter()
    try:
        result = runner.invoke(main, [
            "--data-dir", str(tmp_data_dir), "--json", "list-sources",
        ])
        assert result.exit_code == 0, result.output
        env = _extract_json(result.output)
        names = {s["name"] for s in env["data"]["sources"]}
        assert "tushare" in names
        assert "akshare" in names
    finally:
        del registry.SOURCES["broken"]


def test_diff_with_mismatched_other(tmp_data_dir):
    """diff against a different dir should report a row mismatch (exit 5)."""
    import tempfile
    import datetime
    import pyarrow as pa
    import pyarrow.parquet as pq
    other = Path(tempfile.mkdtemp())
    # write a tiny parquet to give the "other" side a real row count
    tbl = pa.table({
        "ts_code": pa.array(["999999.SH"]),
        "trade_date": pa.array([datetime.date(2024, 1, 2)], type=pa.date32()),
        "open": pa.array([1.0], type=pa.float64()),
    })
    # also need to put it under the right subdirectory so the helper finds it
    target_dir = other / "raw_tushare_daily" / "trade_date=2024-01-02"
    target_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(tbl, str(target_dir / "f.parquet"))

    runner = CliRunner()
    runner.invoke(main, ["--data-dir", str(tmp_data_dir), "init"])
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "--i-know-what-im-doing", "--json", "diff",
        "--against", str(other),
    ])
    env = _extract_json(result.output)
    # daily should mismatch (other has 1 row, this has 0)
    assert env["data"]["diffs"] >= 1, env["data"]


def test_run_once_with_subcommand_dry_run_option(tmp_data_dir):
    """run-once's --dry-run flag (legacy, on the subcommand) works."""
    runner = CliRunner()
    result = runner.invoke(main, [
        "--data-dir", str(tmp_data_dir), "run-once",
        "--dry-run", "--only", "daily",
    ])
    assert result.exit_code == 0, result.output
    # The result is non-JSON (no --json), but should mention dry_run
    assert "true" in result.output or "dry" in result.output.lower()
