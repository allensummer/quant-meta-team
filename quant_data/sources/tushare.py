"""Tushare Pro adapter (Layer 1).

Tier-aware: the documented 2000-points ceiling is 200 req/min, 100k req/day.
We use the tushare ``pro`` SDK behind our ``DataSource`` Protocol so downstream
code never imports tushare directly.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import date, datetime
from typing import Any

import pandas as pd

from quant_data.rate_limit import TokenBucket
from quant_data.sources.base import DataSource, LineageRecord, RateLimit

log = logging.getLogger("quant_data.sources.tushare")


class TushareAdapter:
    name = "tushare"
    version = "1.0.0"  # adapter version, distinct from schema version
    capabilities = {
        "stock_basic", "trade_cal", "daily", "adj_factor", "daily_basic",
        # S-tier additions (v0.8 — ADM-652)
        "moneyflow", "moneyflow_hsgt", "index_weight", "hsgt_top10", "fund_holdings",
        # A-tier additions (v0.9 — ADM-653): 20 interfaces across 3 batches
        # Batch 1 — 基础 + 事件 (8)
        "index_classify", "index_daily", "index_member", "sw_index",
        "stk_limit", "suspend", "dividend", "shares_float",
        # Batch 2 — 财务三联表 + 财务指标 (7)
        "fina_indicator", "income", "balancesheet", "cashflow",
        "fina_mainbz", "fina_audit", "top10_holders",
        # Batch 3 — 资金流 + 研报 + 股东 (5)
        "top_list", "margin_detail", "top10_floatholders",
        "stk_holdertrade", "report_rc",
        # pro_bar minutes — 403 on 2000 积分档, do not include
    }

    def __init__(self, pro_token: str, tier: int = 2000):
        self._token = pro_token
        self._tier = tier
        # 2000 积分档: 200 req/min
        self._rl = TokenBucket(RateLimit(requests_per_min=200, notes="tushare-2000"))
        self._pro = None
        self._health_ok: bool | None = None

    # ---------- DataSource protocol ----------
    def rate_limit(self) -> RateLimit:
        return RateLimit(requests_per_min=200, requests_per_day=100_000, notes=f"tushare-{self._tier}")

    def healthcheck(self) -> bool:
        """Cheap ``trade_cal`` ping; does not count against real sync rows."""
        try:
            df = self._call("trade_cal", start_date="20240101", end_date="20240102")
            ok = not df.empty
            self._health_ok = ok
            return ok
        except Exception as e:  # pragma: no cover
            log.warning("tushare healthcheck failed: %s", e)
            self._health_ok = False
            return False

    def fetch(self, topic: str, **params: Any) -> pd.DataFrame:
        """Dispatch to the right ``pro`` method. Returns canonical-columned DataFrame.

        For ``daily`` / ``adj_factor`` / ``daily_basic`` we always pass
        ``trade_date=`` (NOT ``ts_code=``) — that's the 1-req-per-day contract
        documented in v0.4 §4.1.
        """
        if topic not in self.capabilities:
            raise ValueError(f"tushare adapter: unsupported topic {topic!r}")
        df = self._call(topic, **params)
        return self._canonicalize(topic, df)

    # ---------- internals ----------
    @property
    def pro(self):
        if self._pro is None:
            if not self._token:
                raise RuntimeError("TUSHARE_TOKEN not set — cannot construct pro api")
            import tushare as ts
            ts.set_token(self._token)
            self._pro = ts.pro_api()
            # tushare's pro_api uses module-level ``requests.post(...)`` which
            # picks up system-level proxies (e.g. macOS 127.0.0.1:1087). Those
            # proxies can be unreachable from inside a venv. We monkey-patch
            # ``requests.post`` and ``requests.get`` to bypass the proxy.
            self._patch_requests_no_proxy()
        return self._pro

    def _patch_requests_no_proxy(self) -> None:
        """Force every ``requests.post``/``get`` call in this process to ignore proxies.

        Idempotent: only patches once.
        """
        if getattr(self, "_proxy_patched", False):
            return
        import requests
        real_post = requests.post
        real_get = requests.get

        def post_no_proxy(*args, **kwargs):
            kwargs.setdefault("proxies", {"http": "", "https": ""})
            return real_post(*args, **kwargs)

        def get_no_proxy(*args, **kwargs):
            kwargs.setdefault("proxies", {"http": "", "https": ""})
            return real_get(*args, **kwargs)

        requests.post = post_no_proxy
        requests.get = get_no_proxy
        # also patch the api module functions (tushare may import them directly)
        try:
            import requests.api as _api
            _api.post = post_no_proxy
            _api.get = get_no_proxy
        except Exception:
            pass
        self._proxy_patched = True
        log.debug("tushare adapter: requests proxy bypass installed")

    # Maximum number of retries on a server-side 429 / rate-limit error.
    # tushare sometimes returns 429 even with our 80% client-side throttle
    # (large responses, midday spikes). We back off and try again before
    # bubbling up — this is the v0.5 §4.3 fix that prevents daily_basic from
    # dying mid-history.
    _MAX_429_RETRIES = 5
    _BASE_BACKOFF_S = 2.0

    def _is_rate_limit_error(self, exc: BaseException) -> bool:
        """Best-effort detect: tushare / requests 429 / Chinese rate-limit strings."""
        msg = str(exc) or ""
        # tushare raises ``requests.HTTPError``; sometimes ``RuntimeError`` with the
        # body text. Look for the well-known markers regardless of exception class.
        markers = ("429", "rate limit", "超出频率", "超过频率", "访问频率",
                   "too many requests", "每小时访问", "请降低请求频率")
        return any(m.lower() in msg.lower() for m in markers)

    # Map our internal topic -> upstream tushare pro API method name.
    # Most topics match exactly; the few that differ are listed below.
    # ``shares_float`` -> ``share_float`` (singular on tushare).
    TOPIC_TO_PRO_API: dict[str, str] = {
        "shares_float": "share_float",
    }

    # Map our public-facing param name -> upstream tushare param name.
    # We accept the canonical names our callers use; if the upstream uses
    # a different name we transparently rename. Currently:
    #   ``index_member`` callers can pass ``index_code`` (we translate to ``idx_code``).
    TOPIC_PARAM_RENAMES: dict[str, dict[str, str]] = {
        "index_member": {"index_code": "idx_code"},
    }

    # Topics that the 2000 积分档 cannot access (server-side returns
    # "请指定正确的接口名"). On higher tiers they would work.
    TIER_BLOCKED: frozenset[str] = frozenset({
        "sw_index",       # 申万行业指数 — 需 ≥5000 积分档
        "sw_index_daily", # 同上
    })

    def _call(self, topic: str, **params: Any) -> pd.DataFrame:
        """Throttled call into tushare; preserves source-native column names.

        Server-side 429s are retried with exponential backoff + jitter (capped
        at ``_MAX_429_RETRIES``). After that, the error is re-raised so the
        driver can record the failed date and resume picks it up next run.
        """
        if topic in self.TIER_BLOCKED and self._tier < 5000:
            raise RuntimeError(
                f"tushare topic {topic!r} is tier-blocked on {self._tier} 积分档 "
                f"(需 ≥5000 积分档). Marked in TIER_BLOCKED; will not retry."
            )
        upstream = self.TOPIC_TO_PRO_API.get(topic, topic)
        method = getattr(self.pro, upstream, None)
        if method is None:
            raise RuntimeError(f"tushare pro has no method named {upstream!r} (topic={topic!r})")
        # Translate param names if the topic has a public/upstream mapping
        # (e.g. our callers pass ``index_code``; upstream wants ``idx_code``).
        renames = self.TOPIC_PARAM_RENAMES.get(topic, {})
        if renames:
            params = {renames.get(k, k): v for k, v in params.items()}
        t0 = time.monotonic()
        last_exc: BaseException | None = None
        for attempt in range(self._MAX_429_RETRIES + 1):
            self._rl.acquire()
            try:
                df = method(**params)
            except Exception as e:  # noqa: BLE001 — tushare raises mixed types
                if not self._is_rate_limit_error(e):
                    raise
                last_exc = e
                if attempt >= self._MAX_429_RETRIES:
                    log.error("tushare %s: 429 after %d retries, giving up: %s",
                              topic, self._MAX_429_RETRIES, e)
                    raise
                # Exponential backoff: 2,4,8,16,32s + 0-1s jitter.
                backoff = self._BASE_BACKOFF_S * (2 ** attempt) + (time.time() % 1.0)
                self._rl.rate_limit_hit += 1
                log.warning("tushare %s hit server 429 (attempt %d/%d), sleeping %.2fs",
                            topic, attempt + 1, self._MAX_429_RETRIES, backoff)
                time.sleep(backoff)
                continue
            elapsed = time.monotonic() - t0
            if df is None or df.empty:
                log.info("tushare %s(%s) -> 0 rows in %.2fs", topic, params, elapsed)
                return pd.DataFrame()
            log.info("tushare %s -> %d rows in %.2fs", topic, len(df), elapsed)
            return df
        # Shouldn't reach here, but keep mypy happy.
        assert last_exc is not None
        raise last_exc

    def _canonicalize(self, topic: str, df: pd.DataFrame) -> pd.DataFrame:
        """No-op: tushare column names already match our schema. Kept for symmetry."""
        if df.empty:
            return df
        # date string -> date for known date fields. Handle both object-dtype
        # (pandas <2) and StringDtype (pandas >=2) since tushare returns YYYYMMDD.
        for col in ("trade_date", "cal_date", "list_date", "delist_date", "pretrade_date"):
            if col in df.columns:
                dt = df[col].dtype
                if dt == object or pd.api.types.is_string_dtype(df[col]):
                    df[col] = pd.to_datetime(df[col], format="%Y%m%d", errors="coerce").dt.date
        return df

    def lineage(self, table: str, schema_version: str, params: dict, rows: int) -> LineageRecord:
        return LineageRecord(
            table=table,
            schema_version=schema_version,
            source=self.name,
            source_version=f"tushare-pro-{self._tier}",
            fetched_at=datetime.now().astimezone(),
            params=params,
            rows=rows,
            rate_limit_hit=self._rl.rate_limit_hit,
            request_id=str(uuid.uuid4()),
        )
