"""SQLite metadata: ``sync_state`` table + ``meta/_lineage/<table>/<date>.json`` files.

Two small primitives that together give us idempotent resumable syncs and
a queryable audit trail (v0.4 §4.2 + §6.4).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column, Date, DateTime, String, Text, create_engine, select,
)
from sqlalchemy.orm import declarative_base, Session

from quant_data.paths import meta_dir, sqlite_path
from quant_data.sources.base import LineageRecord

log = logging.getLogger("quant_data.store.meta")

Base = declarative_base()


class SyncStateRow(Base):
    __tablename__ = "sync_state"
    table = Column(String, primary_key=True)
    last_trade_date = Column(Date)
    first_trade_date = Column(Date)  # set by 20-year backfill (v0.8); None = legacy
    last_run_at = Column(DateTime)
    status = Column(String, default="ok")
    error_msg = Column(Text, default="")


class MetaSQLite:
    """Thin wrapper over the shared SQLite file used for sync_state + lineage pointers."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else sqlite_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}", future=True)
        Base.metadata.create_all(self.engine)
        self._migrate_add_first_trade_date()

    def _migrate_add_first_trade_date(self) -> None:
        """One-shot migration: add first_trade_date to existing sync_state tables.

        SQLAlchemy's ``create_all`` only creates missing tables, it does NOT
        add new columns to existing tables. The 20-year backfill (v0.8) added
        ``first_trade_date`` to the schema, so any pre-existing ``sync.sqlite``
        (with the v0.4/v0.5/v0.6/v0.7 schema) needs this column added on
        first load. Idempotent: the column is added only if it doesn't exist.
        """
        from sqlalchemy import text, inspect
        insp = inspect(self.engine)
        if "sync_state" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("sync_state")}
        if "first_trade_date" in cols:
            return
        with self.engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE sync_state ADD COLUMN first_trade_date DATE"
            ))
        log.info("meta: migrated sync_state — added first_trade_date column")

    # ---------------- sync_state ----------------
    def get_cursor(self, table: str) -> date | None:
        with Session(self.engine) as s:
            row = s.execute(select(SyncStateRow).where(SyncStateRow.table == table)).scalar_one_or_none()
            return row.last_trade_date if row else None

    def set_cursor(self, table: str, d: date, status: str = "ok", error: str = "",
                   first_trade_date: date | None = None) -> None:
        """Update the cursor row.

        ``first_trade_date`` is sticky: once set by a backfill, subsequent
        incremental syncs must NOT clobber it. Pass ``first_trade_date`` only
        when the caller wants to back-fill the lower bound (e.g. 20y backfill).
        """
        with Session(self.engine) as s:
            row = s.get(SyncStateRow, table)
            if row is None:
                row = SyncStateRow(table=table, last_trade_date=d,
                                   first_trade_date=first_trade_date,
                                   last_run_at=datetime.now(), status=status,
                                   error_msg=error)
                s.add(row)
            else:
                row.last_trade_date = d
                # ``first_trade_date`` is sticky: only set it on the first call
                # that supplies a non-None value, never on later incremental
                # updates. This prevents the cursor "floor" from drifting up.
                if first_trade_date is not None and row.first_trade_date is None:
                    row.first_trade_date = first_trade_date
                row.last_run_at = datetime.now()
                row.status = status
                row.error_msg = error
            s.commit()

    def all_cursors(self) -> dict[str, dict[str, Any]]:
        with Session(self.engine) as s:
            rows = s.execute(select(SyncStateRow)).scalars().all()
            return {
                r.table: {
                    "last_trade_date": r.last_trade_date.isoformat() if r.last_trade_date else None,
                    "first_trade_date": r.first_trade_date.isoformat() if r.first_trade_date else None,
                    "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
                    "status": r.status,
                    "error_msg": r.error_msg,
                }
                for r in rows
            }

    # ---------------- lineage ----------------
    def write_lineage(self, rec: LineageRecord) -> Path:
        d = meta_dir() / "_lineage" / rec.table
        d.mkdir(parents=True, exist_ok=True)
        # 1 lineage per (table, fetched_at) — daily-batched files would collide, so use
        # the trade_date from params if present, else the timestamp suffix.
        trade_date = rec.params.get("trade_date")
        if trade_date:
            fname = f"{trade_date}.json"
        else:
            fname = f"{rec.fetched_at.strftime('%Y%m%dT%H%M%S')}-{rec.request_id[:8]}.json"
        path = d / fname
        path.write_text(json.dumps(rec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def recent_lineage(self, table: str, limit: int = 5) -> list[dict[str, Any]]:
        d = meta_dir() / "_lineage" / table
        if not d.exists():
            return []
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        out = []
        for f in files:
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return out
