# A 股数据本地化方案（tushare + akshare）

> 适用范围：quant-meta-team 三 agent 协作（Data → Portfolio → Risk）。
> 维护：quant-orchestrator；落地执行：quant-data-agent。
> 状态：**v0.4**（已对齐用户指令：**本地优先暂存**，迁移到 `RSS_DATA` 外挂盘时只需改 `DATA_DIR` env；降级策略放宽；Week 1 已派发）。
>
> **变更记录**：
> - v0.4 (2026-06-05)：按用户 19:12 指令改为**本地优先**——`DATA_DIR` 默认 `~/Code/quant-meta-team/quant_data/data`（项目仓库内，方便一起版本化/迁移），外置盘 `/Volumes/RSS_DATA/quant_data` 作为后续迁移目标；降级策略由 `blocked + @mention` 放宽为 `warn + 本地落盘 + 一次 mention`；新增 §6.6 显式迁移清单（rsync / rename / 重启调度）。
> - v0.3 (2026-06-05)：外挂硬盘约定对齐 `~/news-rss`——卷标 `RSS_DATA`，根目录 `/Volumes/RSS_DATA/quant_data`；补充 rss 项目的实际探活结果（卷当前**未挂载**）。
> - v0.2 (2026-06-05)：按用户反馈调整——tushare 升级为 2000 积分档；存储路径改为外挂硬盘 + `DATA_DIR` 配置；统一抽象层升级为**注册表 + Schema 版本化**模式，为后续多源接入做准备。
> - v0.1 (2026-06-05)：初始调研稿。

## 1. 数据源对比结论

| 维度 | tushare Pro | tushare 旧版 | akshare |
|------|-------------|--------------|---------|
| 注册/Token | 必须注册 + Token | 必须注册 + Token | 不需要 |
| 主要数据源 | Tushare 官方维护 + 交易所合作 | 早年抓取新浪/网易（已停维） | 抓取东财/新浪/腾讯/同花顺等网页接口 |
| 字段规范 | 统一 `ts_code`（如 `000001.SZ`）、字段命名稳定 | 6 位代码 | 6 位代码 + 接口命名不统一 |
| 免费层频率 | **2000 积分：200 req/min、10 万次/日**（用户当前档位，覆盖 ~95% 接口） | 旧接口不再维护 | 取决于上游网站，普遍存在封 IP / 验证码 / 429 |
| 高频/分钟线 | 单独权限（捐助约 1000 元/年起 + 频次单算） | 无 | 部分接口有，稳定性差 |
| 数据质量 | 高（自营清洗 + 复权因子独立生产） | 中（已停更） | 中（依赖第三方，字段名偶有变化） |
| 合规 | 商业付费接口，授权清晰 | 已停维 | 仅限「个人学习」；批量/商业再分发存在法律风险（公开判例可查） |
| 稳定性 | 服务端稳定，但有积分风控 | 不可用 | 严重依赖上游：东财单页 100 条，全市场 50+ 次请求 → 经常被限流 |
| 适用角色 | 主数据源（标准、字段稳定、可追溯） | 仅作旧数据补全参考 | 备用 + 兜底（tushare 缺字段时拉一遍交叉验证） |

**结论**：以 **tushare Pro 为主、akshare 为辅**。日常数据全部走 tushare，akshare 仅作为字段缺失时的交叉验证源，且必须做限速与失败重试隔离。

> 证据：tushare 官方积分档位（https://tushare.pro/document/1?doc_id=290）、积分与限频关系（https://blog.csdn.net/linzhjbtx/article/details/117854638）、akshare 东财限流案例（https://github.com/akfamily/akshare/issues/6106、https://www.cnblogs.com/snowlove67/p/19015348）。

## 2. 存储选型对比

| 方案 | 写吞吐 | 列存友好度 | 查询生态 | 运维成本 | Python 集成 | 单价/容量 | 量化回测适配 |
|------|--------|------------|----------|----------|-------------|-----------|--------------|
| **Parquet + DuckDB** | 批量 50-200 MB/s | 极佳（Snappy/Zstd 压缩 5-20×） | DuckDB SQL + pandas | 零（文件即可） | `duckdb`、`pyarrow`、`pandas.read_parquet` | 极低（GB 级） | **最佳**（横截面查询、列裁剪、谓词下推） |
| **ClickHouse** | 50-200 MB/s 批量；单行写弱 | 极佳 | 强 SQL，但 JOIN 写法特殊 | 中（需服务进程） | `clickhouse-driver` / `clickhouse-connect` | 较低 | 适合多日多表关联分析 |
| **PostgreSQL** | 中，行式 | 一般（不列存） | 完整 SQL + 丰富扩展 | 中 | `psycopg` / `SQLAlchemy` | 中 | 适合元数据 + 事务型记录 |
| **MySQL** | 中，行式 | 一般 | 主流但分析能力弱 | 低 | `pymysql` | 中 | 仅适合中小规模研究 |
| **SQLite** | 低-中 | 无 | 嵌入式 SQL | 零 | 内置 `sqlite3` | 极低 | 单人 / 临时脚本 |
| **HDF5** | 高 | 一般（按表结构） | h5py / pandas | 低 | `h5py`、`pandas.HDFStore` | 中 | 日内分钟 / tick 存储尚可，但横截面/多表查询难 |

**结论（推荐存储分层）**：

1. **主存储：Parquet + DuckDB** — 行情/复权/财务/因子等列式数据落 Parquet（按 `trade_date` / `ts_code` Hive 分区），DuckDB 作为查询引擎向 agent 提供 SQL 视图。**对应个人 / 小团队 + Data → Portfolio → Risk 协作的最优解**。
2. **元数据/任务状态：SQLite（最小化）或 PostgreSQL** — 记录增量游标、字段口径、任务调度、字段映射字典等小表。SQLite 适合单机；多 agent 共写时升 PostgreSQL。
3. **备选：ClickHouse** — 当日线数据量到 10 亿行、agent 并发查询高（>50 QPS）时再考虑；当前阶段不必上。

> 证据：ClickHouse 列存与性能（https://zhuanlan.zhihu.com/p/405090984）、DuckDB + Parquet 量化实战（https://cloud.tencent.com/developer/article/2659230）、HDF5/Parquet 选择经验（https://zhuanlan.zhihu.com/p/675767714）、ClickHouse 存 A 股实践（https://blog.csdn.net/wowotuo/article/details/122902005）。

## 3. 统一数据 Schema（最小可复用）

### 3.1 ID 与命名

- **主 ID：`ts_code`（Tushare 风格）**，例如 `000001.SZ`、`600519.SH`。
- akhare 的 6 位代码统一加 `.SH`/`.SZ`/`.BJ` 后缀进入同一空间（SH: 6/9 开头；SZ: 0/3 开头；BJ: 4/8 开头）。
- 表名统一前缀 `raw_<source>_<topic>` 落地原值，`mv_<topic>` 提供清洗/对齐后的标准视图。

### 3.2 核心表（Parquet 落地）

| 表 | 主键 | 关键字段 | 来源 |
|----|------|----------|------|
| `raw_tushare_stock_basic` | `ts_code` | `name, list_date, delist_date, industry, exchange, list_status` | tushare `stock_basic` |
| `raw_tushare_trade_cal` | `(exchange, cal_date)` | `is_open, pretrade_date` | tushare `trade_cal` |
| `raw_tushare_daily` | `(ts_code, trade_date)` | `open, high, low, close, pre_close, change, pct_chg, vol, amount` | tushare `daily` |
| `raw_tushare_adj_factor` | `(ts_code, trade_date)` | `adj_factor` | tushare `adj_factor` |
| `raw_tushare_daily_basic` | `(ts_code, trade_date)` | `turnover_rate, pe, pb, ps, total_mv, circ_mv` | tushare `daily_basic` |
| `raw_akshare_stock_zh_a_hist` | `(code, date)` | `open, high, low, close, volume, amount, turnover` | akshare（兜底/对账） |

### 3.3 标准视图（DuckDB 中物化）

| 视图 | 公式 | 用途 |
|------|------|------|
| `mv_daily_qfq` | `close * adj_factor / latest_adj_factor`（向前复权） | K 线分析、价量研究 |
| `mv_daily_hfq` | `close * adj_factor / first_adj_factor`（向后复权） | 累计收益、回测 |
| `mv_trade_cal` | 仅 `is_open=1` 的交易日 | 调仓、对齐 |

**复权约定（强烈建议落地前评审通过）**：
- 默认入库 **不复权** + **复权因子**。
- 回测时按需物化前复权 / 后复权视图，避免「一个因子存在多个版本」导致研究与上线错位。
- 公式来源：tushare 官方 `adj_factor`（https://tushare.pro/document/2?doc_id=28）、通达信复权算法对照（https://blog.csdn.net/m0_37967652/article/details/146885598）。

## 4. 增量更新与工程化

### 4.1 增量游标

- 每个表维护 `meta_sync_state(table, last_trade_date, last_run_at, status, error_msg)`。
- 启动时读游标 → 调 `pro.daily(trade_date=游标+1, …)`（按 trade_date 全市场一次拉）→ upsert → 更新游标。
- 关键设计：**按 trade_date 拉取，而不是按 ts_code**。tushare 全市场单日 5000+ 只股票一次返回，比逐只循环快 1-2 个数量级。

### 4.2 断点续传

- 游标持久化到 SQLite/Postgres 的 `sync_state` 表。
- 任务失败时记录 `status='failed'` + 异常堆栈；下次启动自动从 `last_trade_date+1` 续跑。
- 对**上市/退市/复牌**事件：每次同步完成后调用 `stock_basic(list_status='L/D/P')` 做差异对比，生成 `stock_lifecycle_event` 表，供 Portfolio/Risk 过滤股票池。

### 4.3 限频与重试

- tushare 2000 积分默认 200 req/min；建议包一层令牌桶（`token-bucket`），实测留 20% 余量。
- 指数退避：429 / 限频响应时 `sleep(2^n + jitter)`，n ≤ 5。
- akshare：仅用于**对账/兜底**（同一交易日与 tushare 比对，差值 < 阈值则忽略），避免触发东财反爬。

### 4.4 任务调度模板（伪代码）

```python
# quant-data-agent 的标准更新任务
def sync_table(table: str, fetcher, store, *, lookback_days: int = 1):
    cursor = store.get_cursor(table)         # last trade_date 已同步
    target_dates = get_open_trade_days(cursor + 1, today, lookback=lookback_days)
    for d in target_dates:
        df = retry_with_backoff(lambda: fetcher(trade_date=d.isoformat()))
        df = normalize_columns(df, table)     # 字段口径统一（见 §5）
        store.upsert(table, df)              # DuckDB upsert via temp table
        store.set_cursor(table, d)
        emit_metric("data_sync_rows", len(df), tags={"table": table, "date": str(d)})
```

## 5. 字段口径统一（关键风险点）

- **代码体系**：akshare 6 位 → tushare `ts_code` 补后缀。函数：见 CSDN 资料 https://cloud.tencent.com/developer/article/2659458 的转换片段。
- **成交量单位**：tushare `vol` 单位是「手」（1 手 = 100 股），akshare `volume` 已是「股」。下游口径统一成「股」（=`vol * 100` 或直接 `amount / avg_price` 反算）。
- **成交额单位**：tushare `amount` 单位是「千元」，akshare `amount` 是「元」。下游统一成「元」。
- **日期格式**：tushare `YYYYMMDD`；akshare `YYYY-MM-DD`。统一成 `DATE` 类型（DuckDB 直接 cast）。
- **复权**：`close_qfq = close * adj_factor / latest_adj_factor`，只算一次落视图，不在每次回测里重算。
- **停牌 / 涨跌停**：tushare 日线 `high == low` 且 `vol == 0` 视为停牌；回测时停牌日不调仓（涨/跌停默认不可买/卖），由 Risk agent 在回测引擎里强制。
- **退市股**：`list_status='D'` 一律不进入股票池；用 `delist_date` 做截止。

## 6. 与三 Agent 的衔接

| Agent | 消费数据 | 写入 | 接口形态 |
|-------|----------|------|----------|
| **Data** | 调度 `sync_table` 落 Parquet + 更新游标 | `data/`, `meta/` | CLI / Python module；输出 `sync_report` 到 issue comment |
| **Portfolio** | DuckDB 视图 `mv_daily_qfq` + `mv_daily_basic` | 因子矩阵、候选组合 | SQL + pandas；不直连 tushare |
| **Risk** | 同 Portfolio；额外读 `mv_trade_cal` 做 T+1、调仓对齐 | 回测报告、风控指标 | 读 DuckDB，输出 backtest JSON/MD |

**统一抽象层（落地到 `~/Code/quant-meta-team/quant_data/`，按"注册表 + Schema 版本化"设计，支持多源可插拔）**：

> 设计原则：用户已明示后续会接入更多数据源，因此**接口边界、字段口径、数据血缘、Schema 版本**全部按多源设计。Data / Portfolio / Risk 三个 agent 只依赖 `DataSource` / `DataStore` 抽象，绝不直接 import 任何数据源 SDK。

### 6.1 三层抽象

```python
# ---- Layer 1: 数据源（Source）— 每个外部数据源 = 一个 Adapter 注册项 ----
class DataSource(Protocol):
    name: str                     # 唯一短码：tushare / akshare / wind / joinquant / custom_csv
    version: str                  # adapter 自身版本（区别于 schema 版本）
    capabilities: set[str]        # {"daily", "adj_factor", "fina_indicator", ...}

    def fetch(self, topic: str, **params) -> pd.DataFrame: ...
    def rate_limit(self) -> RateLimit: ...   # 200 req/min, etc.
    def healthcheck(self) -> bool: ...

# ---- Layer 2: 字段口径（Schema）— 跨源字段对齐 + 版本化 ----
@dataclass
class FieldSpec:
    name: str                     # "close"
    dtype: str                    # "float64"
    unit: str                     # "yuan" / "share" / "lot" / "kilo_yuan"
    nullable: bool
    description: str

@dataclass
class TableSchema:
    table: str                    # "daily"
    version: str                  # "v1.0" — 字段口径变更时 +minor
    primary_key: list[str]
    fields: dict[str, FieldSpec]
    source_mapping: dict[str, str]   # source_name -> source_native_field

# ---- Layer 3: 存储（Store）— 物理落地（DuckDB / Parquet / ClickHouse 均可） ----
class DataStore(Protocol):
    def query(self, sql: str, params: dict) -> pd.DataFrame: ...
    def upsert(self, table: str, df: pd.DataFrame, schema_version: str) -> int: ...
    def get_cursor(self, table: str) -> date: ...
    def set_cursor(self, table: str, d: date) -> None: ...
    def register_schema(self, schema: TableSchema) -> None: ...
```

### 6.2 注册表（SourceRegistry）

新增数据源 = 注册一行，不改 agent 代码：

```python
# quant_data/registry.py
SOURCES: dict[str, DataSource] = {
    "tushare": TushareAdapter(pro_token=os.getenv("TUSHARE_TOKEN"), tier=2000),
    "akshare": AkshareAdapter(proxy=os.getenv("AKSHARE_PROXY")),
    # 未来直接注册：
    # "wind":     WindAdapter(...),
    # "joinquant": JoinQuantAdapter(...),
    # "csv":      CsvFileAdapter("/path/to/dropbox"),
}
SCHEMAS: dict[tuple[str, str], TableSchema] = {
    ("daily",      "v1.0"): DAILY_V1,
    ("adj_factor", "v1.0"): ADJ_FACTOR_V1,
    ("fina",       "v1.0"): FINA_V1,
}
```

### 6.3 表落地约定（兼容多源）

| 物化表 | 来源 | 主键 | 备注 |
|--------|------|------|------|
| `raw_tushare_daily` | tushare | `(ts_code, trade_date)` | 原值 + `source='tushare'` + `fetched_at` |
| `raw_akshare_daily` | akshare | `(ts_code, trade_date)` | 同上 |
| `raw_wind_daily` | wind（未来） | `(ts_code, trade_date)` | 同上 |
| `mv_daily_v1` | **多源融合** | `(ts_code, trade_date)` | 取 `raw_*` 优先级链中第一个非空行；写表时记录 `provenance` |

```sql
-- mv_daily_v1：tushare 优先，akshare 兜底；每个 ts_code × trade_date 只一行
SELECT
  COALESCE(t.ts_code, a.ts_code) AS ts_code,
  COALESCE(t.trade_date, a.trade_date) AS trade_date,
  COALESCE(t.close, a.close) AS close,
  COALESCE(t.vol_lot, a.vol_share / 100.0) AS vol_lot,   -- 单位统一为"手"
  COALESCE(t.amount_yuan, a.amount_yuan) AS amount_yuan, -- 单位统一为"元"
  CASE WHEN t.ts_code IS NOT NULL THEN 'tushare'
       WHEN a.ts_code IS NOT NULL THEN 'akshare'
  END AS provenance
FROM raw_tushare_daily t
FULL OUTER JOIN raw_akshare_daily a
  ON t.ts_code = a.ts_code AND t.trade_date = a.trade_date;
```

**Schema 演进规则**：新增字段 = `+minor`（如 `v1.0 → v1.1`），下游不需改；改变单位 / 主键 / 字段语义 = `+major`（`v1.x → v2.0`），下游需显式 `migrate` 后再读。

### 6.4 数据血缘（lineage）

每次 `upsert` 自动写一份元数据，方便 Portfolio / Risk 验证数据来自哪个源、哪个时间点拉取：

```python
meta = {
  "table":         "raw_tushare_daily",
  "schema_version":"v1.0",
  "source":        "tushare",
  "source_version":"tushare-pro-2026.06",
  "fetched_at":    "2026-06-05T18:30:00+08:00",
  "params":        {"trade_date": "20260605"},
  "rows":          5234,
  "rate_limit_hit":0,
  "request_id":    "uuid-...",
}
store.write_meta(table, meta)   # 落到 meta/_lineage/<table>/<date>.json
```

### 6.5 路径与配置（本地优先 + 后续迁外置盘）

**v0.4 起改为本地优先**——`DATA_DIR` 默认落在本机项目目录内，方便随代码一起版本化和迁移。挂外置盘（`RSS_DATA`）时**无需改代码**，只改 env 即可。

| 阶段 | `DATA_DIR` 路径 | 备注 |
|------|-----------------|------|
| **当前（v0.4，本地优先）** | `~/Code/quant-meta-team/quant_data/data` | 项目内、本机 SSD；落 Parquet / DuckDB / 游标 / 血缘 |
| **后续（迁外置盘）** | `/Volumes/RSS_DATA/quant_data` | 与 `news-rss` 共用卷；见 §6.6 迁移步骤 |

| 项 | 值 | 说明 |
|---|---|---|
| 卷标（目标） | `RSS_DATA` | 与 `news-rss`、`backup_rss_data` 共用同一块盘（参考 `~/news-rss`） |
| 默认挂载点（目标） | `/Volumes/RSS_DATA` | macOS 自动挂载；卷名变更时需同步更新 |
| 量化数据根（目标） | `/Volumes/RSS_DATA/quant_data` | 与 rss 的 `rss-feed/` 同级，互不干扰 |
| 日志/代码 | `~/Code/quant-meta-team/` | **永远放本机**（高频写、占空间小），不外挂 |

```bash
# .env（不入 git）
TUSHARE_TOKEN=...                      # 已在环境变量里
AKSHARE_PROXY=...                      # 可选
# DATA_DIR 不写 → 默认落本机 ~/Code/quant-meta-team/quant_data/data
# 迁外置盘时改成：DATA_DIR=/Volumes/RSS_DATA/quant_data
LOG_DIR=~/Code/quant-meta-team/logs
CACHE_DIR=${DATA_DIR}/cache
```

代码里所有路径都通过 `paths.data_dir()` / `paths.log_dir()` 这种函数读 env，**禁止硬编码 `/Users/...` 或 `~/...`**。挂载点缺失时 fallback：

```python
def data_dir() -> Path:
    env = os.getenv("DATA_DIR")
    if env:
        p = Path(env).expanduser()
    else:
        p = Path.home() / "Code" / "quant-meta-team" / "quant_data" / "data"
    p.mkdir(parents=True, exist_ok=True)
    if env and str(p).startswith("/Volumes/") and not p.exists():
        # 仅在外置盘路径显式设置但挂载缺失时回退到本地，并发一次 mention
        logger.warning("DATA_DIR %s 不存在，回退到本地 %s", p, fallback)
        return fallback
    if not env:
        logger.info("DATA_DIR 未设置，使用本地默认 %s", p)
    return p
```

**降级策略（v0.4 放宽）**：
- `DATA_DIR` 未设置 → 用本地默认路径 + `info` 日志；正常跑。
- `DATA_DIR` 显式设为 `/Volumes/...` 但挂载缺失 → `warn` + 回退本地 + `sync_report` comment 里 **@mention 用户一次**；不阻塞（之前是 `blocked`，本地优先阶段已无意义）。
- 永远不静默写到与 `DATA_DIR` 不同的目录。

### 6.6 后续迁移到外置盘 `RSS_DATA` 的步骤

触发条件：本地数据 >50 GB 或本机 SSD 空间吃紧时迁移。**所有步骤停 scheduler，期间不写**：

```bash
# 0. 停调度（手动 / autopilot 关掉）
# 1. 挂外置盘（diskutil mount /Volumes/RSS_DATA）
# 2. 单次全量 rsync（首次）
rsync -avh --progress \
  ~/Code/quant-meta-team/quant_data/data/ \
  /Volumes/RSS_DATA/quant_data/
# 3. 验证：行数 / checksum
duckdb -c "SELECT count(*) FROM read_parquet('/Volumes/RSS_DATA/quant_data/raw_tushare_daily/**/*.parquet')"
# 4. 改 env：.env 里 DATA_DIR=/Volumes/RSS_DATA/quant_data
# 5. 重启 scheduler；首轮跑 test_external_drive.py 验证挂载存在
# 6. 旧本地目录暂留 1 周观察期后删除（避免误删恢复成本）
```

> 注意：`/Volumes/...` 路径在 macOS 休眠唤醒后可能掉，scheduler 重启时按 §6.5 降级策略走「回退本地 + 一次 mention」。

## 7. 成本与风险评估

### 7.1 成本

| 项 | 量化 | 备注 |
|----|------|------|
| tushare Token | 用户已开通 **2000 积分**（~¥200/年） | **200 req/min、10 万次/日**；覆盖 ~95% 接口，可支撑分钟线以外的常规研究 |
| tushare 升级 | 5000 积分 ~¥500/年（常规数据无上限） | 暂不需要，2000 积分已够 |
| tushare 分钟线 | 单独捐助（参考 1000 元/年起） | 当前阶段不需要 |
| 存储 | 5000 只 × 10 年日线 ≈ 1-2 GB（Parquet+Zstd 压缩） | **v0.4 暂存本机** `~/Code/quant-meta-team/quant_data/data`；后续可平滑迁移到 `/Volumes/RSS_DATA/quant_data`（见 §6.6） |
| 维护人力 | 1 人 / 0.2 FTE 即可（Data agent 自动跑） | 增量任务 + 偶发对账 |

### 7.2 风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| tushare 积分清零（一年有效期） | 中 | 预算 ¥200/年续期；保留 akshare 兜底；抽象层隔离切源成本 |
| akshare 上游（东财）反爬 | 高 | 仅作兜底；配失败重试 + 限速 + 登录态（cookie） |
| **外挂硬盘掉线 / 未挂载** | **高** | 启动时校验 `DATA_DIR`；缺失则 `blocked` + `@mention` 用户；fallback 目录仅作应急，禁止写生产数据 |
| **多源字段冲突（用户后续接入新源）** | **高** | 走 `mv_*` 视图 + `provenance` 字段 + Schema 版本；冲突差异写入 `meta/_conflicts/<date>.json` 供 Data agent 仲裁 |
| 数据源条款变更 | 中 | `SourceRegistry` 解耦；切源不改 agent 代码 |
| 复权口径不一致 | 高 | **入库不复权 + 落 adj_factor**；回测时统一视图 |
| 停牌 / 涨跌停处理遗漏 | 高 | Risk 回测引擎必须做停牌/涨跌停过滤 |
| 退市股导致幸存者偏差 | 高 | Data 维护 `stock_lifecycle_event`；Risk 在股票池构建时按历史时点过滤 |
| IP 封禁 / 验证码 | 中 | akshare 走 cookie + 限速；tushare 服务端 IP 不受此影响 |
| Token 泄露 | 中 | token 入 `.env` / 系统密钥，不进 git；CI 用 secret |

## 8. 最小可行落地步骤（MVP）

```text
quant-data/                                  # 实际路径：~/Code/quant-meta-team/quant_data/
├── pyproject.toml          # tushare, akshare, duckdb, pyarrow, sqlalchemy, apscheduler, python-dotenv
├── .env.example            # TUSHARE_TOKEN, AKSHARE_PROXY, DATA_DIR=/Volumes/RSS_DATA/quant_data, LOG_DIR=./logs
├── README.md               # 安装 / 配置 / 接入新数据源（contribution guide）
├── quant_data/
│   ├── paths.py            # 唯一读 DATA_DIR / LOG_DIR 的地方
│   ├── registry.py         # SourceRegistry + SchemaRegistry
│   ├── schemas/
│   │   ├── daily_v1.py
│   │   ├── adj_factor_v1.py
│   │   ├── fina_v1.py
│   │   └── trade_cal_v1.py
│   ├── sources/            # 一个文件一个 DataSource
│   │   ├── base.py         # DataSource / DataAdapter protocols
│   │   ├── tushare.py      # TushareAdapter（tier-aware：2000 积分档）
│   │   ├── akshare.py
│   │   └── _template.py    # 新源复制此文件改 50 行即可
│   ├── store/
│   │   ├── duckdb_store.py # 主查询引擎
│   │   ├── parquet_store.py
│   │   └── meta_sqlite.py  # 游标 + lineage
│   ├── sync/
│   │   ├── sync_daily.py
│   │   ├── sync_adj_factor.py
│   │   ├── sync_basic.py
│   │   └── sync_trade_cal.py
│   ├── views/              # DuckDB 视图 SQL：qfq/hfq/calendar/mv_daily_v1（多源融合）
│   ├── rate_limit.py       # 令牌桶（按 source 配置）
│   └── scheduler.py        # APScheduler 每日 17:30 跑增量
├── tests/
│   ├── test_sync_idempotent.py   # 重复跑不会产生重复行
│   ├── test_resume.py            # 故意 kill 中间状态后能从断点续跑
│   ├── test_field_alignment.py   # akshare vs tushare 同日数据差值阈值
│   ├── test_registry.py          # 注册新源不改 agent 代码
│   └── test_external_drive.py    # DATA_DIR 未挂载 → blocked + 告警
└── Makefile                # make init / sync / test / report
```

**3 步上线（v0.4 适配本地优先 + 2000 积分档）**：

1. **Week 1（已派发）**：①**本机默认路径** `~/Code/quant-meta-team/quant_data/data`（不依赖外置盘）；②`tushare.pro.stock_basic` 探活（已验证 token OK）；③同步 `stock_basic` + `trade_cal` + `daily` + `adj_factor` + `daily_basic` 全市场历史（按 trade_date 拉，5 千只 × 1 日 = 1 次请求），落 Parquet 到本机；④DuckDB 建视图 `mv_daily_v1`（多源融合骨架）；⑤写 `tests/test_local_fallback.py`（验证 `DATA_DIR` 缺失回退本地、显式 `/Volumes/...` 挂载缺失也回退）。
2. **Week 2**：①接 APScheduler 每日 17:30 增量同步（200 req/min 留 20% 余量，跑到 ~160 req/min）；②写 `sync_state` 游标 + `meta/_lineage`；③加 `test_resume`、`test_registry`。
3. **Week 3**：①Portfolio / Risk agent 切到 DuckDB 查询；②写 README + 接入新源指引（`_template.py`）；③跑 7 天压测，收集 `token_calls_per_min` / `cache_hit_rate` 写 L1 经验。

## 9. 未验证假设（落地前需要 Data agent 实测确认）

1. **tushare 2000 积分档实际限频**：官方文档是 200 req/min / 10 万次/日，但 tushare 在请求体很大（如 daily 全市场 5000+ 行）时仍可能触发隐性 throttle。**需 Data agent 跑 7 天压测，记录 P99 响应时间 + 被限频次数**。
2. **本机 SSD 写吞吐与并发**（v0.4 临时）：Parquet 批量写 50-200 MB/s 是 SSD 估值，**需 Data agent 测 `make sync-full` 的 wall-clock**。后续迁外置盘（HDD/USB 桥接）时再回归测 3-5× 衰减，并确认是否要按月分目录落盘。
3. **本机 → 外置盘迁移的真实耗时**：v0.4 阶段不验证，迁盘时（§6.6）单跑 `rsync -avh` 看 wall-clock；首轮也跑 `duckdb count(*)` 校验行数一致。
4. **DuckDB 单文件超过 ~50 GB 时查询性能**：本项目估算 1-2 GB，但 10 年分钟线 / 全 A 财务全字段可能到几十 GB。**需在跑满 1 年后回归**。
5. **akshare 在登录态（cookie）下是否真能稳定 24/7**：社区说法不一。**需 Data agent 在沙箱跑一周观察**。
6. **停牌日 `high==low` 判定的可靠性**：极少数事件（首日上市、复牌首日）可能误判。**需 Risk agent 在回测前做白名单覆盖**。
7. **tushare 复权因子 vs 通达信/同花顺复权因子的数值一致性**：偶有口径差异（红筹股 / CDR）。**需在 MVP 跑通后抽样 5-10 只股票比对**。
8. **多 agent 并发写 Parquet 时的冲突**：当前规划是 Data 单写、Portfolio/Risk 只读；**若后续多写需加 advisory lock**。
9. **后续新数据源接入是否真的"不改 agent 代码"**：注册新源时需要写 adapter + schema，但 Portfolio / Risk 的 SQL 不能动。**第 3 周专门跑一遍接入 Wind/聚宽的 dry-run 验证**。

## 10. 参考资料

- tushare 官方积分档位表：https://tushare.pro/document/1?doc_id=290
- tushare 复权因子接口：https://tushare.pro/document/2?doc_id=28
- tushare 交易日历接口：https://tushare.pro/document/2?doc_id=26
- tushare 积分机制与社区讨论：https://blog.csdn.net/linzhjbtx/article/details/117854638
- akshare 项目主页：https://github.com/akfamily/akshare
- akshare 东财限流案例：https://github.com/akfamily/akshare/issues/6106
- A 股复权算法对照：https://blog.csdn.net/m0_37967652/article/details/146885598
- DuckDB + Parquet 量化实战：https://cloud.tencent.com/developer/article/2659230
- ClickHouse 存 A 股实践：https://blog.csdn.net/wowotuo/article/details/122902005
- HDF5 vs Parquet 选型经验：https://zhuanlan.zhihu.com/p/675767714
- tushare → akshare 代码封装示例：https://cloud.tencent.com/developer/article/2659458
- 类 zer0share 范式（参考实现）：https://github.com/skyformat99/zer0share
