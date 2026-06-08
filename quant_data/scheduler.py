"""APScheduler-based 17:30 weekday incremental sync (v0.5 §4.4 / §8 Week 2).

Why APScheduler (over cron / launchd) for the in-process loop
------------------------------------------------------------
- One process, one set of imports, one rate-limit bucket — no thundering
  herd across multiple ``python -m quant_data.cli`` invocations.
- ``BlockingScheduler`` plays nicely with launchd ``KeepAlive=false``
  + Python ``atexit`` — launchd restarts the process on crash.
- ``dry_run`` is a first-class mode so CI / smoke tests can verify the
  5 tables are scheduled without actually hitting tushare.

The scheduler NEVER calls tushare on its own — it delegates to
``quant_data.sync.driver`` so resume / cursor / lineage semantics are
identical to manual ``make sync-daily``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Sequence

log = logging.getLogger("quant_data.scheduler")


# ---------------------------------------------------------------------------
# Order matters: stock_basic + trade_cal are cheap & must run first so the
# downstream tables see a fresh universe + trading-day calendar. The other
# three are time-series by trade_date; they share the same fetchFn shape.
# ---------------------------------------------------------------------------
DEFAULT_JOBS: list[dict[str, Any]] = [
    {"topic": "stock_basic", "fn_name": "sync_stock_basic", "kwargs": {}},
    {"topic": "trade_cal",   "fn_name": "sync_trade_cal",   "kwargs": {}},
    {"topic": "daily",       "fn_name": "sync_daily",       "kwargs": {}},
    {"topic": "adj_factor",  "fn_name": "sync_adj_factor",  "kwargs": {}},
    {"topic": "daily_basic", "fn_name": "sync_daily_basic", "kwargs": {}},
]


@dataclass
class JobResult:
    topic: str
    ok: bool
    report: dict[str, Any] | None = None
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class ScheduleConfig:
    """All knobs the scheduler respects.

    Default trigger is 17:30 Asia/Shanghai on weekdays (Mon-Fri).
    """
    hour: int = 17
    minute: int = 30
    day_of_week: str = "mon-fri"     # APScheduler expression
    timezone: str = "Asia/Shanghai"
    # lookback_days for incremental tables — covers late-arriving rows.
    lookback_days: int = 1
    dry_run: bool = False
    # If set, ignore the 5 default jobs and run only this subset (topic names).
    only: Sequence[str] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_once(*, lookback_days: int = 1, dry_run: bool = False,
             only: Sequence[str] = ()) -> list[JobResult]:
    """Execute the standard 5-table sweep exactly once.

    Used by:
      - ``python -m quant_data.cli run-once`` (manual cron / launchd)
      - ``python -m quant_data.cli serve-scheduler`` (in-process APScheduler)
    """
    import time

    from quant_data.sync import driver as _drv

    results: list[JobResult] = []
    end = date.today()
    start = end - timedelta(days=lookback_days)
    jobs = [j for j in DEFAULT_JOBS if not only or j["topic"] in only]

    for j in jobs:
        t0 = time.monotonic()
        topic = j["topic"]
        fn = getattr(_drv, j["fn_name"], None)
        if fn is None:
            log.error("scheduler.run_once: missing driver fn %s", j["fn_name"])
            results.append(JobResult(topic=topic, ok=False, error="missing_fn"))
            continue
        try:
            if dry_run:
                # No network, no fetch. Just record the topic that *would* run.
                log.info("[dry-run] %s: would sync (start=%s end=%s)",
                         topic, start, end)
                report = {
                    "topic": topic, "dry_run": True,
                    "start": start.isoformat(), "end": end.isoformat(),
                }
                results.append(JobResult(topic=topic, ok=True, report=report,
                                         duration_s=time.monotonic() - t0))
            else:
                if topic in ("stock_basic",):
                    report = fn()
                elif topic == "trade_cal":
                    report = fn(start=start, end=end)
                else:
                    report = fn(start_date=start, end_date=end)
                log.info("%s -> %s", topic, report)
                results.append(JobResult(topic=topic, ok=True, report=report,
                                         duration_s=time.monotonic() - t0))
        except Exception as e:  # noqa: BLE001
            log.exception("scheduler.run_once: %s failed: %s", topic, e)
            results.append(JobResult(topic=topic, ok=False, error=str(e),
                                     duration_s=time.monotonic() - t0))
    return results


def build_scheduler(cfg: ScheduleConfig | None = None,
                    job_fn: Callable[[], list[JobResult]] | None = None
                    ) -> "apscheduler.schedulers.blocking.BlockingScheduler":
    """Construct (but do NOT start) an APScheduler ``BlockingScheduler``.

    ``job_fn`` is the callable to run on every trigger. Defaults to
    ``run_once(lookback_days=cfg.lookback_days, dry_run=cfg.dry_run,
    only=cfg.only)`` — pluggable so tests can swap in a mock.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    cfg = cfg or ScheduleConfig()
    fn = job_fn or (lambda: run_once(lookback_days=cfg.lookback_days,
                                     dry_run=cfg.dry_run, only=cfg.only))

    sched = BlockingScheduler(timezone=cfg.timezone)
    sched.add_job(
        fn,
        CronTrigger(
            hour=cfg.hour, minute=cfg.minute,
            day_of_week=cfg.day_of_week, timezone=cfg.timezone,
        ),
        id="quant_data_evening_sync",
        name="quant_data 17:30 weekday incremental sync",
        max_instances=1,
        coalesce=True,        # if we missed a window, run once, not 5×
        misfire_grace_time=600,  # 10 min — relaunch, not catch up
    )
    return sched


def serve_forever(cfg: ScheduleConfig | None = None) -> None:
    """Block on the scheduler. Ctrl-C / SIGTERM cleanly shuts it down."""
    sched = build_scheduler(cfg)
    log.info("scheduler starting: %02d:%02d %s (%s)",
             cfg.hour if cfg else 17, cfg.minute if cfg else 30,
             (cfg.day_of_week if cfg else "mon-fri"),
             (cfg.timezone if cfg else "Asia/Shanghai"))
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler: shutdown signal received")
        sched.shutdown(wait=False)


__all__ = [
    "DEFAULT_JOBS", "JobResult", "ScheduleConfig",
    "run_once", "build_scheduler", "serve_forever",
]
