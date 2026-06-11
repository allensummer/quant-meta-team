# A 股数据本地化方案（tushare + akshare）

> 适用范围：quant-meta-team 三 agent 协作（Data → Portfolio → Risk）。
> 维护：quant-orchestrator；落地执行：quant-data-agent。
> 状态：**v0.7**（2026-06-11 数据已迁移至 `RSS_DATA` 外置盘 —— `/Volumes/RSS_DATA/quant_data`，共 3.9 GB；DuckDB SHA256 与迁移前一致、5 表行数一致、5 cursors 全部 `last_trade_date=2026-06-05 status=ok`；旧本地数据已重命名为 `quant_data/data.local-bak-20260611` 保留 1 周观察期；`.env` / `.env.example` / launchd plist 模板 / README 的 DATA_DIR 已切到 RSS_DATA）。
>
> **变更记录**：
> - v0.7 (2026-06-11)：数据迁移至外置盘 RSS_DATA。步骤按 v0.4 §6.6 SOP 执行 — rsync 本地 → RSS_DATA；DuckDB SHA256 比对一致 + 5 表行数一致 + sync_state 5 cursors 全部 `2026-06-05 / ok`；旧目录重命名为 `data.local-bak-20260611`（保留 1 周）；`.env`（不入仓）/ `.env.example` / `config/launchd/com.quant.data.sync.plist` / `README.md` 的 `DATA_DIR` 全部更新为 `/Volumes/RSS_DATA/quant_data`；路径发现（§6.5）行为不变——DATA_DIR 未设走本地默认，指向 `/Volumes/...` 但卷未挂载走 `warn + 本地回退`。
> - v0.6 (2026-06-05)：Week 2 验收 (ADM-608 done) —— 5 表游标全部 2026-06-05、APScheduler 17:30 + launchd 模板 + 58/58 测试 83.4% coverage；Week 3 (ADM-611) 派发 — A: Portfolio 切 DuckDB + 动量/反转样例 / B: Risk 切 DuckDB + 月频回测 / C: 多 agent 并发读验证（v0.4 §9.6 #6）/ D: 下游接入指南 + 新源 contribution guide（§11 + §12 + README 章节）。
> - v0.5 (2026-06-05)：Week 1 验收 (ADM-606 done) —— 5 表 schema + DuckDB 视图 (mv_daily_v1/qfq/hfq/trade_cal) + 46/46 测试 84% coverage；3 表游标到 2024-01+，daily_basic 因 RateLimit 抛错中断在 2010-01-04；Week 2 (ADM-608) 派发：补全 3 表 + 修 daily_basic RateLimit backoff + APScheduler 17:30 + launchd plist 模板。
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

### 6.6 迁移到外置盘 `RSS_DATA` 的步骤

> **状态（v0.7，2026-06-11）**：迁移已完成。`DATA_DIR` 已切到 `/Volumes/RSS_DATA/quant_data`，本地旧数据保留在 `quant_data/data.local-bak-20260611` 观察 1 周。
> 触发条件：本地数据 >50 GB 或本机 SSD 空间吃紧时迁移。**所有步骤停 scheduler，期间不写**：

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
# 5. **重建视图（关键）**：rsync 只复制 .duckdb 文件，但视图 SQL 体内 read_parquet(...) 的 glob
#    路径是 init 时烘焙的，指向旧 DATA_DIR。必须重跑 bootstrap_views 让视图指向新路径。
DATA_DIR=/Volumes/RSS_DATA/quant_data .venv/bin/python -c \
  "from quant_data.store.duckdb_store import DuckDBStore; DuckDBStore().bootstrap_views()"
# 6. 验证：cli report 中 view_rows 全是 int（不是 "err: IO Error..."）
DATA_DIR=/Volumes/RSS_DATA/quant_data .venv/bin/python -m quant_data.cli report
# 7. 重启 scheduler；首轮跑 test_external_drive.py + test_view_paths_align_with_data_dir.py 验证挂载
.venv/bin/pytest tests/test_external_drive.py tests/test_view_paths_align_with_data_dir.py -v
# 8. 旧本地目录暂留 1 周观察期后删除（避免误删恢复成本）
```

**踩坑（v0.7 现场发现）**：第 5 步的视图重建**不是可选项**。``bootstrap_views`` 创建视图时把 ``@<topic>_tushare@`` 占位符替换成 ``read_parquet('<DATA_DIR>/raw_tushare_<topic>/**/*.parquet')`` 并 ``CREATE VIEW`` 进 ``quant.duckdb``，从那一刻起路径就**被烘焙在 view body 里**了。``rsync -a`` 是文件级 copy，只搬字节，不动 view SQL。所以第 6 步 ``cli report`` 会出现：

```json
"view_rows": {
  "mv_daily_v1": "err: IO Error: No files found that match the pattern \"/Users/.../old path.../raw_tushare_daily/**/*.parquet\"",
  ...
}
```

旧路径的 parquet 已经搬走 / 归档了，自然 IO 错误。修法就是第 5 步重跑 ``bootstrap_views``（用 ``CREATE OR REPLACE VIEW`` 把所有 mv_ 视图替换为新路径）。回归测试 ``tests/test_view_paths_align_with_data_dir.py`` 已就位，未来 ``cli report view_rows`` 出现 ``err:`` 字符串会立刻 fail。

**v0.7 实际执行回执**：

| 步骤 | 命令 | 证据 |
|---|---|---|
| 0. 确认 RSS_DATA 挂载 | `ls /Volumes/RSS_DATA` | `RSS_DATA/` 出现（APFS 卷，466 GiB，1% 已用） |
| 1. 落盘前快照 | `quant_data/.pre_migration_snapshot.json` | 5 表行数 / DuckDB SHA256 / sync_state 5 行 |
| 2. rsync | `rsync -a ~/Code/quant-meta-team/quant_data/data/ /Volumes/RSS_DATA/quant_data/` | 3.9 GB / exit 0 |
| 3. 比对 | SHA256 一致、5 表行数一致、5 cursors 全部 `2026-06-05 / ok` | 迁移后 report 见 §v0.7 |
| 4. 改 env | `.env.example` / `.env` / launchd plist / README 全部 DATA_DIR 切到 RSS_DATA | 4 文件已改 |
| 5. cli 验证 | `DATA_DIR=/Volumes/RSS_DATA/quant_data python -m quant_data.cli report` | `data_dir` = RSS_DATA，5 cursors ok |
| 6. 旧目录归档 | `mv quant_data/data quant_data/data.local-bak-20260611` | 3.9 GB 保留，1 周后未异常再 `rm -rf` |

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
3. **Week 3**：①Portfolio / Risk agent 切到 DuckDB 查询；②写 README + 接入新源指引（`_template.py`）；③跑 7 天压测，收集 `token_calls_per_min` / `cache_hit_rate` 写 L1 经验。Week 3 已派发为 [ADM-611](mention://issue/c4d7e577-5bbe-40a9-8888-90274b4ee5ff)（A+B+C+D），详见 §11 §12。

## 9. 未验证假设（落地前需要 Data agent 实测确认）

1. **tushare 2000 积分档实际限频**：官方文档是 200 req/min / 10 万次/日，但 tushare 在请求体很大（如 daily 全市场 5000+ 行）时仍可能触发隐性 throttle。**需 Data agent 跑 7 天压测，记录 P99 响应时间 + 被限频次数**。
2. **本机 SSD 写吞吐与并发**（v0.4 临时）：Parquet 批量写 50-200 MB/s 是 SSD 估值，**需 Data agent 测 `make sync-full` 的 wall-clock**。后续迁外置盘（HDD/USB 桥接）时再回归测 3-5× 衰减，并确认是否要按月分目录落盘。
3. **本机 → 外置盘迁移的真实耗时**：v0.4 阶段不验证，迁盘时（§6.6）单跑 `rsync -avh` 看 wall-clock；首轮也跑 `duckdb count(*)` 校验行数一致。
4. **DuckDB 单文件超过 ~50 GB 时查询性能**：本项目估算 1-2 GB，但 10 年分钟线 / 全 A 财务全字段可能到几十 GB。**需在跑满 1 年后回归**。
5. **akshare 在登录态（cookie）下是否真能稳定 24/7**：社区说法不一。**需 Data agent 在沙箱跑一周观察**。
6. **停牌日 `high==low` 判定的可靠性**：极少数事件（首日上市、复牌首日）可能误判。**需 Risk agent 在回测前做白名单覆盖**。
7. **tushare 复权因子 vs 通达信/同花顺复权因子的数值一致性**：偶有口径差异（红筹股 / CDR）。**需在 MVP 跑通后抽样 5-10 只股票比对**。
8. **多 agent 并发写 Parquet 时的冲突**：当前规划是 Data 单写、Portfolio/Risk 只读；**若后续多写需加 advisory lock**。
9. **后续新数据源接入是否真的"不改 agent 代码"**：注册新源时需要写 adapter + schema，但 Portfolio / Risk 的 SQL 不能动。**第 3 周专门跑一遍接入 Wind/聚宽的 dry-run 验证**（参见 §12 contribution guide）。

## 11. 下游接入指南（Portfolio / Risk Agent）

> 适用对象：quant-portfolio-agent、quant-risk-agent，以及任何后续接入的下游策略代码。

### 11.1 唯一允许的 import

```python
# ✅ 允许
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.registry import SOURCES, SCHEMAS, get_source, get_schema
from quant_data.sources.base import DataSource, DataStore, TableSchema, FieldSpec

# ❌ 禁止（直连数据源 = 绕过 SourceRegistry，无法复用 lineage / 限频 / 单元统一）
import tushare
import akshare
from tushare import pro_api
from akshare import stock_zh_a_hist
```

> **CI 门禁**：`quant_portfolio/` 与 `quant_risk/` 下 `grep -RE "import tushare|import akshare|from tushare|from akshare"` 必须 0 命中；Week 3 A/B 子 issue DoD 第 3 项已写入此检查。

### 11.2 拿数据：只通过 DuckDBStore.query()

```python
from quant_data.store.duckdb_store import DuckDBStore

store = DuckDBStore(read_only=True)   # Portfolio / Risk 必须 read_only=True
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

要点：
- **不写 SQL 字符串拼 ts_code / 日期**，用 `params=` 传参，避免 SQL 注入 + DuckDB 解析抖动。
- **只读视图**（`mv_*`）优于原始表（`raw_*`）：视图已做单位统一（vol×100 → 股，amount×1000 → 元，qfq 公式）和多源融合。
- **跨日/跨股操作尽量让 DuckDB 做**（窗口函数、滚动、QUALIFY），不要拉到 pandas 再用 `groupby().rolling()`，Ducker 谓词下推 + 列裁剪性能差 10-100×。

### 11.3 可用视图清单（截至 v0.6）

| 视图 | 主键 | 用途 | DoD 引用 |
|------|------|------|----------|
| `mv_daily_v1` | `(ts_code, trade_date)` | 多源融合骨架（tushare ∪ akshare 字段对齐），**生产 SQL 别用这个** | Week 1 |
| `mv_daily_qfq` | `(ts_code, trade_date)` | 前复权 OHLCV + `close_qfq`（Portfolio 选股 / 价量研究） | Week 1 |
| `mv_daily_hfq` | `(ts_code, trade_date)` | 后复权 OHLCV + `close_hfq`（Risk 累计收益 / 回测） | Week 1 |
| `mv_trade_cal` | `(exchange, cal_date)` | 交易日 / 调仓日 / T+1 对齐 | Week 1 |
| `raw_tushare_daily` | `(ts_code, trade_date)` | 不复权原始行情（停牌 / 涨跌停判定用 `high==low && vol==0`） | Week 1 |
| `raw_tushare_adj_factor` | `(ts_code, trade_date)` | 复权因子（验证 qfq/hfq 计算用，不要直接做选股） | Week 1 |
| `raw_tushare_daily_basic` | `(ts_code, trade_date)` | `turnover_rate / pe / pb / total_mv / circ_mv`（Portfolio 价值/流动性因子） | Week 1 |
| `raw_tushare_stock_basic` | `ts_code` | 股票池过滤：`list_status='L'/'D'/'P'`、`delist_date` 截止 | Week 1 |
| `raw_tushare_trade_cal` | `(exchange, cal_date)` | 全部日历（含休市），`is_open=1` 才是交易日 | Week 1 |

### 11.4 数据质量门禁

每次 Portfolio/Risk 输出必须在 comment 报告：
- 股票池大小（start/end 数量是否一致，是否有中途退市）
- 关键字段缺失率（`close_qfq` / `pe` / `circ_mv` 应为 0）
- 时间区间（是否覆盖研究窗口）
- 复权口径（前复权 / 后复权 / 不复权，公式引用 v0.4 §5）
- 停牌/涨跌停跳过数量（Risk 必报）

### 11.5 已知限制（v0.6）

- **沪深 300 成分股未精确化**：当前 Risk agent 用「全 A active(L) + list_date ≥ 2010 + 剔除 ST/退市」近似，等 Wind/聚宽接入（§12.4）后单独开 issue 替换。
- **行业 / 风格中性化未在视图层做**：Portfolio agent 自行在 SQL 或 pandas 层做行业映射。
- **分钟线 / tick 未接入**：v0.6 只覆盖日线；分钟线需要 tushare 单独捐助（~1000 元/年起）并单独开 issue。

## 12. 新数据源 Contribution Guide

> 适用对象：后续接入 Wind / 聚宽 / Choice / 自建 CSV / DBF 等新源时，按本节步骤"复制 + 改 50 行 + 注册 1 行"，Portfolio/Risk 不动。

### 12.1 五步接入法

```
新数据源接入 = 复制 _template → 写 schema → 注册 SOURCES → 补 view → 加测试
                                       (5 行)        (可选, 仅新表)  (~30 行)
```

每步详解：

**Step 1：复制模板**
```bash
cp quant_data/sources/_template.py quant_data/sources/wind.py
```

**Step 2：填 50 行**

| 字段 | 说明 | 例子 |
|------|------|------|
| `name` | 唯一短码 | `"wind"` |
| `version` | adapter 自版本 | `"1.0.0"` |
| `capabilities` | 支持的 topic 集合 | `{"daily", "adj_factor", "fina"}` |
| `rate_limit()` | 上游文档限频 | `RateLimit(requests_per_min=400, notes="wind-doc")` |
| `healthcheck()` | 廉价探活 | `self._client.ping()` |
| `fetch(topic, **params)` | 实际拉取；必须返回与对应 `TableSchema` 字段名一致的 DataFrame | 见 §5 字段口径 |

**Step 3：注册（一行）**

```python
# quant_data/registry.py
def _build_default_sources() -> dict[str, object]:
    return {
        "tushare": TushareAdapter(pro_token=token, tier=2000),
        "wind": WindAdapter(endpoint=os.getenv("WIND_ENDPOINT"), ...),  # ← 新加这一行
    }
```

**Step 4：写 schema（仅当引入新表）**

- 复用现有 5 张表 → 跳过本步
- 引入新 topic（如 `fina` / `index_daily`）→ 在 `quant_data/schemas/<topic>_v1.py` 写 `TableSchema` 实例，并在 `quant_data/schemas/__init__.py` 的 `SCHEMAS` dict 注册

**Step 5：DuckDB view 增量补一节（多源融合场景）**

如果新源是**同 topic 增量补充**（如 wind 的 daily 字段比 tushare 多几个）→ 改 `quant_data/views/mv_daily_v1.sql`，加 `LEFT JOIN` + `COALESCE`；下游视图 `mv_daily_qfq` 不动（v0.4 §3.3）。

如果新源是**新 topic**（如 `fina`）→ 新建 `quant_data/views/mv_fina_v1.sql`，在 `DuckDBStore.bootstrap_views()` 自动拾取（`view_dir.glob("*.sql")`）。

**Step 6：测试**

```python
# tests/test_registry.py 加一例
def test_wind_adapter_healthcheck(monkeypatch):
    monkeypatch.setenv("WIND_ENDPOINT", "ws://mock")
    register_source("wind", WindAdapter(endpoint="ws://mock"))
    assert get_source("wind").healthcheck() is True
```

`test_registry.py` 现有用例已覆盖"注册新源不改 Portfolio/Risk 代码"——加新源后跑一遍全绿即可。

### 12.2 字段口径差异处理

新源字段名/单位与 tushare 不一致时，**不要在 SQL 层做转换**，改在 `TableSchema.source_mapping`：

```python
# quant_data/schemas/daily_v1.py
DAILY_V1 = TableSchema(
    table="daily", version="v1",
    primary_key=["ts_code", "trade_date"],
    fields={"open": FieldSpec("open", "float64", "yuan", ...), ...},
    source_mapping={
        "tushare": {"open": "open", "vol": "vol", "amount": "amount"},
        "wind":    {"open": "open", "vol": "volume", "amount": "amt"},  # ← wind 用 volume/amt
    },
)
```

`TushareAdapter` 读自己的 `source_mapping["tushare"]`，未来的 `WindAdapter` 读 `source_mapping["wind"]`；SQL 层一律用规范名（`open`/`vol`/`amount`）。

### 12.3 DoD 清单（接入新源必跑）

- [ ] `tests/test_registry.py` 加新源用例全绿
- [ ] `quant_data/sources/<name>.py` 不引入 tushare / akshare 反向依赖
- [ ] `docs/data-localization.md` §6.2 表格加一行（新源 + 限频 + 备注）
- [ ] `docs/data-localization.md` §3.2 核心表加新源写入路径
- [ ] 跑 `make sync-full` 验证不破坏 5 表游标
- [ ] `multica-memory` hot tier 记一条 L1 经验（限频 / 字段差异 / 健康检查）

### 12.4 接入 Wind / 聚宽 dry-run 步骤（不实跑，写清单）

仅作路线图，不在本 issue 实施。

**Wind 接入（假设万得终端 + WindPy SDK）**：
1. `pip install WindPy`（仅 Windows；macOS 用 `pip install windpy-sim`）
2. `quant_data/sources/wind.py` 复制 `_template.py`，`fetch()` 内调 `w.wsd("000001.SZ", "open,high,low,close,volume,amt", start, end, ...)`
3. `rate_limit = RateLimit(requests_per_min=400, notes="wind-doc-2024")`
4. `source_mapping = {"wind": {"vol": "volume", "amount": "amt"}}`
5. 写 schema 注册 `WIND_DAILY_V1 = TableSchema(...)`（**新表**，与 DAILY_V1 并存）
6. DuckDB 视图 `mv_daily_wind_v1` 单源视图（v0.4 §3.3 多源融合 v2 时再考虑合表）

**聚宽接入（jqdatasdk）**：
1. `pip install jqdatasdk` + 账号认证
2. `quant_data/sources/joinquant.py` 复制 `_template.py`，`fetch()` 调 `jq.get_price(...)`
3. `rate_limit = RateLimit(requests_per_min=200, notes="jq-pro-2024")`（聚宽 Pro 文档）
4. `source_mapping = {"joinquant": {"vol": "volume", "amount": "money"}}`
5. 同上

**CSV / DBF 自建源**：
1. `quant_data/sources/csv.py`，`fetch()` 用 `pd.read_csv()` 即可
2. 限频 = 0（本地读），`RateLimit(requests_per_min=10**9, notes="local-fs")`
3. 主键按文件结构写 schema，注意 timezone（CSV 通常是字符串日期，schema 注册 `dtype="date32"`，adapter 负责 `pd.to_datetime()`）

### 12.5 接入新源 = Portfolio/Risk 零改动

验证方法（Week 3 [ADM-611](mention://issue/c4d7e577-5bbe-40a9-8888-90274b4ee5ff) DoD 第 6 项落地后跑一次）：

```bash
git diff --stat HEAD~10 -- quant_portfolio/ quant_risk/   # 新源 commit 前后, 这两个目录应该 0 行变更
pytest tests/test_portfolio_factor.py tests/test_risk_backtest.py  # 全绿
```

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
