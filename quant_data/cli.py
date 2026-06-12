"""``python -m quant_data.cli`` — 15 subcommands covering init / sync / inspect / diagnose.

Exit codes (DoD §4)
------------------
  0  ok
  1  generic / unhandled error
  2  partial sync failure (run-once has historically used this)
  3  blocked (DATA_DIR missing, TUSHARE_TOKEN unset, view missing)
  4  rate-limit hit repeatedly (must @mention the human)
  5  data quality gate failed (diff mismatch / doctor red flag)

JSON envelope (DoD §3)
---------------------
Every command line that takes ``--json`` emits a versioned envelope:
    {cli_version, command, ok, exit_code, error, data, ts}

The same payload is also what the human-readable output renders in
summary form (we print ``data`` directly, with envelope fields in stderr).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import click

from quant_data import cli_support
from quant_data.logging_setup import setup_logging
from quant_data.paths import data_dir, duckdb_path, sqlite_path
from quant_data.scheduler import ScheduleConfig, run_once, serve_forever
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.store.meta_sqlite import MetaSQLite
from quant_data.sync import (
    sync_daily, sync_adj_factor, sync_daily_basic, sync_full, sync_stock_basic, sync_trade_cal,
    sync_moneyflow, sync_moneyflow_hsgt, sync_hsgt_top10,
    sync_index_weight, sync_fund_holdings,
)


# ---------------------------------------------------------------------------
# CLI context
# ---------------------------------------------------------------------------
class CLIContext:
    def __init__(self) -> None:
        self.data_dir: str | None = None
        self.json_output: bool = False
        self.dry_run: bool = False
        self.verbose: int = 0
        self.quiet: bool = False
        self.i_know: bool = False


pass_ctx = click.make_pass_decorator(CLIContext, ensure=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# Global option naming convention (DoD §2):
# All global options live on the GROUP only — they must appear BEFORE the
# subcommand name. This is the standard Click convention and means
# ``cli --data-dir /tmp --json list-tables`` works as expected.
# (The shorthand ``cli list-tables --data-dir /tmp`` is NOT supported;
# documented in cli-reference.md.)
def _emit(ctx: CLIContext, command: str, payload: dict[str, Any],
          *, exit_code: int = 0, error: str | None = None) -> None:
    """Print either the JSON envelope (--json) or a human summary.

    The exit code is communicated via :func:`_finalize`, never raised here, so
    callers can continue cleanup.
    """
    ok = exit_code == 0 and error is None
    env = cli_support.envelope(ok, payload, command=command,
                                error=error, exit_code=exit_code)
    if ctx.json_output:
        click.echo(json.dumps(env, ensure_ascii=False, indent=2))
    else:
        # human: print the data dict, then summarize
        if error:
            click.echo(f"ERROR: {error}", err=True)
        else:
            click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    # envelope summary on stderr (works in both json and human mode when --verbose)
    if ctx.verbose:
        click.echo(
            f"[cli v{env['cli_version']}] cmd={command} ok={ok} "
            f"exit={exit_code} ts={env['ts']}",
            err=True,
        )


def _finalize(ctx: CLIContext, exit_code: int) -> None:
    """Exit cleanly. We use ``click.exceptions.Exit`` (not SystemExit) so the
    code propagates correctly through CliRunner — SystemExit gets converted
    to ``exit_code=1`` by Click's runner machinery in some versions.
    """
    if exit_code != 0:
        import click as _click
        raise _click.exceptions.Exit(exit_code)


def _warn_data_dir_safety(p: Path, ctx: CLIContext) -> None:
    """Print a soft warning if --data-dir points outside /Volumes/RSS_DATA."""
    if ctx.i_know:
        return
    s = str(p)
    # The conventional safe target is /Volumes/RSS_DATA/quant_data.
    if s.startswith("/Volumes/RSS_DATA"):
        return
    click.echo(
        f"WARNING: --data-dir {s} is not under /Volumes/RSS_DATA. "
        f"Pass --i-know-what-im-doing to silence this warning.",
        err=True,
    )


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------
@click.group()
@click.option("--data-dir", "data_dir_opt", default=None,
              help="Override DATA_DIR (default: read DATA_DIR env)")
@click.option("--quiet/--no-quiet", "quiet", default=None,
              help="Suppress info logs on stderr")
@click.option("--verbose", "verbose", count=True, default=0,
              help="Increase verbosity (repeatable, e.g. -vv)")
@click.option("--json", "json_output", is_flag=True, default=False,
              help="Emit versioned JSON envelope (DoD §3)")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="In init/sync-* commands, do not call network (DoD §2)")
@click.option("--i-know-what-im-doing", "i_know", is_flag=True, default=False,
              help="Acknowledge --data-dir safety warning")
@pass_ctx
def main(ctx: CLIContext, data_dir_opt: str | None, quiet: bool | None,
         verbose: int, json_output: bool, dry_run: bool, i_know: bool) -> None:
    """quant_data CLI — A-share data localization: tushare + akshare → DuckDB."""
    ctx.data_dir = data_dir_opt
    ctx.json_output = json_output
    ctx.dry_run = dry_run
    ctx.verbose = verbose
    ctx.quiet = bool(quiet) if quiet is not None else False
    ctx.i_know = i_know

    # 1. data dir override (set env BEFORE any path helper is called)
    if data_dir_opt:
        p = Path(data_dir_opt).expanduser()
        _warn_data_dir_safety(p, ctx)
        os.environ["DATA_DIR"] = str(p)
        # also redirect LOG_DIR to a sibling if not already set
        os.environ.setdefault("LOG_DIR", str(p.parent / "logs"))

    # 2. logging level
    level = logging.WARNING
    if verbose >= 2:
        level = logging.DEBUG
    elif verbose == 1:
        level = logging.INFO
    if ctx.quiet:
        level = max(level, logging.WARNING)
    setup_logging(level=level)


# ---------------------------------------------------------------------------
# 1) init — keep the original 6.1K behaviour
# ---------------------------------------------------------------------------
@main.command("init")
@pass_ctx
def cmd_init(ctx: CLIContext) -> None:
    """Create data dir, register schemas, bootstrap DuckDB views."""
    log = logging.getLogger("quant_data.cli")
    dd = data_dir()
    log.info("data_dir = %s", dd)
    log.info("duckdb  = %s", duckdb_path())
    log.info("sqlite  = %s", sqlite_path())
    if ctx.dry_run:
        _emit(ctx, "init", {
            "dry_run": True,
            "data_dir": str(dd),
            "duckdb": str(duckdb_path()),
        })
        return
    db = DuckDBStore()
    db.bootstrap_views()
    views = [r[0] for r in db.con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
    ).fetchall()]
    log.info("views bootstrapped: %s", ", ".join(views))
    _emit(ctx, "init", {
        "data_dir": str(dd),
        "duckdb": str(duckdb_path()),
        "views": views,
    })


# ---------------------------------------------------------------------------
# 2) sync-full
# ---------------------------------------------------------------------------
@main.command("sync-full")
@pass_ctx
def cmd_sync_full(ctx: CLIContext) -> None:
    """Backfill all configured tushare tables for the full A-share history."""
    log = logging.getLogger("quant_data.cli")
    if ctx.dry_run:
        _emit(ctx, "sync-full", {"dry_run": True, "would_run": [
            "sync_stock_basic", "sync_trade_cal", "sync_daily",
            "sync_adj_factor", "sync_daily_basic",
        ]})
        return
    reports = sync_full()
    log.info("sync_full complete: %s", reports)
    _emit(ctx, "sync-full", {"reports": reports})


# ---------------------------------------------------------------------------
# 3) sync-daily (legacy)
# ---------------------------------------------------------------------------
@main.command("sync-daily")
@click.option("--lookback", default=5, help="Days to look back from today")
@pass_ctx
def cmd_sync_daily(ctx: CLIContext, lookback: int) -> None:
    """Incremental daily sync (today - lookback days)."""
    if ctx.dry_run:
        _emit(ctx, "sync-daily", {"dry_run": True, "lookback": lookback,
                                  "topics": cli_support.TOPIC_META_LIST})
        return
    end = date.today()
    start = end - timedelta(days=lookback)
    out = []
    for fn in (sync_stock_basic,
               lambda: sync_trade_cal(start=start, end=end),
               lambda: sync_daily(start_date=start, end_date=end),
               lambda: sync_adj_factor(start_date=start, end_date=end),
               lambda: sync_daily_basic(start_date=start, end_date=end)):
        out.append(fn())
    _emit(ctx, "sync-daily", {"lookback": lookback, "start": str(start),
                              "end": str(end), "reports": out})


# ---------------------------------------------------------------------------
# 4) sync-table <topic> — single table incremental
# ---------------------------------------------------------------------------
@main.command("sync-table")
@click.argument("topic", type=click.Choice(sorted(cli_support.TOPIC_META)))
@click.option("--start", default=None, help="Lower bound YYYYMMDD")
@click.option("--end", default=None, help="Upper bound YYYYMMDD")
@pass_ctx
def cmd_sync_table(ctx: CLIContext, topic: str, start: str | None, end: str | None) -> None:
    """Single-table incremental sync (5 topics)."""
    try:
        s_d = cli_support.parse_yyyymmdd(start) if start else None
        e_d = cli_support.parse_yyyymmdd(end) if end else None
    except ValueError as ve:
        _emit(ctx, "sync-table", {}, exit_code=1, error=f"bad date: {ve}")
        _finalize(ctx, 1)
        return
    try:
        rep = cli_support.sync_one_table(topic, start=s_d, end=e_d,
                                          dry_run=ctx.dry_run)
    except Exception as e:  # noqa: BLE001
        _emit(ctx, "sync-table", {"topic": topic}, exit_code=2, error=str(e))
        _finalize(ctx, 2)
        return
    exit_code = 0 if rep.get("ok", True) else 2
    _emit(ctx, "sync-table", rep, exit_code=exit_code)
    _finalize(ctx, exit_code)


# ---------------------------------------------------------------------------
# 5) sync-range — arbitrary date range (DoD 1)
# ---------------------------------------------------------------------------
@main.command("sync-range")
@click.option("--start", required=True, help="Start date YYYYMMDD")
@click.option("--end", required=True, help="End date YYYYMMDD")
@click.option("--only", default="",
              help="Comma-separated topic subset (default: all configured topics)")
@pass_ctx
def cmd_sync_range(ctx: CLIContext, start: str, end: str, only: str) -> None:
    """Sync all (or --only subset) tables across [start, end]."""
    try:
        s_d = cli_support.parse_yyyymmdd(start)
        e_d = cli_support.parse_yyyymmdd(end)
    except ValueError as ve:
        _emit(ctx, "sync-range", {}, exit_code=1, error=f"bad date: {ve}")
        _finalize(ctx, 1)
        return
    if s_d > e_d:
        _emit(ctx, "sync-range", {"start": str(s_d), "end": str(e_d)},
              exit_code=1, error="start > end")
        _finalize(ctx, 1)
        return
    only_t = tuple(t.strip() for t in only.split(",") if t.strip())
    if ctx.dry_run:
        _emit(ctx, "sync-range", {
            "dry_run": True, "start": str(s_d), "end": str(e_d),
            "topics": list(only_t) if only_t else list(cli_support.TOPIC_META),
        })
        return
    try:
        reports = cli_support.sync_range(s_d, e_d, only=only_t, dry_run=False)
    except Exception as e:  # noqa: BLE001
        _emit(ctx, "sync-range", {"start": str(s_d), "end": str(e_d)},
              exit_code=2, error=str(e))
        _finalize(ctx, 2)
        return
    # exit 2 if any report indicates failure
    any_fail = any(r.get("ok") is False for r in reports)
    exit_code = 2 if any_fail else 0
    _emit(ctx, "sync-range", {"start": str(s_d), "end": str(e_d), "reports": reports},
          exit_code=exit_code)
    _finalize(ctx, exit_code)


# ---------------------------------------------------------------------------
# 6) report — legacy
# ---------------------------------------------------------------------------
@main.command("report")
@pass_ctx
def cmd_report(ctx: CLIContext) -> None:
    """Print row counts + cursor state + lineage + disk usage (legacy)."""
    db = DuckDBStore()
    meta = MetaSQLite()
    log = logging.getLogger("quant_data.cli")

    # Row counts come from the DuckDB backing tables (the source of truth
    # for the read views). The legacy read_parquet() approach is no longer
    # used because the backing DuckDB tables reflect the upserts even
    # when the parquet tree is empty.
    rows: dict[str, int] = {}
    for topic, m in cli_support.TOPIC_META.items():
        raw = m["raw"]
        try:
            rows[raw] = int(db.con.execute(f"SELECT count(*) FROM {raw}").fetchone()[0])
        except Exception as e:
            log.debug("count %s failed: %s", raw, e)
            rows[raw] = 0
    view_rows: dict[str, Any] = {}
    for v in ("mv_daily_v1", "mv_daily_qfq", "mv_daily_hfq", "mv_trade_cal", "mv_daily_basic"):
        try:
            view_rows[v] = int(db.con.execute(f"SELECT count(*) FROM {v}").fetchone()[0])
        except Exception as e:
            view_rows[v] = f"err: {e}"

    out = {
        "data_dir": str(data_dir()),
        "disk_bytes": cli_support._disk_size_bytes(data_dir()),
        "raw_rows": rows,
        "view_rows": view_rows,
        "cursors": meta.all_cursors(),
    }
    _emit(ctx, "report", out)


# ---------------------------------------------------------------------------
# 7) run-once (legacy)
# ---------------------------------------------------------------------------
@main.command("run-once")
@click.option("--lookback", default=1, help="Days to look back from today for time-series tables")
@click.option("--dry-run/--no-dry-run", "sub_dry_run", default=None,
              help="Log the topics that *would* sync, no network calls")
@click.option("--only", default="",
              help="Comma-separated topic subset (default: all configured topics)")
@pass_ctx
def cmd_run_once(ctx: CLIContext, lookback: int, sub_dry_run: bool | None, only: str) -> None:
    """Run a single sweep across all configured topics — used by launchd / manual cron."""
    dry = bool(sub_dry_run) if sub_dry_run is not None else ctx.dry_run
    only_t = tuple(t.strip() for t in only.split(",") if t.strip())
    results = run_once(lookback_days=lookback, dry_run=dry, only=only_t)
    payload = [
        {"topic": r.topic, "ok": r.ok, "duration_s": round(r.duration_s, 2),
         "report": r.report, "error": r.error}
        for r in results
    ]
    exit_code = 0 if all(r.ok for r in results) else 2
    _emit(ctx, "run-once", {"lookback": lookback, "dry_run": dry,
                            "results": payload}, exit_code=exit_code)
    _finalize(ctx, exit_code)


# ---------------------------------------------------------------------------
# 8) serve-scheduler (legacy)
# ---------------------------------------------------------------------------
@main.command("serve-scheduler")
@click.option("--hour", default=17, help="Trigger hour (24h, Asia/Shanghai)")
@click.option("--minute", default=30, help="Trigger minute")
@click.option("--day-of-week", default="mon-fri",
              help="APScheduler day-of-week expression")
@click.option("--lookback", default=1, help="Days to look back for time-series tables")
@click.option("--dry-run/--no-dry-run", "sub_dry_run", default=None,
              help="Log the topics that *would* sync, no network calls")
@pass_ctx
def cmd_serve_scheduler(ctx: CLIContext, hour: int, minute: int, day_of_week: str,
                        lookback: int, sub_dry_run: bool | None) -> None:
    """Run APScheduler in-process, blocking on a 17:30 weekday cron trigger."""
    dry = bool(sub_dry_run) if sub_dry_run is not None else ctx.dry_run
    cfg = ScheduleConfig(
        hour=hour, minute=minute, day_of_week=day_of_week,
        lookback_days=lookback, dry_run=dry,
    )
    if ctx.json_output:
        # In JSON mode we print the config and exit without blocking.
        _emit(ctx, "serve-scheduler", {
            "would_block": True, "config": cfg.__dict__,
        })
        return
    serve_forever(cfg)


# ---------------------------------------------------------------------------
# 9) list-tables — DoD 1 (1)
# ---------------------------------------------------------------------------
@main.command("list-tables")
@pass_ctx
def cmd_list_tables(ctx: CLIContext) -> None:
    """List all configured raw_* tables with row count, date range, and cursor."""
    rows = cli_support.list_tables()
    _emit(ctx, "list-tables", {"tables": rows})


# ---------------------------------------------------------------------------
# 10) list-views — DoD 1 (2)
# ---------------------------------------------------------------------------
@main.command("list-views")
@pass_ctx
def cmd_list_views(ctx: CLIContext) -> None:
    """List all configured mv_* views with row count."""
    rows = cli_support.list_views()
    _emit(ctx, "list-views", {"views": rows})


# ---------------------------------------------------------------------------
# 11) list-sources — DoD 1 (3)
# ---------------------------------------------------------------------------
@main.command("list-sources")
@pass_ctx
def cmd_list_sources(ctx: CLIContext) -> None:
    """List registered data sources (tushare / akshare / future)."""
    rows = cli_support.list_sources()
    _emit(ctx, "list-sources", {"sources": rows})


# ---------------------------------------------------------------------------
# 12) status — DoD 1 (6)
# ---------------------------------------------------------------------------
@main.command("status")
@pass_ctx
def cmd_status(ctx: CLIContext) -> None:
    """Health check: DATA_DIR, disk, cursors, rate limit, last sync time."""
    snap = cli_support.status()
    _emit(ctx, "status", snap)


# ---------------------------------------------------------------------------
# 13) diff --against — DoD 1 (7)
# ---------------------------------------------------------------------------
@main.command("diff")
@click.option("--against", "against", required=True,
              help="Other DATA_DIR to compare against (parquet tree)")
@pass_ctx
def cmd_diff(ctx: CLIContext, against: str) -> None:
    """Row count + SHA256 comparison against another DATA_DIR."""
    payload, code = cli_support.diff_against(Path(against))
    if "error" in payload:
        # The diff helper signals a hard failure (e.g. missing root) by
        # putting the reason in ``payload["error"]``; surface it at the
        # envelope level so scripts can grep for it.
        _emit(ctx, "diff", payload, exit_code=code, error=payload["error"])
    else:
        _emit(ctx, "diff", payload, exit_code=code,
              error="row count mismatch" if code == 5 else None)
    _finalize(ctx, code)


# ---------------------------------------------------------------------------
# 14) query --sql — DoD 1 (8)
# ---------------------------------------------------------------------------
@main.command("query")
@click.option("--sql", "sql", required=True, help="Read-only SQL against mv_*/raw_*")
@pass_ctx
def cmd_query(ctx: CLIContext, sql: str) -> None:
    """Convenient read-only SQL (parametrized; DDL/DML blocked)."""
    try:
        result = cli_support.run_query(sql)
    except cli_support.QueryForbidden as qf:
        _emit(ctx, "query", {"sql": sql}, exit_code=1, error=str(qf))
        _finalize(ctx, 1)
        return
    except Exception as e:  # noqa: BLE001
        _emit(ctx, "query", {"sql": sql}, exit_code=1, error=str(e))
        _finalize(ctx, 1)
        return
    _emit(ctx, "query", result)


# ---------------------------------------------------------------------------
# 15) doctor — DoD 1 (9)
# ---------------------------------------------------------------------------
@main.command("doctor")
@pass_ctx
def cmd_doctor(ctx: CLIContext) -> None:
    """One-click self-check: env / token / DATA_DIR / views / cursors / disk."""
    payload, code = cli_support.doctor()
    _emit(ctx, "doctor", payload, exit_code=code,
          error="one or more checks failed" if code != 0 else None)
    _finalize(ctx, code)


# ---------------------------------------------------------------------------
# 16) completion {bash,zsh} — DoD 6
# ---------------------------------------------------------------------------
@main.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
@pass_ctx
def cmd_completion(ctx: CLIContext, shell: str) -> None:
    """Generate shell completion script (bash | zsh | fish)."""
    from click.shell_completion import get_completion_class

    cls = get_completion_class(shell)
    if cls is None:
        _emit(ctx, "completion", {"shell": shell}, exit_code=1,
              error=f"unsupported shell {shell!r}")
        _finalize(ctx, 1)
        return
    # Click 8.x: the class carries a ``source_template`` with %(name)s
    # placeholders. Substitute them; the result is a static script that
    # calls back into the CLI at completion time.
    prog = "python -m quant_data.cli"
    complete_var = f"_QUANT_DATA_COMPLETE"
    complete_func = "_quant_data_completion"
    script_body = cls.source_template % {
        "complete_func": complete_func,
        "complete_var": complete_var,
        "prog_name": prog,
    }
    banner = [
        f"# quant_data CLI completion ({shell})",
        f"# Source this file from your {shell}rc:",
        f"#   eval \"$({prog} completion {shell})\"",
    ]
    click.echo("\n".join(banner) + "\n" + script_body)


# ---------------------------------------------------------------------------
# Patch the helper module to expose TOPIC_META as a list
# (cli.py uses it for --dry-run echo; keeps the source of truth in support).
# ---------------------------------------------------------------------------
cli_support.TOPIC_META_LIST = sorted(cli_support.TOPIC_META)


if __name__ == "__main__":
    main()
