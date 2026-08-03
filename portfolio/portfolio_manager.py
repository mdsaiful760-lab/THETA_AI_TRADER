"""Institutional account-level portfolio aggregation for THETA AI TRADER v1.0.

Consumes immutable position snapshots from Position Manager and produces
authoritative portfolio P&L, exposure, Greeks, utilization rollups, and
``portfolio.*`` lifecycle events.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Mapping

from core.event_bus import EventBus, EventEnvelope
from portfolio.position_manager import (
    Position,
    PositionEvent,
    PositionEventType,
    PositionSide,
    PositionSnapshot,
    PositionUpdateResult,
)
from strategy.signals import StrategyExecutionMode, StrategyFamily

PORTFOLIO_MANAGER_VERSION: Final[str] = "1.0.0"
PORTFOLIO_SCHEMA_VERSION: Final[str] = "1.0.0"
PRODUCER_NAME: Final[str] = "portfolio_manager"
DEFAULT_MARGIN_HINT_MAX_AGE_SECONDS: Final[int] = 300
DEFAULT_GREEK_HINT_MAX_AGE_SECONDS: Final[int] = 120
NOTIONAL_ROUND_DECIMALS: Final[int] = 2
PNL_ROUND_DECIMALS: Final[int] = 2
GREEK_ROUND_DECIMALS: Final[int] = 4
UTILIZATION_ROUND_DECIMALS: Final[int] = 4
WEIGHT_ROUND_DECIMALS: Final[int] = 4
PNL_EPSILON: Final[float] = 0.01
MATERIAL_CHANGE_EPSILON: Final[float] = 0.01

ERROR_CONFIG_INVALID: Final[str] = "PORTFOLIO_MANAGER.CONFIG.INVALID"
ERROR_CONTEXT_INVALID: Final[str] = "PORTFOLIO_MANAGER.CONTEXT.INVALID"
ERROR_CONTEXT_NAIVE_TIMESTAMP: Final[str] = "PORTFOLIO_MANAGER.CONTEXT.NAIVE_TIMESTAMP"
ERROR_CONTEXT_CORRELATION_MISMATCH: Final[str] = "PORTFOLIO_MANAGER.CONTEXT.CORRELATION_MISMATCH"
ERROR_CONTEXT_MISSING_ACCOUNT: Final[str] = "PORTFOLIO_MANAGER.CONTEXT.MISSING_ACCOUNT"
ERROR_SNAPSHOT_MISSING: Final[str] = "PORTFOLIO_MANAGER.SNAPSHOT.MISSING"
ERROR_SNAPSHOT_INVALID: Final[str] = "PORTFOLIO_MANAGER.SNAPSHOT.INVALID"
ERROR_POSITION_MAPPING: Final[str] = "PORTFOLIO_MANAGER.POSITION.MAPPING_FAILED"
ERROR_ACCOUNT_INVALID_EQUITY: Final[str] = "PORTFOLIO_MANAGER.ACCOUNT.INVALID_EQUITY"
ERROR_EXPOSURE_COMPUTATION: Final[str] = "PORTFOLIO_MANAGER.EXPOSURE.COMPUTATION_FAILED"
ERROR_RESULT_INVALID: Final[str] = "PORTFOLIO_MANAGER.RESULT.INVALID"
ERROR_RESULT_FINGERPRINT_MISMATCH: Final[str] = "PORTFOLIO_MANAGER.RESULT.FINGERPRINT_MISMATCH"
ERROR_SERIALIZATION_UNSUPPORTED_VERSION: Final[str] = (
    "PORTFOLIO_MANAGER.SERIALIZATION.UNSUPPORTED_VERSION"
)
ERROR_SERIALIZATION_MALFORMED: Final[str] = "PORTFOLIO_MANAGER.SERIALIZATION.MALFORMED"

WARN_PNL_MISMATCH: Final[str] = "PORTFOLIO_MANAGER.PNL.MISMATCH"
WARN_PRICE_MARK_MISSING: Final[str] = "PORTFOLIO_MANAGER.PRICE.MARK_MISSING"
WARN_GREEK_HINT_MISSING: Final[str] = "PORTFOLIO_MANAGER.GREEK.HINT_MISSING"
WARN_GREEK_HINT_STALE: Final[str] = "PORTFOLIO_MANAGER.GREEK.HINT_STALE"
WARN_MARGIN_HINT_MISSING: Final[str] = "PORTFOLIO_MANAGER.MARGIN.HINT_MISSING"
WARN_MARGIN_HINT_STALE: Final[str] = "PORTFOLIO_MANAGER.MARGIN.HINT_STALE"
WARN_EXPIRY_UNRESOLVED: Final[str] = "PORTFOLIO_MANAGER.EXPIRY.UNRESOLVED"

_logger = logging.getLogger("portfolio.portfolio_manager")


class PortfolioManagerError(Exception):
    """Base portfolio manager exception."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


class PortfolioManagerConfigurationError(PortfolioManagerError):
    """Raised when portfolio manager configuration is invalid."""


class PortfolioManagerValidationError(PortfolioManagerError):
    """Raised when input or output validation fails."""


class PortfolioManagerContextError(PortfolioManagerError):
    """Raised when ingest context is invalid."""


class PortfolioManagerIngestError(PortfolioManagerError):
    """Raised when ingest pipeline fails irrecoverably."""


class PortfolioUpdateStatus(str, Enum):
    """Overall status of a portfolio ingest run."""

    APPLIED = "applied"
    NOOP = "noop"
    REJECTED = "rejected"
    PARTIAL = "partial"
    FAILED = "failed"


class PortfolioEventType(str, Enum):
    """Portfolio lifecycle event discriminator with associated topic."""

    INGEST_RECEIVED = "ingest_received"
    INGEST_REJECTED = "ingest_rejected"
    PORTFOLIO_UPDATED = "portfolio_updated"
    PNL_UPDATED = "pnl_updated"
    EXPOSURE_UPDATED = "exposure_updated"
    GREEKS_UPDATED = "greeks_updated"
    UTILIZATION_UPDATED = "utilization_updated"
    AGGREGATION_UPDATED = "aggregation_updated"
    SNAPSHOT_PUBLISHED = "snapshot_published"
    INGEST_COMPLETED = "ingest_completed"
    PORTFOLIO_ERROR = "portfolio_error"

    @property
    def topic(self) -> str:
        """Return hierarchical event bus topic for this event type."""
        mapping = {
            PortfolioEventType.INGEST_RECEIVED: "portfolio.ingest.received",
            PortfolioEventType.INGEST_REJECTED: "portfolio.ingest.rejected",
            PortfolioEventType.PORTFOLIO_UPDATED: "portfolio.updated",
            PortfolioEventType.PNL_UPDATED: "portfolio.pnl.updated",
            PortfolioEventType.EXPOSURE_UPDATED: "portfolio.exposure.updated",
            PortfolioEventType.GREEKS_UPDATED: "portfolio.greeks.updated",
            PortfolioEventType.UTILIZATION_UPDATED: "portfolio.utilization.updated",
            PortfolioEventType.AGGREGATION_UPDATED: "portfolio.aggregation.updated",
            PortfolioEventType.SNAPSHOT_PUBLISHED: "portfolio.snapshot.published",
            PortfolioEventType.INGEST_COMPLETED: "portfolio.ingest.completed",
            PortfolioEventType.PORTFOLIO_ERROR: "portfolio.error",
        }
        return mapping[self]


class PortfolioIngestStageId(str, Enum):
    """Ordered ingest pipeline stage identifiers."""

    INPUT_GATE = "input_gate"
    SNAPSHOT_INTEGRITY = "snapshot_integrity"
    POSITION_MAPPING = "position_mapping"
    PNL_ROLLUP = "pnl_rollup"
    GREEKS_AGGREGATION = "greeks_aggregation"
    EXPOSURE_CALCULATION = "exposure_calculation"
    UTILIZATION_CALCULATION = "utilization_calculation"
    MULTI_DIM_AGGREGATION = "multi_dim_aggregation"
    SNAPSHOT_ASSEMBLY = "snapshot_assembly"
    RESULT_ASSEMBLY = "result_assembly"
    OUTPUT_VALIDATION = "output_validation"


STAGE_ORDER: Final[tuple[PortfolioIngestStageId, ...]] = (
    PortfolioIngestStageId.INPUT_GATE,
    PortfolioIngestStageId.SNAPSHOT_INTEGRITY,
    PortfolioIngestStageId.POSITION_MAPPING,
    PortfolioIngestStageId.PNL_ROLLUP,
    PortfolioIngestStageId.GREEKS_AGGREGATION,
    PortfolioIngestStageId.EXPOSURE_CALCULATION,
    PortfolioIngestStageId.UTILIZATION_CALCULATION,
    PortfolioIngestStageId.MULTI_DIM_AGGREGATION,
    PortfolioIngestStageId.SNAPSHOT_ASSEMBLY,
    PortfolioIngestStageId.RESULT_ASSEMBLY,
    PortfolioIngestStageId.OUTPUT_VALIDATION,
)


@dataclass(frozen=True)
class PortfolioManagerConfig:
    """Configuration for portfolio manager behavior."""

    strict_correlation: bool = True
    strict_output_validation: bool = True
    deterministic_fingerprint: bool = True
    publish_lifecycle_events: bool = True
    idempotent_ingest: bool = True
    require_account_hints: bool = True
    require_greek_hints: bool = False
    track_peak_equity: bool = True
    session_pnl_tracking: bool = True
    margin_hint_max_age_seconds: int = DEFAULT_MARGIN_HINT_MAX_AGE_SECONDS
    greek_hint_max_age_seconds: int = DEFAULT_GREEK_HINT_MAX_AGE_SECONDS
    max_open_positions: int | None = None
    expiry_bucket_format: str = "%Y-%m-%d"
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.margin_hint_max_age_seconds < 0:
            raise PortfolioManagerConfigurationError(
                "margin_hint_max_age_seconds must be non-negative.",
                code=ERROR_CONFIG_INVALID,
                field="margin_hint_max_age_seconds",
            )
        if self.greek_hint_max_age_seconds < 0:
            raise PortfolioManagerConfigurationError(
                "greek_hint_max_age_seconds must be non-negative.",
                code=ERROR_CONFIG_INVALID,
                field="greek_hint_max_age_seconds",
            )


@dataclass(frozen=True)
class PositionGreekHint:
    """Orchestrator-supplied Greek hint for one position."""

    position_id: str
    as_of: datetime
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    source: str = "greeks_engine"


@dataclass(frozen=True)
class PortfolioIngestContext:
    """Immutable per-run inputs for portfolio ingest."""

    correlation_id: str
    reference_time: datetime
    execution_mode: StrategyExecutionMode
    account_id: str
    equity_hint: float
    cash_available_hint: float
    margin_used_hint: float = 0.0
    margin_available_hint: float | None = None
    peak_equity_hint: float | None = None
    greek_hints: Mapping[str, PositionGreekHint] = field(
        default_factory=lambda: MappingProxyType({})
    )
    price_hints: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    margin_hint_as_of: datetime | None = None


@dataclass(frozen=True)
class PortfolioPositionSummary:
    """Mapped summary of one open position for portfolio rollups."""

    position_id: str
    instrument_key: str
    underlying: str
    expiry: str | None
    strategy_id: str
    strategy_family: StrategyFamily
    side: str
    quantity: int
    notional_exposure: float
    unrealized_pnl: float
    realized_pnl_session: float
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    opened_at: datetime | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class PortfolioAggregationBucket:
    """Rollup container for strategy, underlying, or expiry dimension."""

    bucket_key: str
    open_position_count: int
    gross_notional: float
    net_notional: float
    unrealized_pnl: float
    realized_pnl_session: float
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    weight_pct: float = 0.0


@dataclass(frozen=True)
class PortfolioMetrics:
    """P&L, utilization, and Greek totals for the account."""

    total_realized_pnl_session: float
    total_unrealized_pnl: float
    total_daily_pnl: float
    equity_hint: float
    cash_available_hint: float
    capital_deployed: float
    capital_utilization_pct: float
    margin_used_hint: float
    margin_available_hint: float | None
    margin_utilization_pct: float | None
    portfolio_delta: float | None
    portfolio_gamma: float | None
    portfolio_theta: float | None
    portfolio_vega: float | None
    open_position_count: int
    peak_equity_hint: float | None
    metrics_fingerprint: str


@dataclass(frozen=True)
class PortfolioExposure:
    """Notional exposure breakdowns and concentration weights."""

    gross_notional: float
    net_notional: float
    gross_notional_by_underlying: Mapping[str, float]
    net_notional_by_underlying: Mapping[str, float]
    exposure_by_strategy_id: Mapping[str, float]
    exposure_by_strategy_family: Mapping[str, float]
    exposure_by_expiry: Mapping[str, float]
    largest_underlying_weight_pct: float
    largest_strategy_weight_pct: float
    open_position_count: int
    open_position_count_by_underlying: Mapping[str, int]
    exposure_fingerprint: str


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Immutable account-level portfolio state bundle."""

    snapshot_id: str
    correlation_id: str
    as_of: datetime
    account_id: str
    metrics: PortfolioMetrics
    exposure: PortfolioExposure
    positions: tuple[PortfolioPositionSummary, ...]
    by_strategy: Mapping[str, PortfolioAggregationBucket]
    by_underlying: Mapping[str, PortfolioAggregationBucket]
    by_expiry: Mapping[str, PortfolioAggregationBucket]
    snapshot_fingerprint: str


@dataclass(frozen=True)
class PortfolioWarningRecord:
    """Non-fatal warning emitted during portfolio ingest."""

    code: str
    message: str
    stage_id: PortfolioIngestStageId | None = None
    field: str | None = None
    position_id: str | None = None


@dataclass(frozen=True)
class PortfolioErrorRecord:
    """Structured error emitted during portfolio ingest."""

    code: str
    message: str
    stage_id: PortfolioIngestStageId | None = None
    field: str | None = None
    position_id: str | None = None


@dataclass(frozen=True)
class PortfolioValidationResult:
    """Validation outcome for context or result checks."""

    errors: tuple[PortfolioErrorRecord, ...] = ()
    warnings: tuple[PortfolioWarningRecord, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return True when no errors are present."""
        return not self.errors


@dataclass(frozen=True)
class PortfolioStageResult:
    """Audit record for one pipeline stage."""

    stage_id: PortfolioIngestStageId
    passed: bool
    rejection_code: str | None
    message: str | None
    duration_ms: float
    details: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class PortfolioPipelineResult:
    """Pipeline stage audit summary."""

    total_stages: int
    passed_stages: int
    failed_stage_id: PortfolioIngestStageId | None
    stages: tuple[PortfolioStageResult, ...]
    short_circuited: bool


@dataclass(frozen=True)
class PortfolioUpdateResult:
    """Immutable sealed portfolio ingest outcome."""

    update_id: str
    source_position_snapshot_id: str | None
    correlation_id: str
    status: PortfolioUpdateStatus
    snapshot: PortfolioSnapshot
    metrics: PortfolioMetrics
    exposure: PortfolioExposure
    pipeline_summary: PortfolioPipelineResult
    warnings: tuple[PortfolioWarningRecord, ...]
    errors: tuple[PortfolioErrorRecord, ...]
    primary_error_code: str | None
    submitted_at: datetime
    completed_at: datetime | None
    duration_ms: float
    update_fingerprint: str


@dataclass(frozen=True)
class PortfolioEvent:
    """Structured portfolio lifecycle event payload."""

    event_type: PortfolioEventType
    topic: str
    update_id: str
    correlation_id: str
    occurred_at: datetime
    snapshot_id: str | None = None
    snapshot: PortfolioSnapshot | None = None
    producer: str = PRODUCER_NAME
    producer_version: str = PORTFOLIO_MANAGER_VERSION
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class _StageOutcome:
    """Internal stage handler outcome."""

    passed: bool
    rejection_code: str | None = None
    message: str | None = None
    details: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass
class _PipelineRunState:
    """Mutable per-run pipeline state."""

    snapshot: PositionSnapshot
    context: PortfolioIngestContext
    config: PortfolioManagerConfig
    update_id: str
    started_at: datetime
    source_position_update_correlation_id: str | None = None
    summaries: tuple[PortfolioPositionSummary, ...] = ()
    metrics: PortfolioMetrics | None = None
    exposure: PortfolioExposure | None = None
    portfolio_snapshot: PortfolioSnapshot | None = None
    by_strategy: dict[str, PortfolioAggregationBucket] = field(default_factory=dict)
    by_underlying: dict[str, PortfolioAggregationBucket] = field(default_factory=dict)
    by_expiry: dict[str, PortfolioAggregationBucket] = field(default_factory=dict)
    warnings: list[PortfolioWarningRecord] = field(default_factory=list)
    errors: list[PortfolioErrorRecord] = field(default_factory=list)
    primary_error_code: str | None = None
    pre_ingest_rejected: bool = False
    idempotent_noop: bool = False
    status: PortfolioUpdateStatus = PortfolioUpdateStatus.APPLIED
    peak_equity: float = 0.0
    prior_daily_pnl: float | None = None
    prior_gross_notional: float | None = None
    prior_portfolio_delta: float | None = None
    prior_capital_utilization: float | None = None
    prior_open_count: int | None = None


def default_portfolio_manager_config() -> PortfolioManagerConfig:
    """Return production-default portfolio manager configuration."""
    return PortfolioManagerConfig(metadata=MappingProxyType({}))


def _utc_now() -> datetime:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


def _is_timezone_aware(value: datetime) -> bool:
    """Return whether datetime is timezone-aware."""
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _canonical_json(payload: Mapping[str, Any]) -> str:
    """Serialize mapping to canonical JSON."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _datetime_to_iso(value: datetime) -> str:
    """Serialize timezone-aware datetime to ISO-8601 UTC with Z suffix."""
    if not _is_timezone_aware(value):
        raise PortfolioManagerValidationError(
            "datetime must be timezone-aware.",
            code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
        )
    utc_value = value.astimezone(timezone.utc)
    return utc_value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _datetime_from_iso(value: str) -> datetime:
    """Deserialize ISO-8601 datetime."""
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if not _is_timezone_aware(parsed):
        raise PortfolioManagerValidationError(
            "deserialized datetime must be timezone-aware.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return parsed


def config_fingerprint(config: PortfolioManagerConfig) -> str:
    """Compute deterministic fingerprint for configuration."""
    payload = {
        "strict_correlation": config.strict_correlation,
        "strict_output_validation": config.strict_output_validation,
        "deterministic_fingerprint": config.deterministic_fingerprint,
        "publish_lifecycle_events": config.publish_lifecycle_events,
        "idempotent_ingest": config.idempotent_ingest,
        "require_account_hints": config.require_account_hints,
        "require_greek_hints": config.require_greek_hints,
        "track_peak_equity": config.track_peak_equity,
        "session_pnl_tracking": config.session_pnl_tracking,
        "margin_hint_max_age_seconds": config.margin_hint_max_age_seconds,
        "greek_hint_max_age_seconds": config.greek_hint_max_age_seconds,
        "max_open_positions": config.max_open_positions,
        "expiry_bucket_format": config.expiry_bucket_format,
        "metadata": dict(sorted(config.metadata.items())),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _normalize_underlying(value: str) -> str:
    """Normalize underlying symbol for aggregation."""
    return value.strip().upper()


def _derive_expiry(position: Position, config: PortfolioManagerConfig) -> str:
    """Derive expiry bucket key from position metadata."""
    raw = position.metadata.get("expiry")
    if raw:
        return raw
    instrument = position.metadata.get("instrument_expiry")
    if instrument:
        return instrument
    return "UNKNOWN"


def _parse_opened_at(position: Position) -> datetime | None:
    """Parse opened_at from position metadata."""
    raw = position.metadata.get("opened_at")
    if not raw:
        return None
    try:
        return _datetime_from_iso(raw)
    except (PortfolioManagerValidationError, ValueError):
        return None


def compute_notional_exposure(
    quantity: int,
    *,
    mark_price: float | None,
    average_entry_price: float,
) -> float:
    """Compute notional exposure for one position."""
    price = mark_price if mark_price is not None and mark_price > 0 else average_entry_price
    return round(quantity * price, NOTIONAL_ROUND_DECIMALS)


def map_position_to_summary(
    position: Position,
    *,
    mark_price: float | None,
    greek_hint: PositionGreekHint | None,
    config: PortfolioManagerConfig,
) -> PortfolioPositionSummary:
    """Map Position record to portfolio position summary."""
    underlying_raw = position.metadata.get("underlying")
    if underlying_raw:
        underlying = _normalize_underlying(underlying_raw)
    elif ":" in position.instrument_key:
        underlying = _normalize_underlying(position.instrument_key.split(":")[0])
    else:
        underlying = _normalize_underlying(position.instrument_key)

    expiry = _derive_expiry(position, config)
    notional = compute_notional_exposure(
        position.quantity,
        mark_price=mark_price,
        average_entry_price=position.average_entry_price,
    )
    summary = PortfolioPositionSummary(
        position_id=position.position_id,
        instrument_key=position.instrument_key,
        underlying=underlying,
        expiry=expiry,
        strategy_id=position.strategy_id,
        strategy_family=position.strategy_family,
        side=position.side.value,
        quantity=position.quantity,
        notional_exposure=notional,
        unrealized_pnl=round(position.unrealized_pnl, PNL_ROUND_DECIMALS),
        realized_pnl_session=round(position.realized_pnl, PNL_ROUND_DECIMALS),
        opened_at=_parse_opened_at(position),
        metadata=MappingProxyType(dict(position.metadata)),
    )
    if greek_hint is not None:
        summary = attach_greek_hints(summary, {position.position_id: greek_hint})
    return summary


def attach_greek_hints(
    summary: PortfolioPositionSummary,
    hints: Mapping[str, PositionGreekHint],
) -> PortfolioPositionSummary:
    """Attach Greek hints to position summary."""
    hint = hints.get(summary.position_id)
    if hint is None:
        return summary
    return replace(
        summary,
        delta=hint.delta,
        gamma=hint.gamma,
        theta=hint.theta,
        vega=hint.vega,
    )


def compute_total_unrealized_pnl(
    summaries: tuple[PortfolioPositionSummary, ...],
) -> float:
    """Sum position unrealized P&L."""
    total = sum(summary.unrealized_pnl for summary in summaries)
    return round(total, PNL_ROUND_DECIMALS)


def compute_total_realized_pnl_session(
    summaries: tuple[PortfolioPositionSummary, ...],
    *,
    seed: float = 0.0,
) -> float:
    """Sum session realized P&L from position summaries and optional seed."""
    total = seed + sum(summary.realized_pnl_session for summary in summaries)
    return round(total, PNL_ROUND_DECIMALS)


def compute_gross_notional(
    summaries: tuple[PortfolioPositionSummary, ...],
) -> float:
    """Sum absolute notional exposure."""
    return round(
        sum(abs(summary.notional_exposure) for summary in summaries),
        NOTIONAL_ROUND_DECIMALS,
    )


def compute_net_notional(
    summaries: tuple[PortfolioPositionSummary, ...],
) -> float:
    """Sum signed notional exposure."""
    total = 0.0
    for summary in summaries:
        sign = -1.0 if summary.side == PositionSide.SHORT.value else 1.0
        total += sign * summary.notional_exposure
    return round(total, NOTIONAL_ROUND_DECIMALS)


def aggregate_portfolio_greeks(
    summaries: tuple[PortfolioPositionSummary, ...],
) -> tuple[float | None, float | None, float | None, float | None]:
    """Sum per-position Greek hints into portfolio totals."""
    delta = gamma = theta = vega = 0.0
    any_delta = any_gamma = any_theta = any_vega = False
    for summary in summaries:
        if summary.delta is not None:
            delta += summary.delta
            any_delta = True
        if summary.gamma is not None:
            gamma += summary.gamma
            any_gamma = True
        if summary.theta is not None:
            theta += summary.theta
            any_theta = True
        if summary.vega is not None:
            vega += summary.vega
            any_vega = True
    return (
        round(delta, GREEK_ROUND_DECIMALS) if any_delta else None,
        round(gamma, GREEK_ROUND_DECIMALS) if any_gamma else None,
        round(theta, GREEK_ROUND_DECIMALS) if any_theta else None,
        round(vega, GREEK_ROUND_DECIMALS) if any_vega else None,
    )


def compute_capital_utilization_pct(
    capital_deployed: float,
    equity_hint: float,
) -> float:
    """Compute capital utilization percentage."""
    if equity_hint <= 0:
        return 0.0
    return round(
        (capital_deployed / equity_hint) * 100.0,
        UTILIZATION_ROUND_DECIMALS,
    )


def compute_margin_utilization_pct(
    margin_used_hint: float,
    margin_available_hint: float | None,
) -> float | None:
    """Compute margin utilization percentage."""
    if margin_available_hint is None:
        return None
    denominator = margin_used_hint + margin_available_hint
    if denominator <= 0:
        return None
    return round(
        (margin_used_hint / denominator) * 100.0,
        UTILIZATION_ROUND_DECIMALS,
    )


def compute_largest_weight_pct(
    bucket_notionals: Mapping[str, float],
    gross_total: float,
) -> float:
    """Return largest bucket weight as percentage of gross notional."""
    if gross_total <= 0:
        return 0.0
    largest = max(bucket_notionals.values(), default=0.0)
    return round((largest / gross_total) * 100.0, WEIGHT_ROUND_DECIMALS)


def validate_ingest_context(
    context: PortfolioIngestContext,
    snapshot: PositionSnapshot,
    config: PortfolioManagerConfig,
    *,
    position_update_correlation_id: str | None = None,
) -> PortfolioValidationResult:
    """Validate context and snapshot before portfolio mutation."""
    errors: list[PortfolioErrorRecord] = []
    warnings: list[PortfolioWarningRecord] = []

    if not _is_timezone_aware(context.reference_time):
        errors.append(
            PortfolioErrorRecord(
                code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
                message="reference_time must be timezone-aware.",
                field="reference_time",
            )
        )
    if config.strict_correlation:
        if not context.correlation_id:
            errors.append(
                PortfolioErrorRecord(
                    code=ERROR_CONTEXT_CORRELATION_MISMATCH,
                    message="correlation_id must be non-empty.",
                    field="correlation_id",
                )
            )
        if (
            position_update_correlation_id is not None
            and context.correlation_id != position_update_correlation_id
        ):
            errors.append(
                PortfolioErrorRecord(
                    code=ERROR_CONTEXT_CORRELATION_MISMATCH,
                    message="correlation_id mismatch with position update result.",
                    field="correlation_id",
                )
            )
    if config.require_account_hints and context.execution_mode is StrategyExecutionMode.LIVE:
        if not context.account_id:
            errors.append(
                PortfolioErrorRecord(
                    code=ERROR_CONTEXT_MISSING_ACCOUNT,
                    message="account_id required in LIVE mode.",
                    field="account_id",
                )
            )
        if context.equity_hint <= 0:
            errors.append(
                PortfolioErrorRecord(
                    code=ERROR_ACCOUNT_INVALID_EQUITY,
                    message="equity_hint must be positive in LIVE mode.",
                    field="equity_hint",
                )
            )
    if not isinstance(context.execution_mode, StrategyExecutionMode):
        errors.append(
            PortfolioErrorRecord(
                code=ERROR_CONTEXT_INVALID,
                message="execution_mode is invalid.",
                field="execution_mode",
            )
        )
    _ = snapshot
    return PortfolioValidationResult(errors=tuple(errors), warnings=tuple(warnings))


def compute_metrics_fingerprint(metrics: PortfolioMetrics) -> str:
    """Compute deterministic metrics fingerprint."""
    payload = {
        "total_daily_pnl": metrics.total_daily_pnl,
        "total_unrealized_pnl": metrics.total_unrealized_pnl,
        "capital_utilization_pct": metrics.capital_utilization_pct,
        "portfolio_delta": metrics.portfolio_delta,
        "open_position_count": metrics.open_position_count,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_exposure_fingerprint(exposure: PortfolioExposure) -> str:
    """Compute deterministic exposure fingerprint."""
    payload = {
        "gross_notional": exposure.gross_notional,
        "net_notional": exposure.net_notional,
        "largest_underlying_weight_pct": exposure.largest_underlying_weight_pct,
        "open_position_count": exposure.open_position_count,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_snapshot_fingerprint(snapshot: PortfolioSnapshot) -> str:
    """Compute deterministic portfolio snapshot fingerprint."""
    payload = {
        "correlation_id": snapshot.correlation_id,
        "account_id": snapshot.account_id,
        "as_of": _datetime_to_iso(snapshot.as_of),
        "metrics_fingerprint": snapshot.metrics.metrics_fingerprint,
        "exposure_fingerprint": snapshot.exposure.exposure_fingerprint,
        "position_count": len(snapshot.positions),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_update_fingerprint(
    position_snapshot: PositionSnapshot,
    portfolio_snapshot: PortfolioSnapshot,
    config: PortfolioManagerConfig,
) -> str:
    """Compute SHA-256 update fingerprint for replay verification."""
    payload = {
        "position_snapshot_fingerprint": position_snapshot.snapshot_fingerprint,
        "portfolio_outcomes": {
            "snapshot_id": portfolio_snapshot.snapshot_id,
            "open_position_count": portfolio_snapshot.metrics.open_position_count,
            "gross_notional": portfolio_snapshot.exposure.gross_notional,
            "total_daily_pnl": portfolio_snapshot.metrics.total_daily_pnl,
            "portfolio_delta": portfolio_snapshot.metrics.portfolio_delta,
            "capital_utilization_pct": portfolio_snapshot.metrics.capital_utilization_pct,
        },
        "config_hash": config_fingerprint(config),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def validate_portfolio_update_result(
    result: PortfolioUpdateResult,
) -> PortfolioValidationResult:
    """Validate sealed ingest result."""
    errors: list[PortfolioErrorRecord] = []
    if not result.update_id:
        errors.append(
            PortfolioErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="update_id must be non-empty.",
                field="update_id",
            )
        )
    if result.metrics.open_position_count != result.exposure.open_position_count:
        errors.append(
            PortfolioErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="open_position_count mismatch between metrics and exposure.",
            )
        )
    if len(result.snapshot.positions) != result.metrics.open_position_count:
        errors.append(
            PortfolioErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="positions length mismatch with open_position_count.",
            )
        )
    return PortfolioValidationResult(errors=tuple(errors))


def assert_valid_portfolio_update_result(result: PortfolioUpdateResult) -> None:
    """Raise PortfolioManagerValidationError when result is invalid."""
    validation = validate_portfolio_update_result(result)
    if not validation.is_valid:
        primary = validation.errors[0]
        raise PortfolioManagerValidationError(
            primary.message,
            code=primary.code,
            field=primary.field,
        )


def _aggregation_bucket_to_dict(bucket: PortfolioAggregationBucket) -> dict[str, Any]:
    """Serialize aggregation bucket."""
    return {
        "bucket_key": bucket.bucket_key,
        "open_position_count": bucket.open_position_count,
        "gross_notional": bucket.gross_notional,
        "net_notional": bucket.net_notional,
        "unrealized_pnl": bucket.unrealized_pnl,
        "realized_pnl_session": bucket.realized_pnl_session,
        "delta": bucket.delta,
        "gamma": bucket.gamma,
        "theta": bucket.theta,
        "vega": bucket.vega,
        "weight_pct": bucket.weight_pct,
    }


def _aggregation_bucket_from_dict(data: Mapping[str, Any]) -> PortfolioAggregationBucket:
    """Deserialize aggregation bucket."""
    return PortfolioAggregationBucket(
        bucket_key=str(data["bucket_key"]),
        open_position_count=int(data["open_position_count"]),
        gross_notional=float(data["gross_notional"]),
        net_notional=float(data["net_notional"]),
        unrealized_pnl=float(data["unrealized_pnl"]),
        realized_pnl_session=float(data["realized_pnl_session"]),
        delta=float(data["delta"]) if data.get("delta") is not None else None,
        gamma=float(data["gamma"]) if data.get("gamma") is not None else None,
        theta=float(data["theta"]) if data.get("theta") is not None else None,
        vega=float(data["vega"]) if data.get("vega") is not None else None,
        weight_pct=float(data.get("weight_pct", 0.0)),
    )


def _summary_to_dict(summary: PortfolioPositionSummary) -> dict[str, Any]:
    """Serialize position summary."""
    return {
        "position_id": summary.position_id,
        "instrument_key": summary.instrument_key,
        "underlying": summary.underlying,
        "expiry": summary.expiry,
        "strategy_id": summary.strategy_id,
        "strategy_family": summary.strategy_family.value,
        "side": summary.side,
        "quantity": summary.quantity,
        "notional_exposure": summary.notional_exposure,
        "unrealized_pnl": summary.unrealized_pnl,
        "realized_pnl_session": summary.realized_pnl_session,
        "delta": summary.delta,
        "gamma": summary.gamma,
        "theta": summary.theta,
        "vega": summary.vega,
        "opened_at": _datetime_to_iso(summary.opened_at) if summary.opened_at else None,
        "metadata": dict(sorted(summary.metadata.items())),
    }


def _summary_from_dict(data: Mapping[str, Any]) -> PortfolioPositionSummary:
    """Deserialize position summary."""
    opened_raw = data.get("opened_at")
    return PortfolioPositionSummary(
        position_id=str(data["position_id"]),
        instrument_key=str(data["instrument_key"]),
        underlying=str(data["underlying"]),
        expiry=str(data["expiry"]) if data.get("expiry") else None,
        strategy_id=str(data["strategy_id"]),
        strategy_family=StrategyFamily(str(data["strategy_family"])),
        side=str(data["side"]),
        quantity=int(data["quantity"]),
        notional_exposure=float(data["notional_exposure"]),
        unrealized_pnl=float(data["unrealized_pnl"]),
        realized_pnl_session=float(data["realized_pnl_session"]),
        delta=float(data["delta"]) if data.get("delta") is not None else None,
        gamma=float(data["gamma"]) if data.get("gamma") is not None else None,
        theta=float(data["theta"]) if data.get("theta") is not None else None,
        vega=float(data["vega"]) if data.get("vega") is not None else None,
        opened_at=_datetime_from_iso(opened_raw) if opened_raw else None,
        metadata=MappingProxyType(dict(data.get("metadata", {}))),
    )


def _metrics_to_dict(metrics: PortfolioMetrics) -> dict[str, Any]:
    """Serialize portfolio metrics."""
    return {
        "total_realized_pnl_session": metrics.total_realized_pnl_session,
        "total_unrealized_pnl": metrics.total_unrealized_pnl,
        "total_daily_pnl": metrics.total_daily_pnl,
        "equity_hint": metrics.equity_hint,
        "cash_available_hint": metrics.cash_available_hint,
        "capital_deployed": metrics.capital_deployed,
        "capital_utilization_pct": metrics.capital_utilization_pct,
        "margin_used_hint": metrics.margin_used_hint,
        "margin_available_hint": metrics.margin_available_hint,
        "margin_utilization_pct": metrics.margin_utilization_pct,
        "portfolio_delta": metrics.portfolio_delta,
        "portfolio_gamma": metrics.portfolio_gamma,
        "portfolio_theta": metrics.portfolio_theta,
        "portfolio_vega": metrics.portfolio_vega,
        "open_position_count": metrics.open_position_count,
        "peak_equity_hint": metrics.peak_equity_hint,
        "metrics_fingerprint": metrics.metrics_fingerprint,
    }


def _metrics_from_dict(data: Mapping[str, Any]) -> PortfolioMetrics:
    """Deserialize portfolio metrics."""
    return PortfolioMetrics(
        total_realized_pnl_session=float(data["total_realized_pnl_session"]),
        total_unrealized_pnl=float(data["total_unrealized_pnl"]),
        total_daily_pnl=float(data["total_daily_pnl"]),
        equity_hint=float(data["equity_hint"]),
        cash_available_hint=float(data["cash_available_hint"]),
        capital_deployed=float(data["capital_deployed"]),
        capital_utilization_pct=float(data["capital_utilization_pct"]),
        margin_used_hint=float(data["margin_used_hint"]),
        margin_available_hint=(
            float(data["margin_available_hint"])
            if data.get("margin_available_hint") is not None
            else None
        ),
        margin_utilization_pct=(
            float(data["margin_utilization_pct"])
            if data.get("margin_utilization_pct") is not None
            else None
        ),
        portfolio_delta=float(data["portfolio_delta"]) if data.get("portfolio_delta") is not None else None,
        portfolio_gamma=float(data["portfolio_gamma"]) if data.get("portfolio_gamma") is not None else None,
        portfolio_theta=float(data["portfolio_theta"]) if data.get("portfolio_theta") is not None else None,
        portfolio_vega=float(data["portfolio_vega"]) if data.get("portfolio_vega") is not None else None,
        open_position_count=int(data["open_position_count"]),
        peak_equity_hint=(
            float(data["peak_equity_hint"]) if data.get("peak_equity_hint") is not None else None
        ),
        metrics_fingerprint=str(data["metrics_fingerprint"]),
    )


def _exposure_to_dict(exposure: PortfolioExposure) -> dict[str, Any]:
    """Serialize portfolio exposure."""
    return {
        "gross_notional": exposure.gross_notional,
        "net_notional": exposure.net_notional,
        "gross_notional_by_underlying": dict(sorted(exposure.gross_notional_by_underlying.items())),
        "net_notional_by_underlying": dict(sorted(exposure.net_notional_by_underlying.items())),
        "exposure_by_strategy_id": dict(sorted(exposure.exposure_by_strategy_id.items())),
        "exposure_by_strategy_family": dict(sorted(exposure.exposure_by_strategy_family.items())),
        "exposure_by_expiry": dict(sorted(exposure.exposure_by_expiry.items())),
        "largest_underlying_weight_pct": exposure.largest_underlying_weight_pct,
        "largest_strategy_weight_pct": exposure.largest_strategy_weight_pct,
        "open_position_count": exposure.open_position_count,
        "open_position_count_by_underlying": dict(
            sorted(exposure.open_position_count_by_underlying.items())
        ),
        "exposure_fingerprint": exposure.exposure_fingerprint,
    }


def _exposure_from_dict(data: Mapping[str, Any]) -> PortfolioExposure:
    """Deserialize portfolio exposure."""
    return PortfolioExposure(
        gross_notional=float(data["gross_notional"]),
        net_notional=float(data["net_notional"]),
        gross_notional_by_underlying=MappingProxyType(
            dict(data.get("gross_notional_by_underlying", {}))
        ),
        net_notional_by_underlying=MappingProxyType(dict(data.get("net_notional_by_underlying", {}))),
        exposure_by_strategy_id=MappingProxyType(dict(data.get("exposure_by_strategy_id", {}))),
        exposure_by_strategy_family=MappingProxyType(
            dict(data.get("exposure_by_strategy_family", {}))
        ),
        exposure_by_expiry=MappingProxyType(dict(data.get("exposure_by_expiry", {}))),
        largest_underlying_weight_pct=float(data["largest_underlying_weight_pct"]),
        largest_strategy_weight_pct=float(data["largest_strategy_weight_pct"]),
        open_position_count=int(data["open_position_count"]),
        open_position_count_by_underlying=MappingProxyType(
            dict(data.get("open_position_count_by_underlying", {}))
        ),
        exposure_fingerprint=str(data["exposure_fingerprint"]),
    )


def portfolio_snapshot_to_dict(snapshot: PortfolioSnapshot) -> dict[str, Any]:
    """Convert portfolio snapshot to serializable dictionary."""
    return {
        "schema_version": PORTFOLIO_SCHEMA_VERSION,
        "snapshot_id": snapshot.snapshot_id,
        "correlation_id": snapshot.correlation_id,
        "as_of": _datetime_to_iso(snapshot.as_of),
        "account_id": snapshot.account_id,
        "metrics": _metrics_to_dict(snapshot.metrics),
        "exposure": _exposure_to_dict(snapshot.exposure),
        "positions": [_summary_to_dict(item) for item in snapshot.positions],
        "by_strategy": {
            key: _aggregation_bucket_to_dict(value)
            for key, value in sorted(snapshot.by_strategy.items())
        },
        "by_underlying": {
            key: _aggregation_bucket_to_dict(value)
            for key, value in sorted(snapshot.by_underlying.items())
        },
        "by_expiry": {
            key: _aggregation_bucket_to_dict(value)
            for key, value in sorted(snapshot.by_expiry.items())
        },
        "snapshot_fingerprint": snapshot.snapshot_fingerprint,
    }


def portfolio_snapshot_from_dict(data: Mapping[str, Any]) -> PortfolioSnapshot:
    """Deserialize portfolio snapshot from dictionary."""
    schema = data.get("schema_version")
    if schema != PORTFOLIO_SCHEMA_VERSION:
        raise PortfolioManagerValidationError(
            f"Unsupported schema version: {schema}",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
        )
    return PortfolioSnapshot(
        snapshot_id=str(data["snapshot_id"]),
        correlation_id=str(data["correlation_id"]),
        as_of=_datetime_from_iso(str(data["as_of"])),
        account_id=str(data["account_id"]),
        metrics=_metrics_from_dict(data["metrics"]),
        exposure=_exposure_from_dict(data["exposure"]),
        positions=tuple(_summary_from_dict(item) for item in data["positions"]),
        by_strategy=MappingProxyType(
            {
                key: _aggregation_bucket_from_dict(value)
                for key, value in data.get("by_strategy", {}).items()
            }
        ),
        by_underlying=MappingProxyType(
            {
                key: _aggregation_bucket_from_dict(value)
                for key, value in data.get("by_underlying", {}).items()
            }
        ),
        by_expiry=MappingProxyType(
            {
                key: _aggregation_bucket_from_dict(value)
                for key, value in data.get("by_expiry", {}).items()
            }
        ),
        snapshot_fingerprint=str(data["snapshot_fingerprint"]),
    )


def portfolio_update_result_to_dict(result: PortfolioUpdateResult) -> dict[str, Any]:
    """Convert update result to serializable dictionary."""
    return {
        "schema_version": PORTFOLIO_SCHEMA_VERSION,
        "update_id": result.update_id,
        "source_position_snapshot_id": result.source_position_snapshot_id,
        "correlation_id": result.correlation_id,
        "status": result.status.value,
        "snapshot": portfolio_snapshot_to_dict(result.snapshot),
        "metrics": _metrics_to_dict(result.metrics),
        "exposure": _exposure_to_dict(result.exposure),
        "pipeline_summary": {
            "total_stages": result.pipeline_summary.total_stages,
            "passed_stages": result.pipeline_summary.passed_stages,
            "failed_stage_id": (
                result.pipeline_summary.failed_stage_id.value
                if result.pipeline_summary.failed_stage_id
                else None
            ),
            "stages": [
                {
                    "stage_id": stage.stage_id.value,
                    "passed": stage.passed,
                    "rejection_code": stage.rejection_code,
                    "message": stage.message,
                    "duration_ms": stage.duration_ms,
                }
                for stage in result.pipeline_summary.stages
            ],
            "short_circuited": result.pipeline_summary.short_circuited,
        },
        "warnings": [
            {
                "code": warning.code,
                "message": warning.message,
                "stage_id": warning.stage_id.value if warning.stage_id else None,
                "field": warning.field,
                "position_id": warning.position_id,
            }
            for warning in result.warnings
        ],
        "errors": [
            {
                "code": error.code,
                "message": error.message,
                "stage_id": error.stage_id.value if error.stage_id else None,
                "field": error.field,
                "position_id": error.position_id,
            }
            for error in result.errors
        ],
        "primary_error_code": result.primary_error_code,
        "submitted_at": _datetime_to_iso(result.submitted_at),
        "completed_at": _datetime_to_iso(result.completed_at) if result.completed_at else None,
        "duration_ms": result.duration_ms,
        "update_fingerprint": result.update_fingerprint,
    }


def portfolio_update_result_from_dict(data: Mapping[str, Any]) -> PortfolioUpdateResult:
    """Deserialize update result from dictionary."""
    schema = data.get("schema_version")
    if schema != PORTFOLIO_SCHEMA_VERSION:
        raise PortfolioManagerValidationError(
            f"Unsupported schema version: {schema}",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
        )
    pipeline_data = data["pipeline_summary"]
    stages = tuple(
        PortfolioStageResult(
            stage_id=PortfolioIngestStageId(item["stage_id"]),
            passed=bool(item["passed"]),
            rejection_code=str(item["rejection_code"]) if item.get("rejection_code") else None,
            message=str(item["message"]) if item.get("message") else None,
            duration_ms=float(item["duration_ms"]),
        )
        for item in pipeline_data["stages"]
    )
    pipeline = PortfolioPipelineResult(
        total_stages=int(pipeline_data["total_stages"]),
        passed_stages=int(pipeline_data["passed_stages"]),
        failed_stage_id=(
            PortfolioIngestStageId(pipeline_data["failed_stage_id"])
            if pipeline_data.get("failed_stage_id")
            else None
        ),
        stages=stages,
        short_circuited=bool(pipeline_data["short_circuited"]),
    )
    completed_raw = data.get("completed_at")
    snapshot = portfolio_snapshot_from_dict(data["snapshot"])
    return PortfolioUpdateResult(
        update_id=str(data["update_id"]),
        source_position_snapshot_id=(
            str(data["source_position_snapshot_id"])
            if data.get("source_position_snapshot_id")
            else None
        ),
        correlation_id=str(data["correlation_id"]),
        status=PortfolioUpdateStatus(str(data["status"])),
        snapshot=snapshot,
        metrics=_metrics_from_dict(data["metrics"]),
        exposure=_exposure_from_dict(data["exposure"]),
        pipeline_summary=pipeline,
        warnings=tuple(
            PortfolioWarningRecord(
                code=str(item["code"]),
                message=str(item["message"]),
                stage_id=(
                    PortfolioIngestStageId(item["stage_id"]) if item.get("stage_id") else None
                ),
                field=str(item["field"]) if item.get("field") else None,
                position_id=str(item["position_id"]) if item.get("position_id") else None,
            )
            for item in data.get("warnings", [])
        ),
        errors=tuple(
            PortfolioErrorRecord(
                code=str(item["code"]),
                message=str(item["message"]),
                stage_id=(
                    PortfolioIngestStageId(item["stage_id"]) if item.get("stage_id") else None
                ),
                field=str(item["field"]) if item.get("field") else None,
                position_id=str(item["position_id"]) if item.get("position_id") else None,
            )
            for item in data.get("errors", [])
        ),
        primary_error_code=str(data["primary_error_code"]) if data.get("primary_error_code") else None,
        submitted_at=_datetime_from_iso(str(data["submitted_at"])),
        completed_at=_datetime_from_iso(completed_raw) if completed_raw else None,
        duration_ms=float(data["duration_ms"]),
        update_fingerprint=str(data["update_fingerprint"]),
    )


def serialize_portfolio_update_result(result: PortfolioUpdateResult) -> str:
    """Serialize update result to canonical JSON."""
    return _canonical_json(portfolio_update_result_to_dict(result))


def deserialize_portfolio_update_result(payload: str) -> PortfolioUpdateResult:
    """Deserialize update result from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PortfolioManagerValidationError(
            "Malformed JSON payload.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(data, dict):
        raise PortfolioManagerValidationError(
            "JSON payload must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return portfolio_update_result_from_dict(data)


def serialize_portfolio_snapshot(snapshot: PortfolioSnapshot) -> str:
    """Serialize portfolio snapshot to canonical JSON."""
    return _canonical_json(portfolio_snapshot_to_dict(snapshot))


def deserialize_portfolio_snapshot(payload: str) -> PortfolioSnapshot:
    """Deserialize portfolio snapshot from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PortfolioManagerValidationError(
            "Malformed JSON payload.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(data, dict):
        raise PortfolioManagerValidationError(
            "JSON payload must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return portfolio_snapshot_from_dict(data)


class _EventPublisher:
    """Lifecycle event publisher with graceful no-op when bus absent."""

    def __init__(
        self,
        event_bus: EventBus | None,
        *,
        enabled: bool,
        update_id: str,
        correlation_id: str,
    ) -> None:
        self._event_bus = event_bus
        self._enabled = enabled and event_bus is not None
        self._update_id = update_id
        self._correlation_id = correlation_id
        self._pending: list[PortfolioEvent] = []

    def publish(
        self,
        event_type: PortfolioEventType,
        *,
        occurred_at: datetime,
        snapshot: PortfolioSnapshot | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Queue lifecycle event for ordered dispatch."""
        event = PortfolioEvent(
            event_type=event_type,
            topic=event_type.topic,
            update_id=self._update_id,
            correlation_id=self._correlation_id,
            occurred_at=occurred_at,
            snapshot_id=snapshot.snapshot_id if snapshot else None,
            snapshot=snapshot,
            metadata=MappingProxyType(dict(metadata or {})),
        )
        self._pending.append(event)

    def flush(self) -> tuple[PortfolioEvent, ...]:
        """Publish queued events in order."""
        if not self._enabled or self._event_bus is None:
            self._pending.clear()
            return ()
        published: list[PortfolioEvent] = []
        for event in self._pending:
            envelope = EventEnvelope(
                event_id=str(uuid.uuid4()),
                topic=event.topic,
                payload=event,
                correlation_id=self._correlation_id,
                producer=PRODUCER_NAME,
                occurred_at=event.occurred_at,
                published_at=_utc_now(),
                producer_version=PORTFOLIO_MANAGER_VERSION,
                payload_type="PortfolioEvent",
            )
            self._event_bus.publish(envelope)
            published.append(event)
        self._pending.clear()
        return tuple(published)


def _build_bucket(
    summaries: tuple[PortfolioPositionSummary, ...],
    key_fn: Any,
    gross_total: float,
) -> dict[str, PortfolioAggregationBucket]:
    """Build aggregation buckets for one dimension."""
    groups: dict[str, list[PortfolioPositionSummary]] = {}
    for summary in summaries:
        key = key_fn(summary)
        groups.setdefault(key, []).append(summary)

    buckets: dict[str, PortfolioAggregationBucket] = {}
    for key, items in groups.items():
        group_tuple = tuple(items)
        gross = compute_gross_notional(group_tuple)
        net = compute_net_notional(group_tuple)
        delta, gamma, theta, vega = aggregate_portfolio_greeks(group_tuple)
        buckets[key] = PortfolioAggregationBucket(
            bucket_key=key,
            open_position_count=len(items),
            gross_notional=gross,
            net_notional=net,
            unrealized_pnl=compute_total_unrealized_pnl(group_tuple),
            realized_pnl_session=compute_total_realized_pnl_session(group_tuple),
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            weight_pct=compute_largest_weight_pct({key: gross}, gross_total) if gross_total > 0 else 0.0,
        )
        if gross_total > 0:
            buckets[key] = replace(
                buckets[key],
                weight_pct=round((gross / gross_total) * 100.0, WEIGHT_ROUND_DECIMALS),
            )
    return buckets


def _material_change(
    prior: float | None,
    current: float,
    *,
    epsilon: float = MATERIAL_CHANGE_EPSILON,
) -> bool:
    """Return True when numeric value changed beyond epsilon."""
    if prior is None:
        return True
    return abs(prior - current) > epsilon


class PortfolioIngestPipeline:
    """Stateless multi-stage portfolio ingest pipeline."""

    def execute(
        self,
        run_state: _PipelineRunState,
        config: PortfolioManagerConfig,
        *,
        event_bus: EventBus | None = None,
        applied_snapshots: set[str] | None = None,
    ) -> PortfolioUpdateResult:
        """Execute full ingest pipeline."""
        publisher = _EventPublisher(
            event_bus,
            enabled=config.publish_lifecycle_events,
            update_id=run_state.update_id,
            correlation_id=run_state.context.correlation_id,
        )
        stages: list[PortfolioStageResult] = []
        short_circuit = False

        for stage_id in STAGE_ORDER:
            if short_circuit and stage_id not in (
                PortfolioIngestStageId.RESULT_ASSEMBLY,
                PortfolioIngestStageId.OUTPUT_VALIDATION,
            ):
                if run_state.idempotent_noop:
                    continue
            stage_started = time.perf_counter()
            outcome = self._run_stage(stage_id, run_state, applied_snapshots or set())
            duration_ms = (time.perf_counter() - stage_started) * 1000.0
            stages.append(
                PortfolioStageResult(
                    stage_id=stage_id,
                    passed=outcome.passed,
                    rejection_code=outcome.rejection_code,
                    message=outcome.message,
                    duration_ms=duration_ms,
                    details=outcome.details,
                )
            )
            if not outcome.passed and stage_id in (
                PortfolioIngestStageId.INPUT_GATE,
                PortfolioIngestStageId.SNAPSHOT_INTEGRITY,
            ):
                run_state.pre_ingest_rejected = True
                run_state.status = PortfolioUpdateStatus.REJECTED
                run_state.primary_error_code = outcome.rejection_code
                run_state.errors.append(
                    PortfolioErrorRecord(
                        code=outcome.rejection_code or ERROR_RESULT_INVALID,
                        message=outcome.message or "Stage failed.",
                        stage_id=stage_id,
                    )
                )
                publisher.publish(
                    PortfolioEventType.INGEST_REJECTED,
                    occurred_at=run_state.context.reference_time,
                    metadata=MappingProxyType(
                        {"error_code": outcome.rejection_code or ERROR_RESULT_INVALID}
                    ),
                )
                short_circuit = True
            elif run_state.idempotent_noop and stage_id is PortfolioIngestStageId.SNAPSHOT_INTEGRITY:
                short_circuit = True

            _logger.debug(
                "portfolio_manager.ingest.stage",
                extra={
                    "event": "portfolio_manager.ingest.stage",
                    "stage_id": stage_id.value,
                    "passed": outcome.passed,
                },
            )

        if not run_state.pre_ingest_rejected and not run_state.idempotent_noop:
            publisher.publish(
                PortfolioEventType.INGEST_RECEIVED,
                occurred_at=run_state.context.reference_time,
                metadata=MappingProxyType(
                    {
                        "position_snapshot_id": run_state.snapshot.snapshot_id,
                        "position_count": str(len(run_state.snapshot.positions)),
                    }
                ),
            )

        completed_at = run_state.context.reference_time
        duration_ms = (completed_at - run_state.started_at).total_seconds() * 1000.0

        if run_state.idempotent_noop and run_state.portfolio_snapshot is not None:
            portfolio_snapshot = run_state.portfolio_snapshot
            metrics = portfolio_snapshot.metrics
            exposure = portfolio_snapshot.exposure
            run_state.status = PortfolioUpdateStatus.NOOP
        elif run_state.pre_ingest_rejected:
            portfolio_snapshot = _empty_portfolio_snapshot(run_state)
            metrics = portfolio_snapshot.metrics
            exposure = portfolio_snapshot.exposure
        else:
            portfolio_snapshot = run_state.portfolio_snapshot
            assert portfolio_snapshot is not None
            metrics = portfolio_snapshot.metrics
            exposure = portfolio_snapshot.exposure

        fingerprint = (
            compute_update_fingerprint(run_state.snapshot, portfolio_snapshot, config)
            if config.deterministic_fingerprint and not run_state.pre_ingest_rejected
            else ""
        )

        if (
            not run_state.pre_ingest_rejected
            and not run_state.idempotent_noop
            and run_state.status is PortfolioUpdateStatus.APPLIED
        ):
            if _material_change(run_state.prior_daily_pnl, metrics.total_daily_pnl):
                publisher.publish(
                    PortfolioEventType.PNL_UPDATED,
                    occurred_at=completed_at,
                    snapshot=portfolio_snapshot,
                )
            if _material_change(run_state.prior_gross_notional, exposure.gross_notional):
                publisher.publish(
                    PortfolioEventType.EXPOSURE_UPDATED,
                    occurred_at=completed_at,
                    snapshot=portfolio_snapshot,
                )
            if _material_change(run_state.prior_portfolio_delta, metrics.portfolio_delta or 0.0):
                publisher.publish(
                    PortfolioEventType.GREEKS_UPDATED,
                    occurred_at=completed_at,
                    snapshot=portfolio_snapshot,
                )
            if _material_change(
                run_state.prior_capital_utilization,
                metrics.capital_utilization_pct,
            ):
                publisher.publish(
                    PortfolioEventType.UTILIZATION_UPDATED,
                    occurred_at=completed_at,
                    snapshot=portfolio_snapshot,
                )
            if run_state.prior_open_count != metrics.open_position_count:
                publisher.publish(
                    PortfolioEventType.AGGREGATION_UPDATED,
                    occurred_at=completed_at,
                    snapshot=portfolio_snapshot,
                )
            publisher.publish(
                PortfolioEventType.PORTFOLIO_UPDATED,
                occurred_at=completed_at,
                snapshot=portfolio_snapshot,
            )
            publisher.publish(
                PortfolioEventType.SNAPSHOT_PUBLISHED,
                occurred_at=completed_at,
                snapshot=portfolio_snapshot,
                metadata=MappingProxyType(
                    {
                        "snapshot_id": portfolio_snapshot.snapshot_id,
                        "open_count": str(metrics.open_position_count),
                    }
                ),
            )

        if run_state.warnings and run_state.status is PortfolioUpdateStatus.APPLIED:
            run_state.status = PortfolioUpdateStatus.PARTIAL

        pipeline_summary = PortfolioPipelineResult(
            total_stages=len(stages),
            passed_stages=sum(1 for stage in stages if stage.passed),
            failed_stage_id=next((stage.stage_id for stage in stages if not stage.passed), None),
            stages=tuple(stages),
            short_circuited=short_circuit,
        )

        result = PortfolioUpdateResult(
            update_id=run_state.update_id,
            source_position_snapshot_id=run_state.snapshot.snapshot_id,
            correlation_id=run_state.context.correlation_id,
            status=run_state.status,
            snapshot=portfolio_snapshot,
            metrics=metrics,
            exposure=exposure,
            pipeline_summary=pipeline_summary,
            warnings=tuple(run_state.warnings),
            errors=tuple(run_state.errors),
            primary_error_code=run_state.primary_error_code,
            submitted_at=run_state.started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            update_fingerprint=fingerprint,
        )

        if config.strict_output_validation and not run_state.pre_ingest_rejected:
            validation = validate_portfolio_update_result(result)
            if not validation.is_valid:
                run_state.primary_error_code = validation.errors[0].code
                result = replace(
                    result,
                    status=PortfolioUpdateStatus.FAILED,
                    errors=result.errors + validation.errors,
                    primary_error_code=validation.errors[0].code,
                )
            elif config.deterministic_fingerprint and fingerprint:
                recomputed = compute_update_fingerprint(
                    run_state.snapshot,
                    portfolio_snapshot,
                    config,
                )
                if recomputed != result.update_fingerprint:
                    mismatch = PortfolioErrorRecord(
                        code=ERROR_RESULT_FINGERPRINT_MISMATCH,
                        message="Update fingerprint mismatch.",
                        stage_id=PortfolioIngestStageId.OUTPUT_VALIDATION,
                    )
                    result = replace(
                        result,
                        status=PortfolioUpdateStatus.FAILED,
                        errors=result.errors + (mismatch,),
                        primary_error_code=ERROR_RESULT_FINGERPRINT_MISMATCH,
                    )

        publisher.publish(
            PortfolioEventType.INGEST_COMPLETED,
            occurred_at=completed_at,
            snapshot=portfolio_snapshot,
            metadata=MappingProxyType({"status": result.status.value}),
        )
        publisher.flush()
        return result

    def _run_stage(
        self,
        stage_id: PortfolioIngestStageId,
        run_state: _PipelineRunState,
        applied_snapshots: set[str],
    ) -> _StageOutcome:
        """Execute one pipeline stage."""
        if run_state.idempotent_noop and stage_id not in (
            PortfolioIngestStageId.RESULT_ASSEMBLY,
            PortfolioIngestStageId.OUTPUT_VALIDATION,
        ):
            return _StageOutcome(passed=True)

        handlers = {
            PortfolioIngestStageId.INPUT_GATE: lambda: _stage_input_gate(run_state),
            PortfolioIngestStageId.SNAPSHOT_INTEGRITY: lambda: _stage_snapshot_integrity(
                run_state, applied_snapshots
            ),
            PortfolioIngestStageId.POSITION_MAPPING: lambda: _stage_position_mapping(run_state),
            PortfolioIngestStageId.PNL_ROLLUP: lambda: _stage_pnl_rollup(run_state),
            PortfolioIngestStageId.GREEKS_AGGREGATION: lambda: _stage_greeks_aggregation(run_state),
            PortfolioIngestStageId.EXPOSURE_CALCULATION: lambda: _stage_exposure_calculation(
                run_state
            ),
            PortfolioIngestStageId.UTILIZATION_CALCULATION: lambda: _stage_utilization_calculation(
                run_state
            ),
            PortfolioIngestStageId.MULTI_DIM_AGGREGATION: lambda: _stage_multi_dim_aggregation(
                run_state
            ),
            PortfolioIngestStageId.SNAPSHOT_ASSEMBLY: lambda: _stage_snapshot_assembly(run_state),
            PortfolioIngestStageId.RESULT_ASSEMBLY: lambda: _StageOutcome(passed=True),
            PortfolioIngestStageId.OUTPUT_VALIDATION: lambda: _StageOutcome(passed=True),
        }
        handler = handlers.get(stage_id)
        if handler is None:
            return _StageOutcome(
                passed=False,
                rejection_code=ERROR_RESULT_INVALID,
                message="Unknown stage.",
            )
        return handler()


def _empty_portfolio_snapshot(run_state: _PipelineRunState) -> PortfolioSnapshot:
    """Build empty portfolio snapshot for rejected ingest."""
    metrics = PortfolioMetrics(
        total_realized_pnl_session=0.0,
        total_unrealized_pnl=0.0,
        total_daily_pnl=0.0,
        equity_hint=run_state.context.equity_hint,
        cash_available_hint=run_state.context.cash_available_hint,
        capital_deployed=0.0,
        capital_utilization_pct=0.0,
        margin_used_hint=run_state.context.margin_used_hint,
        margin_available_hint=run_state.context.margin_available_hint,
        margin_utilization_pct=compute_margin_utilization_pct(
            run_state.context.margin_used_hint,
            run_state.context.margin_available_hint,
        ),
        portfolio_delta=None,
        portfolio_gamma=None,
        portfolio_theta=None,
        portfolio_vega=None,
        open_position_count=0,
        peak_equity_hint=run_state.peak_equity or None,
        metrics_fingerprint="",
    )
    exposure = PortfolioExposure(
        gross_notional=0.0,
        net_notional=0.0,
        gross_notional_by_underlying=MappingProxyType({}),
        net_notional_by_underlying=MappingProxyType({}),
        exposure_by_strategy_id=MappingProxyType({}),
        exposure_by_strategy_family=MappingProxyType({}),
        exposure_by_expiry=MappingProxyType({}),
        largest_underlying_weight_pct=0.0,
        largest_strategy_weight_pct=0.0,
        open_position_count=0,
        open_position_count_by_underlying=MappingProxyType({}),
        exposure_fingerprint="",
    )
    return PortfolioSnapshot(
        snapshot_id=f"pf-rejected-{uuid.uuid4().hex[:12]}",
        correlation_id=run_state.context.correlation_id,
        as_of=run_state.context.reference_time,
        account_id=run_state.context.account_id,
        metrics=metrics,
        exposure=exposure,
        positions=(),
        by_strategy=MappingProxyType({}),
        by_underlying=MappingProxyType({}),
        by_expiry=MappingProxyType({}),
        snapshot_fingerprint="",
    )


def _stage_input_gate(run_state: _PipelineRunState) -> _StageOutcome:
    """Validate ingest inputs."""
    if run_state.snapshot is None:
        return _StageOutcome(
            passed=False,
            rejection_code=ERROR_SNAPSHOT_MISSING,
            message="PositionSnapshot is required.",
        )
    validation = validate_ingest_context(
        run_state.context,
        run_state.snapshot,
        run_state.config,
        position_update_correlation_id=run_state.source_position_update_correlation_id,
    )
    if not validation.is_valid:
        primary = validation.errors[0]
        return _StageOutcome(
            passed=False,
            rejection_code=primary.code,
            message=primary.message,
        )
    return _StageOutcome(passed=True)


def _stage_snapshot_integrity(
    run_state: _PipelineRunState,
    applied_snapshots: set[str],
) -> _StageOutcome:
    """Validate position snapshot integrity."""
    snapshot = run_state.snapshot
    if not snapshot.snapshot_id:
        return _StageOutcome(
            passed=False,
            rejection_code=ERROR_SNAPSHOT_INVALID,
            message="snapshot_id must be non-empty.",
        )
    if len(snapshot.positions) != snapshot.open_position_count:
        return _StageOutcome(
            passed=False,
            rejection_code=ERROR_SNAPSHOT_INVALID,
            message="open_position_count inconsistent with positions length.",
        )
    if (
        run_state.config.idempotent_ingest
        and snapshot.snapshot_fingerprint
        and snapshot.snapshot_fingerprint in applied_snapshots
    ):
        run_state.idempotent_noop = True
        run_state.status = PortfolioUpdateStatus.NOOP
        if run_state.portfolio_snapshot is None:
            run_state.portfolio_snapshot = _empty_portfolio_snapshot(run_state)
    if run_state.config.deterministic_fingerprint and not snapshot.snapshot_fingerprint:
        run_state.warnings.append(
            PortfolioWarningRecord(
                code=ERROR_SNAPSHOT_INVALID,
                message="snapshot_fingerprint missing.",
                stage_id=PortfolioIngestStageId.SNAPSHOT_INTEGRITY,
            )
        )
    return _StageOutcome(passed=True)


def _stage_position_mapping(run_state: _PipelineRunState) -> _StageOutcome:
    """Map positions to portfolio summaries."""
    summaries: list[PortfolioPositionSummary] = []
    for position in run_state.snapshot.positions:
        try:
            mark = run_state.context.price_hints.get(position.instrument_key)
            if mark is None and position.instrument_key not in run_state.context.price_hints:
                run_state.warnings.append(
                    PortfolioWarningRecord(
                        code=WARN_PRICE_MARK_MISSING,
                        message="No mark price hint; using average entry.",
                        position_id=position.position_id,
                        stage_id=PortfolioIngestStageId.POSITION_MAPPING,
                    )
                )
            greek_hint = run_state.context.greek_hints.get(position.position_id)
            if greek_hint is None:
                if run_state.config.require_greek_hints:
                    run_state.warnings.append(
                        PortfolioWarningRecord(
                            code=WARN_GREEK_HINT_MISSING,
                            message="Greek hint missing for position.",
                            position_id=position.position_id,
                            stage_id=PortfolioIngestStageId.POSITION_MAPPING,
                        )
                    )
            elif (
                run_state.config.greek_hint_max_age_seconds >= 0
                and (run_state.context.reference_time - greek_hint.as_of).total_seconds()
                > run_state.config.greek_hint_max_age_seconds
            ):
                run_state.warnings.append(
                    PortfolioWarningRecord(
                        code=WARN_GREEK_HINT_STALE,
                        message="Greek hint is stale.",
                        position_id=position.position_id,
                        stage_id=PortfolioIngestStageId.GREEKS_AGGREGATION,
                    )
                )
            summary = map_position_to_summary(
                position,
                mark_price=mark,
                greek_hint=greek_hint,
                config=run_state.config,
            )
            if summary.expiry == "UNKNOWN":
                run_state.warnings.append(
                    PortfolioWarningRecord(
                        code=WARN_EXPIRY_UNRESOLVED,
                        message="Could not resolve expiry bucket.",
                        position_id=position.position_id,
                        stage_id=PortfolioIngestStageId.POSITION_MAPPING,
                    )
                )
            summaries.append(summary)
        except Exception as exc:
            run_state.errors.append(
                PortfolioErrorRecord(
                    code=ERROR_POSITION_MAPPING,
                    message=str(exc),
                    position_id=position.position_id,
                    stage_id=PortfolioIngestStageId.POSITION_MAPPING,
                )
            )
            return _StageOutcome(
                passed=False,
                rejection_code=ERROR_POSITION_MAPPING,
                message=str(exc),
            )
    run_state.summaries = tuple(summaries)
    if (
        run_state.config.max_open_positions is not None
        and len(summaries) > run_state.config.max_open_positions
    ):
        run_state.warnings.append(
            PortfolioWarningRecord(
                code=ERROR_SNAPSHOT_INVALID,
                message="Open position count exceeds configured informational max.",
                stage_id=PortfolioIngestStageId.POSITION_MAPPING,
            )
        )
    return _StageOutcome(passed=True)


def _stage_pnl_rollup(run_state: _PipelineRunState) -> _StageOutcome:
    """Roll up portfolio P&L."""
    seed = (
        run_state.snapshot.aggregate_realized_pnl_session
        if run_state.config.session_pnl_tracking
        else 0.0
    )
    unrealized = compute_total_unrealized_pnl(run_state.summaries)
    realized = compute_total_realized_pnl_session(run_state.summaries, seed=seed)
    daily = round(realized + unrealized, PNL_ROUND_DECIMALS)
    if abs(unrealized - run_state.snapshot.aggregate_unrealized_pnl) > PNL_EPSILON:
        run_state.warnings.append(
            PortfolioWarningRecord(
                code=WARN_PNL_MISMATCH,
                message="Unrealized P&L rollup differs from position snapshot aggregate.",
                stage_id=PortfolioIngestStageId.PNL_ROLLUP,
            )
        )
    run_state._pnl_cache = (realized, unrealized, daily)  # type: ignore[attr-defined]
    return _StageOutcome(passed=True)


def _stage_greeks_aggregation(run_state: _PipelineRunState) -> _StageOutcome:
    """Aggregate portfolio Greeks."""
    run_state._greeks_cache = aggregate_portfolio_greeks(run_state.summaries)  # type: ignore[attr-defined]
    return _StageOutcome(passed=True)


def _stage_exposure_calculation(run_state: _PipelineRunState) -> _StageOutcome:
    """Calculate portfolio exposure."""
    try:
        gross = compute_gross_notional(run_state.summaries)
        net = compute_net_notional(run_state.summaries)
        gross_by_underlying: dict[str, float] = {}
        net_by_underlying: dict[str, float] = {}
        count_by_underlying: dict[str, int] = {}
        by_strategy_id: dict[str, float] = {}
        by_strategy_family: dict[str, float] = {}
        by_expiry: dict[str, float] = {}

        for summary in run_state.summaries:
            sign = -1.0 if summary.side == PositionSide.SHORT.value else 1.0
            signed = sign * summary.notional_exposure
            abs_notional = abs(summary.notional_exposure)
            gross_by_underlying[summary.underlying] = (
                gross_by_underlying.get(summary.underlying, 0.0) + abs_notional
            )
            net_by_underlying[summary.underlying] = (
                net_by_underlying.get(summary.underlying, 0.0) + signed
            )
            count_by_underlying[summary.underlying] = (
                count_by_underlying.get(summary.underlying, 0) + 1
            )
            by_strategy_id[summary.strategy_id] = (
                by_strategy_id.get(summary.strategy_id, 0.0) + abs_notional
            )
            family_key = summary.strategy_family.value
            by_strategy_family[family_key] = (
                by_strategy_family.get(family_key, 0.0) + abs_notional
            )
            expiry_key = summary.expiry or "UNKNOWN"
            by_expiry[expiry_key] = by_expiry.get(expiry_key, 0.0) + abs_notional

        exposure = PortfolioExposure(
            gross_notional=gross,
            net_notional=net,
            gross_notional_by_underlying=MappingProxyType(
                {k: round(v, NOTIONAL_ROUND_DECIMALS) for k, v in sorted(gross_by_underlying.items())}
            ),
            net_notional_by_underlying=MappingProxyType(
                {k: round(v, NOTIONAL_ROUND_DECIMALS) for k, v in sorted(net_by_underlying.items())}
            ),
            exposure_by_strategy_id=MappingProxyType(
                {k: round(v, NOTIONAL_ROUND_DECIMALS) for k, v in sorted(by_strategy_id.items())}
            ),
            exposure_by_strategy_family=MappingProxyType(
                {k: round(v, NOTIONAL_ROUND_DECIMALS) for k, v in sorted(by_strategy_family.items())}
            ),
            exposure_by_expiry=MappingProxyType(
                {k: round(v, NOTIONAL_ROUND_DECIMALS) for k, v in sorted(by_expiry.items())}
            ),
            largest_underlying_weight_pct=compute_largest_weight_pct(
                gross_by_underlying,
                gross,
            ),
            largest_strategy_weight_pct=compute_largest_weight_pct(by_strategy_id, gross),
            open_position_count=len(run_state.summaries),
            open_position_count_by_underlying=MappingProxyType(
                dict(sorted(count_by_underlying.items()))
            ),
            exposure_fingerprint="",
        )
        exposure = replace(
            exposure,
            exposure_fingerprint=compute_exposure_fingerprint(exposure),
        )
        run_state.exposure = exposure
    except Exception as exc:
        return _StageOutcome(
            passed=False,
            rejection_code=ERROR_EXPOSURE_COMPUTATION,
            message=str(exc),
        )
    return _StageOutcome(passed=True)


def _stage_utilization_calculation(run_state: _PipelineRunState) -> _StageOutcome:
    """Calculate capital and margin utilization."""
    assert run_state.exposure is not None
    context = run_state.context
    if context.margin_available_hint is None:
        run_state.warnings.append(
            PortfolioWarningRecord(
                code=WARN_MARGIN_HINT_MISSING,
                message="margin_available_hint not supplied.",
                stage_id=PortfolioIngestStageId.UTILIZATION_CALCULATION,
            )
        )
    if context.margin_hint_as_of is not None:
        age = (context.reference_time - context.margin_hint_as_of).total_seconds()
        if age > run_state.config.margin_hint_max_age_seconds:
            run_state.warnings.append(
                PortfolioWarningRecord(
                    code=WARN_MARGIN_HINT_STALE,
                    message="Margin hint is stale.",
                    stage_id=PortfolioIngestStageId.UTILIZATION_CALCULATION,
                )
            )
    capital_deployed = run_state.exposure.gross_notional
    run_state._utilization_cache = (  # type: ignore[attr-defined]
        capital_deployed,
        compute_capital_utilization_pct(capital_deployed, context.equity_hint),
        compute_margin_utilization_pct(context.margin_used_hint, context.margin_available_hint),
    )
    return _StageOutcome(passed=True)


def _stage_multi_dim_aggregation(run_state: _PipelineRunState) -> _StageOutcome:
    """Build multi-dimensional aggregation buckets."""
    assert run_state.exposure is not None
    gross_total = run_state.exposure.gross_notional
    run_state.by_strategy = _build_bucket(
        run_state.summaries,
        lambda s: s.strategy_id,
        gross_total,
    )
    run_state.by_underlying = _build_bucket(
        run_state.summaries,
        lambda s: s.underlying,
        gross_total,
    )
    run_state.by_expiry = _build_bucket(
        run_state.summaries,
        lambda s: s.expiry or "UNKNOWN",
        gross_total,
    )
    return _StageOutcome(passed=True)


def _stage_snapshot_assembly(run_state: _PipelineRunState) -> _StageOutcome:
    """Assemble portfolio snapshot and metrics."""
    assert run_state.exposure is not None
    realized, unrealized, daily = run_state._pnl_cache  # type: ignore[attr-defined]
    delta, gamma, theta, vega = run_state._greeks_cache  # type: ignore[attr-defined]
    capital_deployed, capital_util, margin_util = run_state._utilization_cache  # type: ignore[attr-defined]
    context = run_state.context

    peak = run_state.peak_equity
    if run_state.config.track_peak_equity:
        peak = max(peak, context.equity_hint, context.peak_equity_hint or 0.0)

    metrics = PortfolioMetrics(
        total_realized_pnl_session=realized,
        total_unrealized_pnl=unrealized,
        total_daily_pnl=daily,
        equity_hint=context.equity_hint,
        cash_available_hint=context.cash_available_hint,
        capital_deployed=capital_deployed,
        capital_utilization_pct=capital_util,
        margin_used_hint=context.margin_used_hint,
        margin_available_hint=context.margin_available_hint,
        margin_utilization_pct=margin_util,
        portfolio_delta=delta,
        portfolio_gamma=gamma,
        portfolio_theta=theta,
        portfolio_vega=vega,
        open_position_count=len(run_state.summaries),
        peak_equity_hint=peak if peak > 0 else None,
        metrics_fingerprint="",
    )
    metrics = replace(metrics, metrics_fingerprint=compute_metrics_fingerprint(metrics))

    portfolio_snapshot = PortfolioSnapshot(
        snapshot_id=_generate_portfolio_snapshot_id(
            run_state.snapshot,
            context,
            run_state.config,
        ),
        correlation_id=context.correlation_id,
        as_of=context.reference_time,
        account_id=context.account_id,
        metrics=metrics,
        exposure=run_state.exposure,
        positions=run_state.summaries,
        by_strategy=MappingProxyType(dict(sorted(run_state.by_strategy.items()))),
        by_underlying=MappingProxyType(dict(sorted(run_state.by_underlying.items()))),
        by_expiry=MappingProxyType(dict(sorted(run_state.by_expiry.items()))),
        snapshot_fingerprint="",
    )
    portfolio_snapshot = replace(
        portfolio_snapshot,
        snapshot_fingerprint=compute_snapshot_fingerprint(portfolio_snapshot),
    )
    run_state.portfolio_snapshot = portfolio_snapshot
    run_state.metrics = metrics
    run_state.peak_equity = peak
    return _StageOutcome(passed=True)


def _generate_update_id(context: PortfolioIngestContext) -> str:
    """Generate ingest run identifier."""
    payload = f"{context.correlation_id}|{_datetime_to_iso(context.reference_time)}|ingest"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"pfin-{digest}"


def _generate_portfolio_snapshot_id(
    position_snapshot: PositionSnapshot,
    context: PortfolioIngestContext,
    config: PortfolioManagerConfig,
) -> str:
    """Generate portfolio snapshot identifier.

    When ``deterministic_fingerprint`` is enabled, derive a stable ID from
    upstream snapshot fingerprint and ingest context so replay fingerprints
    remain stable across manager instances.
    """
    if config.deterministic_fingerprint:
        payload = {
            "position_snapshot_fingerprint": position_snapshot.snapshot_fingerprint,
            "correlation_id": context.correlation_id,
            "as_of": _datetime_to_iso(context.reference_time),
        }
        digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:16]
        return f"pf-{digest}"
    return f"pf-{uuid.uuid4().hex[:16]}"


def _build_rejected_result(
    context: PortfolioIngestContext,
    config: PortfolioManagerConfig,
    *,
    error_code: str,
    message: str,
) -> PortfolioUpdateResult:
    """Build rejected result without mutating registry."""
    as_of = context.reference_time if _is_timezone_aware(context.reference_time) else _utc_now()
    empty_state = _PipelineRunState(
        snapshot=PositionSnapshot(
            snapshot_id="",
            as_of=as_of,
            account_id=context.account_id,
            positions=(),
            open_position_count=0,
            aggregate_quantity_by_underlying=MappingProxyType({}),
            aggregate_unrealized_pnl=0.0,
            aggregate_realized_pnl_session=0.0,
            snapshot_fingerprint="",
        ),
        context=context,
        config=config,
        update_id=f"pfin-rejected-{uuid.uuid4().hex[:12]}",
        started_at=as_of,
    )
    portfolio_snapshot = _empty_portfolio_snapshot(empty_state)
    return PortfolioUpdateResult(
        update_id=empty_state.update_id,
        source_position_snapshot_id=None,
        correlation_id=context.correlation_id,
        status=PortfolioUpdateStatus.REJECTED,
        snapshot=portfolio_snapshot,
        metrics=portfolio_snapshot.metrics,
        exposure=portfolio_snapshot.exposure,
        pipeline_summary=PortfolioPipelineResult(
            total_stages=1,
            passed_stages=0,
            failed_stage_id=PortfolioIngestStageId.INPUT_GATE,
            stages=(),
            short_circuited=True,
        ),
        warnings=(),
        errors=(PortfolioErrorRecord(code=error_code, message=message),),
        primary_error_code=error_code,
        submitted_at=as_of,
        completed_at=as_of,
        duration_ms=0.0,
        update_fingerprint="",
    )


class PortfolioManager:
    """Institutional account-level portfolio aggregation manager.

    Consumes PositionSnapshot artifacts from Position Manager, computes
    portfolio P&L, exposure, Greeks, utilization, and publishes portfolio.*
    lifecycle events.

    Args:
        config: Injected immutable configuration.
        event_bus: Optional EventBus for lifecycle event publishing.
    """

    def __init__(
        self,
        config: PortfolioManagerConfig | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._config = config or default_portfolio_manager_config()
        self._event_bus = event_bus
        self._registry_lock = threading.RLock()
        self._latest_snapshot: PortfolioSnapshot | None = None
        self._applied_snapshots: set[str] = set()
        self._peak_equity: float = 0.0
        self._pipeline = PortfolioIngestPipeline()

    @property
    def config(self) -> PortfolioManagerConfig:
        """Return manager configuration."""
        return self._config

    def ingest_position_snapshot(
        self,
        snapshot: PositionSnapshot,
        context: PortfolioIngestContext,
        *,
        position_update_correlation_id: str | None = None,
    ) -> PortfolioUpdateResult:
        """Ingest a Position Manager snapshot and recompute portfolio rollups."""
        _logger.info(
            "portfolio_manager.ingest.start",
            extra={
                "event": "portfolio_manager.ingest.start",
                "position_snapshot_id": snapshot.snapshot_id,
            },
        )
        if not _is_timezone_aware(context.reference_time):
            _logger.info(
                "portfolio_manager.ingest.rejected",
                extra={"event": "portfolio_manager.ingest.rejected"},
            )
            return _build_rejected_result(
                context,
                self._config,
                error_code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
                message="reference_time must be timezone-aware.",
            )
        with self._registry_lock:
            prior = self._latest_snapshot
            run_state = _PipelineRunState(
                snapshot=snapshot,
                context=context,
                config=self._config,
                update_id=_generate_update_id(context),
                started_at=context.reference_time,
                peak_equity=self._peak_equity,
                portfolio_snapshot=self._latest_snapshot,
                source_position_update_correlation_id=position_update_correlation_id,
                prior_daily_pnl=prior.metrics.total_daily_pnl if prior else None,
                prior_gross_notional=prior.exposure.gross_notional if prior else None,
                prior_portfolio_delta=prior.metrics.portfolio_delta if prior else None,
                prior_capital_utilization=(
                    prior.metrics.capital_utilization_pct if prior else None
                ),
                prior_open_count=prior.metrics.open_position_count if prior else None,
            )
            result = self._pipeline.execute(
                run_state,
                self._config,
                event_bus=self._event_bus,
                applied_snapshots=set(self._applied_snapshots),
            )
            if result.status not in (
                PortfolioUpdateStatus.REJECTED,
                PortfolioUpdateStatus.FAILED,
                PortfolioUpdateStatus.NOOP,
            ):
                self._latest_snapshot = result.snapshot
                if snapshot.snapshot_fingerprint:
                    self._applied_snapshots.add(snapshot.snapshot_fingerprint)
                self._peak_equity = run_state.peak_equity
            elif result.status is PortfolioUpdateStatus.NOOP and self._latest_snapshot:
                pass
            elif result.status not in (PortfolioUpdateStatus.REJECTED, PortfolioUpdateStatus.FAILED):
                self._latest_snapshot = result.snapshot
        _logger.info(
            "portfolio_manager.ingest.complete",
            extra={
                "event": "portfolio_manager.ingest.complete",
                "status": result.status.value,
                "update_id": result.update_id,
            },
        )
        return result

    def ingest_position_update_result(
        self,
        result: PositionUpdateResult,
        context: PortfolioIngestContext,
    ) -> PortfolioUpdateResult:
        """Ingest from sealed PositionUpdateResult wrapper."""
        return self.ingest_position_snapshot(
            result.snapshot,
            context,
            position_update_correlation_id=result.correlation_id,
        )

    def get_snapshot(self) -> PortfolioSnapshot | None:
        """Return latest immutable portfolio snapshot."""
        with self._registry_lock:
            return self._latest_snapshot

    def get_metrics(self) -> PortfolioMetrics | None:
        """Return metrics from latest snapshot."""
        with self._registry_lock:
            return self._latest_snapshot.metrics if self._latest_snapshot else None

    def get_exposure(self) -> PortfolioExposure | None:
        """Return exposure from latest snapshot."""
        with self._registry_lock:
            return self._latest_snapshot.exposure if self._latest_snapshot else None

    def on_position_snapshot_event(self, event: PositionEvent) -> None:
        """Handle position.snapshot.published events.

        v1 requires orchestrator to call ingest_position_snapshot with full
        PositionSnapshot; this handler records the event for optional wiring.
        """
        if event.event_type is not PositionEventType.SNAPSHOT_PUBLISHED:
            return
        _logger.debug(
            "portfolio_manager.position_event.received",
            extra={"event": "portfolio_manager.position_event.received", "topic": event.topic},
        )

    def validate_ingest_context(
        self,
        context: PortfolioIngestContext,
        snapshot: PositionSnapshot,
    ) -> PortfolioValidationResult:
        """Validate context and snapshot without mutating state."""
        return validate_ingest_context(context, snapshot, self._config)

    def validate_update_result(
        self,
        result: PortfolioUpdateResult,
    ) -> PortfolioValidationResult:
        """Validate sealed ingest result."""
        return validate_portfolio_update_result(result)
