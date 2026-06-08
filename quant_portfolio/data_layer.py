"""High-level data access for the portfolio / factor layer (ADM-612).

Wraps ``quant_data.store.duckdb_store.DuckDBStore.query()`` into the
three calls the rest of the package actually uses:

  - ``get_universe(date)``                     → today's tradable stock pool
  - ``get_factor_universe(date, lookback)``    → universe + cross-section factors
  - ``get_calendar(start, end)``               → trading days

The data layer is the only place portfolio code touches the warehouse.
If you find yourself writing ``SELECT *`` somewhere else, route it
through this module so the SQL stays testable and the field-unit
translations (§5 of the v0.4 design doc) stay in one place.

Field-unit contract (mirrors ``docs/data-localization.md`` v0.4 §5):
  - ``vol``   : 手 (1 手 = 100 股). Helper ``vol_to_shares()`` converts.
  - ``amount``: 千元 (tushare native). Helper ``amount_to_yuan()`` converts.
  - ``close_qfq`` = ``close * adj_factor / latest_adj_factor`` (per stock).
  - 停牌判定: ``high = low AND vol = 0`` (§5 最后一段).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from quant_data.paths import data_dir
from quant_data.store.duckdb_store import DuckDBStore

# ---------- field-unit constants (v0.4 §5) ----------
SHARES_PER_LOT = 100          # vol is in lots
YUAN_PER_KILOYUAN = 1_000.0   # amount is in kilo-yuan (千元)
SUSPEND_HIGH_LOW_TOL = 0.0     # v0.4 §5: high == low exactly
SUSPEND_VOL_TOL = 0.0          # v0.4 §5: vol == 0 exactly


# ---------- helpers (unit conversions) ----------
def vol_to_shares(vol_lots: float | pd.Series) -> float | pd.Series:
    """Convert tushare-native 手 → 股. 1 手 = 100 股."""
    return vol_lots * SHARES_PER_LOT


def amount_to_yuan(amount_kilo_yuan: float | pd.Series) -> float | pd.Series:
    """Convert tushare-native 千元 → 元."""
    return amount_kilo_yuan * YUAN_PER_KILOYUAN


def to_date(d: date | str | datetime | pd.Timestamp) -> date:
    """Coerce a date-like value to ``datetime.date``.

    Accepts ``datetime.date``, ``datetime.datetime``, ISO-format ``str``,
    or ``pandas.Timestamp`` (which is what DuckDB returns). Returns a
    plain ``datetime.date`` for stable comparison.
    """
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, pd.Timestamp):
        return d.date()
    return datetime.strptime(str(d), "%Y-%m-%d").date()


# ---------- public dataclasses ----------
@dataclass(frozen=True)
class FactorSpec:
    """One factor's definition. Used for logging + ranking stability checks.

    direction: +1 = factor↑ → long, -1 = factor↑ → short (reversal-style).
    """
    name: str
    lookback_days: int
    direction: int = 1
    description: str = ""
    weight: float = 1.0


@dataclass
class UniverseResult:
    """Result envelope for ``get_universe()`` / ``get_factor_universe()``."""
    as_of: date
    df: pd.DataFrame
    factors: list[FactorSpec] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------- core layer ----------
class PortfolioDataLayer:
    """Read-only façade over ``DuckDBStore``.

    Construct with no args to use the global DuckDBStore on disk; pass
    ``store=`` in tests to inject a fresh tmp store. Either way, we
    never reach for a tushare / akshare SDK — only DuckDB SQL.
    """

    def __init__(self, store: DuckDBStore | None = None) -> None:
        self.store = store or DuckDBStore()

    # ---------------- queries ----------------
    def get_calendar(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Open trading days between ``start`` and ``end`` (inclusive).

        The underlying ``mv_trade_cal`` is one row per ``(exchange, cal_date)``
        (SSE + SZSE). For portfolio scheduling we only need the unique
        ``cal_date``; we dedupe and return sorted ascending.

        Returns columns: ``cal_date`` (date), ``pretrade_date`` (date|None).
        """
        s = to_date(start)
        e = to_date(end)
        if e < s:
            return pd.DataFrame(columns=["cal_date", "pretrade_date"])
        sql = """
            SELECT cal_date, pretrade_date
            FROM mv_trade_cal
            WHERE cal_date BETWEEN ? AND ?
            ORDER BY cal_date
        """
        df = self.store.query(sql, [s, e])
        if df.empty:
            return df
        df = df.drop_duplicates(subset=["cal_date"]).sort_values("cal_date").reset_index(drop=True)
        return df

    def latest_trade_day(self, on_or_before: date | str | None = None) -> date | None:
        """Return the most recent open trade day on/before the given date.

        Defaults to today. Returns ``None`` if the calendar has no rows.
        """
        d = to_date(on_or_before) if on_or_before is not None else date.today()
        sql = "SELECT MAX(cal_date) AS d FROM mv_trade_cal WHERE cal_date <= ?"
        row = self.store.query(sql, [d])
        if row.empty or row["d"].iloc[0] is None or pd.isna(row["d"].iloc[0]):
            return None
        v = row["d"].iloc[0]
        return to_date(v)

    def get_universe(
        self,
        on_date: date | str,
        *,
        min_amount_yuan: float = 0.0,
        drop_suspended: bool = True,
    ) -> pd.DataFrame:
        """Tradable stock pool on a given trade day.

        Joins ``mv_daily_qfq`` (price/amount) with stock universe info
        (industry, name) from the Parquet root. Filters delisted /
        un-listed names (``list_status='L'``), suspended rows
        (``high=low AND vol=0``), and minimum liquidity.

        Returns columns: ``ts_code, trade_date, name, industry, close,
        vol_lots, amount_yuan, is_suspended``.
        """
        d = to_date(on_date)
        # stock_basic is a snapshot (no trade_date partition); use the static glob.
        # Real tushare stock_basic files have a partial schema; we use
        # union_by_name=True so the placeholder marker is tolerated.
        # The same ts_code can appear in multiple snapshot Parquet files
        # (re-synced daily), so we dedupe via QUALIFY ROW_NUMBER().
        sb_glob = str(data_dir() / "raw_tushare_stock_basic" / "**" / "*.parquet")
        sql = f"""
            WITH sb AS (
                SELECT ts_code, name, industry, list_status
                FROM read_parquet('{sb_glob}', union_by_name=True)
                QUALIFY ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY ts_code) = 1
            ),
            base AS (
                SELECT
                    q.ts_code,
                    q.trade_date,
                    q.close,
                    q.vol,
                    q.amount,
                    (q.high = q.low AND q.vol = 0) AS is_suspended_raw
                FROM mv_daily_qfq q
                WHERE q.trade_date = ?
            )
            SELECT
                b.ts_code,
                b.trade_date,
                COALESCE(sb.name, '')         AS name,
                COALESCE(sb.industry, '')     AS industry,
                b.close,
                b.vol                          AS vol_lots,
                b.amount * 1000.0              AS amount_yuan,
                b.is_suspended_raw             AS is_suspended
            FROM base b
            LEFT JOIN sb
              ON b.ts_code = sb.ts_code
            WHERE (sb.list_status IS NULL OR sb.list_status = 'L')
        """
        df = self.store.query(sql, [d])
        if df.empty:
            return df
        df = df[df["amount_yuan"] >= float(min_amount_yuan)]
        if drop_suspended:
            df = df[~df["is_suspended"].astype(bool)]
        return df.reset_index(drop=True)

    def get_factor_universe(
        self,
        on_date: date | str,
        lookback_days: int = 30,
        *,
        min_amount_yuan: float = 0.0,
        factors: Sequence[FactorSpec] | None = None,
    ) -> UniverseResult:
        """Universe + cross-section factors on ``on_date``.

        For each stock in the universe, we look back ``lookback_days``
        calendar days (mapped to the nearest prior trade days) and
        compute momentum / reversal / turnover-style factors from
        ``mv_daily_qfq``.

        ``lookback_days`` MUST be at least ``max(lookback) + 5`` to leave
        room for a 5-day buffer (skip the most recent row to avoid
        intraday noise in production data). A small safety check is
        enforced below.
        """
        on = to_date(on_date)
        if factors is None:
            factors = (
                FactorSpec("momentum_20d", 20, +1, "20D momentum = close[t]/close[t-20] - 1"),
                FactorSpec("reversal_5d",  5, -1, "5D reversal = -1 * (close[t]/close[t-5] - 1)"),
            )
        max_lb = max(f.lookback_days for f in factors)
        if lookback_days < max_lb + 5:
            lookback_days = max_lb + 5

        notes: list[str] = []
        start = on - timedelta(days=int(lookback_days) * 2)  # calendar cushion
        cal = self.get_calendar(start, on)
        if cal.empty:
            notes.append("calendar is empty for the requested window")
            return UniverseResult(as_of=on, df=pd.DataFrame(), factors=list(factors), notes=notes)
        # We need (max_lb + 1) prior trade days from ``on`` (inclusive of ``on``).
        prior_days = [to_date(d) for d in cal["cal_date"].tolist()]
        # Filter to days <= on and take the last (max_lb + 1) for window computation.
        prior_days = [d for d in prior_days if d <= on][-(max_lb + 1):]
        if len(prior_days) < 2:
            notes.append("not enough prior trade days to compute factors")
            return UniverseResult(as_of=on, df=pd.DataFrame(), factors=list(factors), notes=notes)
        earliest = prior_days[0]
        latest = prior_days[-1]

        # 1) pull the universe (also enforces suspension / liquidity filters)
        universe = self.get_universe(on, min_amount_yuan=min_amount_yuan, drop_suspended=True)
        if universe.empty:
            notes.append("universe is empty after liquidity / suspension filters")
            return UniverseResult(as_of=on, df=pd.DataFrame(), factors=list(factors), notes=notes)

        # 2) pull the close-history window for those ts_codes
        codes = universe["ts_code"].tolist()
        ph = ",".join(["?"] * len(codes))
        sql = f"""
            SELECT ts_code, trade_date, close, vol, amount
            FROM mv_daily_qfq
            WHERE trade_date BETWEEN ? AND ?
              AND ts_code IN ({ph})
            ORDER BY ts_code, trade_date
        """
        params: list[Any] = [earliest, latest, *codes]
        hist = self.store.query(sql, params)
        if hist.empty:
            notes.append("no history rows for universe in window")
            return UniverseResult(as_of=on, df=pd.DataFrame(), factors=list(factors), notes=notes)

        # 3) per-stock factor computation (vectorised, in pandas)
        out = universe.copy()
        # Normalize trade_date to python date for stable comparison (DuckDB returns
        # midnight Timestamps; `==` against a date object would miss matches).
        hist = hist.copy()
        hist["trade_date"] = hist["trade_date"].apply(to_date)
        base = hist[hist["trade_date"] == latest].set_index("ts_code")["close"]
        for spec in factors:
            target_day = latest - timedelta(days=int(spec.lookback_days))
            # snap to the latest prior trade day for each stock (per-stock
            # universe varies when some stocks have data gaps).
            sub = hist[hist["trade_date"] <= target_day]
            if sub.empty:
                out[spec.name] = float("nan")
                continue
            ref = sub.sort_values("trade_date").groupby("ts_code").tail(1).set_index("ts_code")["close"]
            ratio = base / ref - 1.0
            ratio = ratio.reindex(out["ts_code"])
            if spec.direction == -1:
                ratio = -ratio
            out[spec.name] = ratio.values
        # 4) convenience: turnover (amount_yuan) — keep untransformed so callers can re-rank
        out["amount_yuan"] = universe["amount_yuan"].values
        return UniverseResult(as_of=on, df=out.reset_index(drop=True), factors=list(factors), notes=notes)

    # ---------------- risk-filter helpers ----------------
    @staticmethod
    def filter_suspended(df: pd.DataFrame, *, col: str = "is_suspended") -> pd.DataFrame:
        """Drop rows where ``col`` is True. Pure pandas, no SQL."""
        if col not in df.columns:
            return df
        return df[~df[col].astype(bool)].reset_index(drop=True)

    @staticmethod
    def top_bottom(df: pd.DataFrame, score_col: str, *, n: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Return (top_n, bottom_n) sorted by ``score_col`` (desc / asc)."""
        if df.empty or score_col not in df.columns:
            empty = df.head(0)
            return empty, empty
        s = df[score_col]
        top = df.iloc[s.nlargest(min(n, len(df))).index].reset_index(drop=True)
        bot = df.iloc[s.nsmallest(min(n, len(df))).index].reset_index(drop=True)
        return top, bot


# ---------- internal helpers ----------
def _pivot_close_at_offset(hist: pd.DataFrame, lookback: int) -> pd.Series:
    """Return a Series indexed by ts_code with close at ``trade_date = latest - lookback``.

    Used inside ``get_factor_universe`` to avoid recomputing the same
    window multiple times. Falls back to NaN if a stock has fewer rows
    than ``lookback + 1``.
    """
    if hist.empty:
        return pd.Series(dtype=float)
    latest = hist["trade_date"].max()
    target_day = latest - pd.Timedelta(days=int(lookback))
    # snap to the most recent trade day ≤ target_day per stock
    sub = hist[hist["trade_date"] <= target_day]
    if sub.empty:
        return pd.Series(dtype=float)
    sub = sub.sort_values("trade_date")
    last = sub.groupby("ts_code").tail(1).set_index("ts_code")["close"]
    return last
