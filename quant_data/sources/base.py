"""Layer 1 + Layer 2 abstractions (v0.4 §6.1).

Defines the protocols every DataSource / DataStore must satisfy, and the
schema-versioning dataclasses used by the registry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class FieldSpec:
    """One column of a table schema.

    ``unit`` carries the value-side semantics (``yuan`` / ``share`` / ``lot``
    / ``kilo_yuan``) so downstream views can normalize at the SQL layer.
    """
    name: str
    dtype: str
    unit: str
    nullable: bool = False
    description: str = ""


@dataclass(frozen=True)
class TableSchema:
    """Versioned schema for one table (e.g. ``("daily", "v1.0")``).

    ``source_mapping`` is a per-source dict from the canonical field name to
    the source-native field name. The TushareAdapter reads it to rename
    columns into the canonical space.
    """
    table: str
    version: str
    primary_key: list[str]
    fields: dict[str, FieldSpec]
    source_mapping: dict[str, dict[str, str]] = field(default_factory=dict)

    def field_names(self) -> list[str]:
        return list(self.fields.keys())


@dataclass
class RateLimit:
    """Per-source request budget."""
    requests_per_min: int
    requests_per_day: int | None = None
    notes: str = ""

    @property
    def safe_rpm(self) -> int:
        """Rate we actually drive at (80% of the documented ceiling, v0.4 §4.3)."""
        return int(self.requests_per_min * 0.8)


@runtime_checkable
class DataSource(Protocol):
    """A single external data source (tushare / akshare / wind / csv)."""
    name: str
    version: str
    capabilities: set[str]

    def fetch(self, topic: str, **params: Any) -> Any:  # returns pd.DataFrame
        ...

    def rate_limit(self) -> RateLimit:
        ...

    def healthcheck(self) -> bool:
        ...


@runtime_checkable
class DataStore(Protocol):
    """Physical landing for the canonical schema-versioned tables."""
    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> Any: ...
    def upsert(self, table: str, df: Any, schema_version: str) -> int: ...
    def get_cursor(self, table: str) -> date | None: ...
    def set_cursor(self, table: str, d: date, status: str = "ok", error: str = "") -> None: ...
    def register_schema(self, schema: TableSchema) -> None: ...


@dataclass
class LineageRecord:
    """Per-batch metadata persisted to ``meta/_lineage/<table>/<date>.json``."""
    table: str
    schema_version: str
    source: str
    source_version: str
    fetched_at: datetime
    params: Mapping[str, Any]
    rows: int
    rate_limit_hit: int
    request_id: str
    extras: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "table": self.table,
            "schema_version": self.schema_version,
            "source": self.source,
            "source_version": self.source_version,
            "fetched_at": self.fetched_at.isoformat(),
            "params": dict(self.params),
            "rows": self.rows,
            "rate_limit_hit": self.rate_limit_hit,
            "request_id": self.request_id,
        }
        d.update(dict(self.extras))
        return d


def iter_schemas(schemas: Iterable[TableSchema]):
    for s in schemas:
        yield s
