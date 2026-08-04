"""Immutable, searchable broker instrument-master catalog.

This module deliberately owns catalog identity only.  Broker transport is
provided through ``InstrumentMasterClient`` and downstream consumers receive
already-resolved immutable projections.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import threading
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from config.application_configuration import EnvironmentProfile

INSTRUMENT_LOADER_VERSION = "1.0.0"
INSTRUMENT_LOADER_SCHEMA_VERSION = "1.0.0"
PRODUCER_NAME = "broker.instrument_loader"
SUPPORTED_PRIMARY_UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})
SUPPORTED_SECONDARY_UNDERLYINGS = frozenset({"FINNIFTY", "MIDCPNIFTY"})
SUPPORTED_INDEX_UNDERLYINGS = SUPPORTED_PRIMARY_UNDERLYINGS | SUPPORTED_SECONDARY_UNDERLYINGS
SUPPORTED_EXCHANGES = frozenset({"NSE", "NFO", "BSE", "BFO", "MCX"})
SUPPORTED_OPTION_TYPES = frozenset({"CE", "PE"})
TOPIC_CATALOG_LOADED = "market.instruments.catalog.loaded"
TOPIC_CATALOG_FAILED = "market.instruments.catalog.failed"
DEFAULT_MAX_RECORDS = 500_000
DEFAULT_CACHE_MAX_AGE_SECONDS = 86_400.0
DEFAULT_STRIKE_STEP = 50.0
INDEX_NAME_ALIASES = MappingProxyType({
    "NIFTY 50": "NIFTY", "NIFTY50": "NIFTY", "NIFTY BANK": "BANKNIFTY",
    "BANKNIFTY": "BANKNIFTY", "SENSEX": "SENSEX",
    "NIFTY FIN SERVICE": "FINNIFTY", "NIFTY MID SELECT": "MIDCPNIFTY",
})
_EMPTY_STR_MAPPING: Mapping[str, str] = MappingProxyType({})


class InstrumentRole(str, Enum):
    """Canonical role of an instrument."""
    SPOT = "SPOT"
    FUTURE = "FUTURE"
    OPTION_CE = "OPTION_CE"
    OPTION_PE = "OPTION_PE"
    VOLATILITY_INDEX = "VOLATILITY_INDEX"
    EQUITY = "EQUITY"
    UNKNOWN = "UNKNOWN"


class UnderlyingSupportTier(str, Enum):
    """Support classification of a canonical underlying."""
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    EQUITY_FO = "EQUITY_FO"
    EXPERIMENTAL = "EXPERIMENTAL"


class CatalogLifecycleState(str, Enum):
    """Lifecycle state for an instrument loader."""
    CREATED = "CREATED"
    LOADING = "LOADING"
    READY = "READY"
    RELOADING = "RELOADING"
    DEGRADED = "DEGRADED"
    CLOSED = "CLOSED"


class CatalogHealthStatus(str, Enum):
    """Overall catalog health classification."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class LookupStatus(str, Enum):
    """Result state of a catalog query."""
    HIT = "HIT"
    MISS = "MISS"
    AMBIGUOUS = "AMBIGUOUS"
    REJECTED = "REJECTED"


class InstrumentSourceKind(str, Enum):
    """Provenance of loaded instrument rows."""
    BROKER_DOWNLOAD = "BROKER_DOWNLOAD"
    LOCAL_CSV = "LOCAL_CSV"
    LOCAL_JSON = "LOCAL_JSON"
    IN_MEMORY_ROWS = "IN_MEMORY_ROWS"
    CACHE = "CACHE"


class DuplicatePolicy(str, Enum):
    """Deterministic duplicate conflict policy."""
    KEEP_FIRST_STABLE = "KEEP_FIRST_STABLE"
    KEEP_LAST_STABLE = "KEEP_LAST_STABLE"
    REJECT = "REJECT"


class InstrumentLoaderError(Exception):
    """Base error with stable code and row attribution."""

    def __init__(self, message: str, *, code: str, field: str | None = None,
                 underlying: str | None = None, instrument_token: int | None = None,
                 tradingsymbol: str | None = None) -> None:
        super().__init__(message)
        self.message, self.code, self.field = message, code, field
        self.underlying, self.instrument_token = underlying, instrument_token
        self.tradingsymbol = tradingsymbol


class InstrumentLoaderConfigurationError(InstrumentLoaderError):
    """Invalid loader configuration."""


class InstrumentLoaderStateError(InstrumentLoaderError):
    """Invalid loader lifecycle operation."""


class InstrumentParseError(InstrumentLoaderError):
    """Unparseable source data."""


class InstrumentValidationError(InstrumentLoaderError):
    """Invalid instrument row."""


class InstrumentLoaderIOError(InstrumentLoaderError):
    """Broker or filesystem failure."""


class InstrumentLoaderSerializationError(InstrumentLoaderError):
    """Invalid serialized payload."""


class InstrumentLookupError(InstrumentLoaderError):
    """Strict scalar lookup failure."""


def _freeze(mapping: Mapping[Any, Any]) -> Mapping[Any, Any]:
    return MappingProxyType(dict(mapping))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def normalize_underlying_name(value: str) -> str:
    """Return a canonical uppercase underlying name."""
    normalized = str(value).strip().upper()
    if not normalized:
        raise InstrumentValidationError("Underlying is empty.", code="IL.VALIDATION.UNDERLYING_UNRESOLVED", field="underlying")
    return INDEX_NAME_ALIASES.get(normalized, normalized)


def classify_underlying_tier(underlying: str, *, equity_underlyings: Sequence[str] = ()) -> UnderlyingSupportTier:
    """Classify an underlying without assigning any instrument identity."""
    name = normalize_underlying_name(underlying)
    if name in SUPPORTED_PRIMARY_UNDERLYINGS:
        return UnderlyingSupportTier.PRIMARY
    if name in SUPPORTED_SECONDARY_UNDERLYINGS:
        return UnderlyingSupportTier.SECONDARY
    if name in {normalize_underlying_name(item) for item in equity_underlyings}:
        return UnderlyingSupportTier.EQUITY_FO
    return UnderlyingSupportTier.EXPERIMENTAL


def resolve_instrument_role(instrument_type: str, *, name: str | None = None) -> InstrumentRole:
    """Resolve a broker instrument type into the platform role vocabulary."""
    kind = str(instrument_type).strip().upper()
    if kind == "INDEX":
        return InstrumentRole.SPOT
    if kind == "FUT":
        return InstrumentRole.FUTURE
    if kind == "CE":
        return InstrumentRole.OPTION_CE
    if kind == "PE":
        return InstrumentRole.OPTION_PE
    if kind == "EQ":
        return InstrumentRole.EQUITY
    if "VIX" in str(name or "").upper():
        return InstrumentRole.VOLATILITY_INDEX
    return InstrumentRole.UNKNOWN


@dataclass(frozen=True)
class InstrumentRecord:
    """One validated and normalized broker instrument row."""
    instrument_token: int
    exchange_token: int | None
    tradingsymbol: str
    name: str
    underlying: str
    exchange: str
    instrument_type: str
    instrument_role: InstrumentRole
    segment: str | None
    expiry: str | None
    strike: float | None
    option_type: str | None
    lot_size: int
    tick_size: float
    quote_key: str
    support_tier: UnderlyingSupportTier
    is_expired: bool
    raw_name: str | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_STR_MAPPING)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True)
class CatalogIndexes:
    """Immutable lookup indexes built once at catalog seal time."""
    by_token: Mapping[int, InstrumentRecord]
    by_quote_key: Mapping[str, InstrumentRecord]
    by_tradingsymbol: Mapping[tuple[str, str], InstrumentRecord]
    by_underlying: Mapping[str, tuple[InstrumentRecord, ...]]
    by_underlying_expiry: Mapping[tuple[str, str], tuple[InstrumentRecord, ...]]
    by_underlying_expiry_strike: Mapping[tuple[str, str, float], tuple[InstrumentRecord, ...]]
    by_underlying_role: Mapping[tuple[str, InstrumentRole], tuple[InstrumentRecord, ...]]
    option_expiries: Mapping[str, tuple[str, ...]]
    future_expiries: Mapping[str, tuple[str, ...]]
    strikes: Mapping[tuple[str, str], tuple[float, ...]]


@dataclass(frozen=True)
class CatalogStatistics:
    """Counters and phase timings from the last loading attempt."""
    as_of: datetime
    source_kind: InstrumentSourceKind | None = None
    load_duration_ms: float = 0.0
    parse_duration_ms: float = 0.0
    validate_duration_ms: float = 0.0
    index_duration_ms: float = 0.0
    raw_row_count: int = 0
    retained_record_count: int = 0
    discarded_invalid_count: int = 0
    discarded_duplicate_count: int = 0
    discarded_expired_count: int = 0
    discarded_underlying_count: int = 0
    discarded_exchange_count: int = 0
    discarded_equity_fo_count: int = 0
    option_count: int = 0
    future_count: int = 0
    spot_count: int = 0
    volatility_count: int = 0
    expiry_count: int = 0
    underlying_counts: Mapping[str, int] = field(default_factory=lambda: _EMPTY_STR_MAPPING)
    last_error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _utc(self.as_of))
        object.__setattr__(self, "underlying_counts", _freeze(self.underlying_counts))


@dataclass(frozen=True)
class InstrumentCatalog:
    """Sealed immutable instrument catalog and its indexes."""
    catalog_id: str
    schema_version: str
    loaded_at: datetime
    as_of_date: str
    source_kind: InstrumentSourceKind
    source_uri: str | None
    enabled_underlyings: tuple[str, ...]
    enabled_exchanges: tuple[str, ...]
    records: tuple[InstrumentRecord, ...]
    record_count: int
    indexes: CatalogIndexes
    statistics: CatalogStatistics
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_STR_MAPPING)

    def __post_init__(self) -> None:
        object.__setattr__(self, "loaded_at", _utc(self.loaded_at))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True)
class LookupResult:
    """Uniform immutable result envelope for catalog lookups."""
    status: LookupStatus
    query_name: str
    records: tuple[InstrumentRecord, ...] = ()
    primary: InstrumentRecord | None = None
    reason_code: str | None = None
    reason_message: str | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", _freeze(self.diagnostics))


@dataclass(frozen=True)
class CatalogHealthIssue:
    """One immutable catalog health finding."""
    issue_code: str
    severity: str
    message: str
    underlying: str | None = None
    instrument_token: int | None = None


@dataclass(frozen=True)
class CatalogHealth:
    """Immutable health view suitable for orchestration."""
    report_id: str
    as_of: datetime
    lifecycle_state: CatalogLifecycleState
    overall_health: CatalogHealthStatus
    has_catalog: bool
    catalog_id: str | None
    record_count: int
    enabled_underlyings: tuple[str, ...]
    underlyings_with_records: tuple[str, ...]
    underlyings_missing_records: tuple[str, ...]
    seconds_since_load: float | None
    issues: tuple[CatalogHealthIssue, ...]
    statistics: CatalogStatistics
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_STR_MAPPING)

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _utc(self.as_of))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True)
class InstrumentLoaderConfig:
    """Immutable policy projection used to build an instrument catalog."""
    enabled_underlyings: tuple[str, ...]
    enabled_exchanges: tuple[str, ...] = ("NSE", "NFO", "BSE", "BFO")
    allow_experimental_underlyings: bool = False
    allow_equity_fo: bool = False
    enabled_equity_underlyings: tuple[str, ...] = ()
    include_index_spot: bool = True
    include_futures: bool = True
    include_options: bool = True
    include_volatility_index: bool = True
    drop_expired: bool = True
    expiry_timezone: str = "Asia/Kolkata"
    duplicate_policy: DuplicatePolicy | str = DuplicatePolicy.KEEP_FIRST_STABLE
    require_non_empty_catalog: bool = True
    max_records: int = DEFAULT_MAX_RECORDS
    default_strike_step: float = DEFAULT_STRIKE_STEP
    strike_step: Mapping[str, float] = field(default_factory=dict)
    spot_overrides: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    volatility_index_map: Mapping[str, str] = field(default_factory=dict)
    cache_enabled: bool = True
    cache_directory: str | None = None
    cache_filename: str = "instrument_catalog_cache.json"
    prefer_cache_before_download: bool = True
    cache_max_age_seconds: float = DEFAULT_CACHE_MAX_AGE_SECONDS
    publish_events: bool = False
    environment_profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT
    runner_kind: str = "unknown"
    metadata: Mapping[str, str] = field(default_factory=dict)
    replace_on_failure: bool = False
    strict_parse: bool = False
    strict_validation: bool = False
    allow_unknown_roles: bool = False
    allow_stale_cache: bool = False

    def __post_init__(self) -> None:
        names = tuple(normalize_underlying_name(item) for item in self.enabled_underlyings)
        if not names:
            raise InstrumentLoaderConfigurationError("At least one underlying is required.", code="IL.CONFIG.UNDERLYING_REQUIRED")
        if len(set(names)) != len(names):
            raise InstrumentLoaderConfigurationError("Duplicate underlying.", code="IL.CONFIG.UNDERLYING_DUPLICATE")
        equities = tuple(normalize_underlying_name(item) for item in self.enabled_equity_underlyings)
        for name in names:
            known = name in SUPPORTED_INDEX_UNDERLYINGS or name in equities
            if not known and not self.allow_experimental_underlyings:
                raise InstrumentLoaderConfigurationError("Unsupported underlying.", code="IL.CONFIG.UNDERLYING_UNSUPPORTED", underlying=name)
            if name in equities and not self.allow_equity_fo:
                raise InstrumentLoaderConfigurationError("Equity F&O disabled.", code="IL.CONFIG.UNDERLYING_UNSUPPORTED", underlying=name)
        exchanges = tuple(str(item).strip().upper() for item in self.enabled_exchanges)
        if not exchanges or any(item not in SUPPORTED_EXCHANGES for item in exchanges):
            raise InstrumentLoaderConfigurationError("Invalid exchange.", code="IL.CONFIG.EXCHANGE_INVALID")
        try:
            policy = DuplicatePolicy(self.duplicate_policy)
        except ValueError as exc:
            raise InstrumentLoaderConfigurationError("Invalid duplicate policy.", code="IL.CONFIG.POLICY_INVALID") from exc
        if (not math.isfinite(self.default_strike_step) or self.default_strike_step <= 0 or
                self.max_records < 1 or self.cache_max_age_seconds < 0 or
                any(not math.isfinite(value) or value <= 0 for value in self.strike_step.values())):
            raise InstrumentLoaderConfigurationError("Invalid numeric threshold.", code="IL.CONFIG.THRESHOLD_OUT_OF_RANGE")
        try:
            ZoneInfo(self.expiry_timezone)
        except Exception as exc:
            raise InstrumentLoaderConfigurationError("Invalid expiry timezone.", code="IL.CONFIG.THRESHOLD_OUT_OF_RANGE", field="expiry_timezone") from exc
        if self.cache_enabled and self.environment_profile in (EnvironmentProfile.PAPER, EnvironmentProfile.PRODUCTION) and not self.cache_directory:
            raise InstrumentLoaderConfigurationError("Cache directory is required.", code="IL.CONFIG.CACHE_PATH_REQUIRED")
        object.__setattr__(self, "enabled_underlyings", names)
        object.__setattr__(self, "enabled_equity_underlyings", equities)
        object.__setattr__(self, "enabled_exchanges", exchanges)
        object.__setattr__(self, "duplicate_policy", policy)
        object.__setattr__(self, "strike_step", _freeze({normalize_underlying_name(k): float(v) for k, v in self.strike_step.items()}))
        object.__setattr__(self, "spot_overrides", _freeze(self.spot_overrides))
        object.__setattr__(self, "volatility_index_map", _freeze({normalize_underlying_name(k): str(v) for k, v in self.volatility_index_map.items()}))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


def default_instrument_loader_config(profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT, *, enabled_underlyings: Sequence[str] = ("NIFTY",)) -> InstrumentLoaderConfig:
    """Build profile-safe default loader configuration."""
    return InstrumentLoaderConfig(
        enabled_underlyings=tuple(enabled_underlyings), environment_profile=profile,
        allow_experimental_underlyings=profile is EnvironmentProfile.DEVELOPMENT,
        require_non_empty_catalog=profile is not EnvironmentProfile.DEVELOPMENT,
        cache_enabled=False,
    )


class InstrumentMasterClient(Protocol):
    """Injected broker transport boundary."""
    def fetch_instrument_rows(self, *, exchange: str) -> Sequence[Mapping[str, Any]]:
        """Return raw broker instrument rows for one exchange."""


def _parse_expiry(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        if text.isdigit() and len(text) >= 10:
            return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc).date().isoformat()
    except (ValueError, OSError):
        pass
    raise InstrumentValidationError("Invalid expiry.", code="IL.VALIDATION.MISSING_EXPIRY", field="expiry")


def _number(value: Any, kind: type[int] | type[float], field_name: str, *, optional: bool = False) -> int | float | None:
    if value is None or str(value).strip() == "":
        if optional:
            return None
        raise InstrumentValidationError(f"Missing {field_name}.", code="IL.VALIDATION.INVALID_TOKEN", field=field_name)
    try:
        return kind(float(value)) if kind is int else float(value)
    except (TypeError, ValueError) as exc:
        raise InstrumentValidationError(f"Invalid {field_name}.", code="IL.VALIDATION.INVALID_TOKEN", field=field_name) from exc


def _build_indexes(records: tuple[InstrumentRecord, ...]) -> CatalogIndexes:
    token: dict[int, InstrumentRecord] = {}
    quote: dict[str, InstrumentRecord] = {}
    symbol: dict[tuple[str, str], InstrumentRecord] = {}
    underlying: defaultdict[str, list[InstrumentRecord]] = defaultdict(list)
    expiry: defaultdict[tuple[str, str], list[InstrumentRecord]] = defaultdict(list)
    strike: defaultdict[tuple[str, str, float], list[InstrumentRecord]] = defaultdict(list)
    role: defaultdict[tuple[str, InstrumentRole], list[InstrumentRecord]] = defaultdict(list)
    option_expiries: defaultdict[str, set[str]] = defaultdict(set)
    future_expiries: defaultdict[str, set[str]] = defaultdict(set)
    strikes: defaultdict[tuple[str, str], set[float]] = defaultdict(set)
    for record in records:
        token[record.instrument_token], quote[record.quote_key] = record, record
        symbol[(record.exchange, record.tradingsymbol)] = record
        underlying[record.underlying].append(record)
        role[(record.underlying, record.instrument_role)].append(record)
        if record.expiry:
            expiry[(record.underlying, record.expiry)].append(record)
        if record.strike is not None and record.expiry:
            strike[(record.underlying, record.expiry, record.strike)].append(record)
            strikes[(record.underlying, record.expiry)].add(record.strike)
        if record.instrument_role in (InstrumentRole.OPTION_CE, InstrumentRole.OPTION_PE) and record.expiry:
            option_expiries[record.underlying].add(record.expiry)
        if record.instrument_role is InstrumentRole.FUTURE and record.expiry:
            future_expiries[record.underlying].add(record.expiry)
    freeze_tuple = lambda items: _freeze({key: tuple(value) for key, value in items.items()})
    return CatalogIndexes(_freeze(token), _freeze(quote), _freeze(symbol), freeze_tuple(underlying),
        freeze_tuple(expiry), freeze_tuple(strike), freeze_tuple(role),
        _freeze({key: tuple(sorted(value)) for key, value in option_expiries.items()}),
        _freeze({key: tuple(sorted(value)) for key, value in future_expiries.items()}),
        _freeze({key: tuple(sorted(value)) for key, value in strikes.items()}))


def _record_payload(record: InstrumentRecord) -> dict[str, Any]:
    return {"instrument_token": record.instrument_token, "exchange_token": record.exchange_token,
        "tradingsymbol": record.tradingsymbol, "name": record.name, "underlying": record.underlying,
        "exchange": record.exchange, "instrument_type": record.instrument_type, "instrument_role": record.instrument_role.value,
        "segment": record.segment, "expiry": record.expiry, "strike": record.strike, "option_type": record.option_type,
        "lot_size": record.lot_size, "tick_size": record.tick_size, "quote_key": record.quote_key,
        "support_tier": record.support_tier.value, "is_expired": record.is_expired, "raw_name": record.raw_name,
        "metadata": dict(record.metadata)}


def serialize_instrument_record(record: InstrumentRecord) -> dict[str, Any]:
    """Serialize an instrument record into JSON-safe data."""
    return {"schema_version": INSTRUMENT_LOADER_SCHEMA_VERSION, **_record_payload(record)}


def deserialize_instrument_record(payload: Mapping[str, Any]) -> InstrumentRecord:
    """Deserialize and validate an instrument record payload."""
    _check_schema(payload)
    try:
        data = dict(payload)
        data.pop("schema_version", None)
        data["instrument_role"] = InstrumentRole(data["instrument_role"])
        data["support_tier"] = UnderlyingSupportTier(data["support_tier"])
        return InstrumentRecord(**data)
    except (KeyError, TypeError, ValueError) as exc:
        raise InstrumentLoaderSerializationError("Malformed record.", code="IL.SERIALIZATION.MALFORMED") from exc


def _statistics_payload(stats: CatalogStatistics) -> dict[str, Any]:
    return {"as_of": _iso(stats.as_of), "source_kind": stats.source_kind.value if stats.source_kind else None,
        "load_duration_ms": stats.load_duration_ms, "parse_duration_ms": stats.parse_duration_ms,
        "validate_duration_ms": stats.validate_duration_ms, "index_duration_ms": stats.index_duration_ms,
        "raw_row_count": stats.raw_row_count, "retained_record_count": stats.retained_record_count,
        "discarded_invalid_count": stats.discarded_invalid_count, "discarded_duplicate_count": stats.discarded_duplicate_count,
        "discarded_expired_count": stats.discarded_expired_count, "discarded_underlying_count": stats.discarded_underlying_count,
        "discarded_exchange_count": stats.discarded_exchange_count, "discarded_equity_fo_count": stats.discarded_equity_fo_count,
        "option_count": stats.option_count, "future_count": stats.future_count, "spot_count": stats.spot_count,
        "volatility_count": stats.volatility_count, "expiry_count": stats.expiry_count,
        "underlying_counts": dict(stats.underlying_counts), "last_error_code": stats.last_error_code}


def serialize_catalog_statistics(statistics: CatalogStatistics) -> dict[str, Any]:
    """Serialize load statistics."""
    return {"schema_version": INSTRUMENT_LOADER_SCHEMA_VERSION, **_statistics_payload(statistics)}


def deserialize_catalog_statistics(payload: Mapping[str, Any]) -> CatalogStatistics:
    """Deserialize load statistics."""
    _check_schema(payload)
    try:
        data = dict(payload); data.pop("schema_version", None)
        data["as_of"] = _parse_datetime(data["as_of"])
        if data["source_kind"] is not None: data["source_kind"] = InstrumentSourceKind(data["source_kind"])
        return CatalogStatistics(**data)
    except (KeyError, TypeError, ValueError) as exc:
        raise InstrumentLoaderSerializationError("Malformed statistics.", code="IL.SERIALIZATION.MALFORMED") from exc


def serialize_instrument_catalog(catalog: InstrumentCatalog, *, include_indexes: bool = False) -> dict[str, Any]:
    """Serialize a sealed catalog, omitting rebuildable indexes by default."""
    data = {"schema_version": INSTRUMENT_LOADER_SCHEMA_VERSION, "catalog_id": catalog.catalog_id,
        "loaded_at": _iso(catalog.loaded_at), "as_of_date": catalog.as_of_date, "source_kind": catalog.source_kind.value,
        "source_uri": catalog.source_uri, "enabled_underlyings": list(catalog.enabled_underlyings),
        "enabled_exchanges": list(catalog.enabled_exchanges), "records": [_record_payload(item) for item in catalog.records],
        "record_count": catalog.record_count, "statistics": _statistics_payload(catalog.statistics), "metadata": dict(catalog.metadata)}
    if include_indexes: data["indexes_included"] = True
    return data


def deserialize_instrument_catalog(payload: Mapping[str, Any]) -> InstrumentCatalog:
    """Deserialize a catalog and rebuild its immutable indexes."""
    _check_schema(payload)
    try:
        records = tuple(deserialize_instrument_record({"schema_version": INSTRUMENT_LOADER_SCHEMA_VERSION, **item}) for item in payload["records"])
        return InstrumentCatalog(str(payload["catalog_id"]), INSTRUMENT_LOADER_SCHEMA_VERSION, _parse_datetime(payload["loaded_at"]),
            str(payload["as_of_date"]), InstrumentSourceKind(payload["source_kind"]), payload.get("source_uri"),
            tuple(payload["enabled_underlyings"]), tuple(payload["enabled_exchanges"]), records, len(records), _build_indexes(records),
            deserialize_catalog_statistics({"schema_version": INSTRUMENT_LOADER_SCHEMA_VERSION, **payload["statistics"]}), payload.get("metadata", {}))
    except (KeyError, TypeError, ValueError) as exc:
        raise InstrumentLoaderSerializationError("Malformed catalog.", code="IL.SERIALIZATION.MALFORMED") from exc


def serialize_lookup_result(result: LookupResult) -> dict[str, Any]:
    """Serialize a lookup result."""
    return {"schema_version": INSTRUMENT_LOADER_SCHEMA_VERSION, "status": result.status.value, "query_name": result.query_name,
        "records": [_record_payload(item) for item in result.records], "primary": _record_payload(result.primary) if result.primary else None,
        "reason_code": result.reason_code, "reason_message": result.reason_message, "diagnostics": dict(result.diagnostics)}


def deserialize_lookup_result(payload: Mapping[str, Any]) -> LookupResult:
    """Deserialize a lookup result."""
    _check_schema(payload)
    try:
        record = lambda item: deserialize_instrument_record({"schema_version": INSTRUMENT_LOADER_SCHEMA_VERSION, **item})
        records = tuple(record(item) for item in payload["records"])
        primary = record(payload["primary"]) if payload.get("primary") else None
        return LookupResult(LookupStatus(payload["status"]), str(payload["query_name"]), records, primary,
                            payload.get("reason_code"), payload.get("reason_message"), payload.get("diagnostics", {}))
    except (KeyError, TypeError, ValueError) as exc:
        raise InstrumentLoaderSerializationError("Malformed lookup.", code="IL.SERIALIZATION.MALFORMED") from exc


def serialize_catalog_health(health: CatalogHealth) -> dict[str, Any]:
    """Serialize a catalog health report."""
    return {"schema_version": INSTRUMENT_LOADER_SCHEMA_VERSION, "report_id": health.report_id, "as_of": _iso(health.as_of),
        "lifecycle_state": health.lifecycle_state.value, "overall_health": health.overall_health.value, "has_catalog": health.has_catalog,
        "catalog_id": health.catalog_id, "record_count": health.record_count, "enabled_underlyings": list(health.enabled_underlyings),
        "underlyings_with_records": list(health.underlyings_with_records), "underlyings_missing_records": list(health.underlyings_missing_records),
        "seconds_since_load": health.seconds_since_load, "issues": [issue.__dict__ for issue in health.issues],
        "statistics": _statistics_payload(health.statistics), "metadata": dict(health.metadata)}


def deserialize_catalog_health(payload: Mapping[str, Any]) -> CatalogHealth:
    """Deserialize a catalog health report."""
    _check_schema(payload)
    try:
        return CatalogHealth(str(payload["report_id"]), _parse_datetime(payload["as_of"]), CatalogLifecycleState(payload["lifecycle_state"]),
            CatalogHealthStatus(payload["overall_health"]), bool(payload["has_catalog"]), payload.get("catalog_id"), int(payload["record_count"]),
            tuple(payload["enabled_underlyings"]), tuple(payload["underlyings_with_records"]), tuple(payload["underlyings_missing_records"]),
            payload.get("seconds_since_load"), tuple(CatalogHealthIssue(**item) for item in payload["issues"]),
            deserialize_catalog_statistics({"schema_version": INSTRUMENT_LOADER_SCHEMA_VERSION, **payload["statistics"]}), payload.get("metadata", {}))
    except (KeyError, TypeError, ValueError) as exc:
        raise InstrumentLoaderSerializationError("Malformed health.", code="IL.SERIALIZATION.MALFORMED") from exc


def _check_schema(payload: Mapping[str, Any]) -> None:
    version = str(payload.get("schema_version", ""))
    if version.split(".", 1)[0] != INSTRUMENT_LOADER_SCHEMA_VERSION.split(".", 1)[0]:
        raise InstrumentLoaderSerializationError("Unsupported schema.", code="IL.SERIALIZATION.UNSUPPORTED_VERSION")


def _json_pair(serializer: Callable[[Any], dict[str, Any]], deserializer: Callable[[Mapping[str, Any]], Any]) -> tuple[Callable[[Any], str], Callable[[str], Any]]:
    def to_json(value: Any) -> str: return json.dumps(serializer(value), sort_keys=True, separators=(",", ":"))
    def from_json(value: str) -> Any:
        try: return deserializer(json.loads(value))
        except json.JSONDecodeError as exc: raise InstrumentLoaderSerializationError("Malformed JSON.", code="IL.SERIALIZATION.MALFORMED") from exc
    return to_json, from_json


instrument_record_to_json, instrument_record_from_json = _json_pair(serialize_instrument_record, deserialize_instrument_record)
instrument_catalog_to_json, instrument_catalog_from_json = _json_pair(serialize_instrument_catalog, deserialize_instrument_catalog)
catalog_statistics_to_json, catalog_statistics_from_json = _json_pair(serialize_catalog_statistics, deserialize_catalog_statistics)
catalog_health_to_json, catalog_health_from_json = _json_pair(serialize_catalog_health, deserialize_catalog_health)
lookup_result_to_json, lookup_result_from_json = _json_pair(serialize_lookup_result, deserialize_lookup_result)


class InstrumentLoader:
    """Thread-safe facade that atomically serves sealed instrument catalogs."""

    def __init__(self, config: InstrumentLoaderConfig, *, master_client: InstrumentMasterClient | None = None,
                 event_bus: Any | None = None, clock: Callable[[], datetime] | None = None,
                 id_factory: Callable[[], str] | None = None) -> None:
        """Initialize a loader with injected side-effect boundaries."""
        self._config, self._client, self._event_bus = config, master_client, event_bus
        self._clock, self._id_factory = clock or (lambda: datetime.now(timezone.utc)), id_factory or (lambda: str(uuid.uuid4()))
        self._state = CatalogLifecycleState.CREATED
        self._catalog: InstrumentCatalog | None = None
        self._statistics = CatalogStatistics(as_of=_utc(self._clock()))
        self._last_source: tuple[InstrumentSourceKind, str | None, tuple[str, ...] | None] | None = None
        self._last_error: InstrumentLoaderError | None = None
        self._state_lock, self._load_lock = threading.RLock(), threading.Lock()

    def get_status(self) -> CatalogLifecycleState:
        """Return the lifecycle state snapshot."""
        return self._state

    def close(self) -> None:
        """Close the loader permanently without mutating published catalogs."""
        with self._state_lock: self._state = CatalogLifecycleState.CLOSED

    def _begin(self) -> None:
        if not self._load_lock.acquire(blocking=False):
            raise InstrumentLoaderStateError("A load is already running.", code="IL.STATE.LOAD_IN_PROGRESS")
        with self._state_lock:
            if self._state is CatalogLifecycleState.CLOSED:
                self._load_lock.release()
                raise InstrumentLoaderStateError("Loader is closed.", code="IL.STATE.CLOSED")
            self._state = CatalogLifecycleState.RELOADING if self._catalog else CatalogLifecycleState.LOADING

    def _finish_failure(self, error: InstrumentLoaderError) -> None:
        self._last_error = error
        self._statistics = CatalogStatistics(as_of=_utc(self._clock()), last_error_code=error.code)
        with self._state_lock: self._state = CatalogLifecycleState.DEGRADED
        self._load_lock.release()
        self._publish(TOPIC_CATALOG_FAILED, {"code": error.code, "message": error.message})

    def _publish(self, topic: str, payload: Mapping[str, Any]) -> None:
        if self._config.publish_events and self._event_bus is not None:
            try: self._event_bus.publish(topic, payload)
            except Exception: pass

    def load_from_broker(self, *, exchanges: Sequence[str] | None = None) -> InstrumentCatalog:
        """Download configured exchanges through the injected client."""
        if self._client is None:
            raise InstrumentLoaderStateError(
                "Broker client is not configured.",
                code="IL.STATE.CLIENT_NOT_CONFIGURED",
            )
        if (
            self._config.prefer_cache_before_download
            and self._config.cache_enabled
            and self._config.cache_directory
        ):
            try:
                return self.load_from_cache()
            except InstrumentLoaderError:
                pass
        self._begin()
        try:
            selected = tuple(sorted(exchanges or self._config.enabled_exchanges))
            rows: list[Mapping[str, Any]] = []
            for exchange in selected:
                try:
                    fetched = self._client.fetch_instrument_rows(exchange=exchange)
                    rows.extend(
                        {**row, "_source_exchange": row.get("exchange", exchange)}
                        for row in fetched
                    )
                except Exception as exc:  # noqa: BLE001 - transport boundary
                    raise InstrumentLoaderIOError(
                        "Broker fetch failed.",
                        code="IL.IO.BROKER_FETCH_FAILED",
                    ) from exc
            catalog = self._seal(
                rows, InstrumentSourceKind.BROKER_DOWNLOAD, "broker://instruments"
            )
            self._last_source = (
                InstrumentSourceKind.BROKER_DOWNLOAD,
                None,
                selected,
            )
            if self._config.cache_enabled and self._config.cache_directory:
                self.save_cache()
            return catalog
        except InstrumentLoaderError as exc:
            self._finish_failure(exc)
            raise

    def load_from_file(self, path: str | Path, *, source_kind: InstrumentSourceKind | None = None) -> InstrumentCatalog:
        """Load a CSV or JSON instrument master from a local path."""
        self._begin()
        source_path = Path(path)
        try:
            if not source_path.exists(): raise InstrumentLoaderIOError("File not found.", code="IL.IO.FILE_NOT_FOUND")
            suffix = source_path.suffix.lower()
            if suffix == ".csv":
                with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    required = {"instrument_token", "tradingsymbol", "name", "expiry", "strike", "tick_size", "lot_size", "instrument_type", "exchange"}
                    if not reader.fieldnames or not required.issubset(reader.fieldnames):
                        raise InstrumentParseError("Required CSV columns missing.", code="IL.PARSE.MISSING_COLUMNS")
                    rows = list(reader)
                kind = source_kind or InstrumentSourceKind.LOCAL_CSV
            elif suffix == ".json":
                try:
                    payload = json.loads(source_path.read_text(encoding="utf-8"))
                    rows = payload.get("records", payload) if isinstance(payload, Mapping) else payload
                    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows): raise ValueError
                except (json.JSONDecodeError, ValueError) as exc:
                    raise InstrumentParseError("Invalid JSON input.", code="IL.PARSE.JSON_INVALID") from exc
                kind = source_kind or InstrumentSourceKind.LOCAL_JSON
            else: raise InstrumentParseError("Unsupported source format.", code="IL.PARSE.MISSING_COLUMNS")
            catalog = self._seal(rows, kind, str(source_path))
            self._last_source = (kind, str(source_path), None)
            return catalog
        except InstrumentLoaderError as exc:
            self._finish_failure(exc); raise

    def load_from_rows(self, rows: Sequence[Mapping[str, Any]], *, source_kind: InstrumentSourceKind = InstrumentSourceKind.IN_MEMORY_ROWS,
                       source_uri: str | None = None) -> InstrumentCatalog:
        """Seal an in-memory broker row sequence."""
        self._begin()
        try:
            catalog = self._seal(rows, source_kind, source_uri)
            self._last_source = (source_kind, source_uri, None)
            return catalog
        except InstrumentLoaderError as exc:
            self._finish_failure(exc); raise

    def _seal(
        self,
        rows: Sequence[Mapping[str, Any]],
        source_kind: InstrumentSourceKind,
        source_uri: str | None,
    ) -> InstrumentCatalog:
        start = datetime.now(timezone.utc)
        counters: dict[str, int] = defaultdict(int)
        valid: list[InstrumentRecord] = []
        as_of = (
            _utc(self._clock())
            .astimezone(ZoneInfo(self._config.expiry_timezone))
            .date()
            .isoformat()
        )
        enriched_rows: list[Mapping[str, Any]] = list(rows)
        for underlying, override in self._config.spot_overrides.items():
            if any(
                str(item.get("instrument_token")) == str(override.get("instrument_token"))
                for item in enriched_rows
            ):
                continue
            enriched_rows.append(
                {
                    "instrument_token": override.get("instrument_token"),
                    "exchange_token": override.get("exchange_token"),
                    "tradingsymbol": override.get(
                        "tradingsymbol", override.get("symbol", underlying)
                    ),
                    "name": override.get("name", underlying),
                    "expiry": "",
                    "strike": "",
                    "tick_size": override.get("tick_size", 0.05),
                    "lot_size": override.get("lot_size", 1),
                    "instrument_type": override.get("instrument_type", "INDEX"),
                    "segment": override.get("segment"),
                    "exchange": override.get("exchange", "NSE"),
                    "metadata": override.get("metadata", {}),
                }
            )
        for row in enriched_rows:
            try:
                record = self._normalize_record(row, as_of, counters)
                if record is None:
                    continue
                valid.append(record)
            except InstrumentValidationError:
                if self._config.strict_validation:
                    raise
                counters["invalid"] += 1
        valid.sort(
            key=lambda item: (
                item.instrument_token,
                item.exchange,
                item.tradingsymbol,
            )
        )
        seen_tokens: dict[int, InstrumentRecord] = {}
        seen_symbols: dict[tuple[str, str], InstrumentRecord] = {}
        retained: list[InstrumentRecord] = []
        for record in valid:
            key = (record.exchange, record.tradingsymbol)
            duplicate = (
                record.instrument_token in seen_tokens or key in seen_symbols
            )
            if duplicate:
                if self._config.duplicate_policy is DuplicatePolicy.REJECT:
                    raise InstrumentValidationError(
                        "Duplicate instrument.",
                        code="IL.VALIDATION.DUPLICATE_TOKEN",
                    )
                counters["duplicate"] += 1
                if self._config.duplicate_policy is DuplicatePolicy.KEEP_LAST_STABLE:
                    prior = seen_tokens.get(record.instrument_token)
                    if prior is None:
                        prior = seen_symbols.get(key)
                    if prior is not None and prior in retained:
                        retained.remove(prior)
                        if prior.instrument_token in seen_tokens:
                            del seen_tokens[prior.instrument_token]
                        prior_key = (prior.exchange, prior.tradingsymbol)
                        if prior_key in seen_symbols:
                            del seen_symbols[prior_key]
                else:
                    continue
            seen_tokens[record.instrument_token] = record
            seen_symbols[key] = record
            if self._config.drop_expired and record.is_expired:
                counters["expired"] += 1
            else:
                retained.append(record)
        if len(retained) > self._config.max_records:
            raise InstrumentLoaderConfigurationError(
                "Maximum record count exceeded.",
                code="IL.CONFIG.THRESHOLD_OUT_OF_RANGE",
            )
        retained.sort(
            key=lambda item: (
                item.underlying,
                item.exchange,
                item.expiry or "",
                item.strike if item.strike is not None else -1,
                item.option_type or "",
                item.tradingsymbol,
                item.instrument_token,
            )
        )
        records = tuple(retained)
        if self._config.require_non_empty_catalog and not records:
            raise InstrumentValidationError(
                "Catalog is empty.",
                code="IL.VALIDATION.EMPTY_CATALOG",
            )
        index_start = datetime.now(timezone.utc)
        indexes = _build_indexes(records)
        index_ms = (datetime.now(timezone.utc) - index_start).total_seconds() * 1000
        counts: dict[str, int] = defaultdict(int)
        for record in records:
            counts[record.underlying] += 1
        elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        stats = CatalogStatistics(
            as_of=_utc(self._clock()),
            source_kind=source_kind,
            load_duration_ms=elapsed,
            validate_duration_ms=max(0.0, elapsed - index_ms),
            index_duration_ms=index_ms,
            raw_row_count=len(enriched_rows),
            retained_record_count=len(records),
            discarded_invalid_count=counters["invalid"],
            discarded_duplicate_count=counters["duplicate"],
            discarded_expired_count=counters["expired"],
            discarded_underlying_count=counters["underlying"],
            discarded_exchange_count=counters["exchange"],
            discarded_equity_fo_count=counters["equity_fo"],
            option_count=sum(
                1
                for role_record in records
                if role_record.instrument_role
                in (InstrumentRole.OPTION_CE, InstrumentRole.OPTION_PE)
            ),
            future_count=sum(
                1
                for role_record in records
                if role_record.instrument_role is InstrumentRole.FUTURE
            ),
            spot_count=sum(
                1
                for role_record in records
                if role_record.instrument_role is InstrumentRole.SPOT
            ),
            volatility_count=sum(
                1
                for role_record in records
                if role_record.instrument_role is InstrumentRole.VOLATILITY_INDEX
            ),
            expiry_count=len(
                {
                    role_record.expiry
                    for role_record in records
                    if role_record.instrument_role
                    in (InstrumentRole.OPTION_CE, InstrumentRole.OPTION_PE)
                    and role_record.expiry
                }
            ),
            underlying_counts=counts,
        )
        catalog = InstrumentCatalog(
            self._id_factory(),
            INSTRUMENT_LOADER_SCHEMA_VERSION,
            _utc(self._clock()),
            as_of,
            source_kind,
            source_uri,
            self._config.enabled_underlyings,
            self._config.enabled_exchanges,
            records,
            len(records),
            indexes,
            stats,
            self._config.metadata,
        )
        self._catalog = catalog
        self._statistics = stats
        self._last_error = None
        with self._state_lock:
            self._state = CatalogLifecycleState.READY
        self._load_lock.release()
        self._publish(
            TOPIC_CATALOG_LOADED,
            {"catalog_id": catalog.catalog_id, "record_count": catalog.record_count},
        )
        return catalog

    def _normalize_record(
        self,
        raw: Mapping[str, Any],
        as_of: str,
        counters: dict[str, int],
    ) -> InstrumentRecord | None:
        exchange = str(
            raw.get("exchange", raw.get("_source_exchange", ""))
        ).strip().upper()
        if exchange not in SUPPORTED_EXCHANGES or exchange not in self._config.enabled_exchanges:
            counters["exchange"] += 1
            return None
        token = _number(raw.get("instrument_token"), int, "instrument_token")
        assert isinstance(token, int)
        if token <= 0:
            raise InstrumentValidationError(
                "Invalid token.",
                code="IL.VALIDATION.INVALID_TOKEN",
            )
        symbol = str(raw.get("tradingsymbol", "")).strip()
        name = str(raw.get("name", "")).strip()
        if not symbol:
            raise InstrumentValidationError(
                "Missing symbol.",
                code="IL.VALIDATION.MISSING_SYMBOL",
            )
        instrument_type = str(raw.get("instrument_type", "")).strip().upper()
        role = resolve_instrument_role(instrument_type, name=name)
        mapped_vix = next(
            (
                key
                for key, value in self._config.volatility_index_map.items()
                if str(value).upper() in {name.upper(), symbol.upper()}
            ),
            None,
        )
        if mapped_vix:
            role, underlying = InstrumentRole.VOLATILITY_INDEX, mapped_vix
        else:
            underlying = normalize_underlying_name(name)
        tier = classify_underlying_tier(
            underlying,
            equity_underlyings=self._config.enabled_equity_underlyings,
        )
        if (
            underlying not in self._config.enabled_underlyings
            and role is not InstrumentRole.VOLATILITY_INDEX
        ):
            if tier is UnderlyingSupportTier.EQUITY_FO:
                counters["equity_fo"] += 1
            else:
                counters["underlying"] += 1
            return None
        if (
            tier is UnderlyingSupportTier.EXPERIMENTAL
            and not self._config.allow_experimental_underlyings
        ):
            counters["underlying"] += 1
            return None
        if tier is UnderlyingSupportTier.EQUITY_FO and not self._config.allow_equity_fo:
            counters["equity_fo"] += 1
            return None
        if role is InstrumentRole.UNKNOWN and not self._config.allow_unknown_roles:
            raise InstrumentValidationError(
                "Unknown role.",
                code="IL.VALIDATION.UNKNOWN_ROLE",
            )
        expiry = _parse_expiry(raw.get("expiry"))
        strike = _number(raw.get("strike"), float, "strike", optional=True)
        lot = _number(raw.get("lot_size", 1), int, "lot_size")
        tick = _number(raw.get("tick_size", 0.05), float, "tick_size")
        assert isinstance(lot, int) and isinstance(tick, float)
        if lot < 1:
            raise InstrumentValidationError(
                "Invalid lot size.",
                code="IL.VALIDATION.INVALID_LOT_SIZE",
            )
        if tick <= 0 or not math.isfinite(tick):
            raise InstrumentValidationError(
                "Invalid tick size.",
                code="IL.VALIDATION.INVALID_TICK_SIZE",
            )
        option_type = (
            instrument_type if instrument_type in SUPPORTED_OPTION_TYPES else None
        )
        if role in (InstrumentRole.OPTION_CE, InstrumentRole.OPTION_PE):
            if not expiry:
                raise InstrumentValidationError(
                    "Missing expiry.",
                    code="IL.VALIDATION.MISSING_EXPIRY",
                )
            if (
                not isinstance(strike, float)
                or strike <= 0
                or not math.isfinite(strike)
            ):
                raise InstrumentValidationError(
                    "Invalid strike.",
                    code="IL.VALIDATION.INVALID_STRIKE",
                )
            if option_type not in SUPPORTED_OPTION_TYPES:
                raise InstrumentValidationError(
                    "Invalid option type.",
                    code="IL.VALIDATION.INVALID_OPTION_TYPE",
                )
            if not self._config.include_options:
                return None
        if role is InstrumentRole.FUTURE:
            if not expiry:
                raise InstrumentValidationError(
                    "Missing expiry.",
                    code="IL.VALIDATION.MISSING_EXPIRY",
                )
            if not self._config.include_futures:
                return None
        if role is InstrumentRole.SPOT and (
            strike is not None or not self._config.include_index_spot
        ):
            return None
        if (
            role is InstrumentRole.VOLATILITY_INDEX
            and not self._config.include_volatility_index
        ):
            return None
        exchange_token = _number(
            raw.get("exchange_token"), int, "exchange_token", optional=True
        )
        metadata = raw.get("metadata", {})
        quote_override = None
        if isinstance(metadata, Mapping):
            quote_override = metadata.get("quote_key_override")
        quote_key = (
            str(quote_override)
            if quote_override
            else f"{exchange}:{symbol}"
        )
        return InstrumentRecord(
            token,
            exchange_token if isinstance(exchange_token, int) else None,
            symbol,
            name.upper(),
            underlying,
            exchange,
            instrument_type,
            role,
            str(raw["segment"]).strip() if raw.get("segment") else None,
            expiry,
            strike if isinstance(strike, float) else None,
            option_type,
            lot,
            tick,
            quote_key,
            tier,
            bool(expiry and expiry < as_of),
            name,
            {str(k): str(v) for k, v in dict(metadata).items()}
            if isinstance(metadata, Mapping)
            else {},
        )

    def load_from_cache(self) -> InstrumentCatalog:
        """Load a fresh versioned cache and atomically publish it."""
        self._begin()
        try:
            path = self._cache_path()
            try: payload = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc: raise InstrumentLoaderIOError("Cache not found.", code="IL.IO.FILE_NOT_FOUND") from exc
            except json.JSONDecodeError as exc: raise InstrumentLoaderIOError("Cache corrupt.", code="IL.IO.CACHE_CORRUPT") from exc
            expected = "sha256:" + hashlib.sha256(json.dumps(payload.get("catalog"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if payload.get("checksum") != expected: raise InstrumentLoaderIOError("Cache checksum mismatch.", code="IL.IO.CACHE_CORRUPT")
            catalog = replace(
                deserialize_instrument_catalog(payload["catalog"]),
                source_kind=InstrumentSourceKind.CACHE,
                source_uri=str(path),
            )
            age = (_utc(self._clock()) - catalog.loaded_at).total_seconds()
            if (
                age > self._config.cache_max_age_seconds
                and not self._config.allow_stale_cache
            ):
                raise InstrumentLoaderIOError(
                    "Cache stale.",
                    code="IL.IO.CACHE_STALE",
                )
            self._catalog = catalog
            self._statistics = catalog.statistics
            self._last_error = None
            self._last_source = (InstrumentSourceKind.CACHE, str(path), None)
            with self._state_lock:
                self._state = CatalogLifecycleState.READY
            self._load_lock.release()
            return catalog
        except InstrumentLoaderError as exc:
            self._finish_failure(exc)
            raise

    def save_cache(self, path: str | Path | None = None) -> Path:
        """Atomically persist the current catalog as a checksummed JSON cache."""
        catalog = self.get_catalog()
        target = Path(path) if path else self._cache_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            body = serialize_instrument_catalog(catalog)
            checksum = "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(json.dumps({"schema_version": INSTRUMENT_LOADER_SCHEMA_VERSION, "catalog": body, "checksum": checksum, "written_at": _iso(_utc(self._clock()))}, sort_keys=True), encoding="utf-8")
            temporary.replace(target)
            return target
        except OSError as exc: raise InstrumentLoaderIOError("Cache write failed.", code="IL.IO.WRITE_FAILED") from exc

    def _cache_path(self) -> Path:
        if not self._config.cache_directory: raise InstrumentLoaderIOError("Cache directory unavailable.", code="IL.IO.FILE_NOT_FOUND")
        return Path(self._config.cache_directory) / self._config.cache_filename

    def reload(self) -> InstrumentCatalog:
        """Reload the most recently successful reloadable source."""
        if self._last_source is None: raise InstrumentLoaderStateError("No previous source.", code="IL.STATE.RELOAD_UNSUPPORTED")
        kind, uri, exchanges = self._last_source
        if kind is InstrumentSourceKind.BROKER_DOWNLOAD: return self.load_from_broker(exchanges=exchanges)
        if kind in (InstrumentSourceKind.LOCAL_CSV, InstrumentSourceKind.LOCAL_JSON) and uri: return self.load_from_file(uri, source_kind=kind)
        if kind is InstrumentSourceKind.CACHE: return self.load_from_cache()
        raise InstrumentLoaderStateError("In-memory rows cannot reload.", code="IL.STATE.RELOAD_UNSUPPORTED")

    def get_catalog(self) -> InstrumentCatalog:
        """Return the current immutable catalog."""
        if self._state is CatalogLifecycleState.CLOSED: raise InstrumentLoaderStateError("Loader is closed.", code="IL.STATE.CLOSED")
        catalog = self._catalog
        if catalog is None: raise InstrumentLoaderStateError("Catalog is not ready.", code="IL.STATE.NOT_READY")
        return catalog

    def get_statistics(self) -> CatalogStatistics:
        """Return statistics from the latest load attempt."""
        return self._statistics

    def get_health(self) -> CatalogHealth:
        """Return a deterministic health snapshot of the current catalog."""
        now, catalog = _utc(self._clock()), self._catalog
        if catalog is None:
            issue = CatalogHealthIssue("IL.HEALTH.NO_CATALOG", "error", "No sealed catalog is available.")
            return CatalogHealth("health-" + str(now.timestamp()), now, self._state, CatalogHealthStatus.UNHEALTHY if self._state is CatalogLifecycleState.DEGRADED else CatalogHealthStatus.UNKNOWN, False, None, 0, self._config.enabled_underlyings, (), self._config.enabled_underlyings, None, (issue,), self._statistics)
        present = tuple(item for item in self._config.enabled_underlyings if item in catalog.indexes.by_underlying)
        missing = tuple(item for item in self._config.enabled_underlyings if item not in present)
        issues = tuple(CatalogHealthIssue("IL.HEALTH.UNDERLYING_MISSING", "warning", "Configured underlying has no records.", item) for item in missing)
        status = CatalogHealthStatus.HEALTHY if not issues and self._state is CatalogLifecycleState.READY else CatalogHealthStatus.DEGRADED
        return CatalogHealth("health-" + catalog.catalog_id, now, self._state, status, True, catalog.catalog_id, catalog.record_count,
            self._config.enabled_underlyings, present, missing, max(0.0, (now - catalog.loaded_at).total_seconds()), issues, catalog.statistics)

    def _result(self, name: str, records: Sequence[InstrumentRecord], *, miss: str, diagnostics: Mapping[str, Any] | None = None) -> LookupResult:
        found = tuple(records)
        return LookupResult(LookupStatus.HIT if found else LookupStatus.MISS, name, found, found[0] if found else None, None if found else miss, None, diagnostics or {})

    def get_by_token(self, instrument_token: int) -> LookupResult:
        """Look up one record by positive instrument token."""
        if instrument_token <= 0: return LookupResult(LookupStatus.REJECTED, "get_by_token", reason_code="IL.LOOKUP.INVALID_TOKEN")
        item = self.get_catalog().indexes.by_token.get(instrument_token)
        return self._result("get_by_token", (item,) if item else (), miss="IL.LOOKUP.TOKEN_NOT_FOUND")

    def get_by_tradingsymbol(self, exchange: str, tradingsymbol: str) -> LookupResult:
        """Look up one record by exchange and trading symbol."""
        item = self.get_catalog().indexes.by_tradingsymbol.get((exchange.strip().upper(), tradingsymbol.strip()))
        return self._result("get_by_tradingsymbol", (item,) if item else (), miss="IL.LOOKUP.SYMBOL_NOT_FOUND")

    def get_by_quote_key(self, quote_key: str) -> LookupResult:
        """Look up one record by its canonical quote key."""
        item = self.get_catalog().indexes.by_quote_key.get(quote_key.strip())
        return self._result("get_by_quote_key", (item,) if item else (), miss="IL.LOOKUP.SYMBOL_NOT_FOUND")

    def get_by_underlying(self, underlying: str) -> LookupResult:
        """Return all retained records for one underlying."""
        return self._result("get_by_underlying", self.get_catalog().indexes.by_underlying.get(normalize_underlying_name(underlying), ()), miss="IL.LOOKUP.UNDERLYING_EMPTY")

    def get_by_underlying_and_expiry(self, underlying: str, expiry: str) -> LookupResult:
        """Return all derivatives for an underlying expiry."""
        key = (normalize_underlying_name(underlying), _parse_expiry(expiry))
        return self._result("get_by_underlying_and_expiry", self.get_catalog().indexes.by_underlying_expiry.get(key, ()), miss="IL.LOOKUP.EXPIRY_NOT_FOUND")

    def get_options(self, underlying: str, *, expiry: str | None = None, strike: float | None = None, option_type: str | None = None) -> LookupResult:
        """Return options matching optional expiry, strike, and type predicates."""
        records = self.get_by_underlying(underlying).records
        wanted = option_type.upper() if option_type else None
        found = tuple(item for item in records if item.instrument_role in (InstrumentRole.OPTION_CE, InstrumentRole.OPTION_PE) and
                      (expiry is None or item.expiry == _parse_expiry(expiry)) and (strike is None or item.strike == float(strike)) and (wanted is None or item.option_type == wanted))
        return self._result("get_options", found, miss="IL.LOOKUP.EXPIRY_NOT_FOUND")

    def get_futures(self, underlying: str, *, expiry: str | None = None) -> LookupResult:
        """Return futures matching an optional expiry."""
        found = tuple(item for item in self.get_by_underlying(underlying).records if item.instrument_role is InstrumentRole.FUTURE and (expiry is None or item.expiry == _parse_expiry(expiry)))
        return self._result("get_futures", found, miss="IL.LOOKUP.EXPIRY_NOT_FOUND")

    def get_spot(self, underlying: str) -> LookupResult:
        """Return loaded index spot records for an underlying."""
        found = tuple(item for item in self.get_by_underlying(underlying).records if item.instrument_role is InstrumentRole.SPOT)
        return self._result("get_spot", found, miss="IL.LOOKUP.UNDERLYING_EMPTY")

    def find_nearest_expiry(self, underlying: str, *, as_of: date | None = None, kind: str = "option") -> LookupResult:
        """Find the first non-past expiry for options or futures."""
        catalog, name = self.get_catalog(), normalize_underlying_name(underlying)
        expiries = catalog.indexes.option_expiries.get(name, ()) if kind == "option" else catalog.indexes.future_expiries.get(name, ())
        if not expiries: return self._result("find_nearest_expiry", (), miss="IL.LOOKUP.EXPIRY_NOT_FOUND")
        today = as_of or _utc(self._clock()).astimezone(ZoneInfo(self._config.expiry_timezone)).date()
        selected = next((item for item in expiries if date.fromisoformat(item) >= today), expiries[-1])
        records = self.get_options(name, expiry=selected).records if kind == "option" else self.get_futures(name, expiry=selected).records
        diag = {"reason_code": "IL.LOOKUP.PAST_ONLY_EXPIRY"} if date.fromisoformat(selected) < today else {}
        return self._result("find_nearest_expiry", records, miss="IL.LOOKUP.EXPIRY_NOT_FOUND", diagnostics=diag)

    def _monthly(self, underlying: str, as_of: date | None, monthly: bool, limit: int | None) -> LookupResult:
        expiries = self.get_catalog().indexes.option_expiries.get(normalize_underlying_name(underlying), ())
        today = as_of or _utc(self._clock()).date()
        future = [item for item in expiries if date.fromisoformat(item) >= today]
        month_last = {max(item for item in future if item[:7] == month) for month in {item[:7] for item in future}}
        chosen = [item for item in future if (item in month_last) == monthly][:limit]
        records = tuple(record for item in chosen for record in self.get_options(underlying, expiry=item).records)
        return self._result("find_monthly_expiries" if monthly else "find_weekly_expiries", records, miss="IL.LOOKUP.EXPIRY_NOT_FOUND", diagnostics={"expiries": tuple(chosen)})

    def find_weekly_expiries(self, underlying: str, *, as_of: date | None = None, limit: int | None = None) -> LookupResult:
        """Return non-month-end option expiries using the documented heuristic."""
        return self._monthly(underlying, as_of, False, limit)

    def find_monthly_expiries(self, underlying: str, *, as_of: date | None = None, limit: int | None = None) -> LookupResult:
        """Return last option expiry in each month."""
        return self._monthly(underlying, as_of, True, limit)

    def find_closest_expiry(self, underlying: str, *, target: date, kind: str = "option") -> LookupResult:
        """Return records at the expiry nearest target, breaking ties later."""
        name = normalize_underlying_name(underlying)
        expiries = self.get_catalog().indexes.option_expiries.get(name, ()) if kind == "option" else self.get_catalog().indexes.future_expiries.get(name, ())
        if not expiries: return self._result("find_closest_expiry", (), miss="IL.LOOKUP.EXPIRY_NOT_FOUND")
        selected = min(expiries, key=lambda item: (abs(date.fromisoformat(item) - target), -date.fromisoformat(item).toordinal()))
        return self.get_options(name, expiry=selected) if kind == "option" else self.get_futures(name, expiry=selected)

    def resolve_atm_strike(self, underlying: str, *, spot: float, expiry: str, strike_step: float | None = None) -> float:
        """Resolve the available strike nearest the configured grid-snapped spot."""
        if not math.isfinite(spot): raise InstrumentLookupError("Spot is invalid.", code="IL.LOOKUP.EXPIRY_NOT_FOUND")
        name, parsed = normalize_underlying_name(underlying), _parse_expiry(expiry)
        step = strike_step or self._config.strike_step.get(name, self._config.default_strike_step)
        if step <= 0: raise InstrumentLookupError("Strike step invalid.", code="IL.LOOKUP.EXPIRY_NOT_FOUND")
        snapped = round(spot / step) * step
        strikes = self.get_catalog().indexes.strikes.get((name, parsed), ())
        return min(strikes, key=lambda item: (abs(item - snapped), item)) if strikes else snapped

    def find_nearest_strike(self, underlying: str, *, expiry: str, target_price: float) -> LookupResult:
        """Return CE/PE records at the available strike nearest target."""
        name, parsed = normalize_underlying_name(underlying), _parse_expiry(expiry)
        strikes = self.get_catalog().indexes.strikes.get((name, parsed), ())
        if not strikes: return self._result("find_nearest_strike", (), miss="IL.LOOKUP.EXPIRY_NOT_FOUND")
        chosen = min(strikes, key=lambda item: (abs(item - target_price), item))
        return self._result("find_nearest_strike", self.get_catalog().indexes.by_underlying_expiry_strike[(name, parsed, chosen)], miss="IL.LOOKUP.EXPIRY_NOT_FOUND")

    def query_atm_options(self, underlying: str, *, spot: float, expiry: str) -> LookupResult:
        """Return CE and PE records at the resolved ATM strike."""
        return self.get_options(underlying, expiry=expiry, strike=self.resolve_atm_strike(underlying, spot=spot, expiry=expiry))

    def _moneyness(self, underlying: str, spot: float, expiry: str, option_type: str, depth: int, itm: bool) -> LookupResult:
        if depth < 1 or option_type.upper() not in SUPPORTED_OPTION_TYPES: return LookupResult(LookupStatus.REJECTED, "query_moneyness", reason_code="IL.LOOKUP.EXPIRY_NOT_FOUND")
        name, parsed, kind = normalize_underlying_name(underlying), _parse_expiry(expiry), option_type.upper()
        strikes = self.get_catalog().indexes.strikes.get((name, parsed), ())
        candidates = [value for value in strikes if (value < spot if kind == "CE" else value > spot) == itm]
        selected = sorted(candidates, key=lambda value: abs(value - spot))[:depth]
        records = tuple(record for value in selected for record in self.get_options(name, expiry=parsed, strike=value, option_type=kind).records)
        return self._result("query_itm_options" if itm else "query_otm_options", records, miss="IL.LOOKUP.EXPIRY_NOT_FOUND")

    def query_itm_options(self, underlying: str, *, spot: float, expiry: str, option_type: str, depth: int = 1) -> LookupResult:
        """Return requested in-the-money option strikes nearest the spot."""
        return self._moneyness(underlying, spot, expiry, option_type, depth, True)

    def query_otm_options(self, underlying: str, *, spot: float, expiry: str, option_type: str, depth: int = 1) -> LookupResult:
        """Return requested out-of-the-money option strikes nearest the spot."""
        return self._moneyness(underlying, spot, expiry, option_type, depth, False)

    def get_lot_size(self, underlying: str, *, expiry: str | None = None) -> int:
        """Return a deterministic derivative lot size."""
        result = self.get_options(underlying, expiry=expiry) if expiry else self.find_nearest_expiry(underlying)
        lots = sorted({record.lot_size for record in result.records})
        if not lots: raise InstrumentLookupError("Lot size unavailable.", code="IL.LOOKUP.EXPIRY_NOT_FOUND")
        if len(lots) > 1 and self._config.strict_validation: raise InstrumentLookupError("Ambiguous lot size.", code="IL.LOOKUP.AMBIGUOUS_LOT_SIZE")
        return lots[0]

    def _projection_records(self, underlying: str, expiry: str | None, spot: float | None, strikes_each_side: int, include_futures: bool, include_spot: bool, include_vix: bool) -> tuple[InstrumentRecord, ...]:
        if strikes_each_side < 0: raise InstrumentLookupError("Negative strike window.", code="IL.LOOKUP.EXPIRY_NOT_FOUND")
        name = normalize_underlying_name(underlying)
        resolved = expiry or (self.find_nearest_expiry(name).primary.expiry if self.find_nearest_expiry(name).primary else None)
        options = self.get_options(name, expiry=resolved).records if resolved else ()
        grid = self.get_catalog().indexes.strikes.get((name, resolved), ()) if resolved else ()
        if grid:
            center = self.resolve_atm_strike(name, spot=spot if spot is not None else grid[len(grid)//2], expiry=resolved)
            index = grid.index(center); selected = set(grid[max(0, index-strikes_each_side):index+strikes_each_side+1])
            options = tuple(item for item in options if item.strike in selected)
        extras: list[InstrumentRecord] = []
        if include_spot: extras.extend(self.get_spot(name).records)
        if include_futures: extras.extend(self.find_nearest_expiry(name, kind="future").records)
        if include_vix: extras.extend(item for item in self.get_catalog().records if item.instrument_role is InstrumentRole.VOLATILITY_INDEX and item.underlying == name)
        return tuple(sorted((*extras, *options), key=lambda item: item.instrument_token))

    def project_descriptors(self, underlying: str, *, expiry: str | None = None, spot: float | None = None, strikes_each_side: int = 5,
                            include_futures: bool | None = None, include_spot: bool | None = None, include_volatility_index: bool | None = None) -> tuple[Any, ...]:
        """Project matching records into streaming descriptor DTOs."""
        from broker.market_data_streaming import InstrumentDescriptor, InstrumentRole as StreamingRole, UnderlyingSupportTier as StreamingTier
        records = self._projection_records(underlying, expiry, spot, strikes_each_side, self._config.include_futures if include_futures is None else include_futures,
                                           self._config.include_index_spot if include_spot is None else include_spot, self._config.include_volatility_index if include_volatility_index is None else include_volatility_index)
        return tuple(InstrumentDescriptor(record.instrument_token, record.underlying, record.quote_key, record.exchange, record.tradingsymbol, record.instrument_type,
            StreamingRole.UNKNOWN if record.instrument_role is InstrumentRole.EQUITY else StreamingRole(record.instrument_role.value),
            record.strike, record.option_type, record.expiry, record.lot_size, record.tick_size,
            StreamingTier.EXPERIMENTAL if record.support_tier is UnderlyingSupportTier.EQUITY_FO else StreamingTier(record.support_tier.value),
            record.metadata) for record in records)

    def project_subscriptions(self, underlying: str, *, expiry: str | None = None, spot: float | None = None, strikes_each_side: int = 5, mode: str | None = None) -> tuple[Any, ...]:
        """Project matching records into WebSocket subscription DTOs."""
        from broker.kite_websocket import SubscriptionInstrument
        records = self._projection_records(underlying, expiry, spot, strikes_each_side, self._config.include_futures, self._config.include_index_spot, self._config.include_volatility_index)
        return tuple(SubscriptionInstrument(record.instrument_token, record.underlying, record.quote_key, record.exchange, record.tradingsymbol, record.instrument_type, mode, record.metadata) for record in records)
