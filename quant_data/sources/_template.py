"""Template for new data sources.

To add a new source (e.g. wind / joinquant / csv dropbox):

1. Copy this file to ``quant_data/sources/<name>.py``.
2. Fill in ``name``, ``version``, ``capabilities`` and the three methods.
3. Add ONE LINE to ``quant_data/registry.py::SOURCES``:

       register_source("wind", WindAdapter(...))

That's the only edit required for Portfolio / Risk agents.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from quant_data.rate_limit import TokenBucket
from quant_data.sources.base import LineageRecord, RateLimit

log = logging.getLogger("quant_data.sources.template")


class TemplateAdapter:
    # REQUIRED -----------------------------------------------------------------
    name = "template"        # unique short code: tushare / akshare / wind / csv
    version = "0.1.0"        # adapter self-version
    capabilities = {"daily"}  # which topics this adapter can serve

    def __init__(self):
        # 80% of documented ceiling; tune to your upstream
        self._rl = TokenBucket(RateLimit(requests_per_min=60, notes="template-conservative"))

    # REQUIRED: declared by DataSource Protocol --------------------------------
    def rate_limit(self) -> RateLimit:
        return RateLimit(requests_per_min=60, notes="template-conservative")

    def healthcheck(self) -> bool:
        # do something cheap that proves the wire is up
        return True

    def fetch(self, topic: str, **params: Any) -> pd.DataFrame:
        if topic not in self.capabilities:
            raise ValueError(f"template adapter: topic {topic!r} not supported")
        self._rl.acquire()
        # -----------------------------------------------------------------------
        # TODO: replace with your real upstream call. Must return a DataFrame
        # with the column names declared in the corresponding TableSchema.
        # -----------------------------------------------------------------------
        df = pd.DataFrame()
        log.info("template %s -> %d rows", topic, len(df))
        return df

    # OPTIONAL: used by store to write lineage ---------------------------------
    def lineage(self, table: str, schema_version: str, params: dict, rows: int) -> LineageRecord:
        import uuid
        from datetime import datetime
        return LineageRecord(
            table=table,
            schema_version=schema_version,
            source=self.name,
            source_version=self.version,
            fetched_at=datetime.now().astimezone(),
            params=params,
            rows=rows,
            rate_limit_hit=self._rl.rate_limit_hit,
            request_id=str(uuid.uuid4()),
        )
