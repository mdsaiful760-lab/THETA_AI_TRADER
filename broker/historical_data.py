"""Validated, immutable historical OHLCV retrieval and caching.

This module is the sole historical candle authority for THETA AI TRADER v1.0.
Broker transport is supplied through narrow protocols.  Authentication,
streaming, trading, and analytical calculations remain out of scope.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time as clock_time, timedelta, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from config.application_configuration import EnvironmentProfile

HISTORICAL_DATA_VERSION = "1.0.0"
HISTORICAL_DATA_SCHEMA_VERSION = "1.0.0"
PRODUCER_NAME = "broker.historical_data"
SUPPORTED_PRIMARY_UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})
SUPPORTED_SECONDARY_UNDERLYINGS = frozenset({"FINNIFTY", "MIDCPNIFTY"})
SUPPORTED_INDEX_UNDERLYINGS = SUPPORTED_PRIMARY_UNDERLYINGS | SUPPORTED_SECONDARY_UNDERLYINGS
TOPIC_SERIES_FETCHED = "market.historical.series.fetched"
TOPIC_SERIES_FAILED = "market.historical.series.failed"
TOPIC_CACHE_UPDATED = "market.historical.cache.updated"
DEFAULT_MEMORY_CACHE_MAX_ENTRIES = 256
DEFAULT_MEMORY_CACHE_TTL_SECONDS = 60.0
DEFAULT_DISK_CACHE_TTL_SECONDS = 86_400.0
DEFAULT_MAX_CANDLES_PER_REQUEST = 20_000
DEFAULT_BROKER_CHUNK_DAYS = 60
DEFAULT_MAX_GAP_RATIO = 0.05
DEFAULT_SESSION_OPEN = "09:15"
DEFAULT_SESSION_CLOSE = "15:30"
DEFAULT_SESSION_TIMEZONE = "Asia/Kolkata"
DEFAULT_HOLIDAY_SLACK_DAYS = 3
_EMPTY_STR_MAPPING: Mapping[str, str] = MappingProxyType({})
_EMPTY_INT_MAPPING: Mapping[str, int] = MappingProxyType({})


class CandleTimeframe(str, Enum):
    """Supported historical candle granularity."""

    MINUTE_1 = "MINUTE_1"
    MINUTE_3 = "MINUTE_3"
    MINUTE_5 = "MINUTE_5"
    MINUTE_10 = "MINUTE_10"
    MINUTE_15 = "MINUTE_15"
    MINUTE_30 = "MINUTE_30"
    MINUTE_60 = "MINUTE_60"
    DAY_1 = "DAY_1"


class UnderlyingSupportTier(str, Enum):
    """Support classification of an underlying."""

    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    EQUITY_FO = "EQUITY_FO"
    EXPERIMENTAL = "EXPERIMENTAL"


class HistoricalLifecycleState(str, Enum):
    """Lifecycle state of a historical-data service."""

    CREATED = "CREATED"
    READY = "READY"
    DEGRADED = "DEGRADED"
    CLOSED = "CLOSED"


class HistoricalHealthStatus(str, Enum):
    """Aggregated historical-data health."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class SeriesValidationStatus(str, Enum):
    """Quality status of a sealed series."""

    VALID = "VALID"
    PARTIAL = "PARTIAL"
    INVALID = "INVALID"


class GapSeverity(str, Enum):
    """Severity classification for missing expected bars."""

    NONE = "NONE"
    MINOR = "MINOR"
    MAJOR = "MAJOR"
    CRITICAL = "CRITICAL"


class MissingCandlePolicy(str, Enum):
    """Policy for expected candles absent from a series."""

    MARK_GAP = "MARK_GAP"
    FAIL = "FAIL"
    FORWARD_FILL = "FORWARD_FILL"


class DuplicatePolicy(str, Enum):
    """Deterministic duplicate timestamp policy."""

    KEEP_FIRST_STABLE = "KEEP_FIRST_STABLE"
    KEEP_LAST_STABLE = "KEEP_LAST_STABLE"
    REJECT = "REJECT"


class CorporateActionPolicy(str, Enum):
    """Corporate-action awareness policy."""

    ATTACH_METADATA_ONLY = "ATTACH_METADATA_ONLY"
    APPLY_ADJUSTMENTS = "APPLY_ADJUSTMENTS"
    REJECT_IF_UNADJUSTED = "REJECT_IF_UNADJUSTED"


class CandleSourceKind(str, Enum):
    """Origin of a sealed series."""

    BROKER_FETCH = "BROKER_FETCH"
    MEMORY_CACHE = "MEMORY_CACHE"
    DISK_CACHE = "DISK_CACHE"
    IN_MEMORY_ROWS = "IN_MEMORY_ROWS"
    MERGED = "MERGED"


_INTERVALS: Mapping[CandleTimeframe, str] = MappingProxyType({
    CandleTimeframe.MINUTE_1: "minute",
    CandleTimeframe.MINUTE_3: "3minute",
    CandleTimeframe.MINUTE_5: "5minute",
    CandleTimeframe.MINUTE_10: "10minute",
    CandleTimeframe.MINUTE_15: "15minute",
    CandleTimeframe.MINUTE_30: "30minute",
    CandleTimeframe.MINUTE_60: "60minute",
    CandleTimeframe.DAY_1: "day",
})
_DURATIONS: Mapping[CandleTimeframe, timedelta] = MappingProxyType({
    CandleTimeframe.MINUTE_1: timedelta(minutes=1),
    CandleTimeframe.MINUTE_3: timedelta(minutes=3),
    CandleTimeframe.MINUTE_5: timedelta(minutes=5),
    CandleTimeframe.MINUTE_10: timedelta(minutes=10),
    CandleTimeframe.MINUTE_15: timedelta(minutes=15),
    CandleTimeframe.MINUTE_30: timedelta(minutes=30),
    CandleTimeframe.MINUTE_60: timedelta(minutes=60),
})


class HistoricalDataError(Exception):
    """Base exception carrying a stable code and request attribution."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        field: str | None = None,
        underlying: str | None = None,
        instrument_token: int | None = None,
        timeframe: CandleTimeframe | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.field = field
        self.underlying = underlying
        self.instrument_token = instrument_token
        self.timeframe = timeframe


class HistoricalDataConfigurationError(HistoricalDataError):
    """Invalid configuration."""


class HistoricalDataStateError(HistoricalDataError):
    """Invalid service lifecycle state."""


class HistoricalDataValidationError(HistoricalDataError):
    """Invalid candle or request."""


class HistoricalDataIOError(HistoricalDataError):
    """Broker or cache I/O failure."""


class HistoricalDataSerializationError(HistoricalDataError):
    """Malformed serialized payload."""


class HistoricalDataLookupError(HistoricalDataError):
    """Instrument identity lookup failure."""


def _freeze_str(mapping: Mapping[str, str] | None) -> Mapping[str, str]:
    return MappingProxyType(dict(mapping or {}))


def _freeze_int(mapping: Mapping[str, int] | None) -> Mapping[str, int]:
    return MappingProxyType({str(key): int(value) for key, value in dict(mapping or {}).items()})


@dataclass(frozen=True)
class HistoricalCandle:
    """One immutable, UTC-normalized OHLCV candle."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: int | None
    instrument_token: int
    timeframe: CandleTimeframe
    exchange_timestamp: datetime | None = None
    is_adjusted: bool = False
    gap_after: bool = False
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_STR_MAPPING)


@dataclass(frozen=True)
class HistoricalSeries:
    """Sealed, ordered historical candle sequence."""

    series_id: str
    schema_version: str
    instrument_token: int
    tradingsymbol: str | None
    underlying: str | None
    exchange: str | None
    timeframe: CandleTimeframe
    candles: tuple[HistoricalCandle, ...]
    candle_count: int
    start: datetime | None
    end: datetime | None
    validation_status: SeriesValidationStatus
    gap_count: int
    gap_severity: GapSeverity
    missing_bar_count: int
    duplicate_count: int
    source_kind: CandleSourceKind
    fetched_at: datetime
    as_of: datetime
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_STR_MAPPING)


@dataclass(frozen=True)
class HistoricalHealthIssue:
    """One structured health concern."""

    issue_code: str
    severity: str
    message: str
    underlying: str | None = None
    instrument_token: int | None = None
    timeframe: CandleTimeframe | None = None


@dataclass(frozen=True)
class HistoricalStatistics:
    """Immutable fetch/cache statistics snapshot."""

    as_of: datetime
    total_fetch_count: int = 0
    total_fetch_success_count: int = 0
    total_fetch_error_count: int = 0
    memory_cache_hit_count: int = 0
    memory_cache_miss_count: int = 0
    disk_cache_hit_count: int = 0
    disk_cache_miss_count: int = 0
    series_sealed_count: int = 0
    validation_reject_count: int = 0
    gap_detection_count: int = 0
    average_fetch_latency_ms: float | None = None
    max_fetch_latency_ms: float | None = None
    error_rate: float = 0.0
    candles_served_count: int = 0
    last_error_code: str | None = None
    per_timeframe: Mapping[str, int] = field(default_factory=lambda: _EMPTY_INT_MAPPING)


@dataclass(frozen=True)
class HistoricalHealth:
    """Immutable historical-data health snapshot."""

    report_id: str
    as_of: datetime
    lifecycle_state: HistoricalLifecycleState
    overall_health: HistoricalHealthStatus
    memory_cache_entries: int
    disk_cache_enabled: bool
    disk_cache_healthy: bool
    last_fetch_latency_ms: float | None
    error_rate: float
    issues: tuple[HistoricalHealthIssue, ...]
    statistics: HistoricalStatistics
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_STR_MAPPING)


@dataclass(frozen=True)
class HistoricalDataConfig:
    """Configuration projection for :class:`HistoricalDataService`."""

    enabled_underlyings: tuple[str, ...]
    enabled_timeframes: tuple[CandleTimeframe, ...] = tuple(CandleTimeframe)
    default_timeframe: CandleTimeframe = CandleTimeframe.MINUTE_5
    session_timezone: str = DEFAULT_SESSION_TIMEZONE
    session_open: str = DEFAULT_SESSION_OPEN
    session_close: str = DEFAULT_SESSION_CLOSE
    include_partial_bar: bool = False
    strict_validation: bool = False
    duplicate_policy: DuplicatePolicy | str = DuplicatePolicy.KEEP_LAST_STABLE
    missing_candle_policy: MissingCandlePolicy | str = MissingCandlePolicy.MARK_GAP
    max_gap_ratio: float = DEFAULT_MAX_GAP_RATIO
    require_non_empty_series: bool = True
    max_candles_per_request: int = DEFAULT_MAX_CANDLES_PER_REQUEST
    memory_cache_enabled: bool = True
    memory_cache_max_entries: int = DEFAULT_MEMORY_CACHE_MAX_ENTRIES
    memory_cache_ttl_seconds: float = DEFAULT_MEMORY_CACHE_TTL_SECONDS
    disk_cache_enabled: bool = True
    disk_cache_directory: str | None = None
    disk_cache_ttl_seconds: float = DEFAULT_DISK_CACHE_TTL_SECONDS
    prefer_cache_before_fetch: bool = True
    continuous: bool = False
    corporate_action_policy: CorporateActionPolicy | str = CorporateActionPolicy.ATTACH_METADATA_ONLY
    allow_equity_fo: bool = False
    enabled_equity_underlyings: tuple[str, ...] = ()
    allow_experimental_underlyings: bool = False
    publish_events: bool = False
    environment_profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT
    runner_kind: str = "unknown"
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_STR_MAPPING)
    allow_concurrent_fetches: bool = False
    broker_chunk_days: int = DEFAULT_BROKER_CHUNK_DAYS
    allow_stale_disk_cache: bool = False
    holiday_slack_days: int = DEFAULT_HOLIDAY_SLACK_DAYS

    def __post_init__(self) -> None:
        names = tuple(item.strip().upper() for item in self.enabled_underlyings)
        if not names:
            raise HistoricalDataConfigurationError(
                "enabled_underlyings is required",
                code="HD.CONFIG.UNDERLYING_REQUIRED",
            )
        if len(set(names)) != len(names):
            raise HistoricalDataConfigurationError(
                "duplicate underlyings",
                code="HD.CONFIG.UNDERLYING_DUPLICATE",
            )
        equities = {item.strip().upper() for item in self.enabled_equity_underlyings}
        for name in names:
            if name in SUPPORTED_INDEX_UNDERLYINGS:
                continue
            if self.allow_equity_fo and name in equities:
                continue
            if self.allow_experimental_underlyings:
                continue
            raise HistoricalDataConfigurationError(
                f"unsupported underlying: {name}",
                code="HD.CONFIG.UNDERLYING_UNSUPPORTED",
                underlying=name,
            )
        if (
            not self.enabled_timeframes
            or any(not isinstance(item, CandleTimeframe) for item in self.enabled_timeframes)
            or self.default_timeframe not in self.enabled_timeframes
        ):
            raise HistoricalDataConfigurationError(
                "invalid timeframes",
                code="HD.CONFIG.TIMEFRAME_INVALID",
            )
        try:
            ZoneInfo(self.session_timezone)
            opened = _parse_session(self.session_open)
            closed = _parse_session(self.session_close)
        except (ValueError, KeyError):
            raise HistoricalDataConfigurationError(
                "invalid session",
                code="HD.CONFIG.SESSION_INVALID",
            ) from None
        if opened >= closed:
            raise HistoricalDataConfigurationError(
                "invalid session",
                code="HD.CONFIG.SESSION_INVALID",
            )
        try:
            duplicate = DuplicatePolicy(self.duplicate_policy)
            missing = MissingCandlePolicy(self.missing_candle_policy)
            corporate = CorporateActionPolicy(self.corporate_action_policy)
        except ValueError as exc:
            raise HistoricalDataConfigurationError(
                "invalid policy",
                code="HD.CONFIG.POLICY_INVALID",
            ) from exc
        if (
            not 0.0 <= self.max_gap_ratio <= 1.0
            or self.max_candles_per_request < 1
            or self.memory_cache_max_entries < 1
            or self.memory_cache_ttl_seconds < 0
            or self.disk_cache_ttl_seconds < 0
            or self.broker_chunk_days < 1
            or self.holiday_slack_days < 0
        ):
            raise HistoricalDataConfigurationError(
                "invalid threshold",
                code="HD.CONFIG.THRESHOLD_OUT_OF_RANGE",
            )
        if (
            self.disk_cache_enabled
            and self.environment_profile in (EnvironmentProfile.PAPER, EnvironmentProfile.PRODUCTION)
            and not self.disk_cache_directory
        ):
            raise HistoricalDataConfigurationError(
                "disk path required",
                code="HD.CONFIG.CACHE_PATH_REQUIRED",
            )
        if (
            missing is MissingCandlePolicy.FORWARD_FILL
            and self.environment_profile is EnvironmentProfile.PRODUCTION
        ):
            raise HistoricalDataConfigurationError(
                "forward fill is forbidden in PRODUCTION",
                code="HD.CONFIG.POLICY_INVALID",
            )
        object.__setattr__(self, "enabled_underlyings", names)
        object.__setattr__(self, "enabled_equity_underlyings", tuple(sorted(equities)))
        object.__setattr__(self, "duplicate_policy", duplicate)
        object.__setattr__(self, "missing_candle_policy", missing)
        object.__setattr__(self, "corporate_action_policy", corporate)
        object.__setattr__(self, "metadata", _freeze_str(self.metadata))


class HistoricalDataClient(Protocol):
    """Boundary for broker historical REST transport."""

    def fetch_historical_rows(
        self,
        *,
        instrument_token: int,
        from_ts: datetime,
        to_ts: datetime,
        interval: str,
        continuous: bool = False,
    ) -> Sequence[Mapping[str, Any]]:
        """Return raw broker candle mappings."""


class InstrumentTokenResolver(Protocol):
    """Boundary for exchange/symbol identity resolution."""

    def resolve_token(self, *, exchange: str, tradingsymbol: str) -> int:
        """Resolve trading symbol to instrument token."""


def _parse_session(value: str) -> clock_time:
    return datetime.strptime(value, "%H:%M").time()


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError("timestamp must be datetime or ISO-8601 string")


def normalize_candle_timestamp(
    value: datetime,
    *,
    assume_tz: str = DEFAULT_SESSION_TIMEZONE,
) -> datetime:
    """Return ``value`` normalized to timezone-aware UTC.

    Args:
        value: Broker or public datetime.
        assume_tz: Zone used when ``value`` is naive.

    Returns:
        Timezone-aware UTC datetime.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(assume_tz))
    return value.astimezone(timezone.utc)


def to_kite_interval(timeframe: CandleTimeframe) -> str:
    """Map one supported timeframe to its broker interval string.

    Args:
        timeframe: Supported candle timeframe.

    Returns:
        Zerodha/Kite interval wire value.
    """
    try:
        return _INTERVALS[timeframe]
    except KeyError as exc:
        raise HistoricalDataValidationError(
            "unsupported timeframe",
            code="HD.CONFIG.TIMEFRAME_INVALID",
        ) from exc


def default_historical_data_config(
    profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT,
    *,
    enabled_underlyings: Sequence[str] = ("NIFTY",),
) -> HistoricalDataConfig:
    """Create the documented profile defaults.

    Args:
        profile: Runtime environment profile.
        enabled_underlyings: Canonical underlyings allowlist.

    Returns:
        Validated configuration instance.
    """
    if profile is EnvironmentProfile.DEVELOPMENT:
        return HistoricalDataConfig(
            enabled_underlyings=tuple(enabled_underlyings),
            environment_profile=profile,
            disk_cache_enabled=False,
            memory_cache_ttl_seconds=30.0,
            allow_experimental_underlyings=True,
            publish_events=False,
        )
    if profile is EnvironmentProfile.PAPER:
        return HistoricalDataConfig(
            enabled_underlyings=tuple(enabled_underlyings),
            environment_profile=profile,
            disk_cache_enabled=True,
            disk_cache_directory="cache/historical",
            allow_experimental_underlyings=False,
            publish_events=True,
        )
    return HistoricalDataConfig(
        enabled_underlyings=tuple(enabled_underlyings),
        environment_profile=profile,
        disk_cache_enabled=True,
        disk_cache_directory="cache/historical",
        missing_candle_policy=MissingCandlePolicy.MARK_GAP,
        allow_experimental_underlyings=False,
        publish_events=True,
        strict_validation=True,
    )


def _validate_candle(candle: HistoricalCandle) -> None:
    if candle.instrument_token <= 0:
        raise HistoricalDataValidationError(
            "invalid token",
            code="HD.VALIDATION.INVALID_TOKEN",
            instrument_token=candle.instrument_token,
        )
    if candle.timestamp.tzinfo is None:
        raise HistoricalDataValidationError(
            "naive timestamp",
            code="HD.VALIDATION.NAIVE_TIMESTAMP",
        )
    if any(not math.isfinite(price) or price <= 0 for price in (candle.open, candle.high, candle.low, candle.close)):
        raise HistoricalDataValidationError(
            "invalid price",
            code="HD.VALIDATION.INVALID_PRICE",
        )
    if (
        candle.high < candle.low
        or candle.high < max(candle.open, candle.close)
        or candle.low > min(candle.open, candle.close)
    ):
        raise HistoricalDataValidationError(
            "inconsistent OHLC",
            code="HD.VALIDATION.OHLC_INCONSISTENT",
        )
    if candle.volume < 0:
        raise HistoricalDataValidationError(
            "invalid volume",
            code="HD.VALIDATION.INVALID_VOLUME",
        )
    if candle.oi is not None and candle.oi < 0:
        raise HistoricalDataValidationError(
            "invalid OI",
            code="HD.VALIDATION.INVALID_OI",
        )


def serialize_historical_candle(value: HistoricalCandle) -> dict[str, Any]:
    """Serialize a candle into a JSON-safe mapping."""
    return {
        "schema_version": HISTORICAL_DATA_SCHEMA_VERSION,
        "timestamp": _utc_iso(value.timestamp),
        "open": value.open,
        "high": value.high,
        "low": value.low,
        "close": value.close,
        "volume": value.volume,
        "oi": value.oi,
        "instrument_token": value.instrument_token,
        "timeframe": value.timeframe.value,
        "exchange_timestamp": _utc_iso(value.exchange_timestamp) if value.exchange_timestamp else None,
        "is_adjusted": value.is_adjusted,
        "gap_after": value.gap_after,
        "metadata": dict(value.metadata),
    }


def _check_payload(payload: Mapping[str, Any]) -> None:
    version = str(payload.get("schema_version", HISTORICAL_DATA_SCHEMA_VERSION))
    if version.split(".", 1)[0] != HISTORICAL_DATA_SCHEMA_VERSION.split(".", 1)[0]:
        raise HistoricalDataSerializationError(
            "unsupported schema",
            code="HD.SERIALIZATION.UNSUPPORTED_VERSION",
        )


def deserialize_historical_candle(payload: Mapping[str, Any]) -> HistoricalCandle:
    """Deserialize and validate a candle."""
    try:
        _check_payload(payload)
        exchange_raw = payload.get("exchange_timestamp")
        candle = HistoricalCandle(
            timestamp=normalize_candle_timestamp(_parse_datetime(payload["timestamp"])),
            open=float(payload["open"]),
            high=float(payload["high"]),
            low=float(payload["low"]),
            close=float(payload["close"]),
            volume=int(payload["volume"]),
            oi=int(payload["oi"]) if payload.get("oi") is not None else None,
            instrument_token=int(payload["instrument_token"]),
            timeframe=CandleTimeframe(payload["timeframe"]),
            exchange_timestamp=(
                normalize_candle_timestamp(_parse_datetime(exchange_raw))
                if exchange_raw is not None
                else None
            ),
            is_adjusted=bool(payload.get("is_adjusted", False)),
            gap_after=bool(payload.get("gap_after", False)),
            metadata=_freeze_str(payload.get("metadata")),
        )
        _validate_candle(candle)
        return candle
    except HistoricalDataError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise HistoricalDataSerializationError(
            "malformed candle",
            code="HD.SERIALIZATION.MALFORMED",
        ) from exc


def serialize_historical_series(value: HistoricalSeries) -> dict[str, Any]:
    """Serialize a sealed series."""
    return {
        "schema_version": HISTORICAL_DATA_SCHEMA_VERSION,
        "series_id": value.series_id,
        "instrument_token": value.instrument_token,
        "tradingsymbol": value.tradingsymbol,
        "underlying": value.underlying,
        "exchange": value.exchange,
        "timeframe": value.timeframe.value,
        "candles": [serialize_historical_candle(item) for item in value.candles],
        "validation_status": value.validation_status.value,
        "gap_count": value.gap_count,
        "gap_severity": value.gap_severity.value,
        "missing_bar_count": value.missing_bar_count,
        "duplicate_count": value.duplicate_count,
        "source_kind": value.source_kind.value,
        "fetched_at": _utc_iso(value.fetched_at),
        "as_of": _utc_iso(value.as_of),
        "warnings": list(value.warnings),
        "errors": list(value.errors),
        "metadata": dict(value.metadata),
    }


def deserialize_historical_series(payload: Mapping[str, Any]) -> HistoricalSeries:
    """Deserialize a sealed series and revalidate its ordering."""
    try:
        _check_payload(payload)
        candles = tuple(deserialize_historical_candle(item) for item in payload["candles"])
        if any(left.timestamp >= right.timestamp for left, right in zip(candles, candles[1:])):
            raise HistoricalDataSerializationError(
                "unordered series",
                code="HD.SERIALIZATION.MALFORMED",
            )
        return HistoricalSeries(
            series_id=str(payload["series_id"]),
            schema_version=HISTORICAL_DATA_SCHEMA_VERSION,
            instrument_token=int(payload["instrument_token"]),
            tradingsymbol=payload.get("tradingsymbol"),
            underlying=payload.get("underlying"),
            exchange=payload.get("exchange"),
            timeframe=CandleTimeframe(payload["timeframe"]),
            candles=candles,
            candle_count=len(candles),
            start=candles[0].timestamp if candles else None,
            end=candles[-1].timestamp if candles else None,
            validation_status=SeriesValidationStatus(payload["validation_status"]),
            gap_count=int(payload["gap_count"]),
            gap_severity=GapSeverity(payload["gap_severity"]),
            missing_bar_count=int(payload["missing_bar_count"]),
            duplicate_count=int(payload["duplicate_count"]),
            source_kind=CandleSourceKind(payload["source_kind"]),
            fetched_at=normalize_candle_timestamp(_parse_datetime(payload["fetched_at"])),
            as_of=normalize_candle_timestamp(_parse_datetime(payload["as_of"])),
            warnings=tuple(payload.get("warnings", ())),
            errors=tuple(payload.get("errors", ())),
            metadata=_freeze_str(payload.get("metadata")),
        )
    except HistoricalDataError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise HistoricalDataSerializationError(
            "malformed series",
            code="HD.SERIALIZATION.MALFORMED",
        ) from exc


def serialize_historical_statistics(value: HistoricalStatistics) -> dict[str, Any]:
    """Serialize statistics."""
    return {
        "schema_version": HISTORICAL_DATA_SCHEMA_VERSION,
        "as_of": _utc_iso(value.as_of),
        "total_fetch_count": value.total_fetch_count,
        "total_fetch_success_count": value.total_fetch_success_count,
        "total_fetch_error_count": value.total_fetch_error_count,
        "memory_cache_hit_count": value.memory_cache_hit_count,
        "memory_cache_miss_count": value.memory_cache_miss_count,
        "disk_cache_hit_count": value.disk_cache_hit_count,
        "disk_cache_miss_count": value.disk_cache_miss_count,
        "series_sealed_count": value.series_sealed_count,
        "validation_reject_count": value.validation_reject_count,
        "gap_detection_count": value.gap_detection_count,
        "average_fetch_latency_ms": value.average_fetch_latency_ms,
        "max_fetch_latency_ms": value.max_fetch_latency_ms,
        "error_rate": value.error_rate,
        "candles_served_count": value.candles_served_count,
        "last_error_code": value.last_error_code,
        "per_timeframe": dict(value.per_timeframe),
    }


def deserialize_historical_statistics(payload: Mapping[str, Any]) -> HistoricalStatistics:
    """Deserialize statistics."""
    try:
        _check_payload(payload)
        return HistoricalStatistics(
            as_of=normalize_candle_timestamp(_parse_datetime(payload["as_of"])),
            total_fetch_count=int(payload.get("total_fetch_count", 0)),
            total_fetch_success_count=int(payload.get("total_fetch_success_count", 0)),
            total_fetch_error_count=int(payload.get("total_fetch_error_count", 0)),
            memory_cache_hit_count=int(payload.get("memory_cache_hit_count", 0)),
            memory_cache_miss_count=int(payload.get("memory_cache_miss_count", 0)),
            disk_cache_hit_count=int(payload.get("disk_cache_hit_count", 0)),
            disk_cache_miss_count=int(payload.get("disk_cache_miss_count", 0)),
            series_sealed_count=int(payload.get("series_sealed_count", 0)),
            validation_reject_count=int(payload.get("validation_reject_count", 0)),
            gap_detection_count=int(payload.get("gap_detection_count", 0)),
            average_fetch_latency_ms=payload.get("average_fetch_latency_ms"),
            max_fetch_latency_ms=payload.get("max_fetch_latency_ms"),
            error_rate=float(payload.get("error_rate", 0.0)),
            candles_served_count=int(payload.get("candles_served_count", 0)),
            last_error_code=payload.get("last_error_code"),
            per_timeframe=_freeze_int(payload.get("per_timeframe")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HistoricalDataSerializationError(
            "malformed statistics",
            code="HD.SERIALIZATION.MALFORMED",
        ) from exc


def serialize_historical_health(value: HistoricalHealth) -> dict[str, Any]:
    """Serialize health."""
    return {
        "schema_version": HISTORICAL_DATA_SCHEMA_VERSION,
        "report_id": value.report_id,
        "as_of": _utc_iso(value.as_of),
        "lifecycle_state": value.lifecycle_state.value,
        "overall_health": value.overall_health.value,
        "memory_cache_entries": value.memory_cache_entries,
        "disk_cache_enabled": value.disk_cache_enabled,
        "disk_cache_healthy": value.disk_cache_healthy,
        "last_fetch_latency_ms": value.last_fetch_latency_ms,
        "error_rate": value.error_rate,
        "issues": [
            {
                "issue_code": issue.issue_code,
                "severity": issue.severity,
                "message": issue.message,
                "underlying": issue.underlying,
                "instrument_token": issue.instrument_token,
                "timeframe": issue.timeframe.value if issue.timeframe else None,
            }
            for issue in value.issues
        ],
        "statistics": serialize_historical_statistics(value.statistics),
        "metadata": dict(value.metadata),
    }


def deserialize_historical_health(payload: Mapping[str, Any]) -> HistoricalHealth:
    """Deserialize health."""
    try:
        _check_payload(payload)
        issues = tuple(
            HistoricalHealthIssue(
                issue_code=str(item["issue_code"]),
                severity=str(item["severity"]),
                message=str(item["message"]),
                underlying=item.get("underlying"),
                instrument_token=item.get("instrument_token"),
                timeframe=CandleTimeframe(item["timeframe"]) if item.get("timeframe") else None,
            )
            for item in payload.get("issues", ())
        )
        return HistoricalHealth(
            report_id=str(payload["report_id"]),
            as_of=normalize_candle_timestamp(_parse_datetime(payload["as_of"])),
            lifecycle_state=HistoricalLifecycleState(payload["lifecycle_state"]),
            overall_health=HistoricalHealthStatus(payload["overall_health"]),
            memory_cache_entries=int(payload["memory_cache_entries"]),
            disk_cache_enabled=bool(payload["disk_cache_enabled"]),
            disk_cache_healthy=bool(payload["disk_cache_healthy"]),
            last_fetch_latency_ms=payload.get("last_fetch_latency_ms"),
            error_rate=float(payload["error_rate"]),
            issues=issues,
            statistics=deserialize_historical_statistics(payload["statistics"]),
            metadata=_freeze_str(payload.get("metadata")),
        )
    except HistoricalDataError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise HistoricalDataSerializationError(
            "malformed health",
            code="HD.SERIALIZATION.MALFORMED",
        ) from exc


def historical_candle_to_json(value: HistoricalCandle) -> str:
    """Serialize a candle to compact JSON."""
    return json.dumps(serialize_historical_candle(value), sort_keys=True, separators=(",", ":"))


def historical_candle_from_json(value: str) -> HistoricalCandle:
    """Deserialize a candle from JSON."""
    return deserialize_historical_candle(json.loads(value))


def historical_series_to_json(value: HistoricalSeries) -> str:
    """Serialize a series to compact JSON."""
    return json.dumps(serialize_historical_series(value), sort_keys=True, separators=(",", ":"))


def historical_series_from_json(value: str) -> HistoricalSeries:
    """Deserialize a series from JSON."""
    return deserialize_historical_series(json.loads(value))


def historical_statistics_to_json(value: HistoricalStatistics) -> str:
    """Serialize statistics to compact JSON."""
    return json.dumps(serialize_historical_statistics(value), sort_keys=True, separators=(",", ":"))


def historical_statistics_from_json(value: str) -> HistoricalStatistics:
    """Deserialize statistics from JSON."""
    return deserialize_historical_statistics(json.loads(value))


def historical_health_to_json(value: HistoricalHealth) -> str:
    """Serialize health to compact JSON."""
    return json.dumps(serialize_historical_health(value), sort_keys=True, separators=(",", ":"))


def historical_health_from_json(value: str) -> HistoricalHealth:
    """Deserialize health from JSON."""
    return deserialize_historical_health(json.loads(value))


class HistoricalCache:
    """Thread-safe LRU cache with optional checksummed disk persistence."""

    def __init__(self, config: HistoricalDataConfig, *, clock: Callable[[], datetime]) -> None:
        self._config = config
        self._clock = clock
        self._items: OrderedDict[str, tuple[datetime, HistoricalSeries]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._disk_hits = 0
        self._disk_misses = 0

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return Path(self._config.disk_cache_directory or ".") / f"hd_{digest}.json"

    def _ttl_for(self, series: HistoricalSeries) -> float:
        ttl = self._config.memory_cache_ttl_seconds
        if series.timeframe is CandleTimeframe.DAY_1:
            return max(ttl, 300.0)
        return ttl

    def get(self, key: str) -> HistoricalSeries | None:
        """Return a fresh series from memory or disk, if available."""
        now = self._clock()
        with self._lock:
            entry = self._items.get(key)
            if entry is not None:
                written_at, series = entry
                if (now - written_at).total_seconds() <= self._ttl_for(series):
                    self._items.move_to_end(key)
                    self._hits += 1
                    return replace(series, source_kind=CandleSourceKind.MEMORY_CACHE)
                self._items.pop(key, None)
            self._misses += 1
        if not self._config.disk_cache_enabled:
            return None
        return self._get_disk(key, now)

    def _get_disk(self, key: str, now: datetime) -> HistoricalSeries | None:
        try:
            path = self._path(key)
            if not path.exists():
                self._disk_misses += 1
                return None
            document = json.loads(path.read_text(encoding="utf-8"))
            payload = document["series"]
            checksum = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
            )
            if document.get("cache_key") != key or document.get("checksum") != checksum:
                raise HistoricalDataIOError("corrupt cache", code="HD.IO.CACHE_CORRUPT")
            age = (now - normalize_candle_timestamp(_parse_datetime(document["written_at"]))).total_seconds()
            if age > self._config.disk_cache_ttl_seconds and not self._config.allow_stale_disk_cache:
                self._disk_misses += 1
                return None
            series = replace(
                deserialize_historical_series(payload),
                source_kind=CandleSourceKind.DISK_CACHE,
            )
            self.put(key, series, disk=False)
            self._disk_hits += 1
            return series
        except HistoricalDataError:
            self._disk_misses += 1
            return None
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            self._disk_misses += 1
            return None

    def put(self, key: str, series: HistoricalSeries, *, disk: bool = True) -> None:
        """Atomically store an immutable series."""
        now = self._clock()
        with self._lock:
            if self._config.memory_cache_enabled:
                self._items[key] = (now, series)
                self._items.move_to_end(key)
                while len(self._items) > self._config.memory_cache_max_entries:
                    self._items.popitem(last=False)
        if disk and self._config.disk_cache_enabled:
            self._put_disk(key, series, now)

    def _put_disk(self, key: str, series: HistoricalSeries, now: datetime) -> None:
        try:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = serialize_historical_series(series)
            document = {
                "schema_version": HISTORICAL_DATA_SCHEMA_VERSION,
                "cache_key": key,
                "checksum": (
                    "sha256:"
                    + hashlib.sha256(
                        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    ).hexdigest()
                ),
                "written_at": _utc_iso(now),
                "series": payload,
            }
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(document, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError as exc:
            raise HistoricalDataIOError("cache write failed", code="HD.IO.WRITE_FAILED") from exc

    def invalidate(self, key: str | None = None) -> None:
        """Invalidate one key or all cache entries."""
        with self._lock:
            keys = [key] if key is not None else list(self._items)
            if key is None:
                self._items.clear()
            else:
                self._items.pop(key, None)
        if self._config.disk_cache_enabled:
            for item in keys:
                if item is None:
                    continue
                try:
                    self._path(item).unlink(missing_ok=True)
                except OSError:
                    pass

    def stats(self) -> Mapping[str, int]:
        """Return cache counters and current memory entry count."""
        with self._lock:
            return MappingProxyType({
                "entries": len(self._items),
                "memory_hits": self._hits,
                "memory_misses": self._misses,
                "disk_hits": self._disk_hits,
                "disk_misses": self._disk_misses,
            })


class HistoricalDataService:
    """Lifecycle facade for validated historical series."""

    def __init__(
        self,
        config: HistoricalDataConfig,
        *,
        client: HistoricalDataClient | None = None,
        resolver: InstrumentTokenResolver | None = None,
        event_bus: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        holiday_calendar: frozenset[date] | None = None,
        adjustment_factors: Mapping[str, float] | None = None,
        corporate_action_dates: frozenset[date] | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._resolver = resolver
        self._event_bus = event_bus
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._holiday_calendar = holiday_calendar
        self._adjustment_factors = dict(adjustment_factors or {})
        self._corporate_action_dates = corporate_action_dates or frozenset()
        self._cache = HistoricalCache(config, clock=self._clock)
        self._state = HistoricalLifecycleState.CREATED
        self._lock = threading.RLock()
        self._inflight: dict[str, threading.Event] = {}
        self._results: dict[str, HistoricalSeries | HistoricalDataError] = {}
        self._counters: dict[str, Any] = {
            "fetch": 0,
            "success": 0,
            "error": 0,
            "sealed": 0,
            "rejected": 0,
            "gaps": 0,
            "served": 0,
            "latencies": [],
            "last_error": None,
            "per_timeframe": {},
        }

    def get_status(self) -> HistoricalLifecycleState:
        """Return the current lifecycle state."""
        return self._state

    def close(self) -> None:
        """Close the service and reject future operations."""
        with self._lock:
            self._state = HistoricalLifecycleState.CLOSED

    def _assert_open(self) -> None:
        if self._state is HistoricalLifecycleState.CLOSED:
            raise HistoricalDataStateError("service is closed", code="HD.STATE.CLOSED")

    def _publish(self, topic: str, payload: Mapping[str, Any]) -> None:
        if self._config.publish_events and self._event_bus is not None:
            try:
                self._event_bus.publish(topic, payload)
            except Exception:
                pass

    def _identity(
        self,
        token: int | None,
        exchange: str | None,
        symbol: str | None,
    ) -> tuple[int, str | None, str | None]:
        has_token = token is not None
        has_symbol = exchange is not None or symbol is not None
        if has_token and has_symbol:
            raise HistoricalDataLookupError(
                "provide exactly one of instrument_token or exchange+tradingsymbol",
                code="HD.LOOKUP.TOKEN_REQUIRED",
            )
        if has_token:
            assert token is not None
            if token <= 0:
                raise HistoricalDataValidationError(
                    "invalid token",
                    code="HD.VALIDATION.INVALID_TOKEN",
                    instrument_token=token,
                )
            return token, exchange, symbol
        if not exchange or not symbol:
            raise HistoricalDataLookupError(
                "identity required",
                code="HD.LOOKUP.TOKEN_REQUIRED",
            )
        if self._resolver is None:
            raise HistoricalDataStateError(
                "resolver unavailable",
                code="HD.STATE.RESOLVER_NOT_CONFIGURED",
            )
        try:
            resolved = int(self._resolver.resolve_token(exchange=exchange, tradingsymbol=symbol))
        except Exception as exc:
            raise HistoricalDataLookupError(
                "symbol unresolved",
                code="HD.LOOKUP.SYMBOL_UNRESOLVED",
            ) from exc
        if resolved <= 0:
            raise HistoricalDataLookupError(
                "symbol unresolved",
                code="HD.LOOKUP.SYMBOL_UNRESOLVED",
            )
        return resolved, exchange, symbol

    def _timestamp(self, value: datetime, field_name: str) -> datetime:
        if value.tzinfo is None:
            raise HistoricalDataValidationError(
                "naive public timestamp",
                code="HD.VALIDATION.NAIVE_TIMESTAMP",
                field=field_name,
            )
        return value.astimezone(timezone.utc)

    def _key(
        self,
        token: int,
        timeframe: CandleTimeframe,
        start: datetime,
        end: datetime,
        continuous: bool,
    ) -> str:
        return (
            f"hd:{token}:{timeframe.value}:{_utc_iso(start)}:{_utc_iso(end)}"
            f":c={str(continuous).lower()}:p={str(self._config.include_partial_bar).lower()}"
        )

    def fetch_range(
        self,
        *,
        instrument_token: int | None = None,
        exchange: str | None = None,
        tradingsymbol: str | None = None,
        timeframe: CandleTimeframe | None = None,
        from_ts: datetime,
        to_ts: datetime,
        underlying: str | None = None,
        continuous: bool | None = None,
    ) -> HistoricalSeries:
        """Fetch, validate, seal, and cache an inclusive time range."""
        self._assert_open()
        start = self._timestamp(from_ts, "from_ts")
        end = self._timestamp(to_ts, "to_ts")
        if start > end:
            raise HistoricalDataValidationError(
                "invalid range",
                code="HD.VALIDATION.INVALID_RANGE",
            )
        tf = timeframe or self._config.default_timeframe
        if tf not in self._config.enabled_timeframes:
            raise HistoricalDataValidationError(
                "disabled timeframe",
                code="HD.CONFIG.TIMEFRAME_INVALID",
            )
        if underlying and underlying.strip().upper() not in self._config.enabled_underlyings:
            raise HistoricalDataLookupError(
                "unsupported underlying",
                code="HD.CONFIG.UNDERLYING_UNSUPPORTED",
                underlying=underlying,
            )
        token, resolved_exchange, resolved_symbol = self._identity(
            instrument_token,
            exchange,
            tradingsymbol,
        )
        cont = self._config.continuous if continuous is None else continuous
        key = self._key(token, tf, start, end, cont)
        if self._config.prefer_cache_before_fetch:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        return self._fetch_with_coalesce(
            key=key,
            token=token,
            start=start,
            end=end,
            timeframe=tf,
            continuous=cont,
            underlying=underlying,
            tradingsymbol=resolved_symbol or tradingsymbol,
            exchange=resolved_exchange or exchange,
        )

    def _fetch_with_coalesce(
        self,
        *,
        key: str,
        token: int,
        start: datetime,
        end: datetime,
        timeframe: CandleTimeframe,
        continuous: bool,
        underlying: str | None,
        tradingsymbol: str | None,
        exchange: str | None,
    ) -> HistoricalSeries:
        owner = False
        event: threading.Event | None = None
        with self._lock:
            if not self._config.allow_concurrent_fetches:
                existing = self._inflight.get(key)
                if existing is not None:
                    event = existing
                else:
                    event = threading.Event()
                    self._inflight[key] = event
                    owner = True
            else:
                owner = True
        if not owner:
            assert event is not None
            event.wait()
            with self._lock:
                outcome = self._results.get(key)
            if isinstance(outcome, HistoricalSeries):
                return outcome
            if isinstance(outcome, HistoricalDataError):
                raise outcome
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            raise HistoricalDataStateError(
                "coalesced fetch failed",
                code="HD.STATE.FETCH_IN_PROGRESS",
            )
        try:
            if self._client is None:
                raise HistoricalDataStateError(
                    "client unavailable",
                    code="HD.STATE.CLIENT_NOT_CONFIGURED",
                )
            rows = self._fetch_rows(token, start, end, timeframe, continuous)
            series = self._seal(
                rows,
                instrument_token=token,
                timeframe=timeframe,
                underlying=underlying,
                tradingsymbol=tradingsymbol,
                exchange=exchange,
                source=CandleSourceKind.BROKER_FETCH,
                range_start=start,
                range_end=end,
            )
            self._cache.put(key, series)
            self._state = HistoricalLifecycleState.READY
            with self._lock:
                self._results[key] = series
            self._publish(
                TOPIC_SERIES_FETCHED,
                {
                    "series_id": series.series_id,
                    "instrument_token": series.instrument_token,
                    "timeframe": series.timeframe.value,
                    "candle_count": series.candle_count,
                    "correlation_id": series.series_id,
                },
            )
            self._publish(
                TOPIC_CACHE_UPDATED,
                {"cache_key": key, "correlation_id": series.series_id},
            )
            return series
        except HistoricalDataError as exc:
            self._failure(exc.code)
            with self._lock:
                self._results[key] = exc
            self._publish(
                TOPIC_SERIES_FAILED,
                {
                    "code": exc.code,
                    "message": exc.message,
                    "correlation_id": self._id_factory(),
                },
            )
            raise
        finally:
            if event is not None:
                with self._lock:
                    self._inflight.pop(key, None)
                event.set()

    def _fetch_rows(
        self,
        token: int,
        start: datetime,
        end: datetime,
        timeframe: CandleTimeframe,
        continuous: bool,
    ) -> list[Mapping[str, Any]]:
        assert self._client is not None
        output: list[Mapping[str, Any]] = []
        cursor = start
        chunk_span = timedelta(days=self._config.broker_chunk_days)
        while cursor <= end:
            chunk_end = min(end, cursor + chunk_span)
            began = time.perf_counter()
            with self._lock:
                self._counters["fetch"] += 1
            try:
                output.extend(
                    self._client.fetch_historical_rows(
                        instrument_token=token,
                        from_ts=cursor,
                        to_ts=chunk_end,
                        interval=to_kite_interval(timeframe),
                        continuous=continuous,
                    )
                )
            except Exception as exc:
                raise HistoricalDataIOError(
                    "broker fetch failed",
                    code="HD.IO.BROKER_FETCH_FAILED",
                ) from exc
            latency = (time.perf_counter() - began) * 1000.0
            with self._lock:
                self._counters["success"] += 1
                self._counters["latencies"].append(latency)
            if chunk_end >= end:
                break
            cursor = chunk_end + timedelta(microseconds=1)
        return output

    def load_from_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        instrument_token: int,
        timeframe: CandleTimeframe,
        underlying: str | None = None,
        tradingsymbol: str | None = None,
    ) -> HistoricalSeries:
        """Validate and seal supplied broker-shaped rows without transport."""
        self._assert_open()
        if instrument_token <= 0:
            raise HistoricalDataValidationError(
                "invalid token",
                code="HD.VALIDATION.INVALID_TOKEN",
                instrument_token=instrument_token,
            )
        series = self._seal(
            rows,
            instrument_token=instrument_token,
            timeframe=timeframe,
            underlying=underlying,
            tradingsymbol=tradingsymbol,
            exchange=None,
            source=CandleSourceKind.IN_MEMORY_ROWS,
            range_start=None,
            range_end=None,
        )
        self._state = HistoricalLifecycleState.READY
        return series

    def _seal(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        instrument_token: int,
        timeframe: CandleTimeframe,
        underlying: str | None,
        tradingsymbol: str | None,
        exchange: str | None,
        source: CandleSourceKind,
        range_start: datetime | None,
        range_end: datetime | None,
    ) -> HistoricalSeries:
        kept: list[HistoricalCandle] = []
        warnings: list[str] = []
        rejected = 0
        for row in rows:
            try:
                stamp = _parse_datetime(row.get("date", row.get("timestamp")))
                normalized = normalize_candle_timestamp(
                    stamp,
                    assume_tz=self._config.session_timezone,
                )
                metadata = dict(row.get("metadata") or {})
                if "continuous" in row:
                    metadata.setdefault("continuous", str(bool(row["continuous"])).lower())
                if "adjusted" in row:
                    metadata.setdefault("adjusted", str(bool(row["adjusted"])).lower())
                candle = HistoricalCandle(
                    timestamp=normalized,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row.get("volume", 0)),
                    oi=int(row["oi"]) if row.get("oi") is not None else None,
                    instrument_token=instrument_token,
                    timeframe=timeframe,
                    exchange_timestamp=normalized,
                    is_adjusted=bool(row.get("is_adjusted", False)),
                    gap_after=False,
                    metadata=_freeze_str(metadata),
                )
                _validate_candle(candle)
                kept.append(candle)
            except (HistoricalDataError, KeyError, TypeError, ValueError) as exc:
                rejected += 1
                if self._config.strict_validation:
                    if isinstance(exc, HistoricalDataError):
                        raise
                    raise HistoricalDataValidationError(
                        "malformed candle",
                        code="HD.VALIDATION.INVALID_PRICE",
                    ) from exc
        kept = self._filter_partial_bars(kept, timeframe)
        if len(kept) > self._config.max_candles_per_request:
            raise HistoricalDataValidationError(
                "too many candles",
                code="HD.VALIDATION.TOO_MANY_CANDLES",
            )
        candles, duplicates = self._collapse_duplicates(kept)
        if any(left.timestamp >= right.timestamp for left, right in zip(candles, candles[1:])):
            raise HistoricalDataValidationError(
                "timestamps must be strictly ascending",
                code="HD.VALIDATION.TIMESTAMP_ORDER",
            )
        if not candles and self._config.require_non_empty_series:
            raise HistoricalDataValidationError(
                "empty series",
                code="HD.VALIDATION.EMPTY_SERIES",
            )
        gaps, holiday_warning = self._gaps(candles, timeframe, range_start, range_end)
        if holiday_warning:
            warnings.append(holiday_warning)
        missing = sum(len(group) for group in gaps)
        candles = self._apply_missing_policy(candles, gaps, instrument_token, timeframe, missing)
        if missing and self._config.missing_candle_policy is MissingCandlePolicy.MARK_GAP:
            warnings.append("HD.VALIDATION.MISSING_CANDLES")
        candles = self._apply_corporate_actions(candles, instrument_token)
        severity = self._severity(missing, missing + len(candles))
        status = (
            SeriesValidationStatus.PARTIAL
            if rejected or missing
            else SeriesValidationStatus.VALID
        )
        now = normalize_candle_timestamp(self._clock())
        result = HistoricalSeries(
            series_id=self._id_factory(),
            schema_version=HISTORICAL_DATA_SCHEMA_VERSION,
            instrument_token=instrument_token,
            tradingsymbol=tradingsymbol,
            underlying=underlying.strip().upper() if underlying else None,
            exchange=exchange,
            timeframe=timeframe,
            candles=tuple(candles),
            candle_count=len(candles),
            start=candles[0].timestamp if candles else None,
            end=candles[-1].timestamp if candles else None,
            validation_status=status,
            gap_count=len(gaps),
            gap_severity=severity,
            missing_bar_count=missing,
            duplicate_count=duplicates,
            source_kind=source,
            fetched_at=now,
            as_of=now,
            warnings=tuple(warnings),
            errors=(),
            metadata=_freeze_str(self._config.metadata),
        )
        with self._lock:
            self._counters["sealed"] += 1
            self._counters["rejected"] += rejected
            self._counters["gaps"] += int(bool(missing))
            self._counters["served"] += len(candles)
            per = self._counters["per_timeframe"]
            per[timeframe.value] = per.get(timeframe.value, 0) + 1
        return result

    def _filter_partial_bars(
        self,
        candles: Sequence[HistoricalCandle],
        timeframe: CandleTimeframe,
    ) -> list[HistoricalCandle]:
        if self._config.include_partial_bar or timeframe is CandleTimeframe.DAY_1:
            return list(candles)
        now = normalize_candle_timestamp(self._clock())
        duration = _DURATIONS[timeframe]
        return [candle for candle in candles if candle.timestamp + duration <= now]

    def _collapse_duplicates(
        self,
        candles: Sequence[HistoricalCandle],
    ) -> tuple[list[HistoricalCandle], int]:
        by_time: dict[datetime, HistoricalCandle] = {}
        duplicates = 0
        for candle in candles:
            if candle.timestamp in by_time:
                duplicates += 1
                if self._config.duplicate_policy is DuplicatePolicy.REJECT:
                    raise HistoricalDataValidationError(
                        "duplicate candle",
                        code="HD.VALIDATION.DUPLICATE_CANDLE",
                    )
                if self._config.duplicate_policy is DuplicatePolicy.KEEP_FIRST_STABLE:
                    continue
            by_time[candle.timestamp] = candle
        return sorted(by_time.values(), key=lambda item: item.timestamp), duplicates

    def _apply_missing_policy(
        self,
        candles: list[HistoricalCandle],
        gaps: list[list[datetime]],
        token: int,
        timeframe: CandleTimeframe,
        missing: int,
    ) -> list[HistoricalCandle]:
        if not missing:
            return candles
        if self._config.missing_candle_policy is MissingCandlePolicy.FAIL:
            raise HistoricalDataValidationError(
                "missing candles",
                code="HD.VALIDATION.MISSING_CANDLES",
            )
        if self._config.missing_candle_policy is MissingCandlePolicy.FORWARD_FILL:
            return self._forward_fill(candles, gaps, token, timeframe)
        gap_starts = {group[0] for group in gaps if group}
        marked: list[HistoricalCandle] = []
        for index, candle in enumerate(candles):
            next_ts = candles[index + 1].timestamp if index + 1 < len(candles) else None
            mark = any(
                candle.timestamp < gap_start and (next_ts is None or next_ts >= gap_start)
                for gap_start in gap_starts
            )
            marked.append(replace(candle, gap_after=True) if mark else candle)
        return marked

    def _apply_corporate_actions(
        self,
        candles: Sequence[HistoricalCandle],
        token: int,
    ) -> list[HistoricalCandle]:
        policy = self._config.corporate_action_policy
        if policy is CorporateActionPolicy.ATTACH_METADATA_ONLY:
            return list(candles)
        if policy is CorporateActionPolicy.APPLY_ADJUSTMENTS:
            if not self._adjustment_factors:
                raise HistoricalDataValidationError(
                    "adjustment factors required",
                    code="HD.CA.FACTORS_REQUIRED",
                    instrument_token=token,
                )
            adjusted: list[HistoricalCandle] = []
            for candle in candles:
                factor = float(self._adjustment_factors.get(_utc_iso(candle.timestamp)[:10], 1.0))
                adjusted.append(
                    replace(
                        candle,
                        open=candle.open * factor,
                        high=candle.high * factor,
                        low=candle.low * factor,
                        close=candle.close * factor,
                        is_adjusted=True,
                        metadata=_freeze_str({**dict(candle.metadata), "adjustment_factor": str(factor)}),
                    )
                )
            return adjusted
        if candles and self._corporate_action_dates:
            zone = ZoneInfo(self._config.session_timezone)
            start_day = candles[0].timestamp.astimezone(zone).date()
            end_day = candles[-1].timestamp.astimezone(zone).date()
            spans = any(start_day <= event <= end_day for event in self._corporate_action_dates)
            if spans and any(not candle.is_adjusted for candle in candles):
                raise HistoricalDataValidationError(
                    "unadjusted corporate-action span",
                    code="HD.CA.UNADJUSTED_SPAN",
                    instrument_token=token,
                )
        return list(candles)

    def _gaps(
        self,
        candles: Sequence[HistoricalCandle],
        timeframe: CandleTimeframe,
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[list[list[datetime]], str | None]:
        if not candles:
            return [], None
        zone = ZoneInfo(self._config.session_timezone)
        first = (start or candles[0].timestamp).astimezone(zone)
        last = (end or candles[-1].timestamp).astimezone(zone)
        expected: set[datetime] = set()
        holiday_warning: str | None = None
        if timeframe is CandleTimeframe.DAY_1:
            if self._holiday_calendar is None:
                holiday_warning = "HD.HEALTH.HOLIDAY_CALENDAR_MISSING"
            present_days = {candle.timestamp.astimezone(zone).date() for candle in candles}
            expected_days: set[date] = set()
            current = first.date()
            while current <= last.date():
                if current.weekday() < 5 and (
                    self._holiday_calendar is None or current not in self._holiday_calendar
                ):
                    expected_days.add(current)
                current += timedelta(days=1)
            for day in sorted(expected_days - present_days):
                expected.add(
                    datetime.combine(day, _parse_session(self._config.session_open), zone).astimezone(
                        timezone.utc
                    )
                )
            missing = sorted(expected)
            groups = [[item] for item in missing]
            return groups, holiday_warning
        else:
            duration = _DURATIONS[timeframe]
            current_day = first.date()
            while current_day <= last.date():
                if current_day.weekday() < 5 and (
                    self._holiday_calendar is None or current_day not in self._holiday_calendar
                ):
                    cursor = datetime.combine(
                        current_day,
                        _parse_session(self._config.session_open),
                        zone,
                    )
                    close = datetime.combine(
                        current_day,
                        _parse_session(self._config.session_close),
                        zone,
                    )
                    while cursor < close:
                        utc = cursor.astimezone(timezone.utc)
                        if utc >= first.astimezone(timezone.utc) and utc <= last.astimezone(timezone.utc):
                            expected.add(utc)
                        cursor += duration
                current_day += timedelta(days=1)
        present = {candle.timestamp for candle in candles}
        missing = sorted(expected - present)
        groups: list[list[datetime]] = []
        step = timedelta(days=1) if timeframe is CandleTimeframe.DAY_1 else _DURATIONS[timeframe]
        for item in missing:
            if not groups or item - groups[-1][-1] != step:
                groups.append([item])
            else:
                groups[-1].append(item)
        return groups, holiday_warning

    def _forward_fill(
        self,
        candles: list[HistoricalCandle],
        gaps: list[list[datetime]],
        token: int,
        timeframe: CandleTimeframe,
    ) -> list[HistoricalCandle]:
        values = {candle.timestamp: candle for candle in candles}
        for group in gaps:
            for stamp in group:
                prior = max(
                    (item for item in values.values() if item.timestamp < stamp),
                    key=lambda item: item.timestamp,
                    default=None,
                )
                if prior is None:
                    continue
                values[stamp] = HistoricalCandle(
                    timestamp=stamp,
                    open=prior.close,
                    high=prior.close,
                    low=prior.close,
                    close=prior.close,
                    volume=0,
                    oi=prior.oi,
                    instrument_token=token,
                    timeframe=timeframe,
                    metadata=MappingProxyType({"synthetic": "true"}),
                )
        return sorted(values.values(), key=lambda item: item.timestamp)

    def _severity(self, missing: int, expected: int) -> GapSeverity:
        ratio = missing / max(expected, 1)
        threshold = self._config.max_gap_ratio
        if not missing:
            return GapSeverity.NONE
        if ratio <= threshold:
            return GapSeverity.MINOR
        if ratio <= 3 * threshold:
            return GapSeverity.MAJOR
        return GapSeverity.CRITICAL

    def fetch_latest_n(
        self,
        *,
        instrument_token: int | None = None,
        exchange: str | None = None,
        tradingsymbol: str | None = None,
        timeframe: CandleTimeframe | None = None,
        n: int,
        underlying: str | None = None,
        as_of: datetime | None = None,
    ) -> HistoricalSeries:
        """Fetch up to ``n`` most recent bars."""
        if n < 1:
            raise HistoricalDataValidationError(
                "n must be positive",
                code="HD.VALIDATION.INVALID_RANGE",
            )
        tf = timeframe or self._config.default_timeframe
        end = self._timestamp(as_of or self._clock(), "as_of")
        session_length = timedelta(hours=6, minutes=15)
        if tf is CandleTimeframe.DAY_1:
            duration = timedelta(days=n + 2 + self._config.holiday_slack_days)
        else:
            duration = (
                _DURATIONS[tf] * n
                + session_length
                + timedelta(days=2 + self._config.holiday_slack_days)
            )
        result = self.fetch_range(
            instrument_token=instrument_token,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            timeframe=tf,
            from_ts=end - duration,
            to_ts=end,
            underlying=underlying,
        )
        candles = result.candles[-n:]
        return replace(
            result,
            candles=candles,
            candle_count=len(candles),
            start=candles[0].timestamp if candles else None,
            end=candles[-1].timestamp if candles else None,
        )

    def fetch_session(
        self,
        *,
        instrument_token: int | None = None,
        exchange: str | None = None,
        tradingsymbol: str | None = None,
        timeframe: CandleTimeframe | None = None,
        session_date: date,
        underlying: str | None = None,
    ) -> HistoricalSeries:
        """Fetch candles for one configured local trading session."""
        zone = ZoneInfo(self._config.session_timezone)
        start = datetime.combine(session_date, _parse_session(self._config.session_open), zone)
        end = datetime.combine(session_date, _parse_session(self._config.session_close), zone)
        return self.fetch_range(
            instrument_token=instrument_token,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            timeframe=timeframe,
            from_ts=start,
            to_ts=end,
            underlying=underlying,
        )

    def fetch_rolling_window(
        self,
        *,
        instrument_token: int | None = None,
        exchange: str | None = None,
        tradingsymbol: str | None = None,
        timeframe: CandleTimeframe | None = None,
        window: timedelta,
        as_of: datetime | None = None,
        underlying: str | None = None,
    ) -> HistoricalSeries:
        """Fetch a trailing duration ending at ``as_of``."""
        if window <= timedelta():
            raise HistoricalDataValidationError(
                "window must be positive",
                code="HD.VALIDATION.INVALID_RANGE",
            )
        end = self._timestamp(as_of or self._clock(), "as_of")
        return self.fetch_range(
            instrument_token=instrument_token,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            timeframe=timeframe,
            from_ts=end - window,
            to_ts=end,
            underlying=underlying,
        )

    def get_cached_series(self, cache_key: str) -> HistoricalSeries | None:
        """Retrieve one series by canonical cache key."""
        self._assert_open()
        return self._cache.get(cache_key)

    def invalidate_cache(self, cache_key: str | None = None) -> None:
        """Invalidate one or all cached series."""
        self._cache.invalidate(cache_key)

    def _failure(self, code: str) -> None:
        with self._lock:
            self._counters["error"] += 1
            self._counters["last_error"] = code
            self._state = HistoricalLifecycleState.DEGRADED

    def get_statistics(self) -> HistoricalStatistics:
        """Return immutable aggregate statistics."""
        with self._lock:
            counters = self._counters.copy()
            latencies = tuple(self._counters["latencies"])
            per_timeframe = dict(self._counters["per_timeframe"])
        cache = self._cache.stats()
        return HistoricalStatistics(
            as_of=normalize_candle_timestamp(self._clock()),
            total_fetch_count=counters["fetch"],
            total_fetch_success_count=counters["success"],
            total_fetch_error_count=counters["error"],
            memory_cache_hit_count=cache["memory_hits"],
            memory_cache_miss_count=cache["memory_misses"],
            disk_cache_hit_count=cache["disk_hits"],
            disk_cache_miss_count=cache["disk_misses"],
            series_sealed_count=counters["sealed"],
            validation_reject_count=counters["rejected"],
            gap_detection_count=counters["gaps"],
            average_fetch_latency_ms=(sum(latencies) / len(latencies)) if latencies else None,
            max_fetch_latency_ms=max(latencies) if latencies else None,
            error_rate=counters["error"] / max(counters["fetch"], 1),
            candles_served_count=counters["served"],
            last_error_code=counters["last_error"],
            per_timeframe=_freeze_int(per_timeframe),
        )

    def reset_statistics(self) -> None:
        """Reset service counters while retaining cache contents."""
        with self._lock:
            self._counters = {
                "fetch": 0,
                "success": 0,
                "error": 0,
                "sealed": 0,
                "rejected": 0,
                "gaps": 0,
                "served": 0,
                "latencies": [],
                "last_error": None,
                "per_timeframe": {},
            }

    def get_health(self) -> HistoricalHealth:
        """Return health derived from lifecycle, cache, and errors."""
        statistics = self.get_statistics()
        cache = self._cache.stats()
        issues: list[HistoricalHealthIssue] = []
        disk_healthy = True
        if self._client is None:
            issues.append(
                HistoricalHealthIssue(
                    "HD.HEALTH.NO_CLIENT",
                    "error",
                    "broker client is not configured",
                )
            )
        if self._config.disk_cache_enabled and not self._config.disk_cache_directory:
            disk_healthy = False
            issues.append(
                HistoricalHealthIssue(
                    "HD.HEALTH.DISK_CACHE_UNAVAILABLE",
                    "warning",
                    "disk cache directory is unavailable",
                )
            )
        if statistics.error_rate >= 0.05:
            issues.append(
                HistoricalHealthIssue(
                    "HD.HEALTH.HIGH_ERROR_RATE",
                    "warning",
                    "broker error rate is elevated",
                )
            )
        if (
            statistics.average_fetch_latency_ms is not None
            and statistics.average_fetch_latency_ms > 5_000.0
        ):
            issues.append(
                HistoricalHealthIssue(
                    "HD.HEALTH.HIGH_LATENCY",
                    "warning",
                    "average fetch latency is elevated",
                )
            )
        if self._holiday_calendar is None:
            issues.append(
                HistoricalHealthIssue(
                    "HD.HEALTH.HOLIDAY_CALENDAR_MISSING",
                    "info",
                    "holiday calendar missing; weekends-only exclusion in use",
                )
            )
        material_issues = tuple(issue for issue in issues if issue.severity in {"warning", "error"})
        if self._state is HistoricalLifecycleState.CREATED:
            health = HistoricalHealthStatus.UNKNOWN
        elif self._state is HistoricalLifecycleState.CLOSED or (
            statistics.total_fetch_count > 0 and statistics.total_fetch_success_count == 0
        ):
            health = HistoricalHealthStatus.UNHEALTHY
        elif self._state is HistoricalLifecycleState.DEGRADED or material_issues:
            health = HistoricalHealthStatus.DEGRADED
        else:
            health = HistoricalHealthStatus.HEALTHY
        return HistoricalHealth(
            report_id=self._id_factory(),
            as_of=normalize_candle_timestamp(self._clock()),
            lifecycle_state=self._state,
            overall_health=health,
            memory_cache_entries=cache["entries"],
            disk_cache_enabled=self._config.disk_cache_enabled,
            disk_cache_healthy=disk_healthy,
            last_fetch_latency_ms=statistics.average_fetch_latency_ms,
            error_rate=statistics.error_rate,
            issues=tuple(issues),
            statistics=statistics,
            metadata=_freeze_str(self._config.metadata),
        )


__all__ = [
    "HISTORICAL_DATA_VERSION",
    "HISTORICAL_DATA_SCHEMA_VERSION",
    "PRODUCER_NAME",
    "SUPPORTED_PRIMARY_UNDERLYINGS",
    "SUPPORTED_SECONDARY_UNDERLYINGS",
    "SUPPORTED_INDEX_UNDERLYINGS",
    "TOPIC_SERIES_FETCHED",
    "TOPIC_SERIES_FAILED",
    "TOPIC_CACHE_UPDATED",
    "CandleTimeframe",
    "UnderlyingSupportTier",
    "HistoricalLifecycleState",
    "HistoricalHealthStatus",
    "SeriesValidationStatus",
    "GapSeverity",
    "MissingCandlePolicy",
    "DuplicatePolicy",
    "CorporateActionPolicy",
    "CandleSourceKind",
    "HistoricalDataError",
    "HistoricalDataConfigurationError",
    "HistoricalDataStateError",
    "HistoricalDataValidationError",
    "HistoricalDataIOError",
    "HistoricalDataSerializationError",
    "HistoricalDataLookupError",
    "HistoricalCandle",
    "HistoricalSeries",
    "HistoricalHealthIssue",
    "HistoricalStatistics",
    "HistoricalHealth",
    "HistoricalDataConfig",
    "HistoricalDataClient",
    "InstrumentTokenResolver",
    "HistoricalCache",
    "HistoricalDataService",
    "normalize_candle_timestamp",
    "to_kite_interval",
    "default_historical_data_config",
    "serialize_historical_candle",
    "deserialize_historical_candle",
    "serialize_historical_series",
    "deserialize_historical_series",
    "serialize_historical_statistics",
    "deserialize_historical_statistics",
    "serialize_historical_health",
    "deserialize_historical_health",
    "historical_candle_to_json",
    "historical_candle_from_json",
    "historical_series_to_json",
    "historical_series_from_json",
    "historical_statistics_to_json",
    "historical_statistics_from_json",
    "historical_health_to_json",
    "historical_health_from_json",
]
