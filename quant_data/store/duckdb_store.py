"""DuckDB-backed query engine (v0.4 §2 + §6.3).

Owns the single ``quant.duckdb`` file. Materializes ``mv_*`` views over the
Parquet files written by ``ParquetStore``.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import duckdb
import pandas as pd

from quant_data.paths import duckdb_path
from quant_data.schemas import SCHEMAS, TableSchema
from quant_data.store.parquet_store import ParquetStore

log = logging.getLogger("quant_data.store.duckdb")


def _view_sql_path(view_name: str) -> Path:
    from quant_data.paths import data_dir
    return data_dir() / "views_runtime" / f"{view_name}.sql"


class DuckDBStore:
    """Single-file DuckDB database; idempotent view bootstrap."""

    def __init__(self, path: Path | None = None, *, read_only: bool = False):
        """Open (or create) the DuckDB file.

        Parameters
        ----------
        path : Path, optional
            Defaults to :func:`quant_data.paths.duckdb_path`.
        read_only : bool, default False
            When True, the file is opened in DuckDB's native read-only mode
            (multiple readers can attach the same file concurrently; writes
            raise ``InvalidInputException``). Portfolio / Risk agents should
            pass ``read_only=True`` so they never block the Data agent's
            writes and vice versa. Default behavior (read-write) is
            unchanged for the Data agent.
        """
        self.path = Path(path) if path else duckdb_path()
        self.read_only = read_only
        if not read_only:
            # In RW mode, ensure the parent exists. In read-only mode we must
            # not touch the filesystem — opening an existing file must work
            # even if its parent dir is read-only.
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.path), read_only=read_only)
        if not read_only:
            # threads=4 is a writer-side default; readers inherit whatever
            # the writer set, so don't override.
            self.con.execute("PRAGMA threads=4")
        # All Parquet under data_dir
        self._conventions: dict[str, str] = {}  # topic -> glob

    # ---------------- DataStore protocol ----------------
    def register_schema(self, schema: TableSchema) -> None:
        SCHEMAS[(schema.table, schema.version)] = schema

    def register_parquet(self, topic: str, store: ParquetStore) -> None:
        self._conventions[topic] = store.glob_for_duckdb()

    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> pd.DataFrame:
        if params:
            return self.con.execute(sql, params).df()
        return self.con.execute(sql).df()

    def upsert(self, table: str, df: pd.DataFrame, schema_version: str) -> int:
        """Materialize a single-batch df into the per-source raw_* table backing the view."""
        if df is None or df.empty:
            return 0
        view = f"raw_{table}"  # expected pattern, e.g. raw_tushare_daily
        # ensure underlying table exists (in case open_store wasn't called).
        # Build it from a SELECT * on the batch itself so column count always
        # matches the batch — avoids mismatch with the bootstrap placeholder
        # which may have more columns than the user-supplied df.
        self.con.execute(
            f"CREATE TABLE IF NOT EXISTS {view} AS SELECT * FROM df WHERE 1=0"
        )
        # Use a temp table to make the per-key DELETE robust to type coercions
        # (date <-> string, etc.) which can silently fail in IN-subqueries.
        self.con.execute("CREATE OR REPLACE TEMP TABLE _upsert_batch AS SELECT * FROM df")
        if "trade_date" in df.columns and "ts_code" in df.columns:
            self.con.execute(
                f"DELETE FROM {view} WHERE (ts_code, trade_date) IN "
                f"(SELECT ts_code, trade_date FROM _upsert_batch)"
            )
        elif "cal_date" in df.columns and "exchange" in df.columns:
            self.con.execute(
                f"DELETE FROM {view} WHERE (exchange, cal_date) IN "
                f"(SELECT exchange, cal_date FROM _upsert_batch)"
            )
        elif "ts_code" in df.columns and "trade_date" not in df.columns:
            # stock_basic snapshot: drop & re-insert the entire universe
            self.con.execute(f"DELETE FROM {view}")
        # Insert by explicit column list to be robust against schema-mismatched
        # placeholders that may have a different column set.
        cols = ", ".join(df.columns)
        self.con.execute(f"INSERT INTO {view} ({cols}) SELECT {cols} FROM _upsert_batch")
        self.con.execute("DROP TABLE _upsert_batch")
        return len(df)

    def get_cursor(self, table: str) -> date | None:
        try:
            row = self.con.execute(
                'SELECT last_trade_date FROM sync_state WHERE "table" = ?', [table]
            ).fetchone()
        except duckdb.CatalogException:
            return None
        if row and row[0] is not None:
            try:
                return date.fromisoformat(str(row[0]))
            except ValueError:
                return None
        return None

    def set_cursor(self, table: str, d: date, status: str = "ok", error: str = "") -> None:
        self.con.execute(
            '''
            CREATE TABLE IF NOT EXISTS sync_state (
                "table" VARCHAR PRIMARY KEY,
                last_trade_date DATE,
                last_run_at TIMESTAMP,
                status VARCHAR,
                error_msg VARCHAR
            )
            '''
        )
        from datetime import datetime
        self.con.execute(
            '''
            INSERT INTO sync_state ("table", last_trade_date, last_run_at, status, error_msg)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT ("table") DO UPDATE SET
                last_trade_date = excluded.last_trade_date,
                last_run_at = excluded.last_run_at,
                status = excluded.status,
                error_msg = excluded.error_msg
            ''',
            [table, d, datetime.now(), status, error],
        )

    # ---------------- views ----------------
    def bootstrap_views(self) -> list[str]:
        """Create the canonical views (mv_daily_v1 / qfq / hfq / trade_cal).

        Reads SQL files from ``quant_data/views/`` (shipped with the package)
        and substitutes the data_dir glob for each source.

        To keep views queryable before any sync has run, we seed an empty
        placeholder Parquet file into each ``raw_tushare_<topic>`` tree. This
        means ``read_parquet(glob)`` always returns 0 rows rather than erroring
        with "No files found", which lets downstream tools (DBeaver, portfolio
        notebooks, the Risk agent) introspect the schema pre-sync.
        """
        from quant_data import views as _views_pkg
        view_dir = Path(_views_pkg.__file__).parent
        self._ensure_placeholder_files()
        created: list[str] = []
        for sql_path in sorted(view_dir.glob("*.sql")):
            sql = sql_path.read_text(encoding="utf-8")
            sql = self._inject_globals(sql)
            view_name = sql_path.stem  # e.g. mv_daily_v1
            self.con.execute(f"CREATE OR REPLACE VIEW {view_name} AS {sql}")
            log.info("duckdb: view %s ready", view_name)
            created.append(view_name)
        return created

    def _ensure_placeholder_files(self) -> None:
        """Write empty Parquet placeholders so read_parquet globs never fail.

        The placeholders use the *real* dtypes so the view SQL can do
        arithmetic (``close * adj_factor``) without binder errors.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq
        from quant_data.paths import data_dir
        # topic -> ordered list of (name, arrow_type)
        topics: dict[str, list[tuple[str, "pa.DataType"]]] = {
            "daily": [
                ("ts_code", pa.string()),
                ("trade_date", pa.date32()),
                ("open", pa.float64()),
                ("high", pa.float64()),
                ("low", pa.float64()),
                ("close", pa.float64()),
                ("pre_close", pa.float64()),
                ("change", pa.float64()),
                ("pct_chg", pa.float64()),
                ("vol", pa.float64()),
                ("amount", pa.float64()),
            ],
            "adj_factor": [
                ("ts_code", pa.string()),
                ("trade_date", pa.date32()),
                ("adj_factor", pa.float64()),
            ],
            "daily_basic": [
                ("ts_code", pa.string()),
                ("trade_date", pa.date32()),
                ("turnover_rate", pa.float64()),
                ("pe", pa.float64()),
                ("pb", pa.float64()),
                ("total_mv", pa.float64()),
                ("circ_mv", pa.float64()),
            ],
            "trade_cal": [
                ("exchange", pa.string()),
                ("cal_date", pa.date32()),
                ("is_open", pa.int8()),
                ("pretrade_date", pa.date32()),
            ],
            "stock_basic": [
                ("ts_code", pa.string()),
                ("symbol", pa.string()),
                ("name", pa.string()),
                ("industry", pa.string()),
                ("exchange", pa.string()),
                ("curr_type", pa.string()),
                ("list_status", pa.string()),
                ("list_date", pa.date32()),
            ],
        }
        for topic, cols in topics.items():
            d = data_dir() / f"raw_tushare_{topic}" / "_schema"
            d.mkdir(parents=True, exist_ok=True)
            p = d / "schema_marker.parquet"
            if not p.exists():
                tbl = pa.table({n: pa.array([], type=t) for n, t in cols})
                pq.write_table(tbl, str(p))

    def _inject_globals(self, sql: str) -> str:
        # Allow views to reference @daily_tushare@ / @adj_factor_tushare@ placeholders.
        import re
        from quant_data.paths import data_dir
        for topic in ("daily", "adj_factor", "daily_basic", "trade_cal", "stock_basic"):
            placeholder = f"@{topic}_tushare@"
            if placeholder in sql:
                glob = f"'{data_dir()}/raw_tushare_{topic}/**/*.parquet'"
                sql = sql.replace(placeholder, glob)
        return sql
