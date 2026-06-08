"""Path & config helpers for quant_data.

Implements the local-first + external-drive fallback described in
`docs/data-localization.md` v0.4 §6.5.

Rules
-----
- DATA_DIR is read from env. If unset -> project-local default
  ``~/Code/quant-meta-team/quant_data/data``.
- If DATA_DIR is explicitly set to a /Volumes/... path that does not exist
  (e.g. external drive not mounted), fall back to the local default and emit
  a warning. (v0.4: relaxed from ``blocked`` to ``warn + local``.)
- We never hardcode /Users/... or ~/... in any other module — they all reach
  paths through the helpers in this file.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("quant_data.paths")

LOCAL_DEFAULT = Path.home() / "Code" / "quant-meta-team" / "quant_data" / "data"


def data_dir() -> Path:
    """Return the writable data root, honoring DATA_DIR with local fallback.

    v0.4 §6.5:
      - DATA_DIR unset -> local default + info log.
      - DATA_DIR set + exists -> use it.
      - DATA_DIR set + starts with /Volumes/ + missing -> fallback + warn.
      - DATA_DIR set + non-Volumes + missing -> create it.
    """
    env = os.getenv("DATA_DIR")
    if not env:
        p = LOCAL_DEFAULT
        p.mkdir(parents=True, exist_ok=True)
        log.info("DATA_DIR 未设置，使用本地默认 %s", p)
        return p

    p = Path(env).expanduser()
    if p.exists():
        return p

    if str(p).startswith("/Volumes/"):
        fallback = LOCAL_DEFAULT
        fallback.mkdir(parents=True, exist_ok=True)
        log.warning(
            "DATA_DIR %s 显式指向外挂盘但挂载缺失，回退到本地 %s（v0.4 降级策略）",
            p,
            fallback,
        )
        return fallback

    # Non-Volumes: best-effort create. Lets users point DATA_DIR at any writable dir.
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    """LOG_DIR is small + high-frequency writes; always local (v0.4 §6.5)."""
    env = os.getenv("LOG_DIR")
    p = Path(env).expanduser() if env else (Path.home() / "Code" / "quant-meta-team" / "logs")
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    """Derived cache dir under DATA_DIR/cache."""
    p = data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def meta_dir() -> Path:
    """SQLite + lineage metadata root."""
    p = data_dir() / "meta"
    p.mkdir(parents=True, exist_ok=True)
    (p / "_lineage").mkdir(parents=True, exist_ok=True)
    return p


def duckdb_path() -> Path:
    return data_dir() / "quant.duckdb"


def sqlite_path() -> Path:
    return meta_dir() / "sync.sqlite"


def raw_root() -> Path:
    """Root of all ``raw_<source>_<topic>`` Hive-partitioned Parquet trees.

    Each ParquetStore mounts as a direct child: ``data_dir/raw_tushare_daily/``.
    """
    return data_dir()
