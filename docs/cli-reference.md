# `quant_data` CLI Reference (v0.9)

> Stable entry point for all data-side operations. Replaces the v0.7 inline
> `--help` and the 6 ad-hoc subcommands.

## Synopsis

```bash
python -m quant_data.cli [--data-dir PATH] [--quiet|--no-quiet] [--verbose]
                         [--json] [--dry-run] [--i-know-what-im-doing]
                         <command> [command-options]
```

Global options must appear **before** the subcommand name (standard Click
convention; documented below in §1.5).

## Subcommands at a glance (v0.9.0)

| Command             | Purpose                                           | DoD § |
|---------------------|---------------------------------------------------|-------|
| `init`              | Create data dir, register schemas, bootstrap views | 1.1   |
| `sync-full`         | Backfill all configured tushare tables for full A-share history | 1.1   |
| `sync-daily`        | Incremental daily sync (today - lookback)         | 1.1   |
| `sync-table <topic>`| Single-table incremental sync                     | 1.1   |
| `sync-range`        | Sync a custom date range (default: all configured topics)  | 1.1   |
| `report`            | Legacy: row counts + cursors + disk + lineage     | 1.1   |
| `run-once`          | One-shot sweep across all configured topics (used by launchd) | 1.1   |
| `serve-scheduler`   | Block on 17:30 weekday APScheduler                | 1.1   |
| `list-tables`       | List all configured raw tables with row count, range, cursor | 1.1   |
| `list-views`        | List all configured mv_* views with row count     | 1.1   |
| `list-sources`      | List registered data sources (tushare / akshare)  | 1.1   |
| `status`            | Lightweight health check (no network)             | 1.1   |
| `diff`              | Row + SHA256 compare against another DATA_DIR     | 1.1   |
| `query`             | Read-only SQL (parametrized; DDL/DML blocked)     | 1.1   |
| `doctor`            | One-click self-check with recommendations         | 1.1   |
| `completion <shell>`| Print shell completion script (bash / zsh / fish) | 1.6   |

## 1. Global options

| Flag                          | Default          | Description |
|-------------------------------|------------------|-------------|
| `--data-dir PATH`             | `$DATA_DIR` env  | Override the data root. If the path is not under `/Volumes/RSS_DATA`, a soft warning is printed to stderr. Silence it with `--i-know-what-im-doing`. |
| `--quiet` / `--no-quiet`      | `--no-quiet`     | Drop log level to WARNING (suppresses INFO). |
| `--verbose`                   | `0`              | Add `INFO` once, `DEBUG` twice. Repeatable (e.g. `--verbose --verbose`). |
| `--json`                      | off              | Emit a **versioned envelope** (see §3) instead of the human-readable summary. |
| `--dry-run`                   | off              | `init` / `sync-*` commands do not call the network; they print what *would* run. |
| `--i-know-what-im-doing`      | off              | Acknowledge the `--data-dir` safety warning. |

### 1.5 Why global options go before the subcommand

In Click 8.x, options on a `Group` are only recognized **before** the
subcommand name. Therefore:

```bash
# ✓ canonical
python -m quant_data.cli --json list-tables
python -m quant_data.cli --data-dir /tmp/foo init

# ✗ NOT supported (Click parser will reject)
python -m quant_data.cli list-tables --json
```

This is consistent with `git`, `kubectl`, `aws`, and most modern CLIs.

### 1.6 Output

- **Human mode (default)**: the command's `data` payload is printed as
  pretty-printed JSON. Any error appears as `ERROR: <msg>` on stderr. With
  `--verbose`, an envelope summary line is appended to stderr.
- **`--json` mode**: a single JSON object on stdout. See §3 for the schema.

## 2. Exit codes

| Code | Meaning                                                              |
|------|----------------------------------------------------------------------|
| `0`  | ok                                                                   |
| `1`  | generic / unhandled error                                            |
| `2`  | partial sync failure (one or more topics failed; see report)         |
| `3`  | blocked (DATA_DIR missing, TUSHARE_TOKEN unset, view missing)        |
| `4`  | rate-limit hit repeatedly (must `@mention` the human)                |
| `5`  | data quality gate failed (`diff` mismatch, `doctor` red flag)        |

Scripts can rely on the exit code alone; the human / JSON body is for
diagnostics.

## 3. Versioned JSON envelope

```json
{
  "cli_version": "0.8.0",
  "command": "list-tables",
  "ok": true,
  "exit_code": 0,
  "error": null,
  "data": { ... },
  "ts": "2026-06-11T10:33:39+08:00"
}
```

Field guarantees (v0.8.0):
- `cli_version` is always present and follows `x.y.z`. **Bumping it** is the
  contract that announces a breaking change in the envelope shape.
- `command` is the subcommand name (no global-flag prefix).
- `ok` is `true` iff `exit_code == 0` AND `error` is null.
- `data` is the actual command payload (its shape varies per command).
- `ts` is a parseable ISO 8601 timestamp in the local timezone.

## 4. Subcommand reference

### 4.1 `init`

Create the data dir, register schemas, and bootstrap DuckDB views.

- **Syntax**: `python -m quant_data.cli [--data-dir PATH] init`
- **Exit codes**: `0` (ok), `3` (DATA_DIR not writable).
- **Example (human)**:
  ```bash
  python -m quant_data.cli init
  # data_dir=/Volumes/RSS_DATA/quant_data
  # duckdb=/Volumes/RSS_DATA/quant_data/quant.duckdb
  # {"data_dir": ..., "duckdb": ..., "views": [...]}
  ```
- **Example (JSON)**:
  ```bash
  python -m quant_data.cli --json init | jq .data.views
  ```
- **Common error**: `data_dir not writable` → exit 3; check `--data-dir`
  or `DATA_DIR` env, and confirm the parent dir exists.

### 4.2 `sync-full`

Backfill 5 tushare tables for the full A-share history window.

- **Syntax**: `python -m quant_data.cli sync-full`
- **Exit codes**: `0` (ok), `2` (one or more topics failed).
- **Example**:
  ```bash
  python -m quant_data.cli --json sync-full > sync_full_report.json
  ```
- **Common error**: `TUSHARE_TOKEN not set` → exit 3.

### 4.3 `sync-daily`

Incremental sync of all configured tables covering the last `--lookback` days.

- **Options**:
  - `--lookback N` — days to look back from today (default 5).
- **Exit codes**: `0` (ok), `2` (partial failure).
- **Example**:
  ```bash
  python -m quant_data.cli --json sync-daily --lookback 7
  ```

### 4.4 `sync-table <topic>`

Single-table incremental sync. Topics: `stock_basic`, `trade_cal`,
`daily`, `adj_factor`, `daily_basic`.

- **Options**:
  - `--start YYYYMMDD` — lower bound (cursor takes precedence).
  - `--end YYYYMMDD` — upper bound.
- **Exit codes**: `0` (ok), `2` (sync failed), `1` (bad date).
- **Example**:
  ```bash
  # backfill 2024 Q1 for adj_factor
  python -m quant_data.cli --json sync-table adj_factor --start 20240101 --end 20240331
  ```
- **Common error**: `bad date: invalid literal for int() with base 10: 'X'`
  → exit 1; the `--start` / `--end` strings must be YYYYMMDD.

### 4.5 `sync-range`

Sync a custom date range across all (or `--only` subset) tables. Used by
[ADM-640](mention://issue/7c725362-0f5e-4545-a0f4-f8dd77164b11) for the 20-year
backfill.

- **Options**:
  - `--start YYYYMMDD` — **required**.
  - `--end YYYYMMDD` — **required**.
  - `--only t1,t2` — comma-separated topic subset (default: all configured topics).
- **Exit codes**: `0`, `2` (partial failure), `1` (bad date / start > end).
- **Example**:
  ```bash
  # 5-year backfill on just daily + adj_factor
  python -m quant_data.cli --json sync-range \
      --start 20200101 --end 20251231 --only daily,adj_factor
  ```
- **Common error**: `start > end` → exit 1.

### 4.6 `report` (legacy)

Print row counts + cursor state + lineage + disk usage. Kept for backward
compatibility with Week 1-4 issue acceptance scripts.

- **Syntax**: `python -m quant_data.cli report`
- **Exit codes**: `0`.
- **Example**:
  ```bash
  python -m quant_data.cli --json report | jq .data.cursors
  ```

### 4.7 `run-once`

One-shot sweep across all configured topics. Used by `launchd` / `cron` to drive the daily
sync. Exits 2 if any topic fails — that's the launchd signal to alert.

- **Options**:
  - `--lookback N` — days to look back for time-series tables (default 1).
  - `--dry-run` / `--no-dry-run` — log topics only, no network.
  - `--only t1,t2` — comma-separated topic subset.
- **Exit codes**: `0` (ok), `2` (partial failure).
- **Example**:
  ```bash
  # 17:30 launchd job
  python -m quant_data.cli run-once --lookback 1 --only daily,adj_factor
  ```

### 4.8 `serve-scheduler`

Block on APScheduler with a 17:30 weekday cron trigger. In `--json` mode
prints the config and exits without blocking (useful for CI smoke tests).

- **Options**:
  - `--hour`, `--minute` — trigger time (default 17:30).
  - `--day-of-week` — APScheduler expression (default `mon-fri`).
  - `--lookback` — days to look back (default 1).
  - `--dry-run` / `--no-dry-run`.
- **Exit codes**: `0` (in `--json` mode) or process exit on signal.
- **Example**:
  ```bash
  # CI smoke test
  python -m quant_data.cli --json serve-scheduler
  # {"data": {"would_block": true, "config": {...}}}
  ```

### 4.9 `list-tables`

List 5 raw tables (`raw_tushare_*`) with row count, min/max date, and the
cursor state.

- **Syntax**: `python -m quant_data.cli list-tables`
- **Exit codes**: `0`.
- **Example (JSON)**:
  ```bash
  python -m quant_data.cli --json list-tables | jq '.data.tables[] | select(.raw == "raw_tushare_daily")'
  ```
  ```json
  {
    "raw": "raw_tushare_daily",
    "rows": 17584231,
    "min_date": "2010-01-04",
    "max_date": "2026-06-05",
    "cursor_last_trade_date": "2026-06-05",
    "cursor_status": "ok",
    "cursor_last_run_at": "2026-06-11T17:30:42"
  }
  ```
- **Common error**: empty `min_date` / `max_date` → table is empty (no
  data synced yet); run `init` then `sync-range` or `sync-table`.

### 4.10 `list-views`

List 5 `mv_*` views with row count.

- **Syntax**: `python -m quant_data.cli list-views`
- **Exit codes**: `0`.
- **Example**:
  ```bash
  python -m quant_data.cli --json list-views
  ```

### 4.11 `list-sources`

List registered data sources (tushare eager, akshare lazy).

- **Syntax**: `python -m quant_data.cli list-sources`
- **Exit codes**: `0`.
- **Example**:
  ```bash
  python -m quant_data.cli --json list-sources | jq '.data.sources[].name'
  # "tushare"
  # "akshare"
  ```

### 4.12 `status`

Lightweight health snapshot. **Does not** call the network.

- **Returns**: `data_dir`, `data_dir_exists`, `duckdb_path`, `disk_free_gb`,
  `cursors`, `cursor_health`, `rate_limit_hit_total`.
- **Exit codes**: `0`.
- **Example**:
  ```bash
  python -m quant_data.cli --json status | jq '.data | {disk_free_gb, cursor_health}'
  ```

### 4.13 `diff --against <other_data_dir>`

Compare row counts + SHA256 (first 5 files per topic) between the current
`data_dir()` and `--against`. Used for migration validation.

- **Options**:
  - `--against PATH` — **required** other DATA_DIR.
- **Exit codes**: `0` (no diff), `5` (row or hash mismatch), `3` (other
  root missing).
- **Example**:
  ```bash
  python -m quant_data.cli --json diff --against /Volumes/RSS_DATA_BACKUP/quant_data
  ```
- **Common error**: `other root /path does not exist` → exit 3.

### 4.14 `query --sql "<SELECT ...>"`

Convenient read-only SQL against `mv_*` / `raw_*` views. **DDL/DML is
blocked** (DROP, DELETE, UPDATE, INSERT, CREATE, ALTER, TRUNCATE,
REPLACE, ATTACH, DETACH, COPY, EXPORT, IMPORT, CALL, LOAD, INSTALL,
GRANT, REVOKE).

- **Options**:
  - `--sql` — **required**. Single statement; trailing `;` is stripped.
- **Allowed verbs**: `SELECT`, `WITH`, `SHOW`, `DESCRIBE`, `EXPLAIN`.
- **Scope**: every `FROM` / `JOIN` identifier must start with `mv_` or
  `raw_`.
- **Exit codes**: `0` (ok), `1` (forbidden keyword / out-of-scope /
  duckdb error).
- **Example (Risk agent pulling a daily count)**:
  ```bash
  python -m quant_data.cli --json query \
      --sql "SELECT count(*) FROM mv_daily_qfq WHERE trade_date='2025-12-31'"
  ```
- **Common error**: `forbidden keyword 'DROP'` → exit 1; the command
  protects against accidental schema changes.

### 4.15 `doctor`

One-click self-check covering 6 categories: `data_dir`, `disk_free`,
`tushare_token`, `views`, `cursors`, `rate_limit`. Returns
`recommendations` for any failing check.

- **Exit codes**: `0` (all green), `3` (blocked), `5` (quality gate).
- **Example**:
  ```bash
  python -m quant_data.cli --json doctor
  ```
  ```json
  {
    "cli_version": "0.8.0",
    "command": "doctor",
    "ok": false,
    "exit_code": 5,
    "data": {
      "checks": [
        {"name": "data_dir", "ok": true, "detail": "/Volumes/RSS_DATA/quant_data"},
        {"name": "disk_free", "ok": true, "detail": "126.4 GB free at ..."},
        {"name": "tushare_token", "ok": true, "detail": "set"},
        {"name": "views", "ok": true, "detail": "present=[...] missing=[]"},
        {"name": "cursors", "ok": false, "detail": "present=3/5 bad=[]"},
        {"name": "rate_limit", "ok": true, "detail": "cumulative hits=0"}
      ],
      "recommendations": [
        "run `python -m quant_data.cli init` then `sync-table` to seed cursors"
      ]
    }
  }
  ```
- **Common error**: `TUSHARE_TOKEN env not set` → exit 3. Run
  `export TUSHARE_TOKEN=...` then re-run.

### 4.16 `completion {bash|zsh|fish}`

Emit a static shell completion script. Sources back into the CLI at
completion time via `eval "$PROG completion bash)"`.

- **Example**:
  ```bash
  # bash
  python -m quant_data.cli completion bash > ~/.quant_data-completion.bash
  echo 'source ~/.quant_data-completion.bash' >> ~/.bashrc

  # zsh
  python -m quant_data.cli completion zsh > "${fpath[1]}/_python_m_quant_data_cli"
  ```

- **Exit codes**: `0` (ok), `1` (unsupported shell — only `bash`, `zsh`,
  `fish` are valid).

## 5. End-to-end recipes

### 5.1 Bootstrap a fresh machine

```bash
export DATA_DIR=/Volumes/RSS_DATA/quant_data
export TUSHARE_TOKEN=...
python -m quant_data.cli init
python -m quant_data.cli --json doctor     # expect exit 0
python -m quant_data.cli --json sync-range --start 20100101 --end $(date +%Y%m%d)
```

### 5.2 Run a smoke check before launchd kicks in

```bash
python -m quant_data.cli --json run-once --dry-run --only daily
python -m quant_data.cli --json doctor
```

### 5.3 Compare two DATA_DIRs after a migration

```bash
python -m quant_data.cli --json diff --against /Volumes/RSS_DATA_BACKUP/quant_data
# expect exit 0; non-zero → investigate "tables[].row_match == false"
```

### 5.4 One-liner for Risk agent (Week 4 [ADM-619](mention://issue/12024c50-b2e2-42e0-8483-86f3557ebfe6))

```bash
# pull the qfq count for a given date — read-only, mv_* scoped
python -m quant_data.cli --json query \
    --sql "SELECT count(*) FROM mv_daily_qfq WHERE trade_date='2025-12-31'"
```

## 6. Backward compatibility (DoD §8)

The 6 legacy subcommands (`init`, `sync-full`, `sync-daily`, `run-once`,
`serve-scheduler`, `report`) keep their default behaviour and JSON keys.
Week 1-4 issue acceptance scripts that grep `data.raw_rows` /
`data.view_rows` / `data.cursors` continue to pass without modification.

## 7. See also

- `docs/data-localization.md` v0.7 — design doc, including §6.4 rate-limit
  and §6.5 DATA_DIR fallback.
- `quant_data/cli_support.py` — implementation of the versioned envelope
  and the diagnostic helpers.
- `tests/test_cli.py` — 60+ tests covering the DoD §7 acceptance set.
