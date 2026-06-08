"""AkshareAdapter coverage: unit conversion (akshare volume/amount -> tushare space)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from quant_data.sources.akshare import _akshare_to_canonical_daily


def test_akshare_volume_is_in_shares_tushare_uses_lots():
    """akshare stock_zh_a_hist returns volume in shares; tushare vol is in 100-share lots."""
    df = pd.DataFrame({
        "日期": ["2024-01-02", "2024-01-03"],
        "股票代码": ["000001", "000001"],
        "开盘": [10.0, 10.5],
        "最高": [10.5, 11.0],
        "最低": [9.5, 10.0],
        "收盘": [10.0, 10.5],
        "成交量": [100000.0, 200000.0],   # shares
        "成交额": [1000000.0, 2200000.0], # yuan
    })
    out = _akshare_to_canonical_daily(df)
    # vol must be in lots (vol_share / 100)
    assert out["vol"].iloc[0] == 1000.0
    # amount must be in 千元 (yuan / 1000)
    assert out["amount"].iloc[0] == 1000.0
    # trade_date parsed
    assert out["trade_date"].iloc[0] == date(2024, 1, 2)


def test_akshare_adapter_lists_capabilities():
    from quant_data.sources.akshare import AkshareAdapter
    a = AkshareAdapter()
    assert "daily" in a.capabilities
    assert "stock_basic" in a.capabilities
    rl = a.rate_limit()
    assert rl.requests_per_min <= 60  # conservative vs upstream


def test_akshare_unsupported_topic_raises():
    from quant_data.sources.akshare import AkshareAdapter
    a = AkshareAdapter()
    try:
        a.fetch("not_a_topic")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_akshare_healthcheck_handles_exception(monkeypatch):
    """When akshare is not installed or upstream is down, healthcheck returns False."""
    from quant_data.sources.akshare import AkshareAdapter

    # Force the import inside healthcheck to blow up
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *a, **kw):
        if name == "akshare":
            raise ImportError("blocked")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    a = AkshareAdapter()
    assert a.healthcheck() is False
