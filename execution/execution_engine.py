"""Institutional execution planning engine for THETA AI TRADER v1.0.

Consumes immutable :class:`RiskDecisionResult` outputs and produces a single
authoritative execution plan expressed as :class:`ExecutionPlan`. Never places
orders, communicates with brokers, or manages open positions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Final, Mapping

from core.base_engine import BaseEngine
from core.engine_context import EngineContext
from core.engine_metadata import EngineMetadata
from core.engine_result import EngineErrorRecord, EngineResult, EngineWarningRecord
from core.enums import EngineStatus
from market_data.market_snapshot import MarketSnapshot, OptionContractSnapshot, OptionType
from risk.risk_engine import (
    PositionSizingHint,
    RiskDecisionResult,
    RiskVerdict,
    RiskWarningRecord,
)
from strategy.signals import (
    SignalAction,
    StrategyExecutionMode,
    StrategyFamily,
    StructureHint,
    TradingSignal,
    from_dict as signal_from_dict,
    is_signal_expired,
    signal_fingerprint,
    to_dict as signal_to_dict,
    validate_trading_signal,
)

EXECUTION_ENGINE_VERSION: Final[str] = "1.0.0"
EXECUTION_ENGINE_SCHEMA_VERSION: Final[str] = "1.0.0"
EXECUTION_PRICE_EPSILON: Final[float] = 1e-9
DEFAULT_MAX_SLIPPAGE_BPS: Final[float] = 50.0
DEFAULT_PLAN_VALIDITY_SECONDS: Final[int] = 120
DEFAULT_RETRY_MAX_ATTEMPTS: Final[int] = 3
DEFAULT_LIMIT_OFFSET_TICKS: Final[int] = 1
DEFAULT_PRICE_BAND_PCT: Final[float] = 0.02
DEFAULT_SEQUENTIAL_INTER_LEG_DELAY_MS: Final[int] = 250
DEFAULT_TICK_SIZE: Final[float] = 0.05

ERROR_CONFIG_INVALID: Final[str] = "EXECUTION.CONFIG.INVALID"
ERROR_CONTEXT_INVALID: Final[str] = "EXECUTION.CONTEXT.INVALID"
ERROR_CONTEXT_RISK_MISSING: Final[str] = "EXECUTION.CONTEXT.RISK_MISSING"
ERROR_CONTEXT_SNAPSHOT_MISSING: Final[str] = "EXECUTION.CONTEXT.SNAPSHOT_MISSING"
ERROR_CONTEXT_CORRELATION_MISMATCH: Final[str] = "EXECUTION.CONTEXT.CORRELATION_MISMATCH"
ERROR_CONTEXT_NAIVE_TIMESTAMP: Final[str] = "EXECUTION.CONTEXT.NAIVE_TIMESTAMP"
ERROR_CONTEXT_INTEGRITY_FAILED: Final[str] = "EXECUTION.CONTEXT.INTEGRITY_FAILED"
ERROR_RISK_NOT_APPROVED: Final[str] = "EXECUTION.RISK.NOT_APPROVED"
ERROR_SIGNAL_INVALID: Final[str] = "EXECUTION.SIGNAL.INVALID"
ERROR_SIGNAL_EXPIRED: Final[str] = "EXECUTION.SIGNAL.EXPIRED"
ERROR_SIGNAL_ACTION_INVALID: Final[str] = "EXECUTION.SIGNAL.ACTION_INVALID"
ERROR_CONTRACT_MISSING: Final[str] = "EXECUTION.CONTRACT.MISSING"
ERROR_CONTRACT_MISMATCH: Final[str] = "EXECUTION.CONTRACT.MISMATCH"
ERROR_CONTRACT_INVALID: Final[str] = "EXECUTION.CONTRACT.INVALID"
ERROR_STRUCTURE_MISSING: Final[str] = "EXECUTION.STRUCTURE.MISSING"
ERROR_STRUCTURE_UNSUPPORTED: Final[str] = "EXECUTION.STRUCTURE.UNSUPPORTED"
ERROR_SIZING_HINT_REQUIRED: Final[str] = "EXECUTION.SIZING.HINT_REQUIRED"
ERROR_SIZING_INVALID_HINT: Final[str] = "EXECUTION.SIZING.INVALID_HINT"
ERROR_LEG_CONSTRUCTION_FAILED: Final[str] = "EXECUTION.LEG.CONSTRUCTION_FAILED"
ERROR_LEG_SIDE_UNKNOWN: Final[str] = "EXECUTION.LEG.SIDE_UNKNOWN"
ERROR_SEQUENCE_INVALID: Final[str] = "EXECUTION.SEQUENCE.INVALID"
ERROR_POLICY_ORDER_TYPE_BLOCKED: Final[str] = "EXECUTION.POLICY.ORDER_TYPE_BLOCKED"
ERROR_POLICY_PRODUCT_BLOCKED: Final[str] = "EXECUTION.POLICY.PRODUCT_BLOCKED"
ERROR_SLIPPAGE_PRICE_BAND_EXCEEDED: Final[str] = "EXECUTION.SLIPPAGE.PRICE_BAND_EXCEEDED"
ERROR_SLIPPAGE_MISSING_REFERENCE: Final[str] = "EXECUTION.SLIPPAGE.MISSING_REFERENCE"
ERROR_PLAN_EXPIRED: Final[str] = "EXECUTION.PLAN.EXPIRED"
ERROR_RESULT_INVALID: Final[str] = "EXECUTION.RESULT.INVALID"
ERROR_RESULT_FINGERPRINT_MISMATCH: Final[str] = "EXECUTION.RESULT.FINGERPRINT_MISMATCH"
ERROR_SERIALIZATION_UNSUPPORTED_VERSION: Final[str] = "EXECUTION.SERIALIZATION.UNSUPPORTED_VERSION"
ERROR_SERIALIZATION_MALFORMED: Final[str] = "EXECUTION.SERIALIZATION.MALFORMED"

WARN_SIGNAL_NEAR_EXPIRY: Final[str] = "EXECUTION.SIGNAL.NEAR_EXPIRY"
WARN_PLAN_NEAR_EXPIRY: Final[str] = "EXECUTION.PLAN.NEAR_EXPIRY"
WARN_CONTRACT_HEURISTIC_USED: Final[str] = "EXECUTION.CONTRACT.HEURISTIC_USED"
WARN_SLIPPAGE_WIDE_BAND: Final[str] = "EXECUTION.SLIPPAGE.WIDE_BAND"
WARN_POLICY_MARKET_DOWNGRADED: Final[str] = "EXECUTION.POLICY.MARKET_DOWNGRADED"
WARN_SIZING_SPLIT_APPLIED: Final[str] = "EXECUTION.SIZING.SPLIT_APPLIED"
WARN_SNAPSHOT_STALE: Final[str] = "EXECUTION.SNAPSHOT.STALE"
WARN_RISK_APPROVED_FORCE_SKIP: Final[str] = "EXECUTION.RISK.APPROVED_FORCE_SKIP"

_DEFAULT_RETRYABLE_CODES: Final[frozenset[str]] = frozenset(
    {
        "BROKER.TRANSIENT.TIMEOUT",
        "BROKER.TRANSIENT.RATE_LIMIT",
        "BROKER.TRANSIENT.GATEWAY",
        "BROKER.TRANSIENT.CONNECTION",
    }
)
_INSTRUMENT_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9:_\-.]+$")

_logger = logging.getLogger(__name__)


class ExecutionPlanStatus(str, Enum):
    """Execution planning outcome status."""

    READY = "ready"
    SKIPPED = "skipped"
    NO_PLAN = "no_plan"
    REJECTED = "rejected"
    INVALID = "invalid"


class ExecutionStageId(str, Enum):
    """Ordered execution planning pipeline stage identifiers."""

    RISK_VERDICT_GATE = "risk_verdict_gate"
    INPUT_INTEGRITY = "input_integrity"
    SIGNAL_VALIDATION = "signal_validation"
    CONTRACT_RESOLUTION = "contract_resolution"
    LEG_CONSTRUCTION = "leg_construction"
    SEQUENCING = "sequencing"
    POLICY_APPLICATION = "policy_application"
    SLIPPAGE_COMPUTATION = "slippage_computation"
    RETRY_ATTACHMENT = "retry_attachment"
    TIMEOUT_ATTACHMENT = "timeout_attachment"
    PRE_PLAN_VALIDATION = "pre_plan_validation"
    PLAN_ASSEMBLY = "plan_assembly"


STAGE_ORDER: Final[tuple[ExecutionStageId, ...]] = (
    ExecutionStageId.RISK_VERDICT_GATE,
    ExecutionStageId.INPUT_INTEGRITY,
    ExecutionStageId.SIGNAL_VALIDATION,
    ExecutionStageId.CONTRACT_RESOLUTION,
    ExecutionStageId.LEG_CONSTRUCTION,
    ExecutionStageId.SEQUENCING,
    ExecutionStageId.POLICY_APPLICATION,
    ExecutionStageId.SLIPPAGE_COMPUTATION,
    ExecutionStageId.RETRY_ATTACHMENT,
    ExecutionStageId.TIMEOUT_ATTACHMENT,
    ExecutionStageId.PRE_PLAN_VALIDATION,
    ExecutionStageId.PLAN_ASSEMBLY,
)


class OrderSide(str, Enum):
    """Logical order side."""

    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    """Logical order type."""

    MARKET = "market"
    LIMIT = "limit"
    SL = "sl"
    SL_M = "sl_m"


class ProductType(str, Enum):
    """Logical product type."""

    NRML = "nrml"
    MIS = "mis"
    CNC = "cnc"


class LegSequenceMode(str, Enum):
    """Multi-leg submission sequencing mode."""

    SIMULTANEOUS = "simultaneous"
    SEQUENTIAL = "sequential"
    HEDGED_FIRST = "hedged_first"


class ExecutionSkipReasonCode(str, Enum):
    """Structured skip reason codes."""

    RISK_SKIPPED = "risk_skipped"
    RISK_REJECTED = "risk_rejected"
    ORCHESTRATOR_SKIP = "orchestrator_skip"
    ANALYSIS_MODE_SKIP = "analysis_mode_skip"
    NO_TRADE_SIGNAL = "no_trade_signal"


class ContractResolutionSource(str, Enum):
    """How an instrument key was resolved."""

    CONTRACT_SELECTION = "contract_selection"
    STRUCTURE_HINT_HEURISTIC = "structure_hint_heuristic"
    TAGS_INLINE = "tags_inline"
    UNRESOLVED = "unresolved"


class ExecutionEngineError(Exception):
    """Base exception for execution engine failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        strategy_id: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.strategy_id = strategy_id
        self.field = field


class ExecutionEngineConfigurationError(ExecutionEngineError):
    """Raised when engine configuration is invalid."""


class ExecutionEngineValidationError(ExecutionEngineError):
    """Raised when input or output validation fails."""


class ExecutionEngineContextError(ExecutionEngineError):
    """Raised when execution run context is invalid."""


class ExecutionPlanningError(ExecutionEngineError):
    """Raised when a planning stage fails."""


@dataclass(frozen=True)
class ExecutionStructureOverride:
    """Per-structure-type execution overrides."""

    sequencing_mode: LegSequenceMode | None = None
    default_order_type: OrderType | None = None
    hedge_legs_first: bool = False
    max_legs_per_group: int | None = None


@dataclass(frozen=True)
class ExecutionPolicy:
    """Core execution behavior policy."""

    default_order_type: OrderType = OrderType.LIMIT
    default_product: ProductType = ProductType.NRML
    allow_market_orders_live: bool = False
    prefer_limit_orders: bool = True
    sequencing_mode: LegSequenceMode = LegSequenceMode.SIMULTANEOUS
    structure_type_overrides: Mapping[str, ExecutionStructureOverride] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class OrderTypePolicy:
    """Mode-specific order type policy."""

    live_allowed_types: frozenset[OrderType] = frozenset({OrderType.LIMIT})
    analysis_allowed_types: frozenset[OrderType] = frozenset({OrderType.LIMIT, OrderType.MARKET})
    backtest_allowed_types: frozenset[OrderType] = frozenset({OrderType.LIMIT, OrderType.MARKET})
    force_limit_for_short_premium: bool = True


@dataclass(frozen=True)
class ProductTypePolicy:
    """Product type resolution policy."""

    default_product: ProductType = ProductType.NRML
    intraday_only_strategies: frozenset[str] = frozenset()
    overnight_strategies: frozenset[str] = frozenset()
    live_product_map: Mapping[StrategyFamily, ProductType] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class RetryPolicy:
    """Retry metadata attached to execution plans."""

    max_attempts: int = DEFAULT_RETRY_MAX_ATTEMPTS
    initial_backoff_ms: int = 500
    backoff_multiplier: float = 2.0
    max_backoff_ms: int = 8000
    retryable_error_codes: frozenset[str] = _DEFAULT_RETRYABLE_CODES
    idempotency_regenerate_on_retry: bool = False

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ExecutionEngineConfigurationError(
                "max_attempts must be >= 1.",
                code=ERROR_CONFIG_INVALID,
                field="max_attempts",
            )
        if self.initial_backoff_ms < 0:
            raise ExecutionEngineConfigurationError(
                "initial_backoff_ms must be >= 0.",
                code=ERROR_CONFIG_INVALID,
                field="initial_backoff_ms",
            )
        if self.backoff_multiplier < 1.0:
            raise ExecutionEngineConfigurationError(
                "backoff_multiplier must be >= 1.0.",
                code=ERROR_CONFIG_INVALID,
                field="backoff_multiplier",
            )


@dataclass(frozen=True)
class TimeoutPolicy:
    """Timeout and validity metadata."""

    plan_validity_seconds: int = DEFAULT_PLAN_VALIDITY_SECONDS
    leg_submission_timeout_ms: int = 30000
    sequential_group_timeout_ms: int = 120000
    stage_timeout_ms: Mapping[ExecutionStageId, int] = field(
        default_factory=lambda: MappingProxyType(
            {
                ExecutionStageId.CONTRACT_RESOLUTION: 500,
                ExecutionStageId.SLIPPAGE_COMPUTATION: 200,
            }
        )
    )

    def __post_init__(self) -> None:
        if self.plan_validity_seconds <= 0:
            raise ExecutionEngineConfigurationError(
                "plan_validity_seconds must be positive.",
                code=ERROR_CONFIG_INVALID,
                field="plan_validity_seconds",
            )


@dataclass(frozen=True)
class SlippagePolicy:
    """Slippage and limit price hint policy."""

    max_slippage_bps: float = DEFAULT_MAX_SLIPPAGE_BPS
    limit_offset_ticks: int = DEFAULT_LIMIT_OFFSET_TICKS
    use_bid_ask_for_limits: bool = True
    price_band_pct: float = DEFAULT_PRICE_BAND_PCT
    per_underlying_overrides: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if self.max_slippage_bps < 0:
            raise ExecutionEngineConfigurationError(
                "max_slippage_bps must be >= 0.",
                code=ERROR_CONFIG_INVALID,
                field="max_slippage_bps",
            )


@dataclass(frozen=True)
class ExecutionEngineConfig:
    """Immutable configuration for :class:`ExecutionEngine`."""

    execution_policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)
    order_type_policy: OrderTypePolicy = field(default_factory=OrderTypePolicy)
    product_type_policy: ProductTypePolicy = field(default_factory=ProductTypePolicy)
    default_retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    default_timeout_policy: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    default_slippage_policy: SlippagePolicy = field(default_factory=SlippagePolicy)
    require_contract_selection_in_live: bool = True
    allow_structure_hint_heuristics: bool = False
    require_sizing_hint_in_live: bool = True
    allow_market_orders_live: bool = False
    split_quantity_equally_across_legs: bool = True
    default_quantity_fallback: int = 1
    short_circuit_on_failure: bool = True
    strict_correlation: bool = True
    strict_output_validation: bool = True
    deterministic_fingerprint: bool = True
    skip_planning_in_analysis: bool = False
    allow_invalid_signal_in_analysis: bool = False
    sequential_inter_leg_delay_ms: int = DEFAULT_SEQUENTIAL_INTER_LEG_DELAY_MS
    abort_on_leg_failure: bool = True
    max_snapshot_age_seconds: int | None = 300
    backtest_plan_validity_seconds: int = 3600
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.default_quantity_fallback <= 0:
            raise ExecutionEngineConfigurationError(
                "default_quantity_fallback must be positive.",
                code=ERROR_CONFIG_INVALID,
                field="default_quantity_fallback",
            )
        if not self.order_type_policy.live_allowed_types:
            raise ExecutionEngineConfigurationError(
                "live_allowed_types must not be empty.",
                code=ERROR_CONFIG_INVALID,
                field="order_type_policy.live_allowed_types",
            )


@dataclass(frozen=True)
class SelectedContractLeg:
    """Resolved contract for one planned leg."""

    leg_index: int
    instrument_key: str
    strike: float | None = None
    option_type: OptionType | None = None
    exchange: str | None = None
    lot_size: int | None = None


@dataclass(frozen=True)
class ContractSelectionResult:
    """Upstream contract selection output consumed by execution planning."""

    selection_id: str
    correlation_id: str
    underlying: str
    expiry: date
    legs: tuple[SelectedContractLeg, ...]
    selection_fingerprint: str
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class PlannedOrderLeg:
    """Broker-neutral planned order leg."""

    leg_index: int
    sequence_group: int
    instrument_key: str
    side: OrderSide
    order_type: OrderType
    product: ProductType
    quantity: int
    idempotency_key: str
    resolution_source: ContractResolutionSource
    limit_price_hint: float | None = None
    trigger_price_hint: float | None = None
    variety: str = "REGULAR"
    validity: str = "DAY"
    tag: str | None = None
    max_slippage_bps: float | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class LegSequence:
    """Sequencing metadata for a leg group."""

    sequence_group: int
    mode: LegSequenceMode
    leg_indices: tuple[int, ...]
    inter_leg_delay_ms: int = 0
    abort_on_leg_failure: bool = True


@dataclass(frozen=True)
class ExecutionPlanSummary:
    """Human-readable execution plan summary."""

    strategy_id: str
    strategy_family: StrategyFamily
    underlying: str
    leg_count: int
    total_quantity: int
    sequence_mode: LegSequenceMode
    primary_order_type: OrderType
    estimated_notional_hint: float | None = None


@dataclass(frozen=True)
class ExecutionReason:
    """Human-readable execution explanation."""

    code: str
    message: str
    severity: str
    stage_id: ExecutionStageId | None = None


@dataclass(frozen=True)
class ExecutionFactor:
    """Machine-readable execution audit factor."""

    factor_id: str
    label: str
    weight: float
    raw_value: float
    normalized_value: float
    stage_id: ExecutionStageId | None = None


@dataclass(frozen=True)
class ExecutionStageResult:
    """Single pipeline stage outcome."""

    stage_id: ExecutionStageId
    passed: bool
    rejection_code: str | None = None
    message: str | None = None
    duration_ms: float = 0.0
    details: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ExecutionPipelineResult:
    """Pipeline audit summary."""

    total_stages: int
    passed_stages: int
    failed_stage_id: ExecutionStageId | None
    stages: tuple[ExecutionStageResult, ...]
    short_circuited: bool


@dataclass(frozen=True)
class ExecutionWarningRecord:
    """Non-fatal execution planning warning."""

    code: str
    message: str
    severity: str = "WARNING"
    stage_id: ExecutionStageId | None = None
    field: str | None = None


@dataclass(frozen=True)
class ExecutionErrorRecord:
    """Structured execution planning error."""

    code: str
    message: str
    field: str | None = None
    stage_id: ExecutionStageId | None = None


@dataclass(frozen=True)
class ExecutionValidationResult:
    """Output validation outcome."""

    errors: tuple[ExecutionErrorRecord, ...] = ()
    warnings: tuple[ExecutionWarningRecord, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return ``True`` when no validation errors exist."""
        return not self.errors


@dataclass(frozen=True)
class ExecutionRunContext:
    """Immutable per-run execution planning inputs."""

    correlation_id: str
    as_of: datetime
    risk_decision: RiskDecisionResult
    market_snapshot: MarketSnapshot
    position_sizing_hint: PositionSizingHint | None = None
    contract_selection: ContractSelectionResult | None = None
    execution_mode: StrategyExecutionMode | None = None
    reference_time: datetime | None = None
    force_skip: bool = False
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ExecutionPlan:
    """Immutable sealed execution planning outcome."""

    plan_id: str
    correlation_id: str
    risk_id: str
    decision_fingerprint: str
    risk_fingerprint: str
    signal_fingerprint: str
    snapshot_id: str
    status: ExecutionPlanStatus
    trading_signal: TradingSignal
    execution_mode: StrategyExecutionMode
    legs: tuple[PlannedOrderLeg, ...]
    sequences: tuple[LegSequence, ...]
    retry_policy: RetryPolicy
    timeout_policy: TimeoutPolicy
    slippage_policy: SlippagePolicy
    execution_policy: ExecutionPolicy
    summary: ExecutionPlanSummary
    reasons: tuple[ExecutionReason, ...]
    factors: tuple[ExecutionFactor, ...]
    pipeline_summary: ExecutionPipelineResult
    planned_at: datetime
    duration_ms: float
    plan_fingerprint: str
    warnings: tuple[ExecutionWarningRecord, ...]
    errors: tuple[ExecutionErrorRecord, ...]
    valid_until: datetime | None = None
    primary_rejection_code: str | None = None
    skip_reason_code: ExecutionSkipReasonCode | None = None
    approved_risk_budget: float | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ResolvedLegContract:
    """Internal resolved contract for one leg."""

    leg_index: int
    instrument_key: str
    resolution_source: ContractResolutionSource
    strike: float | None = None
    option_type: OptionType | None = None


@dataclass(frozen=True)
class _StageOutcome:
    """Internal stage handler outcome."""

    passed: bool
    rejection_code: str | None = None
    message: str | None = None
    details: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass
class _PipelineState:
    """Mutable accumulator during pipeline execution."""

    run_context: ExecutionRunContext
    config: ExecutionEngineConfig
    reference_time: datetime
    execution_mode: StrategyExecutionMode
    warnings: list[ExecutionWarningRecord] = field(default_factory=list)
    reasons: list[ExecutionReason] = field(default_factory=list)
    factors: list[ExecutionFactor] = field(default_factory=list)
    resolved_contracts: tuple[ResolvedLegContract, ...] = ()
    resolved_legs: tuple[PlannedOrderLeg, ...] = ()
    sequences: tuple[LegSequence, ...] = ()
    primary_rejection_code: str | None = None
    elapsed_ms: float = 0.0


def default_execution_engine_config() -> ExecutionEngineConfig:
    """Return conservative default execution engine configuration."""
    return ExecutionEngineConfig(
        execution_policy=ExecutionPolicy(
            default_order_type=OrderType.LIMIT,
            default_product=ProductType.NRML,
            allow_market_orders_live=False,
            prefer_limit_orders=True,
            sequencing_mode=LegSequenceMode.SIMULTANEOUS,
            structure_type_overrides=MappingProxyType({}),
        ),
        order_type_policy=OrderTypePolicy(
            live_allowed_types=frozenset({OrderType.LIMIT}),
            analysis_allowed_types=frozenset({OrderType.LIMIT, OrderType.MARKET}),
            backtest_allowed_types=frozenset({OrderType.LIMIT, OrderType.MARKET}),
            force_limit_for_short_premium=True,
        ),
        product_type_policy=ProductTypePolicy(
            default_product=ProductType.NRML,
            intraday_only_strategies=frozenset(),
            overnight_strategies=frozenset(),
            live_product_map=MappingProxyType({}),
        ),
        default_retry_policy=RetryPolicy(),
        default_timeout_policy=TimeoutPolicy(),
        default_slippage_policy=SlippagePolicy(),
    )


def _utc_now() -> datetime:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


def _is_timezone_aware(value: datetime) -> bool:
    """Return whether datetime is timezone-aware."""
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _round_price(value: float) -> float:
    """Round price to two decimal places."""
    return round(value, 2)


def _round_money(value: float) -> float:
    """Round monetary value to two decimal places."""
    return round(value, 2)


def generate_idempotency_key(correlation_id: str, plan_id: str, leg_index: int) -> str:
    """Generate deterministic idempotency key for a planned leg."""
    payload = f"{correlation_id}|{plan_id}|{leg_index}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"exec-{digest}"


def _generate_plan_id(correlation_id: str, risk_fingerprint: str) -> str:
    """Generate deterministic plan identifier."""
    payload = f"{correlation_id}|{risk_fingerprint}|plan"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"plan-{digest}"


def validate_planned_order_leg(leg: PlannedOrderLeg) -> ExecutionValidationResult:
    """Validate a single planned order leg."""
    errors: list[ExecutionErrorRecord] = []
    warnings: list[ExecutionWarningRecord] = []
    if leg.quantity <= 0:
        errors.append(
            ExecutionErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="quantity must be positive.",
                field="quantity",
            )
        )
    if not leg.instrument_key.strip():
        errors.append(
            ExecutionErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="instrument_key must be non-empty.",
                field="instrument_key",
            )
        )
    elif not _INSTRUMENT_KEY_PATTERN.match(leg.instrument_key):
        errors.append(
            ExecutionErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="instrument_key contains invalid characters.",
                field="instrument_key",
            )
        )
    if not leg.idempotency_key.strip():
        errors.append(
            ExecutionErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="idempotency_key must be non-empty.",
                field="idempotency_key",
            )
        )
    if leg.order_type is OrderType.LIMIT:
        if leg.limit_price_hint is None or not math.isfinite(leg.limit_price_hint) or leg.limit_price_hint <= 0:
            errors.append(
                ExecutionErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="LIMIT legs require positive limit_price_hint.",
                    field="limit_price_hint",
                )
            )
    if leg.order_type in (OrderType.SL, OrderType.SL_M):
        if leg.trigger_price_hint is None or not math.isfinite(leg.trigger_price_hint) or leg.trigger_price_hint <= 0:
            errors.append(
                ExecutionErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="SL legs require positive trigger_price_hint.",
                    field="trigger_price_hint",
                )
            )
    return ExecutionValidationResult(errors=tuple(errors), warnings=tuple(warnings))


def validate_run_context(
    run_context: ExecutionRunContext,
    *,
    config: ExecutionEngineConfig,
) -> None:
    """Validate execution run context before planning."""
    if run_context is None:
        raise ExecutionEngineContextError("Run context is required.", code=ERROR_CONTEXT_INVALID)
    if not run_context.correlation_id.strip():
        raise ExecutionEngineContextError(
            "correlation_id is required.",
            code=ERROR_CONTEXT_INVALID,
            field="correlation_id",
        )
    if run_context.risk_decision is None:
        raise ExecutionEngineContextError(
            "risk_decision is required.",
            code=ERROR_CONTEXT_RISK_MISSING,
            field="risk_decision",
        )
    if run_context.market_snapshot is None:
        raise ExecutionEngineContextError(
            "market_snapshot is required.",
            code=ERROR_CONTEXT_SNAPSHOT_MISSING,
            field="market_snapshot",
        )
    if not _is_timezone_aware(run_context.as_of):
        raise ExecutionEngineContextError(
            "as_of must be timezone-aware.",
            code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
            field="as_of",
        )
    if run_context.reference_time is not None and not _is_timezone_aware(run_context.reference_time):
        raise ExecutionEngineContextError(
            "reference_time must be timezone-aware.",
            code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
            field="reference_time",
        )
    if config.strict_correlation and run_context.correlation_id != run_context.risk_decision.correlation_id:
        raise ExecutionEngineContextError(
            "correlation_id mismatch with risk_decision.",
            code=ERROR_CONTEXT_CORRELATION_MISMATCH,
            field="correlation_id",
        )
    if run_context.contract_selection is not None:
        if (
            config.strict_correlation
            and run_context.contract_selection.correlation_id != run_context.correlation_id
        ):
            raise ExecutionEngineContextError(
                "contract_selection correlation_id mismatch.",
                code=ERROR_CONTEXT_CORRELATION_MISMATCH,
                field="contract_selection.correlation_id",
            )
    snapshot_id = run_context.market_snapshot.provenance.snapshot_id
    if not snapshot_id.strip():
        raise ExecutionEngineContextError(
            "market_snapshot provenance.snapshot_id must be non-empty.",
            code=ERROR_CONTEXT_INTEGRITY_FAILED,
            field="market_snapshot.provenance.snapshot_id",
        )
    if not run_context.risk_decision.risk_fingerprint.strip():
        raise ExecutionEngineContextError(
            "risk_decision.risk_fingerprint must be non-empty.",
            code=ERROR_CONTEXT_INTEGRITY_FAILED,
            field="risk_decision.risk_fingerprint",
        )


def validate_execution_plan(plan: ExecutionPlan) -> ExecutionValidationResult:
    """Validate sealed execution plan."""
    errors: list[ExecutionErrorRecord] = []
    warnings: list[ExecutionWarningRecord] = []

    if plan.status is ExecutionPlanStatus.READY:
        if not plan.legs:
            errors.append(
                ExecutionErrorRecord(code=ERROR_RESULT_INVALID, message="READY plan must have legs.")
            )
        for leg in plan.legs:
            leg_validation = validate_planned_order_leg(leg)
            errors.extend(leg_validation.errors)
            warnings.extend(leg_validation.warnings)
            if not leg.instrument_key.strip():
                errors.append(
                    ExecutionErrorRecord(
                        code=ERROR_RESULT_INVALID,
                        message=f"Leg {leg.leg_index} unresolved instrument_key.",
                        field=f"legs[{leg.leg_index}].instrument_key",
                    )
                )

    if plan.status is ExecutionPlanStatus.REJECTED and not plan.primary_rejection_code:
        errors.append(
            ExecutionErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="REJECTED plan missing primary_rejection_code.",
                field="primary_rejection_code",
            )
        )

    if plan.status in (ExecutionPlanStatus.SKIPPED, ExecutionPlanStatus.NO_PLAN):
        if plan.skip_reason_code is None:
            errors.append(
                ExecutionErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="SKIPPED/NO_PLAN missing skip_reason_code.",
                    field="skip_reason_code",
                )
            )
        if plan.legs:
            errors.append(
                ExecutionErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="SKIPPED/NO_PLAN plans must have empty legs.",
                    field="legs",
                )
            )

    keys = [leg.idempotency_key for leg in plan.legs]
    if len(keys) != len(set(keys)):
        errors.append(
            ExecutionErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="Duplicate idempotency keys detected.",
                field="legs",
            )
        )

    if plan.valid_until is not None and plan.valid_until < plan.planned_at:
        errors.append(
            ExecutionErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="valid_until must be >= planned_at.",
                field="valid_until",
            )
        )

    recomputed = plan_fingerprint(plan)
    if recomputed != plan.plan_fingerprint:
        errors.append(
            ExecutionErrorRecord(
                code=ERROR_RESULT_FINGERPRINT_MISMATCH,
                message="plan_fingerprint mismatch.",
                field="plan_fingerprint",
            )
        )

    return ExecutionValidationResult(errors=tuple(errors), warnings=tuple(warnings))


def assert_valid_execution_plan(plan: ExecutionPlan) -> None:
    """Raise when plan validation fails."""
    validation = validate_execution_plan(plan)
    if not validation.is_valid:
        first = validation.errors[0]
        raise ExecutionEngineValidationError(first.message, code=first.code, field=first.field)


def _resolved_execution_mode(context: ExecutionRunContext) -> StrategyExecutionMode:
    """Resolve execution mode from context or risk decision."""
    if context.execution_mode is not None:
        return context.execution_mode
    return context.risk_decision.execution_mode


def _resolved_reference_time(context: ExecutionRunContext) -> datetime:
    """Resolve reference time for freshness checks."""
    return context.reference_time or context.as_of


def _is_live_mode(mode: StrategyExecutionMode) -> bool:
    """Return whether execution mode is LIVE."""
    return mode is StrategyExecutionMode.LIVE


def compute_valid_until(
    planned_at: datetime,
    signal: TradingSignal,
    timeout_policy: TimeoutPolicy,
) -> datetime:
    """Compute plan validity expiry."""
    policy_expiry = planned_at + timedelta(seconds=timeout_policy.plan_validity_seconds)
    if signal.valid_until is not None:
        return min(policy_expiry, signal.valid_until)
    return policy_expiry


def resolve_leg_quantity(
    leg_index: int,
    leg_count: int,
    hint: PositionSizingHint | None,
    *,
    config: ExecutionEngineConfig,
    execution_mode: StrategyExecutionMode,
) -> int:
    """Resolve quantity for a single leg from sizing hint."""
    if hint is None:
        if config.require_sizing_hint_in_live and _is_live_mode(execution_mode):
            raise ExecutionPlanningError(
                "Position sizing hint required in LIVE mode.",
                code=ERROR_SIZING_HINT_REQUIRED,
            )
        return config.default_quantity_fallback

    meta_key = f"leg_{leg_index}_quantity"
    if meta_key in hint.metadata:
        return int(hint.metadata[meta_key])

    if hint.proposed_units_hint is not None:
        total = int(hint.proposed_units_hint)
        if leg_count > 1 and config.split_quantity_equally_across_legs:
            base, remainder = divmod(total, leg_count)
            return base + (1 if leg_index < remainder else 0)
        return total

    raise ExecutionPlanningError(
        "Sizing hint missing proposed_units_hint.",
        code=ERROR_SIZING_INVALID_HINT,
    )


def _resolve_leg_side(
    leg_index: int,
    signal: TradingSignal,
) -> OrderSide:
    """Resolve BUY/SELL for a leg from strategy family and structure."""
    family = signal.strategy_family
    structure_type = (
        signal.structure_hint.structure_type.lower() if signal.structure_hint else ""
    )

    family_sides: dict[tuple[StrategyFamily, int], OrderSide] = {
        (StrategyFamily.IRON_CONDOR, 0): OrderSide.SELL,
        (StrategyFamily.IRON_CONDOR, 1): OrderSide.BUY,
        (StrategyFamily.IRON_CONDOR, 2): OrderSide.SELL,
        (StrategyFamily.IRON_CONDOR, 3): OrderSide.BUY,
        (StrategyFamily.SHORT_STRANGLE, 0): OrderSide.SELL,
        (StrategyFamily.SHORT_STRANGLE, 1): OrderSide.SELL,
        (StrategyFamily.BULL_PUT_SPREAD, 0): OrderSide.SELL,
        (StrategyFamily.BULL_PUT_SPREAD, 1): OrderSide.BUY,
        (StrategyFamily.BEAR_CALL_SPREAD, 0): OrderSide.SELL,
        (StrategyFamily.BEAR_CALL_SPREAD, 1): OrderSide.BUY,
        (StrategyFamily.JADE_LIZARD, 0): OrderSide.SELL,
        (StrategyFamily.JADE_LIZARD, 1): OrderSide.SELL,
        (StrategyFamily.JADE_LIZARD, 2): OrderSide.BUY,
        (StrategyFamily.LONG_VOLATILITY, 0): OrderSide.BUY,
        (StrategyFamily.LONG_VOLATILITY, 1): OrderSide.BUY,
    }
    if (family, leg_index) in family_sides:
        return family_sides[(family, leg_index)]

    structure_sides: dict[tuple[str, int], OrderSide] = {
        ("iron_condor", 0): OrderSide.SELL,
        ("iron_condor", 1): OrderSide.BUY,
        ("iron_condor", 2): OrderSide.SELL,
        ("iron_condor", 3): OrderSide.BUY,
        ("strangle", 0): OrderSide.SELL,
        ("strangle", 1): OrderSide.SELL,
        ("vertical", 0): OrderSide.SELL,
        ("vertical", 1): OrderSide.BUY,
        ("jade_lizard", 0): OrderSide.SELL,
        ("jade_lizard", 1): OrderSide.SELL,
        ("jade_lizard", 2): OrderSide.BUY,
    }
    if (structure_type, leg_index) in structure_sides:
        return structure_sides[(structure_type, leg_index)]

    if family in (StrategyFamily.SHORT_STRANGLE, StrategyFamily.IRON_CONDOR):
        return OrderSide.SELL
    if family is StrategyFamily.LONG_VOLATILITY:
        return OrderSide.BUY
    raise ExecutionPlanningError(
        f"Cannot resolve side for leg {leg_index} and family {family.value}.",
        code=ERROR_LEG_SIDE_UNKNOWN,
        strategy_id=signal.strategy_id,
    )


def _find_contract(
    snapshot: MarketSnapshot,
    *,
    strike: float,
    option_type: OptionType,
    expiry: str | None,
) -> OptionContractSnapshot | None:
    """Find matching contract in snapshot option chain."""
    target_expiry = expiry or snapshot.option_chain.metadata.expiry
    for contract in snapshot.option_chain.contracts:
        if (
            contract.strike == strike
            and contract.option_type is option_type
            and contract.expiry == target_expiry
        ):
            return contract
    return None


def _resolve_strike_for_leg(
    leg_index: int,
    signal: TradingSignal,
    snapshot: MarketSnapshot,
    structure: StructureHint,
) -> tuple[float, OptionType]:
    """Resolve strike and option type for heuristic leg resolution."""
    chain = snapshot.option_chain
    atm = chain.metadata.atm_strike
    step = chain.metadata.strike_step
    offset = structure.strikes_each_side or 1
    family = signal.strategy_family
    structure_type = structure.structure_type.lower()

    if structure.option_types and leg_index < len(structure.option_types):
        option_type = structure.option_types[leg_index]
    elif family in (StrategyFamily.BULL_PUT_SPREAD,) or structure_type == "vertical":
        option_type = OptionType.PE if leg_index < 2 else OptionType.CE
    elif leg_index % 2 == 0:
        option_type = OptionType.CE
    else:
        option_type = OptionType.PE

    if family is StrategyFamily.IRON_CONDOR or structure_type == "iron_condor":
        strikes = [
            atm - offset * step,
            atm - (offset + 1) * step,
            atm + offset * step,
            atm + (offset + 1) * step,
        ]
        return strikes[leg_index], OptionType.PE if leg_index < 2 else OptionType.CE

    if family is StrategyFamily.SHORT_STRANGLE or structure_type == "strangle":
        if leg_index == 0:
            return atm + offset * step, OptionType.CE
        return atm - offset * step, OptionType.PE

    if family is StrategyFamily.BULL_PUT_SPREAD:
        return (atm - step, atm - 2 * step)[leg_index], OptionType.PE

    if family is StrategyFamily.BEAR_CALL_SPREAD:
        return (atm + step, atm + 2 * step)[leg_index], OptionType.CE

    if family is StrategyFamily.LONG_VOLATILITY:
        return atm, OptionType.CE if leg_index == 0 else OptionType.PE

    return atm + (leg_index - 1) * step, option_type


def _resolve_from_structure_hint_heuristic(
    signal: TradingSignal,
    snapshot: MarketSnapshot,
) -> tuple[ResolvedLegContract, ...]:
    """Resolve instrument keys from structure hint and snapshot."""
    structure = signal.structure_hint
    if structure is None:
        raise ExecutionPlanningError(
            "Structure hint required for heuristic resolution.",
            code=ERROR_STRUCTURE_MISSING,
            strategy_id=signal.strategy_id,
        )
    expiry = signal.market.expiry or snapshot.option_chain.metadata.expiry
    resolved: list[ResolvedLegContract] = []
    for leg_index in range(structure.leg_count):
        strike, option_type = _resolve_strike_for_leg(leg_index, signal, snapshot, structure)
        contract = _find_contract(snapshot, strike=strike, option_type=option_type, expiry=expiry)
        if contract is None:
            raise ExecutionPlanningError(
                f"Cannot resolve contract for leg {leg_index}.",
                code=ERROR_CONTRACT_MISSING,
                strategy_id=signal.strategy_id,
            )
        instrument_key = f"{contract.exchange}:{contract.tradingsymbol}"
        resolved.append(
            ResolvedLegContract(
                leg_index=leg_index,
                instrument_key=instrument_key,
                resolution_source=ContractResolutionSource.STRUCTURE_HINT_HEURISTIC,
                strike=strike,
                option_type=option_type,
            )
        )
    return tuple(resolved)


def _resolve_inline_tags(
    run_context: ExecutionRunContext,
    leg_count: int,
) -> tuple[ResolvedLegContract, ...] | None:
    """Resolve instrument keys from inline context tags."""
    resolved: list[ResolvedLegContract] = []
    for leg_index in range(leg_count):
        key = run_context.tags.get(f"leg_{leg_index}_instrument_key")
        if key is None:
            return None
        resolved.append(
            ResolvedLegContract(
                leg_index=leg_index,
                instrument_key=key,
                resolution_source=ContractResolutionSource.TAGS_INLINE,
            )
        )
    return tuple(resolved)


def resolve_contracts(
    signal: TradingSignal,
    contract_selection: ContractSelectionResult | None,
    snapshot: MarketSnapshot,
    *,
    config: ExecutionEngineConfig,
    execution_mode: StrategyExecutionMode,
    run_context: ExecutionRunContext,
    leg_count: int,
) -> tuple[ResolvedLegContract, ...]:
    """Resolve instrument keys for each leg."""
    if contract_selection is not None:
        if signal.structure_hint is not None and len(contract_selection.legs) != signal.structure_hint.leg_count:
            raise ExecutionPlanningError(
                "Contract selection leg count mismatch with structure hint.",
                code=ERROR_CONTRACT_MISMATCH,
                strategy_id=signal.strategy_id,
            )
        if len(contract_selection.legs) != leg_count:
            raise ExecutionPlanningError(
                "Contract selection leg count mismatch.",
                code=ERROR_CONTRACT_MISMATCH,
                strategy_id=signal.strategy_id,
            )
        if contract_selection.underlying.upper() != signal.market.underlying.upper():
            raise ExecutionPlanningError(
                "Contract selection underlying mismatch.",
                code=ERROR_CONTRACT_INVALID,
                strategy_id=signal.strategy_id,
            )
        return tuple(
            ResolvedLegContract(
                leg_index=leg.leg_index,
                instrument_key=leg.instrument_key,
                resolution_source=ContractResolutionSource.CONTRACT_SELECTION,
                strike=leg.strike,
                option_type=leg.option_type,
            )
            for leg in sorted(contract_selection.legs, key=lambda item: item.leg_index)
        )

    inline = _resolve_inline_tags(run_context, leg_count)
    if inline is not None:
        return inline

    if config.require_contract_selection_in_live and _is_live_mode(execution_mode):
        raise ExecutionPlanningError(
            "Contract selection required in LIVE mode.",
            code=ERROR_CONTRACT_MISSING,
            strategy_id=signal.strategy_id,
        )

    if not config.allow_structure_hint_heuristics or signal.structure_hint is None:
        raise ExecutionPlanningError(
            "Cannot resolve contracts without selection or structure hint.",
            code=ERROR_STRUCTURE_MISSING if signal.structure_hint is None else ERROR_CONTRACT_MISSING,
            strategy_id=signal.strategy_id,
        )

    return _resolve_from_structure_hint_heuristic(signal, snapshot)


def _contract_for_leg(leg: PlannedOrderLeg, snapshot: MarketSnapshot) -> OptionContractSnapshot | None:
    """Find snapshot contract matching a planned leg instrument key."""
    _, _, symbol = leg.instrument_key.partition(":")
    if not symbol:
        symbol = leg.instrument_key
    for contract in snapshot.option_chain.contracts:
        tradingsymbol = contract.tradingsymbol
        if tradingsymbol == symbol or f"{contract.exchange}:{tradingsymbol}" == leg.instrument_key:
            return contract
    return None


def _tick_size_for_instrument(leg: PlannedOrderLeg, snapshot: MarketSnapshot) -> float:
    """Resolve tick size for an instrument."""
    contract = _contract_for_leg(leg, snapshot)
    if contract is not None and contract.tick_size is not None and contract.tick_size > 0:
        return contract.tick_size
    return DEFAULT_TICK_SIZE


def _reference_price_for_leg(
    leg: PlannedOrderLeg,
    snapshot: MarketSnapshot,
) -> float:
    """Resolve reference price from snapshot for a leg."""
    contract = _contract_for_leg(leg, snapshot)
    if contract is None:
        raise ExecutionPlanningError(
            f"No reference price for leg {leg.leg_index}.",
            code=ERROR_SLIPPAGE_MISSING_REFERENCE,
        )
    if contract.ltp > 0 and math.isfinite(contract.ltp):
        return contract.ltp
    if contract.bid > 0 and contract.ask > 0:
        return (contract.bid + contract.ask) / 2.0
    raise ExecutionPlanningError(
        f"No reference price for leg {leg.leg_index}.",
        code=ERROR_SLIPPAGE_MISSING_REFERENCE,
    )


def _band_reference_for_leg(
    leg: PlannedOrderLeg,
    snapshot: MarketSnapshot,
    *,
    slippage_policy: SlippagePolicy,
) -> float:
    """Reference price for price-band validation aligned with limit computation."""
    contract = _contract_for_leg(leg, snapshot)
    if leg.side is OrderSide.BUY:
        if slippage_policy.use_bid_ask_for_limits and contract is not None and contract.ask > 0:
            return contract.ask
    elif slippage_policy.use_bid_ask_for_limits and contract is not None and contract.bid > 0:
        return contract.bid
    return _reference_price_for_leg(leg, snapshot)


def validate_price_band(limit_price: float, reference_price: float, price_band_pct: float) -> bool:
    """Return True when limit is within price band of reference."""
    lower = reference_price * (1.0 - price_band_pct)
    upper = reference_price * (1.0 + price_band_pct)
    return lower <= limit_price <= upper


def compute_limit_price_hint(
    leg: PlannedOrderLeg,
    snapshot: MarketSnapshot,
    *,
    slippage_policy: SlippagePolicy,
) -> float:
    """Compute limit price hint from snapshot reference prices."""
    ref_price = _reference_price_for_leg(leg, snapshot)
    tick_size = _tick_size_for_instrument(leg, snapshot)
    offset = slippage_policy.limit_offset_ticks * tick_size
    contract = _contract_for_leg(leg, snapshot)

    if leg.side is OrderSide.BUY:
        if slippage_policy.use_bid_ask_for_limits and contract is not None and contract.ask > 0:
            base = contract.ask
        else:
            base = ref_price
        raw = base + offset
        return _round_price(math.floor(raw / tick_size) * tick_size)

    if slippage_policy.use_bid_ask_for_limits and contract is not None and contract.bid > 0:
        base = contract.bid
    else:
        base = ref_price
    raw = max(tick_size, base - offset)
    return _round_price(math.ceil(raw / tick_size) * tick_size)


def _allowed_order_types(
    execution_mode: StrategyExecutionMode,
    policy: OrderTypePolicy,
) -> frozenset[OrderType]:
    """Return allowed order types for execution mode."""
    if execution_mode is StrategyExecutionMode.LIVE:
        return policy.live_allowed_types
    if execution_mode is StrategyExecutionMode.ANALYSIS:
        return policy.analysis_allowed_types
    return policy.backtest_allowed_types


def _resolve_order_type(
    leg: PlannedOrderLeg,
    signal: TradingSignal,
    *,
    policy: ExecutionPolicy,
    order_type_policy: OrderTypePolicy,
    execution_mode: StrategyExecutionMode,
    override: ExecutionStructureOverride | None,
) -> OrderType:
    """Resolve order type for a leg."""
    allowed = _allowed_order_types(execution_mode, order_type_policy)
    candidate = policy.default_order_type
    if override is not None and override.default_order_type is not None:
        candidate = override.default_order_type
    if policy.prefer_limit_orders and OrderType.LIMIT in allowed:
        candidate = OrderType.LIMIT
    if (
        order_type_policy.force_limit_for_short_premium
        and leg.side is OrderSide.SELL
        and OrderType.LIMIT in allowed
    ):
        candidate = OrderType.LIMIT
    if execution_mode is StrategyExecutionMode.LIVE and not policy.allow_market_orders_live:
        candidate = OrderType.LIMIT
    if candidate not in allowed:
        if OrderType.LIMIT in allowed:
            return OrderType.LIMIT
        raise ExecutionPlanningError(
            f"Order type {candidate.value} blocked for mode {execution_mode.value}.",
            code=ERROR_POLICY_ORDER_TYPE_BLOCKED,
            strategy_id=signal.strategy_id,
        )
    return candidate


def _resolve_product_type(
    leg: PlannedOrderLeg,
    signal: TradingSignal,
    *,
    product_policy: ProductTypePolicy,
    execution_mode: StrategyExecutionMode,
) -> ProductType:
    """Resolve product type for a leg."""
    if signal.strategy_id in product_policy.intraday_only_strategies:
        return ProductType.MIS
    if signal.strategy_id in product_policy.overnight_strategies:
        return ProductType.NRML
    if execution_mode is StrategyExecutionMode.LIVE:
        mapped = product_policy.live_product_map.get(signal.strategy_family)
        if mapped is not None:
            return mapped
    return product_policy.default_product


def apply_execution_policy(
    legs: tuple[PlannedOrderLeg, ...],
    signal: TradingSignal,
    *,
    policy: ExecutionPolicy,
    order_type_policy: OrderTypePolicy,
    product_policy: ProductTypePolicy,
    execution_mode: StrategyExecutionMode,
    warnings: list[ExecutionWarningRecord],
) -> tuple[PlannedOrderLeg, ...]:
    """Apply execution policies to planned legs."""
    override_key = signal.structure_hint.structure_type if signal.structure_hint else ""
    override = policy.structure_type_overrides.get(override_key)
    updated: list[PlannedOrderLeg] = []
    for leg in legs:
        original_type = leg.order_type
        order_type = _resolve_order_type(
            leg,
            signal,
            policy=policy,
            order_type_policy=order_type_policy,
            execution_mode=execution_mode,
            override=override,
        )
        if original_type is OrderType.MARKET and order_type is OrderType.LIMIT:
            warnings.append(
                ExecutionWarningRecord(
                    code=WARN_POLICY_MARKET_DOWNGRADED,
                    message=f"Leg {leg.leg_index} MARKET downgraded to LIMIT.",
                    stage_id=ExecutionStageId.POLICY_APPLICATION,
                )
            )
        product = _resolve_product_type(
            leg,
            signal,
            product_policy=product_policy,
            execution_mode=execution_mode,
        )
        updated.append(replace(leg, order_type=order_type, product=product))
    return tuple(updated)


def _resolve_sequencing_mode(
    signal: TradingSignal,
    config: ExecutionEngineConfig,
) -> LegSequenceMode:
    """Resolve sequencing mode from signal and config."""
    if signal.structure_hint is not None:
        override = config.execution_policy.structure_type_overrides.get(
            signal.structure_hint.structure_type
        )
        if override is not None:
            if override.hedge_legs_first:
                return LegSequenceMode.HEDGED_FIRST
            if override.sequencing_mode is not None:
                return override.sequencing_mode
    family_modes: dict[StrategyFamily, LegSequenceMode] = {
        StrategyFamily.SHORT_STRANGLE: LegSequenceMode.HEDGED_FIRST,
    }
    return family_modes.get(signal.strategy_family, config.execution_policy.sequencing_mode)


def _identify_hedge_leg_indices(legs: tuple[PlannedOrderLeg, ...]) -> tuple[int, ...]:
    """Identify protective (BUY) leg indices."""
    return tuple(leg.leg_index for leg in legs if leg.side is OrderSide.BUY)


def build_sequences(
    legs: tuple[PlannedOrderLeg, ...],
    signal: TradingSignal,
    *,
    config: ExecutionEngineConfig,
) -> tuple[LegSequence, ...]:
    """Build leg sequence metadata from planned legs."""
    mode = _resolve_sequencing_mode(signal, config)
    if len(legs) <= 1:
        return (
            LegSequence(
                sequence_group=0,
                mode=LegSequenceMode.SIMULTANEOUS,
                leg_indices=(0,) if legs else (),
                inter_leg_delay_ms=0,
                abort_on_leg_failure=True,
            ),
        )

    if mode is LegSequenceMode.HEDGED_FIRST:
        hedge_indices = _identify_hedge_leg_indices(legs)
        short_indices = tuple(
            leg.leg_index for leg in legs if leg.leg_index not in hedge_indices
        )
        sequences: list[LegSequence] = []
        if hedge_indices:
            sequences.append(
                LegSequence(
                    sequence_group=0,
                    mode=LegSequenceMode.SEQUENTIAL,
                    leg_indices=hedge_indices,
                    inter_leg_delay_ms=config.sequential_inter_leg_delay_ms,
                    abort_on_leg_failure=True,
                )
            )
        if short_indices:
            sequences.append(
                LegSequence(
                    sequence_group=1 if hedge_indices else 0,
                    mode=LegSequenceMode.SIMULTANEOUS,
                    leg_indices=short_indices,
                    inter_leg_delay_ms=0,
                    abort_on_leg_failure=True,
                )
            )
        return tuple(sequences)

    return (
        LegSequence(
            sequence_group=0,
            mode=mode,
            leg_indices=tuple(leg.leg_index for leg in sorted(legs, key=lambda item: item.leg_index)),
            inter_leg_delay_ms=(
                config.sequential_inter_leg_delay_ms if mode is LegSequenceMode.SEQUENTIAL else 0
            ),
            abort_on_leg_failure=config.abort_on_leg_failure,
        ),
    )


def _build_summary(
    signal: TradingSignal,
    legs: tuple[PlannedOrderLeg, ...],
    sequences: tuple[LegSequence, ...],
) -> ExecutionPlanSummary:
    """Build execution plan summary."""
    order_types = [leg.order_type for leg in legs]
    primary_order_type = max(set(order_types), key=order_types.count) if order_types else OrderType.LIMIT
    sequence_mode = sequences[0].mode if sequences else LegSequenceMode.SIMULTANEOUS
    return ExecutionPlanSummary(
        strategy_id=signal.strategy_id,
        strategy_family=signal.strategy_family,
        underlying=signal.market.underlying,
        leg_count=len(legs),
        total_quantity=sum(leg.quantity for leg in legs),
        sequence_mode=sequence_mode,
        primary_order_type=primary_order_type,
        estimated_notional_hint=None,
    )


class ExecutionPlanningPipeline:
    """Stateless ordered multi-stage execution planning pipeline."""

    def __init__(self) -> None:
        self._handlers = {
            ExecutionStageId.RISK_VERDICT_GATE: self._stage_risk_verdict_gate,
            ExecutionStageId.INPUT_INTEGRITY: self._stage_input_integrity,
            ExecutionStageId.SIGNAL_VALIDATION: self._stage_signal_validation,
            ExecutionStageId.CONTRACT_RESOLUTION: self._stage_contract_resolution,
            ExecutionStageId.LEG_CONSTRUCTION: self._stage_leg_construction,
            ExecutionStageId.SEQUENCING: self._stage_sequencing,
            ExecutionStageId.POLICY_APPLICATION: self._stage_policy_application,
            ExecutionStageId.SLIPPAGE_COMPUTATION: self._stage_slippage_computation,
            ExecutionStageId.RETRY_ATTACHMENT: self._stage_retry_attachment,
            ExecutionStageId.TIMEOUT_ATTACHMENT: self._stage_timeout_attachment,
            ExecutionStageId.PRE_PLAN_VALIDATION: self._stage_pre_plan_validation,
            ExecutionStageId.PLAN_ASSEMBLY: self._stage_plan_assembly,
        }

    def apply(
        self,
        run_context: ExecutionRunContext,
        *,
        config: ExecutionEngineConfig,
        state: _PipelineState,
    ) -> ExecutionPipelineResult:
        """Apply ordered execution planning stages."""
        stages: list[ExecutionStageResult] = []
        for stage_id in STAGE_ORDER:
            started = time.perf_counter()
            outcome = self._handlers[stage_id](state)
            duration_ms = (time.perf_counter() - started) * 1000.0
            if duration_ms > config.default_timeout_policy.stage_timeout_ms.get(stage_id, float("inf")):
                _logger.warning(
                    "execution.plan.stage_timeout",
                    extra={"event": "execution.plan.stage_timeout", "stage_id": stage_id.value},
                )
            stage_result = ExecutionStageResult(
                stage_id=stage_id,
                passed=outcome.passed,
                rejection_code=outcome.rejection_code,
                message=outcome.message,
                duration_ms=duration_ms,
                details=outcome.details,
            )
            stages.append(stage_result)
            if not outcome.passed:
                state.primary_rejection_code = outcome.rejection_code
                if config.short_circuit_on_failure:
                    break

        passed_count = sum(1 for stage in stages if stage.passed)
        failed_stage = next((stage.stage_id for stage in stages if not stage.passed), None)
        return ExecutionPipelineResult(
            total_stages=len(stages),
            passed_stages=passed_count,
            failed_stage_id=failed_stage,
            stages=tuple(stages),
            short_circuited=failed_stage is not None and config.short_circuit_on_failure,
        )

    def _stage_risk_verdict_gate(self, state: _PipelineState) -> _StageOutcome:
        verdict = state.run_context.risk_decision.verdict
        if verdict is not RiskVerdict.APPROVED:
            return _StageOutcome(
                False,
                ERROR_RISK_NOT_APPROVED,
                f"Risk verdict {verdict.value} is not APPROVED.",
            )
        return _StageOutcome(True, message="Risk verdict APPROVED.")

    def _stage_input_integrity(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        if config.strict_correlation and context.correlation_id != context.risk_decision.correlation_id:
            return _StageOutcome(False, ERROR_CONTEXT_CORRELATION_MISMATCH, "correlation_id mismatch.")
        snapshot_id = context.market_snapshot.provenance.snapshot_id
        if not snapshot_id.strip():
            return _StageOutcome(False, ERROR_CONTEXT_INTEGRITY_FAILED, "snapshot_id empty.")
        signal = context.risk_decision.trading_signal
        if signal.market.underlying.upper() != context.market_snapshot.underlying.symbol.upper():
            state.warnings.append(
                ExecutionWarningRecord(
                    code=WARN_SNAPSHOT_STALE,
                    message="Signal underlying differs from snapshot underlying.",
                    stage_id=ExecutionStageId.INPUT_INTEGRITY,
                )
            )
        if config.max_snapshot_age_seconds is not None:
            age = context.market_snapshot.freshness.age_seconds
            if age > config.max_snapshot_age_seconds:
                state.warnings.append(
                    ExecutionWarningRecord(
                        code=WARN_SNAPSHOT_STALE,
                        message=f"Snapshot age {age:.0f}s exceeds threshold.",
                        stage_id=ExecutionStageId.INPUT_INTEGRITY,
                    )
                )
        return _StageOutcome(True, message="Input integrity passed.")

    def _stage_signal_validation(self, state: _PipelineState) -> _StageOutcome:
        signal = state.run_context.risk_decision.trading_signal
        validation = validate_trading_signal(signal)
        if not validation.is_valid:
            if state.config.allow_invalid_signal_in_analysis and state.execution_mode is StrategyExecutionMode.ANALYSIS:
                for item in validation.errors:
                    state.warnings.append(
                        ExecutionWarningRecord(
                            code=item.code,
                            message=item.message,
                            stage_id=ExecutionStageId.SIGNAL_VALIDATION,
                            field=item.field,
                        )
                    )
            else:
                return _StageOutcome(
                    False,
                    ERROR_SIGNAL_INVALID,
                    validation.errors[0].message if validation.errors else "Signal invalid.",
                )
        if signal.action in (SignalAction.NO_TRADE, SignalAction.ABSTAIN):
            return _StageOutcome(
                False,
                ERROR_SIGNAL_ACTION_INVALID,
                f"Signal action {signal.action.value} invalid on approved path.",
            )
        if is_signal_expired(signal, reference_time=state.reference_time):
            return _StageOutcome(False, ERROR_SIGNAL_EXPIRED, "Signal expired at reference time.")
        remaining = (signal.valid_until - state.reference_time).total_seconds() if signal.valid_until else math.inf
        if remaining <= 30:
            state.warnings.append(
                ExecutionWarningRecord(
                    code=WARN_SIGNAL_NEAR_EXPIRY,
                    message="Signal near expiry.",
                    stage_id=ExecutionStageId.SIGNAL_VALIDATION,
                )
            )
        return _StageOutcome(True, message="Signal validation passed.")

    def _stage_contract_resolution(self, state: _PipelineState) -> _StageOutcome:
        signal = state.run_context.risk_decision.trading_signal
        structure = signal.structure_hint
        leg_count = (
            state.run_context.contract_selection.legs.__len__()
            if state.run_context.contract_selection
            else (structure.leg_count if structure else 1)
        )
        try:
            contracts = resolve_contracts(
                signal,
                state.run_context.contract_selection,
                state.run_context.market_snapshot,
                config=state.config,
                execution_mode=state.execution_mode,
                run_context=state.run_context,
                leg_count=leg_count,
            )
        except ExecutionPlanningError as exc:
            return _StageOutcome(False, exc.code, exc.message)
        if any(item.resolution_source is ContractResolutionSource.STRUCTURE_HINT_HEURISTIC for item in contracts):
            state.warnings.append(
                ExecutionWarningRecord(
                    code=WARN_CONTRACT_HEURISTIC_USED,
                    message="Structure hint heuristic resolution used.",
                    stage_id=ExecutionStageId.CONTRACT_RESOLUTION,
                )
            )
        state.resolved_contracts = contracts
        return _StageOutcome(True, message="Contract resolution passed.", details={"leg_count": leg_count})

    def _stage_leg_construction(self, state: _PipelineState) -> _StageOutcome:
        signal = state.run_context.risk_decision.trading_signal
        legs: list[PlannedOrderLeg] = []
        try:
            for contract in sorted(state.resolved_contracts, key=lambda item: item.leg_index):
                quantity = resolve_leg_quantity(
                    contract.leg_index,
                    len(state.resolved_contracts),
                    state.run_context.position_sizing_hint,
                    config=state.config,
                    execution_mode=state.execution_mode,
                )
                if quantity <= 0:
                    return _StageOutcome(
                        False,
                        ERROR_SIZING_INVALID_HINT,
                        f"Leg {contract.leg_index} quantity must be positive.",
                    )
                side = _resolve_leg_side(contract.leg_index, signal)
                legs.append(
                    PlannedOrderLeg(
                        leg_index=contract.leg_index,
                        sequence_group=0,
                        instrument_key=contract.instrument_key,
                        side=side,
                        order_type=state.config.execution_policy.default_order_type,
                        product=state.config.execution_policy.default_product,
                        quantity=quantity,
                        idempotency_key="",
                        resolution_source=contract.resolution_source,
                        tag=signal.strategy_id,
                    )
                )
        except ExecutionPlanningError as exc:
            return _StageOutcome(False, exc.code, exc.message)
        except ExecutionEngineError as exc:
            return _StageOutcome(False, exc.code, exc.message)
        if len(state.resolved_contracts) > 1 and state.config.split_quantity_equally_across_legs:
            state.warnings.append(
                ExecutionWarningRecord(
                    code=WARN_SIZING_SPLIT_APPLIED,
                    message="Quantity split applied across legs.",
                    stage_id=ExecutionStageId.LEG_CONSTRUCTION,
                )
            )
        state.resolved_legs = tuple(sorted(legs, key=lambda item: item.leg_index))
        return _StageOutcome(True, message="Leg construction passed.")

    def _stage_sequencing(self, state: _PipelineState) -> _StageOutcome:
        signal = state.run_context.risk_decision.trading_signal
        sequences = build_sequences(state.resolved_legs, signal, config=state.config)
        updated_legs: list[PlannedOrderLeg] = []
        leg_to_group = {idx: seq.sequence_group for seq in sequences for idx in seq.leg_indices}
        for leg in state.resolved_legs:
            updated_legs.append(replace(leg, sequence_group=leg_to_group.get(leg.leg_index, 0)))
        state.resolved_legs = tuple(updated_legs)
        state.sequences = sequences
        return _StageOutcome(True, message="Sequencing passed.")

    def _stage_policy_application(self, state: _PipelineState) -> _StageOutcome:
        signal = state.run_context.risk_decision.trading_signal
        try:
            state.resolved_legs = apply_execution_policy(
                state.resolved_legs,
                signal,
                policy=state.config.execution_policy,
                order_type_policy=state.config.order_type_policy,
                product_policy=state.config.product_type_policy,
                execution_mode=state.execution_mode,
                warnings=state.warnings,
            )
        except ExecutionPlanningError as exc:
            return _StageOutcome(False, exc.code, exc.message)
        return _StageOutcome(True, message="Policy application passed.")

    def _stage_slippage_computation(self, state: _PipelineState) -> _StageOutcome:
        snapshot = state.run_context.market_snapshot
        slippage_policy = state.config.default_slippage_policy
        underlying = state.run_context.risk_decision.trading_signal.market.underlying.upper()
        max_bps = slippage_policy.per_underlying_overrides.get(
            underlying,
            slippage_policy.max_slippage_bps,
        )
        updated: list[PlannedOrderLeg] = []
        for leg in state.resolved_legs:
            max_slippage = max_bps
            limit_hint = leg.limit_price_hint
            if leg.order_type is OrderType.LIMIT:
                try:
                    limit_hint = compute_limit_price_hint(leg, snapshot, slippage_policy=slippage_policy)
                    band_ref = _band_reference_for_leg(leg, snapshot, slippage_policy=slippage_policy)
                    if not validate_price_band(limit_hint, band_ref, slippage_policy.price_band_pct):
                        return _StageOutcome(
                            False,
                            ERROR_SLIPPAGE_PRICE_BAND_EXCEEDED,
                            f"Leg {leg.leg_index} limit outside price band.",
                        )
                except ExecutionPlanningError as exc:
                    return _StageOutcome(False, exc.code, exc.message)
            updated.append(replace(leg, limit_price_hint=limit_hint, max_slippage_bps=max_slippage))
        state.resolved_legs = tuple(updated)
        return _StageOutcome(True, message="Slippage computation passed.")

    def _stage_retry_attachment(self, state: _PipelineState) -> _StageOutcome:
        return _StageOutcome(True, message="Retry policy attached.")

    def _stage_timeout_attachment(self, state: _PipelineState) -> _StageOutcome:
        return _StageOutcome(True, message="Timeout policy attached.")

    def _stage_pre_plan_validation(self, state: _PipelineState) -> _StageOutcome:
        indices = [leg.leg_index for leg in state.resolved_legs]
        if len(indices) != len(set(indices)):
            return _StageOutcome(False, ERROR_LEG_CONSTRUCTION_FAILED, "Duplicate leg_index values.")
        for sequence in state.sequences:
            for leg_index in sequence.leg_indices:
                if leg_index not in indices:
                    return _StageOutcome(
                        False,
                        ERROR_SEQUENCE_INVALID,
                        f"Sequence references unknown leg {leg_index}.",
                    )
        return _StageOutcome(True, message="Pre-plan validation passed.")

    def _stage_plan_assembly(self, state: _PipelineState) -> _StageOutcome:
        state.reasons.append(
            ExecutionReason(
                code="EXECUTION.PLAN.READY",
                message=f"All {len(STAGE_ORDER)} planning stages passed.",
                severity="INFO",
                stage_id=ExecutionStageId.PLAN_ASSEMBLY,
            )
        )
        return _StageOutcome(True, message="Plan assembly passed.")


def plan_fingerprint(plan: ExecutionPlan) -> str:
    """Compute deterministic SHA-256 fingerprint for ExecutionPlan."""
    payload = {
        "schema_version": EXECUTION_ENGINE_SCHEMA_VERSION,
        "correlation_id": plan.correlation_id,
        "risk_fingerprint": plan.risk_fingerprint,
        "decision_fingerprint": plan.decision_fingerprint,
        "signal_fingerprint": plan.signal_fingerprint,
        "snapshot_id": plan.snapshot_id,
        "status": plan.status.value,
        "primary_rejection_code": plan.primary_rejection_code,
        "skip_reason_code": plan.skip_reason_code.value if plan.skip_reason_code else None,
        "execution_mode": plan.execution_mode.value,
        "approved_risk_budget": _round_money(plan.approved_risk_budget)
        if plan.approved_risk_budget is not None
        else None,
        "legs": [
            {
                "leg_index": leg.leg_index,
                "sequence_group": leg.sequence_group,
                "instrument_key": leg.instrument_key,
                "side": leg.side.value,
                "order_type": leg.order_type.value,
                "product": leg.product.value,
                "quantity": leg.quantity,
                "limit_price_hint": _round_price(leg.limit_price_hint)
                if leg.limit_price_hint is not None
                else None,
                "idempotency_key": leg.idempotency_key,
            }
            for leg in plan.legs
        ],
        "sequences": [
            {
                "sequence_group": seq.sequence_group,
                "mode": seq.mode.value,
                "leg_indices": list(seq.leg_indices),
            }
            for seq in plan.sequences
        ],
        "pipeline_passed": plan.pipeline_summary.passed_stages,
        "pipeline_failed_stage": (
            plan.pipeline_summary.failed_stage_id.value
            if plan.pipeline_summary.failed_stage_id
            else None
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def leg_to_dict(leg: PlannedOrderLeg) -> dict[str, Any]:
    """Convert PlannedOrderLeg to JSON-serializable dict."""
    return {
        "leg_index": leg.leg_index,
        "sequence_group": leg.sequence_group,
        "instrument_key": leg.instrument_key,
        "side": leg.side.value,
        "order_type": leg.order_type.value,
        "product": leg.product.value,
        "quantity": leg.quantity,
        "limit_price_hint": leg.limit_price_hint,
        "trigger_price_hint": leg.trigger_price_hint,
        "variety": leg.variety,
        "validity": leg.validity,
        "tag": leg.tag,
        "idempotency_key": leg.idempotency_key,
        "max_slippage_bps": leg.max_slippage_bps,
        "resolution_source": leg.resolution_source.value,
        "metadata": dict(leg.metadata),
    }


def leg_from_dict(data: Mapping[str, Any]) -> PlannedOrderLeg:
    """Deserialize PlannedOrderLeg from dict."""
    return PlannedOrderLeg(
        leg_index=int(data["leg_index"]),
        sequence_group=int(data["sequence_group"]),
        instrument_key=str(data["instrument_key"]),
        side=OrderSide(str(data["side"])),
        order_type=OrderType(str(data["order_type"])),
        product=ProductType(str(data["product"])),
        quantity=int(data["quantity"]),
        idempotency_key=str(data["idempotency_key"]),
        resolution_source=ContractResolutionSource(str(data["resolution_source"])),
        limit_price_hint=data.get("limit_price_hint"),
        trigger_price_hint=data.get("trigger_price_hint"),
        variety=str(data.get("variety", "REGULAR")),
        validity=str(data.get("validity", "DAY")),
        tag=data.get("tag"),
        max_slippage_bps=data.get("max_slippage_bps"),
        metadata=MappingProxyType(dict(data.get("metadata", {}))),
    )


def _sequence_to_dict(sequence: LegSequence) -> dict[str, Any]:
    return {
        "sequence_group": sequence.sequence_group,
        "mode": sequence.mode.value,
        "leg_indices": list(sequence.leg_indices),
        "inter_leg_delay_ms": sequence.inter_leg_delay_ms,
        "abort_on_leg_failure": sequence.abort_on_leg_failure,
    }


def _sequence_from_dict(data: Mapping[str, Any]) -> LegSequence:
    return LegSequence(
        sequence_group=int(data["sequence_group"]),
        mode=LegSequenceMode(str(data["mode"])),
        leg_indices=tuple(int(item) for item in data["leg_indices"]),
        inter_leg_delay_ms=int(data.get("inter_leg_delay_ms", 0)),
        abort_on_leg_failure=bool(data.get("abort_on_leg_failure", True)),
    )


def _retry_policy_to_dict(policy: RetryPolicy) -> dict[str, Any]:
    return {
        "max_attempts": policy.max_attempts,
        "initial_backoff_ms": policy.initial_backoff_ms,
        "backoff_multiplier": policy.backoff_multiplier,
        "max_backoff_ms": policy.max_backoff_ms,
        "retryable_error_codes": sorted(policy.retryable_error_codes),
        "idempotency_regenerate_on_retry": policy.idempotency_regenerate_on_retry,
    }


def _retry_policy_from_dict(data: Mapping[str, Any]) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=int(data.get("max_attempts", DEFAULT_RETRY_MAX_ATTEMPTS)),
        initial_backoff_ms=int(data.get("initial_backoff_ms", 500)),
        backoff_multiplier=float(data.get("backoff_multiplier", 2.0)),
        max_backoff_ms=int(data.get("max_backoff_ms", 8000)),
        retryable_error_codes=frozenset(data.get("retryable_error_codes", _DEFAULT_RETRYABLE_CODES)),
        idempotency_regenerate_on_retry=bool(data.get("idempotency_regenerate_on_retry", False)),
    )


def _timeout_policy_to_dict(policy: TimeoutPolicy) -> dict[str, Any]:
    return {
        "plan_validity_seconds": policy.plan_validity_seconds,
        "leg_submission_timeout_ms": policy.leg_submission_timeout_ms,
        "sequential_group_timeout_ms": policy.sequential_group_timeout_ms,
        "stage_timeout_ms": {key.value: value for key, value in policy.stage_timeout_ms.items()},
    }


def _timeout_policy_from_dict(data: Mapping[str, Any]) -> TimeoutPolicy:
    stage_raw = data.get("stage_timeout_ms", {})
    stage_timeout = {
        ExecutionStageId(key): int(value) for key, value in stage_raw.items()
    } if stage_raw else {
        ExecutionStageId.CONTRACT_RESOLUTION: 500,
        ExecutionStageId.SLIPPAGE_COMPUTATION: 200,
    }
    return TimeoutPolicy(
        plan_validity_seconds=int(data.get("plan_validity_seconds", DEFAULT_PLAN_VALIDITY_SECONDS)),
        leg_submission_timeout_ms=int(data.get("leg_submission_timeout_ms", 30000)),
        sequential_group_timeout_ms=int(data.get("sequential_group_timeout_ms", 120000)),
        stage_timeout_ms=MappingProxyType(stage_timeout),
    )


def _slippage_policy_to_dict(policy: SlippagePolicy) -> dict[str, Any]:
    return {
        "max_slippage_bps": policy.max_slippage_bps,
        "limit_offset_ticks": policy.limit_offset_ticks,
        "use_bid_ask_for_limits": policy.use_bid_ask_for_limits,
        "price_band_pct": policy.price_band_pct,
        "per_underlying_overrides": dict(policy.per_underlying_overrides),
    }


def _slippage_policy_from_dict(data: Mapping[str, Any]) -> SlippagePolicy:
    return SlippagePolicy(
        max_slippage_bps=float(data.get("max_slippage_bps", DEFAULT_MAX_SLIPPAGE_BPS)),
        limit_offset_ticks=int(data.get("limit_offset_ticks", DEFAULT_LIMIT_OFFSET_TICKS)),
        use_bid_ask_for_limits=bool(data.get("use_bid_ask_for_limits", True)),
        price_band_pct=float(data.get("price_band_pct", DEFAULT_PRICE_BAND_PCT)),
        per_underlying_overrides=MappingProxyType(dict(data.get("per_underlying_overrides", {}))),
    )


def _summary_to_dict(summary: ExecutionPlanSummary) -> dict[str, Any]:
    return {
        "strategy_id": summary.strategy_id,
        "strategy_family": summary.strategy_family.value,
        "underlying": summary.underlying,
        "leg_count": summary.leg_count,
        "total_quantity": summary.total_quantity,
        "sequence_mode": summary.sequence_mode.value,
        "primary_order_type": summary.primary_order_type.value,
        "estimated_notional_hint": summary.estimated_notional_hint,
    }


def _summary_from_dict(data: Mapping[str, Any]) -> ExecutionPlanSummary:
    return ExecutionPlanSummary(
        strategy_id=str(data["strategy_id"]),
        strategy_family=StrategyFamily(str(data["strategy_family"])),
        underlying=str(data["underlying"]),
        leg_count=int(data["leg_count"]),
        total_quantity=int(data["total_quantity"]),
        sequence_mode=LegSequenceMode(str(data["sequence_mode"])),
        primary_order_type=OrderType(str(data["primary_order_type"])),
        estimated_notional_hint=data.get("estimated_notional_hint"),
    )


def _reason_to_dict(reason: ExecutionReason) -> dict[str, Any]:
    return {
        "code": reason.code,
        "message": reason.message,
        "severity": reason.severity,
        "stage_id": reason.stage_id.value if reason.stage_id else None,
    }


def _reason_from_dict(data: Mapping[str, Any]) -> ExecutionReason:
    stage_raw = data.get("stage_id")
    return ExecutionReason(
        code=str(data["code"]),
        message=str(data["message"]),
        severity=str(data.get("severity", "INFO")),
        stage_id=ExecutionStageId(stage_raw) if stage_raw else None,
    )


def _factor_to_dict(factor: ExecutionFactor) -> dict[str, Any]:
    return {
        "factor_id": factor.factor_id,
        "label": factor.label,
        "weight": factor.weight,
        "raw_value": factor.raw_value,
        "normalized_value": factor.normalized_value,
        "stage_id": factor.stage_id.value if factor.stage_id else None,
    }


def _factor_from_dict(data: Mapping[str, Any]) -> ExecutionFactor:
    stage_raw = data.get("stage_id")
    return ExecutionFactor(
        factor_id=str(data["factor_id"]),
        label=str(data["label"]),
        weight=float(data["weight"]),
        raw_value=float(data["raw_value"]),
        normalized_value=float(data["normalized_value"]),
        stage_id=ExecutionStageId(stage_raw) if stage_raw else None,
    )


def _pipeline_to_dict(pipeline: ExecutionPipelineResult) -> dict[str, Any]:
    return {
        "total_stages": pipeline.total_stages,
        "passed_stages": pipeline.passed_stages,
        "failed_stage_id": pipeline.failed_stage_id.value if pipeline.failed_stage_id else None,
        "short_circuited": pipeline.short_circuited,
        "stages": [
            {
                "stage_id": stage.stage_id.value,
                "passed": stage.passed,
                "rejection_code": stage.rejection_code,
                "message": stage.message,
                "duration_ms": stage.duration_ms,
            }
            for stage in pipeline.stages
        ],
    }


def _pipeline_from_dict(data: Mapping[str, Any]) -> ExecutionPipelineResult:
    stages = tuple(
        ExecutionStageResult(
            stage_id=ExecutionStageId(item["stage_id"]),
            passed=bool(item["passed"]),
            rejection_code=item.get("rejection_code"),
            message=item.get("message"),
            duration_ms=float(item.get("duration_ms", 0.0)),
        )
        for item in data.get("stages", [])
    )
    failed_raw = data.get("failed_stage_id")
    return ExecutionPipelineResult(
        total_stages=int(data.get("total_stages", len(stages))),
        passed_stages=int(data.get("passed_stages", sum(1 for stage in stages if stage.passed))),
        failed_stage_id=ExecutionStageId(failed_raw) if failed_raw else None,
        stages=stages,
        short_circuited=bool(data.get("short_circuited", False)),
    )


def _warning_to_dict(record: ExecutionWarningRecord) -> dict[str, Any]:
    return {
        "code": record.code,
        "message": record.message,
        "severity": record.severity,
        "stage_id": record.stage_id.value if record.stage_id else None,
        "field": record.field,
    }


def _warning_from_dict(data: Mapping[str, Any]) -> ExecutionWarningRecord:
    stage_raw = data.get("stage_id")
    return ExecutionWarningRecord(
        code=str(data["code"]),
        message=str(data["message"]),
        severity=str(data.get("severity", "WARNING")),
        stage_id=ExecutionStageId(stage_raw) if stage_raw else None,
        field=data.get("field"),
    )


def _error_to_dict(record: ExecutionErrorRecord) -> dict[str, Any]:
    return {
        "code": record.code,
        "message": record.message,
        "field": record.field,
        "stage_id": record.stage_id.value if record.stage_id else None,
    }


def _error_from_dict(data: Mapping[str, Any]) -> ExecutionErrorRecord:
    stage_raw = data.get("stage_id")
    return ExecutionErrorRecord(
        code=str(data["code"]),
        message=str(data["message"]),
        field=data.get("field"),
        stage_id=ExecutionStageId(stage_raw) if stage_raw else None,
    )


def plan_to_dict(plan: ExecutionPlan) -> dict[str, Any]:
    """Convert ExecutionPlan to JSON-serializable dict."""
    return {
        "schema_version": EXECUTION_ENGINE_SCHEMA_VERSION,
        "plan_id": plan.plan_id,
        "correlation_id": plan.correlation_id,
        "risk_id": plan.risk_id,
        "decision_fingerprint": plan.decision_fingerprint,
        "risk_fingerprint": plan.risk_fingerprint,
        "signal_fingerprint": plan.signal_fingerprint,
        "snapshot_id": plan.snapshot_id,
        "status": plan.status.value,
        "execution_mode": plan.execution_mode.value,
        "primary_rejection_code": plan.primary_rejection_code,
        "skip_reason_code": plan.skip_reason_code.value if plan.skip_reason_code else None,
        "approved_risk_budget": plan.approved_risk_budget,
        "plan_fingerprint": plan.plan_fingerprint,
        "planned_at": plan.planned_at.isoformat(),
        "valid_until": plan.valid_until.isoformat() if plan.valid_until else None,
        "duration_ms": plan.duration_ms,
        "legs": [leg_to_dict(leg) for leg in plan.legs],
        "sequences": [_sequence_to_dict(seq) for seq in plan.sequences],
        "retry_policy": _retry_policy_to_dict(plan.retry_policy),
        "timeout_policy": _timeout_policy_to_dict(plan.timeout_policy),
        "slippage_policy": _slippage_policy_to_dict(plan.slippage_policy),
        "execution_policy": {
            "default_order_type": plan.execution_policy.default_order_type.value,
            "default_product": plan.execution_policy.default_product.value,
            "allow_market_orders_live": plan.execution_policy.allow_market_orders_live,
            "prefer_limit_orders": plan.execution_policy.prefer_limit_orders,
            "sequencing_mode": plan.execution_policy.sequencing_mode.value,
        },
        "summary": _summary_to_dict(plan.summary),
        "reasons": [_reason_to_dict(reason) for reason in plan.reasons],
        "factors": [_factor_to_dict(factor) for factor in plan.factors],
        "pipeline_summary": _pipeline_to_dict(plan.pipeline_summary),
        "trading_signal": signal_to_dict(plan.trading_signal),
        "warnings": [_warning_to_dict(item) for item in plan.warnings],
        "errors": [_error_to_dict(item) for item in plan.errors],
        "metadata": dict(plan.metadata),
    }


def plan_from_dict(data: Mapping[str, Any]) -> ExecutionPlan:
    """Deserialize ExecutionPlan from dict."""
    schema_version = str(data.get("schema_version", EXECUTION_ENGINE_SCHEMA_VERSION))
    if schema_version != EXECUTION_ENGINE_SCHEMA_VERSION:
        raise ExecutionEngineValidationError(
            f"Unsupported schema version: {schema_version}.",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
        )
    try:
        planned_at = datetime.fromisoformat(str(data["planned_at"]))
        valid_until_raw = data.get("valid_until")
        valid_until = datetime.fromisoformat(str(valid_until_raw)) if valid_until_raw else None
        plan = ExecutionPlan(
            plan_id=str(data["plan_id"]),
            correlation_id=str(data["correlation_id"]),
            risk_id=str(data["risk_id"]),
            decision_fingerprint=str(data["decision_fingerprint"]),
            risk_fingerprint=str(data["risk_fingerprint"]),
            signal_fingerprint=str(data["signal_fingerprint"]),
            snapshot_id=str(data["snapshot_id"]),
            status=ExecutionPlanStatus(str(data["status"])),
            trading_signal=signal_from_dict(data["trading_signal"]),
            execution_mode=StrategyExecutionMode(str(data["execution_mode"])),
            legs=tuple(leg_from_dict(item) for item in data.get("legs", [])),
            sequences=tuple(_sequence_from_dict(item) for item in data.get("sequences", [])),
            retry_policy=_retry_policy_from_dict(data.get("retry_policy", {})),
            timeout_policy=_timeout_policy_from_dict(data.get("timeout_policy", {})),
            slippage_policy=_slippage_policy_from_dict(data.get("slippage_policy", {})),
            execution_policy=default_execution_engine_config().execution_policy,
            summary=_summary_from_dict(data["summary"]),
            reasons=tuple(_reason_from_dict(item) for item in data.get("reasons", [])),
            factors=tuple(_factor_from_dict(item) for item in data.get("factors", [])),
            pipeline_summary=_pipeline_from_dict(data.get("pipeline_summary", {})),
            planned_at=planned_at,
            duration_ms=float(data.get("duration_ms", 0.0)),
            plan_fingerprint=str(data["plan_fingerprint"]),
            warnings=tuple(_warning_from_dict(item) for item in data.get("warnings", [])),
            errors=tuple(_error_from_dict(item) for item in data.get("errors", [])),
            valid_until=valid_until,
            primary_rejection_code=data.get("primary_rejection_code"),
            skip_reason_code=(
                ExecutionSkipReasonCode(str(data["skip_reason_code"]))
                if data.get("skip_reason_code")
                else None
            ),
            approved_risk_budget=data.get("approved_risk_budget"),
            metadata=MappingProxyType(dict(data.get("metadata", {}))),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExecutionEngineValidationError(
            f"Malformed execution plan payload: {exc}.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    return plan


def plan_to_json(plan: ExecutionPlan, *, indent: int | None = None) -> str:
    """Serialize ExecutionPlan to JSON string."""
    return json.dumps(plan_to_dict(plan), indent=indent, sort_keys=True)


def plan_from_json(payload: str) -> ExecutionPlan:
    """Deserialize ExecutionPlan from JSON string."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ExecutionEngineValidationError(
            f"Malformed JSON: {exc}.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(data, dict):
        raise ExecutionEngineValidationError(
            "Execution plan JSON root must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return plan_from_dict(data)


def _empty_pipeline_result() -> ExecutionPipelineResult:
    return ExecutionPipelineResult(
        total_stages=0,
        passed_stages=0,
        failed_stage_id=None,
        stages=(),
        short_circuited=False,
    )


def _gate_pipeline_result() -> ExecutionPipelineResult:
    return ExecutionPipelineResult(
        total_stages=1,
        passed_stages=0,
        failed_stage_id=ExecutionStageId.RISK_VERDICT_GATE,
        stages=(
            ExecutionStageResult(
                stage_id=ExecutionStageId.RISK_VERDICT_GATE,
                passed=False,
                message="Risk verdict gate short-circuited.",
            ),
        ),
        short_circuited=True,
    )


def _attach_idempotency_keys(
    legs: tuple[PlannedOrderLeg, ...],
    correlation_id: str,
    plan_id: str,
) -> tuple[PlannedOrderLeg, ...]:
    """Attach deterministic idempotency keys to all legs."""
    return tuple(
        replace(
            leg,
            idempotency_key=generate_idempotency_key(correlation_id, plan_id, leg.leg_index),
        )
        for leg in legs
    )


def _map_plan_status_to_engine_status(
    plan_status: ExecutionPlanStatus,
    errors: tuple[EngineErrorRecord, ...],
) -> EngineStatus:
    """Map execution plan status to engine status."""
    if errors:
        return EngineStatus.REJECTED
    if plan_status in (
        ExecutionPlanStatus.READY,
        ExecutionPlanStatus.SKIPPED,
        ExecutionPlanStatus.NO_PLAN,
        ExecutionPlanStatus.REJECTED,
    ):
        return EngineStatus.SUCCESS
    return EngineStatus.FAILED


def _risk_warnings_to_execution(
    warnings: tuple[RiskWarningRecord, ...],
) -> tuple[ExecutionWarningRecord, ...]:
    return tuple(
        ExecutionWarningRecord(code=item.code, message=item.message, field=item.field)
        for item in warnings
    )


class ExecutionEngine(BaseEngine):
    """Institutional execution planning engine for THETA AI TRADER v1.0."""

    def __init__(
        self,
        config: ExecutionEngineConfig | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        metadata: EngineMetadata | None = None,
        pipeline: ExecutionPlanningPipeline | None = None,
        re_raise_on_failure: bool = False,
    ) -> None:
        """Initialize execution engine with injected policies."""
        self._exec_config = config or default_execution_engine_config()
        self._clock = clock or _utc_now
        self._pipeline = pipeline or ExecutionPlanningPipeline()
        self._metadata = metadata
        super().__init__(
            config=MappingProxyType({"engine": "execution_engine"}),
            re_raise_on_failure=re_raise_on_failure,
        )

    @property
    def exec_config(self) -> ExecutionEngineConfig:
        """Return immutable execution engine configuration."""
        return self._exec_config

    @property
    def engine_name(self) -> str:
        """Return stable engine identifier."""
        return "execution_engine"

    @property
    def engine_version(self) -> str:
        """Return semantic engine version."""
        return EXECUTION_ENGINE_VERSION

    def validate_configuration(self) -> None:
        """Validate static engine configuration."""
        _ = self._exec_config

    def validate_context(self, context: EngineContext) -> None:
        """Validate engine context wrapping execution run context."""
        super().validate_context(context)
        if not isinstance(context.payload, ExecutionRunContext):
            raise ExecutionEngineContextError(
                "EngineContext.payload must be ExecutionRunContext.",
                code=ERROR_CONTEXT_INVALID,
                field="payload",
            )

    def validate_run_context(self, run_context: ExecutionRunContext) -> None:
        """Validate execution run inputs; raise on fatal issues."""
        validate_run_context(run_context, config=self._exec_config)

    def validate_execution_plan(self, plan: ExecutionPlan) -> ExecutionValidationResult:
        """Validate sealed execution plan output."""
        return validate_execution_plan(plan)

    def assert_valid_execution_plan(self, plan: ExecutionPlan) -> None:
        """Raise when execution plan output is invalid."""
        assert_valid_execution_plan(plan)

    def evaluate(self, context: EngineContext) -> EngineResult:
        """BaseEngine entry point — delegates to plan()."""
        return self.plan(context)

    def plan(self, context: EngineContext) -> EngineResult:
        """Plan execution from engine context."""
        started_perf = time.perf_counter()
        warnings: list[EngineWarningRecord] = []
        try:
            if not isinstance(context.payload, ExecutionRunContext):
                raise ExecutionEngineContextError(
                    "EngineContext.payload must be ExecutionRunContext.",
                    code=ERROR_CONTEXT_INVALID,
                    field="payload",
                )
            run_context: ExecutionRunContext = context.payload
            self.validate_run_context(run_context)
            _logger.info(
                "execution.plan.start",
                extra={
                    "event": "execution.plan.start",
                    "correlation_id": run_context.correlation_id,
                    "risk_verdict": run_context.risk_decision.verdict.value,
                    "risk_fingerprint": run_context.risk_decision.risk_fingerprint,
                },
            )
            execution_plan = self.plan_from_run_context(run_context)
            validation = self.validate_execution_plan(execution_plan)
            if not validation.is_valid:
                if self._exec_config.strict_output_validation:
                    first = validation.errors[0]
                    raise ExecutionEngineValidationError(
                        first.message,
                        code=first.code,
                        field=first.field,
                    )
                warnings.extend(
                    EngineWarningRecord(code=item.code, message=item.message, field=item.field)
                    for item in validation.errors
                )
            warnings.extend(
                EngineWarningRecord(code=item.code, message=item.message, field=item.field)
                for item in validation.warnings
            )
            warnings.extend(
                EngineWarningRecord(code=item.code, message=item.message, field=item.field)
                for item in execution_plan.warnings
            )
            duration_ms = (time.perf_counter() - started_perf) * 1000.0
            status = _map_plan_status_to_engine_status(execution_plan.status, ())
            started_at = self._clock()
            completed_at = self._clock()
            metadata = self._metadata or EngineMetadata(
                engine_name=self.engine_name,
                engine_version=self.engine_version,
                correlation_id=run_context.correlation_id,
                execution_id=run_context.correlation_id,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
            )
            _logger.info(
                "execution.plan.complete",
                extra={
                    "event": "execution.plan.complete",
                    "correlation_id": run_context.correlation_id,
                    "plan_id": execution_plan.plan_id,
                    "plan_status": execution_plan.status.value,
                    "plan_fingerprint": execution_plan.plan_fingerprint,
                    "leg_count": len(execution_plan.legs),
                    "duration_ms": duration_ms,
                    "pipeline_passed_stages": execution_plan.pipeline_summary.passed_stages,
                    "execution_mode": execution_plan.execution_mode.value,
                },
            )
            return EngineResult(
                status=status,
                metadata=metadata,
                payload=execution_plan,
                warnings=tuple(warnings),
                errors=(),
            )
        except ExecutionEngineError as exc:
            duration_ms = (time.perf_counter() - started_perf) * 1000.0
            _logger.error(
                "execution.plan.failed",
                extra={
                    "event": "execution.plan.failed",
                    "correlation_id": context.correlation_id,
                    "code": exc.code,
                },
            )
            started_at = self._clock()
            completed_at = self._clock()
            return EngineResult(
                status=EngineStatus.REJECTED,
                metadata=self._metadata
                or EngineMetadata(
                    engine_name=self.engine_name,
                    engine_version=self.engine_version,
                    correlation_id=context.correlation_id,
                    execution_id=context.correlation_id,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                ),
                payload=None,
                errors=(EngineErrorRecord(code=exc.code, message=str(exc), field=exc.field),),
                warnings=tuple(warnings),
            )

    def plan_from_run_context(self, run_context: ExecutionRunContext) -> ExecutionPlan:
        """Core planning API returning sealed ExecutionPlan."""
        started_perf = time.perf_counter()
        config = self._exec_config
        reference_time = _resolved_reference_time(run_context)
        execution_mode = _resolved_execution_mode(run_context)
        risk = run_context.risk_decision
        signal = risk.trading_signal
        snapshot = run_context.market_snapshot
        planned_at = self._clock()

        if run_context.force_skip:
            if risk.verdict is RiskVerdict.APPROVED:
                _logger.info(
                    "execution.plan.gate.skip",
                    extra={"event": "execution.plan.gate.skip", "reason": "force_skip"},
                )
            return self._build_skipped_plan(
                run_context,
                ExecutionSkipReasonCode.ORCHESTRATOR_SKIP,
                planned_at=planned_at,
                duration_ms=(time.perf_counter() - started_perf) * 1000.0,
                extra_warnings=(
                    ExecutionWarningRecord(
                        code=WARN_RISK_APPROVED_FORCE_SKIP,
                        message="APPROVED risk but force_skip set.",
                    ),
                )
                if risk.verdict is RiskVerdict.APPROVED
                else (),
            )

        if (
            execution_mode is StrategyExecutionMode.ANALYSIS
            and config.skip_planning_in_analysis
            and run_context.tags.get("force_plan_in_analysis") != "true"
        ):
            return self._build_skipped_plan(
                run_context,
                ExecutionSkipReasonCode.ANALYSIS_MODE_SKIP,
                planned_at=planned_at,
                duration_ms=(time.perf_counter() - started_perf) * 1000.0,
            )

        if risk.verdict is RiskVerdict.SKIPPED:
            _logger.info("execution.plan.skipped", extra={"event": "execution.plan.skipped"})
            return self._build_skipped_plan(
                run_context,
                ExecutionSkipReasonCode.RISK_SKIPPED,
                planned_at=planned_at,
                duration_ms=(time.perf_counter() - started_perf) * 1000.0,
            )

        if risk.verdict is RiskVerdict.REJECTED:
            _logger.info("execution.plan.no_plan", extra={"event": "execution.plan.no_plan"})
            return self._build_no_plan(
                run_context,
                ExecutionSkipReasonCode.RISK_REJECTED,
                planned_at=planned_at,
                duration_ms=(time.perf_counter() - started_perf) * 1000.0,
            )

        state = _PipelineState(
            run_context=run_context,
            config=config,
            reference_time=reference_time,
            execution_mode=execution_mode,
        )
        pipeline_result = self._pipeline.apply(run_context, config=config, state=state)
        state.elapsed_ms = (time.perf_counter() - started_perf) * 1000.0

        if pipeline_result.failed_stage_id is not None:
            _logger.info("execution.plan.rejected", extra={"event": "execution.plan.rejected"})
            return self._build_rejected_plan(
                run_context,
                state,
                pipeline_result,
                planned_at=planned_at,
                duration_ms=state.elapsed_ms,
            )

        plan_id = _generate_plan_id(run_context.correlation_id, risk.risk_fingerprint)
        legs_with_keys = _attach_idempotency_keys(state.resolved_legs, run_context.correlation_id, plan_id)
        timeout_policy = config.default_timeout_policy
        if execution_mode is StrategyExecutionMode.BACKTEST:
            timeout_policy = replace(
                timeout_policy,
                plan_validity_seconds=config.backtest_plan_validity_seconds,
            )
        valid_until = compute_valid_until(planned_at, signal, timeout_policy)
        if valid_until - planned_at <= timedelta(seconds=15):
            state.warnings.append(
                ExecutionWarningRecord(
                    code=WARN_PLAN_NEAR_EXPIRY,
                    message="Plan validity window near expiry.",
                )
            )

        plan = ExecutionPlan(
            plan_id=plan_id,
            correlation_id=run_context.correlation_id,
            risk_id=risk.risk_id,
            decision_fingerprint=risk.decision_fingerprint,
            risk_fingerprint=risk.risk_fingerprint,
            signal_fingerprint=signal_fingerprint(signal),
            snapshot_id=snapshot.provenance.snapshot_id,
            status=ExecutionPlanStatus.READY,
            trading_signal=signal,
            execution_mode=execution_mode,
            legs=legs_with_keys,
            sequences=state.sequences,
            retry_policy=config.default_retry_policy,
            timeout_policy=timeout_policy,
            slippage_policy=config.default_slippage_policy,
            execution_policy=config.execution_policy,
            summary=_build_summary(signal, legs_with_keys, state.sequences),
            reasons=tuple(state.reasons),
            factors=tuple(state.factors),
            pipeline_summary=pipeline_result,
            planned_at=planned_at,
            valid_until=valid_until,
            duration_ms=state.elapsed_ms,
            plan_fingerprint="",
            approved_risk_budget=risk.approved_risk_budget,
            warnings=tuple(state.warnings) + _risk_warnings_to_execution(risk.warnings),
            errors=(),
            metadata=MappingProxyType(dict(run_context.tags)),
        )
        fingerprint = plan_fingerprint(plan)
        _logger.info("execution.plan.ready", extra={"event": "execution.plan.ready", "plan_id": plan_id})
        return replace(plan, plan_fingerprint=fingerprint)

    def _build_skipped_plan(
        self,
        run_context: ExecutionRunContext,
        skip_reason: ExecutionSkipReasonCode,
        *,
        planned_at: datetime,
        duration_ms: float,
        extra_warnings: tuple[ExecutionWarningRecord, ...] = (),
    ) -> ExecutionPlan:
        risk = run_context.risk_decision
        reasons = (
            ExecutionReason(
                code=f"EXECUTION.SKIP.{skip_reason.value.upper()}",
                message=f"Execution planning skipped: {skip_reason.value}.",
                severity="INFO",
                stage_id=ExecutionStageId.RISK_VERDICT_GATE,
            ),
        )
        warnings = _risk_warnings_to_execution(risk.warnings) + extra_warnings
        plan = ExecutionPlan(
            plan_id=_generate_plan_id(run_context.correlation_id, risk.risk_fingerprint),
            correlation_id=run_context.correlation_id,
            risk_id=risk.risk_id,
            decision_fingerprint=risk.decision_fingerprint,
            risk_fingerprint=risk.risk_fingerprint,
            signal_fingerprint=signal_fingerprint(risk.trading_signal),
            snapshot_id=run_context.market_snapshot.provenance.snapshot_id,
            status=ExecutionPlanStatus.SKIPPED,
            trading_signal=risk.trading_signal,
            execution_mode=_resolved_execution_mode(run_context),
            legs=(),
            sequences=(),
            retry_policy=self._exec_config.default_retry_policy,
            timeout_policy=self._exec_config.default_timeout_policy,
            slippage_policy=self._exec_config.default_slippage_policy,
            execution_policy=self._exec_config.execution_policy,
            summary=ExecutionPlanSummary(
                strategy_id=risk.trading_signal.strategy_id,
                strategy_family=risk.trading_signal.strategy_family,
                underlying=risk.trading_signal.market.underlying,
                leg_count=0,
                total_quantity=0,
                sequence_mode=self._exec_config.execution_policy.sequencing_mode,
                primary_order_type=self._exec_config.execution_policy.default_order_type,
            ),
            reasons=reasons,
            factors=(),
            pipeline_summary=_gate_pipeline_result(),
            planned_at=planned_at,
            duration_ms=duration_ms,
            plan_fingerprint="",
            skip_reason_code=skip_reason,
            warnings=warnings,
            errors=(),
            approved_risk_budget=risk.approved_risk_budget,
            metadata=MappingProxyType(dict(run_context.tags)),
        )
        return replace(plan, plan_fingerprint=plan_fingerprint(plan))

    def _build_no_plan(
        self,
        run_context: ExecutionRunContext,
        skip_reason: ExecutionSkipReasonCode,
        *,
        planned_at: datetime,
        duration_ms: float,
    ) -> ExecutionPlan:
        risk = run_context.risk_decision
        reasons = (
            ExecutionReason(
                code="EXECUTION.NO_PLAN.RISK_REJECTED",
                message=(
                    f"Risk rejected upstream"
                    f"{': ' + risk.primary_rejection_code if risk.primary_rejection_code else '.'}"
                ),
                severity="INFO",
                stage_id=ExecutionStageId.RISK_VERDICT_GATE,
            ),
        )
        plan = ExecutionPlan(
            plan_id=_generate_plan_id(run_context.correlation_id, risk.risk_fingerprint),
            correlation_id=run_context.correlation_id,
            risk_id=risk.risk_id,
            decision_fingerprint=risk.decision_fingerprint,
            risk_fingerprint=risk.risk_fingerprint,
            signal_fingerprint=signal_fingerprint(risk.trading_signal),
            snapshot_id=run_context.market_snapshot.provenance.snapshot_id,
            status=ExecutionPlanStatus.NO_PLAN,
            trading_signal=risk.trading_signal,
            execution_mode=_resolved_execution_mode(run_context),
            legs=(),
            sequences=(),
            retry_policy=self._exec_config.default_retry_policy,
            timeout_policy=self._exec_config.default_timeout_policy,
            slippage_policy=self._exec_config.default_slippage_policy,
            execution_policy=self._exec_config.execution_policy,
            summary=ExecutionPlanSummary(
                strategy_id=risk.trading_signal.strategy_id,
                strategy_family=risk.trading_signal.strategy_family,
                underlying=risk.trading_signal.market.underlying,
                leg_count=0,
                total_quantity=0,
                sequence_mode=self._exec_config.execution_policy.sequencing_mode,
                primary_order_type=self._exec_config.execution_policy.default_order_type,
            ),
            reasons=reasons,
            factors=(),
            pipeline_summary=_gate_pipeline_result(),
            planned_at=planned_at,
            duration_ms=duration_ms,
            plan_fingerprint="",
            skip_reason_code=skip_reason,
            warnings=_risk_warnings_to_execution(risk.warnings),
            errors=(),
            approved_risk_budget=risk.approved_risk_budget,
            metadata=MappingProxyType(dict(run_context.tags)),
        )
        return replace(plan, plan_fingerprint=plan_fingerprint(plan))

    def _build_rejected_plan(
        self,
        run_context: ExecutionRunContext,
        state: _PipelineState,
        pipeline_result: ExecutionPipelineResult,
        *,
        planned_at: datetime,
        duration_ms: float,
    ) -> ExecutionPlan:
        risk = run_context.risk_decision
        rejection_code = state.primary_rejection_code or ERROR_RESULT_INVALID
        failed_stage = pipeline_result.failed_stage_id
        failed_message = next(
            (stage.message for stage in pipeline_result.stages if not stage.passed),
            "Execution planning rejected.",
        )
        reasons = (
            ExecutionReason(
                code=rejection_code,
                message=failed_message or "Execution planning rejected.",
                severity="ERROR",
                stage_id=failed_stage,
            ),
        )
        plan = ExecutionPlan(
            plan_id=_generate_plan_id(run_context.correlation_id, risk.risk_fingerprint),
            correlation_id=run_context.correlation_id,
            risk_id=risk.risk_id,
            decision_fingerprint=risk.decision_fingerprint,
            risk_fingerprint=risk.risk_fingerprint,
            signal_fingerprint=signal_fingerprint(risk.trading_signal),
            snapshot_id=run_context.market_snapshot.provenance.snapshot_id,
            status=ExecutionPlanStatus.REJECTED,
            trading_signal=risk.trading_signal,
            execution_mode=state.execution_mode,
            legs=(),
            sequences=(),
            retry_policy=self._exec_config.default_retry_policy,
            timeout_policy=self._exec_config.default_timeout_policy,
            slippage_policy=self._exec_config.default_slippage_policy,
            execution_policy=self._exec_config.execution_policy,
            summary=ExecutionPlanSummary(
                strategy_id=risk.trading_signal.strategy_id,
                strategy_family=risk.trading_signal.strategy_family,
                underlying=risk.trading_signal.market.underlying,
                leg_count=0,
                total_quantity=0,
                sequence_mode=self._exec_config.execution_policy.sequencing_mode,
                primary_order_type=self._exec_config.execution_policy.default_order_type,
            ),
            reasons=reasons,
            factors=tuple(state.factors),
            pipeline_summary=pipeline_result,
            planned_at=planned_at,
            duration_ms=duration_ms,
            plan_fingerprint="",
            primary_rejection_code=rejection_code,
            warnings=tuple(state.warnings) + _risk_warnings_to_execution(risk.warnings),
            errors=(
                ExecutionErrorRecord(
                    code=rejection_code,
                    message=failed_message or "Execution planning rejected.",
                    stage_id=failed_stage,
                ),
            ),
            approved_risk_budget=risk.approved_risk_budget,
            metadata=MappingProxyType(dict(run_context.tags)),
        )
        return replace(plan, plan_fingerprint=plan_fingerprint(plan))


__all__ = [
    "EXECUTION_ENGINE_VERSION",
    "EXECUTION_ENGINE_SCHEMA_VERSION",
    "EXECUTION_PRICE_EPSILON",
    "DEFAULT_MAX_SLIPPAGE_BPS",
    "DEFAULT_PLAN_VALIDITY_SECONDS",
    "DEFAULT_RETRY_MAX_ATTEMPTS",
    "DEFAULT_LIMIT_OFFSET_TICKS",
    "DEFAULT_PRICE_BAND_PCT",
    "DEFAULT_SEQUENTIAL_INTER_LEG_DELAY_MS",
    "STAGE_ORDER",
    "ContractResolutionSource",
    "ContractSelectionResult",
    "ExecutionEngine",
    "ExecutionEngineConfig",
    "ExecutionEngineConfigurationError",
    "ExecutionEngineContextError",
    "ExecutionEngineError",
    "ExecutionEngineValidationError",
    "ExecutionFactor",
    "ExecutionPlan",
    "ExecutionPlanStatus",
    "ExecutionPlanSummary",
    "ExecutionPlanningError",
    "ExecutionPlanningPipeline",
    "ExecutionPolicy",
    "ExecutionReason",
    "ExecutionRunContext",
    "ExecutionSkipReasonCode",
    "ExecutionStageId",
    "ExecutionStageResult",
    "ExecutionPipelineResult",
    "ExecutionValidationResult",
    "ExecutionWarningRecord",
    "ExecutionErrorRecord",
    "ExecutionStructureOverride",
    "LegSequence",
    "LegSequenceMode",
    "OrderSide",
    "OrderType",
    "OrderTypePolicy",
    "PlannedOrderLeg",
    "ProductType",
    "ProductTypePolicy",
    "RetryPolicy",
    "SelectedContractLeg",
    "SlippagePolicy",
    "TimeoutPolicy",
    "assert_valid_execution_plan",
    "build_sequences",
    "compute_limit_price_hint",
    "compute_valid_until",
    "default_execution_engine_config",
    "generate_idempotency_key",
    "leg_from_dict",
    "leg_to_dict",
    "plan_fingerprint",
    "plan_from_dict",
    "plan_from_json",
    "plan_to_dict",
    "plan_to_json",
    "resolve_contracts",
    "resolve_leg_quantity",
    "validate_execution_plan",
    "validate_planned_order_leg",
    "validate_run_context",
]
