"""Registering a new source must NOT require editing any agent code (v0.4 §6.2)."""
from __future__ import annotations

import pandas as pd
import pytest

from quant_data.registry import SOURCES, register_source
from quant_data.sources._template import TemplateAdapter
from quant_data.sources.base import RateLimit


def test_registering_new_source_does_not_touch_agents():
    """A new source registration is one line — the agent code that consumes
    SOURCES via get_source() must work without modification.
    """
    # Simulate a downstream agent that picks up the source by name
    from quant_data.registry import get_source

    class _Custom:
        name = "csv_dropbox"
        version = "0.1.0"
        capabilities = {"daily"}
        def rate_limit(self): return RateLimit(requests_per_min=1000)
        def healthcheck(self): return True
        def fetch(self, topic, **p): return pd.DataFrame()

    instance = _Custom()
    register_source("csv_dropbox", instance)
    src = get_source("csv_dropbox")
    # registered instance == what get_source returns (downstream code path unchanged)
    assert src is instance
    assert src.healthcheck() is True
    assert src.fetch("daily", foo="bar") is not None
    assert "csv_dropbox" in SOURCES


def test_template_adapter_is_valid_datasource():
    """The shipped template satisfies the DataSource protocol (structural typing)."""
    t = TemplateAdapter()
    assert t.name == "template"
    assert "daily" in t.capabilities
    # explicit checks rather than runtime_checkable (which has Python-version quirks)
    assert callable(getattr(t, "fetch", None))
    assert callable(getattr(t, "rate_limit", None))
    assert callable(getattr(t, "healthcheck", None))


def test_unknown_source_raises():
    from quant_data.registry import get_source
    with pytest.raises(KeyError):
        get_source("does_not_exist_zzz")
