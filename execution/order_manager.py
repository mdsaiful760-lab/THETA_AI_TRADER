"""Institutional order submission and lifecycle management for THETA AI TRADER v1.0.

Consumes immutable :class:`ExecutionPlan` outputs and submits orders exclusively
via :class:`BaseBrokerClient`, executing retry policy, tracking lifecycle state,
and publishing ``order.*`` lifecycle events.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Final, Mapping

from broker.base_broker import (
    ERROR_AUTH_EXPIRED,
    ERROR_AUTH_INVALID,
    ERROR_CAPABILITY_UNSUPPORTED,
    ERROR_CONNECTION_DISCONNECTED,
    ERROR_ORDER_REJECTED,
    ERROR_RATE_LIMIT_EXCEEDED,
    ERROR_REQUEST_INVALID,
    ERROR_REQUEST_TIMEOUT,
    BaseBrokerClient,
    BrokerAuthenticationError,
    BrokerCapabilities,
    BrokerCapabilityError,
    BrokerClientError,
    BrokerOrderError,
    BrokerTimeoutError,
    CancelOrderRequest,
    ConnectionState,
    OrderQueryRequest,
    OrderRecord,
    OrderSide as BrokerOrderSide,
    OrderStatus,
    OrderType as BrokerOrderType,
    OrderVariety,
    PlaceOrderRequest,
    PlaceOrderResult,
    ProductType as BrokerProductType,
    SessionState,
    validate_place_order_request,
)
from core.event_bus import EventBus, EventEnvelope
from execution.execution_engine import (
    ExecutionPlan,
    ExecutionPlanStatus,
    LegSequence,
    LegSequenceMode,
    OrderSide,
    OrderType,
    PlannedOrderLeg,
    ProductType,
    RetryPolicy,
    validate_execution_plan,
)
from strategy.signals import StrategyExecutionMode

ORDER_MANAGER_VERSION: Final[str] = "1.0.0"
ORDER_STATE_SCHEMA_VERSION: Final[str] = "1.0.0"
DEFAULT_POLL_INTERVAL_MS: Final[int] = 500
DEFAULT_MAX_POLL_ATTEMPTS: Final[int] = 60
PRODUCER_NAME: Final[str] = "order_manager"
NEAR_EXPIRY_SECONDS: Final[int] = 15

ERROR_CONFIG_INVALID: Final[str] = "ORDER_MANAGER.CONFIG.INVALID"
ERROR_CONTEXT_INVALID: Final[str] = "ORDER_MANAGER.CONTEXT.INVALID"
ERROR_CONTEXT_NAIVE_TIMESTAMP: Final[str] = "ORDER_MANAGER.CONTEXT.NAIVE_TIMESTAMP"
ERROR_CONTEXT_CORRELATION_MISMATCH: Final[str] = "ORDER_MANAGER.CONTEXT.CORRELATION_MISMATCH"
ERROR_PLAN_NOT_READY: Final[str] = "ORDER_MANAGER.PLAN.NOT_READY"
ERROR_PLAN_NO_LEGS: Final[str] = "ORDER_MANAGER.PLAN.NO_LEGS"
ERROR_PLAN_EXPIRED: Final[str] = "ORDER_MANAGER.PLAN.EXPIRED"
ERROR_PLAN_INVALID: Final[str] = "ORDER_MANAGER.PLAN.INVALID"
ERROR_PLAN_INVALID_LEGS: Final[str] = "ORDER_MANAGER.PLAN.INVALID_LEGS"
ERROR_PLAN_NEAR_EXPIRY: Final[str] = "ORDER_MANAGER.PLAN.NEAR_EXPIRY"
ERROR_PLAN_STALE_LIMIT_HINT: Final[str] = "ORDER_MANAGER.PLAN.STALE_LIMIT_HINT"
ERROR_MAP_INVALID_INSTRUMENT: Final[str] = "ORDER_MANAGER.MAP.INVALID_INSTRUMENT"
ERROR_MAP_INVALID_QUANTITY: Final[str] = "ORDER_MANAGER.MAP.INVALID_QUANTITY"
ERROR_MAP_MISSING_LIMIT_PRICE: Final[str] = "ORDER_MANAGER.MAP.MISSING_LIMIT_PRICE"
ERROR_MAP_MISSING_TRIGGER_PRICE: Final[str] = "ORDER_MANAGER.MAP.MISSING_TRIGGER_PRICE"
ERROR_MAP_MISSING_IDEMPOTENCY_KEY: Final[str] = "ORDER_MANAGER.MAP.MISSING_IDEMPOTENCY_KEY"
ERROR_MAP_BROKER_VALIDATION_FAILED: Final[str] = "ORDER_MANAGER.MAP.BROKER_VALIDATION_FAILED"
ERROR_BROKER_MISSING: Final[str] = "ORDER_MANAGER.BROKER.MISSING"
ERROR_BROKER_NOT_CONNECTED: Final[str] = "ORDER_MANAGER.BROKER.NOT_CONNECTED"
ERROR_BROKER_NOT_AUTHENTICATED: Final[str] = "ORDER_MANAGER.BROKER.NOT_AUTHENTICATED"
ERROR_BROKER_SESSION_EXPIRED: Final[str] = "ORDER_MANAGER.BROKER.SESSION_EXPIRED"
ERROR_BROKER_PLACEMENT_UNSUPPORTED: Final[str] = "ORDER_MANAGER.BROKER.PLACEMENT_UNSUPPORTED"
ERROR_BROKER_AUTH_FAILED: Final[str] = "ORDER_MANAGER.BROKER.AUTH_FAILED"
ERROR_BROKER_CAPABILITY_UNSUPPORTED: Final[str] = "ORDER_MANAGER.BROKER.CAPABILITY_UNSUPPORTED"
ERROR_SEQUENCE_INVALID: Final[str] = "ORDER_MANAGER.SEQUENCE.INVALID"
ERROR_SEQUENCE_TIMEOUT: Final[str] = "ORDER_MANAGER.SEQUENCE.TIMEOUT"
ERROR_LEG_SUBMIT_FAILED: Final[str] = "ORDER_MANAGER.LEG.SUBMIT_FAILED"
ERROR_LEG_TIMEOUT: Final[str] = "ORDER_MANAGER.LEG.TIMEOUT"
ERROR_LEG_REJECTED: Final[str] = "ORDER_MANAGER.LEG.REJECTED"
ERROR_LEG_CANCEL_FAILED: Final[str] = "ORDER_MANAGER.LEG.CANCEL_FAILED"
ERROR_RESULT_INVALID: Final[str] = "ORDER_MANAGER.RESULT.INVALID"
ERROR_RESULT_FINGERPRINT_MISMATCH: Final[str] = "ORDER_MANAGER.RESULT.FINGERPRINT_MISMATCH"
ERROR_SERIALIZATION_UNSUPPORTED_VERSION: Final[str] = "ORDER_MANAGER.SERIALIZATION.UNSUPPORTED_VERSION"
ERROR_SERIALIZATION_MALFORMED: Final[str] = "ORDER_MANAGER.SERIALIZATION.MALFORMED"

WARN_PLAN_NEAR_EXPIRY: Final[str] = "ORDER_MANAGER.PLAN.NEAR_EXPIRY"
WARN_PLAN_STALE_LIMIT_HINT: Final[str] = "ORDER_MANAGER.PLAN.STALE_LIMIT_HINT"
WARN_BROKER_DEGRADED: Final[str] = "ORDER_MANAGER.BROKER.DEGRADED"
WARN_LEG_RETRY_SUCCEEDED: Final[str] = "ORDER_MANAGER.LEG.RETRY_SUCCEEDED"
WARN_POLL_TIMEOUT: Final[str] = "ORDER_MANAGER.POLL.TIMEOUT"

_BROKER_CODE_MAP: Final[Mapping[str, str]] = MappingProxyType(
    {
        ERROR_REQUEST_TIMEOUT: "BROKER.TRANSIENT.TIMEOUT",
        ERROR_RATE_LIMIT_EXCEEDED: "BROKER.TRANSIENT.RATE_LIMIT",
        ERROR_CONNECTION_DISCONNECTED: "BROKER.TRANSIENT.CONNECTION",
    }
)

_logger = logging.getLogger("execution.order_manager")
_SleepFn = Callable[[float], None]


class OrderManagerError(Exception):
    """Base order manager exception."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        field: str | None = None,
        leg_index: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.leg_index = leg_index


class OrderManagerConfigurationError(OrderManagerError):
    """Raised when order manager configuration is invalid."""


class OrderManagerValidationError(OrderManagerError):
    """Raised when input or output validation fails."""


class OrderManagerContextError(OrderManagerError):
    """Raised when submission context is invalid."""


class OrderManagerSubmissionError(OrderManagerError):
    """Raised when a submission stage fails fatally."""


class OrderSubmissionStatus(str, Enum):
    """Overall order submission run status."""

    SUBMITTED = "submitted"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    TIMEOUT = "timeout"


class OrderAggregateStatus(str, Enum):
    """Rollup status across all legs in a tracker."""

    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    ALL_COMPLETE = "all_complete"
    PARTIALLY_FILLED = "partially_filled"
    MIXED_TERMINAL = "mixed_terminal"
    ALL_FAILED = "all_failed"
    ALL_CANCELLED = "all_cancelled"
    ABORTED = "aborted"


class OrderLifecycleStatus(str, Enum):
    """Per-leg lifecycle status."""

    PLANNED = "planned"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    COMPLETE = "complete"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class OrderLifecycleEventType(str, Enum):
    """Lifecycle event discriminator with associated topic."""

    PLAN_RECEIVED = "plan_received"
    PLAN_REJECTED = "plan_rejected"
    LEG_SUBMIT_STARTED = "leg_submit_started"
    LEG_SUBMITTED = "leg_submitted"
    LEG_RETRY_SCHEDULED = "leg_retry_scheduled"
    LEG_RETRY_ATTEMPT = "leg_retry_attempt"
    LEG_OPEN = "leg_open"
    LEG_PARTIAL_FILL = "leg_partial_fill"
    LEG_COMPLETE = "leg_complete"
    LEG_CANCEL_REQUESTED = "leg_cancel_requested"
    LEG_CANCELLED = "leg_cancelled"
    LEG_REJECTED = "leg_rejected"
    LEG_FAILED = "leg_failed"
    LEG_TIMEOUT = "leg_timeout"
    LEG_SKIPPED = "leg_skipped"
    SEQUENCE_GROUP_STARTED = "sequence_group_started"
    SEQUENCE_GROUP_COMPLETED = "sequence_group_completed"
    SEQUENCE_GROUP_ABORTED = "sequence_group_aborted"
    PLAN_SUBMISSION_COMPLETED = "plan_submission_completed"

    @property
    def topic(self) -> str:
        """Return hierarchical event bus topic for this event type."""
        mapping = {
            OrderLifecycleEventType.PLAN_RECEIVED: "order.plan.received",
            OrderLifecycleEventType.PLAN_REJECTED: "order.plan.rejected",
            OrderLifecycleEventType.LEG_SUBMIT_STARTED: "order.leg.submit_started",
            OrderLifecycleEventType.LEG_SUBMITTED: "order.leg.submitted",
            OrderLifecycleEventType.LEG_RETRY_SCHEDULED: "order.leg.retry_scheduled",
            OrderLifecycleEventType.LEG_RETRY_ATTEMPT: "order.leg.retry_attempt",
            OrderLifecycleEventType.LEG_OPEN: "order.leg.open",
            OrderLifecycleEventType.LEG_PARTIAL_FILL: "order.leg.partial_fill",
            OrderLifecycleEventType.LEG_COMPLETE: "order.leg.complete",
            OrderLifecycleEventType.LEG_CANCEL_REQUESTED: "order.leg.cancel_requested",
            OrderLifecycleEventType.LEG_CANCELLED: "order.leg.cancelled",
            OrderLifecycleEventType.LEG_REJECTED: "order.leg.rejected",
            OrderLifecycleEventType.LEG_FAILED: "order.leg.failed",
            OrderLifecycleEventType.LEG_TIMEOUT: "order.leg.timeout",
            OrderLifecycleEventType.LEG_SKIPPED: "order.leg.skipped",
            OrderLifecycleEventType.SEQUENCE_GROUP_STARTED: "order.sequence.started",
            OrderLifecycleEventType.SEQUENCE_GROUP_COMPLETED: "order.sequence.completed",
            OrderLifecycleEventType.SEQUENCE_GROUP_ABORTED: "order.sequence.aborted",
            OrderLifecycleEventType.PLAN_SUBMISSION_COMPLETED: "order.plan.completed",
        }
        return mapping[self]


class OrderSubmissionStageId(str, Enum):
    """Ordered submission pipeline stage identifiers."""

    PLAN_GATE = "plan_gate"
    INPUT_INTEGRITY = "input_integrity"
    BROKER_READINESS = "broker_readiness"
    LEG_MAPPING = "leg_mapping"
    PRE_SUBMIT_VALIDATION = "pre_submit_validation"
    SEQUENCE_EXECUTION = "sequence_execution"
    POST_SUBMIT_RECONCILIATION = "post_submit_reconciliation"
    RESULT_ASSEMBLY = "result_assembly"
    OUTPUT_VALIDATION = "output_validation"


STAGE_ORDER: Final[tuple[OrderSubmissionStageId, ...]] = (
    OrderSubmissionStageId.PLAN_GATE,
    OrderSubmissionStageId.INPUT_INTEGRITY,
    OrderSubmissionStageId.BROKER_READINESS,
    OrderSubmissionStageId.LEG_MAPPING,
    OrderSubmissionStageId.PRE_SUBMIT_VALIDATION,
    OrderSubmissionStageId.SEQUENCE_EXECUTION,
    OrderSubmissionStageId.POST_SUBMIT_RECONCILIATION,
    OrderSubmissionStageId.RESULT_ASSEMBLY,
    OrderSubmissionStageId.OUTPUT_VALIDATION,
)

# Populate terminal status sets after enum definition.
_TERMINAL_LIFECYCLE = frozenset(
    {
        OrderLifecycleStatus.COMPLETE,
        OrderLifecycleStatus.CANCELLED,
        OrderLifecycleStatus.REJECTED,
        OrderLifecycleStatus.FAILED,
        OrderLifecycleStatus.TIMEOUT,
        OrderLifecycleStatus.SKIPPED,
    }
)
_FAILURE_TERMINAL = frozenset(
    {
        OrderLifecycleStatus.REJECTED,
        OrderLifecycleStatus.FAILED,
        OrderLifecycleStatus.TIMEOUT,
    }
)


@dataclass(frozen=True)
class OrderManagerConfig:
    """Immutable configuration for :class:`OrderManager`."""

    strict_plan_validation: bool = True
    reject_expired_plans: bool = True
    require_broker_connected: bool = True
    require_broker_authenticated: bool = True
    enable_status_polling: bool = True
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS
    max_poll_attempts: int = DEFAULT_MAX_POLL_ATTEMPTS
    publish_lifecycle_events: bool = True
    strict_output_validation: bool = True
    deterministic_fingerprint: bool = True
    allow_analysis_dry_run: bool = False
    honor_sequence_delays: bool = True
    strict_correlation: bool = True
    partial_fill_terminal_in_backtest: bool = True
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.poll_interval_ms <= 0:
            raise OrderManagerConfigurationError(
                "poll_interval_ms must be positive.",
                code=ERROR_CONFIG_INVALID,
                field="poll_interval_ms",
            )
        if self.max_poll_attempts < 1:
            raise OrderManagerConfigurationError(
                "max_poll_attempts must be >= 1.",
                code=ERROR_CONFIG_INVALID,
                field="max_poll_attempts",
            )


@dataclass(frozen=True)
class OrderSubmissionContext:
    """Immutable per-run submission context."""

    correlation_id: str
    reference_time: datetime
    execution_mode: StrategyExecutionMode
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    submission_id: str | None = None
    force_cancel_token: str | None = None


@dataclass(frozen=True)
class OrderStateTransition:
    """Single append-only lifecycle transition record."""

    from_status: OrderLifecycleStatus
    to_status: OrderLifecycleStatus
    occurred_at: datetime
    reason_code: str
    message: str


@dataclass(frozen=True)
class OrderState:
    """Immutable per-leg lifecycle snapshot."""

    leg_index: int
    sequence_group: int
    instrument_key: str
    side: OrderSide
    order_type: OrderType
    product: ProductType
    planned_quantity: int
    lifecycle_status: OrderLifecycleStatus
    idempotency_key: str
    filled_quantity: int = 0
    remaining_quantity: int = 0
    broker_order_id: str | None = None
    average_fill_price: float | None = None
    last_broker_status: OrderStatus | None = None
    attempt_count: int = 0
    terminal: bool = False
    terminal_at: datetime | None = None
    last_error_code: str | None = None
    transitions: tuple[OrderStateTransition, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.remaining_quantity != self.planned_quantity - self.filled_quantity:
            raise OrderManagerValidationError(
                "remaining_quantity must equal planned_quantity - filled_quantity.",
                code=ERROR_RESULT_INVALID,
                field="remaining_quantity",
                leg_index=self.leg_index,
            )
        if self.filled_quantity < 0 or self.filled_quantity > self.planned_quantity:
            raise OrderManagerValidationError(
                "filled_quantity out of range.",
                code=ERROR_RESULT_INVALID,
                field="filled_quantity",
                leg_index=self.leg_index,
            )
        if self.terminal and self.lifecycle_status not in _TERMINAL_LIFECYCLE:
            raise OrderManagerValidationError(
                "terminal=True requires terminal lifecycle status.",
                code=ERROR_RESULT_INVALID,
                field="terminal",
                leg_index=self.leg_index,
            )


@dataclass(frozen=True)
class OrderSequenceResult:
    """Outcome for one sequence group."""

    sequence_group: int
    mode: LegSequenceMode
    leg_indices: tuple[int, ...]
    completed: bool
    aborted: bool
    duration_ms: float


@dataclass(frozen=True)
class OrderTracker:
    """Immutable aggregate of leg lifecycle states for one submission run."""

    submission_id: str
    plan_id: str
    correlation_id: str
    plan_fingerprint: str
    leg_states: tuple[OrderState, ...]
    aggregate_status: OrderAggregateStatus
    sequence_results: tuple[OrderSequenceResult, ...]
    started_at: datetime
    completed_at: datetime | None
    tracker_fingerprint: str


@dataclass(frozen=True)
class OrderWarningRecord:
    """Non-fatal warning emitted during submission."""

    code: str
    message: str
    leg_index: int | None = None
    stage_id: OrderSubmissionStageId | None = None
    field: str | None = None


@dataclass(frozen=True)
class OrderErrorRecord:
    """Structured error emitted during submission."""

    code: str
    message: str
    leg_index: int | None = None
    stage_id: OrderSubmissionStageId | None = None
    field: str | None = None


@dataclass(frozen=True)
class OrderValidationResult:
    """Validation outcome for context or result checks."""

    errors: tuple[OrderErrorRecord, ...] = ()
    warnings: tuple[OrderWarningRecord, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return True when no errors are present."""
        return not self.errors


@dataclass(frozen=True)
class OrderStageResult:
    """Audit record for one pipeline stage."""

    stage_id: OrderSubmissionStageId
    passed: bool
    rejection_code: str | None
    message: str | None
    duration_ms: float
    details: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class OrderPipelineResult:
    """Pipeline stage audit summary."""

    total_stages: int
    passed_stages: int
    failed_stage_id: OrderSubmissionStageId | None
    stages: tuple[OrderStageResult, ...]
    short_circuited: bool


@dataclass(frozen=True)
class OrderSubmissionResult:
    """Immutable sealed order submission outcome."""

    submission_id: str
    plan_id: str
    correlation_id: str
    status: OrderSubmissionStatus
    tracker: OrderTracker
    pipeline_summary: OrderPipelineResult
    warnings: tuple[OrderWarningRecord, ...]
    errors: tuple[OrderErrorRecord, ...]
    primary_error_code: str | None
    submitted_at: datetime
    completed_at: datetime | None
    duration_ms: float
    submission_fingerprint: str


@dataclass(frozen=True)
class OrderLifecycleEvent:
    """Structured lifecycle event payload."""

    event_type: OrderLifecycleEventType
    topic: str
    submission_id: str
    plan_id: str
    correlation_id: str
    occurred_at: datetime
    leg_index: int | None = None
    sequence_group: int | None = None
    order_state: OrderState | None = None
    producer: str = PRODUCER_NAME
    producer_version: str = ORDER_MANAGER_VERSION
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
    """Mutable accumulator for one submission run."""

    plan: ExecutionPlan
    context: OrderSubmissionContext
    config: OrderManagerConfig
    broker: BaseBrokerClient | None
    submission_id: str
    started_at: datetime
    warnings: list[OrderWarningRecord] = field(default_factory=list)
    errors: list[OrderErrorRecord] = field(default_factory=list)
    leg_states: dict[int, OrderState] = field(default_factory=dict)
    mapped_requests: dict[int, PlaceOrderRequest] = field(default_factory=dict)
    sequence_results: list[OrderSequenceResult] = field(default_factory=list)
    primary_error_code: str | None = None
    pre_submit_rejected: bool = False
    broker_calls_allowed: bool = True
    execution_mode: StrategyExecutionMode = StrategyExecutionMode.LIVE


def default_order_manager_config() -> OrderManagerConfig:
    """Return production-default order manager configuration."""
    return OrderManagerConfig(
        strict_plan_validation=True,
        reject_expired_plans=True,
        require_broker_connected=True,
        require_broker_authenticated=True,
        enable_status_polling=True,
        poll_interval_ms=DEFAULT_POLL_INTERVAL_MS,
        max_poll_attempts=DEFAULT_MAX_POLL_ATTEMPTS,
        publish_lifecycle_events=True,
        strict_output_validation=True,
        deterministic_fingerprint=True,
        allow_analysis_dry_run=False,
        honor_sequence_delays=True,
        strict_correlation=True,
        partial_fill_terminal_in_backtest=True,
        metadata=MappingProxyType({}),
    )


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
        raise OrderManagerValidationError(
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
        raise OrderManagerValidationError(
            "deserialized datetime must be timezone-aware.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return parsed


def config_fingerprint(config: OrderManagerConfig) -> str:
    """Compute deterministic fingerprint for configuration."""
    payload = {
        "strict_plan_validation": config.strict_plan_validation,
        "reject_expired_plans": config.reject_expired_plans,
        "require_broker_connected": config.require_broker_connected,
        "require_broker_authenticated": config.require_broker_authenticated,
        "enable_status_polling": config.enable_status_polling,
        "poll_interval_ms": config.poll_interval_ms,
        "max_poll_attempts": config.max_poll_attempts,
        "publish_lifecycle_events": config.publish_lifecycle_events,
        "strict_output_validation": config.strict_output_validation,
        "deterministic_fingerprint": config.deterministic_fingerprint,
        "allow_analysis_dry_run": config.allow_analysis_dry_run,
        "honor_sequence_delays": config.honor_sequence_delays,
        "strict_correlation": config.strict_correlation,
        "partial_fill_terminal_in_backtest": config.partial_fill_terminal_in_backtest,
        "metadata": dict(sorted(config.metadata.items())),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _generate_submission_id(plan: ExecutionPlan, context: OrderSubmissionContext) -> str:
    """Generate submission identifier."""
    if context.submission_id:
        return context.submission_id
    payload = f"{plan.plan_fingerprint}|{_datetime_to_iso(context.reference_time)}|submit"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"sub-{digest}"


def map_broker_error_code(code: str) -> str:
    """Map broker client error code to platform retry classification code."""
    return _BROKER_CODE_MAP.get(code, code)


def is_retryable(error: BrokerClientError, policy: RetryPolicy) -> bool:
    """Return True when error code is retryable per policy."""
    mapped = map_broker_error_code(error.code)
    return mapped in policy.retryable_error_codes and error.recoverable


def regenerate_idempotency_key(leg: PlannedOrderLeg, plan: ExecutionPlan, attempt: int) -> str:
    """Generate deterministic retry idempotency key."""
    _ = plan
    return f"{leg.idempotency_key}-retry-{attempt}"


def map_leg_to_place_order_request(
    leg: PlannedOrderLeg,
    plan: ExecutionPlan,
    *,
    idempotency_key: str | None = None,
) -> PlaceOrderRequest:
    """Map planned leg to broker-neutral order request."""
    variety = OrderVariety.REGULAR
    if leg.variety and leg.variety.upper() == "AMO":
        variety = OrderVariety.AMO
    return PlaceOrderRequest(
        instrument_key=leg.instrument_key,
        side=BrokerOrderSide(leg.side.value),
        order_type=BrokerOrderType(leg.order_type.value),
        product=BrokerProductType(leg.product.value),
        quantity=leg.quantity,
        price=leg.limit_price_hint,
        trigger_price=leg.trigger_price_hint,
        variety=variety,
        validity=leg.validity or "DAY",
        tag=leg.tag or plan.summary.strategy_id,
        idempotency_key=idempotency_key or leg.idempotency_key,
        correlation_id=plan.correlation_id,
    )


def validate_leg_mapping(leg: PlannedOrderLeg) -> OrderValidationResult:
    """Validate mapping preconditions for one leg."""
    errors: list[OrderErrorRecord] = []
    if not leg.instrument_key.strip():
        errors.append(
            OrderErrorRecord(
                code=ERROR_MAP_INVALID_INSTRUMENT,
                message="instrument_key must be non-empty.",
                leg_index=leg.leg_index,
                field="instrument_key",
            )
        )
    if leg.quantity <= 0:
        errors.append(
            OrderErrorRecord(
                code=ERROR_MAP_INVALID_QUANTITY,
                message="quantity must be positive.",
                leg_index=leg.leg_index,
                field="quantity",
            )
        )
    if leg.order_type is OrderType.LIMIT and (
        leg.limit_price_hint is None or not math.isfinite(leg.limit_price_hint) or leg.limit_price_hint <= 0
    ):
        errors.append(
            OrderErrorRecord(
                code=ERROR_MAP_MISSING_LIMIT_PRICE,
                message="LIMIT legs require positive limit_price_hint.",
                leg_index=leg.leg_index,
                field="limit_price_hint",
            )
        )
    if leg.order_type in (OrderType.SL, OrderType.SL_M) and (
        leg.trigger_price_hint is None
        or not math.isfinite(leg.trigger_price_hint)
        or leg.trigger_price_hint <= 0
    ):
        errors.append(
            OrderErrorRecord(
                code=ERROR_MAP_MISSING_TRIGGER_PRICE,
                message="SL legs require positive trigger_price_hint.",
                leg_index=leg.leg_index,
                field="trigger_price_hint",
            )
        )
    if not leg.idempotency_key.strip():
        errors.append(
            OrderErrorRecord(
                code=ERROR_MAP_MISSING_IDEMPOTENCY_KEY,
                message="idempotency_key must be non-empty.",
                leg_index=leg.leg_index,
                field="idempotency_key",
            )
        )
    return OrderValidationResult(errors=tuple(errors))


def validate_submission_context(
    context: OrderSubmissionContext,
    plan: ExecutionPlan,
    config: OrderManagerConfig,
) -> OrderValidationResult:
    """Validate context and plan before broker contact."""
    errors: list[OrderErrorRecord] = []
    warnings: list[OrderWarningRecord] = []

    if not _is_timezone_aware(context.reference_time):
        errors.append(
            OrderErrorRecord(
                code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
                message="reference_time must be timezone-aware.",
                field="reference_time",
            )
        )
    if config.strict_correlation and context.correlation_id != plan.correlation_id:
        errors.append(
            OrderErrorRecord(
                code=ERROR_CONTEXT_CORRELATION_MISMATCH,
                message="correlation_id mismatch between context and plan.",
                field="correlation_id",
            )
        )
    if plan.status is not ExecutionPlanStatus.READY:
        errors.append(
            OrderErrorRecord(
                code=ERROR_PLAN_NOT_READY,
                message=f"Plan status {plan.status.value} is not READY.",
                stage_id=OrderSubmissionStageId.PLAN_GATE,
            )
        )
    if not plan.legs:
        errors.append(
            OrderErrorRecord(
                code=ERROR_PLAN_NO_LEGS,
                message="Plan must contain at least one leg.",
                stage_id=OrderSubmissionStageId.PLAN_GATE,
            )
        )
    if config.reject_expired_plans and plan.valid_until is not None and _is_timezone_aware(context.reference_time):
        if context.reference_time >= plan.valid_until:
            errors.append(
                OrderErrorRecord(
                    code=ERROR_PLAN_EXPIRED,
                    message="Plan valid_until has passed.",
                    stage_id=OrderSubmissionStageId.PLAN_GATE,
                )
            )
        else:
            remaining = (plan.valid_until - context.reference_time).total_seconds()
            if remaining <= NEAR_EXPIRY_SECONDS:
                warnings.append(
                    OrderWarningRecord(
                        code=WARN_PLAN_NEAR_EXPIRY,
                        message="Plan valid_until within 15 seconds.",
                        stage_id=OrderSubmissionStageId.PRE_SUBMIT_VALIDATION,
                    )
                )
    if not plan.plan_fingerprint.strip():
        errors.append(
            OrderErrorRecord(
                code=ERROR_PLAN_INVALID,
                message="plan_fingerprint must be non-empty.",
                stage_id=OrderSubmissionStageId.INPUT_INTEGRITY,
            )
        )
    leg_indices = [leg.leg_index for leg in plan.legs]
    if len(leg_indices) != len(set(leg_indices)):
        errors.append(
            OrderErrorRecord(
                code=ERROR_PLAN_INVALID_LEGS,
                message="Leg indices must be unique.",
                stage_id=OrderSubmissionStageId.INPUT_INTEGRITY,
            )
        )
    expected = list(range(len(plan.legs)))
    if sorted(leg_indices) != expected and plan.legs:
        errors.append(
            OrderErrorRecord(
                code=ERROR_PLAN_INVALID_LEGS,
                message="Leg indices must be contiguous from 0.",
                stage_id=OrderSubmissionStageId.INPUT_INTEGRITY,
            )
        )
    for sequence in plan.sequences:
        for index in sequence.leg_indices:
            if index not in leg_indices:
                errors.append(
                    OrderErrorRecord(
                        code=ERROR_SEQUENCE_INVALID,
                        message=f"Sequence references unknown leg index {index}.",
                        stage_id=OrderSubmissionStageId.INPUT_INTEGRITY,
                    )
                )
    plan_validation = validate_execution_plan(plan)
    if not plan_validation.is_valid:
        for item in plan_validation.errors:
            errors.append(
                OrderErrorRecord(
                    code=ERROR_PLAN_INVALID,
                    message=item.message,
                    field=item.field,
                    stage_id=OrderSubmissionStageId.INPUT_INTEGRITY,
                )
            )
    for leg in plan.legs:
        if leg.metadata.get("limit_hint_stale", "").lower() == "true":
            warnings.append(
                OrderWarningRecord(
                    code=WARN_PLAN_STALE_LIMIT_HINT,
                    message="Limit hint may be stale.",
                    leg_index=leg.leg_index,
                    stage_id=OrderSubmissionStageId.PRE_SUBMIT_VALIDATION,
                )
            )
    return OrderValidationResult(errors=tuple(errors), warnings=tuple(warnings))


def derive_aggregate_status(states: tuple[OrderState, ...]) -> OrderAggregateStatus:
    """Derive tracker rollup from leg states."""
    if not states:
        return OrderAggregateStatus.PENDING
    if all(s.lifecycle_status is OrderLifecycleStatus.PLANNED for s in states):
        return OrderAggregateStatus.PENDING
    if any(not s.terminal for s in states):
        return OrderAggregateStatus.IN_FLIGHT
    if all(s.lifecycle_status is OrderLifecycleStatus.COMPLETE for s in states):
        return OrderAggregateStatus.ALL_COMPLETE
    if all(s.lifecycle_status is OrderLifecycleStatus.CANCELLED for s in states):
        return OrderAggregateStatus.ALL_CANCELLED
    if all(s.lifecycle_status in _FAILURE_TERMINAL for s in states):
        return OrderAggregateStatus.ALL_FAILED
    if any(s.lifecycle_status is OrderLifecycleStatus.SKIPPED for s in states) and any(
        s.lifecycle_status in _FAILURE_TERMINAL for s in states
    ):
        return OrderAggregateStatus.ABORTED
    if any(s.lifecycle_status is OrderLifecycleStatus.PARTIALLY_FILLED for s in states):
        return OrderAggregateStatus.PARTIALLY_FILLED
    return OrderAggregateStatus.MIXED_TERMINAL


def derive_submission_status(
    tracker: OrderTracker,
    *,
    pre_rejected: bool = False,
) -> OrderSubmissionStatus:
    """Derive overall submission status from tracker snapshot."""
    if pre_rejected:
        return OrderSubmissionStatus.REJECTED
    states = tracker.leg_states
    if any(not s.terminal for s in states):
        return OrderSubmissionStatus.SUBMITTED
    aggregate = tracker.aggregate_status
    if aggregate is OrderAggregateStatus.ALL_COMPLETE:
        return OrderSubmissionStatus.COMPLETED
    if aggregate is OrderAggregateStatus.ALL_CANCELLED:
        return OrderSubmissionStatus.CANCELLED
    if aggregate is OrderAggregateStatus.ALL_FAILED:
        return OrderSubmissionStatus.FAILED
    if aggregate in (
        OrderAggregateStatus.PARTIALLY_FILLED,
        OrderAggregateStatus.MIXED_TERMINAL,
        OrderAggregateStatus.ABORTED,
    ):
        return OrderSubmissionStatus.PARTIAL
    if all(s.lifecycle_status is OrderLifecycleStatus.PLANNED for s in states):
        return OrderSubmissionStatus.REJECTED
    return OrderSubmissionStatus.PARTIAL


def compute_tracker_fingerprint(states: tuple[OrderState, ...]) -> str:
    """Compute deterministic tracker fingerprint."""
    payload = {
        "leg_outcomes": [
            {
                "leg_index": state.leg_index,
                "lifecycle_status": state.lifecycle_status.value,
                "broker_order_id": state.broker_order_id,
                "filled_quantity": state.filled_quantity,
                "attempt_count": state.attempt_count,
                "last_error_code": state.last_error_code,
            }
            for state in sorted(states, key=lambda item: item.leg_index)
        ]
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_submission_fingerprint(
    plan: ExecutionPlan,
    tracker: OrderTracker,
    config: OrderManagerConfig,
) -> str:
    """Compute SHA-256 submission fingerprint for replay verification."""
    payload = {
        "plan_fingerprint": plan.plan_fingerprint,
        "leg_outcomes": [
            {
                "leg_index": state.leg_index,
                "lifecycle_status": state.lifecycle_status.value,
                "broker_order_id": state.broker_order_id,
                "filled_quantity": state.filled_quantity,
                "attempt_count": state.attempt_count,
                "last_error_code": state.last_error_code,
            }
            for state in sorted(tracker.leg_states, key=lambda item: item.leg_index)
        ],
        "aggregate_status": tracker.aggregate_status.value,
        "config_hash": config_fingerprint(config),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _compute_submission_fingerprint_from_tracker(
    tracker: OrderTracker,
    config: OrderManagerConfig,
) -> str:
    """Compute submission fingerprint using tracker plan fingerprint."""
    payload = {
        "plan_fingerprint": tracker.plan_fingerprint,
        "leg_outcomes": [
            {
                "leg_index": state.leg_index,
                "lifecycle_status": state.lifecycle_status.value,
                "broker_order_id": state.broker_order_id,
                "filled_quantity": state.filled_quantity,
                "attempt_count": state.attempt_count,
                "last_error_code": state.last_error_code,
            }
            for state in sorted(tracker.leg_states, key=lambda item: item.leg_index)
        ],
        "aggregate_status": tracker.aggregate_status.value,
        "config_hash": config_fingerprint(config),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _create_initial_state(leg: PlannedOrderLeg, plan: ExecutionPlan) -> OrderState:
    """Create PLANNED OrderState for a leg."""
    metadata = dict(plan.metadata)
    metadata.update(dict(leg.metadata))
    metadata["plan_id"] = plan.plan_id
    metadata["plan_fingerprint"] = plan.plan_fingerprint
    metadata["risk_fingerprint"] = plan.risk_fingerprint
    return OrderState(
        leg_index=leg.leg_index,
        sequence_group=leg.sequence_group,
        instrument_key=leg.instrument_key,
        side=leg.side,
        order_type=leg.order_type,
        product=leg.product,
        planned_quantity=leg.quantity,
        lifecycle_status=OrderLifecycleStatus.PLANNED,
        idempotency_key=leg.idempotency_key,
        filled_quantity=0,
        remaining_quantity=leg.quantity,
        metadata=MappingProxyType(metadata),
    )


def _append_transition(
    state: OrderState,
    to_status: OrderLifecycleStatus,
    *,
    occurred_at: datetime,
    reason_code: str,
    message: str,
    terminal: bool | None = None,
    **updates: object,
) -> OrderState:
    """Return new OrderState with appended transition."""
    transition = OrderStateTransition(
        from_status=state.lifecycle_status,
        to_status=to_status,
        occurred_at=occurred_at,
        reason_code=reason_code,
        message=message,
    )
    transitions = state.transitions + (transition,)
    resolved_terminal = terminal if terminal is not None else to_status in _TERMINAL_LIFECYCLE
    terminal_at = state.terminal_at
    if resolved_terminal and terminal_at is None:
        terminal_at = occurred_at
    fields = {
        "lifecycle_status": to_status,
        "transitions": transitions,
        "terminal": resolved_terminal,
        "terminal_at": terminal_at,
    }
    fields.update(updates)
    return replace(state, **fields)


def _broker_status_to_lifecycle(
    status: OrderStatus,
    *,
    planned_quantity: int,
    filled_quantity: int,
) -> OrderLifecycleStatus:
    """Map broker order status to lifecycle status."""
    if status is OrderStatus.COMPLETE:
        if filled_quantity >= planned_quantity:
            return OrderLifecycleStatus.COMPLETE
        if filled_quantity > 0:
            return OrderLifecycleStatus.PARTIALLY_FILLED
        return OrderLifecycleStatus.COMPLETE
    if status is OrderStatus.CANCELLED:
        return OrderLifecycleStatus.CANCELLED
    if status is OrderStatus.REJECTED:
        return OrderLifecycleStatus.REJECTED
    if status is OrderStatus.OPEN:
        if 0 < filled_quantity < planned_quantity:
            return OrderLifecycleStatus.PARTIALLY_FILLED
        return OrderLifecycleStatus.OPEN
    if status is OrderStatus.PENDING:
        return OrderLifecycleStatus.SUBMITTED
    return OrderLifecycleStatus.SUBMITTED


def _extract_filled_quantity(record: OrderRecord | PlaceOrderResult, planned_quantity: int) -> int:
    """Extract filled quantity from broker record."""
    raw = record.raw if hasattr(record, "raw") else None
    if raw and "filled_quantity" in raw:
        try:
            return int(raw["filled_quantity"])
        except (TypeError, ValueError):
            pass
    status = record.status
    if status is OrderStatus.COMPLETE:
        return planned_quantity
    return 0


def validate_order_submission_result(result: OrderSubmissionResult) -> OrderValidationResult:
    """Validate sealed submission result."""
    errors: list[OrderErrorRecord] = []
    if not result.submission_id.strip():
        errors.append(
            OrderErrorRecord(code=ERROR_RESULT_INVALID, message="submission_id must be non-empty.")
        )
    if not result.tracker.leg_states:
        if result.status is not OrderSubmissionStatus.REJECTED:
            errors.append(
                OrderErrorRecord(code=ERROR_RESULT_INVALID, message="tracker must contain leg states.")
            )
        return OrderValidationResult(errors=tuple(errors))
    indices = {state.leg_index for state in result.tracker.leg_states}
    expected = set(range(len(result.tracker.leg_states)))
    if indices != expected and result.status is not OrderSubmissionStatus.REJECTED:
        errors.append(
            OrderErrorRecord(code=ERROR_RESULT_INVALID, message="Leg indices must be contiguous.")
        )
    for state in result.tracker.leg_states:
        if state.terminal and state.terminal_at is None:
            errors.append(
                OrderErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="Terminal legs must have terminal_at set.",
                    leg_index=state.leg_index,
                )
            )
        if state.lifecycle_status is OrderLifecycleStatus.COMPLETE:
            if state.filled_quantity != state.planned_quantity:
                errors.append(
                    OrderErrorRecord(
                        code=ERROR_RESULT_INVALID,
                        message="COMPLETE legs must be fully filled.",
                        leg_index=state.leg_index,
                    )
                )
    return OrderValidationResult(errors=tuple(errors))


def assert_valid_order_submission_result(result: OrderSubmissionResult) -> None:
    """Raise OrderManagerValidationError when result is invalid."""
    validation = validate_order_submission_result(result)
    if not validation.is_valid:
        raise OrderManagerValidationError(
            validation.errors[0].message,
            code=validation.errors[0].code,
            leg_index=validation.errors[0].leg_index,
        )


def _order_state_to_dict(state: OrderState) -> dict[str, Any]:
    """Serialize OrderState to dict."""
    return {
        "schema_version": ORDER_STATE_SCHEMA_VERSION,
        "leg_index": state.leg_index,
        "sequence_group": state.sequence_group,
        "instrument_key": state.instrument_key,
        "side": state.side.value,
        "order_type": state.order_type.value,
        "product": state.product.value,
        "planned_quantity": state.planned_quantity,
        "lifecycle_status": state.lifecycle_status.value,
        "broker_order_id": state.broker_order_id,
        "idempotency_key": state.idempotency_key,
        "filled_quantity": state.filled_quantity,
        "remaining_quantity": state.remaining_quantity,
        "average_fill_price": state.average_fill_price,
        "last_broker_status": state.last_broker_status.value if state.last_broker_status else None,
        "attempt_count": state.attempt_count,
        "terminal": state.terminal,
        "terminal_at": _datetime_to_iso(state.terminal_at) if state.terminal_at else None,
        "last_error_code": state.last_error_code,
        "transitions": [
            {
                "from_status": item.from_status.value,
                "to_status": item.to_status.value,
                "occurred_at": _datetime_to_iso(item.occurred_at),
                "reason_code": item.reason_code,
                "message": item.message,
            }
            for item in state.transitions
        ],
        "metadata": dict(sorted(state.metadata.items())),
    }


def _order_state_from_dict(data: dict[str, Any]) -> OrderState:
    """Deserialize OrderState from dict."""
    schema = data.get("schema_version", ORDER_STATE_SCHEMA_VERSION)
    if schema != ORDER_STATE_SCHEMA_VERSION:
        raise OrderManagerValidationError(
            f"Unsupported schema version {schema}.",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
        )
    transitions = tuple(
        OrderStateTransition(
            from_status=OrderLifecycleStatus(item["from_status"]),
            to_status=OrderLifecycleStatus(item["to_status"]),
            occurred_at=_datetime_from_iso(item["occurred_at"]),
            reason_code=str(item["reason_code"]),
            message=str(item["message"]),
        )
        for item in data.get("transitions", [])
    )
    last_broker_status = data.get("last_broker_status")
    terminal_at_raw = data.get("terminal_at")
    return OrderState(
        leg_index=int(data["leg_index"]),
        sequence_group=int(data["sequence_group"]),
        instrument_key=str(data["instrument_key"]),
        side=OrderSide(str(data["side"])),
        order_type=OrderType(str(data["order_type"])),
        product=ProductType(str(data["product"])),
        planned_quantity=int(data["planned_quantity"]),
        lifecycle_status=OrderLifecycleStatus(str(data["lifecycle_status"])),
        idempotency_key=str(data["idempotency_key"]),
        filled_quantity=int(data.get("filled_quantity", 0)),
        remaining_quantity=int(data.get("remaining_quantity", 0)),
        broker_order_id=data.get("broker_order_id"),
        average_fill_price=data.get("average_fill_price"),
        last_broker_status=OrderStatus(last_broker_status) if last_broker_status else None,
        attempt_count=int(data.get("attempt_count", 0)),
        terminal=bool(data.get("terminal", False)),
        terminal_at=_datetime_from_iso(terminal_at_raw) if terminal_at_raw else None,
        last_error_code=data.get("last_error_code"),
        transitions=transitions,
        metadata=MappingProxyType(dict(data.get("metadata", {}))),
    )


def _tracker_to_dict(tracker: OrderTracker) -> dict[str, Any]:
    """Serialize OrderTracker to dict."""
    return {
        "schema_version": ORDER_STATE_SCHEMA_VERSION,
        "submission_id": tracker.submission_id,
        "plan_id": tracker.plan_id,
        "correlation_id": tracker.correlation_id,
        "plan_fingerprint": tracker.plan_fingerprint,
        "leg_states": [_order_state_to_dict(state) for state in tracker.leg_states],
        "aggregate_status": tracker.aggregate_status.value,
        "sequence_results": [
            {
                "sequence_group": item.sequence_group,
                "mode": item.mode.value,
                "leg_indices": list(item.leg_indices),
                "completed": item.completed,
                "aborted": item.aborted,
                "duration_ms": item.duration_ms,
            }
            for item in tracker.sequence_results
        ],
        "started_at": _datetime_to_iso(tracker.started_at),
        "completed_at": _datetime_to_iso(tracker.completed_at) if tracker.completed_at else None,
        "tracker_fingerprint": tracker.tracker_fingerprint,
    }


def _tracker_from_dict(data: dict[str, Any]) -> OrderTracker:
    """Deserialize OrderTracker from dict."""
    schema = data.get("schema_version", ORDER_STATE_SCHEMA_VERSION)
    if schema != ORDER_STATE_SCHEMA_VERSION:
        raise OrderManagerValidationError(
            f"Unsupported schema version {schema}.",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
        )
    sequence_results = tuple(
        OrderSequenceResult(
            sequence_group=int(item["sequence_group"]),
            mode=LegSequenceMode(str(item["mode"])),
            leg_indices=tuple(int(value) for value in item["leg_indices"]),
            completed=bool(item["completed"]),
            aborted=bool(item["aborted"]),
            duration_ms=float(item["duration_ms"]),
        )
        for item in data.get("sequence_results", [])
    )
    completed_at_raw = data.get("completed_at")
    return OrderTracker(
        submission_id=str(data["submission_id"]),
        plan_id=str(data["plan_id"]),
        correlation_id=str(data["correlation_id"]),
        plan_fingerprint=str(data["plan_fingerprint"]),
        leg_states=tuple(_order_state_from_dict(item) for item in data["leg_states"]),
        aggregate_status=OrderAggregateStatus(str(data["aggregate_status"])),
        sequence_results=sequence_results,
        started_at=_datetime_from_iso(data["started_at"]),
        completed_at=_datetime_from_iso(completed_at_raw) if completed_at_raw else None,
        tracker_fingerprint=str(data["tracker_fingerprint"]),
    )


def submission_result_to_dict(result: OrderSubmissionResult) -> dict[str, Any]:
    """Serialize OrderSubmissionResult to dict."""
    return {
        "schema_version": ORDER_STATE_SCHEMA_VERSION,
        "submission_id": result.submission_id,
        "plan_id": result.plan_id,
        "correlation_id": result.correlation_id,
        "status": result.status.value,
        "tracker": _tracker_to_dict(result.tracker),
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
                "code": item.code,
                "message": item.message,
                "leg_index": item.leg_index,
                "stage_id": item.stage_id.value if item.stage_id else None,
                "field": item.field,
            }
            for item in result.warnings
        ],
        "errors": [
            {
                "code": item.code,
                "message": item.message,
                "leg_index": item.leg_index,
                "stage_id": item.stage_id.value if item.stage_id else None,
                "field": item.field,
            }
            for item in result.errors
        ],
        "primary_error_code": result.primary_error_code,
        "submitted_at": _datetime_to_iso(result.submitted_at),
        "completed_at": _datetime_to_iso(result.completed_at) if result.completed_at else None,
        "duration_ms": result.duration_ms,
        "submission_fingerprint": result.submission_fingerprint,
    }


def submission_result_from_dict(data: dict[str, Any]) -> OrderSubmissionResult:
    """Deserialize OrderSubmissionResult from dict."""
    schema = data.get("schema_version", ORDER_STATE_SCHEMA_VERSION)
    if schema != ORDER_STATE_SCHEMA_VERSION:
        raise OrderManagerValidationError(
            f"Unsupported schema version {schema}.",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
        )
    pipeline_raw = data["pipeline_summary"]
    stages = tuple(
        OrderStageResult(
            stage_id=OrderSubmissionStageId(str(item["stage_id"])),
            passed=bool(item["passed"]),
            rejection_code=item.get("rejection_code"),
            message=item.get("message"),
            duration_ms=float(item["duration_ms"]),
        )
        for item in pipeline_raw.get("stages", [])
    )
    failed_stage_raw = pipeline_raw.get("failed_stage_id")
    pipeline = OrderPipelineResult(
        total_stages=int(pipeline_raw["total_stages"]),
        passed_stages=int(pipeline_raw["passed_stages"]),
        failed_stage_id=(
            OrderSubmissionStageId(str(failed_stage_raw)) if failed_stage_raw else None
        ),
        stages=stages,
        short_circuited=bool(pipeline_raw.get("short_circuited", False)),
    )
    warnings = tuple(
        OrderWarningRecord(
            code=str(item["code"]),
            message=str(item["message"]),
            leg_index=item.get("leg_index"),
            stage_id=(
                OrderSubmissionStageId(str(item["stage_id"])) if item.get("stage_id") else None
            ),
            field=item.get("field"),
        )
        for item in data.get("warnings", [])
    )
    errors = tuple(
        OrderErrorRecord(
            code=str(item["code"]),
            message=str(item["message"]),
            leg_index=item.get("leg_index"),
            stage_id=(
                OrderSubmissionStageId(str(item["stage_id"])) if item.get("stage_id") else None
            ),
            field=item.get("field"),
        )
        for item in data.get("errors", [])
    )
    completed_at_raw = data.get("completed_at")
    return OrderSubmissionResult(
        submission_id=str(data["submission_id"]),
        plan_id=str(data["plan_id"]),
        correlation_id=str(data["correlation_id"]),
        status=OrderSubmissionStatus(str(data["status"])),
        tracker=_tracker_from_dict(data["tracker"]),
        pipeline_summary=pipeline,
        warnings=warnings,
        errors=errors,
        primary_error_code=data.get("primary_error_code"),
        submitted_at=_datetime_from_iso(data["submitted_at"]),
        completed_at=_datetime_from_iso(completed_at_raw) if completed_at_raw else None,
        duration_ms=float(data["duration_ms"]),
        submission_fingerprint=str(data["submission_fingerprint"]),
    )


def serialize_order_submission_result(result: OrderSubmissionResult) -> str:
    """Serialize submission result to canonical JSON."""
    return _canonical_json(submission_result_to_dict(result))


def deserialize_order_submission_result(payload: str) -> OrderSubmissionResult:
    """Deserialize submission result from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OrderManagerValidationError(
            "Malformed JSON payload.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(data, dict):
        raise OrderManagerValidationError(
            "JSON payload must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return submission_result_from_dict(data)


class _EventPublisher:
    """Lifecycle event publisher with graceful no-op when bus absent."""

    def __init__(
        self,
        event_bus: EventBus | None,
        *,
        enabled: bool,
        submission_id: str,
        plan_id: str,
        correlation_id: str,
    ) -> None:
        self._event_bus = event_bus
        self._enabled = enabled and event_bus is not None
        self._submission_id = submission_id
        self._plan_id = plan_id
        self._correlation_id = correlation_id

    def publish(
        self,
        event_type: OrderLifecycleEventType,
        *,
        occurred_at: datetime,
        leg_index: int | None = None,
        sequence_group: int | None = None,
        order_state: OrderState | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Publish lifecycle event when enabled."""
        if not self._enabled or self._event_bus is None:
            return
        lifecycle = OrderLifecycleEvent(
            event_type=event_type,
            topic=event_type.topic,
            submission_id=self._submission_id,
            plan_id=self._plan_id,
            correlation_id=self._correlation_id,
            occurred_at=occurred_at,
            leg_index=leg_index,
            sequence_group=sequence_group,
            order_state=order_state,
            metadata=MappingProxyType(dict(metadata or {})),
        )
        envelope = EventEnvelope(
            event_id=str(uuid.uuid4()),
            topic=lifecycle.topic,
            payload=lifecycle,
            correlation_id=self._correlation_id,
            producer=PRODUCER_NAME,
            occurred_at=occurred_at,
            published_at=_utc_now(),
            producer_version=ORDER_MANAGER_VERSION,
            payload_type="OrderLifecycleEvent",
        )
        self._event_bus.publish(envelope)


class OrderSubmissionPipeline:
    """Stateless multi-stage order submission pipeline."""

    def __init__(self, *, sleep_fn: _SleepFn | None = None) -> None:
        self._sleep_fn = sleep_fn or time.sleep

    def execute(
        self,
        plan: ExecutionPlan,
        broker: BaseBrokerClient | None,
        context: OrderSubmissionContext,
        config: OrderManagerConfig,
        *,
        event_bus: EventBus | None = None,
        submission_id: str | None = None,
    ) -> OrderSubmissionResult:
        """Execute full submission pipeline for one plan."""
        started_at = context.reference_time
        resolved_submission_id = submission_id or _generate_submission_id(plan, context)
        execution_mode = context.execution_mode or plan.execution_mode
        run_state = _PipelineRunState(
            plan=plan,
            context=context,
            config=config,
            broker=broker,
            submission_id=resolved_submission_id,
            started_at=started_at,
            execution_mode=execution_mode,
        )
        for leg in plan.legs:
            run_state.leg_states[leg.leg_index] = _create_initial_state(leg, plan)

        publisher = _EventPublisher(
            event_bus,
            enabled=config.publish_lifecycle_events,
            submission_id=resolved_submission_id,
            plan_id=plan.plan_id,
            correlation_id=plan.correlation_id,
        )
        stages: list[OrderStageResult] = []
        short_circuit = False
        for stage_id in STAGE_ORDER:
            if short_circuit and stage_id not in (
                OrderSubmissionStageId.RESULT_ASSEMBLY,
                OrderSubmissionStageId.OUTPUT_VALIDATION,
            ):
                continue
            stage_started = time.perf_counter()
            outcome = self._run_stage(stage_id, run_state, publisher)
            duration_ms = (time.perf_counter() - stage_started) * 1000.0
            stages.append(
                OrderStageResult(
                    stage_id=stage_id,
                    passed=outcome.passed,
                    rejection_code=outcome.rejection_code,
                    message=outcome.message,
                    duration_ms=duration_ms,
                    details=outcome.details,
                )
            )
            if not outcome.passed and stage_id in (
                OrderSubmissionStageId.PLAN_GATE,
                OrderSubmissionStageId.INPUT_INTEGRITY,
                OrderSubmissionStageId.BROKER_READINESS,
                OrderSubmissionStageId.LEG_MAPPING,
                OrderSubmissionStageId.PRE_SUBMIT_VALIDATION,
            ):
                run_state.pre_submit_rejected = True
                run_state.primary_error_code = outcome.rejection_code
                run_state.errors.append(
                    OrderErrorRecord(
                        code=outcome.rejection_code or ERROR_RESULT_INVALID,
                        message=outcome.message or "Stage failed.",
                        stage_id=stage_id,
                    )
                )
                publisher.publish(
                    OrderLifecycleEventType.PLAN_REJECTED,
                    occurred_at=context.reference_time,
                    metadata=MappingProxyType(
                        {"error_code": outcome.rejection_code or ERROR_RESULT_INVALID}
                    ),
                )
                short_circuit = True
            _logger.debug(
                "order_manager.submit.stage",
                extra={
                    "event": "order_manager.submit.stage",
                    "stage_id": stage_id.value,
                    "passed": outcome.passed,
                },
            )

        completed_at = context.reference_time
        leg_tuple = tuple(run_state.leg_states[index] for index in sorted(run_state.leg_states))
        aggregate = derive_aggregate_status(leg_tuple)
        tracker = OrderTracker(
            submission_id=resolved_submission_id,
            plan_id=plan.plan_id,
            correlation_id=plan.correlation_id,
            plan_fingerprint=plan.plan_fingerprint,
            leg_states=leg_tuple,
            aggregate_status=aggregate,
            sequence_results=tuple(run_state.sequence_results),
            started_at=started_at,
            completed_at=completed_at,
            tracker_fingerprint=compute_tracker_fingerprint(leg_tuple),
        )
        status = derive_submission_status(tracker, pre_rejected=run_state.pre_submit_rejected)
        fingerprint = (
            compute_submission_fingerprint(plan, tracker, config)
            if config.deterministic_fingerprint
            else ""
        )
        duration_ms = max((stage.duration_ms for stage in stages), default=0.0)
        pipeline_summary = OrderPipelineResult(
            total_stages=len(stages),
            passed_stages=sum(1 for stage in stages if stage.passed),
            failed_stage_id=next((stage.stage_id for stage in stages if not stage.passed), None),
            stages=tuple(stages),
            short_circuited=short_circuit,
        )
        result = OrderSubmissionResult(
            submission_id=resolved_submission_id,
            plan_id=plan.plan_id,
            correlation_id=plan.correlation_id,
            status=status,
            tracker=tracker,
            pipeline_summary=pipeline_summary,
            warnings=tuple(run_state.warnings),
            errors=tuple(run_state.errors),
            primary_error_code=run_state.primary_error_code,
            submitted_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            submission_fingerprint=fingerprint,
        )
        if config.strict_output_validation:
            validation = validate_order_submission_result(result)
            if not validation.is_valid:
                raise OrderManagerValidationError(
                    validation.errors[0].message,
                    code=validation.errors[0].code,
                    leg_index=validation.errors[0].leg_index,
                )
            if config.deterministic_fingerprint:
                recomputed = compute_submission_fingerprint(plan, tracker, config)
                if recomputed != fingerprint:
                    raise OrderManagerValidationError(
                        "submission_fingerprint mismatch.",
                        code=ERROR_RESULT_FINGERPRINT_MISMATCH,
                    )
        if not run_state.pre_submit_rejected:
            publisher.publish(
                OrderLifecycleEventType.PLAN_SUBMISSION_COMPLETED,
                occurred_at=completed_at,
                metadata=MappingProxyType({"status": status.value}),
            )
        _logger.info(
            "order_manager.submit.complete",
            extra={
                "event": "order_manager.submit.complete",
                "submission_id": resolved_submission_id,
                "status": status.value,
            },
        )
        return result

    def _run_stage(
        self,
        stage_id: OrderSubmissionStageId,
        state: _PipelineRunState,
        publisher: _EventPublisher,
    ) -> _StageOutcome:
        handlers = {
            OrderSubmissionStageId.PLAN_GATE: self._stage_plan_gate,
            OrderSubmissionStageId.INPUT_INTEGRITY: self._stage_input_integrity,
            OrderSubmissionStageId.BROKER_READINESS: self._stage_broker_readiness,
            OrderSubmissionStageId.LEG_MAPPING: self._stage_leg_mapping,
            OrderSubmissionStageId.PRE_SUBMIT_VALIDATION: self._stage_pre_submit_validation,
            OrderSubmissionStageId.SEQUENCE_EXECUTION: self._stage_sequence_execution,
            OrderSubmissionStageId.POST_SUBMIT_RECONCILIATION: self._stage_post_submit_reconciliation,
            OrderSubmissionStageId.RESULT_ASSEMBLY: self._stage_result_assembly,
            OrderSubmissionStageId.OUTPUT_VALIDATION: self._stage_output_validation,
        }
        return handlers[stage_id](state, publisher)

    def _stage_plan_gate(self, state: _PipelineRunState, publisher: _EventPublisher) -> _StageOutcome:
        _ = publisher
        if state.plan.status is not ExecutionPlanStatus.READY:
            return _StageOutcome(False, ERROR_PLAN_NOT_READY, "Plan status is not READY.")
        if not state.plan.legs:
            return _StageOutcome(False, ERROR_PLAN_NO_LEGS, "Plan has no legs.")
        if state.config.reject_expired_plans and state.plan.valid_until is not None:
            if (
                _is_timezone_aware(state.context.reference_time)
                and state.context.reference_time >= state.plan.valid_until
            ):
                return _StageOutcome(False, ERROR_PLAN_EXPIRED, "Plan has expired.")
        return _StageOutcome(True, message="Plan gate passed.")

    def _stage_input_integrity(
        self,
        state: _PipelineRunState,
        publisher: _EventPublisher,
    ) -> _StageOutcome:
        validation = validate_submission_context(state.context, state.plan, state.config)
        state.warnings.extend(validation.warnings)
        if not validation.is_valid:
            return _StageOutcome(
                False,
                validation.errors[0].code,
                validation.errors[0].message,
            )
        publisher.publish(
            OrderLifecycleEventType.PLAN_RECEIVED,
            occurred_at=state.context.reference_time,
        )
        return _StageOutcome(True, message="Input integrity passed.")

    def _stage_broker_readiness(
        self,
        state: _PipelineRunState,
        publisher: _EventPublisher,
    ) -> _StageOutcome:
        _ = publisher
        if (
            state.execution_mode is StrategyExecutionMode.ANALYSIS
            and state.config.allow_analysis_dry_run
        ):
            state.broker_calls_allowed = False
            return _StageOutcome(True, message="Analysis dry run — broker skipped.")
        if state.broker is None:
            return _StageOutcome(False, ERROR_BROKER_MISSING, "Broker client is missing.")
        if state.config.require_broker_connected and not state.broker.is_connected():
            return _StageOutcome(False, ERROR_BROKER_NOT_CONNECTED, "Broker is not connected.")
        if state.config.require_broker_authenticated and not state.broker.is_authenticated():
            return _StageOutcome(False, ERROR_BROKER_NOT_AUTHENTICATED, "Broker not authenticated.")
        session_state = state.broker.get_session_state()
        if session_state is SessionState.EXPIRED:
            return _StageOutcome(False, ERROR_BROKER_SESSION_EXPIRED, "Broker session expired.")
        if not state.broker.capabilities.order_placement:
            return _StageOutcome(
                False,
                ERROR_BROKER_PLACEMENT_UNSUPPORTED,
                "Broker does not support order placement.",
            )
        connection = state.broker.get_connection_info()
        if connection.state is ConnectionState.DEGRADED:
            state.warnings.append(
                OrderWarningRecord(
                    code=WARN_BROKER_DEGRADED,
                    message="Broker connection is degraded.",
                    stage_id=OrderSubmissionStageId.BROKER_READINESS,
                )
            )
        return _StageOutcome(True, message="Broker readiness passed.")

    def _stage_leg_mapping(self, state: _PipelineRunState, publisher: _EventPublisher) -> _StageOutcome:
        _ = publisher
        for leg in state.plan.legs:
            mapping_validation = validate_leg_mapping(leg)
            if not mapping_validation.is_valid:
                error = mapping_validation.errors[0]
                return _StageOutcome(False, error.code, error.message)
            request = map_leg_to_place_order_request(leg, state.plan)
            try:
                validate_place_order_request(request)
            except BrokerClientError as exc:
                return _StageOutcome(
                    False,
                    ERROR_MAP_BROKER_VALIDATION_FAILED,
                    exc.message,
                )
            state.mapped_requests[leg.leg_index] = request
        return _StageOutcome(True, message="Leg mapping passed.")

    def _stage_pre_submit_validation(
        self,
        state: _PipelineRunState,
        publisher: _EventPublisher,
    ) -> _StageOutcome:
        _ = publisher
        if state.plan.valid_until is not None:
            remaining = (state.plan.valid_until - state.context.reference_time).total_seconds()
            if 0 < remaining <= NEAR_EXPIRY_SECONDS:
                state.warnings.append(
                    OrderWarningRecord(
                        code=WARN_PLAN_NEAR_EXPIRY,
                        message="Plan near expiry.",
                        stage_id=OrderSubmissionStageId.PRE_SUBMIT_VALIDATION,
                    )
                )
        for leg in state.plan.legs:
            if leg.metadata.get("limit_hint_stale", "").lower() == "true":
                state.warnings.append(
                    OrderWarningRecord(
                        code=WARN_PLAN_STALE_LIMIT_HINT,
                        message="Stale limit hint.",
                        leg_index=leg.leg_index,
                        stage_id=OrderSubmissionStageId.PRE_SUBMIT_VALIDATION,
                    )
                )
        return _StageOutcome(True, message="Pre-submit validation passed.")

    def _stage_sequence_execution(
        self,
        state: _PipelineRunState,
        publisher: _EventPublisher,
    ) -> _StageOutcome:
        if state.pre_submit_rejected:
            return _StageOutcome(True, message="Skipped due to pre-submit rejection.")
        sequences = state.plan.sequences or (
            LegSequence(
                sequence_group=0,
                mode=LegSequenceMode.SIMULTANEOUS,
                leg_indices=tuple(leg.leg_index for leg in state.plan.legs),
            ),
        )
        for sequence in sorted(sequences, key=lambda item: item.sequence_group):
            group_started = time.perf_counter()
            publisher.publish(
                OrderLifecycleEventType.SEQUENCE_GROUP_STARTED,
                occurred_at=state.context.reference_time,
                sequence_group=sequence.sequence_group,
            )
            aborted = self._execute_sequence_group(sequence, state, publisher)
            duration_ms = (time.perf_counter() - group_started) * 1000.0
            state.sequence_results.append(
                OrderSequenceResult(
                    sequence_group=sequence.sequence_group,
                    mode=sequence.mode,
                    leg_indices=sequence.leg_indices,
                    completed=not aborted,
                    aborted=aborted,
                    duration_ms=duration_ms,
                )
            )
            if aborted:
                publisher.publish(
                    OrderLifecycleEventType.SEQUENCE_GROUP_ABORTED,
                    occurred_at=state.context.reference_time,
                    sequence_group=sequence.sequence_group,
                )
            else:
                publisher.publish(
                    OrderLifecycleEventType.SEQUENCE_GROUP_COMPLETED,
                    occurred_at=state.context.reference_time,
                    sequence_group=sequence.sequence_group,
                )
            if aborted:
                break
        return _StageOutcome(True, message="Sequence execution completed.")

    def _execute_sequence_group(
        self,
        sequence: LegSequence,
        state: _PipelineRunState,
        publisher: _EventPublisher,
    ) -> bool:
        """Execute one sequence group. Return True when aborted."""
        leg_map = {leg.leg_index: leg for leg in state.plan.legs}
        aborted = False
        if sequence.mode is LegSequenceMode.SIMULTANEOUS:
            for leg_index in sequence.leg_indices:
                if aborted and sequence.abort_on_leg_failure:
                    self._mark_skipped(leg_index, state, publisher)
                    continue
                leg = leg_map[leg_index]
                updated = self._submit_leg(leg, state, publisher)
                state.leg_states[leg_index] = updated
                if sequence.abort_on_leg_failure and updated.lifecycle_status in _FAILURE_TERMINAL:
                    aborted = True
            return aborted

        for leg_index in sequence.leg_indices:
            if aborted and sequence.abort_on_leg_failure:
                self._mark_skipped(leg_index, state, publisher)
                continue
            leg = leg_map[leg_index]
            updated = self._submit_leg(leg, state, publisher)
            state.leg_states[leg_index] = updated
            if sequence.abort_on_leg_failure and updated.lifecycle_status in _FAILURE_TERMINAL:
                aborted = True
                continue
            if (
                state.config.honor_sequence_delays
                and sequence.inter_leg_delay_ms > 0
                and leg_index != sequence.leg_indices[-1]
            ):
                self._sleep_fn(sequence.inter_leg_delay_ms / 1000.0)
        return aborted

    def _mark_skipped(
        self,
        leg_index: int,
        state: _PipelineRunState,
        publisher: _EventPublisher,
    ) -> None:
        current = state.leg_states[leg_index]
        if current.lifecycle_status is not OrderLifecycleStatus.PLANNED:
            return
        skipped = _append_transition(
            current,
            OrderLifecycleStatus.SKIPPED,
            occurred_at=state.context.reference_time,
            reason_code=ERROR_SEQUENCE_INVALID,
            message="Skipped due to sequence abort.",
            terminal=True,
        )
        state.leg_states[leg_index] = skipped
        publisher.publish(
            OrderLifecycleEventType.LEG_SKIPPED,
            occurred_at=state.context.reference_time,
            leg_index=leg_index,
            order_state=skipped,
        )

    def _submit_leg(
        self,
        leg: PlannedOrderLeg,
        state: _PipelineRunState,
        publisher: _EventPublisher,
    ) -> OrderState:
        current = state.leg_states[leg.leg_index]
        if not state.broker_calls_allowed:
            dry = _append_transition(
                current,
                OrderLifecycleStatus.SUBMITTED,
                occurred_at=state.context.reference_time,
                reason_code="ORDER_MANAGER.DRY_RUN.SUBMITTED",
                message="Analysis dry run submission.",
                broker_order_id=f"dry-run-{leg.leg_index}",
                attempt_count=1,
                terminal=False,
            )
            publisher.publish(
                OrderLifecycleEventType.LEG_SUBMITTED,
                occurred_at=state.context.reference_time,
                leg_index=leg.leg_index,
                order_state=dry,
            )
            return self._finalize_leg_without_polling(dry, state)

        submitting = _append_transition(
            current,
            OrderLifecycleStatus.SUBMITTING,
            occurred_at=state.context.reference_time,
            reason_code="ORDER_MANAGER.LEG.SUBMITTING",
            message="Submitting leg.",
            terminal=False,
        )
        publisher.publish(
            OrderLifecycleEventType.LEG_SUBMIT_STARTED,
            occurred_at=state.context.reference_time,
            leg_index=leg.leg_index,
            order_state=submitting,
        )
        return self._submit_leg_with_retry(leg, state, publisher, submitting)

    def _submit_leg_with_retry(
        self,
        leg: PlannedOrderLeg,
        state: _PipelineRunState,
        publisher: _EventPublisher,
        current: OrderState,
    ) -> OrderState:
        policy = state.plan.retry_policy
        attempt = 0
        current_key = leg.idempotency_key
        backoff_ms = policy.initial_backoff_ms
        last_error: BrokerClientError | None = None
        leg_started = time.perf_counter()
        timeout_ms = state.plan.timeout_policy.leg_submission_timeout_ms

        while attempt < policy.max_attempts:
            attempt += 1
            elapsed_ms = (time.perf_counter() - leg_started) * 1000.0
            if elapsed_ms > timeout_ms:
                timed_out = _append_transition(
                    current,
                    OrderLifecycleStatus.TIMEOUT,
                    occurred_at=state.context.reference_time,
                    reason_code=ERROR_LEG_TIMEOUT,
                    message="Leg submission timeout exceeded.",
                    attempt_count=attempt,
                    last_error_code=ERROR_LEG_TIMEOUT,
                    terminal=True,
                )
                publisher.publish(
                    OrderLifecycleEventType.LEG_TIMEOUT,
                    occurred_at=state.context.reference_time,
                    leg_index=leg.leg_index,
                    order_state=timed_out,
                )
                state.errors.append(
                    OrderErrorRecord(
                        code=ERROR_LEG_TIMEOUT,
                        message="Leg submission timeout exceeded.",
                        leg_index=leg.leg_index,
                    )
                )
                return timed_out

            request = map_leg_to_place_order_request(leg, state.plan, idempotency_key=current_key)
            try:
                assert state.broker is not None
                result = state.broker.place_order(request)
                filled = _extract_filled_quantity(result, leg.quantity)
                lifecycle = _broker_status_to_lifecycle(
                    result.status,
                    planned_quantity=leg.quantity,
                    filled_quantity=filled,
                )
                submitted = _append_transition(
                    current,
                    lifecycle,
                    occurred_at=state.context.reference_time,
                    reason_code="ORDER_MANAGER.LEG.SUBMITTED",
                    message=result.message,
                    broker_order_id=result.broker_order_id or result.order_id,
                    attempt_count=attempt,
                    last_broker_status=result.status,
                    idempotency_key=current_key,
                    filled_quantity=filled,
                    remaining_quantity=leg.quantity - filled,
                    terminal=lifecycle in _TERMINAL_LIFECYCLE,
                )
                if attempt > 1:
                    state.warnings.append(
                        OrderWarningRecord(
                            code=WARN_LEG_RETRY_SUCCEEDED,
                            message="Leg succeeded after retry.",
                            leg_index=leg.leg_index,
                        )
                    )
                publisher.publish(
                    OrderLifecycleEventType.LEG_SUBMITTED,
                    occurred_at=state.context.reference_time,
                    leg_index=leg.leg_index,
                    order_state=submitted,
                )
                if state.config.enable_status_polling:
                    return self._poll_leg_until_terminal(submitted, leg, state, publisher)
                return self._finalize_leg_without_polling(submitted, state, publisher)
            except BrokerAuthenticationError as exc:
                rejected = _append_transition(
                    current,
                    OrderLifecycleStatus.REJECTED,
                    occurred_at=state.context.reference_time,
                    reason_code=ERROR_BROKER_AUTH_FAILED,
                    message=exc.message,
                    attempt_count=attempt,
                    last_error_code=exc.code,
                    terminal=True,
                )
                publisher.publish(
                    OrderLifecycleEventType.LEG_REJECTED,
                    occurred_at=state.context.reference_time,
                    leg_index=leg.leg_index,
                    order_state=rejected,
                )
                return rejected
            except BrokerCapabilityError as exc:
                state.errors.append(
                    OrderErrorRecord(code=ERROR_BROKER_CAPABILITY_UNSUPPORTED, message=exc.message)
                )
                raise OrderManagerSubmissionError(
                    exc.message,
                    code=ERROR_BROKER_CAPABILITY_UNSUPPORTED,
                ) from exc
            except BrokerOrderError as exc:
                rejected = _append_transition(
                    current,
                    OrderLifecycleStatus.REJECTED,
                    occurred_at=state.context.reference_time,
                    reason_code=ERROR_LEG_REJECTED,
                    message=exc.message,
                    attempt_count=attempt,
                    last_error_code=exc.code,
                    terminal=True,
                )
                publisher.publish(
                    OrderLifecycleEventType.LEG_REJECTED,
                    occurred_at=state.context.reference_time,
                    leg_index=leg.leg_index,
                    order_state=rejected,
                )
                return rejected
            except BrokerClientError as exc:
                last_error = exc
                if not is_retryable(exc, policy) or attempt >= policy.max_attempts:
                    if exc.code in (ERROR_REQUEST_INVALID, ERROR_AUTH_EXPIRED, ERROR_AUTH_INVALID):
                        status = OrderLifecycleStatus.REJECTED
                        code = ERROR_LEG_REJECTED
                        event_type = OrderLifecycleEventType.LEG_REJECTED
                    else:
                        status = OrderLifecycleStatus.FAILED
                        code = ERROR_LEG_SUBMIT_FAILED
                        event_type = OrderLifecycleEventType.LEG_FAILED
                    failed = _append_transition(
                        current,
                        status,
                        occurred_at=state.context.reference_time,
                        reason_code=code,
                        message=exc.message,
                        attempt_count=attempt,
                        last_error_code=exc.code,
                        terminal=True,
                    )
                    publisher.publish(
                        event_type,
                        occurred_at=state.context.reference_time,
                        leg_index=leg.leg_index,
                        order_state=failed,
                    )
                    return failed
                publisher.publish(
                    OrderLifecycleEventType.LEG_RETRY_SCHEDULED,
                    occurred_at=state.context.reference_time,
                    leg_index=leg.leg_index,
                    order_state=current,
                    metadata=MappingProxyType({"backoff_ms": str(backoff_ms), "attempt": str(attempt)}),
                )
                if backoff_ms > 0:
                    self._sleep_fn(backoff_ms / 1000.0)
                backoff_ms = min(int(backoff_ms * policy.backoff_multiplier), policy.max_backoff_ms)
                if policy.idempotency_regenerate_on_retry:
                    current_key = regenerate_idempotency_key(leg, state.plan, attempt)
                publisher.publish(
                    OrderLifecycleEventType.LEG_RETRY_ATTEMPT,
                    occurred_at=state.context.reference_time,
                    leg_index=leg.leg_index,
                    order_state=current,
                    metadata=MappingProxyType({"attempt": str(attempt + 1)}),
                )
                _logger.warning(
                    "order_manager.leg.retry",
                    extra={"event": "order_manager.leg.retry", "leg_index": leg.leg_index},
                )

        failed = _append_transition(
            current,
            OrderLifecycleStatus.FAILED,
            occurred_at=state.context.reference_time,
            reason_code=ERROR_LEG_SUBMIT_FAILED,
            message=last_error.message if last_error else "Retries exhausted.",
            attempt_count=attempt,
            last_error_code=last_error.code if last_error else ERROR_LEG_SUBMIT_FAILED,
            terminal=True,
        )
        publisher.publish(
            OrderLifecycleEventType.LEG_FAILED,
            occurred_at=state.context.reference_time,
            leg_index=leg.leg_index,
            order_state=failed,
        )
        return failed

    def _poll_leg_until_terminal(
        self,
        current: OrderState,
        leg: PlannedOrderLeg,
        state: _PipelineRunState,
        publisher: _EventPublisher,
    ) -> OrderState:
        assert state.broker is not None
        order_id = current.broker_order_id
        if order_id is None:
            return current
        updated = current
        for poll_index in range(state.config.max_poll_attempts):
            if updated.terminal:
                return updated
            if poll_index > 0 and state.config.poll_interval_ms > 0:
                self._sleep_fn(state.config.poll_interval_ms / 1000.0)
            records = state.broker.fetch_orders(OrderQueryRequest(order_id=order_id))
            if not records:
                continue
            record = records[0]
            filled = _extract_filled_quantity(record, leg.quantity)
            lifecycle = _broker_status_to_lifecycle(
                record.status,
                planned_quantity=leg.quantity,
                filled_quantity=filled,
            )
            updated = self._apply_reconciled_state(
                updated,
                lifecycle,
                filled=filled,
                average_fill_price=record.price,
                broker_status=record.status,
                occurred_at=state.context.reference_time,
                publisher=publisher,
                leg_index=leg.leg_index,
            )
            if updated.terminal:
                return updated
        if not updated.terminal:
            state.warnings.append(
                OrderWarningRecord(
                    code=WARN_POLL_TIMEOUT,
                    message="Polling stopped before terminal confirmation.",
                    leg_index=leg.leg_index,
                )
            )
            if (
                state.execution_mode is StrategyExecutionMode.BACKTEST
                and state.config.partial_fill_terminal_in_backtest
                and updated.lifecycle_status is OrderLifecycleStatus.PARTIALLY_FILLED
            ):
                updated = _append_transition(
                    updated,
                    OrderLifecycleStatus.COMPLETE,
                    occurred_at=state.context.reference_time,
                    reason_code="ORDER_MANAGER.BACKTEST.PARTIAL_TERMINAL",
                    message="Backtest partial fill treated as complete.",
                    filled_quantity=updated.planned_quantity,
                    remaining_quantity=0,
                    terminal=True,
                )
        return updated

    def _finalize_leg_without_polling(
        self,
        current: OrderState,
        state: _PipelineRunState,
        publisher: _EventPublisher | None = None,
    ) -> OrderState:
        if state.execution_mode is StrategyExecutionMode.BACKTEST:
            if current.lifecycle_status in (
                OrderLifecycleStatus.OPEN,
                OrderLifecycleStatus.SUBMITTED,
                OrderLifecycleStatus.PARTIALLY_FILLED,
            ):
                filled = current.filled_quantity or current.planned_quantity
                completed = _append_transition(
                    current,
                    OrderLifecycleStatus.COMPLETE,
                    occurred_at=state.context.reference_time,
                    reason_code="ORDER_MANAGER.BACKTEST.COMPLETE",
                    message="Backtest simulation complete.",
                    filled_quantity=filled,
                    remaining_quantity=current.planned_quantity - filled,
                    terminal=True,
                )
                if publisher is not None:
                    publisher.publish(
                        OrderLifecycleEventType.LEG_COMPLETE,
                        occurred_at=state.context.reference_time,
                        leg_index=current.leg_index,
                        order_state=completed,
                    )
                return completed
        return current

    def _apply_reconciled_state(
        self,
        current: OrderState,
        lifecycle: OrderLifecycleStatus,
        *,
        filled: int,
        average_fill_price: float | None,
        broker_status: OrderStatus,
        occurred_at: datetime,
        publisher: _EventPublisher,
        leg_index: int,
    ) -> OrderState:
        if lifecycle is current.lifecycle_status and filled == current.filled_quantity:
            return current
        remaining = current.planned_quantity - filled
        terminal = lifecycle in _TERMINAL_LIFECYCLE
        updated = _append_transition(
            current,
            lifecycle,
            occurred_at=occurred_at,
            reason_code="ORDER_MANAGER.LEG.RECONCILED",
            message=f"Reconciled to {lifecycle.value}.",
            filled_quantity=filled,
            remaining_quantity=remaining,
            average_fill_price=average_fill_price,
            last_broker_status=broker_status,
            terminal=terminal,
        )
        if lifecycle is OrderLifecycleStatus.OPEN:
            publisher.publish(
                OrderLifecycleEventType.LEG_OPEN,
                occurred_at=occurred_at,
                leg_index=leg_index,
                order_state=updated,
            )
        elif lifecycle is OrderLifecycleStatus.PARTIALLY_FILLED:
            publisher.publish(
                OrderLifecycleEventType.LEG_PARTIAL_FILL,
                occurred_at=occurred_at,
                leg_index=leg_index,
                order_state=updated,
            )
        elif lifecycle is OrderLifecycleStatus.COMPLETE:
            publisher.publish(
                OrderLifecycleEventType.LEG_COMPLETE,
                occurred_at=occurred_at,
                leg_index=leg_index,
                order_state=updated,
            )
        elif lifecycle is OrderLifecycleStatus.REJECTED:
            publisher.publish(
                OrderLifecycleEventType.LEG_REJECTED,
                occurred_at=occurred_at,
                leg_index=leg_index,
                order_state=updated,
            )
        elif lifecycle is OrderLifecycleStatus.CANCELLED:
            publisher.publish(
                OrderLifecycleEventType.LEG_CANCELLED,
                occurred_at=occurred_at,
                leg_index=leg_index,
                order_state=updated,
            )
        return updated

    def _stage_post_submit_reconciliation(
        self,
        state: _PipelineRunState,
        publisher: _EventPublisher,
    ) -> _StageOutcome:
        _ = publisher
        if state.pre_submit_rejected:
            return _StageOutcome(True, message="Skipped post-submit reconciliation.")
        return _StageOutcome(True, message="Post-submit reconciliation complete.")

    def _stage_result_assembly(self, state: _PipelineRunState, publisher: _EventPublisher) -> _StageOutcome:
        _ = publisher
        return _StageOutcome(True, message="Result assembly complete.")

    def _stage_output_validation(
        self,
        state: _PipelineRunState,
        publisher: _EventPublisher,
    ) -> _StageOutcome:
        _ = publisher
        return _StageOutcome(True, message="Output validation complete.")


class OrderManager:
    """Institutional order submission and lifecycle manager."""

    def __init__(
        self,
        config: OrderManagerConfig | None = None,
        *,
        event_bus: EventBus | None = None,
        sleep_fn: _SleepFn | None = None,
    ) -> None:
        """Initialize OrderManager with config and optional event bus."""
        self._config = config or default_order_manager_config()
        self._event_bus = event_bus
        self._pipeline = OrderSubmissionPipeline(sleep_fn=sleep_fn)
        self._trackers_lock = threading.RLock()
        self._active_trackers: dict[str, OrderTracker] = {}

    @property
    def config(self) -> OrderManagerConfig:
        """Return frozen configuration."""
        return self._config

    @property
    def version(self) -> str:
        """Return module semantic version."""
        return ORDER_MANAGER_VERSION

    def submit_plan(
        self,
        plan: ExecutionPlan,
        broker_client: BaseBrokerClient,
        context: OrderSubmissionContext,
    ) -> OrderSubmissionResult:
        """Submit a READY execution plan to the broker."""
        _logger.info(
            "order_manager.submit.start",
            extra={
                "event": "order_manager.submit.start",
                "plan_id": plan.plan_id,
                "correlation_id": context.correlation_id,
            },
        )
        result = self._pipeline.execute(
            plan,
            broker_client,
            context,
            self._config,
            event_bus=self._event_bus,
        )
        with self._trackers_lock:
            self._active_trackers[result.submission_id] = result.tracker
        return result

    def cancel_plan(
        self,
        tracker: OrderTracker,
        broker_client: BaseBrokerClient,
        *,
        context: OrderSubmissionContext | None = None,
    ) -> OrderSubmissionResult:
        """Cancel all non-terminal legs in an active submission."""
        _logger.info(
            "order_manager.cancel.start",
            extra={"event": "order_manager.cancel.start", "submission_id": tracker.submission_id},
        )
        reference_time = context.reference_time if context else _utc_now()
        publisher = _EventPublisher(
            self._event_bus,
            enabled=self._config.publish_lifecycle_events,
            submission_id=tracker.submission_id,
            plan_id=tracker.plan_id,
            correlation_id=tracker.correlation_id,
        )
        updated_states: dict[int, OrderState] = {}
        errors: list[OrderErrorRecord] = []
        for state in tracker.leg_states:
            if state.terminal or state.broker_order_id is None:
                updated_states[state.leg_index] = state
                continue
            publisher.publish(
                OrderLifecycleEventType.LEG_CANCEL_REQUESTED,
                occurred_at=reference_time,
                leg_index=state.leg_index,
                order_state=state,
            )
            pending = _append_transition(
                state,
                OrderLifecycleStatus.CANCEL_PENDING,
                occurred_at=reference_time,
                reason_code="ORDER_MANAGER.LEG.CANCEL_PENDING",
                message="Cancel requested.",
                terminal=False,
            )
            try:
                record = broker_client.cancel_order(
                    CancelOrderRequest(order_id=state.broker_order_id)
                )
                if record.status is OrderStatus.CANCELLED:
                    cancelled = _append_transition(
                        pending,
                        OrderLifecycleStatus.CANCELLED,
                        occurred_at=reference_time,
                        reason_code="ORDER_MANAGER.LEG.CANCELLED",
                        message="Cancel confirmed.",
                        last_broker_status=record.status,
                        terminal=True,
                    )
                    updated_states[state.leg_index] = cancelled
                    publisher.publish(
                        OrderLifecycleEventType.LEG_CANCELLED,
                        occurred_at=reference_time,
                        leg_index=state.leg_index,
                        order_state=cancelled,
                    )
                else:
                    updated_states[state.leg_index] = pending
            except BrokerClientError as exc:
                errors.append(
                    OrderErrorRecord(
                        code=ERROR_LEG_CANCEL_FAILED,
                        message=exc.message,
                        leg_index=state.leg_index,
                    )
                )
                failed = _append_transition(
                    pending,
                    OrderLifecycleStatus.FAILED,
                    occurred_at=reference_time,
                    reason_code=ERROR_LEG_CANCEL_FAILED,
                    message=exc.message,
                    last_error_code=exc.code,
                    terminal=True,
                )
                updated_states[state.leg_index] = failed

        leg_tuple = tuple(updated_states[index] for index in sorted(updated_states))
        new_tracker = replace(
            tracker,
            leg_states=leg_tuple,
            aggregate_status=derive_aggregate_status(leg_tuple),
            completed_at=reference_time,
            tracker_fingerprint=compute_tracker_fingerprint(leg_tuple),
        )
        status = derive_submission_status(new_tracker)
        fingerprint = (
            _compute_submission_fingerprint_from_tracker(new_tracker, self._config)
            if self._config.deterministic_fingerprint
            else ""
        )
        pipeline_summary = OrderPipelineResult(
            total_stages=0,
            passed_stages=0,
            failed_stage_id=None,
            stages=(),
            short_circuited=False,
        )
        result = OrderSubmissionResult(
            submission_id=tracker.submission_id,
            plan_id=tracker.plan_id,
            correlation_id=tracker.correlation_id,
            status=status,
            tracker=new_tracker,
            pipeline_summary=pipeline_summary,
            warnings=(),
            errors=tuple(errors),
            primary_error_code=errors[0].code if errors else None,
            submitted_at=tracker.started_at,
            completed_at=reference_time,
            duration_ms=0.0,
            submission_fingerprint=fingerprint,
        )
        with self._trackers_lock:
            self._active_trackers[result.submission_id] = result.tracker
        _logger.info(
            "order_manager.cancel.complete",
            extra={"event": "order_manager.cancel.complete", "submission_id": tracker.submission_id},
        )
        return result

    def get_tracker(self, submission_id: str) -> OrderTracker | None:
        """Return cached tracker for submission_id if still held."""
        with self._trackers_lock:
            return self._active_trackers.get(submission_id)

    def validate_submission_context(
        self,
        context: OrderSubmissionContext,
        plan: ExecutionPlan,
    ) -> OrderValidationResult:
        """Validate context and plan without submitting."""
        return validate_submission_context(context, plan, self._config)

    def validate_submission_result(
        self,
        result: OrderSubmissionResult,
    ) -> OrderValidationResult:
        """Validate sealed submission result."""
        return validate_order_submission_result(result)


__all__ = [
    "ORDER_MANAGER_VERSION",
    "ORDER_STATE_SCHEMA_VERSION",
    "PRODUCER_NAME",
    "DEFAULT_POLL_INTERVAL_MS",
    "DEFAULT_MAX_POLL_ATTEMPTS",
    "STAGE_ORDER",
    "OrderSubmissionStatus",
    "OrderAggregateStatus",
    "OrderLifecycleStatus",
    "OrderLifecycleEventType",
    "OrderSubmissionStageId",
    "OrderManagerConfig",
    "OrderSubmissionContext",
    "OrderState",
    "OrderTracker",
    "OrderSubmissionResult",
    "OrderLifecycleEvent",
    "OrderStateTransition",
    "OrderSequenceResult",
    "OrderStageResult",
    "OrderPipelineResult",
    "OrderWarningRecord",
    "OrderErrorRecord",
    "OrderValidationResult",
    "OrderManager",
    "OrderManagerError",
    "OrderManagerConfigurationError",
    "OrderManagerValidationError",
    "OrderManagerContextError",
    "OrderManagerSubmissionError",
    "default_order_manager_config",
    "map_leg_to_place_order_request",
    "validate_submission_context",
    "validate_order_submission_result",
    "assert_valid_order_submission_result",
    "serialize_order_submission_result",
    "deserialize_order_submission_result",
    "compute_submission_fingerprint",
    "derive_aggregate_status",
    "derive_submission_status",
    "is_retryable",
    "map_broker_error_code",
    "regenerate_idempotency_key",
    "config_fingerprint",
]





