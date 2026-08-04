"""Continuous streaming market-data snapshot assembly for THETA AI TRADER.

This module is the sole streaming market-data assembly component. It
consumes normalized :class:`TickEvent` objects, maintains a thread-safe
latest-quote book per instrument, assembles per-underlying candidate
:class:`~market_data.market_snapshot.MarketSnapshot` instances, delegates
structural/semantic validation to
:func:`market_data.market_snapshot.validate_market_snapshot`, layers
streaming-only gates (staleness, coverage, throttling) on top, and
publishes accepted snapshots via callbacks and an optional Event Bus.

It never owns a WebSocket connection, never performs OAuth, never
redefines the canonical ``MarketSnapshot`` schema, and never evaluates
strategies, risk, or orders. See
``docs/specifications/market_data_streaming.md`` for the full
specification this module implements.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Literal, Protocol
from zoneinfo import ZoneInfo

from config.application_configuration import EnvironmentProfile
from core.event_bus import EventBus
from market_data.market_snapshot import (
    MarketSnapshot,
    OptionChainMetadata,
    OptionChainSnapshot,
    OptionContractSnapshot,
    OptionType,
    SnapshotBuildError,
    SnapshotFreshnessPolicy,
    SnapshotFreshnessStatus,
    SnapshotSource,
    SnapshotValidationStatus,
    UnderlyingSnapshot,
    ValidationPolicy,
    VolatilitySnapshot,
    build_market_snapshot,
)
from market_data.market_snapshot import to_dict as _snapshot_to_dict
from market_data.market_snapshot import from_dict as _snapshot_from_dict

MARKET_DATA_STREAMING_VERSION: Final[str] = "1.0.0"
MARKET_DATA_STREAMING_SCHEMA_VERSION: Final[str] = "1.0.0"
PRODUCER_NAME: Final[str] = "broker.market_data_streaming"

SUPPORTED_PRIMARY_UNDERLYINGS: Final[frozenset[str]] = frozenset(
    {"NIFTY", "BANKNIFTY", "SENSEX"}
)
SUPPORTED_SECONDARY_UNDERLYINGS: Final[frozenset[str]] = frozenset(
    {"FINNIFTY", "MIDCPNIFTY"}
)
SUPPORTED_UNDERLYINGS: Final[frozenset[str]] = (
    SUPPORTED_PRIMARY_UNDERLYINGS | SUPPORTED_SECONDARY_UNDERLYINGS
)

DEFAULT_TICK_STALENESS_SECONDS: Final[float] = 5.0
DEFAULT_SNAPSHOT_MIN_INTERVAL_SECONDS: Final[float] = 0.25
DEFAULT_HISTORY_RING_SIZE: Final[int] = 500
DEFAULT_MAX_MISSING_QUOTE_RATIO: Final[float] = 0.10
DEFAULT_MIN_COMPLETE_PAIRS: Final[int] = 1
DEFAULT_STRIKE_STEP: Final[float] = 50.0
DEFAULT_EXPECTED_MOVE_TRADING_DAYS_PER_YEAR: Final[float] = 365.0
DEFAULT_DEGRADED_FAILURE_THRESHOLD: Final[int] = 3
IST_ZONE: Final[str] = "Asia/Kolkata"

TOPIC_SNAPSHOT_PUBLISHED: Final[str] = "market.streaming.snapshot.published"
TOPIC_SNAPSHOT_SKIPPED: Final[str] = "market.streaming.snapshot.skipped"
TOPIC_SNAPSHOT_FAILED: Final[str] = "market.streaming.snapshot.failed"
TOPIC_TICK: Final[str] = "market.streaming.tick"
TOPIC_HEALTH: Final[str] = "market.streaming.health"

_LOGGER = logging.getLogger(PRODUCER_NAME)

_EMPTY_METADATA: Final[Mapping[str, str]] = MappingProxyType({})


# ---------------------------------------------------------------------------
# Enumerations (spec section 8.2)
# ---------------------------------------------------------------------------


class InstrumentRole(str, Enum):
    """Canonical role of an instrument for assembly grouping."""

    SPOT = "SPOT"
    FUTURE = "FUTURE"
    OPTION_CE = "OPTION_CE"
    OPTION_PE = "OPTION_PE"
    VOLATILITY_INDEX = "VOLATILITY_INDEX"
    UNKNOWN = "UNKNOWN"


class UnderlyingSupportTier(str, Enum):
    """Catalog classification of a canonical underlying."""

    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    EXPERIMENTAL = "EXPERIMENTAL"


class StreamingLifecycleState(str, Enum):
    """Engine lifecycle state."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPED = "STOPPED"


class SnapshotPublishOutcome(str, Enum):
    """Result of one assembly-and-publish attempt."""

    PUBLISHED = "PUBLISHED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class StreamingHealthStatus(str, Enum):
    """Aggregated streaming health classification."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class TimestampSource(str, Enum):
    """Provenance of the timestamp used to build a snapshot field."""

    EXCHANGE = "EXCHANGE"
    RECEIVE = "RECEIVE"
    INJECTED = "INJECTED"


# ---------------------------------------------------------------------------
# Exceptions (spec section 8.6)
# ---------------------------------------------------------------------------


class MarketDataStreamingError(Exception):
    """Base error for all ``broker.market_data_streaming`` failures.

    Attributes:
        code: Stable machine-readable ``MDS.*`` error code.
        field: Optional offending field path.
        underlying: Optional attributed canonical underlying.
        instrument_token: Optional attributed instrument token.
        correlation_id: Optional pipeline correlation identifier.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        field: str | None = None,
        underlying: str | None = None,
        instrument_token: int | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Initialize a streaming error.

        Args:
            message: Human-readable error description.
            code: Stable machine-readable ``MDS.*`` error code.
            field: Optional offending field path.
            underlying: Optional attributed canonical underlying.
            instrument_token: Optional attributed instrument token.
            correlation_id: Optional pipeline correlation identifier.
        """
        super().__init__(message)
        self.code = code
        self.field = field
        self.underlying = underlying
        self.instrument_token = instrument_token
        self.correlation_id = correlation_id


class MarketDataStreamingConfigurationError(MarketDataStreamingError):
    """Raised when :class:`MarketDataStreamingConfig` fails validation."""


class TickValidationError(MarketDataStreamingError):
    """Raised when a :class:`TickEvent` fails structural validation."""


class InstrumentValidationError(MarketDataStreamingError):
    """Raised when instrument registration or resolution fails."""


class SnapshotAssemblyError(MarketDataStreamingError):
    """Raised when assembly cannot produce a candidate snapshot."""


class SnapshotPublishError(MarketDataStreamingError):
    """Raised when publish dispatch fails outside callback isolation."""


class MarketDataStreamingSerializationError(MarketDataStreamingError):
    """Raised when JSON serialization or deserialization fails."""


class MarketDataStreamingStateError(MarketDataStreamingError):
    """Raised for illegal lifecycle transitions or premature operations."""


# ---------------------------------------------------------------------------
# Models -- tick and instrument layer (spec section 8.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GreeksAttachment:
    """Pre-computed option Greeks/IV carried on a tick.

    This module never computes any of these values; it only forwards
    values already present on the input (Rule GRK-001).

    Attributes:
        delta: Pre-computed delta.
        iv: Pre-computed implied volatility (decimal, e.g. ``0.145``).
        gamma: Pre-computed gamma.
        theta: Pre-computed theta.
        vega: Pre-computed vega.
        computed_at: When the upstream Greeks engine computed these values.
        source: Producer identifier (e.g. ``"option_greeks_engine"``).
    """

    delta: float | None = None
    iv: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    computed_at: datetime | None = None
    source: str | None = None


@dataclass(frozen=True)
class TickEvent:
    """Normalized platform tick -- the sole ingestion contract of this module.

    Attributes:
        instrument_token: Resolved instrument token (must be ``> 0``).
        underlying: Canonical underlying name.
        quote_key: Broker quote key, resolved externally.
        exchange: Exchange code, resolved externally.
        tradingsymbol: Trading symbol, resolved externally.
        instrument_kind: Opaque role tag from upstream (e.g. ``"CE"``).
        last_price: Last traded price; must be finite and ``>= 0``.
        volume: Cumulative traded volume; must be ``>= 0``.
        received_at: Local receive timestamp; always timezone-aware.
        bid: Best bid, or ``None`` when unavailable.
        ask: Best ask, or ``None`` when unavailable.
        bid_quantity: Best bid quantity.
        ask_quantity: Best ask quantity.
        open_interest: Open interest; required for options in practice.
        open: Session open.
        high: Session high.
        low: Session low.
        close: Previous session close.
        average_price: Volume-weighted average price when provided.
        exchange_timestamp: Broker-reported quote timestamp, naive or aware.
        sequence: Monotonic per-token sequence number.
        greeks: Optional pre-computed Greeks/IV attachment.
        metadata: Non-secret free-form tags.
    """

    instrument_token: int
    underlying: str
    quote_key: str
    exchange: str
    tradingsymbol: str
    instrument_kind: str
    last_price: float
    volume: int
    received_at: datetime
    bid: float | None = None
    ask: float | None = None
    bid_quantity: int | None = None
    ask_quantity: int | None = None
    open_interest: int | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    average_price: float | None = None
    exchange_timestamp: datetime | None = None
    sequence: int | None = None
    greeks: GreeksAttachment | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_METADATA)

    def __post_init__(self) -> None:
        """Normalize underlying casing and freeze metadata."""
        object.__setattr__(self, "underlying", str(self.underlying).strip().upper())
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class InstrumentDescriptor:
    """Externally resolved static instrument reference metadata.

    Registered once per instrument via ``register_instruments()`` and
    reused across many ticks.

    Attributes:
        instrument_token: Broker instrument token (resolved externally).
        underlying: Canonical underlying name.
        quote_key: Broker quote key.
        exchange: Exchange code.
        tradingsymbol: Trading symbol.
        instrument_kind: Opaque role tag, same vocabulary as ``TickEvent``.
        instrument_role: Resolved role; explicit values override the
            ``instrument_kind`` mapping table.
        strike: Strike price for option instruments.
        option_type: ``"CE"`` / ``"PE"`` for option instruments.
        expiry: ``YYYY-MM-DD`` expiry for option/futures instruments.
        lot_size: Exchange lot size.
        tick_size: Minimum price increment.
        support_tier: Primary/secondary classification for statistics.
        metadata: Non-secret free-form tags.
    """

    instrument_token: int
    underlying: str
    quote_key: str
    exchange: str
    tradingsymbol: str
    instrument_kind: str
    instrument_role: InstrumentRole | None = None
    strike: float | None = None
    option_type: str | None = None
    expiry: str | None = None
    lot_size: int | None = None
    tick_size: float | None = None
    support_tier: UnderlyingSupportTier | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_METADATA)

    def __post_init__(self) -> None:
        """Normalize underlying casing and freeze metadata."""
        object.__setattr__(self, "underlying", str(self.underlying).strip().upper())
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class QuoteRecord:
    """Immutable snapshot of the latest known state for one instrument token.

    Attributes:
        instrument_token: Token.
        underlying: Canonical underlying.
        instrument_role: Resolved role.
        descriptor: Registered static metadata, when available.
        last_tick: The most recently accepted tick for this token.
        first_seen_at: When this token was first observed (engine clock).
        last_updated_at: When this token was last updated (engine clock).
        update_count: Total accepted ticks for this token.
    """

    instrument_token: int
    underlying: str
    instrument_role: InstrumentRole
    descriptor: InstrumentDescriptor | None
    last_tick: TickEvent
    first_seen_at: datetime
    last_updated_at: datetime
    update_count: int


class TickNormalizer(Protocol):
    """Callable converting a raw broker payload into a :class:`TickEvent`."""

    def __call__(self, raw: Mapping[str, Any], *, instrument_token: int) -> TickEvent:
        """Return a normalized tick for the given raw payload."""
        ...


# ---------------------------------------------------------------------------
# Models -- assembly and view layer (spec section 8.4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FuturesSnapshot:
    """Ancillary futures observation; not part of the canonical schema.

    Attributes:
        underlying: Canonical underlying.
        exchange: Exchange code.
        tradingsymbol: Futures trading symbol.
        expiry: ``YYYY-MM-DD`` expiry of the tracked futures contract.
        last_price: Futures LTP.
        instrument_token: Token, when known.
        bid: Best bid.
        ask: Best ask.
        volume: Volume.
        open_interest: OI.
        basis: ``last_price - spot_last_price``, computed at assembly time.
        quote_timestamp: Normalized timezone-aware timestamp.
    """

    underlying: str
    exchange: str
    tradingsymbol: str
    expiry: str
    last_price: float
    instrument_token: int | None = None
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    basis: float | None = None
    quote_timestamp: datetime | None = None


@dataclass(frozen=True)
class ExpectedMoveEstimate:
    """Lightweight ATM-IV, square-root-of-time expected move estimate.

    Attributes:
        underlying: Canonical underlying.
        spot: Spot price used as the basis.
        atm_iv: ATM implied volatility (decimal) used as input.
        days_to_expiry: Calendar days to the option chain's expiry.
        method: Formula identifier, ``"ATM_IV_SQRT_TIME"`` in v1.
        expected_move_points: One-standard-deviation move, in points.
        expected_move_percent: Expected move as a percentage of spot.
        upper_bound: ``spot + expected_move_points``.
        lower_bound: ``spot - expected_move_points``.
        computed_at: Engine clock at computation time.
    """

    underlying: str
    spot: float
    atm_iv: float
    days_to_expiry: float
    method: str
    expected_move_points: float
    expected_move_percent: float
    upper_bound: float
    lower_bound: float
    computed_at: datetime


@dataclass(frozen=True)
class StreamingSnapshotView:
    """Streaming-only projection combining the canonical snapshot with context.

    Never a substitute for the canonical snapshot; always embeds it
    (Rule VIEW-001).

    Attributes:
        underlying: Canonical underlying.
        snapshot: The canonical, embedded snapshot.
        atm_strike: Echoed from ``snapshot.option_chain.metadata.atm_strike``.
        total_call_oi: Sum of open interest across all CE contracts.
        total_put_oi: Sum of open interest across all PE contracts.
        total_volume: Sum of volume across all contracts.
        as_of: Echoed from ``snapshot.provenance.as_of``.
        futures: Futures observation, when available.
        atm_call: Convenience reference to the ATM call contract.
        atm_put: Convenience reference to the ATM put contract.
        atm_iv: Average of ATM call/put IV when both attached.
        expected_move: Present only when enabled and ATM IV is available.
        put_call_oi_ratio: ``total_put_oi / total_call_oi``.
    """

    underlying: str
    snapshot: MarketSnapshot
    atm_strike: float
    total_call_oi: int
    total_put_oi: int
    total_volume: int
    as_of: datetime
    futures: FuturesSnapshot | None = None
    atm_call: OptionContractSnapshot | None = None
    atm_put: OptionContractSnapshot | None = None
    atm_iv: float | None = None
    expected_move: ExpectedMoveEstimate | None = None
    put_call_oi_ratio: float | None = None


# ---------------------------------------------------------------------------
# Models -- config, events, health, statistics (spec section 8.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketDataStreamingConfig:
    """Immutable streaming policy projected from Application Configuration.

    This module never loads Application Configuration, ``.env`` files, or
    ``os.environ`` directly (Rule CFG-MDS-001); callers project settings
    into this frozen configuration and inject it at construction time.

    Attributes:
        enabled_underlyings: Canonical underlyings to assemble.
        environment_profile: Development / Paper / Production.
        allow_experimental_underlyings: When ``False``, only catalog names
            (see :data:`SUPPORTED_UNDERLYINGS`) are accepted.
        tick_staleness_seconds: Per-quote age beyond which a quote is
            considered stale for coverage purposes.
        snapshot_min_interval_seconds: Minimum wall-clock interval between
            successive assembly attempts per underlying (throttle).
        history_ring_size: Maximum retained snapshots per underlying.
        max_missing_quote_ratio: Maximum tolerated fraction of missing
            bid/ask quotes in the chain before a streaming gate rejects.
        min_complete_pairs: Minimum CE/PE complete strike pairs required.
        strike_window_strikes: Strikes retained on each side of ATM.
        strike_step: Per-underlying strike increment overrides.
        default_strike_step: Strike increment used when no override exists.
        require_futures_for_snapshot: When ``True``, missing futures blocks
            publish.
        require_volatility_index: When ``True``, missing volatility index
            blocks publish.
        expected_move_enabled: Enables Expected Move computation.
        expected_move_trading_days_per_year: Time-scaling denominator.
        duplicate_tick_tolerance: When ``True``, non-increasing sequence
            ticks are silently ignored rather than raising.
        validation_policy: Overrides passed to ``validate_market_snapshot``.
        freshness_policy: Overrides passed to ``evaluate_snapshot_freshness``.
        publish_events: When ``True``, publish ``market.streaming.*`` topics.
        publish_tick_events: High-volume; default off even when enabled.
        runner_kind: Audit tag (``cli``, ``paper``, ``live``, ``test``).
        metadata: Non-secret audit metadata.
    """

    enabled_underlyings: tuple[str, ...] = ("NIFTY",)
    environment_profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT
    allow_experimental_underlyings: bool = False
    tick_staleness_seconds: float = DEFAULT_TICK_STALENESS_SECONDS
    snapshot_min_interval_seconds: float = DEFAULT_SNAPSHOT_MIN_INTERVAL_SECONDS
    history_ring_size: int = DEFAULT_HISTORY_RING_SIZE
    max_missing_quote_ratio: float = DEFAULT_MAX_MISSING_QUOTE_RATIO
    min_complete_pairs: int = DEFAULT_MIN_COMPLETE_PAIRS
    strike_window_strikes: int = 10
    strike_step: Mapping[str, float] = field(default_factory=lambda: _EMPTY_METADATA)
    default_strike_step: float = DEFAULT_STRIKE_STEP
    require_futures_for_snapshot: bool = False
    require_volatility_index: bool = False
    expected_move_enabled: bool = True
    expected_move_trading_days_per_year: float = (
        DEFAULT_EXPECTED_MOVE_TRADING_DAYS_PER_YEAR
    )
    duplicate_tick_tolerance: bool = True
    validation_policy: ValidationPolicy | None = None
    freshness_policy: SnapshotFreshnessPolicy | None = None
    publish_events: bool = False
    publish_tick_events: bool = False
    runner_kind: str = "unknown"
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_METADATA)

    def __post_init__(self) -> None:
        """Validate and normalize configuration invariants.

        Raises:
            MarketDataStreamingConfigurationError: On any invalid field.
        """
        normalized = tuple(normalize_underlying_name(u) for u in self.enabled_underlyings)
        if not normalized:
            raise MarketDataStreamingConfigurationError(
                "enabled_underlyings must be non-empty.",
                code="MDS.CONFIG.UNDERLYING_REQUIRED",
            )
        if len(set(normalized)) != len(normalized):
            raise MarketDataStreamingConfigurationError(
                "enabled_underlyings must not contain duplicates.",
                code="MDS.CONFIG.UNDERLYING_DUPLICATE",
            )
        if not self.allow_experimental_underlyings:
            unsupported = [u for u in normalized if u not in SUPPORTED_UNDERLYINGS]
            if unsupported:
                raise MarketDataStreamingConfigurationError(
                    f"Unsupported underlyings: {unsupported}.",
                    code="MDS.CONFIG.UNDERLYING_UNSUPPORTED",
                )
        object.__setattr__(self, "enabled_underlyings", normalized)

        for field_name, value in (
            ("tick_staleness_seconds", self.tick_staleness_seconds),
            ("snapshot_min_interval_seconds", self.snapshot_min_interval_seconds),
            ("max_missing_quote_ratio", self.max_missing_quote_ratio),
            ("expected_move_trading_days_per_year", self.expected_move_trading_days_per_year),
        ):
            if not math.isfinite(value) or value < 0:
                raise MarketDataStreamingConfigurationError(
                    f"{field_name} must be finite and non-negative.",
                    code="MDS.CONFIG.THRESHOLD_OUT_OF_RANGE",
                    field=field_name,
                )
        if self.max_missing_quote_ratio > 1.0:
            raise MarketDataStreamingConfigurationError(
                "max_missing_quote_ratio must be <= 1.0.",
                code="MDS.CONFIG.THRESHOLD_OUT_OF_RANGE",
                field="max_missing_quote_ratio",
            )
        if self.history_ring_size < 1:
            raise MarketDataStreamingConfigurationError(
                "history_ring_size must be >= 1.",
                code="MDS.CONFIG.THRESHOLD_OUT_OF_RANGE",
                field="history_ring_size",
            )
        if self.min_complete_pairs < 0:
            raise MarketDataStreamingConfigurationError(
                "min_complete_pairs must be >= 0.",
                code="MDS.CONFIG.THRESHOLD_OUT_OF_RANGE",
                field="min_complete_pairs",
            )
        if self.strike_window_strikes < 1:
            raise MarketDataStreamingConfigurationError(
                "strike_window_strikes must be >= 1.",
                code="MDS.CONFIG.THRESHOLD_OUT_OF_RANGE",
                field="strike_window_strikes",
            )
        if not math.isfinite(self.default_strike_step) or self.default_strike_step <= 0:
            raise MarketDataStreamingConfigurationError(
                "default_strike_step must be finite and > 0.",
                code="MDS.CONFIG.THRESHOLD_OUT_OF_RANGE",
                field="default_strike_step",
            )
        normalized_strike_step: dict[str, float] = {}
        for underlying, step in self.strike_step.items():
            if not math.isfinite(step) or step <= 0:
                raise MarketDataStreamingConfigurationError(
                    f"strike_step for {underlying} must be finite and > 0.",
                    code="MDS.CONFIG.THRESHOLD_OUT_OF_RANGE",
                    field="strike_step",
                    underlying=underlying,
                )
            normalized_strike_step[normalize_underlying_name(underlying)] = step
        object.__setattr__(
            self, "strike_step", MappingProxyType(normalized_strike_step)
        )
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class StreamingPublishEvent:
    """Result of one assembly-and-publish attempt.

    Attributes:
        event_id: UUID4.
        underlying: Canonical underlying.
        outcome: ``PUBLISHED`` / ``SKIPPED`` / ``FAILED``.
        published_at: Engine clock at publish time.
        sequence: Monotonic per-underlying publish sequence, starting at 1.
        snapshot: Present when ``outcome == PUBLISHED``.
        view: Present when ``outcome == PUBLISHED``.
        reason_code: ``MDS.*`` code when ``SKIPPED``/``FAILED``.
        reason_message: Human-readable reason.
        correlation_id: Optional pipeline correlation identifier.
    """

    event_id: str
    underlying: str
    outcome: SnapshotPublishOutcome
    published_at: datetime
    sequence: int
    snapshot: MarketSnapshot | None = None
    view: StreamingSnapshotView | None = None
    reason_code: str | None = None
    reason_message: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class StreamingHealthIssue:
    """Structured health issue with optional underlying/instrument attribution.

    Attributes:
        issue_code: ``MDS.HEALTH.*`` (or ``MDS.VALIDATION.*``) code.
        severity: Severity classification.
        message: Human-readable description.
        underlying: Attributed underlying, when applicable.
        instrument_token: Attributed instrument, when applicable.
    """

    issue_code: str
    severity: Literal["info", "warning", "error"]
    message: str
    underlying: str | None = None
    instrument_token: int | None = None


@dataclass(frozen=True)
class SnapshotHealth:
    """Per-underlying streaming health.

    Attributes:
        underlying: Canonical underlying.
        support_tier: Primary/secondary/experimental.
        has_snapshot: Whether a snapshot is currently cached.
        consecutive_publish_failures: Running count of consecutive
            non-``PUBLISHED`` outcomes for this underlying.
        issues: Structured issues for this underlying.
        freshness_status: From the cached snapshot's freshness status.
        validation_status: From the cached snapshot's validation status.
        completeness_score: Echoed from cached snapshot quality.
        seconds_since_last_snapshot: Age of the cached snapshot.
        last_publish_outcome: Most recent attempt outcome.
    """

    underlying: str
    support_tier: UnderlyingSupportTier
    has_snapshot: bool
    consecutive_publish_failures: int
    issues: tuple[StreamingHealthIssue, ...]
    freshness_status: SnapshotFreshnessStatus | None = None
    validation_status: SnapshotValidationStatus | None = None
    completeness_score: float | None = None
    seconds_since_last_snapshot: float | None = None
    last_publish_outcome: SnapshotPublishOutcome | None = None


@dataclass(frozen=True)
class UnderlyingStreamStatistics:
    """Per-underlying tick/snapshot counters and timing.

    Attributes:
        underlying: Canonical name.
        support_tier: Classification.
        tick_count: Accepted ticks since start/reset.
        rejected_tick_count: Ticks rejected by validation.
        unique_instruments_seen: Distinct instrument tokens observed.
        snapshot_attempt_count: Assembly attempts (throttle-gated).
        snapshot_published_count: Successful publishes.
        snapshot_skipped_count: Gate-skipped attempts.
        snapshot_failed_count: Validation/assembly failures.
        last_tick_at: Last accepted tick for this underlying.
        last_snapshot_at: Last successful publish.
        average_assembly_ms: Rolling average assembly duration.
        max_assembly_ms: Maximum observed assembly duration.
    """

    underlying: str
    support_tier: UnderlyingSupportTier
    tick_count: int
    rejected_tick_count: int
    unique_instruments_seen: int
    snapshot_attempt_count: int
    snapshot_published_count: int
    snapshot_skipped_count: int
    snapshot_failed_count: int
    last_tick_at: datetime | None = None
    last_snapshot_at: datetime | None = None
    average_assembly_ms: float | None = None
    max_assembly_ms: float | None = None


@dataclass(frozen=True)
class SnapshotStatistics:
    """Global and per-underlying tick/snapshot statistics.

    Attributes:
        as_of: Snapshot time.
        total_tick_count: Global accepted ticks.
        total_rejected_tick_count: Global rejected ticks.
        unattributed_tick_count: Ticks for unregistered tokens.
        total_snapshot_published_count: Global successful publishes.
        total_snapshot_skipped_count: Global gate-skipped attempts.
        total_snapshot_failed_count: Global validation/assembly failures.
        enabled_underlyings: Configured set.
        per_underlying: One entry per enabled underlying, in config order.
    """

    as_of: datetime
    total_tick_count: int
    total_rejected_tick_count: int
    unattributed_tick_count: int
    total_snapshot_published_count: int
    total_snapshot_skipped_count: int
    total_snapshot_failed_count: int
    enabled_underlyings: tuple[str, ...]
    per_underlying: tuple[UnderlyingStreamStatistics, ...]


@dataclass(frozen=True)
class StreamingHealthReport:
    """Aggregated global and per-underlying streaming health.

    Attributes:
        report_id: UUID4.
        as_of: Snapshot time of this report.
        overall_health: Aggregated across all enabled underlyings.
        lifecycle_state: Current engine lifecycle state.
        enabled_underlyings: Configured set, in config order.
        healthy_underlyings: Underlyings currently ``HEALTHY``.
        degraded_underlyings: Underlyings currently ``DEGRADED``.
        unhealthy_underlyings: Underlyings currently ``UNHEALTHY``.
        per_underlying: One entry per enabled underlying, in config order.
        statistics: Embedded statistics snapshot.
        issues: Global (non-underlying-scoped) issues.
        metadata: Free-form.
    """

    report_id: str
    as_of: datetime
    overall_health: StreamingHealthStatus
    lifecycle_state: StreamingLifecycleState
    enabled_underlyings: tuple[str, ...]
    healthy_underlyings: tuple[str, ...]
    degraded_underlyings: tuple[str, ...]
    unhealthy_underlyings: tuple[str, ...]
    per_underlying: tuple[SnapshotHealth, ...]
    statistics: SnapshotStatistics
    issues: tuple[StreamingHealthIssue, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_METADATA)


# ---------------------------------------------------------------------------
# Pure helper functions (spec sections 9.3, 12.2, 11.5, 18.5)
# ---------------------------------------------------------------------------


_INSTRUMENT_KIND_ROLE_MAP: Final[Mapping[str, InstrumentRole]] = {
    "INDEX": InstrumentRole.SPOT,
    "SPOT": InstrumentRole.SPOT,
    "FUT": InstrumentRole.FUTURE,
    "FUTURE": InstrumentRole.FUTURE,
    "FUTURES": InstrumentRole.FUTURE,
    "CE": InstrumentRole.OPTION_CE,
    "PE": InstrumentRole.OPTION_PE,
    "VIX": InstrumentRole.VOLATILITY_INDEX,
    "INDVIX": InstrumentRole.VOLATILITY_INDEX,
    "VOLATILITY": InstrumentRole.VOLATILITY_INDEX,
}


def resolve_instrument_role(instrument_kind: str) -> InstrumentRole:
    """Map an opaque ``instrument_kind`` tag to a canonical role.

    Pure function; case-insensitive (Rule ROLE-001).

    Args:
        instrument_kind: Opaque role tag (e.g. ``"CE"``, ``"INDEX"``).

    Returns:
        Resolved :class:`InstrumentRole`; ``UNKNOWN`` for unrecognized tags.
    """
    return _INSTRUMENT_KIND_ROLE_MAP.get(
        str(instrument_kind).strip().upper(), InstrumentRole.UNKNOWN
    )


def normalize_underlying_name(name: str) -> str:
    """Normalize a canonical underlying name.

    Args:
        name: Raw underlying name.

    Returns:
        Uppercased, stripped name.
    """
    return str(name).strip().upper()


def classify_underlying_tier(underlying: str) -> UnderlyingSupportTier:
    """Classify an underlying against the supported catalog.

    Args:
        underlying: Canonical underlying name.

    Returns:
        Support tier enumeration.
    """
    name = normalize_underlying_name(underlying)
    if name in SUPPORTED_PRIMARY_UNDERLYINGS:
        return UnderlyingSupportTier.PRIMARY
    if name in SUPPORTED_SECONDARY_UNDERLYINGS:
        return UnderlyingSupportTier.SECONDARY
    return UnderlyingSupportTier.EXPERIMENTAL


def normalize_exchange_timestamp(
    value: datetime | None,
    *,
    assume_tz: str = IST_ZONE,
) -> datetime | None:
    """Return a UTC-normalized, timezone-aware timestamp.

    Args:
        value: Raw broker-reported timestamp, naive or aware.
        assume_tz: IANA zone assumed when ``value`` is naive.

    Returns:
        Timezone-aware UTC ``datetime``, or ``None`` when ``value`` is
        ``None``.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(assume_tz))
    return value.astimezone(timezone.utc)


def derive_atm(
    spot_price: float,
    strike_step: float,
    available_strikes: Sequence[float],
) -> float:
    """Return the strike nearest to ``spot_price``, snapped to ``strike_step``.

    Prefers an available strike when the exact snapped value is not
    quoted (Rule ASM-007: pure function of quote-book state).

    Args:
        spot_price: Current spot price.
        strike_step: Configured strike increment for this underlying.
        available_strikes: Strikes currently present in the quote book.

    Returns:
        Derived ATM strike.
    """
    snapped = round(spot_price / strike_step) * strike_step
    if not available_strikes:
        return snapped
    return min(
        available_strikes,
        key=lambda strike: (abs(strike - snapped), strike),
    )


def compute_expected_move(
    *,
    underlying: str,
    spot: float,
    atm_iv: float,
    days_to_expiry: float,
    trading_days_per_year: float,
    now: datetime,
) -> ExpectedMoveEstimate:
    """Compute a one-standard-deviation Expected Move estimate.

    Uses the well-known ATM-IV, square-root-of-time approximation and
    nothing more sophisticated (Rule EM-001). ``atm_iv`` must already be
    attached, never computed, by this module.

    Args:
        underlying: Canonical underlying.
        spot: Spot price used as the basis.
        atm_iv: ATM implied volatility (decimal).
        days_to_expiry: Calendar days to the option chain's expiry.
        trading_days_per_year: Time-scaling denominator.
        now: Engine clock at computation time.

    Returns:
        Immutable :class:`ExpectedMoveEstimate`.
    """
    time_fraction = max(days_to_expiry, 0.0) / trading_days_per_year
    em_points = spot * atm_iv * math.sqrt(time_fraction)
    em_percent = atm_iv * math.sqrt(time_fraction) * 100.0
    return ExpectedMoveEstimate(
        underlying=underlying,
        spot=spot,
        atm_iv=atm_iv,
        days_to_expiry=days_to_expiry,
        method="ATM_IV_SQRT_TIME",
        expected_move_points=em_points,
        expected_move_percent=em_percent,
        upper_bound=spot + em_points,
        lower_bound=spot - em_points,
        computed_at=now,
    )


def default_market_data_streaming_config(
    profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT,
    *,
    enabled_underlyings: Sequence[str] = ("NIFTY",),
) -> MarketDataStreamingConfig:
    """Build a profile-aware default streaming configuration.

    Mirrors the profile defaults documented in Appendix J of the
    specification.

    Args:
        profile: Environment profile.
        enabled_underlyings: Canonical underlyings from Application
            Configuration projection.

    Returns:
        Frozen :class:`MarketDataStreamingConfig`.
    """
    underlyings = tuple(enabled_underlyings)
    if profile is EnvironmentProfile.PRODUCTION:
        return MarketDataStreamingConfig(
            enabled_underlyings=underlyings,
            environment_profile=profile,
            allow_experimental_underlyings=False,
            tick_staleness_seconds=3.0,
            snapshot_min_interval_seconds=0.25,
            max_missing_quote_ratio=0.05,
            min_complete_pairs=3,
            require_futures_for_snapshot=True,
            require_volatility_index=True,
            publish_events=True,
            publish_tick_events=False,
        )
    if profile is EnvironmentProfile.PAPER:
        return MarketDataStreamingConfig(
            enabled_underlyings=underlyings,
            environment_profile=profile,
            allow_experimental_underlyings=False,
            tick_staleness_seconds=5.0,
            snapshot_min_interval_seconds=0.25,
            max_missing_quote_ratio=0.10,
            require_futures_for_snapshot=False,
            require_volatility_index=False,
            publish_events=True,
        )
    return MarketDataStreamingConfig(
        enabled_underlyings=underlyings,
        environment_profile=profile,
        allow_experimental_underlyings=True,
        tick_staleness_seconds=10.0,
        snapshot_min_interval_seconds=0.0,
        max_missing_quote_ratio=0.25,
        require_futures_for_snapshot=False,
        require_volatility_index=False,
        publish_events=False,
    )


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _issue(
    code: str,
    severity: Literal["info", "warning", "error"],
    message: str,
    *,
    underlying: str | None = None,
    instrument_token: int | None = None,
) -> StreamingHealthIssue:
    return StreamingHealthIssue(
        issue_code=code,
        severity=severity,
        message=message,
        underlying=underlying,
        instrument_token=instrument_token,
    )


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _extract_correlation_id(tick: TickEvent) -> str | None:
    value = tick.metadata.get("correlation_id") if tick.metadata else None
    return value or None


def _validate_tick(tick: TickEvent) -> None:
    """Validate a tick per the TICK-* matrix (spec section 13.1).

    Raises:
        TickValidationError: On the first failing rule.
    """
    if tick.instrument_token <= 0:
        raise TickValidationError(
            "instrument_token must be > 0.",
            code="MDS.TICK.INVALID_TOKEN",
            instrument_token=tick.instrument_token,
            underlying=tick.underlying,
        )
    if not _is_timezone_aware(tick.received_at):
        raise TickValidationError(
            "received_at must be timezone-aware.",
            code="MDS.TICK.NAIVE_TIMESTAMP",
            instrument_token=tick.instrument_token,
            underlying=tick.underlying,
        )
    if not math.isfinite(tick.last_price) or tick.last_price < 0:
        raise TickValidationError(
            "last_price must be finite and non-negative.",
            code="MDS.TICK.INVALID_PRICE",
            instrument_token=tick.instrument_token,
            underlying=tick.underlying,
        )
    if tick.volume < 0:
        raise TickValidationError(
            "volume must be non-negative.",
            code="MDS.TICK.INVALID_VOLUME",
            instrument_token=tick.instrument_token,
            underlying=tick.underlying,
        )
    if tick.open_interest is not None and tick.open_interest < 0:
        raise TickValidationError(
            "open_interest must be non-negative.",
            code="MDS.TICK.INVALID_OI",
            instrument_token=tick.instrument_token,
            underlying=tick.underlying,
        )
    for label, value in (("bid", tick.bid), ("ask", tick.ask)):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise TickValidationError(
                f"{label} must be finite and non-negative.",
                code="MDS.TICK.INVALID_QUOTE",
                instrument_token=tick.instrument_token,
                underlying=tick.underlying,
            )
    if not tick.underlying or not tick.underlying.strip():
        raise TickValidationError(
            "underlying must be non-empty.",
            code="MDS.TICK.MISSING_UNDERLYING",
            instrument_token=tick.instrument_token,
        )


def _with_normalized_exchange_timestamp(tick: TickEvent) -> TickEvent:
    normalized = normalize_exchange_timestamp(tick.exchange_timestamp)
    if normalized is tick.exchange_timestamp:
        return tick
    return replace(tick, exchange_timestamp=normalized)


def _is_duplicate_or_out_of_order(prior: QuoteRecord | None, tick: TickEvent) -> bool:
    if prior is None or tick.sequence is None or prior.last_tick.sequence is None:
        return False
    return tick.sequence <= prior.last_tick.sequence


# ---------------------------------------------------------------------------
# LatestQuoteBook (public collaborative component, spec section 8.8/10)
# ---------------------------------------------------------------------------


class LatestQuoteBook:
    """Thread-safe live latest-quote store, indexed by token and underlying.

    Answers, at any instant, "what is the latest known tick for token T?"
    and "what tokens/roles exist for underlying U?" in O(1) and O(k)
    respectively. Never calls the assembler, cache, or publish dispatcher
    directly (Rule QB-001) -- it only stores and serves quote state.
    """

    def __init__(
        self,
        *,
        enabled_underlyings: Sequence[str],
        tick_staleness_seconds: float,
        shard_count: int = 32,
    ) -> None:
        """Construct the quote book.

        Args:
            enabled_underlyings: Configured underlying allowlist.
            tick_staleness_seconds: Per-quote staleness threshold.
            shard_count: Number of lock shards for the token record map.
        """
        self._tick_staleness_seconds = tick_staleness_seconds
        self._shard_count = max(1, shard_count)
        self._shard_locks = [threading.Lock() for _ in range(self._shard_count)]
        self._records: dict[int, QuoteRecord] = {}
        self._descriptors: dict[int, InstrumentDescriptor] = {}
        self._index_lock = threading.RLock()
        self._by_underlying: dict[str, set[int]] = {
            normalize_underlying_name(u): set() for u in enabled_underlyings
        }

    def _shard_for(self, instrument_token: int) -> threading.Lock:
        return self._shard_locks[instrument_token % self._shard_count]

    def register_instruments(self, descriptors: Sequence[InstrumentDescriptor]) -> None:
        """Register or replace static instrument metadata.

        Args:
            descriptors: Fully resolved instrument descriptors.
        """
        with self._index_lock:
            for descriptor in descriptors:
                self._descriptors[descriptor.instrument_token] = descriptor
                self._by_underlying.setdefault(descriptor.underlying, set()).add(
                    descriptor.instrument_token
                )

    def deregister_instruments(self, tokens: Sequence[int]) -> None:
        """Remove instrument metadata and any cached quote for the tokens.

        Args:
            tokens: Instrument tokens to remove.
        """
        with self._index_lock:
            for token in tokens:
                descriptor = self._descriptors.pop(token, None)
                if descriptor is not None:
                    self._by_underlying.get(descriptor.underlying, set()).discard(token)
                self._records.pop(token, None)

    def get_descriptor(self, instrument_token: int) -> InstrumentDescriptor | None:
        """Return the registered descriptor for a token, if any."""
        return self._descriptors.get(instrument_token)

    def get_descriptors_for_underlying(
        self, underlying: str
    ) -> tuple[InstrumentDescriptor, ...]:
        """Return all registered descriptors for an underlying."""
        with self._index_lock:
            tokens = tuple(self._by_underlying.get(underlying, ()))
        descriptors = []
        for token in tokens:
            descriptor = self._descriptors.get(token)
            if descriptor is not None:
                descriptors.append(descriptor)
        return tuple(descriptors)

    def update(self, tick: TickEvent, *, now: datetime) -> QuoteRecord:
        """Atomically update (or insert) the record for a token.

        Args:
            tick: Normalized tick to apply.
            now: Engine clock at update time.

        Returns:
            The newly stored immutable :class:`QuoteRecord`.
        """
        descriptor = self._descriptors.get(tick.instrument_token)
        if descriptor is not None and descriptor.instrument_role is not None:
            role = descriptor.instrument_role
        else:
            role = resolve_instrument_role(tick.instrument_kind)
        underlying = descriptor.underlying if descriptor is not None else tick.underlying

        lock = self._shard_for(tick.instrument_token)
        with lock:
            prior = self._records.get(tick.instrument_token)
            first_seen_at = prior.first_seen_at if prior is not None else now
            update_count = (prior.update_count if prior is not None else 0) + 1
            record = QuoteRecord(
                instrument_token=tick.instrument_token,
                underlying=underlying,
                instrument_role=role,
                descriptor=descriptor,
                last_tick=tick,
                first_seen_at=first_seen_at,
                last_updated_at=now,
                update_count=update_count,
            )
            self._records[tick.instrument_token] = record

        with self._index_lock:
            self._by_underlying.setdefault(underlying, set()).add(tick.instrument_token)
        return record

    def get(self, instrument_token: int) -> QuoteRecord | None:
        """Fetch the current record for a token."""
        return self._records.get(instrument_token)

    def get_for_underlying(self, underlying: str) -> tuple[QuoteRecord, ...]:
        """Return a deterministic snapshot of all records for an underlying.

        Args:
            underlying: Canonical underlying name.

        Returns:
            Records ordered by ``instrument_token``.
        """
        with self._index_lock:
            tokens = tuple(sorted(self._by_underlying.get(underlying, ())))
        records = []
        for token in tokens:
            record = self._records.get(token)
            if record is not None:
                records.append(record)
        return tuple(records)

    def get_by_role(self, underlying: str, role: InstrumentRole) -> tuple[QuoteRecord, ...]:
        """Filter records for an underlying by resolved role."""
        return tuple(
            record
            for record in self.get_for_underlying(underlying)
            if record.instrument_role is role
        )

    def is_stale(self, instrument_token: int, *, now: datetime) -> bool:
        """Return whether a record's last update exceeds the staleness budget.

        Staleness is evaluated relative to ``exchange_timestamp`` when
        present, falling back to ``received_at`` (Rule QB-003).
        """
        record = self.get(instrument_token)
        if record is None:
            return True
        reference = record.last_tick.exchange_timestamp or record.last_tick.received_at
        return (now - reference).total_seconds() > self._tick_staleness_seconds

    def token_count(self) -> int:
        """Return the total number of registered/observed instruments."""
        return len(self._records)

    def underlying_token_count(self, underlying: str) -> int:
        """Return the registered/observed instrument count for an underlying."""
        with self._index_lock:
            return len(self._by_underlying.get(underlying, ()))


# ---------------------------------------------------------------------------
# SnapshotCache (spec section 15)
# ---------------------------------------------------------------------------


class SnapshotCache:
    """Bounded, one-entry-per-underlying cache of the latest published snapshot."""

    def __init__(self) -> None:
        """Construct an empty cache."""
        self._lock = threading.RLock()
        self._snapshots: dict[str, MarketSnapshot] = {}
        self._views: dict[str, StreamingSnapshotView] = {}

    def put(
        self,
        underlying: str,
        snapshot: MarketSnapshot,
        view: StreamingSnapshotView,
    ) -> None:
        """Atomically replace the cached entry for an underlying."""
        with self._lock:
            self._snapshots[underlying] = snapshot
            self._views[underlying] = view

    def get(self, underlying: str) -> MarketSnapshot | None:
        """Fetch the cached snapshot, or ``None`` if never published."""
        with self._lock:
            return self._snapshots.get(underlying)

    def get_view(self, underlying: str) -> StreamingSnapshotView | None:
        """Fetch the cached view, or ``None`` if never published."""
        with self._lock:
            return self._views.get(underlying)

    def all_snapshots(self) -> Mapping[str, MarketSnapshot]:
        """Return an immutable mapping snapshot of all cached entries."""
        with self._lock:
            return MappingProxyType(dict(self._snapshots))

    def clear(self, underlying: str | None = None) -> None:
        """Clear one underlying's entry, or all entries when ``None``."""
        with self._lock:
            if underlying is None:
                self._snapshots.clear()
                self._views.clear()
            else:
                self._snapshots.pop(underlying, None)
                self._views.pop(underlying, None)


# ---------------------------------------------------------------------------
# SnapshotHistory (spec section 16)
# ---------------------------------------------------------------------------


class SnapshotHistory:
    """Bounded per-underlying ring buffer of recently published snapshots."""

    def __init__(
        self,
        *,
        enabled_underlyings: Sequence[str],
        history_ring_size: int,
    ) -> None:
        """Construct the history rings.

        Args:
            enabled_underlyings: Configured underlying allowlist.
            history_ring_size: Maximum retained snapshots per underlying.
        """
        self._capacity = history_ring_size
        self._locks: dict[str, threading.RLock] = {
            normalize_underlying_name(u): threading.RLock() for u in enabled_underlyings
        }
        self._rings: dict[str, deque[MarketSnapshot]] = {
            u: deque(maxlen=history_ring_size) for u in self._locks
        }

    def _lock_for(self, underlying: str) -> threading.RLock:
        lock = self._locks.get(underlying)
        if lock is None:
            lock = threading.RLock()
            self._locks[underlying] = lock
            self._rings[underlying] = deque(maxlen=self._capacity)
        return lock

    def append(self, underlying: str, snapshot: MarketSnapshot) -> None:
        """Append to the ring for ``underlying``, evicting the oldest when full."""
        lock = self._lock_for(underlying)
        with lock:
            self._rings.setdefault(underlying, deque(maxlen=self._capacity)).append(snapshot)

    def get(
        self,
        underlying: str,
        *,
        limit: int | None = None,
    ) -> tuple[MarketSnapshot, ...]:
        """Return up to ``limit`` most recent snapshots, oldest-first."""
        lock = self._locks.get(underlying)
        if lock is None:
            return ()
        with lock:
            items = tuple(self._rings.get(underlying, ()))
        if limit is None:
            return items
        if limit <= 0:
            return ()
        return items[-limit:]

    def size(self, underlying: str) -> int:
        """Return the current retained count for ``underlying``."""
        lock = self._locks.get(underlying)
        if lock is None:
            return 0
        with lock:
            return len(self._rings.get(underlying, ()))

    def capacity(self) -> int:
        """Return the configured ``history_ring_size``."""
        return self._capacity

    def clear(self, underlying: str | None = None) -> None:
        """Clear one underlying's ring, or all rings when ``None``."""
        if underlying is None:
            for name, lock in list(self._locks.items()):
                with lock:
                    self._rings[name].clear()
        else:
            lock = self._locks.get(underlying)
            if lock is not None:
                with lock:
                    self._rings[underlying].clear()


# ---------------------------------------------------------------------------
# Internal mutable runtime state (private; not part of the public contract)
# ---------------------------------------------------------------------------


@dataclass
class _UnderlyingStatsState:
    """Mutable per-underlying tick/snapshot counters (reset by reset_statistics)."""

    tick_count: int = 0
    rejected_tick_count: int = 0
    unique_instruments: set[int] = field(default_factory=set)
    snapshot_attempt_count: int = 0
    snapshot_published_count: int = 0
    snapshot_skipped_count: int = 0
    snapshot_failed_count: int = 0
    last_tick_at: datetime | None = None
    last_snapshot_at: datetime | None = None
    assembly_duration_total_ms: float = 0.0
    assembly_duration_count: int = 0
    assembly_duration_max_ms: float = 0.0


@dataclass
class _UnderlyingOperationalState:
    """Mutable per-underlying operational state (never reset by statistics)."""

    last_attempt_at: datetime | None = None
    publish_sequence: int = 0
    consecutive_publish_failures: int = 0
    last_publish_outcome: SnapshotPublishOutcome | None = None


@dataclass
class _GlobalStatsState:
    """Mutable global tick/snapshot counters (reset by reset_statistics)."""

    total_tick_count: int = 0
    total_rejected_tick_count: int = 0
    unattributed_tick_count: int = 0
    total_snapshot_published_count: int = 0
    total_snapshot_skipped_count: int = 0
    total_snapshot_failed_count: int = 0


_TOPIC_FOR_OUTCOME: Final[Mapping[SnapshotPublishOutcome, str]] = {
    SnapshotPublishOutcome.PUBLISHED: TOPIC_SNAPSHOT_PUBLISHED,
    SnapshotPublishOutcome.SKIPPED: TOPIC_SNAPSHOT_SKIPPED,
    SnapshotPublishOutcome.FAILED: TOPIC_SNAPSHOT_FAILED,
}


def _compute_overall_health(
    per_underlying_status: Sequence[StreamingHealthStatus],
    lifecycle_state: StreamingLifecycleState,
) -> StreamingHealthStatus:
    """Derive overall health from per-underlying statuses (spec section 20.2)."""
    if lifecycle_state is StreamingLifecycleState.STOPPED:
        return StreamingHealthStatus.UNKNOWN
    if not per_underlying_status:
        return StreamingHealthStatus.UNKNOWN

    total = len(per_underlying_status)
    healthy = sum(1 for s in per_underlying_status if s is StreamingHealthStatus.HEALTHY)
    degraded = sum(1 for s in per_underlying_status if s is StreamingHealthStatus.DEGRADED)
    unhealthy = sum(1 for s in per_underlying_status if s is StreamingHealthStatus.UNHEALTHY)
    unknown = sum(1 for s in per_underlying_status if s is StreamingHealthStatus.UNKNOWN)

    if healthy == total:
        return StreamingHealthStatus.HEALTHY
    if unhealthy > 0:
        return (
            StreamingHealthStatus.UNHEALTHY
            if unhealthy > healthy
            else StreamingHealthStatus.DEGRADED
        )
    if degraded > 0:
        return StreamingHealthStatus.DEGRADED
    if unknown == total and lifecycle_state is StreamingLifecycleState.RUNNING:
        return StreamingHealthStatus.UNKNOWN
    return StreamingHealthStatus.DEGRADED if unknown > 0 else StreamingHealthStatus.UNKNOWN


# ---------------------------------------------------------------------------
# MarketDataStreamingEngine (spec section 8.7)
# ---------------------------------------------------------------------------


class MarketDataStreamingEngine:
    """Continuous streaming market-data snapshot assembly service.

    Consumes normalized ``TickEvent`` objects, maintains a thread-safe
    latest-quote book per instrument, assembles per-underlying candidate
    ``MarketSnapshot`` instances, validates them against the canonical
    ``market_data.market_snapshot`` rules plus streaming-only gates, and
    publishes accepted snapshots via callbacks and an optional Event Bus.

    Never owns a WebSocket connection, never performs OAuth, never
    redefines the ``MarketSnapshot`` schema, and never evaluates
    strategies, risk, or orders.

    Args:
        config: Validated streaming configuration.
        event_bus: Optional Event Bus for ``market.streaming.*`` publication.
        clock: Injectable clock returning timezone-aware ``datetime`` values.
        id_factory: Injectable UUID factory for deterministic tests.
        tick_normalizer: Optional callable converting raw broker payloads
            into ``TickEvent`` for callers using ``ingest_raw_tick``.
    """

    def __init__(
        self,
        config: MarketDataStreamingConfig,
        *,
        event_bus: EventBus | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        tick_normalizer: TickNormalizer | None = None,
    ) -> None:
        """Construct the engine. See class docstring for argument semantics."""
        self._config = config
        self._event_bus = event_bus
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._tick_normalizer = tick_normalizer

        self._lifecycle_lock = threading.RLock()
        self._lifecycle_state = StreamingLifecycleState.CREATED

        self._quote_book = LatestQuoteBook(
            enabled_underlyings=config.enabled_underlyings,
            tick_staleness_seconds=config.tick_staleness_seconds,
        )
        self._cache = SnapshotCache()
        self._history = SnapshotHistory(
            enabled_underlyings=config.enabled_underlyings,
            history_ring_size=config.history_ring_size,
        )

        self._state_lock = threading.RLock()
        self._global_stats = _GlobalStatsState()
        self._underlying_stats: dict[str, _UnderlyingStatsState] = {
            u: _UnderlyingStatsState() for u in config.enabled_underlyings
        }
        self._underlying_ops: dict[str, _UnderlyingOperationalState] = {
            u: _UnderlyingOperationalState() for u in config.enabled_underlyings
        }

        self._callbacks_lock = threading.RLock()
        self._callbacks: list[Callable[[StreamingPublishEvent], None]] = []

    # -- Lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Transition ``CREATED`` -> ``RUNNING``.

        Idempotent while already ``RUNNING``/``DEGRADED``.

        Raises:
            MarketDataStreamingStateError: When restarting a stopped engine.
        """
        with self._lifecycle_lock:
            if self._lifecycle_state in (
                StreamingLifecycleState.RUNNING,
                StreamingLifecycleState.DEGRADED,
            ):
                return
            if self._lifecycle_state is StreamingLifecycleState.STOPPED:
                raise MarketDataStreamingStateError(
                    "Cannot restart a stopped engine; construct a new instance.",
                    code="MDS.STATE.INVALID_TRANSITION",
                )
            self._lifecycle_state = StreamingLifecycleState.RUNNING

    def stop(self) -> None:
        """Transition to ``STOPPED`` from any prior state.

        Cache and history remain readable after stop for diagnostics.
        """
        with self._lifecycle_lock:
            self._lifecycle_state = StreamingLifecycleState.STOPPED

    def get_status(self) -> StreamingLifecycleState:
        """Return the current lifecycle state."""
        with self._lifecycle_lock:
            return self._lifecycle_state

    # -- Registration -------------------------------------------------------

    def register_instruments(self, descriptors: Sequence[InstrumentDescriptor]) -> None:
        """Register externally resolved instrument descriptors.

        Args:
            descriptors: Instrument descriptors to register.

        Raises:
            InstrumentValidationError: On any INST-MDS-* validation failure.
        """
        resolved: list[InstrumentDescriptor] = []
        seen_tokens: set[int] = set()
        for descriptor in descriptors:
            if descriptor.instrument_token <= 0:
                raise InstrumentValidationError(
                    "instrument_token must be > 0.",
                    code="MDS.INSTRUMENT.INVALID_TOKEN",
                    underlying=descriptor.underlying,
                    instrument_token=descriptor.instrument_token,
                )
            if descriptor.instrument_token in seen_tokens:
                raise InstrumentValidationError(
                    "Duplicate instrument_token within one register_instruments() call.",
                    code="MDS.INSTRUMENT.DUPLICATE_TOKEN",
                    underlying=descriptor.underlying,
                    instrument_token=descriptor.instrument_token,
                )
            seen_tokens.add(descriptor.instrument_token)

            normalized_underlying = normalize_underlying_name(descriptor.underlying)
            if (
                not self._config.allow_experimental_underlyings
                and normalized_underlying not in SUPPORTED_UNDERLYINGS
            ):
                raise InstrumentValidationError(
                    "Underlying fails catalog membership and experimental "
                    "underlyings are disallowed.",
                    code="MDS.INSTRUMENT.UNDERLYING_UNSUPPORTED",
                    underlying=descriptor.underlying,
                    instrument_token=descriptor.instrument_token,
                )
            if normalized_underlying not in self._config.enabled_underlyings:
                raise InstrumentValidationError(
                    "Descriptor underlying is not in enabled_underlyings.",
                    code="MDS.INSTRUMENT.UNDERLYING_NOT_ENABLED",
                    underlying=descriptor.underlying,
                    instrument_token=descriptor.instrument_token,
                )

            role = descriptor.instrument_role or resolve_instrument_role(
                descriptor.instrument_kind
            )
            if role in (InstrumentRole.OPTION_CE, InstrumentRole.OPTION_PE):
                if (
                    descriptor.strike is None
                    or descriptor.option_type is None
                    or descriptor.expiry is None
                    or descriptor.lot_size is None
                ):
                    raise InstrumentValidationError(
                        "Option descriptors require strike, option_type, "
                        "expiry, and lot_size.",
                        code="MDS.INSTRUMENT.INCOMPLETE_OPTION_METADATA",
                        underlying=descriptor.underlying,
                        instrument_token=descriptor.instrument_token,
                    )

            resolved.append(
                replace(
                    descriptor,
                    underlying=normalized_underlying,
                    instrument_role=role,
                    support_tier=descriptor.support_tier
                    or classify_underlying_tier(normalized_underlying),
                )
            )

        self._quote_book.register_instruments(tuple(resolved))

    def deregister_instruments(self, tokens: Sequence[int]) -> None:
        """Remove previously registered instrument descriptors.

        Args:
            tokens: Instrument tokens to deregister.
        """
        self._quote_book.deregister_instruments(tokens)

    def enabled_underlyings(self) -> tuple[str, ...]:
        """Return the configured underlying set, in config order."""
        return self._config.enabled_underlyings

    # -- Ingestion ------------------------------------------------------

    def ingest_tick(self, tick: TickEvent) -> None:
        """Validate, apply, and (subject to throttle) assemble one tick.

        Never blocks on I/O (Rule ING-001); safe to call concurrently from
        multiple threads (Rule ING-002).

        Args:
            tick: Normalized tick event.

        Raises:
            MarketDataStreamingStateError: When not ``RUNNING``/``DEGRADED``.
            TickValidationError: On structural validation failure.
        """
        self._ensure_running()

        try:
            _validate_tick(tick)
        except TickValidationError:
            self._record_rejected(self._bucket_for(tick.underlying))
            raise

        normalized_tick = _with_normalized_exchange_timestamp(tick)
        descriptor = self._quote_book.get_descriptor(normalized_tick.instrument_token)
        if descriptor is None:
            self._record_unattributed()
            return

        prior = self._quote_book.get(normalized_tick.instrument_token)
        if _is_duplicate_or_out_of_order(prior, normalized_tick):
            if self._config.duplicate_tick_tolerance:
                return
            self._record_rejected(descriptor.underlying)
            raise TickValidationError(
                "Tick sequence is duplicate or out of order.",
                code="MDS.TICK.OUT_OF_ORDER",
                underlying=descriptor.underlying,
                instrument_token=normalized_tick.instrument_token,
            )

        now = self._clock()
        self._quote_book.update(normalized_tick, now=now)
        self._record_tick(descriptor.underlying, normalized_tick.instrument_token, now)
        self._maybe_assemble(descriptor.underlying, now)

    def ingest_raw_tick(self, raw: Mapping[str, Any], *, instrument_token: int) -> None:
        """Normalize a raw broker payload and delegate to :meth:`ingest_tick`.

        Args:
            raw: Raw broker payload.
            instrument_token: Resolved instrument token for the payload.

        Raises:
            MarketDataStreamingStateError: When no ``tick_normalizer`` was
                injected at construction.
        """
        if self._tick_normalizer is None:
            raise MarketDataStreamingStateError(
                "ingest_raw_tick requires an injected tick_normalizer.",
                code="MDS.STATE.NORMALIZER_NOT_CONFIGURED",
            )
        tick = self._tick_normalizer(raw, instrument_token=instrument_token)
        self.ingest_tick(tick)

    def _ensure_running(self) -> None:
        with self._lifecycle_lock:
            state = self._lifecycle_state
        if state not in (StreamingLifecycleState.RUNNING, StreamingLifecycleState.DEGRADED):
            raise MarketDataStreamingStateError(
                "ingest_tick requires the engine to be RUNNING or DEGRADED.",
                code="MDS.STATE.NOT_RUNNING",
            )

    def _bucket_for(self, raw_underlying: str) -> str | None:
        try:
            candidate = normalize_underlying_name(raw_underlying)
        except Exception:  # noqa: BLE001 - defensive against malformed input
            return None
        return candidate if candidate in self._config.enabled_underlyings else None

    # -- Pull access ------------------------------------------------------

    def get_snapshot(self, underlying: str) -> MarketSnapshot | None:
        """Return the latest published snapshot for an underlying, if any."""
        return self._cache.get(normalize_underlying_name(underlying))

    def get_streaming_view(self, underlying: str) -> StreamingSnapshotView | None:
        """Return the latest published streaming view for an underlying."""
        return self._cache.get_view(normalize_underlying_name(underlying))

    def get_history(
        self,
        underlying: str,
        *,
        limit: int | None = None,
    ) -> tuple[MarketSnapshot, ...]:
        """Return up to ``limit`` most recent published snapshots, oldest-first."""
        return self._history.get(normalize_underlying_name(underlying), limit=limit)

    def get_quote(self, instrument_token: int) -> QuoteRecord | None:
        """Return the current quote record for a token, if any."""
        return self._quote_book.get(instrument_token)

    def get_quotes_for_underlying(self, underlying: str) -> tuple[QuoteRecord, ...]:
        """Return all current quote records for an underlying."""
        return self._quote_book.get_for_underlying(normalize_underlying_name(underlying))

    # -- Push access ------------------------------------------------------

    def add_publish_callback(
        self,
        callback: Callable[[StreamingPublishEvent], None],
    ) -> None:
        """Register a publish callback (idempotent by identity)."""
        with self._callbacks_lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def remove_publish_callback(
        self,
        callback: Callable[[StreamingPublishEvent], None],
    ) -> None:
        """Remove a previously registered publish callback."""
        with self._callbacks_lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    # -- Observability ------------------------------------------------------

    def get_health(self) -> StreamingHealthReport:
        """Return an aggregated global and per-underlying health report."""
        now = self._clock()
        lifecycle_state = self.get_status()

        per_underlying: list[SnapshotHealth] = []
        statuses: list[StreamingHealthStatus] = []
        for underlying in self._config.enabled_underlyings:
            health, status = self._compute_underlying_health(underlying, now)
            per_underlying.append(health)
            statuses.append(status)

        overall = _compute_overall_health(statuses, lifecycle_state)
        healthy = tuple(
            h.underlying for h, s in zip(per_underlying, statuses) if s is StreamingHealthStatus.HEALTHY
        )
        degraded = tuple(
            h.underlying for h, s in zip(per_underlying, statuses) if s is StreamingHealthStatus.DEGRADED
        )
        unhealthy = tuple(
            h.underlying for h, s in zip(per_underlying, statuses) if s is StreamingHealthStatus.UNHEALTHY
        )

        return StreamingHealthReport(
            report_id=self._id_factory(),
            as_of=now,
            overall_health=overall,
            lifecycle_state=lifecycle_state,
            enabled_underlyings=self._config.enabled_underlyings,
            healthy_underlyings=healthy,
            degraded_underlyings=degraded,
            unhealthy_underlyings=unhealthy,
            per_underlying=tuple(per_underlying),
            statistics=self.get_statistics(),
            issues=(),
            metadata=self._config.metadata,
        )

    def _compute_underlying_health(
        self,
        underlying: str,
        now: datetime,
    ) -> tuple[SnapshotHealth, StreamingHealthStatus]:
        with self._state_lock:
            ops = self._underlying_ops[underlying]
            consecutive_failures = ops.consecutive_publish_failures
            last_publish_outcome = ops.last_publish_outcome
            stats = self._underlying_stats[underlying]
            last_tick_at = stats.last_tick_at

        cached = self._cache.get(underlying)
        tier = classify_underlying_tier(underlying)
        issues: list[StreamingHealthIssue] = []

        has_snapshot = cached is not None
        freshness_status = cached.freshness.status if cached is not None else None
        validation_status = cached.quality.validation_status if cached is not None else None
        completeness_score = (
            cached.quality.completeness_score if cached is not None else None
        )
        has_warnings = bool(cached.quality.warnings) if cached is not None else False
        seconds_since_last_snapshot = (
            (now - cached.provenance.as_of).total_seconds() if cached is not None else None
        )

        threshold = DEFAULT_DEGRADED_FAILURE_THRESHOLD
        registered_count = self._quote_book.underlying_token_count(underlying)

        if not has_snapshot:
            if consecutive_failures >= threshold:
                status = StreamingHealthStatus.UNHEALTHY
                issues.append(
                    _issue(
                        "MDS.HEALTH.CONSECUTIVE_FAILURES",
                        "error",
                        "Consecutive publish failures reached the degraded threshold.",
                        underlying=underlying,
                    )
                )
            elif registered_count > 0:
                status = StreamingHealthStatus.UNHEALTHY
            else:
                status = StreamingHealthStatus.UNKNOWN
            if last_publish_outcome is None:
                issues.append(
                    _issue(
                        "MDS.HEALTH.NO_SNAPSHOT_YET",
                        "info",
                        "Underlying enabled but never published (startup grace period).",
                        underlying=underlying,
                    )
                )
            if registered_count == 0:
                issues.append(
                    _issue(
                        "MDS.HEALTH.NO_INSTRUMENTS_REGISTERED",
                        "warning",
                        "Enabled underlying has zero registered descriptors.",
                        underlying=underlying,
                    )
                )
        else:
            if (
                freshness_status is SnapshotFreshnessStatus.FUTURE_TIMESTAMP
                or consecutive_failures >= threshold
            ):
                status = StreamingHealthStatus.UNHEALTHY
                if consecutive_failures >= threshold:
                    issues.append(
                        _issue(
                            "MDS.HEALTH.CONSECUTIVE_FAILURES",
                            "error",
                            "Consecutive publish failures reached the degraded threshold.",
                            underlying=underlying,
                        )
                    )
            elif (
                freshness_status
                in (SnapshotFreshnessStatus.STALE, SnapshotFreshnessStatus.MARKET_CLOSED)
                or (validation_status is SnapshotValidationStatus.PARTIAL and has_warnings)
                or (1 <= consecutive_failures < threshold)
            ):
                status = StreamingHealthStatus.DEGRADED
                if freshness_status is SnapshotFreshnessStatus.STALE:
                    issues.append(
                        _issue(
                            "MDS.HEALTH.STALE_SNAPSHOT",
                            "warning",
                            "Cached snapshot freshness status is STALE.",
                            underlying=underlying,
                        )
                    )
            else:
                status = StreamingHealthStatus.HEALTHY

        silence_budget = self._config.tick_staleness_seconds * 3
        if last_tick_at is None or (now - last_tick_at).total_seconds() > silence_budget:
            issues.append(
                _issue(
                    "MDS.HEALTH.UNDERLYING_SILENT",
                    "warning",
                    "No accepted tick for this underlying within the silence budget.",
                    underlying=underlying,
                )
            )

        descriptors = self._quote_book.get_descriptors_for_underlying(underlying)
        if descriptors:
            has_spot = any(d.instrument_role is InstrumentRole.SPOT for d in descriptors)
            has_other = any(d.instrument_role is not InstrumentRole.SPOT for d in descriptors)
            if has_other and not has_spot:
                issues.append(
                    _issue(
                        "MDS.HEALTH.MISSING_SPOT_INSTRUMENT",
                        "error",
                        "Underlying has option/future descriptors but no SPOT instrument.",
                        underlying=underlying,
                    )
                )

        health = SnapshotHealth(
            underlying=underlying,
            support_tier=tier,
            has_snapshot=has_snapshot,
            consecutive_publish_failures=consecutive_failures,
            issues=tuple(issues),
            freshness_status=freshness_status,
            validation_status=validation_status,
            completeness_score=completeness_score,
            seconds_since_last_snapshot=seconds_since_last_snapshot,
            last_publish_outcome=last_publish_outcome,
        )
        return health, status

    def get_statistics(self) -> SnapshotStatistics:
        """Return global and per-underlying tick/snapshot statistics."""
        now = self._clock()
        with self._state_lock:
            per_underlying = tuple(
                self._build_underlying_statistics(u) for u in self._config.enabled_underlyings
            )
            return SnapshotStatistics(
                as_of=now,
                total_tick_count=self._global_stats.total_tick_count,
                total_rejected_tick_count=self._global_stats.total_rejected_tick_count,
                unattributed_tick_count=self._global_stats.unattributed_tick_count,
                total_snapshot_published_count=self._global_stats.total_snapshot_published_count,
                total_snapshot_skipped_count=self._global_stats.total_snapshot_skipped_count,
                total_snapshot_failed_count=self._global_stats.total_snapshot_failed_count,
                enabled_underlyings=self._config.enabled_underlyings,
                per_underlying=per_underlying,
            )

    def _build_underlying_statistics(self, underlying: str) -> UnderlyingStreamStatistics:
        stats = self._underlying_stats[underlying]
        average_ms = (
            stats.assembly_duration_total_ms / stats.assembly_duration_count
            if stats.assembly_duration_count
            else None
        )
        max_ms = stats.assembly_duration_max_ms if stats.assembly_duration_count else None
        return UnderlyingStreamStatistics(
            underlying=underlying,
            support_tier=classify_underlying_tier(underlying),
            tick_count=stats.tick_count,
            rejected_tick_count=stats.rejected_tick_count,
            unique_instruments_seen=len(stats.unique_instruments),
            snapshot_attempt_count=stats.snapshot_attempt_count,
            snapshot_published_count=stats.snapshot_published_count,
            snapshot_skipped_count=stats.snapshot_skipped_count,
            snapshot_failed_count=stats.snapshot_failed_count,
            last_tick_at=stats.last_tick_at,
            last_snapshot_at=stats.last_snapshot_at,
            average_assembly_ms=average_ms,
            max_assembly_ms=max_ms,
        )

    def reset_statistics(self) -> None:
        """Zero all statistics counters and timing accumulators.

        Never clears the cache, history, quote book, or underlying
        identity (Rule STATS-001).
        """
        with self._state_lock:
            self._global_stats = _GlobalStatsState()
            for underlying in self._config.enabled_underlyings:
                self._underlying_stats[underlying] = _UnderlyingStatsState()

    def validate(self) -> tuple[StreamingHealthIssue, ...]:
        """Return static configuration/registry consistency issues.

        Never mutates state and never raises.
        """
        issues: list[StreamingHealthIssue] = []
        for underlying in self._config.enabled_underlyings:
            descriptors = self._quote_book.get_descriptors_for_underlying(underlying)
            if not descriptors:
                issues.append(
                    _issue(
                        "MDS.VALIDATION.UNDERLYING_WITHOUT_INSTRUMENTS",
                        "warning",
                        f"{underlying} has zero registered instruments.",
                        underlying=underlying,
                    )
                )
                continue
            if not any(d.instrument_role is InstrumentRole.SPOT for d in descriptors):
                issues.append(
                    _issue(
                        "MDS.VALIDATION.UNDERLYING_WITHOUT_SPOT",
                        "error",
                        f"{underlying} has no registered SPOT instrument.",
                        underlying=underlying,
                    )
                )
        if not (0.0 <= self._config.max_missing_quote_ratio <= 1.0):
            issues.append(
                _issue(
                    "MDS.VALIDATION.CONFIG_THRESHOLD_OUT_OF_RANGE",
                    "error",
                    "max_missing_quote_ratio is out of the documented valid range.",
                )
            )
        return tuple(issues)

    # -- Statistics recording (private) --------------------------------------

    def _record_rejected(self, underlying: str | None) -> None:
        with self._state_lock:
            self._global_stats.total_rejected_tick_count += 1
            if underlying is not None:
                stats = self._underlying_stats.get(underlying)
                if stats is not None:
                    stats.rejected_tick_count += 1

    def _record_unattributed(self) -> None:
        with self._state_lock:
            self._global_stats.unattributed_tick_count += 1

    def _record_tick(self, underlying: str, instrument_token: int, now: datetime) -> None:
        with self._state_lock:
            self._global_stats.total_tick_count += 1
            stats = self._underlying_stats[underlying]
            stats.tick_count += 1
            stats.unique_instruments.add(instrument_token)
            stats.last_tick_at = now

    def _record_snapshot_attempt(self, underlying: str) -> None:
        with self._state_lock:
            self._underlying_stats[underlying].snapshot_attempt_count += 1

    # -- Assembly (private, spec section 11) ---------------------------------

    def _maybe_assemble(self, underlying: str, now: datetime) -> None:
        with self._state_lock:
            ops = self._underlying_ops[underlying]
            last_attempt = ops.last_attempt_at
            if (
                last_attempt is not None
                and (now - last_attempt).total_seconds()
                < self._config.snapshot_min_interval_seconds
            ):
                return
            ops.last_attempt_at = now
        self._assemble_and_publish(underlying, now)

    def _select_nearest_expiry(
        self,
        option_quotes: Sequence[QuoteRecord],
        now: datetime,
    ) -> str | None:
        expiries = sorted(
            {
                q.descriptor.expiry
                for q in option_quotes
                if q.descriptor is not None and q.descriptor.expiry
            }
        )
        if not expiries:
            return None
        today = now.astimezone(ZoneInfo(IST_ZONE)).date().isoformat()
        for expiry in expiries:
            if expiry >= today:
                return expiry
        return expiries[-1]

    def _select_nearest_future(
        self,
        future_quotes: Sequence[QuoteRecord],
    ) -> QuoteRecord | None:
        if not future_quotes:
            return None
        with_expiry = [
            q for q in future_quotes if q.descriptor is not None and q.descriptor.expiry
        ]
        if not with_expiry:
            return future_quotes[0]
        return min(with_expiry, key=lambda q: q.descriptor.expiry)  # type: ignore[union-attr]

    def _build_contract(
        self,
        quote: QuoteRecord,
        underlying: str,
        expiry: str,
    ) -> OptionContractSnapshot:
        descriptor = quote.descriptor
        assert descriptor is not None  # guaranteed by the caller's filter
        tick = quote.last_tick
        greeks = tick.greeks
        bid = tick.bid if tick.bid is not None else 0.0
        ask = tick.ask if tick.ask is not None else 0.0
        ltp = tick.last_price if tick.last_price > 0 else (ask or bid or 0.01)
        quote_timestamp = normalize_exchange_timestamp(tick.exchange_timestamp) or tick.received_at
        return OptionContractSnapshot(
            underlying=underlying,
            exchange=descriptor.exchange,
            tradingsymbol=descriptor.tradingsymbol,
            expiry=expiry,
            strike=descriptor.strike,  # type: ignore[arg-type]
            option_type=OptionType(descriptor.option_type),
            lot_size=descriptor.lot_size,  # type: ignore[arg-type]
            ltp=ltp,
            bid=bid,
            ask=ask,
            volume=tick.volume,
            open_interest=tick.open_interest or 0,
            delta=greeks.delta if greeks else None,
            iv=greeks.iv if greeks else None,
            gamma=greeks.gamma if greeks else None,
            theta=greeks.theta if greeks else None,
            vega=greeks.vega if greeks else None,
            instrument_token=quote.instrument_token,
            tick_size=descriptor.tick_size,
            quote_timestamp=quote_timestamp,
            average_price=tick.average_price,
        )

    def _build_underlying_snapshot(self, spot: QuoteRecord) -> UnderlyingSnapshot:
        tick = spot.last_tick
        descriptor = spot.descriptor
        quote_timestamp = normalize_exchange_timestamp(tick.exchange_timestamp) or tick.received_at
        return UnderlyingSnapshot(
            symbol=descriptor.tradingsymbol if descriptor else tick.tradingsymbol,
            exchange=descriptor.exchange if descriptor else tick.exchange,
            quote_key=descriptor.quote_key if descriptor else tick.quote_key,
            last_price=tick.last_price,
            open=tick.open,
            high=tick.high,
            low=tick.low,
            previous_close=tick.close,
            quote_timestamp=quote_timestamp,
            volume=tick.volume,
        )

    def _build_volatility_snapshot(self, vol: QuoteRecord) -> VolatilitySnapshot:
        tick = vol.last_tick
        descriptor = vol.descriptor
        quote_timestamp = normalize_exchange_timestamp(tick.exchange_timestamp) or tick.received_at
        return VolatilitySnapshot(
            symbol=descriptor.tradingsymbol if descriptor else tick.tradingsymbol,
            exchange=descriptor.exchange if descriptor else tick.exchange,
            quote_key=descriptor.quote_key if descriptor else tick.quote_key,
            last_price=tick.last_price,
            quote_timestamp=quote_timestamp,
        )

    def _build_futures_snapshot(self, quote: QuoteRecord, spot_price: float) -> FuturesSnapshot:
        tick = quote.last_tick
        descriptor = quote.descriptor
        quote_timestamp = normalize_exchange_timestamp(tick.exchange_timestamp) or tick.received_at
        return FuturesSnapshot(
            underlying=quote.underlying,
            exchange=descriptor.exchange if descriptor else tick.exchange,
            tradingsymbol=descriptor.tradingsymbol if descriptor else tick.tradingsymbol,
            expiry=(descriptor.expiry if descriptor and descriptor.expiry else ""),
            instrument_token=quote.instrument_token,
            last_price=tick.last_price,
            bid=tick.bid,
            ask=tick.ask,
            volume=tick.volume,
            open_interest=tick.open_interest,
            basis=tick.last_price - spot_price,
            quote_timestamp=quote_timestamp,
        )

    def _build_streaming_view(
        self,
        underlying: str,
        snapshot: MarketSnapshot,
        future_quote: QuoteRecord | None,
        now: datetime,
    ) -> StreamingSnapshotView:
        contracts = snapshot.option_chain.contracts
        atm_strike = snapshot.option_chain.metadata.atm_strike
        atm_call = next(
            (c for c in contracts if c.strike == atm_strike and c.option_type is OptionType.CE),
            None,
        )
        atm_put = next(
            (c for c in contracts if c.strike == atm_strike and c.option_type is OptionType.PE),
            None,
        )

        atm_ivs = [c.iv for c in (atm_call, atm_put) if c is not None and c.iv is not None]
        atm_iv = (sum(atm_ivs) / len(atm_ivs)) if atm_ivs else None

        total_call_oi = sum(c.open_interest for c in contracts if c.option_type is OptionType.CE)
        total_put_oi = sum(c.open_interest for c in contracts if c.option_type is OptionType.PE)
        put_call_oi_ratio = (total_put_oi / total_call_oi) if total_call_oi else None
        total_volume = sum(c.volume for c in contracts)

        futures_snapshot = None
        if future_quote is not None:
            futures_snapshot = self._build_futures_snapshot(
                future_quote, snapshot.underlying.last_price
            )

        expected_move = None
        if self._config.expected_move_enabled and atm_iv is not None:
            expiry_date = date.fromisoformat(snapshot.option_chain.metadata.expiry)
            today = now.astimezone(ZoneInfo(IST_ZONE)).date()
            days_to_expiry = float((expiry_date - today).days)
            expected_move = compute_expected_move(
                underlying=underlying,
                spot=snapshot.underlying.last_price,
                atm_iv=atm_iv,
                days_to_expiry=days_to_expiry,
                trading_days_per_year=self._config.expected_move_trading_days_per_year,
                now=now,
            )

        return StreamingSnapshotView(
            underlying=underlying,
            snapshot=snapshot,
            atm_strike=atm_strike,
            total_call_oi=total_call_oi,
            total_put_oi=total_put_oi,
            total_volume=total_volume,
            as_of=snapshot.provenance.as_of,
            futures=futures_snapshot,
            atm_call=atm_call,
            atm_put=atm_put,
            atm_iv=atm_iv,
            expected_move=expected_move,
            put_call_oi_ratio=put_call_oi_ratio,
        )

    def _evaluate_streaming_gates(
        self,
        candidate: MarketSnapshot,
        *,
        future: QuoteRecord | None,
        volatility: QuoteRecord | None,
        contributing: Sequence[QuoteRecord],
        now: datetime,
    ) -> tuple[SnapshotPublishOutcome, str, str] | None:
        """Apply streaming-only publish gates in fixed order (spec section 13.3)."""
        contract_count = candidate.option_chain.metadata.contract_count
        missing_ratio = (
            candidate.quality.missing_quotes / contract_count if contract_count else 0.0
        )
        if missing_ratio > self._config.max_missing_quote_ratio:
            return (
                SnapshotPublishOutcome.SKIPPED,
                "MDS.SNAPSHOT.INSUFFICIENT_COVERAGE",
                "Missing-quote ratio exceeds the configured maximum.",
            )
        if candidate.option_chain.metadata.complete_pairs < self._config.min_complete_pairs:
            return (
                SnapshotPublishOutcome.SKIPPED,
                "MDS.SNAPSHOT.INSUFFICIENT_PAIRS",
                "Complete CE/PE strike pairs are below the configured minimum.",
            )
        if self._config.require_futures_for_snapshot and future is None:
            return (
                SnapshotPublishOutcome.SKIPPED,
                "MDS.SNAPSHOT.FUTURES_REQUIRED",
                "Futures quote is required but unavailable.",
            )
        if self._config.require_volatility_index and volatility is None:
            return (
                SnapshotPublishOutcome.SKIPPED,
                "MDS.SNAPSHOT.VOLATILITY_REQUIRED",
                "Volatility index quote is required but unavailable.",
            )
        if any(self._quote_book.is_stale(q.instrument_token, now=now) for q in contributing):
            return (
                SnapshotPublishOutcome.SKIPPED,
                "MDS.SNAPSHOT.STALE_INPUT",
                "A contributing quote exceeds the staleness budget.",
            )
        return None

    def _run_assembly(
        self,
        underlying: str,
        now: datetime,
        start: float,
    ) -> StreamingPublishEvent:
        quotes = self._quote_book.get_for_underlying(underlying)
        spot_candidates = [q for q in quotes if q.instrument_role is InstrumentRole.SPOT]

        if not spot_candidates:
            return self._publish(
                underlying,
                SnapshotPublishOutcome.FAILED,
                now=now,
                duration_ms=_elapsed_ms(start),
                reason_code="MDS.SNAPSHOT.MISSING_SPOT",
                reason_message="No SPOT quote is available for assembly.",
            )
        if len(spot_candidates) > 1:
            return self._publish(
                underlying,
                SnapshotPublishOutcome.FAILED,
                now=now,
                duration_ms=_elapsed_ms(start),
                reason_code="MDS.SNAPSHOT.AMBIGUOUS_SPOT",
                reason_message="More than one SPOT instrument is registered for this underlying.",
            )
        spot = spot_candidates[0]

        future_candidates = [q for q in quotes if q.instrument_role is InstrumentRole.FUTURE]
        future = self._select_nearest_future(future_candidates)

        vol_candidates = [
            q for q in quotes if q.instrument_role is InstrumentRole.VOLATILITY_INDEX
        ]
        volatility = vol_candidates[0] if vol_candidates else None

        option_candidates = [
            q
            for q in quotes
            if q.instrument_role in (InstrumentRole.OPTION_CE, InstrumentRole.OPTION_PE)
        ]
        selected_expiry = self._select_nearest_expiry(option_candidates, now)
        options = (
            [
                q
                for q in option_candidates
                if q.descriptor is not None and q.descriptor.expiry == selected_expiry
            ]
            if selected_expiry is not None
            else []
        )

        if not options:
            return self._publish(
                underlying,
                SnapshotPublishOutcome.SKIPPED,
                now=now,
                duration_ms=_elapsed_ms(start),
                reason_code="MDS.SNAPSHOT.INSUFFICIENT_COVERAGE",
                reason_message="No option contracts are available for the selected expiry.",
            )

        strikes = sorted(
            {
                q.descriptor.strike
                for q in options
                if q.descriptor is not None and q.descriptor.strike is not None
            }
        )
        step = self._config.strike_step.get(underlying, self._config.default_strike_step)
        atm_strike = derive_atm(spot.last_tick.last_price, step, strikes)

        contracts = tuple(
            self._build_contract(q, underlying, selected_expiry) for q in options  # type: ignore[arg-type]
        )
        underlying_snapshot = self._build_underlying_snapshot(spot)
        volatility_snapshot = (
            self._build_volatility_snapshot(volatility) if volatility is not None else None
        )

        exchange = options[0].descriptor.exchange  # type: ignore[union-attr]
        lot_size = options[0].descriptor.lot_size  # type: ignore[union-attr]
        correlation_id = _extract_correlation_id(spot.last_tick)

        try:
            candidate = build_market_snapshot(
                underlying=underlying_snapshot,
                contracts=contracts,
                underlying_symbol=underlying,
                exchange=exchange,
                expiry=selected_expiry,  # type: ignore[arg-type]
                atm_strike=atm_strike,
                strike_step=step,
                strike_window_strikes=self._config.strike_window_strikes,
                minimum_strike=min(strikes),
                maximum_strike=max(strikes),
                lot_size=lot_size,  # type: ignore[arg-type]
                as_of=now,
                captured_at=now,
                source=SnapshotSource.LIVE,
                adapter_name=PRODUCER_NAME,
                adapter_version=MARKET_DATA_STREAMING_VERSION,
                correlation_id=correlation_id,
                snapshot_id=self._id_factory(),
                volatility=volatility_snapshot,
                freshness_policy=self._config.freshness_policy,
                validation_policy=self._config.validation_policy,
                reference_time=now,
                strict=False,
            )
        except SnapshotBuildError as exc:
            return self._publish(
                underlying,
                SnapshotPublishOutcome.FAILED,
                now=now,
                duration_ms=_elapsed_ms(start),
                reason_code="MDS.SNAPSHOT.BUILD_FAILED",
                reason_message=str(exc),
                correlation_id=correlation_id,
            )

        if candidate.quality.validation_status is SnapshotValidationStatus.INVALID:
            return self._publish(
                underlying,
                SnapshotPublishOutcome.FAILED,
                now=now,
                duration_ms=_elapsed_ms(start),
                reason_code="MDS.SNAPSHOT.CANONICAL_INVALID",
                reason_message="Canonical validation rejected the candidate snapshot.",
                correlation_id=correlation_id,
            )

        contributing: list[QuoteRecord] = [spot, *options]
        if future is not None:
            contributing.append(future)
        if volatility is not None:
            contributing.append(volatility)

        gate_failure = self._evaluate_streaming_gates(
            candidate,
            future=future,
            volatility=volatility,
            contributing=contributing,
            now=now,
        )
        if gate_failure is not None:
            outcome, code, message = gate_failure
            return self._publish(
                underlying,
                outcome,
                now=now,
                duration_ms=_elapsed_ms(start),
                reason_code=code,
                reason_message=message,
                correlation_id=correlation_id,
            )

        view = self._build_streaming_view(underlying, candidate, future, now)
        return self._publish(
            underlying,
            SnapshotPublishOutcome.PUBLISHED,
            snapshot=candidate,
            view=view,
            now=now,
            duration_ms=_elapsed_ms(start),
            correlation_id=correlation_id,
        )

    def _assemble_and_publish(self, underlying: str, now: datetime) -> StreamingPublishEvent:
        start = time.perf_counter()
        self._record_snapshot_attempt(underlying)
        try:
            return self._run_assembly(underlying, now, start)
        except Exception:  # noqa: BLE001 - assembly must never crash ingestion
            _LOGGER.exception(
                "market_data_streaming.assembly.unexpected_failure",
                extra={"underlying": underlying},
            )
            return self._publish(
                underlying,
                SnapshotPublishOutcome.FAILED,
                now=now,
                duration_ms=_elapsed_ms(start),
                reason_code="MDS.SNAPSHOT.BUILD_FAILED",
                reason_message="Unexpected assembly failure.",
            )

    def _publish(
        self,
        underlying: str,
        outcome: SnapshotPublishOutcome,
        *,
        now: datetime,
        duration_ms: float,
        snapshot: MarketSnapshot | None = None,
        view: StreamingSnapshotView | None = None,
        reason_code: str | None = None,
        reason_message: str | None = None,
        correlation_id: str | None = None,
    ) -> StreamingPublishEvent:
        """Dispatch one publish attempt (spec section 14.1)."""
        with self._state_lock:
            ops = self._underlying_ops[underlying]
            ops.publish_sequence += 1
            sequence = ops.publish_sequence
            if outcome is SnapshotPublishOutcome.PUBLISHED:
                ops.consecutive_publish_failures = 0
            else:
                ops.consecutive_publish_failures += 1
            ops.last_publish_outcome = outcome

            stats = self._underlying_stats[underlying]
            if outcome is SnapshotPublishOutcome.PUBLISHED:
                stats.snapshot_published_count += 1
                stats.last_snapshot_at = now
                self._global_stats.total_snapshot_published_count += 1
            elif outcome is SnapshotPublishOutcome.SKIPPED:
                stats.snapshot_skipped_count += 1
                self._global_stats.total_snapshot_skipped_count += 1
            else:
                stats.snapshot_failed_count += 1
                self._global_stats.total_snapshot_failed_count += 1
            stats.assembly_duration_total_ms += duration_ms
            stats.assembly_duration_count += 1
            stats.assembly_duration_max_ms = max(stats.assembly_duration_max_ms, duration_ms)

        event = StreamingPublishEvent(
            event_id=self._id_factory(),
            underlying=underlying,
            outcome=outcome,
            published_at=now,
            sequence=sequence,
            snapshot=snapshot,
            view=view,
            reason_code=reason_code,
            reason_message=reason_message,
            correlation_id=correlation_id,
        )

        if outcome is SnapshotPublishOutcome.PUBLISHED:
            assert snapshot is not None and view is not None
            self._cache.put(underlying, snapshot, view)
            self._history.append(underlying, snapshot)

        with self._callbacks_lock:
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            try:
                callback(event)
            except Exception:  # noqa: BLE001 - callback isolation boundary
                _LOGGER.exception("market_data_streaming.callback.failed")

        if self._config.publish_events and self._event_bus is not None:
            topic = _TOPIC_FOR_OUTCOME[outcome]
            try:
                self._event_bus.publish(
                    topic,
                    event,
                    correlation_id=correlation_id or event.event_id,
                    producer=PRODUCER_NAME,
                    occurred_at=now,
                )
            except Exception:  # noqa: BLE001 - event bus isolation boundary
                _LOGGER.exception("market_data_streaming.event_bus.publish_failed")

        return event


StreamingSnapshotService = MarketDataStreamingEngine
"""Alias emphasizing this class's role as a continuous snapshot source
for ``MarketDataEngine`` and other pull-based consumers."""


# ---------------------------------------------------------------------------
# Serialization (spec section 25)
# ---------------------------------------------------------------------------


def _dt_to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_to_dt(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MarketDataStreamingSerializationError(
            f"Invalid ISO-8601 timestamp: {value!r}.",
            code="MDS.SERIALIZATION.MALFORMED",
        ) from exc
    if parsed.tzinfo is None:
        raise MarketDataStreamingSerializationError(
            f"Timestamp must be timezone-aware: {value!r}.",
            code="MDS.SERIALIZATION.MALFORMED",
        )
    return parsed


def _check_schema_version(version: Any) -> None:
    if version is None:
        return
    if not isinstance(version, str):
        raise MarketDataStreamingSerializationError(
            "schema_version must be a string.",
            code="MDS.SERIALIZATION.UNSUPPORTED_VERSION",
        )
    major = version.split(".", 1)[0]
    expected_major = MARKET_DATA_STREAMING_SCHEMA_VERSION.split(".", 1)[0]
    if major != expected_major:
        raise MarketDataStreamingSerializationError(
            f"Unsupported schema version: {version}.",
            code="MDS.SERIALIZATION.UNSUPPORTED_VERSION",
        )


def _issue_to_dict(issue: StreamingHealthIssue) -> dict[str, Any]:
    return {
        "issue_code": issue.issue_code,
        "severity": issue.severity,
        "message": issue.message,
        "underlying": issue.underlying,
        "instrument_token": issue.instrument_token,
    }


def _issue_from_dict(data: Mapping[str, Any]) -> StreamingHealthIssue:
    return StreamingHealthIssue(
        issue_code=str(data["issue_code"]),
        severity=data["severity"],
        message=str(data["message"]),
        underlying=data.get("underlying"),
        instrument_token=data.get("instrument_token"),
    )


def _snapshot_health_to_dict(health: SnapshotHealth) -> dict[str, Any]:
    return {
        "underlying": health.underlying,
        "support_tier": health.support_tier.value,
        "has_snapshot": health.has_snapshot,
        "freshness_status": (
            health.freshness_status.value if health.freshness_status is not None else None
        ),
        "validation_status": (
            health.validation_status.value if health.validation_status is not None else None
        ),
        "completeness_score": health.completeness_score,
        "seconds_since_last_snapshot": health.seconds_since_last_snapshot,
        "consecutive_publish_failures": health.consecutive_publish_failures,
        "last_publish_outcome": (
            health.last_publish_outcome.value
            if health.last_publish_outcome is not None
            else None
        ),
        "issues": [_issue_to_dict(i) for i in health.issues],
    }


def _snapshot_health_from_dict(data: Mapping[str, Any]) -> SnapshotHealth:
    return SnapshotHealth(
        underlying=str(data["underlying"]),
        support_tier=UnderlyingSupportTier(data["support_tier"]),
        has_snapshot=bool(data["has_snapshot"]),
        consecutive_publish_failures=int(data["consecutive_publish_failures"]),
        issues=tuple(_issue_from_dict(i) for i in data.get("issues", ())),
        freshness_status=(
            SnapshotFreshnessStatus(data["freshness_status"])
            if data.get("freshness_status")
            else None
        ),
        validation_status=(
            SnapshotValidationStatus(data["validation_status"])
            if data.get("validation_status")
            else None
        ),
        completeness_score=data.get("completeness_score"),
        seconds_since_last_snapshot=data.get("seconds_since_last_snapshot"),
        last_publish_outcome=(
            SnapshotPublishOutcome(data["last_publish_outcome"])
            if data.get("last_publish_outcome")
            else None
        ),
    )


def _underlying_statistics_to_dict(stats: UnderlyingStreamStatistics) -> dict[str, Any]:
    return {
        "underlying": stats.underlying,
        "support_tier": stats.support_tier.value,
        "tick_count": stats.tick_count,
        "rejected_tick_count": stats.rejected_tick_count,
        "unique_instruments_seen": stats.unique_instruments_seen,
        "snapshot_attempt_count": stats.snapshot_attempt_count,
        "snapshot_published_count": stats.snapshot_published_count,
        "snapshot_skipped_count": stats.snapshot_skipped_count,
        "snapshot_failed_count": stats.snapshot_failed_count,
        "last_tick_at": _dt_to_iso(stats.last_tick_at) if stats.last_tick_at else None,
        "last_snapshot_at": (
            _dt_to_iso(stats.last_snapshot_at) if stats.last_snapshot_at else None
        ),
        "average_assembly_ms": stats.average_assembly_ms,
        "max_assembly_ms": stats.max_assembly_ms,
    }


def _underlying_statistics_from_dict(data: Mapping[str, Any]) -> UnderlyingStreamStatistics:
    return UnderlyingStreamStatistics(
        underlying=str(data["underlying"]),
        support_tier=UnderlyingSupportTier(data["support_tier"]),
        tick_count=int(data["tick_count"]),
        rejected_tick_count=int(data["rejected_tick_count"]),
        unique_instruments_seen=int(data["unique_instruments_seen"]),
        snapshot_attempt_count=int(data["snapshot_attempt_count"]),
        snapshot_published_count=int(data["snapshot_published_count"]),
        snapshot_skipped_count=int(data["snapshot_skipped_count"]),
        snapshot_failed_count=int(data["snapshot_failed_count"]),
        last_tick_at=_iso_to_dt(data["last_tick_at"]) if data.get("last_tick_at") else None,
        last_snapshot_at=(
            _iso_to_dt(data["last_snapshot_at"]) if data.get("last_snapshot_at") else None
        ),
        average_assembly_ms=data.get("average_assembly_ms"),
        max_assembly_ms=data.get("max_assembly_ms"),
    )


def serialize_snapshot_statistics(stats: SnapshotStatistics) -> dict[str, Any]:
    """Serialize :class:`SnapshotStatistics` to a JSON-ready dictionary."""
    return {
        "schema_version": MARKET_DATA_STREAMING_SCHEMA_VERSION,
        "as_of": _dt_to_iso(stats.as_of),
        "total_tick_count": stats.total_tick_count,
        "total_rejected_tick_count": stats.total_rejected_tick_count,
        "unattributed_tick_count": stats.unattributed_tick_count,
        "total_snapshot_published_count": stats.total_snapshot_published_count,
        "total_snapshot_skipped_count": stats.total_snapshot_skipped_count,
        "total_snapshot_failed_count": stats.total_snapshot_failed_count,
        "enabled_underlyings": list(stats.enabled_underlyings),
        "per_underlying": [_underlying_statistics_to_dict(u) for u in stats.per_underlying],
    }


def deserialize_snapshot_statistics(data: Mapping[str, Any]) -> SnapshotStatistics:
    """Deserialize :class:`SnapshotStatistics` from a dictionary.

    Raises:
        MarketDataStreamingSerializationError: On malformed or unsupported
            payloads.
    """
    _check_schema_version(data.get("schema_version"))
    try:
        return SnapshotStatistics(
            as_of=_iso_to_dt(data["as_of"]),
            total_tick_count=int(data["total_tick_count"]),
            total_rejected_tick_count=int(data["total_rejected_tick_count"]),
            unattributed_tick_count=int(data["unattributed_tick_count"]),
            total_snapshot_published_count=int(data["total_snapshot_published_count"]),
            total_snapshot_skipped_count=int(data["total_snapshot_skipped_count"]),
            total_snapshot_failed_count=int(data["total_snapshot_failed_count"]),
            enabled_underlyings=tuple(data["enabled_underlyings"]),
            per_underlying=tuple(
                _underlying_statistics_from_dict(u) for u in data.get("per_underlying", ())
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataStreamingSerializationError(
            f"Malformed SnapshotStatistics payload: {exc}.",
            code="MDS.SERIALIZATION.MALFORMED",
        ) from exc


def serialize_streaming_health_report(report: StreamingHealthReport) -> dict[str, Any]:
    """Serialize :class:`StreamingHealthReport` to a JSON-ready dictionary."""
    return {
        "schema_version": MARKET_DATA_STREAMING_SCHEMA_VERSION,
        "report_id": report.report_id,
        "as_of": _dt_to_iso(report.as_of),
        "overall_health": report.overall_health.value,
        "lifecycle_state": report.lifecycle_state.value,
        "enabled_underlyings": list(report.enabled_underlyings),
        "healthy_underlyings": list(report.healthy_underlyings),
        "degraded_underlyings": list(report.degraded_underlyings),
        "unhealthy_underlyings": list(report.unhealthy_underlyings),
        "per_underlying": [_snapshot_health_to_dict(h) for h in report.per_underlying],
        "statistics": serialize_snapshot_statistics(report.statistics),
        "issues": [_issue_to_dict(i) for i in report.issues],
        "metadata": dict(sorted(report.metadata.items())),
    }


def deserialize_streaming_health_report(data: Mapping[str, Any]) -> StreamingHealthReport:
    """Deserialize :class:`StreamingHealthReport` from a dictionary.

    Raises:
        MarketDataStreamingSerializationError: On malformed or unsupported
            payloads.
    """
    _check_schema_version(data.get("schema_version"))
    try:
        return StreamingHealthReport(
            report_id=str(data["report_id"]),
            as_of=_iso_to_dt(data["as_of"]),
            overall_health=StreamingHealthStatus(data["overall_health"]),
            lifecycle_state=StreamingLifecycleState(data["lifecycle_state"]),
            enabled_underlyings=tuple(data["enabled_underlyings"]),
            healthy_underlyings=tuple(data["healthy_underlyings"]),
            degraded_underlyings=tuple(data["degraded_underlyings"]),
            unhealthy_underlyings=tuple(data["unhealthy_underlyings"]),
            per_underlying=tuple(
                _snapshot_health_from_dict(h) for h in data.get("per_underlying", ())
            ),
            statistics=deserialize_snapshot_statistics(data["statistics"]),
            issues=tuple(_issue_from_dict(i) for i in data.get("issues", ())),
            metadata=MappingProxyType(dict(data.get("metadata", {}))),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataStreamingSerializationError(
            f"Malformed StreamingHealthReport payload: {exc}.",
            code="MDS.SERIALIZATION.MALFORMED",
        ) from exc


def _futures_to_dict(futures: FuturesSnapshot) -> dict[str, Any]:
    return {
        "underlying": futures.underlying,
        "exchange": futures.exchange,
        "tradingsymbol": futures.tradingsymbol,
        "expiry": futures.expiry,
        "instrument_token": futures.instrument_token,
        "last_price": futures.last_price,
        "bid": futures.bid,
        "ask": futures.ask,
        "volume": futures.volume,
        "open_interest": futures.open_interest,
        "basis": futures.basis,
        "quote_timestamp": (
            _dt_to_iso(futures.quote_timestamp) if futures.quote_timestamp else None
        ),
    }


def _futures_from_dict(data: Mapping[str, Any]) -> FuturesSnapshot:
    return FuturesSnapshot(
        underlying=str(data["underlying"]),
        exchange=str(data["exchange"]),
        tradingsymbol=str(data["tradingsymbol"]),
        expiry=str(data["expiry"]),
        instrument_token=data.get("instrument_token"),
        last_price=float(data["last_price"]),
        bid=data.get("bid"),
        ask=data.get("ask"),
        volume=data.get("volume"),
        open_interest=data.get("open_interest"),
        basis=data.get("basis"),
        quote_timestamp=(
            _iso_to_dt(data["quote_timestamp"]) if data.get("quote_timestamp") else None
        ),
    )


def _expected_move_to_dict(estimate: ExpectedMoveEstimate) -> dict[str, Any]:
    return {
        "underlying": estimate.underlying,
        "spot": estimate.spot,
        "atm_iv": estimate.atm_iv,
        "days_to_expiry": estimate.days_to_expiry,
        "method": estimate.method,
        "expected_move_points": estimate.expected_move_points,
        "expected_move_percent": estimate.expected_move_percent,
        "upper_bound": estimate.upper_bound,
        "lower_bound": estimate.lower_bound,
        "computed_at": _dt_to_iso(estimate.computed_at),
    }


def _expected_move_from_dict(data: Mapping[str, Any]) -> ExpectedMoveEstimate:
    return ExpectedMoveEstimate(
        underlying=str(data["underlying"]),
        spot=float(data["spot"]),
        atm_iv=float(data["atm_iv"]),
        days_to_expiry=float(data["days_to_expiry"]),
        method=str(data["method"]),
        expected_move_points=float(data["expected_move_points"]),
        expected_move_percent=float(data["expected_move_percent"]),
        upper_bound=float(data["upper_bound"]),
        lower_bound=float(data["lower_bound"]),
        computed_at=_iso_to_dt(data["computed_at"]),
    )


def _contract_reference_to_dict(contract: OptionContractSnapshot) -> dict[str, Any]:
    return {
        "tradingsymbol": contract.tradingsymbol,
        "strike": contract.strike,
        "option_type": contract.option_type.value,
    }


def serialize_streaming_snapshot_view(view: StreamingSnapshotView) -> dict[str, Any]:
    """Serialize :class:`StreamingSnapshotView` to a JSON-ready dictionary.

    The embedded canonical ``MarketSnapshot`` delegates entirely to
    ``market_data.market_snapshot.to_dict`` (Rule SER-MDS-004).
    """
    return {
        "schema_version": MARKET_DATA_STREAMING_SCHEMA_VERSION,
        "underlying": view.underlying,
        "snapshot": _snapshot_to_dict(view.snapshot),
        "futures": _futures_to_dict(view.futures) if view.futures is not None else None,
        "atm_strike": view.atm_strike,
        "atm_call": (
            _contract_reference_to_dict(view.atm_call) if view.atm_call is not None else None
        ),
        "atm_put": (
            _contract_reference_to_dict(view.atm_put) if view.atm_put is not None else None
        ),
        "atm_iv": view.atm_iv,
        "expected_move": (
            _expected_move_to_dict(view.expected_move) if view.expected_move is not None else None
        ),
        "total_call_oi": view.total_call_oi,
        "total_put_oi": view.total_put_oi,
        "put_call_oi_ratio": view.put_call_oi_ratio,
        "total_volume": view.total_volume,
        "as_of": _dt_to_iso(view.as_of),
    }


def deserialize_streaming_snapshot_view(data: Mapping[str, Any]) -> StreamingSnapshotView:
    """Deserialize :class:`StreamingSnapshotView` from a dictionary.

    Raises:
        MarketDataStreamingSerializationError: On malformed or unsupported
            payloads.
    """
    _check_schema_version(data.get("schema_version"))
    try:
        snapshot = _snapshot_from_dict(data["snapshot"])
        contracts_by_key = {
            (c.strike, c.option_type.value): c for c in snapshot.option_chain.contracts
        }

        def _lookup(raw: Mapping[str, Any] | None) -> OptionContractSnapshot | None:
            if raw is None:
                return None
            return contracts_by_key.get((raw["strike"], raw["option_type"]))

        futures_raw = data.get("futures")
        expected_move_raw = data.get("expected_move")
        return StreamingSnapshotView(
            underlying=str(data["underlying"]),
            snapshot=snapshot,
            atm_strike=float(data["atm_strike"]),
            total_call_oi=int(data["total_call_oi"]),
            total_put_oi=int(data["total_put_oi"]),
            total_volume=int(data["total_volume"]),
            as_of=_iso_to_dt(data["as_of"]),
            futures=_futures_from_dict(futures_raw) if futures_raw else None,
            atm_call=_lookup(data.get("atm_call")),
            atm_put=_lookup(data.get("atm_put")),
            atm_iv=data.get("atm_iv"),
            expected_move=(
                _expected_move_from_dict(expected_move_raw) if expected_move_raw else None
            ),
            put_call_oi_ratio=data.get("put_call_oi_ratio"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataStreamingSerializationError(
            f"Malformed StreamingSnapshotView payload: {exc}.",
            code="MDS.SERIALIZATION.MALFORMED",
        ) from exc


def serialize_streaming_publish_event(event: StreamingPublishEvent) -> dict[str, Any]:
    """Serialize :class:`StreamingPublishEvent` to a JSON-ready dictionary."""
    return {
        "schema_version": MARKET_DATA_STREAMING_SCHEMA_VERSION,
        "event_id": event.event_id,
        "underlying": event.underlying,
        "outcome": event.outcome.value,
        "snapshot": _snapshot_to_dict(event.snapshot) if event.snapshot is not None else None,
        "view": (
            serialize_streaming_snapshot_view(event.view) if event.view is not None else None
        ),
        "reason_code": event.reason_code,
        "reason_message": event.reason_message,
        "published_at": _dt_to_iso(event.published_at),
        "correlation_id": event.correlation_id,
        "sequence": event.sequence,
    }


def deserialize_streaming_publish_event(data: Mapping[str, Any]) -> StreamingPublishEvent:
    """Deserialize :class:`StreamingPublishEvent` from a dictionary.

    Raises:
        MarketDataStreamingSerializationError: On malformed or unsupported
            payloads.
    """
    _check_schema_version(data.get("schema_version"))
    try:
        snapshot_raw = data.get("snapshot")
        view_raw = data.get("view")
        return StreamingPublishEvent(
            event_id=str(data["event_id"]),
            underlying=str(data["underlying"]),
            outcome=SnapshotPublishOutcome(data["outcome"]),
            published_at=_iso_to_dt(data["published_at"]),
            sequence=int(data["sequence"]),
            snapshot=_snapshot_from_dict(snapshot_raw) if snapshot_raw else None,
            view=deserialize_streaming_snapshot_view(view_raw) if view_raw else None,
            reason_code=data.get("reason_code"),
            reason_message=data.get("reason_message"),
            correlation_id=data.get("correlation_id"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataStreamingSerializationError(
            f"Malformed StreamingPublishEvent payload: {exc}.",
            code="MDS.SERIALIZATION.MALFORMED",
        ) from exc


def streaming_health_report_to_json(report: StreamingHealthReport) -> str:
    """Serialize :class:`StreamingHealthReport` to a JSON string."""
    return json.dumps(serialize_streaming_health_report(report), indent=2)


def streaming_health_report_from_json(text: str) -> StreamingHealthReport:
    """Deserialize :class:`StreamingHealthReport` from a JSON string.

    Raises:
        MarketDataStreamingSerializationError: On malformed JSON or payload.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MarketDataStreamingSerializationError(
            f"Invalid JSON payload: {exc.msg}.",
            code="MDS.SERIALIZATION.MALFORMED",
        ) from exc
    return deserialize_streaming_health_report(payload)


def snapshot_statistics_to_json(stats: SnapshotStatistics) -> str:
    """Serialize :class:`SnapshotStatistics` to a JSON string."""
    return json.dumps(serialize_snapshot_statistics(stats), indent=2)


def snapshot_statistics_from_json(text: str) -> SnapshotStatistics:
    """Deserialize :class:`SnapshotStatistics` from a JSON string.

    Raises:
        MarketDataStreamingSerializationError: On malformed JSON or payload.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MarketDataStreamingSerializationError(
            f"Invalid JSON payload: {exc.msg}.",
            code="MDS.SERIALIZATION.MALFORMED",
        ) from exc
    return deserialize_snapshot_statistics(payload)


__all__ = [
    "MARKET_DATA_STREAMING_VERSION",
    "MARKET_DATA_STREAMING_SCHEMA_VERSION",
    "PRODUCER_NAME",
    "SUPPORTED_PRIMARY_UNDERLYINGS",
    "SUPPORTED_SECONDARY_UNDERLYINGS",
    "SUPPORTED_UNDERLYINGS",
    "DEFAULT_TICK_STALENESS_SECONDS",
    "DEFAULT_SNAPSHOT_MIN_INTERVAL_SECONDS",
    "DEFAULT_HISTORY_RING_SIZE",
    "DEFAULT_MAX_MISSING_QUOTE_RATIO",
    "DEFAULT_MIN_COMPLETE_PAIRS",
    "DEFAULT_STRIKE_STEP",
    "DEFAULT_EXPECTED_MOVE_TRADING_DAYS_PER_YEAR",
    "DEFAULT_DEGRADED_FAILURE_THRESHOLD",
    "IST_ZONE",
    "TOPIC_SNAPSHOT_PUBLISHED",
    "TOPIC_SNAPSHOT_SKIPPED",
    "TOPIC_SNAPSHOT_FAILED",
    "TOPIC_TICK",
    "TOPIC_HEALTH",
    "InstrumentRole",
    "UnderlyingSupportTier",
    "StreamingLifecycleState",
    "SnapshotPublishOutcome",
    "StreamingHealthStatus",
    "TimestampSource",
    "MarketDataStreamingError",
    "MarketDataStreamingConfigurationError",
    "TickValidationError",
    "InstrumentValidationError",
    "SnapshotAssemblyError",
    "SnapshotPublishError",
    "MarketDataStreamingSerializationError",
    "MarketDataStreamingStateError",
    "GreeksAttachment",
    "TickEvent",
    "InstrumentDescriptor",
    "QuoteRecord",
    "TickNormalizer",
    "FuturesSnapshot",
    "ExpectedMoveEstimate",
    "StreamingSnapshotView",
    "MarketDataStreamingConfig",
    "StreamingPublishEvent",
    "SnapshotHealth",
    "StreamingHealthIssue",
    "StreamingHealthReport",
    "UnderlyingStreamStatistics",
    "SnapshotStatistics",
    "LatestQuoteBook",
    "SnapshotCache",
    "SnapshotHistory",
    "MarketDataStreamingEngine",
    "StreamingSnapshotService",
    "resolve_instrument_role",
    "normalize_exchange_timestamp",
    "derive_atm",
    "compute_expected_move",
    "default_market_data_streaming_config",
    "normalize_underlying_name",
    "classify_underlying_tier",
    "serialize_snapshot_statistics",
    "deserialize_snapshot_statistics",
    "serialize_streaming_health_report",
    "deserialize_streaming_health_report",
    "serialize_streaming_snapshot_view",
    "deserialize_streaming_snapshot_view",
    "serialize_streaming_publish_event",
    "deserialize_streaming_publish_event",
    "streaming_health_report_to_json",
    "streaming_health_report_from_json",
    "snapshot_statistics_to_json",
    "snapshot_statistics_from_json",
]
