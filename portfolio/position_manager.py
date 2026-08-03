"""Institutional live position tracking and P&L accounting for THETA AI TRADER v1.0.

Consumes immutable order lifecycle artifacts from Order Manager and maintains
authoritative position records, P&L, lifecycle transitions, and ``position.*``
lifecycle events.
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

from broker.base_broker import PositionRecord
from core.event_bus import EventBus, EventEnvelope
from execution.execution_engine import OrderSide, OrderType, ProductType
from execution.order_manager import (
    OrderLifecycleEvent,
    OrderLifecycleStatus,
    OrderState,
    OrderTracker,
)
from strategy.signals import StrategyExecutionMode, StrategyFamily

POSITION_MANAGER_VERSION: Final[str] = "1.0.0"
POSITION_SCHEMA_VERSION: Final[str] = "1.0.0"
PRODUCER_NAME: Final[str] = "position_manager"
DEFAULT_PRICE_HINT_MAX_AGE_SECONDS: Final[int] = 300
QUANTITY_EPSILON: Final[int] = 0
PRICE_ROUND_DECIMALS: Final[int] = 2
PNL_ROUND_DECIMALS: Final[int] = 2
MAX_QUANTITY: Final[int] = 2_147_483_647

ERROR_CONFIG_INVALID: Final[str] = "POSITION_MANAGER.CONFIG.INVALID"
ERROR_CONTEXT_INVALID: Final[str] = "POSITION_MANAGER.CONTEXT.INVALID"
ERROR_CONTEXT_NAIVE_TIMESTAMP: Final[str] = "POSITION_MANAGER.CONTEXT.NAIVE_TIMESTAMP"
ERROR_CONTEXT_CORRELATION_MISMATCH: Final[str] = "POSITION_MANAGER.CONTEXT.CORRELATION_MISMATCH"
ERROR_TRACKER_MISSING: Final[str] = "POSITION_MANAGER.TRACKER.MISSING"
ERROR_TRACKER_NO_LEGS: Final[str] = "POSITION_MANAGER.TRACKER.NO_LEGS"
ERROR_TRACKER_NO_FILLS: Final[str] = "POSITION_MANAGER.TRACKER.NO_FILLS"
ERROR_TRACKER_INVALID: Final[str] = "POSITION_MANAGER.TRACKER.INVALID"
ERROR_FILL_INVALID_INSTRUMENT: Final[str] = "POSITION_MANAGER.FILL.INVALID_INSTRUMENT"
ERROR_FILL_INVALID_QUANTITY: Final[str] = "POSITION_MANAGER.FILL.INVALID_QUANTITY"
ERROR_FILL_INVALID_PRICE: Final[str] = "POSITION_MANAGER.FILL.INVALID_PRICE"
ERROR_FILL_OVER_EXIT: Final[str] = "POSITION_MANAGER.FILL.OVER_EXIT"
ERROR_POSITION_NOT_FOUND: Final[str] = "POSITION_MANAGER.POSITION.NOT_FOUND"
ERROR_POSITION_INTEGRITY: Final[str] = "POSITION_MANAGER.POSITION.INTEGRITY_FAILED"
ERROR_RESULT_INVALID: Final[str] = "POSITION_MANAGER.RESULT.INVALID"
ERROR_RESULT_FINGERPRINT_MISMATCH: Final[str] = "POSITION_MANAGER.RESULT.FINGERPRINT_MISMATCH"
ERROR_SERIALIZATION_UNSUPPORTED_VERSION: Final[str] = (
    "POSITION_MANAGER.SERIALIZATION.UNSUPPORTED_VERSION"
)
ERROR_SERIALIZATION_MALFORMED: Final[str] = "POSITION_MANAGER.SERIALIZATION.MALFORMED"

WARN_STRATEGY_MISSING: Final[str] = "POSITION_MANAGER.STRATEGY.MISSING"
WARN_PRICE_HINT_MISSING: Final[str] = "POSITION_MANAGER.PRICE.HINT_MISSING"
WARN_PRICE_HINT_STALE: Final[str] = "POSITION_MANAGER.PRICE.HINT_STALE"
WARN_BROKER_DRIFT: Final[str] = "POSITION_MANAGER.BROKER.DRIFT"

_FILL_BEARING_STATUSES: Final[frozenset[OrderLifecycleStatus]] = frozenset(
    {
        OrderLifecycleStatus.PARTIALLY_FILLED,
        OrderLifecycleStatus.COMPLETE,
        OrderLifecycleStatus.OPEN,
    }
)
_logger = logging.getLogger("portfolio.position_manager")


class PositionManagerError(Exception):
    """Base position manager exception."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        field: str | None = None,
        position_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.position_id = position_id


class PositionManagerConfigurationError(PositionManagerError):
    """Raised when position manager configuration is invalid."""


class PositionManagerValidationError(PositionManagerError):
    """Raised when input or output validation fails."""


class PositionManagerContextError(PositionManagerError):
    """Raised when update context is invalid."""


class PositionManagerUpdateError(PositionManagerError):
    """Raised when update pipeline fails irrecoverably."""


class PositionUpdateStatus(str, Enum):
    """Overall status of a position update run."""

    APPLIED = "applied"
    PARTIAL = "partial"
    NOOP = "noop"
    REJECTED = "rejected"
    FAILED = "failed"


class PositionLifecycleState(str, Enum):
    """Position lifecycle phase."""

    PENDING = "pending"
    OPENING = "opening"
    OPEN = "open"
    PARTIALLY_CLOSED = "partially_closed"
    CLOSING = "closing"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    ERROR = "error"


class PositionSide(str, Enum):
    """Net position direction."""

    LONG = "long"
    SHORT = "short"


class PositionEventType(str, Enum):
    """Position lifecycle event discriminator with associated topic."""

    UPDATE_RECEIVED = "update_received"
    UPDATE_REJECTED = "update_rejected"
    POSITION_OPENED = "position_opened"
    POSITION_UPDATED = "position_updated"
    POSITION_PARTIAL_FILL = "position_partial_fill"
    POSITION_PARTIAL_CLOSE = "position_partial_close"
    POSITION_CLOSED = "position_closed"
    POSITION_PNL_UPDATED = "position_pnl_updated"
    POSITION_REALIZED_PNL = "position_realized_pnl"
    POSITION_CANCELLED = "position_cancelled"
    POSITION_ERROR = "position_error"
    SNAPSHOT_PUBLISHED = "snapshot_published"
    UPDATE_COMPLETED = "update_completed"

    @property
    def topic(self) -> str:
        """Return hierarchical event bus topic for this event type."""
        mapping = {
            PositionEventType.UPDATE_RECEIVED: "position.update.received",
            PositionEventType.UPDATE_REJECTED: "position.update.rejected",
            PositionEventType.POSITION_OPENED: "position.opened",
            PositionEventType.POSITION_UPDATED: "position.updated",
            PositionEventType.POSITION_PARTIAL_FILL: "position.partial_fill",
            PositionEventType.POSITION_PARTIAL_CLOSE: "position.partial_close",
            PositionEventType.POSITION_CLOSED: "position.closed",
            PositionEventType.POSITION_PNL_UPDATED: "position.pnl.updated",
            PositionEventType.POSITION_REALIZED_PNL: "position.pnl.realized",
            PositionEventType.POSITION_CANCELLED: "position.cancelled",
            PositionEventType.POSITION_ERROR: "position.error",
            PositionEventType.SNAPSHOT_PUBLISHED: "position.snapshot.published",
            PositionEventType.UPDATE_COMPLETED: "position.update.completed",
        }
        return mapping[self]


class PositionUpdateStageId(str, Enum):
    """Ordered update pipeline stage identifiers."""

    INPUT_GATE = "input_gate"
    TRACKER_INTEGRITY = "tracker_integrity"
    FILL_EXTRACTION = "fill_extraction"
    PRE_UPDATE_VALIDATION = "pre_update_validation"
    POSITION_APPLICATION = "position_application"
    PNL_RECOMPUTATION = "pnl_recomputation"
    SNAPSHOT_ASSEMBLY = "snapshot_assembly"
    RESULT_ASSEMBLY = "result_assembly"
    OUTPUT_VALIDATION = "output_validation"


STAGE_ORDER: Final[tuple[PositionUpdateStageId, ...]] = (
    PositionUpdateStageId.INPUT_GATE,
    PositionUpdateStageId.TRACKER_INTEGRITY,
    PositionUpdateStageId.FILL_EXTRACTION,
    PositionUpdateStageId.PRE_UPDATE_VALIDATION,
    PositionUpdateStageId.POSITION_APPLICATION,
    PositionUpdateStageId.PNL_RECOMPUTATION,
    PositionUpdateStageId.SNAPSHOT_ASSEMBLY,
    PositionUpdateStageId.RESULT_ASSEMBLY,
    PositionUpdateStageId.OUTPUT_VALIDATION,
)

_OPEN_LIFECYCLE = frozenset(
    {
        PositionLifecycleState.OPEN,
        PositionLifecycleState.OPENING,
        PositionLifecycleState.PARTIALLY_CLOSED,
        PositionLifecycleState.CLOSING,
    }
)
_TERMINAL_LIFECYCLE = frozenset(
    {
        PositionLifecycleState.CLOSED,
        PositionLifecycleState.CANCELLED,
        PositionLifecycleState.EXPIRED,
        PositionLifecycleState.ERROR,
    }
)


@dataclass(frozen=True)
class PositionManagerConfig:
    """Configuration for position manager behavior."""

    strict_correlation: bool = True
    strict_output_validation: bool = True
    deterministic_fingerprint: bool = True
    publish_lifecycle_events: bool = True
    idempotent_updates: bool = True
    group_multi_leg_by_plan: bool = True
    allow_negative_quantity: bool = False
    enable_broker_reconciliation: bool = False
    price_hint_max_age_seconds: int = DEFAULT_PRICE_HINT_MAX_AGE_SECONDS
    session_realized_pnl_tracking: bool = True
    partial_fill_terminal_in_backtest: bool = True
    reject_skipped_legs: bool = False
    allow_orphan_exits: bool = False
    continue_on_leg_error: bool = True
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.price_hint_max_age_seconds < 0:
            raise PositionManagerConfigurationError(
                "price_hint_max_age_seconds must be non-negative.",
                code=ERROR_CONFIG_INVALID,
                field="price_hint_max_age_seconds",
            )


@dataclass(frozen=True)
class PositionUpdateContext:
    """Immutable per-run inputs for position updates."""

    correlation_id: str
    reference_time: datetime
    execution_mode: StrategyExecutionMode
    price_hints: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    account_id: str | None = None
    broker_positions: tuple[PositionRecord, ...] = ()


@dataclass(frozen=True)
class PositionTransition:
    """Append-only lifecycle transition record."""

    from_state: PositionLifecycleState
    to_state: PositionLifecycleState
    occurred_at: datetime
    reason_code: str
    message: str
    quantity_before: int
    quantity_after: int
    fill_id: str | None = None


@dataclass(frozen=True)
class Position:
    """Immutable live position record."""

    position_id: str
    instrument_key: str
    side: PositionSide
    product: ProductType
    quantity: int
    average_entry_price: float
    cost_basis: float
    lifecycle_state: PositionLifecycleState
    strategy_id: str
    strategy_family: StrategyFamily
    realized_pnl: float
    unrealized_pnl: float
    transitions: tuple[PositionTransition, ...]
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    position_group_id: str | None = None

    def __post_init__(self) -> None:
        if self.quantity < 0:
            raise PositionManagerValidationError(
                "quantity must be non-negative.",
                code=ERROR_POSITION_INTEGRITY,
                field="quantity",
                position_id=self.position_id,
            )
        if self.quantity > 0 and self.average_entry_price <= 0:
            raise PositionManagerValidationError(
                "average_entry_price must be positive when quantity > 0.",
                code=ERROR_POSITION_INTEGRITY,
                field="average_entry_price",
                position_id=self.position_id,
            )
        if self.lifecycle_state is PositionLifecycleState.CLOSED and self.quantity != 0:
            raise PositionManagerValidationError(
                "CLOSED positions must have zero quantity.",
                code=ERROR_POSITION_INTEGRITY,
                field="quantity",
                position_id=self.position_id,
            )
        if self.lifecycle_state is PositionLifecycleState.OPEN and self.quantity <= 0:
            raise PositionManagerValidationError(
                "OPEN positions must have positive quantity.",
                code=ERROR_POSITION_INTEGRITY,
                field="quantity",
                position_id=self.position_id,
            )


@dataclass(frozen=True)
class PositionSnapshot:
    """Point-in-time aggregate of open positions."""

    snapshot_id: str
    as_of: datetime
    account_id: str | None
    positions: tuple[Position, ...]
    open_position_count: int
    aggregate_quantity_by_underlying: Mapping[str, int]
    aggregate_unrealized_pnl: float
    aggregate_realized_pnl_session: float
    snapshot_fingerprint: str


@dataclass(frozen=True)
class PositionWarningRecord:
    """Non-fatal warning emitted during position update."""

    code: str
    message: str
    position_id: str | None = None
    stage_id: PositionUpdateStageId | None = None
    field: str | None = None


@dataclass(frozen=True)
class PositionErrorRecord:
    """Structured error emitted during position update."""

    code: str
    message: str
    position_id: str | None = None
    stage_id: PositionUpdateStageId | None = None
    field: str | None = None
    leg_index: int | None = None


@dataclass(frozen=True)
class PositionValidationResult:
    """Validation outcome for context or result checks."""

    errors: tuple[PositionErrorRecord, ...] = ()
    warnings: tuple[PositionWarningRecord, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return True when no errors are present."""
        return not self.errors


@dataclass(frozen=True)
class PositionStageResult:
    """Audit record for one pipeline stage."""

    stage_id: PositionUpdateStageId
    passed: bool
    rejection_code: str | None
    message: str | None
    duration_ms: float
    details: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class PositionPipelineResult:
    """Pipeline stage audit summary."""

    total_stages: int
    passed_stages: int
    failed_stage_id: PositionUpdateStageId | None
    stages: tuple[PositionStageResult, ...]
    short_circuited: bool


@dataclass(frozen=True)
class PositionUpdateResult:
    """Immutable sealed position update outcome."""

    update_id: str
    tracker_submission_id: str | None
    correlation_id: str
    status: PositionUpdateStatus
    snapshot: PositionSnapshot
    updated_positions: tuple[Position, ...]
    pipeline_summary: PositionPipelineResult
    warnings: tuple[PositionWarningRecord, ...]
    errors: tuple[PositionErrorRecord, ...]
    primary_error_code: str | None
    submitted_at: datetime
    completed_at: datetime | None
    duration_ms: float
    update_fingerprint: str


@dataclass(frozen=True)
class PositionEvent:
    """Structured position lifecycle event payload."""

    event_type: PositionEventType
    topic: str
    update_id: str
    correlation_id: str
    occurred_at: datetime
    position_id: str | None = None
    position: Position | None = None
    producer: str = PRODUCER_NAME
    producer_version: str = POSITION_MANAGER_VERSION
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class FillDelta:
    """Normalized fill increment extracted from OrderState."""

    fill_id: str
    leg_index: int
    instrument_key: str
    side: OrderSide
    product: ProductType
    fill_quantity: int
    fill_price: float
    cumulative_filled: int
    planned_quantity: int
    is_exit: bool
    plan_id: str
    strategy_id: str
    strategy_family: StrategyFamily
    correlation_id: str
    submission_id: str
    occurred_at: datetime
    underlying: str = ""


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

    tracker: OrderTracker | None
    context: PositionUpdateContext
    config: PositionManagerConfig
    update_id: str
    started_at: datetime
    registry: dict[str, Position]
    applied_fills: set[str]
    fill_deltas: tuple[FillDelta, ...] = ()
    updated_position_ids: set[str] = field(default_factory=set)
    warnings: list[PositionWarningRecord] = field(default_factory=list)
    errors: list[PositionErrorRecord] = field(default_factory=list)
    primary_error_code: str | None = None
    pre_update_rejected: bool = False
    short_circuit: bool = False
    status: PositionUpdateStatus = PositionUpdateStatus.APPLIED
    session_realized_pnl: float = 0.0
    plan_leg_counts: dict[str, int] = field(default_factory=dict)
    events: list[PositionEvent] = field(default_factory=list)


def default_position_manager_config() -> PositionManagerConfig:
    """Return production-default position manager configuration."""
    return PositionManagerConfig(metadata=MappingProxyType({}))


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
        raise PositionManagerValidationError(
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
        raise PositionManagerValidationError(
            "deserialized datetime must be timezone-aware.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return parsed


def config_fingerprint(config: PositionManagerConfig) -> str:
    """Compute deterministic fingerprint for configuration."""
    payload = {
        "strict_correlation": config.strict_correlation,
        "strict_output_validation": config.strict_output_validation,
        "deterministic_fingerprint": config.deterministic_fingerprint,
        "publish_lifecycle_events": config.publish_lifecycle_events,
        "idempotent_updates": config.idempotent_updates,
        "group_multi_leg_by_plan": config.group_multi_leg_by_plan,
        "allow_negative_quantity": config.allow_negative_quantity,
        "enable_broker_reconciliation": config.enable_broker_reconciliation,
        "price_hint_max_age_seconds": config.price_hint_max_age_seconds,
        "session_realized_pnl_tracking": config.session_realized_pnl_tracking,
        "partial_fill_terminal_in_backtest": config.partial_fill_terminal_in_backtest,
        "reject_skipped_legs": config.reject_skipped_legs,
        "allow_orphan_exits": config.allow_orphan_exits,
        "continue_on_leg_error": config.continue_on_leg_error,
        "metadata": dict(sorted(config.metadata.items())),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _parse_strategy_family(metadata: Mapping[str, str]) -> StrategyFamily:
    """Parse strategy family from order metadata."""
    raw = metadata.get("strategy_family", "")
    if not raw:
        return StrategyFamily.CUSTOM
    try:
        return StrategyFamily(raw)
    except ValueError:
        return StrategyFamily.CUSTOM


def _price_from_metadata(state: OrderState) -> float:
    """Extract fill price hint from order state metadata."""
    for key in ("fill_price", "limit_price", "average_fill_price"):
        raw = state.metadata.get(key)
        if raw:
            try:
                return float(raw)
            except ValueError:
                continue
    return 0.0


def _compute_fill_id(state: OrderState, submission_id: str) -> str:
    """Compute deterministic fill identifier."""
    payload = (
        f"{submission_id}|{state.leg_index}|{state.idempotency_key}|"
        f"{state.filled_quantity}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _position_lookup_key(
    instrument_key: str,
    strategy_id: str,
    product: ProductType,
) -> str:
    """Build registry lookup key for position resolution."""
    return f"{instrument_key}|{strategy_id}|{product.value}"


def _generate_position_id(
    instrument_key: str,
    strategy_id: str,
    product: ProductType,
    plan_id: str,
) -> str:
    """Generate stable position identifier."""
    payload = f"{instrument_key}|{strategy_id}|{product.value}|{plan_id}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"pos-{digest}"


def _side_from_order_side(order_side: OrderSide) -> PositionSide:
    """Map order side to position side for new positions."""
    if order_side is OrderSide.BUY:
        return PositionSide.LONG
    return PositionSide.SHORT


def _determine_is_exit(
    delta_side: OrderSide,
    position: Position | None,
    metadata: Mapping[str, str] | None = None,
) -> bool:
    """Determine whether fill reduces existing position."""
    if metadata and metadata.get("position_intent") == "exit":
        return True
    if position is None or position.quantity <= 0:
        return False
    if position.side is PositionSide.LONG:
        return delta_side is OrderSide.SELL
    return delta_side is OrderSide.BUY


def compute_new_average_entry_price(
    current_qty: int,
    current_avg: float,
    fill_qty: int,
    fill_price: float,
) -> float:
    """Compute volume-weighted average entry price."""
    if current_qty <= 0:
        return round(fill_price, PRICE_ROUND_DECIMALS)
    total_cost = (current_avg * current_qty) + (fill_price * fill_qty)
    new_qty = current_qty + fill_qty
    return round(total_cost / new_qty, PRICE_ROUND_DECIMALS)


def compute_realized_pnl_delta(
    side: PositionSide,
    avg_entry: float,
    exit_qty: int,
    exit_price: float,
) -> float:
    """Compute realized P&L for quantity reduction."""
    if side is PositionSide.LONG:
        pnl = (exit_price - avg_entry) * exit_qty
    else:
        pnl = (avg_entry - exit_price) * exit_qty
    return round(pnl, PNL_ROUND_DECIMALS)


def compute_unrealized_pnl(
    side: PositionSide,
    quantity: int,
    avg_entry: float,
    mark_price: float,
) -> float:
    """Compute mark-to-market unrealized P&L."""
    if quantity <= 0:
        return 0.0
    if side is PositionSide.LONG:
        return round((mark_price - avg_entry) * quantity, PNL_ROUND_DECIMALS)
    return round((avg_entry - mark_price) * quantity, PNL_ROUND_DECIMALS)


def extract_fill_deltas(
    tracker: OrderTracker,
    *,
    previously_applied: frozenset[str],
    registry: Mapping[str, Position] | None = None,
) -> tuple[FillDelta, ...]:
    """Extract new fill deltas from tracker leg states."""
    lookup: dict[str, Position] = {}
    if registry:
        for position in registry.values():
            key = _position_lookup_key(
                position.instrument_key,
                position.strategy_id,
                position.product,
            )
            lookup[key] = position

    deltas: list[FillDelta] = []
    for state in tracker.leg_states:
        if state.filled_quantity <= 0:
            continue
        if state.lifecycle_status not in _FILL_BEARING_STATUSES:
            continue
        fill_id = _compute_fill_id(state, tracker.submission_id)
        if fill_id in previously_applied:
            continue
        price = state.average_fill_price or _price_from_metadata(state)
        lookup_key = _position_lookup_key(
            state.instrument_key,
            state.metadata.get("strategy_id", "unknown-strategy"),
            state.product,
        )
        existing = lookup.get(lookup_key)
        deltas.append(
            FillDelta(
                fill_id=fill_id,
                leg_index=state.leg_index,
                instrument_key=state.instrument_key,
                side=state.side,
                product=state.product,
                fill_quantity=state.filled_quantity,
                fill_price=price,
                cumulative_filled=state.filled_quantity,
                planned_quantity=state.planned_quantity,
                is_exit=_determine_is_exit(state.side, existing, state.metadata),
                plan_id=state.metadata.get("plan_id", tracker.plan_id),
                strategy_id=state.metadata.get("strategy_id", "unknown-strategy"),
                strategy_family=_parse_strategy_family(state.metadata),
                correlation_id=tracker.correlation_id,
                submission_id=tracker.submission_id,
                occurred_at=tracker.completed_at or tracker.started_at,
                underlying=state.metadata.get("underlying", ""),
            )
        )
    return tuple(sorted(deltas, key=lambda item: (item.occurred_at, item.leg_index)))


def validate_update_context(
    context: PositionUpdateContext,
    tracker: OrderTracker,
    config: PositionManagerConfig,
) -> PositionValidationResult:
    """Validate context and tracker before position mutation."""
    errors: list[PositionErrorRecord] = []
    warnings: list[PositionWarningRecord] = []

    if not _is_timezone_aware(context.reference_time):
        errors.append(
            PositionErrorRecord(
                code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
                message="reference_time must be timezone-aware.",
                field="reference_time",
            )
        )
    if config.strict_correlation and context.correlation_id != tracker.correlation_id:
        errors.append(
            PositionErrorRecord(
                code=ERROR_CONTEXT_CORRELATION_MISMATCH,
                message="correlation_id mismatch between context and tracker.",
                field="correlation_id",
            )
        )
    if context.execution_mode not in StrategyExecutionMode:
        errors.append(
            PositionErrorRecord(
                code=ERROR_CONTEXT_INVALID,
                message="execution_mode is invalid.",
                field="execution_mode",
            )
        )
    return PositionValidationResult(
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def compute_snapshot_fingerprint(
    snapshot: PositionSnapshot,
    config: PositionManagerConfig,
) -> str:
    """Compute deterministic snapshot fingerprint."""
    payload = {
        "as_of": _datetime_to_iso(snapshot.as_of),
        "position_outcomes": [
            {
                "position_id": position.position_id,
                "instrument_key": position.instrument_key,
                "quantity": position.quantity,
                "lifecycle_state": position.lifecycle_state.value,
                "average_entry_price": position.average_entry_price,
                "realized_pnl": position.realized_pnl,
                "unrealized_pnl": position.unrealized_pnl,
            }
            for position in sorted(snapshot.positions, key=lambda item: item.position_id)
        ],
        "aggregate_unrealized_pnl": snapshot.aggregate_unrealized_pnl,
        "config_hash": config_fingerprint(config),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_update_fingerprint(
    tracker: OrderTracker | None,
    snapshot: PositionSnapshot,
    config: PositionManagerConfig,
) -> str:
    """Compute SHA-256 update fingerprint for replay verification."""
    payload = {
        "tracker_fingerprint": tracker.tracker_fingerprint if tracker else "",
        "position_outcomes": [
            {
                "position_id": position.position_id,
                "instrument_key": position.instrument_key,
                "quantity": position.quantity,
                "lifecycle_state": position.lifecycle_state.value,
                "average_entry_price": position.average_entry_price,
                "realized_pnl": position.realized_pnl,
                "unrealized_pnl": position.unrealized_pnl,
            }
            for position in sorted(snapshot.positions, key=lambda item: item.position_id)
        ],
        "aggregate_unrealized_pnl": snapshot.aggregate_unrealized_pnl,
        "config_hash": config_fingerprint(config),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def validate_position_update_result(
    result: PositionUpdateResult,
) -> PositionValidationResult:
    """Validate sealed update result."""
    errors: list[PositionErrorRecord] = []
    warnings: list[PositionWarningRecord] = []

    if not result.update_id:
        errors.append(
            PositionErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="update_id must be non-empty.",
                field="update_id",
            )
        )
    for position in result.snapshot.positions:
        if position.lifecycle_state in _OPEN_LIFECYCLE and position.quantity <= 0:
            errors.append(
                PositionErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="Open positions must have positive quantity.",
                    position_id=position.position_id,
                )
            )
        if position.lifecycle_state is PositionLifecycleState.CLOSED and position.quantity != 0:
            errors.append(
                PositionErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="Closed positions in snapshot must not appear open.",
                    position_id=position.position_id,
                )
            )
    return PositionValidationResult(errors=tuple(errors), warnings=tuple(warnings))


def assert_valid_position_update_result(result: PositionUpdateResult) -> None:
    """Raise PositionManagerValidationError when result is invalid."""
    validation = validate_position_update_result(result)
    if not validation.is_valid:
        primary = validation.errors[0]
        raise PositionManagerValidationError(
            primary.message,
            code=primary.code,
            field=primary.field,
            position_id=primary.position_id,
        )


def _position_to_dict(position: Position) -> dict[str, Any]:
    """Serialize position to dictionary."""
    return {
        "position_id": position.position_id,
        "position_group_id": position.position_group_id,
        "instrument_key": position.instrument_key,
        "side": position.side.value,
        "product": position.product.value,
        "quantity": position.quantity,
        "average_entry_price": position.average_entry_price,
        "cost_basis": position.cost_basis,
        "lifecycle_state": position.lifecycle_state.value,
        "strategy_id": position.strategy_id,
        "strategy_family": position.strategy_family.value,
        "realized_pnl": position.realized_pnl,
        "unrealized_pnl": position.unrealized_pnl,
        "transitions": [
            {
                "from_state": transition.from_state.value,
                "to_state": transition.to_state.value,
                "occurred_at": _datetime_to_iso(transition.occurred_at),
                "reason_code": transition.reason_code,
                "message": transition.message,
                "quantity_before": transition.quantity_before,
                "quantity_after": transition.quantity_after,
                "fill_id": transition.fill_id,
            }
            for transition in position.transitions
        ],
        "metadata": dict(sorted(position.metadata.items())),
    }


def _position_from_dict(data: Mapping[str, Any]) -> Position:
    """Deserialize position from dictionary."""
    transitions = tuple(
        PositionTransition(
            from_state=PositionLifecycleState(item["from_state"]),
            to_state=PositionLifecycleState(item["to_state"]),
            occurred_at=_datetime_from_iso(str(item["occurred_at"])),
            reason_code=str(item["reason_code"]),
            message=str(item["message"]),
            quantity_before=int(item["quantity_before"]),
            quantity_after=int(item["quantity_after"]),
            fill_id=str(item["fill_id"]) if item.get("fill_id") else None,
        )
        for item in data.get("transitions", [])
    )
    return Position(
        position_id=str(data["position_id"]),
        position_group_id=str(data["position_group_id"]) if data.get("position_group_id") else None,
        instrument_key=str(data["instrument_key"]),
        side=PositionSide(str(data["side"])),
        product=ProductType(str(data["product"])),
        quantity=int(data["quantity"]),
        average_entry_price=float(data["average_entry_price"]),
        cost_basis=float(data["cost_basis"]),
        lifecycle_state=PositionLifecycleState(str(data["lifecycle_state"])),
        strategy_id=str(data["strategy_id"]),
        strategy_family=StrategyFamily(str(data["strategy_family"])),
        realized_pnl=float(data["realized_pnl"]),
        unrealized_pnl=float(data["unrealized_pnl"]),
        transitions=transitions,
        metadata=MappingProxyType(dict(data.get("metadata", {}))),
    )


def position_update_result_to_dict(result: PositionUpdateResult) -> dict[str, Any]:
    """Convert update result to serializable dictionary."""
    return {
        "schema_version": POSITION_SCHEMA_VERSION,
        "update_id": result.update_id,
        "tracker_submission_id": result.tracker_submission_id,
        "correlation_id": result.correlation_id,
        "status": result.status.value,
        "snapshot": {
            "snapshot_id": result.snapshot.snapshot_id,
            "as_of": _datetime_to_iso(result.snapshot.as_of),
            "account_id": result.snapshot.account_id,
            "positions": [_position_to_dict(position) for position in result.snapshot.positions],
            "open_position_count": result.snapshot.open_position_count,
            "aggregate_quantity_by_underlying": dict(
                sorted(result.snapshot.aggregate_quantity_by_underlying.items())
            ),
            "aggregate_unrealized_pnl": result.snapshot.aggregate_unrealized_pnl,
            "aggregate_realized_pnl_session": result.snapshot.aggregate_realized_pnl_session,
            "snapshot_fingerprint": result.snapshot.snapshot_fingerprint,
        },
        "updated_positions": [_position_to_dict(position) for position in result.updated_positions],
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
                "position_id": warning.position_id,
                "stage_id": warning.stage_id.value if warning.stage_id else None,
                "field": warning.field,
            }
            for warning in result.warnings
        ],
        "errors": [
            {
                "code": error.code,
                "message": error.message,
                "position_id": error.position_id,
                "stage_id": error.stage_id.value if error.stage_id else None,
                "field": error.field,
                "leg_index": error.leg_index,
            }
            for error in result.errors
        ],
        "primary_error_code": result.primary_error_code,
        "submitted_at": _datetime_to_iso(result.submitted_at),
        "completed_at": _datetime_to_iso(result.completed_at) if result.completed_at else None,
        "duration_ms": result.duration_ms,
        "update_fingerprint": result.update_fingerprint,
    }


def position_update_result_from_dict(data: Mapping[str, Any]) -> PositionUpdateResult:
    """Deserialize update result from dictionary."""
    schema = data.get("schema_version")
    if schema != POSITION_SCHEMA_VERSION:
        raise PositionManagerValidationError(
            f"Unsupported schema version: {schema}",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
        )
    snapshot_data = data["snapshot"]
    positions = tuple(_position_from_dict(item) for item in snapshot_data["positions"])
    snapshot = PositionSnapshot(
        snapshot_id=str(snapshot_data["snapshot_id"]),
        as_of=_datetime_from_iso(str(snapshot_data["as_of"])),
        account_id=str(snapshot_data["account_id"]) if snapshot_data.get("account_id") else None,
        positions=positions,
        open_position_count=int(snapshot_data["open_position_count"]),
        aggregate_quantity_by_underlying=MappingProxyType(
            dict(snapshot_data.get("aggregate_quantity_by_underlying", {}))
        ),
        aggregate_unrealized_pnl=float(snapshot_data["aggregate_unrealized_pnl"]),
        aggregate_realized_pnl_session=float(snapshot_data["aggregate_realized_pnl_session"]),
        snapshot_fingerprint=str(snapshot_data["snapshot_fingerprint"]),
    )
    pipeline_data = data["pipeline_summary"]
    stages = tuple(
        PositionStageResult(
            stage_id=PositionUpdateStageId(item["stage_id"]),
            passed=bool(item["passed"]),
            rejection_code=str(item["rejection_code"]) if item.get("rejection_code") else None,
            message=str(item["message"]) if item.get("message") else None,
            duration_ms=float(item["duration_ms"]),
        )
        for item in pipeline_data["stages"]
    )
    pipeline = PositionPipelineResult(
        total_stages=int(pipeline_data["total_stages"]),
        passed_stages=int(pipeline_data["passed_stages"]),
        failed_stage_id=(
            PositionUpdateStageId(pipeline_data["failed_stage_id"])
            if pipeline_data.get("failed_stage_id")
            else None
        ),
        stages=stages,
        short_circuited=bool(pipeline_data["short_circuited"]),
    )
    completed_raw = data.get("completed_at")
    return PositionUpdateResult(
        update_id=str(data["update_id"]),
        tracker_submission_id=(
            str(data["tracker_submission_id"]) if data.get("tracker_submission_id") else None
        ),
        correlation_id=str(data["correlation_id"]),
        status=PositionUpdateStatus(str(data["status"])),
        snapshot=snapshot,
        updated_positions=tuple(_position_from_dict(item) for item in data["updated_positions"]),
        pipeline_summary=pipeline,
        warnings=tuple(
            PositionWarningRecord(
                code=str(item["code"]),
                message=str(item["message"]),
                position_id=str(item["position_id"]) if item.get("position_id") else None,
                stage_id=(
                    PositionUpdateStageId(item["stage_id"]) if item.get("stage_id") else None
                ),
                field=str(item["field"]) if item.get("field") else None,
            )
            for item in data.get("warnings", [])
        ),
        errors=tuple(
            PositionErrorRecord(
                code=str(item["code"]),
                message=str(item["message"]),
                position_id=str(item["position_id"]) if item.get("position_id") else None,
                stage_id=(
                    PositionUpdateStageId(item["stage_id"]) if item.get("stage_id") else None
                ),
                field=str(item["field"]) if item.get("field") else None,
                leg_index=int(item["leg_index"]) if item.get("leg_index") is not None else None,
            )
            for item in data.get("errors", [])
        ),
        primary_error_code=str(data["primary_error_code"]) if data.get("primary_error_code") else None,
        submitted_at=_datetime_from_iso(str(data["submitted_at"])),
        completed_at=_datetime_from_iso(completed_raw) if completed_raw else None,
        duration_ms=float(data["duration_ms"]),
        update_fingerprint=str(data["update_fingerprint"]),
    )


def serialize_position_update_result(result: PositionUpdateResult) -> str:
    """Serialize update result to canonical JSON."""
    return _canonical_json(position_update_result_to_dict(result))


def deserialize_position_update_result(payload: str) -> PositionUpdateResult:
    """Deserialize update result from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PositionManagerValidationError(
            "Malformed JSON payload.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(data, dict):
        raise PositionManagerValidationError(
            "JSON payload must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return position_update_result_from_dict(data)


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
        self._pending: list[PositionEvent] = []

    def publish(
        self,
        event_type: PositionEventType,
        *,
        occurred_at: datetime,
        position: Position | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Queue lifecycle event for ordered dispatch."""
        event = PositionEvent(
            event_type=event_type,
            topic=event_type.topic,
            update_id=self._update_id,
            correlation_id=self._correlation_id,
            occurred_at=occurred_at,
            position_id=position.position_id if position else None,
            position=position,
            metadata=MappingProxyType(dict(metadata or {})),
        )
        self._pending.append(event)

    def flush(self) -> tuple[PositionEvent, ...]:
        """Publish queued events in order."""
        if not self._enabled or self._event_bus is None:
            self._pending.clear()
            return ()
        published: list[PositionEvent] = []
        for event in self._pending:
            envelope = EventEnvelope(
                event_id=str(uuid.uuid4()),
                topic=event.topic,
                payload=event,
                correlation_id=self._correlation_id,
                producer=PRODUCER_NAME,
                occurred_at=event.occurred_at,
                published_at=_utc_now(),
                producer_version=POSITION_MANAGER_VERSION,
                payload_type="PositionEvent",
            )
            self._event_bus.publish(envelope)
            published.append(event)
        self._pending.clear()
        return tuple(published)


class PositionUpdatePipeline:
    """Stateless multi-stage position update pipeline."""

    def execute(
        self,
        run_state: _PipelineRunState,
        config: PositionManagerConfig,
        *,
        event_bus: EventBus | None = None,
    ) -> tuple[PositionUpdateResult, tuple[PositionEvent, ...]]:
        """Execute full update pipeline."""
        publisher = _EventPublisher(
            event_bus,
            enabled=config.publish_lifecycle_events,
            update_id=run_state.update_id,
            correlation_id=run_state.context.correlation_id,
        )
        stages: list[PositionStageResult] = []
        short_circuit = False

        for stage_id in STAGE_ORDER:
            if short_circuit and stage_id not in (
                PositionUpdateStageId.RESULT_ASSEMBLY,
                PositionUpdateStageId.OUTPUT_VALIDATION,
            ):
                continue
            stage_started = time.perf_counter()
            outcome = self._run_stage(stage_id, run_state, publisher)
            duration_ms = (time.perf_counter() - stage_started) * 1000.0
            stages.append(
                PositionStageResult(
                    stage_id=stage_id,
                    passed=outcome.passed,
                    rejection_code=outcome.rejection_code,
                    message=outcome.message,
                    duration_ms=duration_ms,
                    details=outcome.details,
                )
            )
            if not outcome.passed and stage_id in (
                PositionUpdateStageId.INPUT_GATE,
                PositionUpdateStageId.TRACKER_INTEGRITY,
                PositionUpdateStageId.PRE_UPDATE_VALIDATION,
            ):
                run_state.pre_update_rejected = True
                run_state.primary_error_code = outcome.rejection_code
                run_state.status = PositionUpdateStatus.REJECTED
                run_state.errors.append(
                    PositionErrorRecord(
                        code=outcome.rejection_code or ERROR_RESULT_INVALID,
                        message=outcome.message or "Stage failed.",
                        stage_id=stage_id,
                    )
                )
                publisher.publish(
                    PositionEventType.UPDATE_REJECTED,
                    occurred_at=run_state.context.reference_time,
                    metadata=MappingProxyType(
                        {"error_code": outcome.rejection_code or ERROR_RESULT_INVALID}
                    ),
                )
                short_circuit = True
            elif not outcome.passed and stage_id is PositionUpdateStageId.POSITION_APPLICATION:
                run_state.status = PositionUpdateStatus.PARTIAL
                short_circuit = not config.continue_on_leg_error

            _logger.debug(
                "position_manager.update.stage",
                extra={
                    "event": "position_manager.update.stage",
                    "stage_id": stage_id.value,
                    "passed": outcome.passed,
                },
            )

        if run_state.tracker and not run_state.pre_update_rejected:
            publisher.publish(
                PositionEventType.UPDATE_RECEIVED,
                occurred_at=run_state.context.reference_time,
                metadata=MappingProxyType(
                    {
                        "submission_id": run_state.tracker.submission_id,
                        "leg_count": str(len(run_state.tracker.leg_states)),
                    }
                ),
            )

        snapshot = _assemble_snapshot(run_state, config)
        updated_positions = tuple(
            run_state.registry[position_id]
            for position_id in sorted(run_state.updated_position_ids)
            if position_id in run_state.registry
        )

        if (
            not run_state.pre_update_rejected
            and not updated_positions
            and run_state.status is PositionUpdateStatus.APPLIED
        ):
            if run_state.errors:
                run_state.status = PositionUpdateStatus.PARTIAL
            else:
                run_state.status = PositionUpdateStatus.NOOP
        elif run_state.errors and run_state.status is PositionUpdateStatus.APPLIED:
            run_state.status = PositionUpdateStatus.PARTIAL

        fingerprint = (
            compute_update_fingerprint(run_state.tracker, snapshot, config)
            if config.deterministic_fingerprint
            else ""
        )
        completed_at = run_state.context.reference_time
        duration_ms = (completed_at - run_state.started_at).total_seconds() * 1000.0

        if run_state.status not in (PositionUpdateStatus.REJECTED, PositionUpdateStatus.NOOP):
            publisher.publish(
                PositionEventType.SNAPSHOT_PUBLISHED,
                occurred_at=completed_at,
                metadata=MappingProxyType(
                    {
                        "snapshot_id": snapshot.snapshot_id,
                        "open_count": str(snapshot.open_position_count),
                    }
                ),
            )

        pipeline_summary = PositionPipelineResult(
            total_stages=len(stages),
            passed_stages=sum(1 for stage in stages if stage.passed),
            failed_stage_id=next(
                (stage.stage_id for stage in stages if not stage.passed),
                None,
            ),
            stages=tuple(stages),
            short_circuited=short_circuit,
        )

        result = PositionUpdateResult(
            update_id=run_state.update_id,
            tracker_submission_id=(
                run_state.tracker.submission_id if run_state.tracker else None
            ),
            correlation_id=run_state.context.correlation_id,
            status=run_state.status,
            snapshot=snapshot,
            updated_positions=updated_positions,
            pipeline_summary=pipeline_summary,
            warnings=tuple(run_state.warnings),
            errors=tuple(run_state.errors),
            primary_error_code=run_state.primary_error_code,
            submitted_at=run_state.started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            update_fingerprint=fingerprint,
        )

        if config.strict_output_validation:
            validation = validate_position_update_result(result)
            if not validation.is_valid:
                run_state.primary_error_code = validation.errors[0].code
                result = replace(
                    result,
                    status=PositionUpdateStatus.FAILED,
                    errors=result.errors + validation.errors,
                    primary_error_code=validation.errors[0].code,
                )
            elif config.deterministic_fingerprint:
                recomputed = compute_update_fingerprint(run_state.tracker, snapshot, config)
                if recomputed != result.update_fingerprint:
                    mismatch = PositionErrorRecord(
                        code=ERROR_RESULT_FINGERPRINT_MISMATCH,
                        message="Update fingerprint mismatch.",
                        stage_id=PositionUpdateStageId.OUTPUT_VALIDATION,
                    )
                    result = replace(
                        result,
                        status=PositionUpdateStatus.FAILED,
                        errors=result.errors + (mismatch,),
                        primary_error_code=ERROR_RESULT_FINGERPRINT_MISMATCH,
                    )
                    if config.strict_output_validation:
                        assert_valid_position_update_result(result)

        publisher.publish(
            PositionEventType.UPDATE_COMPLETED,
            occurred_at=completed_at,
            metadata=MappingProxyType({"status": result.status.value}),
        )
        events = publisher.flush()
        return result, events

    def _run_stage(
        self,
        stage_id: PositionUpdateStageId,
        run_state: _PipelineRunState,
        publisher: _EventPublisher,
    ) -> _StageOutcome:
        """Execute one pipeline stage."""
        if stage_id is PositionUpdateStageId.INPUT_GATE:
            return _stage_input_gate(run_state)
        if stage_id is PositionUpdateStageId.TRACKER_INTEGRITY:
            return _stage_tracker_integrity(run_state)
        if stage_id is PositionUpdateStageId.FILL_EXTRACTION:
            return _stage_fill_extraction(run_state)
        if stage_id is PositionUpdateStageId.PRE_UPDATE_VALIDATION:
            return _stage_pre_update_validation(run_state)
        if stage_id is PositionUpdateStageId.POSITION_APPLICATION:
            return _stage_position_application(run_state, publisher)
        if stage_id is PositionUpdateStageId.PNL_RECOMPUTATION:
            return _stage_pnl_recomputation(run_state, publisher)
        if stage_id is PositionUpdateStageId.SNAPSHOT_ASSEMBLY:
            return _StageOutcome(passed=True)
        if stage_id is PositionUpdateStageId.RESULT_ASSEMBLY:
            return _StageOutcome(passed=True)
        if stage_id is PositionUpdateStageId.OUTPUT_VALIDATION:
            return _StageOutcome(passed=True)
        return _StageOutcome(passed=False, rejection_code=ERROR_RESULT_INVALID, message="Unknown stage.")


def _stage_input_gate(run_state: _PipelineRunState) -> _StageOutcome:
    """Validate tracker and context at input gate."""
    if run_state.tracker is None:
        return _StageOutcome(
            passed=False,
            rejection_code=ERROR_TRACKER_MISSING,
            message="OrderTracker is required.",
        )
    validation = validate_update_context(
        run_state.context,
        run_state.tracker,
        run_state.config,
    )
    if not validation.is_valid:
        primary = validation.errors[0]
        return _StageOutcome(
            passed=False,
            rejection_code=primary.code,
            message=primary.message,
        )
    return _StageOutcome(passed=True)


def _stage_tracker_integrity(run_state: _PipelineRunState) -> _StageOutcome:
    """Validate tracker structural integrity."""
    tracker = run_state.tracker
    assert tracker is not None
    if not tracker.submission_id:
        return _StageOutcome(
            passed=False,
            rejection_code=ERROR_TRACKER_INVALID,
            message="submission_id must be non-empty.",
        )
    if not tracker.leg_states:
        return _StageOutcome(
            passed=False,
            rejection_code=ERROR_TRACKER_NO_LEGS,
            message="tracker.leg_states must be non-empty.",
        )
    indices = [state.leg_index for state in tracker.leg_states]
    if len(indices) != len(set(indices)):
        return _StageOutcome(
            passed=False,
            rejection_code=ERROR_TRACKER_INVALID,
            message="leg_states indices must be unique.",
        )
    for state in tracker.leg_states:
        if state.filled_quantity > state.planned_quantity:
            return _StageOutcome(
                passed=False,
                rejection_code=ERROR_TRACKER_INVALID,
                message="filled_quantity exceeds planned_quantity.",
                details=MappingProxyType({"leg_index": state.leg_index}),
            )
        if (
            state.lifecycle_status is OrderLifecycleStatus.COMPLETE
            and state.filled_quantity != state.planned_quantity
        ):
            run_state.warnings.append(
                PositionWarningRecord(
                    code=ERROR_TRACKER_INVALID,
                    message=f"COMPLETE leg filled_quantity mismatch (leg_index={state.leg_index}).",
                    stage_id=PositionUpdateStageId.TRACKER_INTEGRITY,
                )
            )
    run_state.plan_leg_counts[tracker.plan_id] = len(tracker.leg_states)
    return _StageOutcome(passed=True)


def _stage_fill_extraction(run_state: _PipelineRunState) -> _StageOutcome:
    """Extract fill deltas from tracker."""
    tracker = run_state.tracker
    assert tracker is not None
    previously = frozenset(run_state.applied_fills)
    deltas = extract_fill_deltas(
        tracker,
        previously_applied=previously,
        registry=run_state.registry,
    )
    run_state.fill_deltas = deltas
    if not deltas:
        has_fills = any(state.filled_quantity > 0 for state in tracker.leg_states)
        if not has_fills:
            run_state.status = PositionUpdateStatus.NOOP
        else:
            run_state.status = PositionUpdateStatus.NOOP
    return _StageOutcome(passed=True, details=MappingProxyType({"delta_count": len(deltas)}))


def _stage_pre_update_validation(run_state: _PipelineRunState) -> _StageOutcome:
    """Validate fill deltas before application."""
    if not run_state.fill_deltas:
        return _StageOutcome(passed=True)
    for delta in run_state.fill_deltas:
        if not delta.instrument_key:
            run_state.errors.append(
                PositionErrorRecord(
                    code=ERROR_FILL_INVALID_INSTRUMENT,
                    message="instrument_key must be non-empty.",
                    leg_index=delta.leg_index,
                    stage_id=PositionUpdateStageId.PRE_UPDATE_VALIDATION,
                )
            )
            return _StageOutcome(
                passed=False,
                rejection_code=ERROR_FILL_INVALID_INSTRUMENT,
                message="Empty instrument_key.",
            )
        if delta.fill_quantity <= 0:
            run_state.errors.append(
                PositionErrorRecord(
                    code=ERROR_FILL_INVALID_QUANTITY,
                    message="fill_quantity must be positive.",
                    leg_index=delta.leg_index,
                    stage_id=PositionUpdateStageId.PRE_UPDATE_VALIDATION,
                )
            )
            return _StageOutcome(
                passed=False,
                rejection_code=ERROR_FILL_INVALID_QUANTITY,
                message="Invalid fill quantity.",
            )
        if delta.fill_price <= 0:
            run_state.errors.append(
                PositionErrorRecord(
                    code=ERROR_FILL_INVALID_PRICE,
                    message="fill_price must be positive.",
                    leg_index=delta.leg_index,
                    stage_id=PositionUpdateStageId.PRE_UPDATE_VALIDATION,
                )
            )
            return _StageOutcome(
                passed=False,
                rejection_code=ERROR_FILL_INVALID_PRICE,
                message="Invalid fill price.",
            )
        if delta.strategy_id == "unknown-strategy":
            run_state.warnings.append(
                PositionWarningRecord(
                    code=WARN_STRATEGY_MISSING,
                    message="strategy_id missing in order metadata.",
                    stage_id=PositionUpdateStageId.PRE_UPDATE_VALIDATION,
                )
            )
    if run_state.context.price_hints:
        return _StageOutcome(passed=True)
    for position in run_state.registry.values():
        if position.quantity > 0 and position.instrument_key not in run_state.context.price_hints:
            run_state.warnings.append(
                PositionWarningRecord(
                    code=WARN_PRICE_HINT_MISSING,
                    message="No mark price hint for open position.",
                    position_id=position.position_id,
                    stage_id=PositionUpdateStageId.PRE_UPDATE_VALIDATION,
                )
            )
    return _StageOutcome(passed=True)


def _find_position(
    registry: Mapping[str, Position],
    delta: FillDelta,
) -> Position | None:
    """Find existing position for fill delta."""
    lookup_key = _position_lookup_key(delta.instrument_key, delta.strategy_id, delta.product)
    for position in registry.values():
        if (
            _position_lookup_key(position.instrument_key, position.strategy_id, position.product)
            == lookup_key
        ):
            return position
    return None


def _append_transition(
    position: Position,
    *,
    to_state: PositionLifecycleState,
    occurred_at: datetime,
    reason_code: str,
    message: str,
    quantity_before: int,
    quantity_after: int,
    fill_id: str | None,
) -> Position:
    """Return position with appended transition."""
    transition = PositionTransition(
        from_state=position.lifecycle_state,
        to_state=to_state,
        occurred_at=occurred_at,
        reason_code=reason_code,
        message=message,
        quantity_before=quantity_before,
        quantity_after=quantity_after,
        fill_id=fill_id,
    )
    return replace(
        position,
        lifecycle_state=to_state,
        transitions=position.transitions + (transition,),
    )


def _apply_entry_fill(
    position: Position | None,
    delta: FillDelta,
    *,
    occurred_at: datetime,
    config: PositionManagerConfig,
) -> Position:
    """Apply entry fill to create or update position."""
    if position is None:
        side = _side_from_order_side(delta.side)
        avg_price = round(delta.fill_price, PRICE_ROUND_DECIMALS)
        qty = delta.fill_quantity
        metadata = MappingProxyType(
            {
                "plan_id": delta.plan_id,
                "correlation_id": delta.correlation_id,
                "opened_at": _datetime_to_iso(occurred_at),
                "underlying": delta.underlying
                or (
                    delta.instrument_key.split(":")[0]
                    if ":" in delta.instrument_key
                    else delta.instrument_key
                ),
            }
        )
        position_id = _generate_position_id(
            delta.instrument_key,
            delta.strategy_id,
            delta.product,
            delta.plan_id,
        )
        group_id = delta.plan_id if config.group_multi_leg_by_plan else None
        initial_state = (
            PositionLifecycleState.OPENING
            if config.group_multi_leg_by_plan
            else PositionLifecycleState.OPEN
        )
        position = Position(
            position_id=position_id,
            instrument_key=delta.instrument_key,
            side=side,
            product=delta.product,
            quantity=qty,
            average_entry_price=avg_price,
            cost_basis=round(avg_price * qty, PRICE_ROUND_DECIMALS),
            lifecycle_state=initial_state,
            strategy_id=delta.strategy_id,
            strategy_family=delta.strategy_family,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            transitions=(),
            metadata=metadata,
            position_group_id=group_id,
        )
        return _append_transition(
            position,
            to_state=initial_state,
            occurred_at=occurred_at,
            reason_code="PT-001",
            message="First entry fill received.",
            quantity_before=0,
            quantity_after=qty,
            fill_id=delta.fill_id,
        )

    qty_before = position.quantity
    new_qty = qty_before + delta.fill_quantity
    if new_qty > MAX_QUANTITY:
        raise PositionManagerUpdateError(
            "Quantity overflow.",
            code=ERROR_FILL_INVALID_QUANTITY,
        )
    new_avg = compute_new_average_entry_price(
        qty_before,
        position.average_entry_price,
        delta.fill_quantity,
        delta.fill_price,
    )
    new_state = position.lifecycle_state
    if position.lifecycle_state is PositionLifecycleState.PARTIALLY_CLOSED:
        new_state = PositionLifecycleState.OPEN
    updated = replace(
        position,
        quantity=new_qty,
        average_entry_price=new_avg,
        cost_basis=round(new_avg * new_qty, PRICE_ROUND_DECIMALS),
        lifecycle_state=new_state,
    )
    return _append_transition(
        updated,
        to_state=new_state,
        occurred_at=occurred_at,
        reason_code="PT-005",
        message="Entry fill increased quantity.",
        quantity_before=qty_before,
        quantity_after=new_qty,
        fill_id=delta.fill_id,
    )


def _apply_exit_fill(
    position: Position | None,
    delta: FillDelta,
    *,
    occurred_at: datetime,
    config: PositionManagerConfig,
) -> tuple[Position, float]:
    """Apply exit fill; return updated position and realized P&L delta."""
    if position is None or position.quantity <= 0:
        if config.allow_orphan_exits:
            return (
                Position(
                    position_id=_generate_position_id(
                        delta.instrument_key,
                        delta.strategy_id,
                        delta.product,
                        delta.plan_id,
                    ),
                    instrument_key=delta.instrument_key,
                    side=_side_from_order_side(delta.side),
                    product=delta.product,
                    quantity=0,
                    average_entry_price=0.0,
                    cost_basis=0.0,
                    lifecycle_state=PositionLifecycleState.CLOSED,
                    strategy_id=delta.strategy_id,
                    strategy_family=delta.strategy_family,
                    realized_pnl=0.0,
                    unrealized_pnl=0.0,
                    transitions=(),
                    metadata=MappingProxyType({"orphan_exit": "true"}),
                ),
                0.0,
            )
        raise PositionManagerUpdateError(
            "No open position for exit fill.",
            code=ERROR_POSITION_NOT_FOUND,
            position_id=None,
        )

    if delta.fill_quantity > position.quantity:
        raise PositionManagerUpdateError(
            "Exit quantity exceeds open quantity.",
            code=ERROR_FILL_OVER_EXIT,
            position_id=position.position_id,
        )

    qty_before = position.quantity
    qty_after = qty_before - delta.fill_quantity
    realized_delta = compute_realized_pnl_delta(
        position.side,
        position.average_entry_price,
        delta.fill_quantity,
        delta.fill_price,
    )
    new_realized = round(position.realized_pnl + realized_delta, PNL_ROUND_DECIMALS)

    if qty_after == 0:
        new_state = PositionLifecycleState.CLOSED
        metadata = dict(position.metadata)
        metadata["closed_at"] = _datetime_to_iso(occurred_at)
        updated = replace(
            position,
            quantity=0,
            cost_basis=0.0,
            lifecycle_state=new_state,
            realized_pnl=new_realized,
            unrealized_pnl=0.0,
            metadata=MappingProxyType(metadata),
        )
        updated = _append_transition(
            updated,
            to_state=new_state,
            occurred_at=occurred_at,
            reason_code="PT-008",
            message="Position fully closed.",
            quantity_before=qty_before,
            quantity_after=0,
            fill_id=delta.fill_id,
        )
        return updated, realized_delta

    new_state = PositionLifecycleState.PARTIALLY_CLOSED
    new_avg = position.average_entry_price
    updated = replace(
        position,
        quantity=qty_after,
        cost_basis=round(new_avg * qty_after, PRICE_ROUND_DECIMALS),
        lifecycle_state=new_state,
        realized_pnl=new_realized,
    )
    updated = _append_transition(
        updated,
        to_state=new_state,
        occurred_at=occurred_at,
        reason_code="PT-003",
        message="Partial exit applied.",
        quantity_before=qty_before,
        quantity_after=qty_after,
        fill_id=delta.fill_id,
    )
    return updated, realized_delta


def _finalize_multi_leg_opening(
    run_state: _PipelineRunState,
    plan_id: str,
) -> None:
    """Transition OPENING positions to OPEN when all plan legs filled."""
    if not run_state.config.group_multi_leg_by_plan:
        return
    expected = run_state.plan_leg_counts.get(plan_id, 0)
    if expected <= 1:
        for position_id, position in list(run_state.registry.items()):
            if position.position_group_id == plan_id and position.lifecycle_state is PositionLifecycleState.OPENING:
                run_state.registry[position_id] = replace(
                    position,
                    lifecycle_state=PositionLifecycleState.OPEN,
                )
        return

    filled_legs = sum(
        1
        for position in run_state.registry.values()
        if position.position_group_id == plan_id and position.quantity > 0
    )
    if filled_legs >= expected:
        for position_id, position in list(run_state.registry.items()):
            if position.position_group_id == plan_id and position.lifecycle_state is PositionLifecycleState.OPENING:
                updated = replace(position, lifecycle_state=PositionLifecycleState.OPEN)
                run_state.registry[position_id] = _append_transition(
                    updated,
                    to_state=PositionLifecycleState.OPEN,
                    occurred_at=run_state.context.reference_time,
                    reason_code="PT-002",
                    message="Multi-leg entry complete.",
                    quantity_before=position.quantity,
                    quantity_after=position.quantity,
                    fill_id=None,
                )


def _stage_position_application(
    run_state: _PipelineRunState,
    publisher: _EventPublisher,
) -> _StageOutcome:
    """Apply fill deltas to positions."""
    if not run_state.fill_deltas:
        return _StageOutcome(passed=True)

    for delta in run_state.fill_deltas:
        try:
            existing = _find_position(run_state.registry, delta)
            if delta.is_exit:
                position, realized_delta = _apply_exit_fill(
                    existing,
                    delta,
                    occurred_at=delta.occurred_at,
                    config=run_state.config,
                )
                run_state.session_realized_pnl += realized_delta
                publisher.publish(
                    PositionEventType.POSITION_REALIZED_PNL,
                    occurred_at=delta.occurred_at,
                    position=position,
                    metadata=MappingProxyType(
                        {"realized_delta": str(realized_delta), "fill_id": delta.fill_id}
                    ),
                )
                if position.lifecycle_state is PositionLifecycleState.CLOSED:
                    publisher.publish(
                        PositionEventType.POSITION_CLOSED,
                        occurred_at=delta.occurred_at,
                        position=position,
                    )
                else:
                    publisher.publish(
                        PositionEventType.POSITION_PARTIAL_CLOSE,
                        occurred_at=delta.occurred_at,
                        position=position,
                        metadata=MappingProxyType(
                            {
                                "quantity_before": str(
                                    position.transitions[-1].quantity_before
                                ),
                                "quantity_after": str(position.quantity),
                            }
                        ),
                    )
            else:
                created = existing is None
                position = _apply_entry_fill(
                    existing,
                    delta,
                    occurred_at=delta.occurred_at,
                    config=run_state.config,
                )
                if created:
                    publisher.publish(
                        PositionEventType.POSITION_OPENED,
                        occurred_at=delta.occurred_at,
                        position=position,
                    )
                else:
                    publisher.publish(
                        PositionEventType.POSITION_PARTIAL_FILL,
                        occurred_at=delta.occurred_at,
                        position=position,
                        metadata=MappingProxyType({"fill_id": delta.fill_id}),
                    )
                    publisher.publish(
                        PositionEventType.POSITION_UPDATED,
                        occurred_at=delta.occurred_at,
                        position=position,
                    )

            run_state.registry[position.position_id] = position
            run_state.updated_position_ids.add(position.position_id)
            run_state.applied_fills.add(delta.fill_id)
            _finalize_multi_leg_opening(run_state, delta.plan_id)
            _logger.info(
                "position_manager.fill.applied",
                extra={
                    "event": "position_manager.fill.applied",
                    "position_id": position.position_id,
                    "fill_id": delta.fill_id,
                },
            )
        except PositionManagerUpdateError as exc:
            run_state.errors.append(
                PositionErrorRecord(
                    code=exc.code,
                    message=exc.message,
                    position_id=exc.position_id,
                    leg_index=delta.leg_index,
                    stage_id=PositionUpdateStageId.POSITION_APPLICATION,
                )
            )
            run_state.primary_error_code = exc.code
            if not run_state.config.continue_on_leg_error:
                return _StageOutcome(
                    passed=False,
                    rejection_code=exc.code,
                    message=exc.message,
                )

    return _StageOutcome(passed=True)


def _stage_pnl_recomputation(
    run_state: _PipelineRunState,
    publisher: _EventPublisher,
) -> _StageOutcome:
    """Recompute unrealized P&L for open positions."""
    hints = run_state.context.price_hints
    mode = run_state.context.execution_mode
    for position_id, position in list(run_state.registry.items()):
        if position.lifecycle_state in _TERMINAL_LIFECYCLE or position.quantity <= 0:
            continue
        mark = hints.get(position.instrument_key)
        if mark is None:
            if mode is StrategyExecutionMode.BACKTEST and run_state.config.partial_fill_terminal_in_backtest:
                continue
            continue
        new_unrealized = compute_unrealized_pnl(
            position.side,
            position.quantity,
            position.average_entry_price,
            mark,
        )
        if new_unrealized != position.unrealized_pnl:
            updated = replace(position, unrealized_pnl=new_unrealized)
            run_state.registry[position_id] = updated
            if position_id not in run_state.updated_position_ids:
                run_state.updated_position_ids.add(position_id)
            publisher.publish(
                PositionEventType.POSITION_PNL_UPDATED,
                occurred_at=run_state.context.reference_time,
                position=updated,
                metadata=MappingProxyType({"unrealized_pnl": str(new_unrealized)}),
            )
    if run_state.config.enable_broker_reconciliation:
        _check_broker_drift(run_state)
    return _StageOutcome(passed=True)


def _check_broker_drift(run_state: _PipelineRunState) -> None:
    """Compare registry against broker position records."""
    broker_map: dict[tuple[str, str], PositionRecord] = {}
    for record in run_state.context.broker_positions:
        broker_map[(record.instrument_key, record.product.value)] = record
    for position in run_state.registry.values():
        if position.quantity <= 0:
            continue
        broker = broker_map.get((position.instrument_key, position.product.value))
        if broker is None:
            continue
        if abs(broker.quantity) != position.quantity:
            run_state.warnings.append(
                PositionWarningRecord(
                    code=WARN_BROKER_DRIFT,
                    message="Broker position quantity mismatch.",
                    position_id=position.position_id,
                )
            )


def _extract_underlying(instrument_key: str, metadata: Mapping[str, str]) -> str:
    """Resolve underlying symbol for aggregation."""
    if "underlying" in metadata:
        return metadata["underlying"]
    if ":" in instrument_key:
        return instrument_key.split(":")[0]
    return instrument_key


def _assemble_snapshot(
    run_state: _PipelineRunState,
    config: PositionManagerConfig,
) -> PositionSnapshot:
    """Assemble position snapshot from registry."""
    open_positions = tuple(
        sorted(
            (
                position
                for position in run_state.registry.values()
                if position.lifecycle_state in _OPEN_LIFECYCLE and position.quantity > 0
            ),
            key=lambda item: item.position_id,
        )
    )
    aggregate_by_underlying: dict[str, int] = {}
    aggregate_unrealized = 0.0
    for position in open_positions:
        underlying = _extract_underlying(position.instrument_key, position.metadata)
        aggregate_by_underlying[underlying] = (
            aggregate_by_underlying.get(underlying, 0) + position.quantity
        )
        aggregate_unrealized = round(
            aggregate_unrealized + position.unrealized_pnl,
            PNL_ROUND_DECIMALS,
        )
    session_realized = (
        run_state.session_realized_pnl if config.session_realized_pnl_tracking else 0.0
    )
    as_of = run_state.context.reference_time
    snapshot = PositionSnapshot(
        snapshot_id=f"snap-{uuid.uuid4().hex[:16]}",
        as_of=as_of,
        account_id=run_state.context.account_id,
        positions=open_positions,
        open_position_count=len(open_positions),
        aggregate_quantity_by_underlying=MappingProxyType(
            dict(sorted(aggregate_by_underlying.items()))
        ),
        aggregate_unrealized_pnl=aggregate_unrealized,
        aggregate_realized_pnl_session=round(session_realized, PNL_ROUND_DECIMALS),
        snapshot_fingerprint="",
    )
    fingerprint = compute_snapshot_fingerprint(snapshot, config)
    return replace(snapshot, snapshot_fingerprint=fingerprint)


def _generate_update_id(context: PositionUpdateContext) -> str:
    """Generate update run identifier."""
    payload = f"{context.correlation_id}|{_datetime_to_iso(context.reference_time)}|update"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"pupd-{digest}"


def _build_rejected_result(
    context: PositionUpdateContext,
    config: PositionManagerConfig,
    *,
    error_code: str,
    message: str,
    tracker: OrderTracker | None = None,
) -> PositionUpdateResult:
    """Build rejected result without mutating registry."""
    as_of = context.reference_time if _is_timezone_aware(context.reference_time) else _utc_now()
    snapshot = PositionSnapshot(
        snapshot_id=f"snap-rejected-{uuid.uuid4().hex[:12]}",
        as_of=as_of,
        account_id=context.account_id,
        positions=(),
        open_position_count=0,
        aggregate_quantity_by_underlying=MappingProxyType({}),
        aggregate_unrealized_pnl=0.0,
        aggregate_realized_pnl_session=0.0,
        snapshot_fingerprint="",
    )
    update_id = f"pupd-rejected-{uuid.uuid4().hex[:12]}"
    return PositionUpdateResult(
        update_id=update_id,
        tracker_submission_id=tracker.submission_id if tracker else None,
        correlation_id=context.correlation_id,
        status=PositionUpdateStatus.REJECTED,
        snapshot=snapshot,
        updated_positions=(),
        pipeline_summary=PositionPipelineResult(
            total_stages=1,
            passed_stages=0,
            failed_stage_id=PositionUpdateStageId.INPUT_GATE,
            stages=(),
            short_circuited=True,
        ),
        warnings=(),
        errors=(PositionErrorRecord(code=error_code, message=message),),
        primary_error_code=error_code,
        submitted_at=as_of,
        completed_at=as_of,
        duration_ms=0.0,
        update_fingerprint="",
    )


class PositionManager:
    """Institutional live position tracking and P&L manager.

    Consumes OrderTracker artifacts from Order Manager, maintains immutable
    Position records, computes P&L, and publishes position.* lifecycle events.

    Args:
        config: Injected immutable configuration.
        event_bus: Optional EventBus for lifecycle event publishing.
    """

    def __init__(
        self,
        config: PositionManagerConfig | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._config = config or default_position_manager_config()
        self._event_bus = event_bus
        self._registry_lock = threading.RLock()
        self._registry: dict[str, Position] = {}
        self._applied_fills: set[str] = set()
        self._session_realized_pnl: float = 0.0
        self._plan_leg_counts: dict[str, int] = {}
        self._pipeline = PositionUpdatePipeline()

    @property
    def config(self) -> PositionManagerConfig:
        """Return manager configuration."""
        return self._config

    def apply_order_tracker(
        self,
        tracker: OrderTracker,
        context: PositionUpdateContext,
    ) -> PositionUpdateResult:
        """Apply fills from an OrderTracker snapshot to live positions."""
        _logger.info(
            "position_manager.update.start",
            extra={
                "event": "position_manager.update.start",
                "submission_id": tracker.submission_id,
            },
        )
        if not _is_timezone_aware(context.reference_time):
            _logger.info(
                "position_manager.update.rejected",
                extra={"event": "position_manager.update.rejected"},
            )
            return _build_rejected_result(
                context,
                self._config,
                error_code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
                message="reference_time must be timezone-aware.",
                tracker=tracker,
            )
        with self._registry_lock:
            run_state = _PipelineRunState(
                tracker=tracker,
                context=context,
                config=self._config,
                update_id=_generate_update_id(context),
                started_at=context.reference_time,
                registry=dict(self._registry),
                applied_fills=set(self._applied_fills),
                session_realized_pnl=self._session_realized_pnl,
                plan_leg_counts=dict(self._plan_leg_counts),
            )
            result, _events = self._pipeline.execute(
                run_state,
                self._config,
                event_bus=self._event_bus,
            )
            if result.status not in (PositionUpdateStatus.REJECTED, PositionUpdateStatus.FAILED):
                self._registry = run_state.registry
                self._applied_fills = run_state.applied_fills
                self._session_realized_pnl = run_state.session_realized_pnl
                self._plan_leg_counts.update(run_state.plan_leg_counts)
        _logger.info(
            "position_manager.update.complete",
            extra={
                "event": "position_manager.update.complete",
                "status": result.status.value,
                "update_id": result.update_id,
            },
        )
        return result

    def apply_fill_delta(
        self,
        delta: FillDelta,
        context: PositionUpdateContext,
    ) -> PositionUpdateResult:
        """Apply a single fill delta for event-driven updates."""
        synthetic_state = OrderState(
            leg_index=delta.leg_index,
            sequence_group=0,
            instrument_key=delta.instrument_key,
            side=delta.side,
            order_type=OrderType.MARKET,
            product=delta.product,
            planned_quantity=delta.planned_quantity,
            lifecycle_status=OrderLifecycleStatus.COMPLETE,
            idempotency_key=delta.fill_id,
            filled_quantity=delta.fill_quantity,
            remaining_quantity=max(0, delta.planned_quantity - delta.fill_quantity),
            average_fill_price=delta.fill_price,
            terminal=True,
            metadata=MappingProxyType(
                {
                    "plan_id": delta.plan_id,
                    "strategy_id": delta.strategy_id,
                    "strategy_family": delta.strategy_family.value,
                }
            ),
        )
        tracker = OrderTracker(
            submission_id=delta.submission_id,
            plan_id=delta.plan_id,
            correlation_id=delta.correlation_id,
            plan_fingerprint="",
            leg_states=(synthetic_state,),
            aggregate_status=OrderAggregateStatus.ALL_COMPLETE,
            sequence_results=(),
            started_at=delta.occurred_at,
            completed_at=delta.occurred_at,
            tracker_fingerprint="",
        )
        with self._registry_lock:
            run_state = _PipelineRunState(
                tracker=tracker,
                context=context,
                config=self._config,
                update_id=_generate_update_id(context),
                started_at=context.reference_time,
                registry=dict(self._registry),
                applied_fills=set(self._applied_fills),
                fill_deltas=(delta,),
                session_realized_pnl=self._session_realized_pnl,
                plan_leg_counts=dict(self._plan_leg_counts),
            )
            if delta.fill_id in run_state.applied_fills and self._config.idempotent_updates:
                snapshot = _assemble_snapshot(run_state, self._config)
                return PositionUpdateResult(
                    update_id=run_state.update_id,
                    tracker_submission_id=delta.submission_id,
                    correlation_id=context.correlation_id,
                    status=PositionUpdateStatus.NOOP,
                    snapshot=snapshot,
                    updated_positions=(),
                    pipeline_summary=PositionPipelineResult(
                        total_stages=0,
                        passed_stages=0,
                        failed_stage_id=None,
                        stages=(),
                        short_circuited=False,
                    ),
                    warnings=(),
                    errors=(),
                    primary_error_code=None,
                    submitted_at=context.reference_time,
                    completed_at=context.reference_time,
                    duration_ms=0.0,
                    update_fingerprint=compute_update_fingerprint(tracker, snapshot, self._config),
                )
            publisher = _EventPublisher(
                self._event_bus,
                enabled=self._config.publish_lifecycle_events,
                update_id=run_state.update_id,
                correlation_id=context.correlation_id,
            )
            _stage_pre_update_validation(run_state)
            _stage_position_application(run_state, publisher)
            _stage_pnl_recomputation(run_state, publisher)
            snapshot = _assemble_snapshot(run_state, self._config)
            updated_positions = tuple(
                run_state.registry[position_id]
                for position_id in sorted(run_state.updated_position_ids)
                if position_id in run_state.registry
            )
            status = (
                PositionUpdateStatus.NOOP
                if not updated_positions
                else PositionUpdateStatus.APPLIED
            )
            result = PositionUpdateResult(
                update_id=run_state.update_id,
                tracker_submission_id=delta.submission_id,
                correlation_id=context.correlation_id,
                status=status,
                snapshot=snapshot,
                updated_positions=updated_positions,
                pipeline_summary=PositionPipelineResult(
                    total_stages=3,
                    passed_stages=3,
                    failed_stage_id=None,
                    stages=(),
                    short_circuited=False,
                ),
                warnings=tuple(run_state.warnings),
                errors=tuple(run_state.errors),
                primary_error_code=run_state.primary_error_code,
                submitted_at=context.reference_time,
                completed_at=context.reference_time,
                duration_ms=0.0,
                update_fingerprint=compute_update_fingerprint(tracker, snapshot, self._config),
            )
            if status is not PositionUpdateStatus.REJECTED:
                self._registry = run_state.registry
                self._applied_fills = run_state.applied_fills
                self._session_realized_pnl = run_state.session_realized_pnl
            publisher.flush()
        return result

    def get_snapshot(self, *, as_of: datetime | None = None) -> PositionSnapshot:
        """Return immutable snapshot of all open positions."""
        with self._registry_lock:
            context = PositionUpdateContext(
                correlation_id="snapshot",
                reference_time=as_of or _utc_now(),
                execution_mode=StrategyExecutionMode.LIVE,
            )
            run_state = _PipelineRunState(
                tracker=None,
                context=context,
                config=self._config,
                update_id="snapshot-read",
                started_at=context.reference_time,
                registry=dict(self._registry),
                applied_fills=set(self._applied_fills),
                session_realized_pnl=self._session_realized_pnl,
            )
            return _assemble_snapshot(run_state, self._config)

    def get_position(self, position_id: str) -> Position | None:
        """Return position by ID if held in registry."""
        with self._registry_lock:
            return self._registry.get(position_id)

    def on_order_lifecycle_event(self, event: OrderLifecycleEvent) -> None:
        """Handle order lifecycle event for near-real-time position updates."""
        if event.order_state is None:
            return
        if event.event_type.topic not in (
            "order.leg.partial_fill",
            "order.leg.complete",
        ):
            return
        state = event.order_state
        if state.filled_quantity <= 0:
            return
        context = PositionUpdateContext(
            correlation_id=event.correlation_id,
            reference_time=event.occurred_at,
            execution_mode=StrategyExecutionMode.LIVE,
        )
        tracker = OrderTracker(
            submission_id=event.submission_id,
            plan_id=event.plan_id,
            correlation_id=event.correlation_id,
            plan_fingerprint="",
            leg_states=(state,),
            aggregate_status=OrderAggregateStatus.ALL_COMPLETE,
            sequence_results=(),
            started_at=event.occurred_at,
            completed_at=event.occurred_at,
            tracker_fingerprint="",
        )
        self.apply_order_tracker(tracker, context)

    def validate_update_context(
        self,
        context: PositionUpdateContext,
        tracker: OrderTracker,
    ) -> PositionValidationResult:
        """Validate context and tracker without mutating state."""
        return validate_update_context(context, tracker, self._config)

    def validate_update_result(
        self,
        result: PositionUpdateResult,
    ) -> PositionValidationResult:
        """Validate sealed update result."""
        return validate_position_update_result(result)


# Late import to avoid circular dependency at module load for apply_fill_delta.
from execution.order_manager import OrderAggregateStatus  # noqa: E402
