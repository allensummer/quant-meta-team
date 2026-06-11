# quant-data

A-share data localization for the `quant-meta-team` (Data → Portfolio → Risk) pipeline.

Implements `docs/data-localization.md` **v0.6**: tushare primary, akshare backup,
Parquet + DuckDB + SQLite storage, multi-source pluggable via a `SourceRegistry`.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env       # then fill TUSHARE_TOKEN
```

`DATA_DIR` defaults to the external drive `/Volumes/RSS_DATA/quant_data`
(active since 2026-06-11). Leave it unset to fall back to the project-local
`~/Code/quant-meta-team/quant_data/data` (see `docs/data-localization.md` §6.5 + §6.6
for migration SOP). If the external drive is unmounted, `paths.py` falls back to
local and emits a warning — it never silently writes to the wrong place.

## Quickstart

```bash
make init          # create dirs, bootstrap DuckDB + views
make sync-full     # backfill 5 tushare tables for the full A-share history
make test          # run pytest with coverage (target: ≥80%)
make report        # print row counts + cursor + lineage
```

`make sync-daily` runs an incremental pull (today − 1 trade day).

## Architecture (v0.4, §6.1-§6.3)

```
quant_data/
├── paths.py            # 唯一读 DATA_DIR / LOG_DIR 的地方（§6.5 降级策略）
├── registry.py         # SourceRegistry + SchemaRegistry
├── rate_limit.py       # 令牌桶（按 source 配置；tushare 200 req/min → 用 160）
├── sources/
│   ├── base.py         # DataSource / DataAdapter protocols
│   ├── tushare.py      # TushareAdapter (tier-aware, 2000 积分档)
│   ├── akshare.py      # AkshareAdapter
│   └── _template.py    # 新源 = 复制此文件改 ~50 行
├── store/
│   ├── duckdb_store.py # 主查询引擎（DuckDB + 视图）
│   ├── parquet_store.py# Hive 分区写盘
│   └── meta_sqlite.py  # sync_state + meta/_lineage
├── schemas/            # v1 字段口径 + primary_key + source_mapping
├── sync/               # 5 张表的同步入口
├── views/              # mv_daily_v1 / qfq / hfq / trade_cal SQL
└── cli.py              # `python -m quant_data.cli …`
```

## Adding a new data source

1. Copy `quant_data/sources/_template.py` to `quant_data/sources/<name>.py`.
2. Implement `name`, `version`, `capabilities`, `fetch(topic, **params)`,
   `rate_limit()`, `healthcheck()`.
3. Register in `quant_data/registry.py::SOURCES` (one line).
4. Add a schema in `quant_data/schemas/` if introducing a new table; reuse
   existing schemas otherwise. Portfolio / Risk agents never need to change.

That's the only edit required. Downstream consumers (Portfolio, Risk) only see
`mv_*` views and `DataSource` / `DataStore` abstractions — no SDK import.

完整流程（含字段口径差异、DoD 清单、Wind/聚宽 dry-run 步骤）见
`docs/data-localization.md` §12。

## 下游接入指南（Portfolio / Risk Agent）

> 详细版见 `docs/data-localization.md` §11，本节为速查。

**唯一允许的 import**：

```python
from quant_data.store.duckdb_store import DuckDBStore   # 强制 read_only=True
from quant_data.registry import SOURCES, SCHEMAS, get_source, get_schema
from quant_data.sources.base import DataSource, DataStore, TableSchema
```

**禁止**：`import tushare` / `import akshare` / `from tushare import pro_api` 等。
CI 门禁：`grep -RE "import tushare|import akshare|from tushare|from akshare" quant_portfolio/ quant_risk/` 必须 0 命中。

**基本查询模板**：

```python
store = DuckDBStore(read_only=True)
df = store.query(
    """
    SELECT ts_code, trade_date, close_qfq
    FROM mv_daily_qfq
    WHERE trade_date BETWEEN ? AND ?
      AND ts_code IN (SELECT ts_code FROM raw_tushare_stock_basic
                       WHERE list_status = 'L' AND delist_date IS NULL)
    ORDER BY ts_code, trade_date
    """,
    params={"start": "2025-01-01", "end": "2025-06-05"},
)
```

**可用视图清单（截至 v0.6）**：

| 视图 | 主键 | 用途 |
|------|------|------|
| `mv_daily_qfq` | `(ts_code, trade_date)` | 前复权 OHLCV + `close_qfq`（Portfolio 选股） |
| `mv_daily_hfq` | `(ts_code, trade_date)` | 后复权 OHLCV + `close_hfq`（Risk 回测累计收益） |
| `mv_trade_cal` | `(exchange, cal_date)` | 交易日 / 调仓日 / T+1 对齐 |
| `raw_tushare_daily` | `(ts_code, trade_date)` | 不复权原始行情（停牌 / 涨跌停判定） |
| `raw_tushare_daily_basic` | `(ts_code, trade_date)` | `turnover_rate / pe / pb / total_mv / circ_mv` |
| `raw_tushare_stock_basic` | `ts_code` | 股票池过滤（`list_status` / `delist_date`） |
| `raw_tushare_trade_cal` | `(exchange, cal_date)` | 全部日历（含休市） |

**已知限制（v0.6）**：
- 沪深 300 成分股未精确化（用全 A active 近似，等 Wind 接入后再单独 issue）
- 行业 / 风格中性化未在视图层做（Portfolio 在 SQL/pandas 层自做）
- 分钟线 / tick 未接入（需要 tushare 单独捐助，单独开 issue）
