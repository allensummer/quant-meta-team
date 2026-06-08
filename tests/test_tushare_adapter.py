"""TushareAdapter coverage: rate-limit, canonicalization, healthcheck, lineage.

We mock ``tushare.pro_api`` so no real network is touched.
"""
from __future__ import annotations

import os
from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from quant_data.sources.tushare import TushareAdapter


@pytest.fixture
def mock_pro():
    """Patch tushare.pro_api so the adapter never hits the network."""
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        yield pro


def test_rate_limit_uses_2000_tier_ceiling():
    a = TushareAdapter(pro_token="x", tier=2000)
    rl = a.rate_limit()
    assert rl.requests_per_min == 200
    assert rl.requests_per_day == 100_000
    assert "tushare-2000" in rl.notes


def test_healthcheck_uses_trade_cal(mock_pro):
    mock_pro.trade_cal.return_value = pd.DataFrame({"cal_date": ["20240102"], "is_open": [1]})
    a = TushareAdapter(pro_token="x")
    assert a.healthcheck() is True
    mock_pro.trade_cal.assert_called()


def test_healthcheck_returns_false_on_exception(mock_pro):
    mock_pro.trade_cal.side_effect = RuntimeError("boom")
    a = TushareAdapter(pro_token="x")
    assert a.healthcheck() is False


def test_fetch_dispatches_to_pro_method(mock_pro):
    df = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "trade_date": ["20240102"],
        "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
        "pre_close": [10.0], "change": [0.5], "pct_chg": [5.0],
        "vol": [100.0], "amount": [105.0],
    })
    mock_pro.daily.return_value = df
    a = TushareAdapter(pro_token="x")
    out = a.fetch("daily", trade_date="20240102")
    assert len(out) == 1
    # date string was canonicalized
    assert out["trade_date"].iloc[0] == date(2024, 1, 2)
    # original column names preserved
    assert {"ts_code", "trade_date", "open", "close", "vol", "amount"} <= set(out.columns)


def test_unsupported_topic_raises(mock_pro):
    a = TushareAdapter(pro_token="x")
    with pytest.raises(ValueError):
        a.fetch("not_a_topic")


def test_pro_lazy_construction_requires_token():
    a = TushareAdapter(pro_token="", tier=2000)
    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
        _ = a.pro


def test_pro_lazy_construction_calls_set_token(mock_pro):
    with patch("tushare.set_token") as set_token:
        a = TushareAdapter(pro_token="xyz")
        _ = a.pro
        set_token.assert_called_with("xyz")


def test_lineage_record_shape():
    a = TushareAdapter(pro_token="x")
    rec = a.lineage(table="raw_tushare_daily", schema_version="v1",
                    params={"trade_date": "20240102"}, rows=5000)
    d = rec.to_dict()
    assert d["table"] == "raw_tushare_daily"
    assert d["source"] == "tushare"
    assert d["rows"] == 5000
    assert d["params"]["trade_date"] == "20240102"
    assert "fetched_at" in d
    assert "request_id" in d
