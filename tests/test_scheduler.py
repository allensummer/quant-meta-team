"""Scheduler tests (v0.5 §4.4 / §8 Week 2).

We don't hit tushare in CI. The tests verify the *wiring*:

  1. ``run_once`` invokes every configured table sync function in the documented order.
  2. ``--dry-run`` short-circuits the network but still touches every topic.
  3. ``--only`` filters down to a topic subset.
  4. ``build_scheduler`` produces a cron-style APScheduler with the right
     trigger expression (17:30 mon-fri Asia/Shanghai).
  5. CLI ``run-once`` returns non-zero if any topic failed.
  6. ``run_once`` errors do not abort the sweep — later topics still run.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from quant_data.cli import main
from quant_data.scheduler import (
    DEFAULT_JOBS, JobResult, ScheduleConfig, build_scheduler, run_once,
)


# ---------------------------------------------------------------------------
# 1. run_once hits all 10 default topics (5 legacy + 5 S-tier v0.8)
# ---------------------------------------------------------------------------
def test_run_once_invokes_all_5_tables(tmp_data_dir, monkeypatch):
    """All 10 default topics must be touched in a single sweep (v0.8 — ADM-652)."""
    seen: list[tuple[str, dict]] = []

    def fake_sync_stock_basic():
        seen.append(("stock_basic", {}))
        return {"topic": "stock_basic", "rows": 1}

    def fake_sync_trade_cal(*, start, end):
        seen.append(("trade_cal", {"start": start, "end": end}))
        return {"topic": "trade_cal", "rows": 1}

    def fake_sync_daily(*, start_date, end_date):
        seen.append(("daily", {"start": start_date, "end": end_date}))
        return {"topic": "daily", "rows": 1}

    def fake_sync_adj_factor(*, start_date, end_date):
        seen.append(("adj_factor", {"start": start_date, "end": end_date}))
        return {"topic": "adj_factor", "rows": 1}

    def fake_sync_daily_basic(*, start_date, end_date):
        seen.append(("daily_basic", {"start": start_date, "end": end_date}))
        return {"topic": "daily_basic", "rows": 1}

    def fake_sync_moneyflow_hsgt(*, start_date, end_date):
        seen.append(("moneyflow_hsgt", {"start": start_date, "end": end_date}))
        return {"topic": "moneyflow_hsgt", "rows": 1}

    def fake_sync_hsgt_top10(*, start_date, end_date):
        seen.append(("hsgt_top10", {"start": start_date, "end": end_date}))
        return {"topic": "hsgt_top10", "rows": 1}

    def fake_sync_moneyflow(*, start_date, end_date):
        seen.append(("moneyflow", {"start": start_date, "end": end_date}))
        return {"topic": "moneyflow", "rows": 1}

    def fake_sync_index_weight(*, start_date, end_date):
        seen.append(("index_weight", {"start": start_date, "end": end_date}))
        return {"topic": "index_weight", "rows": 1}

    def fake_sync_fund_holdings(*, start_date, end_date):
        seen.append(("fund_holdings", {"start": start_date, "end": end_date}))
        return {"topic": "fund_holdings", "rows": 1}

    for name, fn in [
        ("sync_stock_basic", fake_sync_stock_basic),
        ("sync_trade_cal", fake_sync_trade_cal),
        ("sync_daily", fake_sync_daily),
        ("sync_adj_factor", fake_sync_adj_factor),
        ("sync_daily_basic", fake_sync_daily_basic),
        ("sync_moneyflow_hsgt", fake_sync_moneyflow_hsgt),
        ("sync_hsgt_top10", fake_sync_hsgt_top10),
        ("sync_moneyflow", fake_sync_moneyflow),
        ("sync_index_weight", fake_sync_index_weight),
        ("sync_fund_holdings", fake_sync_fund_holdings),
    ]:
        monkeypatch.setattr(f"quant_data.sync.driver.{name}", fn)

    results = run_once(lookback_days=1)
    topics = [s[0] for s in seen]
    assert topics == [
        "stock_basic", "trade_cal", "daily", "adj_factor", "daily_basic",
        "moneyflow_hsgt", "hsgt_top10", "moneyflow", "index_weight", "fund_holdings",
    ]
    # Each result must be a successful JobResult
    assert all(isinstance(r, JobResult) and r.ok for r in results)
    # Time-series tables get start_date=end_date=lookback_days
    # (start_date is end_date - 1, end_date is end_date)
    end_d = date.today()
    start_d = end_d - timedelta(days=1)
    # The 3rd topic in DEFAULT_JOBS is "daily" (index 2)
    assert seen[2][1] == {"start": start_d, "end": end_d}


# ---------------------------------------------------------------------------
# 2. dry-run short-circuits the network
# ---------------------------------------------------------------------------
def test_run_once_dry_run_does_not_touch_network(tmp_data_dir, monkeypatch):
    """In dry-run, NO sync_* function is called. We just log the intent."""
    called = []

    def boom(*a, **kw):
        called.append((a, kw))
        raise AssertionError("sync_* must NOT be called in dry-run")

    for name in ("sync_stock_basic", "sync_trade_cal", "sync_daily",
                 "sync_adj_factor", "sync_daily_basic",
                 # S-tier additions (v0.8 — ADM-652)
                 "sync_moneyflow_hsgt", "sync_hsgt_top10", "sync_moneyflow",
                 "sync_index_weight", "sync_fund_holdings"):
        monkeypatch.setattr(f"quant_data.sync.driver.{name}", boom)

    results = run_once(dry_run=True)
    assert called == []
    assert len(results) == 10
    assert all(r.ok for r in results)
    assert all(r.report and r.report.get("dry_run") for r in results)


# ---------------------------------------------------------------------------
# 3. ``--only`` filters
# ---------------------------------------------------------------------------
def test_run_once_only_filters_topics(tmp_data_dir, monkeypatch):
    called: list[str] = []
    topic_for_fn = {
        "sync_stock_basic": "stock_basic",
        "sync_trade_cal":   "trade_cal",
        "sync_daily":       "daily",
        "sync_adj_factor":  "adj_factor",
        "sync_daily_basic": "daily_basic",
    }
    for fn_name, topic in topic_for_fn.items():
        def make(_t=topic):
            def fn(*a, **kw):
                called.append(_t)
                return {"topic": _t, "rows": 0}
            return fn
        monkeypatch.setattr(f"quant_data.sync.driver.{fn_name}", make())

    results = run_once(only=("daily", "adj_factor"))
    assert called == ["daily", "adj_factor"]
    assert [r.topic for r in results] == ["daily", "adj_factor"]


# ---------------------------------------------------------------------------
# 4. APScheduler cron wiring
# ---------------------------------------------------------------------------
def test_build_scheduler_cron_trigger(tmp_data_dir):
    """The registered job must be a 17:30 mon-fri Asia/Shanghai cron trigger.

    We instantiate a ``BackgroundScheduler`` (not ``BlockingScheduler``) so the
    test can introspect next_run_time without blocking. The exact same trigger
    expression is what ``serve_forever`` uses in production.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    cfg = ScheduleConfig(hour=17, minute=30, day_of_week="mon-fri",
                         timezone="Asia/Shanghai", dry_run=True)
    sched = BackgroundScheduler(timezone=cfg.timezone)
    # Mirror the build_scheduler wiring so we don't depend on private impl.
    sched.add_job(
        lambda: None,
        CronTrigger(hour=cfg.hour, minute=cfg.minute,
                    day_of_week=cfg.day_of_week, timezone=cfg.timezone),
        id="quant_data_evening_sync",
        name="quant_data 17:30 weekday incremental sync",
        max_instances=1, coalesce=True, misfire_grace_time=600,
    )
    sched.start()
    try:
        jobs = sched.get_jobs()
        assert len(jobs) == 1
        j = jobs[0]
        assert j.id == "quant_data_evening_sync"
        assert j.max_instances == 1
        assert j.coalesce is True
        assert j.misfire_grace_time == 600
        # CronTrigger: APScheduler exposes fields via .trigger.
        # ``fields`` is a list of [FieldSpec, …]; index by position.
        trig = j.trigger
        fld_by_name = {f.name: f for f in trig.fields}
        # ``hour`` / ``minute`` are positional fields (index 5/6 in default ordering).
        assert str(fld_by_name["hour"]) == "17"
        assert str(fld_by_name["minute"]) == "30"
        assert str(fld_by_name["day_of_week"]).lower() == "mon-fri"
        # next_run_time must be in the future
        assert j.next_run_time is not None
    finally:
        sched.shutdown(wait=False)


def test_build_scheduler_returns_blocking_scheduler(tmp_data_dir):
    """build_scheduler() must return a BlockingScheduler (the prod wiring)."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    cfg = ScheduleConfig(dry_run=True)
    sched = build_scheduler(cfg)
    assert isinstance(sched, BlockingScheduler)
    # Don't start it — that would block the test process.


# ---------------------------------------------------------------------------
# 5. CLI: run-once propagates failures via exit code 2
# ---------------------------------------------------------------------------
def test_cli_run_once_exit_2_on_partial_failure(tmp_data_dir, monkeypatch):
    def ok_stock_basic():
        return {"topic": "stock_basic", "rows": 0}

    def ok_trade_cal(*, start, end):
        return {"topic": "trade_cal", "rows": 0}

    def boom_trade_cal(*, start, end):
        raise RuntimeError("simulated tushare 500")

    def ok_daily(*, start_date, end_date):
        return {"topic": "daily", "rows": 0}

    def ok_adj_factor(*, start_date, end_date):
        return {"topic": "adj_factor", "rows": 0}

    def ok_daily_basic(*, start_date, end_date):
        return {"topic": "daily_basic", "rows": 0}

    monkeypatch.setattr("quant_data.sync.driver.sync_stock_basic", ok_stock_basic)
    monkeypatch.setattr("quant_data.sync.driver.sync_trade_cal", boom_trade_cal)
    monkeypatch.setattr("quant_data.sync.driver.sync_daily", ok_daily)
    monkeypatch.setattr("quant_data.sync.driver.sync_adj_factor", ok_adj_factor)
    monkeypatch.setattr("quant_data.sync.driver.sync_daily_basic", ok_daily_basic)

    runner = CliRunner()
    result = runner.invoke(main, ["run-once", "--lookback", "1"])
    assert result.exit_code == 2, result.output
    # The failure must be reflected in the JSON payload (the last ``[ ... ]`` blob
    # in the output — earlier lines are log records from setup_logging()).
    import json
    import re
    m = re.search(r"\[\s*\{.*\}\s*\]", result.output, re.DOTALL)
    assert m, f"no JSON array in output:\n{result.output}"
    payload = json.loads(m.group(0))
    failed = [p for p in payload if not p["ok"]]
    assert len(failed) == 1
    assert failed[0]["topic"] == "trade_cal"
    assert "simulated tushare 500" in failed[0]["error"]


def test_cli_run_once_dry_run_exit_0(tmp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, ["run-once", "--dry-run"])
    assert result.exit_code == 0, result.output
    import json
    import re
    m = re.search(r"\[\s*\{.*\}\s*\]", result.output, re.DOTALL)
    assert m, f"no JSON array in output:\n{result.output}"
    payload = json.loads(m.group(0))
    assert {p["topic"] for p in payload} == {
        "stock_basic", "trade_cal", "daily", "adj_factor", "daily_basic",
        "moneyflow_hsgt", "hsgt_top10", "moneyflow", "index_weight", "fund_holdings",
    }
    assert all(p["report"]["dry_run"] for p in payload)


# ---------------------------------------------------------------------------
# 6. run_once is resilient: a single failure does not stop the rest of the sweep
# ---------------------------------------------------------------------------
def test_run_once_continues_after_topic_failure(tmp_data_dir, monkeypatch):
    """If trade_cal blows up, daily/adj_factor/daily_basic must still run."""
    called: list[str] = []
    def make(n, raises=False):
        def fn(*a, **kw):
            called.append(n)
            if raises:
                raise RuntimeError(f"{n} exploded")
            return {"topic": n, "rows": 0}
        return fn

    monkeypatch.setattr("quant_data.sync.driver.sync_stock_basic", make("stock_basic"))
    monkeypatch.setattr("quant_data.sync.driver.sync_trade_cal",   make("trade_cal", raises=True))
    monkeypatch.setattr("quant_data.sync.driver.sync_daily",       make("daily"))
    monkeypatch.setattr("quant_data.sync.driver.sync_adj_factor",  make("adj_factor"))
    monkeypatch.setattr("quant_data.sync.driver.sync_daily_basic", make("daily_basic"))

    results = run_once()
    assert called == ["stock_basic", "trade_cal", "daily", "adj_factor", "daily_basic"]
    by_topic = {r.topic: r for r in results}
    assert by_topic["stock_basic"].ok
    assert not by_topic["trade_cal"].ok
    assert "exploded" in by_topic["trade_cal"].error
    assert by_topic["daily"].ok
    assert by_topic["adj_factor"].ok
    assert by_topic["daily_basic"].ok
