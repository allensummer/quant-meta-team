"""Tiny CLI: ``python -m quant_data.cli {init,sync-full,sync-daily,run-once,serve-scheduler,report}``."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import click

from quant_data.logging_setup import setup_logging
from quant_data.paths import data_dir, duckdb_path, sqlite_path
from quant_data.scheduler import ScheduleConfig, run_once, serve_forever
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.store.meta_sqlite import MetaSQLite
from quant_data.sync import (
    sync_daily, sync_adj_factor, sync_daily_basic, sync_full, sync_stock_basic, sync_trade_cal,
)


def _disk_size_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


@click.group()
def main() -> None:
    setup_logging()


@main.command("init")
def cmd_init() -> None:
    """Create data dir, register schemas, bootstrap DuckDB views."""
    setup_logging()
    log = logging.getLogger("quant_data.cli")
    dd = data_dir()
    log.info("data_dir = %s", dd)
    log.info("duckdb  = %s", duckdb_path())
    log.info("sqlite  = %s", sqlite_path())
    db = DuckDBStore()
    db.bootstrap_views()
    log.info("views bootstrapped: %s", ", ".join(
        r[0] for r in db.con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
        ).fetchall()
    ))
    click.echo(f"data_dir={dd}")
    click.echo(f"duckdb={duckdb_path()}")


@main.command("sync-full")
def cmd_sync_full() -> None:
    """Backfill 5 tushare tables for the full A-share history."""
    setup_logging()
    log = logging.getLogger("quant_data.cli")
    reports = sync_full()
    click.echo(json.dumps(reports, indent=2, ensure_ascii=False))
    log.info("sync_full complete: %s", reports)


@main.command("sync-daily")
@click.option("--lookback", default=5, help="Days to look back from today")
def cmd_sync_daily(lookback: int) -> None:
    """Incremental daily sync (today - lookback days)."""
    setup_logging()
    end = date.today()
    start = end - timedelta(days=lookback)
    out = []
    for fn in (sync_stock_basic,
               lambda: sync_trade_cal(start=start, end=end),
               lambda: sync_daily(start_date=start, end_date=end),
               lambda: sync_adj_factor(start_date=start, end_date=end),
               lambda: sync_daily_basic(start_date=start, end_date=end)):
        out.append(fn())
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@main.command("report")
def cmd_report() -> None:
    """Print row counts + cursor state + lineage + disk usage."""
    setup_logging()
    db = DuckDBStore()
    meta = MetaSQLite()
    log = logging.getLogger("quant_data.cli")

    # row counts: rely on the backing Parquet
    def _count(topic: str) -> int:
        root = data_dir() / f"raw_tushare_{topic}"
        if not root.exists():
            return 0
        try:
            return int(db.con.execute(
                f"SELECT count(*) FROM read_parquet('{root}/**/*.parquet')"
            ).fetchone()[0])
        except Exception as e:
            log.debug("count %s failed: %s", topic, e)
            return 0

    rows = {
        "raw_tushare_stock_basic": _count("stock_basic"),
        "raw_tushare_trade_cal":   _count("trade_cal"),
        "raw_tushare_daily":       _count("daily"),
        "raw_tushare_adj_factor":  _count("adj_factor"),
        "raw_tushare_daily_basic": _count("daily_basic"),
    }

    # view counts
    view_rows: dict[str, int] = {}
    for v in ("mv_daily_v1", "mv_daily_qfq", "mv_daily_hfq", "mv_trade_cal"):
        try:
            view_rows[v] = int(db.con.execute(f"SELECT count(*) FROM {v}").fetchone()[0])
        except Exception as e:
            view_rows[v] = f"err: {e}"

    cursors = meta.all_cursors()
    disk_bytes = _disk_size_bytes(data_dir())
    out = {
        "data_dir": str(data_dir()),
        "disk_bytes": disk_bytes,
        "raw_rows": rows,
        "view_rows": view_rows,
        "cursors": cursors,
    }
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Scheduler commands (v0.5 §4.4 / §8 Week 2)
# ---------------------------------------------------------------------------
@main.command("run-once")
@click.option("--lookback", default=1, help="Days to look back from today for time-series tables")
@click.option("--dry-run/--no-dry-run", default=False,
              help="Log the topics that *would* sync, no network calls")
@click.option("--only", default="",
              help="Comma-separated topic subset (default: all 5)")
def cmd_run_once(lookback: int, dry_run: bool, only: str) -> None:
    """Run a single 5-table sweep — used by launchd / manual cron."""
    setup_logging()
    only_t = tuple(t.strip() for t in only.split(",") if t.strip())
    results = run_once(lookback_days=lookback, dry_run=dry_run, only=only_t)
    payload = [
        {"topic": r.topic, "ok": r.ok, "duration_s": round(r.duration_s, 2),
         "report": r.report, "error": r.error}
        for r in results
    ]
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    if not all(r.ok for r in results):
        # Non-zero exit so launchd / monitoring sees the failure.
        raise SystemExit(2)


@main.command("serve-scheduler")
@click.option("--hour", default=17, help="Trigger hour (24h, Asia/Shanghai)")
@click.option("--minute", default=30, help="Trigger minute")
@click.option("--day-of-week", default="mon-fri",
              help="APScheduler day-of-week expression")
@click.option("--lookback", default=1, help="Days to look back for time-series tables")
@click.option("--dry-run/--no-dry-run", default=False,
              help="Log the topics that *would* sync, no network calls")
def cmd_serve_scheduler(hour: int, minute: int, day_of_week: str,
                        lookback: int, dry_run: bool) -> None:
    """Run APScheduler in-process, blocking on a 17:30 weekday cron trigger."""
    setup_logging()
    cfg = ScheduleConfig(
        hour=hour, minute=minute, day_of_week=day_of_week,
        lookback_days=lookback, dry_run=dry_run,
    )
    serve_forever(cfg)


if __name__ == "__main__":
    main()
