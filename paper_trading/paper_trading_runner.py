"""Institutional paper execution simulation for THETA AI TRADER v1.0.

Consumes immutable :class:`ExecutionPlan` outputs and performs deterministic
simulated fills, virtual capital accounting, paper position bookkeeping, and
OrderTracker-compatible projections without broker I/O.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Final, Mapping

from core.event_bus import EventBus, EventEnvelope
from execution.execution_engine import (
    ExecutionPlan,
    ExecutionPlanStatus,
    LegSequence,
    LegSequenceMode,
    OrderSide,
    OrderType,
    PlannedOrderLeg,
)
from execution.order_manager import (
    OrderAggregateStatus,
    OrderLifecycleStatus,
    OrderSequenceResult,
    OrderState,
    OrderStateTransition,
    OrderTracker,
    compute_tracker_fingerprint,
    derive_aggregate_status,
)
from strategy.signals import StrategyExecutionMode

PAPER_TRADING_RUNNER_VERSION: Final[str] = "1.0.0"
PAPER_SCHEMA_VERSION: Final[str] = "1.0.0"
PRODUCER_NAME: Final[str] = "paper_trading_runner"
DEFAULT_INITIAL_CASH: Final[Decimal] = Decimal("1000000.00")
DEFAULT_SLIPPAGE_BPS: Final[Decimal] = Decimal("5")
DEFAULT_BROKERAGE_PER_ORDER: Final[Decimal] = Decimal("20.00")
DEFAULT_LATENCY_MS: Final[int] = 0
DEFAULT_DEDUPE_RETENTION: Final[int] = 10_000
MONEY_QUANTUM: Final[Decimal] = Decimal("0.01")
PRICE_QUANTUM: Final[Decimal] = Decimal("0.05")

_EXECUTION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._:-]+$")

ERROR_PLAN_NOT_READY: Final[str] = "PAPER.PLAN.NOT_READY"
ERROR_PLAN_EXPIRED: Final[str] = "PAPER.PLAN.EXPIRED"
ERROR_PLAN_EMPTY_LEGS: Final[str] = "PAPER.PLAN.EMPTY_LEGS"
ERROR_CORR_MISMATCH: Final[str] = "PAPER.CORR.MISMATCH"
ERROR_CONTEXT_INVALID: Final[str] = "PAPER.CONTEXT.INVALID"
ERROR_CONTEXT_FORCE_REJECT: Final[str] = "PAPER.CONTEXT.FORCE_REJECT"
ERROR_EXECUTION_DUPLICATE_ID: Final[str] = "PAPER.EXECUTION.DUPLICATE_ID"
ERROR_EXECUTION_INVALID_ID: Final[str] = "PAPER.EXECUTION.INVALID_ID"
ERROR_LEG_INVALID: Final[str] = "PAPER.LEG.INVALID"
ERROR_PRICE_MARK_MISSING: Final[str] = "PAPER.PRICE.MARK_MISSING"
ERROR_PRICE_NON_FINITE: Final[str] = "PAPER.PRICE.NON_FINITE"
ERROR_PRICE_NON_POSITIVE: Final[str] = "PAPER.PRICE.NON_POSITIVE"
ERROR_PRICE_INVALID_LIMIT: Final[str] = "PAPER.PRICE.INVALID_LIMIT"
ERROR_PRICE_INVALID_FILL: Final[str] = "PAPER.PRICE.INVALID_FILL"
ERROR_QTY_NON_POSITIVE: Final[str] = "PAPER.QTY.NON_POSITIVE"
ERROR_QTY_INVALID: Final[str] = "PAPER.QTY.INVALID"
ERROR_SLIPPAGE_EXCEEDED: Final[str] = "PAPER.SLIPPAGE.EXCEEDED"
ERROR_CAPITAL_INSUFFICIENT: Final[str] = "PAPER.CAPITAL.INSUFFICIENT"
ERROR_SEQUENCE_ABORTED: Final[str] = "PAPER.SEQUENCE.ABORTED"
ERROR_RESULT_INVALID: Final[str] = "PAPER.RESULT.INVALID"
ERROR_SERIALIZATION_UNSUPPORTED: Final[str] = "PAPER.SERIALIZATION.UNSUPPORTED_VERSION"

WARN_PLAN_NEAR_EXPIRY: Final[str] = "PAPER.PLAN.NEAR_EXPIRY"
WARN_BROKERAGE_ZERO: Final[str] = "PAPER.BROKERAGE.ZERO"
WARN_SLIPPAGE_CAPPED: Final[str] = "PAPER.SLIPPAGE.CAPPED"

_logger = logging.getLogger("paper_trading.paper_trading_runner")


class PaperExecutionStatus(str, Enum):
    """Paper simulation run outcome status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    REJECTED = "rejected"
    FAILED = "failed"
    INSUFFICIENT_CAPITAL = "insufficient_capital"
    EXPIRED = "expired"


class PaperFillModel(str, Enum):
    """Deterministic fill quantity/price model."""

    FULL_AT_MARK = "full_at_mark"
    FULL_AT_LIMIT_OR_MARK = "full_at_limit_or_mark"
    DETERMINISTIC_PARTIAL = "deterministic_partial"


class PaperSlippageMode(str, Enum):
    """Slippage application mode."""

    BPS_ADVERSE = "bps_adverse"
    ABSOLUTE = "absolute"
    MAX_OF_PLAN_AND_CONFIG = "max_of_plan_and_config"
    NONE = "none"


class PaperBrokerageMode(str, Enum):
    """Brokerage fee computation mode."""

    FLAT_PER_LEG = "flat_per_leg"
    BPS_OF_NOTIONAL = "bps_of_notional"
    FLAT_PLUS_BPS = "flat_plus_bps"
    NONE = "none"


class PaperLatencyMode(str, Enum):
    """Fill timestamp latency model."""

    FIXED_MS = "fixed_ms"
    PER_LEG_INCREMENT_MS = "per_leg_increment_ms"
    SEQUENCE_DELAY = "sequence_delay"
    NONE = "none"


class PaperEventType(str, Enum):
    """Paper lifecycle event discriminator with associated topic."""

    PLAN_SIMULATION_STARTED = "plan_simulation_started"
    LEG_FILLED = "leg_filled"
    LEG_PARTIALLY_FILLED = "leg_partially_filled"
    LEG_REJECTED = "leg_rejected"
    PLAN_COMPLETED = "plan_completed"
    PLAN_REJECTED = "plan_rejected"
    CAPITAL_UPDATED = "capital_updated"
    POSITION_UPDATED = "position_updated"
    PORTFOLIO_UPDATED = "portfolio_updated"
    MARK_TO_MARKET = "mark_to_market"

    @property
    def topic(self) -> str:
        """Return hierarchical event bus topic for this event type."""
        mapping = {
            PaperEventType.PLAN_SIMULATION_STARTED: "paper.order.plan.started",
            PaperEventType.LEG_FILLED: "paper.order.leg.filled",
            PaperEventType.LEG_PARTIALLY_FILLED: "paper.order.leg.partial_fill",
            PaperEventType.LEG_REJECTED: "paper.order.leg.rejected",
            PaperEventType.PLAN_COMPLETED: "paper.order.plan.completed",
            PaperEventType.PLAN_REJECTED: "paper.order.plan.rejected",
            PaperEventType.CAPITAL_UPDATED: "paper.capital.updated",
            PaperEventType.POSITION_UPDATED: "paper.position.updated",
            PaperEventType.PORTFOLIO_UPDATED: "paper.portfolio.updated",
            PaperEventType.MARK_TO_MARKET: "paper.portfolio.marked",
        }
        return mapping[self]


class PaperTradingError(Exception):
    """Base paper trading runner exception."""

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


class PaperTradingConfigurationError(PaperTradingError):
    """Raised when runner configuration is invalid."""


class PaperTradingValidationError(PaperTradingError):
    """Raised when input or output validation fails."""


class PaperTradingSimulationError(PaperTradingError):
    """Raised when an unexpected simulation failure occurs."""


class PaperTradingSerializationError(PaperTradingError):
    """Raised when serialization or deserialization fails."""


class PaperTradingConcurrencyError(PaperTradingError):
    """Raised on concurrency invariant violations."""


@dataclass(frozen=True)
class PaperTradingRunnerConfig:
    """Immutable configuration for :class:`PaperTradingRunner`."""

    initial_cash: Decimal = DEFAULT_INITIAL_CASH
    reject_insufficient_capital: bool = True
    minimum_cash_floor: Decimal = Decimal("0")
    fill_model: PaperFillModel = PaperFillModel.FULL_AT_LIMIT_OR_MARK
    partial_fill_fraction: Decimal = Decimal("1")
    slippage_mode: PaperSlippageMode = PaperSlippageMode.BPS_ADVERSE
    slippage_bps: Decimal = DEFAULT_SLIPPAGE_BPS
    slippage_absolute: Decimal = Decimal("0")
    brokerage_mode: PaperBrokerageMode = PaperBrokerageMode.FLAT_PER_LEG
    brokerage_flat: Decimal = DEFAULT_BROKERAGE_PER_ORDER
    brokerage_bps: Decimal = Decimal("0")
    latency_mode: PaperLatencyMode = PaperLatencyMode.SEQUENCE_DELAY
    latency_base_ms: int = DEFAULT_LATENCY_MS
    latency_step_ms: int = 0
    price_quantum: Decimal = PRICE_QUANTUM
    contract_multiplier: Decimal = Decimal("1")
    honor_plan_slippage_policy: bool = True
    allow_market_orders: bool = True
    dedupe_retention: int = DEFAULT_DEDUPE_RETENTION
    publish_events: bool = True
    strict_mark_presence: bool = True
    execution_mode_default: StrategyExecutionMode = StrategyExecutionMode.LIVE
    schema_version: str = PAPER_SCHEMA_VERSION
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not _is_finite_non_negative(self.initial_cash):
            raise PaperTradingConfigurationError(
                "initial_cash must be finite and >= 0.",
                code="CFG-PAPER-001",
                field="initial_cash",
            )
        for field_name in ("slippage_bps", "slippage_absolute", "brokerage_flat", "brokerage_bps"):
            value = getattr(self, field_name)
            if not _is_finite_non_negative(value):
                raise PaperTradingConfigurationError(
                    f"{field_name} must be finite and >= 0.",
                    code="CFG-PAPER-002",
                    field=field_name,
                )
        if not (Decimal("0") < self.partial_fill_fraction <= Decimal("1")):
            raise PaperTradingConfigurationError(
                "partial_fill_fraction must be in (0, 1].",
                code="CFG-PAPER-003",
                field="partial_fill_fraction",
            )
        if self.latency_base_ms < 0 or self.latency_step_ms < 0:
            raise PaperTradingConfigurationError(
                "latency fields must be >= 0.",
                code="CFG-PAPER-004",
                field="latency_base_ms",
            )
        if self.price_quantum <= 0 or self.contract_multiplier <= 0:
            raise PaperTradingConfigurationError(
                "price_quantum and contract_multiplier must be > 0.",
                code="CFG-PAPER-005",
                field="price_quantum",
            )
        if self.dedupe_retention < 1:
            raise PaperTradingConfigurationError(
                "dedupe_retention must be >= 1.",
                code="CFG-PAPER-006",
                field="dedupe_retention",
            )
        if self.schema_version != PAPER_SCHEMA_VERSION:
            raise PaperTradingConfigurationError(
                f"schema_version must be {PAPER_SCHEMA_VERSION}.",
                code="CFG-PAPER-007",
                field="schema_version",
            )
        if self.fill_model in {PaperFillModel.FULL_AT_MARK, PaperFillModel.FULL_AT_LIMIT_OR_MARK}:
            if self.partial_fill_fraction != Decimal("1"):
                raise PaperTradingConfigurationError(
                    "partial_fill_fraction must be 1 for FULL_* fill models.",
                    code="CFG-PAPER-008",
                    field="partial_fill_fraction",
                )


@dataclass(frozen=True)
class PaperSimulationContext:
    """Immutable per-run simulation inputs."""

    correlation_id: str
    reference_time: datetime
    marks: Mapping[str, Decimal]
    execution_id: str | None = None
    execution_mode: StrategyExecutionMode | None = None
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    force_reject: bool = False


@dataclass(frozen=True)
class PaperWarningRecord:
    """Non-fatal warning emitted during simulation."""

    code: str
    message: str
    leg_index: int | None = None
    field: str | None = None


@dataclass(frozen=True)
class PaperErrorRecord:
    """Structured error emitted during simulation."""

    code: str
    message: str
    leg_index: int | None = None
    field: str | None = None


@dataclass(frozen=True)
class PaperStageResult:
    """Audit record for one pipeline stage."""

    stage_id: str
    passed: bool
    rejection_code: str | None
    message: str | None
    duration_ms: float


@dataclass(frozen=True)
class PaperPipelineResult:
    """Ordered pipeline audit summary."""

    stages: tuple[PaperStageResult, ...]
    completed: bool
    primary_rejection_code: str | None
    duration_ms: float


@dataclass(frozen=True)
class PaperFill:
    """Immutable simulated fill record."""

    leg_index: int
    instrument_key: str
    side: OrderSide
    quantity: int
    raw_reference_price: Decimal
    fill_price: Decimal
    slippage_applied: Decimal
    brokerage: Decimal
    notional: Decimal
    cash_delta: Decimal
    filled_at: datetime
    idempotency_key: str
    paper_order_id: str
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class PaperCapitalSnapshot:
    """Immutable virtual capital snapshot."""

    cash: Decimal
    reserved_margin_hint: Decimal
    starting_cash: Decimal
    cumulative_brokerage: Decimal
    cumulative_realized_pnl: Decimal
    as_of: datetime
    capital_fingerprint: str


@dataclass(frozen=True)
class PaperPosition:
    """Immutable open paper position."""

    position_id: str
    instrument_key: str
    quantity: int
    average_price: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    mark_price: Decimal | None
    opened_at: datetime
    updated_at: datetime
    strategy_id: str | None = None
    plan_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class PaperPositionBookSnapshot:
    """Immutable paper position book snapshot."""

    positions: tuple[PaperPosition, ...]
    as_of: datetime
    book_fingerprint: str


@dataclass(frozen=True)
class PaperPortfolioView:
    """Immutable aggregated paper portfolio view."""

    capital: PaperCapitalSnapshot
    positions: PaperPositionBookSnapshot
    total_realized_pnl: Decimal
    total_unrealized_pnl: Decimal
    total_pnl: Decimal
    open_position_count: int
    gross_notional: Decimal
    as_of: datetime
    portfolio_fingerprint: str


@dataclass(frozen=True)
class PaperExecutionResult:
    """Immutable sealed paper simulation outcome."""

    execution_id: str
    plan_id: str
    correlation_id: str
    status: PaperExecutionStatus
    fills: tuple[PaperFill, ...]
    order_tracker: OrderTracker
    capital_snapshot: PaperCapitalSnapshot
    position_book: PaperPositionBookSnapshot
    portfolio_view: PaperPortfolioView
    pipeline_summary: PaperPipelineResult
    warnings: tuple[PaperWarningRecord, ...]
    errors: tuple[PaperErrorRecord, ...]
    simulated_at: datetime
    duration_ms: float
    execution_fingerprint: str
    primary_error_code: str | None = None
    schema_version: str = PAPER_SCHEMA_VERSION
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class PaperValidationResult:
    """Validation outcome for sealed results."""

    errors: tuple[PaperErrorRecord, ...] = ()
    warnings: tuple[PaperWarningRecord, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return True when no errors are present."""
        return not self.errors


@dataclass
class _PendingEvent:
    """Internal event queued for post-lock publish."""

    event_type: PaperEventType
    payload: Mapping[str, object]
    correlation_id: str
    occurred_at: datetime


@dataclass
class _InternalPosition:
    """Mutable internal position state."""

    position_id: str
    instrument_key: str
    quantity: int
    average_price: Decimal
    realized_pnl: Decimal
    opened_at: datetime
    updated_at: datetime
    strategy_id: str | None = None
    plan_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


def default_paper_trading_runner_config() -> PaperTradingRunnerConfig:
    """Return conservative PAPER defaults."""
    return PaperTradingRunnerConfig()


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _is_finite_non_negative(value: Decimal) -> bool:
    return value.is_finite() and value >= 0


def _is_finite_positive(value: Decimal) -> bool:
    return value.is_finite() and value > 0


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _datetime_to_iso(value: datetime) -> str:
    if not _is_timezone_aware(value):
        raise PaperTradingValidationError(
            "datetime must be timezone-aware.",
            code=ERROR_CONTEXT_INVALID,
        )
    utc_value = value.astimezone(timezone.utc)
    return utc_value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _round_price(value: Decimal, quantum: Decimal) -> Decimal:
    if quantum <= 0:
        return value
    units = (value / quantum).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return units * quantum


def config_fingerprint(config: PaperTradingRunnerConfig) -> str:
    """Compute deterministic configuration fingerprint."""
    payload = {
        "initial_cash": str(config.initial_cash),
        "reject_insufficient_capital": config.reject_insufficient_capital,
        "minimum_cash_floor": str(config.minimum_cash_floor),
        "fill_model": config.fill_model.value,
        "partial_fill_fraction": str(config.partial_fill_fraction),
        "slippage_mode": config.slippage_mode.value,
        "slippage_bps": str(config.slippage_bps),
        "slippage_absolute": str(config.slippage_absolute),
        "brokerage_mode": config.brokerage_mode.value,
        "brokerage_flat": str(config.brokerage_flat),
        "brokerage_bps": str(config.brokerage_bps),
        "latency_mode": config.latency_mode.value,
        "latency_base_ms": config.latency_base_ms,
        "latency_step_ms": config.latency_step_ms,
        "price_quantum": str(config.price_quantum),
        "contract_multiplier": str(config.contract_multiplier),
        "honor_plan_slippage_policy": config.honor_plan_slippage_policy,
        "allow_market_orders": config.allow_market_orders,
        "strict_mark_presence": config.strict_mark_presence,
        "schema_version": config.schema_version,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_capital_fingerprint(snapshot: PaperCapitalSnapshot) -> str:
    """Compute deterministic capital snapshot fingerprint."""
    payload = {
        "cash": str(snapshot.cash),
        "starting_cash": str(snapshot.starting_cash),
        "cumulative_brokerage": str(snapshot.cumulative_brokerage),
        "cumulative_realized_pnl": str(snapshot.cumulative_realized_pnl),
        "as_of": _datetime_to_iso(snapshot.as_of),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_book_fingerprint(positions: tuple[PaperPosition, ...]) -> str:
    """Compute deterministic position book fingerprint."""
    payload = {
        "positions": [
            {
                "instrument_key": position.instrument_key,
                "quantity": position.quantity,
                "average_price": str(position.average_price),
                "realized_pnl": str(position.realized_pnl),
            }
            for position in sorted(positions, key=lambda item: item.instrument_key)
        ]
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_portfolio_fingerprint(view: PaperPortfolioView) -> str:
    """Compute deterministic portfolio view fingerprint."""
    payload = {
        "capital_fingerprint": view.capital.capital_fingerprint,
        "book_fingerprint": view.positions.book_fingerprint,
        "total_pnl": str(view.total_pnl),
        "as_of": _datetime_to_iso(view.as_of),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_execution_fingerprint(
    *,
    plan_fingerprint: str,
    execution_id: str,
    fills: tuple[PaperFill, ...],
    capital_snapshot: PaperCapitalSnapshot,
    position_book: PaperPositionBookSnapshot,
    portfolio_view: PaperPortfolioView,
    order_tracker: OrderTracker,
    config: PaperTradingRunnerConfig,
) -> str:
    """Compute deterministic execution fingerprint for replay verification."""
    payload = {
        "plan_fingerprint": plan_fingerprint,
        "execution_id": execution_id,
        "fills": [
            {
                "leg_index": fill.leg_index,
                "instrument_key": fill.instrument_key,
                "side": fill.side.value,
                "quantity": fill.quantity,
                "fill_price": str(fill.fill_price),
                "cash_delta": str(fill.cash_delta),
                "filled_at": _datetime_to_iso(fill.filled_at),
            }
            for fill in fills
        ],
        "capital_fingerprint": capital_snapshot.capital_fingerprint,
        "book_fingerprint": position_book.book_fingerprint,
        "portfolio_fingerprint": portfolio_view.portfolio_fingerprint,
        "tracker_fingerprint": order_tracker.tracker_fingerprint,
        "config_hash": config_fingerprint(config),
        "schema_version": PAPER_SCHEMA_VERSION,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _safe_as_of(reference_time: datetime) -> datetime:
    """Return timezone-aware timestamp for snapshots."""
    if _is_timezone_aware(reference_time):
        return reference_time
    return datetime.now(timezone.utc)


def _generate_execution_id(plan: ExecutionPlan, correlation_id: str) -> str:
    digest = hashlib.sha256(
        f"{plan.plan_id}|{plan.plan_fingerprint}|{correlation_id}".encode("utf-8")
    ).hexdigest()
    return f"paper-exec-{digest[:24]}"


def _generate_paper_order_id(idempotency_key: str, leg_index: int) -> str:
    digest = hashlib.sha256(f"{idempotency_key}|{leg_index}".encode("utf-8")).hexdigest()
    return f"paper-ord-{digest[:16]}"


def _generate_position_id(instrument_key: str) -> str:
    digest = hashlib.sha256(instrument_key.encode("utf-8")).hexdigest()
    return f"paper-pos-{digest[:16]}"


def _decimal_from_float(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _compute_effective_slippage_bps(
    config: PaperTradingRunnerConfig,
    leg: PlannedOrderLeg,
) -> Decimal:
    leg_bps = Decimal(str(leg.max_slippage_bps or 0))
    if config.slippage_mode == PaperSlippageMode.MAX_OF_PLAN_AND_CONFIG:
        return max(config.slippage_bps, leg_bps)
    if config.slippage_mode == PaperSlippageMode.NONE:
        return Decimal("0")
    return config.slippage_bps


def _select_reference_price(
    leg: PlannedOrderLeg,
    mark: Decimal,
    config: PaperTradingRunnerConfig,
) -> Decimal:
    if leg.order_type is OrderType.LIMIT and leg.limit_price_hint is not None:
        limit_hint = Decimal(str(leg.limit_price_hint))
        if leg.side is OrderSide.BUY and mark <= limit_hint:
            return mark
        if leg.side is OrderSide.SELL and mark >= limit_hint:
            return mark
        if config.fill_model == PaperFillModel.FULL_AT_LIMIT_OR_MARK:
            return limit_hint
        return mark
    return mark


def _apply_slippage(
    reference: Decimal,
    side: OrderSide,
    config: PaperTradingRunnerConfig,
    effective_bps: Decimal,
) -> tuple[Decimal, Decimal]:
    if config.slippage_mode == PaperSlippageMode.NONE:
        fill = _round_price(reference, config.price_quantum)
        return fill, Decimal("0")
    bps_factor = effective_bps / Decimal("10000")
    if config.slippage_mode == PaperSlippageMode.ABSOLUTE:
        if side is OrderSide.BUY:
            raw = reference + config.slippage_absolute
        else:
            raw = reference - config.slippage_absolute
    else:
        if side is OrderSide.BUY:
            raw = reference * (Decimal("1") + bps_factor) + config.slippage_absolute
        else:
            raw = reference * (Decimal("1") - bps_factor) - config.slippage_absolute
    fill = _round_price(raw, config.price_quantum)
    slippage = abs(fill - reference)
    return fill, slippage


def _slippage_exceeds_ceiling(
    reference: Decimal,
    fill: Decimal,
    side: OrderSide,
    max_slippage_bps: float,
) -> bool:
    if max_slippage_bps <= 0:
        return False
    ceiling = Decimal(str(max_slippage_bps)) / Decimal("10000")
    if side is OrderSide.BUY:
        max_fill = reference * (Decimal("1") + ceiling)
        return fill > max_fill + PRICE_QUANTUM
    min_fill = reference * (Decimal("1") - ceiling)
    return fill < min_fill - PRICE_QUANTUM


def _compute_fill_quantity(planned_quantity: int, config: PaperTradingRunnerConfig) -> int:
    if config.fill_model == PaperFillModel.DETERMINISTIC_PARTIAL:
        raw = int(planned_quantity * config.partial_fill_fraction)
        return max(1, min(raw, planned_quantity))
    return planned_quantity


def _compute_brokerage(notional: Decimal, config: PaperTradingRunnerConfig) -> Decimal:
    if config.brokerage_mode == PaperBrokerageMode.NONE:
        return Decimal("0")
    flat = Decimal("0")
    bps_fee = Decimal("0")
    if config.brokerage_mode in {PaperBrokerageMode.FLAT_PER_LEG, PaperBrokerageMode.FLAT_PLUS_BPS}:
        flat = config.brokerage_flat
    if config.brokerage_mode in {PaperBrokerageMode.BPS_OF_NOTIONAL, PaperBrokerageMode.FLAT_PLUS_BPS}:
        bps_fee = abs(notional) * config.brokerage_bps / Decimal("10000")
    return _round_money(flat + bps_fee)


def _compute_cash_delta_premium(side: OrderSide, fill_price: Decimal, quantity: int, multiplier: Decimal) -> Decimal:
    notional = fill_price * Decimal(quantity) * multiplier
    if side is OrderSide.BUY:
        return -notional
    return notional


def _compute_fill_timestamp(
    leg_index: int,
    reference_time: datetime,
    config: PaperTradingRunnerConfig,
    cumulative_delay_ms: int,
) -> datetime:
    if config.latency_mode == PaperLatencyMode.NONE:
        return reference_time
    if config.latency_mode == PaperLatencyMode.FIXED_MS:
        return reference_time + timedelta(milliseconds=config.latency_base_ms)
    if config.latency_mode == PaperLatencyMode.PER_LEG_INCREMENT_MS:
        offset = config.latency_base_ms + leg_index * config.latency_step_ms
        return reference_time + timedelta(milliseconds=offset)
    return reference_time + timedelta(milliseconds=cumulative_delay_ms)


def _ordered_leg_indices(plan: ExecutionPlan) -> list[tuple[int, int]]:
    """Return (leg_index, cumulative_delay_ms) pairs in sequence order."""
    if not plan.sequences:
        return [(leg.leg_index, 0) for leg in sorted(plan.legs, key=lambda item: item.leg_index)]
    ordered: list[tuple[int, int]] = []
    cumulative = 0
    seen: set[int] = set()
    for sequence in sorted(plan.sequences, key=lambda item: item.sequence_group):
        for leg_index in sequence.leg_indices:
            ordered.append((leg_index, cumulative))
            seen.add(leg_index)
            cumulative += sequence.inter_leg_delay_ms
    for leg in sorted(plan.legs, key=lambda item: item.leg_index):
        if leg.leg_index not in seen:
            ordered.append((leg.leg_index, cumulative))
    return ordered


def _leg_by_index(plan: ExecutionPlan) -> dict[int, PlannedOrderLeg]:
    return {leg.leg_index: leg for leg in plan.legs}


def _sequence_for_leg(plan: ExecutionPlan, leg_index: int) -> LegSequence | None:
    for sequence in plan.sequences:
        if leg_index in sequence.leg_indices:
            return sequence
    return None


def _derive_paper_execution_status(
    leg_states: tuple[OrderState, ...],
    *,
    pre_rejected: bool,
    pre_status: PaperExecutionStatus | None,
) -> PaperExecutionStatus:
    if pre_rejected and pre_status is not None:
        return pre_status
    if not leg_states:
        return PaperExecutionStatus.REJECTED
    statuses = {state.lifecycle_status for state in leg_states}
    if all(state.lifecycle_status is OrderLifecycleStatus.COMPLETE for state in leg_states):
        return PaperExecutionStatus.COMPLETED
    if any(state.lifecycle_status is OrderLifecycleStatus.PARTIALLY_FILLED for state in leg_states):
        return PaperExecutionStatus.PARTIAL
    if any(state.lifecycle_status is OrderLifecycleStatus.COMPLETE for state in leg_states) and any(
        state.lifecycle_status in {OrderLifecycleStatus.REJECTED, OrderLifecycleStatus.FAILED}
        for state in leg_states
    ):
        return PaperExecutionStatus.PARTIAL
    if all(
        state.lifecycle_status in {OrderLifecycleStatus.REJECTED, OrderLifecycleStatus.FAILED}
        for state in leg_states
    ):
        return PaperExecutionStatus.FAILED
    return PaperExecutionStatus.PARTIAL


def _build_order_state_from_fill(
    leg: PlannedOrderLeg,
    plan: ExecutionPlan,
    fill: PaperFill | None,
    *,
    lifecycle_status: OrderLifecycleStatus,
    filled_at: datetime,
    error_code: str | None = None,
) -> OrderState:
    metadata = dict(plan.metadata)
    metadata.update(dict(leg.metadata))
    metadata["plan_id"] = plan.plan_id
    metadata["plan_fingerprint"] = plan.plan_fingerprint
    filled_qty = fill.quantity if fill is not None else 0
    avg_price = float(fill.fill_price) if fill is not None else None
    paper_order_id = fill.paper_order_id if fill is not None else None
    transitions: tuple[OrderStateTransition, ...] = ()
    if lifecycle_status is not OrderLifecycleStatus.PLANNED:
        path = [OrderLifecycleStatus.SUBMITTED]
        if lifecycle_status is OrderLifecycleStatus.PARTIALLY_FILLED:
            path.append(OrderLifecycleStatus.PARTIALLY_FILLED)
        elif lifecycle_status is OrderLifecycleStatus.COMPLETE:
            path.append(OrderLifecycleStatus.COMPLETE)
        elif lifecycle_status is OrderLifecycleStatus.REJECTED:
            path.append(OrderLifecycleStatus.REJECTED)
        current = OrderLifecycleStatus.PLANNED
        built: list[OrderStateTransition] = []
        for nxt in path:
            built.append(
                OrderStateTransition(
                    from_status=current,
                    to_status=nxt,
                    occurred_at=filled_at,
                    reason_code=error_code or "PAPER.SIMULATION",
                    message="Paper simulation transition.",
                )
            )
            current = nxt
        transitions = tuple(built)
    terminal = lifecycle_status in {
        OrderLifecycleStatus.COMPLETE,
        OrderLifecycleStatus.REJECTED,
        OrderLifecycleStatus.FAILED,
        OrderLifecycleStatus.CANCELLED,
        OrderLifecycleStatus.TIMEOUT,
        OrderLifecycleStatus.SKIPPED,
    }
    return OrderState(
        leg_index=leg.leg_index,
        sequence_group=leg.sequence_group,
        instrument_key=leg.instrument_key,
        side=leg.side,
        order_type=leg.order_type,
        product=leg.product,
        planned_quantity=leg.quantity,
        lifecycle_status=lifecycle_status,
        idempotency_key=leg.idempotency_key,
        filled_quantity=filled_qty,
        remaining_quantity=leg.quantity - filled_qty,
        broker_order_id=paper_order_id,
        average_fill_price=avg_price,
        attempt_count=1,
        terminal=terminal,
        terminal_at=filled_at if terminal else None,
        last_error_code=error_code,
        transitions=transitions,
        metadata=MappingProxyType(metadata),
    )


def _build_empty_tracker(plan: ExecutionPlan, execution_id: str, reference_time: datetime) -> OrderTracker:
    leg_states: tuple[OrderState, ...] = ()
    return OrderTracker(
        submission_id=execution_id,
        plan_id=plan.plan_id,
        correlation_id=plan.correlation_id,
        plan_fingerprint=plan.plan_fingerprint,
        leg_states=leg_states,
        aggregate_status=derive_aggregate_status(leg_states),
        sequence_results=(),
        started_at=reference_time,
        completed_at=reference_time,
        tracker_fingerprint=compute_tracker_fingerprint(leg_states),
    )


class _VirtualCapitalLedger:
    """Internal mutable virtual capital book."""

    def __init__(self, config: PaperTradingRunnerConfig) -> None:
        self._starting_cash = config.initial_cash
        self._cash = config.initial_cash
        self._cumulative_brokerage = Decimal("0")
        self._cumulative_realized_pnl = Decimal("0")
        self._as_of = datetime.now(timezone.utc)

    def reset(self, initial_cash: Decimal | None, reference_time: datetime) -> None:
        """Reset ledger to initial cash."""
        if initial_cash is not None:
            self._starting_cash = initial_cash
        self._cash = self._starting_cash
        self._cumulative_brokerage = Decimal("0")
        self._cumulative_realized_pnl = Decimal("0")
        self._as_of = reference_time

    def snapshot(self, as_of: datetime) -> PaperCapitalSnapshot:
        """Return immutable capital snapshot."""
        snap = PaperCapitalSnapshot(
            cash=_round_money(self._cash),
            reserved_margin_hint=Decimal("0"),
            starting_cash=self._starting_cash,
            cumulative_brokerage=_round_money(self._cumulative_brokerage),
            cumulative_realized_pnl=_round_money(self._cumulative_realized_pnl),
            as_of=as_of,
            capital_fingerprint="",
        )
        return replace(snap, capital_fingerprint=compute_capital_fingerprint(snap))

    def apply_deltas(
        self,
        cash_deltas: tuple[Decimal, ...],
        brokerage_total: Decimal,
        realized_delta: Decimal,
        as_of: datetime,
    ) -> None:
        """Apply fill economics to ledger."""
        for delta in cash_deltas:
            self._cash += delta
        self._cumulative_brokerage += brokerage_total
        self._cumulative_realized_pnl += realized_delta
        self._as_of = as_of


class _PaperPositionBookStore:
    """Internal mutable paper position book."""

    def __init__(self) -> None:
        self._positions: dict[str, _InternalPosition] = {}

    def reset(self) -> None:
        """Clear all positions."""
        self._positions.clear()

    def apply_fill(
        self,
        leg: PlannedOrderLeg,
        fill: PaperFill,
        plan: ExecutionPlan,
        config: PaperTradingRunnerConfig,
    ) -> Decimal:
        """Apply fill to position book; return realized PnL delta."""
        signed_qty = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
        key = leg.instrument_key
        existing = self._positions.get(key)
        realized_delta = Decimal("0")
        multiplier = config.contract_multiplier
        now = fill.filled_at

        if existing is None:
            self._positions[key] = _InternalPosition(
                position_id=_generate_position_id(key),
                instrument_key=key,
                quantity=signed_qty,
                average_price=fill.fill_price,
                realized_pnl=Decimal("0"),
                opened_at=now,
                updated_at=now,
                strategy_id=plan.summary.strategy_id,
                plan_id=plan.plan_id,
            )
            return realized_delta

        old_qty = existing.quantity
        new_qty = old_qty + signed_qty

        if old_qty == 0:
            existing.quantity = signed_qty
            existing.average_price = fill.fill_price
            existing.updated_at = now
            return realized_delta

        if (old_qty > 0 and signed_qty > 0) or (old_qty < 0 and signed_qty < 0):
            total_abs = abs(old_qty) + abs(signed_qty)
            existing.average_price = (
                Decimal(abs(old_qty)) * existing.average_price + Decimal(abs(signed_qty)) * fill.fill_price
            ) / Decimal(total_abs)
            existing.quantity = new_qty
            existing.updated_at = now
            return realized_delta

        reduce_qty = min(abs(signed_qty), abs(old_qty))
        if old_qty > 0:
            realized_delta = (fill.fill_price - existing.average_price) * Decimal(reduce_qty) * multiplier
        else:
            realized_delta = (existing.average_price - fill.fill_price) * Decimal(reduce_qty) * multiplier
        existing.realized_pnl += realized_delta

        remainder = abs(signed_qty) - reduce_qty
        if remainder == 0:
            existing.quantity = new_qty
            if new_qty == 0:
                del self._positions[key]
            else:
                existing.updated_at = now
            return realized_delta

        existing.quantity = (1 if signed_qty > 0 else -1) * remainder
        existing.average_price = fill.fill_price
        existing.opened_at = now
        existing.updated_at = now
        return realized_delta

    def snapshot(self, marks: Mapping[str, Decimal], as_of: datetime, config: PaperTradingRunnerConfig) -> PaperPositionBookSnapshot:
        """Return immutable position book with unrealized PnL."""
        positions: list[PaperPosition] = []
        multiplier = config.contract_multiplier
        for internal in sorted(self._positions.values(), key=lambda item: item.instrument_key):
            mark = marks.get(internal.instrument_key)
            unrealized = Decimal("0")
            if mark is not None and internal.quantity != 0:
                if internal.quantity > 0:
                    unrealized = (mark - internal.average_price) * Decimal(internal.quantity) * multiplier
                else:
                    unrealized = (internal.average_price - mark) * Decimal(abs(internal.quantity)) * multiplier
                unrealized = _round_money(unrealized)
            positions.append(
                PaperPosition(
                    position_id=internal.position_id,
                    instrument_key=internal.instrument_key,
                    quantity=internal.quantity,
                    average_price=internal.average_price,
                    realized_pnl=_round_money(internal.realized_pnl),
                    unrealized_pnl=unrealized,
                    mark_price=mark,
                    opened_at=internal.opened_at,
                    updated_at=internal.updated_at,
                    strategy_id=internal.strategy_id,
                    plan_id=internal.plan_id,
                    metadata=MappingProxyType(dict(internal.metadata)),
                )
            )
        book = PaperPositionBookSnapshot(positions=tuple(positions), as_of=as_of, book_fingerprint="")
        return replace(book, book_fingerprint=compute_book_fingerprint(book.positions))


def _aggregate_portfolio(
    capital: PaperCapitalSnapshot,
    positions: PaperPositionBookSnapshot,
    as_of: datetime,
) -> PaperPortfolioView:
    total_realized = sum((position.realized_pnl for position in positions.positions), Decimal("0"))
    total_unrealized = sum((position.unrealized_pnl for position in positions.positions), Decimal("0"))
    total_realized = _round_money(total_realized)
    total_unrealized = _round_money(total_unrealized)
    gross_notional = Decimal("0")
    for position in positions.positions:
        if position.mark_price is not None:
            gross_notional += abs(Decimal(position.quantity)) * position.mark_price
    view = PaperPortfolioView(
        capital=capital,
        positions=positions,
        total_realized_pnl=total_realized,
        total_unrealized_pnl=total_unrealized,
        total_pnl=_round_money(total_realized + total_unrealized),
        open_position_count=sum(1 for position in positions.positions if position.quantity != 0),
        gross_notional=_round_money(gross_notional),
        as_of=as_of,
        portfolio_fingerprint="",
    )
    return replace(view, portfolio_fingerprint=compute_portfolio_fingerprint(view))


class PaperTradingRunner:
    """Simulate READY execution plans without live broker I/O."""

    def __init__(
        self,
        config: PaperTradingRunnerConfig,
        event_bus: EventBus | None = None,
    ) -> None:
        self._config = config
        self._event_bus = event_bus
        self._lock = threading.RLock()
        self._capital = _VirtualCapitalLedger(config)
        self._positions = _PaperPositionBookStore()
        self._seen_execution_ids: OrderedDict[str, datetime] = OrderedDict()
        self._last_marks: Mapping[str, Decimal] = MappingProxyType({})

    def simulate_plan(
        self,
        plan: ExecutionPlan,
        context: PaperSimulationContext,
    ) -> PaperExecutionResult:
        """Simulate fills for a READY plan and update the paper ledger."""
        started = time.perf_counter()
        pending_events: list[_PendingEvent] = []
        _logger.info(
            "paper.simulate.start",
            extra={
                "event": "paper.simulate.start",
                "plan_id": plan.plan_id,
                "correlation_id": context.correlation_id,
            },
        )
        with self._lock:
            result = self._simulate_plan_locked(plan, context, started, pending_events)
        self._publish_pending_events(pending_events)
        return result

    def mark_to_market(
        self,
        marks: Mapping[str, Decimal],
        reference_time: datetime,
    ) -> PaperPortfolioView:
        """Recompute unrealized P&L and portfolio view from marks."""
        pending_events: list[_PendingEvent] = []
        with self._lock:
            for instrument_key, mark in marks.items():
                if not isinstance(mark, Decimal) or not _is_finite_positive(mark):
                    raise PaperTradingValidationError(
                        f"Invalid mark for {instrument_key}.",
                        code=ERROR_PRICE_NON_POSITIVE,
                        field="marks",
                    )
            self._last_marks = MappingProxyType(dict(marks))
            capital = self._capital.snapshot(reference_time)
            positions = self._positions.snapshot(marks, reference_time, self._config)
            view = _aggregate_portfolio(capital, positions, reference_time)
            pending_events.append(
                _PendingEvent(
                    event_type=PaperEventType.MARK_TO_MARKET,
                    payload={"portfolio_fingerprint": view.portfolio_fingerprint},
                    correlation_id="paper-mtm",
                    occurred_at=reference_time,
                )
            )
        self._publish_pending_events(pending_events)
        _logger.info("paper.mark_to_market", extra={"event": "paper.mark_to_market"})
        return view

    def get_capital_snapshot(self) -> PaperCapitalSnapshot:
        """Return immutable virtual capital snapshot."""
        with self._lock:
            return self._capital.snapshot(self._capital._as_of)

    def get_position_book(self) -> PaperPositionBookSnapshot:
        """Return immutable paper position book snapshot."""
        with self._lock:
            return self._positions.snapshot(self._last_marks, self._capital._as_of, self._config)

    def get_portfolio_view(self) -> PaperPortfolioView:
        """Return immutable paper portfolio view."""
        with self._lock:
            capital = self._capital.snapshot(self._capital._as_of)
            positions = self._positions.snapshot(self._last_marks, self._capital._as_of, self._config)
            return _aggregate_portfolio(capital, positions, self._capital._as_of)

    def reset_ledger(
        self,
        *,
        initial_cash: Decimal | None = None,
        clear_dedupe: bool = True,
    ) -> PaperCapitalSnapshot:
        """Reset capital and positions for tests or session restart."""
        reference_time = datetime.now(timezone.utc)
        with self._lock:
            if initial_cash is not None and not _is_finite_non_negative(initial_cash):
                raise PaperTradingValidationError(
                    "initial_cash must be finite and >= 0.",
                    code=ERROR_CONTEXT_INVALID,
                    field="initial_cash",
                )
            self._capital.reset(initial_cash, reference_time)
            self._positions.reset()
            self._last_marks = MappingProxyType({})
            if clear_dedupe:
                self._seen_execution_ids.clear()
            return self._capital.snapshot(reference_time)

    def validate_result(self, result: PaperExecutionResult) -> PaperValidationResult:
        """Validate a sealed simulation result."""
        errors: list[PaperErrorRecord] = []
        warnings: list[PaperWarningRecord] = list(result.warnings)
        if result.schema_version != PAPER_SCHEMA_VERSION:
            errors.append(
                PaperErrorRecord(
                    code=ERROR_SERIALIZATION_UNSUPPORTED,
                    message="Unsupported schema version.",
                )
            )
        if result.status in {
            PaperExecutionStatus.REJECTED,
            PaperExecutionStatus.EXPIRED,
            PaperExecutionStatus.INSUFFICIENT_CAPITAL,
        } and result.fills:
            errors.append(
                PaperErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="Rejected result must not contain fills.",
                )
            )
        expected = compute_execution_fingerprint(
            plan_fingerprint=result.order_tracker.plan_fingerprint,
            execution_id=result.execution_id,
            fills=result.fills,
            capital_snapshot=result.capital_snapshot,
            position_book=result.position_book,
            portfolio_view=result.portfolio_view,
            order_tracker=result.order_tracker,
            config=self._config,
        )
        if expected != result.execution_fingerprint:
            errors.append(
                PaperErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="Execution fingerprint mismatch.",
                )
            )
        if result.order_tracker.plan_id != result.plan_id:
            errors.append(
                PaperErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="Tracker plan_id mismatch.",
                )
            )
        return PaperValidationResult(errors=tuple(errors), warnings=tuple(warnings))

    def _simulate_plan_locked(
        self,
        plan: ExecutionPlan,
        context: PaperSimulationContext,
        started: float,
        pending_events: list[_PendingEvent],
    ) -> PaperExecutionResult:
        stages: list[PaperStageResult] = []
        errors: list[PaperErrorRecord] = []
        warnings: list[PaperWarningRecord] = []
        execution_id = context.execution_id or _generate_execution_id(plan, context.correlation_id)
        reserve_execution_id = False

        def stage(name: str, fn: Callable[[], str | None]) -> bool:
            stage_started = time.perf_counter()
            rejection = fn()
            duration_ms = (time.perf_counter() - stage_started) * 1000.0
            passed = rejection is None
            stages.append(
                PaperStageResult(
                    stage_id=name,
                    passed=passed,
                    rejection_code=rejection,
                    message=None if passed else rejection,
                    duration_ms=duration_ms,
                )
            )
            if rejection is not None:
                errors.append(PaperErrorRecord(code=rejection, message=rejection))
            return passed

        # Stage 1 — validate context
        if not stage("VALIDATE_CONTEXT", lambda: self._validate_context(context)):
            return self._reject_result(
                plan, context, execution_id, started, stages, errors, warnings, pending_events,
                PaperExecutionStatus.REJECTED,
            )

        if context.force_reject:
            errors.append(PaperErrorRecord(code=ERROR_CONTEXT_FORCE_REJECT, message="Forced reject."))
            return self._reject_result(
                plan, context, execution_id, started, stages, errors, warnings, pending_events,
                PaperExecutionStatus.REJECTED,
            )

        # Stage 2 — plan status
        if plan.status is not ExecutionPlanStatus.READY:
            errors.append(PaperErrorRecord(code=ERROR_PLAN_NOT_READY, message="Plan not READY."))
            stages.append(
                PaperStageResult(
                    stage_id="GATE_PLAN_STATUS",
                    passed=False,
                    rejection_code=ERROR_PLAN_NOT_READY,
                    message=ERROR_PLAN_NOT_READY,
                    duration_ms=0.0,
                )
            )
            return self._reject_result(
                plan, context, execution_id, started, stages, errors, warnings, pending_events,
                PaperExecutionStatus.REJECTED,
            )

        # Stage 3 — expiry
        if plan.valid_until is not None and context.reference_time >= plan.valid_until:
            errors.append(PaperErrorRecord(code=ERROR_PLAN_EXPIRED, message="Plan expired."))
            stages.append(
                PaperStageResult(
                    stage_id="GATE_PLAN_EXPIRY",
                    passed=False,
                    rejection_code=ERROR_PLAN_EXPIRED,
                    message=ERROR_PLAN_EXPIRED,
                    duration_ms=0.0,
                )
            )
            return self._reject_result(
                plan, context, execution_id, started, stages, errors, warnings, pending_events,
                PaperExecutionStatus.EXPIRED,
            )

        if plan.valid_until is not None:
            remaining = (plan.valid_until - context.reference_time).total_seconds()
            if 0 < remaining <= 15:
                warnings.append(
                    PaperWarningRecord(
                        code=WARN_PLAN_NEAR_EXPIRY,
                        message="Plan near expiry.",
                    )
                )

        # Stage 4 — correlation
        if context.correlation_id != plan.correlation_id:
            errors.append(PaperErrorRecord(code=ERROR_CORR_MISMATCH, message="Correlation mismatch."))
            stages.append(
                PaperStageResult(
                    stage_id="GATE_CORRELATION",
                    passed=False,
                    rejection_code=ERROR_CORR_MISMATCH,
                    message=ERROR_CORR_MISMATCH,
                    duration_ms=0.0,
                )
            )
            return self._reject_result(
                plan, context, execution_id, started, stages, errors, warnings, pending_events,
                PaperExecutionStatus.REJECTED,
            )

        # Stage 5 — execution id format + duplicate
        if context.execution_id is not None:
            if not context.execution_id or not _EXECUTION_ID_PATTERN.match(context.execution_id):
                errors.append(PaperErrorRecord(code=ERROR_EXECUTION_INVALID_ID, message="Invalid execution_id."))
                return self._reject_result(
                    plan, context, execution_id, started, stages, errors, warnings, pending_events,
                    PaperExecutionStatus.REJECTED,
                )
        if execution_id in self._seen_execution_ids:
            errors.append(PaperErrorRecord(code=ERROR_EXECUTION_DUPLICATE_ID, message="Duplicate execution_id."))
            stages.append(
                PaperStageResult(
                    stage_id="GATE_DUPLICATE_EXECUTION_ID",
                    passed=False,
                    rejection_code=ERROR_EXECUTION_DUPLICATE_ID,
                    message=ERROR_EXECUTION_DUPLICATE_ID,
                    duration_ms=0.0,
                )
            )
            return self._reject_result(
                plan, context, execution_id, started, stages, errors, warnings, pending_events,
                PaperExecutionStatus.REJECTED,
            )
        reserve_execution_id = True

        # Stage 6 — empty legs
        if not plan.legs:
            errors.append(PaperErrorRecord(code=ERROR_PLAN_EMPTY_LEGS, message="Empty legs."))
            return self._reject_result(
                plan, context, execution_id, started, stages, errors, warnings, pending_events,
                PaperExecutionStatus.REJECTED,
                record_id=reserve_execution_id,
            )

        # Stage 7 — validate legs
        leg_error = self._validate_legs(plan)
        if leg_error is not None:
            errors.append(PaperErrorRecord(code=leg_error, message=leg_error))
            return self._reject_result(
                plan, context, execution_id, started, stages, errors, warnings, pending_events,
                PaperExecutionStatus.REJECTED,
                record_id=reserve_execution_id,
            )

        # Stage 8 — marks and prices
        price_error = self._validate_marks_and_prices(plan, context)
        if price_error is not None:
            code, leg_index = price_error
            errors.append(PaperErrorRecord(code=code, message=code, leg_index=leg_index))
            return self._reject_result(
                plan, context, execution_id, started, stages, errors, warnings, pending_events,
                PaperExecutionStatus.REJECTED,
                record_id=reserve_execution_id,
            )

        # Stage 9 — quantities
        qty_error = self._validate_quantities(plan)
        if qty_error is not None:
            errors.append(PaperErrorRecord(code=qty_error, message=qty_error))
            return self._reject_result(
                plan, context, execution_id, started, stages, errors, warnings, pending_events,
                PaperExecutionStatus.REJECTED,
                record_id=reserve_execution_id,
            )

        pending_events.append(
            _PendingEvent(
                event_type=PaperEventType.PLAN_SIMULATION_STARTED,
                payload={"plan_id": plan.plan_id, "execution_id": execution_id},
                correlation_id=context.correlation_id,
                occurred_at=context.reference_time,
            )
        )

        # Stage 10 — simulate fills
        fills, leg_states, sim_errors, sim_warnings, aborted = self._simulate_fills(
            plan, context, execution_id, warnings
        )
        warnings.extend(sim_warnings)
        errors.extend(sim_errors)

        if aborted and not fills:
            self._record_execution_id(execution_id, context.reference_time)
            return self._failure_result(
                plan, context, execution_id, started, stages, fills, leg_states, errors, warnings,
                pending_events, PaperExecutionStatus.FAILED,
            )

        # Stage 11 — capital gate (atomic)
        cash_deltas = tuple(fill.cash_delta for fill in fills)
        projected_cash = self._capital._cash + sum(cash_deltas, Decimal("0"))
        if self._config.reject_insufficient_capital and projected_cash < self._config.minimum_cash_floor:
            errors.append(PaperErrorRecord(code=ERROR_CAPITAL_INSUFFICIENT, message="Insufficient capital."))
            self._record_execution_id(execution_id, context.reference_time)
            pending_events.append(
                _PendingEvent(
                    event_type=PaperEventType.PLAN_REJECTED,
                    payload={"execution_id": execution_id, "code": ERROR_CAPITAL_INSUFFICIENT},
                    correlation_id=context.correlation_id,
                    occurred_at=context.reference_time,
                )
            )
            return self._reject_result(
                plan, context, execution_id, started, stages, errors, warnings, pending_events,
                PaperExecutionStatus.INSUFFICIENT_CAPITAL,
                record_id=False,
            )

        # Stage 12 — commit ledger
        brokerage_total = sum((fill.brokerage for fill in fills), Decimal("0"))
        realized_total = Decimal("0")
        for fill in fills:
            leg = _leg_by_index(plan)[fill.leg_index]
            realized_total += self._positions.apply_fill(leg, fill, plan, self._config)
        self._capital.apply_deltas(cash_deltas, brokerage_total, realized_total, context.reference_time)
        self._last_marks = MappingProxyType(dict(context.marks))

        if self._config.brokerage_mode == PaperBrokerageMode.NONE:
            warnings.append(PaperWarningRecord(code=WARN_BROKERAGE_ZERO, message="Zero brokerage mode."))

        capital = self._capital.snapshot(context.reference_time)
        positions = self._positions.snapshot(context.marks, context.reference_time, self._config)
        portfolio = _aggregate_portfolio(capital, positions, context.reference_time)

        status = _derive_paper_execution_status(leg_states, pre_rejected=False, pre_status=None)
        if aborted:
            status = PaperExecutionStatus.PARTIAL if fills else PaperExecutionStatus.FAILED

        tracker = self._build_tracker(plan, execution_id, leg_states, context.reference_time)
        pipeline = PaperPipelineResult(
            stages=tuple(stages),
            completed=status in {PaperExecutionStatus.COMPLETED, PaperExecutionStatus.PARTIAL},
            primary_rejection_code=errors[0].code if errors else None,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        fingerprint = compute_execution_fingerprint(
            plan_fingerprint=plan.plan_fingerprint,
            execution_id=execution_id,
            fills=tuple(fills),
            capital_snapshot=capital,
            position_book=positions,
            portfolio_view=portfolio,
            order_tracker=tracker,
            config=self._config,
        )
        result = PaperExecutionResult(
            execution_id=execution_id,
            plan_id=plan.plan_id,
            correlation_id=plan.correlation_id,
            status=status,
            fills=tuple(fills),
            order_tracker=tracker,
            capital_snapshot=capital,
            position_book=positions,
            portfolio_view=portfolio,
            pipeline_summary=pipeline,
            warnings=tuple(warnings),
            errors=tuple(errors),
            simulated_at=context.reference_time,
            duration_ms=pipeline.duration_ms,
            execution_fingerprint=fingerprint,
            primary_error_code=None,
            metadata=MappingProxyType(dict(context.tags)),
        )
        self._record_execution_id(execution_id, context.reference_time)
        self._enqueue_success_events(pending_events, result, context)
        _logger.info(
            "paper.simulate.completed",
            extra={
                "event": "paper.simulate.completed",
                "execution_id": execution_id,
                "status": status.value,
            },
        )
        return result

    def _validate_context(self, context: PaperSimulationContext) -> str | None:
        if not context.correlation_id:
            return ERROR_CONTEXT_INVALID
        if not _is_timezone_aware(context.reference_time):
            return ERROR_CONTEXT_INVALID
        if not isinstance(context.marks, Mapping):
            return ERROR_CONTEXT_INVALID
        return None

    def _validate_legs(self, plan: ExecutionPlan) -> str | None:
        for leg in plan.legs:
            if not leg.instrument_key:
                return ERROR_LEG_INVALID
            if leg.side not in {OrderSide.BUY, OrderSide.SELL}:
                return ERROR_LEG_INVALID
            if not self._config.allow_market_orders and leg.order_type is OrderType.MARKET:
                return ERROR_LEG_INVALID
        return None

    def _validate_marks_and_prices(
        self,
        plan: ExecutionPlan,
        context: PaperSimulationContext,
    ) -> tuple[str, int | None] | None:
        for leg in plan.legs:
            mark = context.marks.get(leg.instrument_key)
            if mark is None:
                if self._config.strict_mark_presence:
                    return ERROR_PRICE_MARK_MISSING, leg.leg_index
                continue
            if not isinstance(mark, Decimal) or not mark.is_finite():
                return ERROR_PRICE_NON_FINITE, leg.leg_index
            if mark <= 0:
                return ERROR_PRICE_NON_POSITIVE, leg.leg_index
            if leg.limit_price_hint is not None:
                limit = Decimal(str(leg.limit_price_hint))
                if not limit.is_finite() or limit <= 0:
                    return ERROR_PRICE_INVALID_LIMIT, leg.leg_index
        return None

    def _validate_quantities(self, plan: ExecutionPlan) -> str | None:
        for leg in plan.legs:
            if leg.quantity <= 0:
                return ERROR_QTY_NON_POSITIVE
            if not isinstance(leg.quantity, int):
                return ERROR_QTY_INVALID
        return None

    def _simulate_fills(
        self,
        plan: ExecutionPlan,
        context: PaperSimulationContext,
        execution_id: str,
        existing_warnings: list[PaperWarningRecord],
    ) -> tuple[
        list[PaperFill],
        list[OrderState],
        list[PaperErrorRecord],
        list[PaperWarningRecord],
        bool,
    ]:
        fills: list[PaperFill] = []
        leg_states: list[OrderState] = []
        errors: list[PaperErrorRecord] = []
        warnings: list[PaperWarningRecord] = []
        legs_map = _leg_by_index(plan)
        abort = False
        processed: set[int] = set()

        for leg_index, cumulative_delay in _ordered_leg_indices(plan):
            if abort:
                leg = legs_map[leg_index]
                leg_states.append(
                    _build_order_state_from_fill(
                        leg,
                        plan,
                        None,
                        lifecycle_status=OrderLifecycleStatus.REJECTED,
                        filled_at=context.reference_time,
                        error_code=ERROR_SEQUENCE_ABORTED,
                    )
                )
                errors.append(
                    PaperErrorRecord(
                        code=ERROR_SEQUENCE_ABORTED,
                        message="Sequence aborted.",
                        leg_index=leg_index,
                    )
                )
                continue

            leg = legs_map.get(leg_index)
            if leg is None:
                errors.append(PaperErrorRecord(code=ERROR_LEG_INVALID, message="Missing leg.", leg_index=leg_index))
                abort = True
                continue

            mark = context.marks.get(leg.instrument_key)
            if mark is None:
                errors.append(
                    PaperErrorRecord(
                        code=ERROR_PRICE_MARK_MISSING,
                        message="Mark missing.",
                        leg_index=leg_index,
                    )
                )
                sequence = _sequence_for_leg(plan, leg_index)
                if sequence is not None and sequence.abort_on_leg_failure:
                    abort = True
                leg_states.append(
                    _build_order_state_from_fill(
                        leg,
                        plan,
                        None,
                        lifecycle_status=OrderLifecycleStatus.REJECTED,
                        filled_at=context.reference_time,
                        error_code=ERROR_PRICE_MARK_MISSING,
                    )
                )
                continue

            reference = _select_reference_price(leg, mark, self._config)
            effective_bps = _compute_effective_slippage_bps(self._config, leg)
            fill_price, slippage = _apply_slippage(reference, leg.side, self._config, effective_bps)

            if fill_price <= 0:
                errors.append(
                    PaperErrorRecord(
                        code=ERROR_PRICE_INVALID_FILL,
                        message="Invalid fill price.",
                        leg_index=leg_index,
                    )
                )
                sequence = _sequence_for_leg(plan, leg_index)
                if sequence is not None and sequence.abort_on_leg_failure:
                    abort = True
                leg_states.append(
                    _build_order_state_from_fill(
                        leg,
                        plan,
                        None,
                        lifecycle_status=OrderLifecycleStatus.REJECTED,
                        filled_at=context.reference_time,
                        error_code=ERROR_PRICE_INVALID_FILL,
                    )
                )
                continue

            if self._config.honor_plan_slippage_policy and _slippage_exceeds_ceiling(
                reference, fill_price, leg.side, plan.slippage_policy.max_slippage_bps
            ):
                errors.append(
                    PaperErrorRecord(
                        code=ERROR_SLIPPAGE_EXCEEDED,
                        message="Slippage exceeded plan ceiling.",
                        leg_index=leg_index,
                    )
                )
                warnings.append(
                    PaperWarningRecord(
                        code=WARN_SLIPPAGE_CAPPED,
                        message="Slippage capped by plan policy.",
                        leg_index=leg_index,
                    )
                )
                sequence = _sequence_for_leg(plan, leg_index)
                if sequence is not None and sequence.abort_on_leg_failure:
                    abort = True
                leg_states.append(
                    _build_order_state_from_fill(
                        leg,
                        plan,
                        None,
                        lifecycle_status=OrderLifecycleStatus.REJECTED,
                        filled_at=context.reference_time,
                        error_code=ERROR_SLIPPAGE_EXCEEDED,
                    )
                )
                continue

            fill_qty = _compute_fill_quantity(leg.quantity, self._config)
            filled_at = _compute_fill_timestamp(leg_index, context.reference_time, self._config, cumulative_delay)
            notional = _round_money(fill_price * Decimal(fill_qty) * self._config.contract_multiplier)
            brokerage = _compute_brokerage(notional, self._config)
            premium = _compute_cash_delta_premium(leg.side, fill_price, fill_qty, self._config.contract_multiplier)
            cash_delta = _round_money(premium - brokerage)
            paper_order_id = _generate_paper_order_id(leg.idempotency_key, leg.leg_index)
            fill = PaperFill(
                leg_index=leg.leg_index,
                instrument_key=leg.instrument_key,
                side=leg.side,
                quantity=fill_qty,
                raw_reference_price=reference,
                fill_price=fill_price,
                slippage_applied=slippage,
                brokerage=brokerage,
                notional=notional,
                cash_delta=cash_delta,
                filled_at=filled_at,
                idempotency_key=leg.idempotency_key,
                paper_order_id=paper_order_id,
                metadata=MappingProxyType({"execution_id": execution_id}),
            )
            fills.append(fill)
            lifecycle = (
                OrderLifecycleStatus.PARTIALLY_FILLED
                if fill_qty < leg.quantity
                else OrderLifecycleStatus.COMPLETE
            )
            leg_states.append(
                _build_order_state_from_fill(
                    leg,
                    plan,
                    fill,
                    lifecycle_status=lifecycle,
                    filled_at=filled_at,
                )
            )
            processed.add(leg_index)
            _logger.info(
                "paper.leg.filled",
                extra={"event": "paper.leg.filled", "leg_index": leg_index},
            )

        for leg in plan.legs:
            if leg.leg_index not in processed and not any(state.leg_index == leg.leg_index for state in leg_states):
                leg_states.append(
                    _build_order_state_from_fill(
                        leg,
                        plan,
                        None,
                        lifecycle_status=OrderLifecycleStatus.REJECTED,
                        filled_at=context.reference_time,
                        error_code=ERROR_SEQUENCE_ABORTED,
                    )
                )

        leg_states.sort(key=lambda item: item.leg_index)
        return fills, leg_states, errors, warnings, abort

    def _build_tracker(
        self,
        plan: ExecutionPlan,
        execution_id: str,
        leg_states: list[OrderState],
        reference_time: datetime,
    ) -> OrderTracker:
        states = tuple(leg_states)
        sequence_results: list[OrderSequenceResult] = []
        for sequence in plan.sequences:
            seq_states = [state for state in states if state.leg_index in sequence.leg_indices]
            completed = all(state.lifecycle_status is OrderLifecycleStatus.COMPLETE for state in seq_states)
            aborted = any(state.lifecycle_status is OrderLifecycleStatus.REJECTED for state in seq_states)
            sequence_results.append(
                OrderSequenceResult(
                    sequence_group=sequence.sequence_group,
                    mode=sequence.mode,
                    leg_indices=sequence.leg_indices,
                    completed=completed,
                    aborted=aborted,
                    duration_ms=0.0,
                )
            )
        return OrderTracker(
            submission_id=execution_id,
            plan_id=plan.plan_id,
            correlation_id=plan.correlation_id,
            plan_fingerprint=plan.plan_fingerprint,
            leg_states=states,
            aggregate_status=derive_aggregate_status(states),
            sequence_results=tuple(sequence_results),
            started_at=reference_time,
            completed_at=reference_time,
            tracker_fingerprint=compute_tracker_fingerprint(states),
        )

    def _reject_result(
        self,
        plan: ExecutionPlan,
        context: PaperSimulationContext,
        execution_id: str,
        started: float,
        stages: list[PaperStageResult],
        errors: list[PaperErrorRecord],
        warnings: list[PaperWarningRecord],
        pending_events: list[_PendingEvent],
        status: PaperExecutionStatus,
        *,
        record_id: bool = False,
    ) -> PaperExecutionResult:
        _logger.info(
            "paper.simulate.rejected",
            extra={"event": "paper.simulate.rejected", "status": status.value},
        )
        if record_id:
            self._record_execution_id(execution_id, _safe_as_of(context.reference_time))
        as_of = _safe_as_of(context.reference_time)
        capital = self._capital.snapshot(as_of)
        positions = self._positions.snapshot(context.marks, as_of, self._config)
        portfolio = _aggregate_portfolio(capital, positions, as_of)
        tracker = _build_empty_tracker(plan, execution_id, as_of)
        pipeline = PaperPipelineResult(
            stages=tuple(stages),
            completed=False,
            primary_rejection_code=errors[0].code if errors else None,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        fingerprint = compute_execution_fingerprint(
            plan_fingerprint=plan.plan_fingerprint,
            execution_id=execution_id,
            fills=(),
            capital_snapshot=capital,
            position_book=positions,
            portfolio_view=portfolio,
            order_tracker=tracker,
            config=self._config,
        )
        pending_events.append(
            _PendingEvent(
                event_type=PaperEventType.PLAN_REJECTED,
                payload={
                    "execution_id": execution_id,
                    "code": errors[0].code if errors else status.value,
                },
                correlation_id=context.correlation_id,
                occurred_at=as_of,
            )
        )
        return PaperExecutionResult(
            execution_id=execution_id,
            plan_id=plan.plan_id,
            correlation_id=plan.correlation_id,
            status=status,
            fills=(),
            order_tracker=tracker,
            capital_snapshot=capital,
            position_book=positions,
            portfolio_view=portfolio,
            pipeline_summary=pipeline,
            warnings=tuple(warnings),
            errors=tuple(errors),
            simulated_at=as_of,
            duration_ms=pipeline.duration_ms,
            execution_fingerprint=fingerprint,
            primary_error_code=errors[0].code if errors else None,
            metadata=MappingProxyType(dict(context.tags)),
        )

    def _failure_result(
        self,
        plan: ExecutionPlan,
        context: PaperSimulationContext,
        execution_id: str,
        started: float,
        stages: list[PaperStageResult],
        fills: list[PaperFill],
        leg_states: list[OrderState],
        errors: list[PaperErrorRecord],
        warnings: list[PaperWarningRecord],
        pending_events: list[_PendingEvent],
        status: PaperExecutionStatus,
    ) -> PaperExecutionResult:
        _logger.info("paper.simulate.failed", extra={"event": "paper.simulate.failed"})
        capital = self._capital.snapshot(context.reference_time)
        positions = self._positions.snapshot(context.marks, context.reference_time, self._config)
        portfolio = _aggregate_portfolio(capital, positions, context.reference_time)
        tracker = self._build_tracker(plan, execution_id, leg_states, context.reference_time)
        pipeline = PaperPipelineResult(
            stages=tuple(stages),
            completed=False,
            primary_rejection_code=errors[0].code if errors else None,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        fingerprint = compute_execution_fingerprint(
            plan_fingerprint=plan.plan_fingerprint,
            execution_id=execution_id,
            fills=tuple(fills),
            capital_snapshot=capital,
            position_book=positions,
            portfolio_view=portfolio,
            order_tracker=tracker,
            config=self._config,
        )
        pending_events.append(
            _PendingEvent(
                event_type=PaperEventType.PLAN_REJECTED,
                payload={"execution_id": execution_id, "code": errors[0].code if errors else status.value},
                correlation_id=context.correlation_id,
                occurred_at=context.reference_time,
            )
        )
        return PaperExecutionResult(
            execution_id=execution_id,
            plan_id=plan.plan_id,
            correlation_id=plan.correlation_id,
            status=status,
            fills=tuple(fills),
            order_tracker=tracker,
            capital_snapshot=capital,
            position_book=positions,
            portfolio_view=portfolio,
            pipeline_summary=pipeline,
            warnings=tuple(warnings),
            errors=tuple(errors),
            simulated_at=context.reference_time,
            duration_ms=pipeline.duration_ms,
            execution_fingerprint=fingerprint,
            primary_error_code=errors[0].code if errors else None,
            metadata=MappingProxyType(dict(context.tags)),
        )

    def _record_execution_id(self, execution_id: str, seen_at: datetime) -> None:
        self._seen_execution_ids[execution_id] = seen_at
        while len(self._seen_execution_ids) > self._config.dedupe_retention:
            self._seen_execution_ids.popitem(last=False)

    def _enqueue_success_events(
        self,
        pending_events: list[_PendingEvent],
        result: PaperExecutionResult,
        context: PaperSimulationContext,
    ) -> None:
        for fill in result.fills:
            event_type = (
                PaperEventType.LEG_PARTIALLY_FILLED
                if any(
                    state.leg_index == fill.leg_index
                    and state.lifecycle_status is OrderLifecycleStatus.PARTIALLY_FILLED
                    for state in result.order_tracker.leg_states
                )
                else PaperEventType.LEG_FILLED
            )
            pending_events.append(
                _PendingEvent(
                    event_type=event_type,
                    payload={"leg_index": fill.leg_index, "fill_price": str(fill.fill_price)},
                    correlation_id=context.correlation_id,
                    occurred_at=fill.filled_at,
                )
            )
        pending_events.append(
            _PendingEvent(
                event_type=PaperEventType.CAPITAL_UPDATED,
                payload={"capital_fingerprint": result.capital_snapshot.capital_fingerprint},
                correlation_id=context.correlation_id,
                occurred_at=context.reference_time,
            )
        )
        pending_events.append(
            _PendingEvent(
                event_type=PaperEventType.POSITION_UPDATED,
                payload={"book_fingerprint": result.position_book.book_fingerprint},
                correlation_id=context.correlation_id,
                occurred_at=context.reference_time,
            )
        )
        pending_events.append(
            _PendingEvent(
                event_type=PaperEventType.PORTFOLIO_UPDATED,
                payload={"portfolio_fingerprint": result.portfolio_view.portfolio_fingerprint},
                correlation_id=context.correlation_id,
                occurred_at=context.reference_time,
            )
        )
        pending_events.append(
            _PendingEvent(
                event_type=PaperEventType.PLAN_COMPLETED,
                payload={
                    "execution_id": result.execution_id,
                    "status": result.status.value,
                    "execution_fingerprint": result.execution_fingerprint,
                },
                correlation_id=context.correlation_id,
                occurred_at=context.reference_time,
            )
        )

    def _publish_pending_events(self, pending_events: list[_PendingEvent]) -> None:
        if self._event_bus is None or not self._config.publish_events:
            return
        for item in pending_events:
            envelope = EventEnvelope(
                event_id=str(uuid.uuid4()),
                topic=item.event_type.topic,
                payload=dict(item.payload),
                correlation_id=item.correlation_id,
                producer=PRODUCER_NAME,
                occurred_at=item.occurred_at,
                published_at=datetime.now(timezone.utc),
                producer_version=PAPER_TRADING_RUNNER_VERSION,
            )
            self._event_bus.publish(envelope)


def to_jsonable(obj: object) -> Mapping[str, object]:
    """Convert public paper types to JSON-serializable mapping."""
    if isinstance(obj, PaperExecutionResult):
        return serialize_paper_execution_result(obj)
    if isinstance(obj, PaperTradingRunnerConfig):
        return {
            "schema_version": obj.schema_version,
            "initial_cash": str(obj.initial_cash),
            "reject_insufficient_capital": obj.reject_insufficient_capital,
            "minimum_cash_floor": str(obj.minimum_cash_floor),
            "fill_model": obj.fill_model.value,
            "partial_fill_fraction": str(obj.partial_fill_fraction),
            "slippage_mode": obj.slippage_mode.value,
            "slippage_bps": str(obj.slippage_bps),
            "slippage_absolute": str(obj.slippage_absolute),
            "brokerage_mode": obj.brokerage_mode.value,
            "brokerage_flat": str(obj.brokerage_flat),
            "brokerage_bps": str(obj.brokerage_bps),
            "latency_mode": obj.latency_mode.value,
            "latency_base_ms": obj.latency_base_ms,
            "latency_step_ms": obj.latency_step_ms,
            "price_quantum": str(obj.price_quantum),
            "contract_multiplier": str(obj.contract_multiplier),
        }
    raise PaperTradingSerializationError(
        "Unsupported type for JSON serialization.",
        code=ERROR_SERIALIZATION_UNSUPPORTED,
    )


def serialize_paper_execution_result(result: PaperExecutionResult) -> Mapping[str, object]:
    """Serialize :class:`PaperExecutionResult` to JSON-compatible mapping."""
    return {
        "schema_version": result.schema_version,
        "execution_id": result.execution_id,
        "plan_id": result.plan_id,
        "correlation_id": result.correlation_id,
        "status": result.status.value,
        "fills": [
            {
                "leg_index": fill.leg_index,
                "instrument_key": fill.instrument_key,
                "side": fill.side.value,
                "quantity": fill.quantity,
                "raw_reference_price": str(fill.raw_reference_price),
                "fill_price": str(fill.fill_price),
                "slippage_applied": str(fill.slippage_applied),
                "brokerage": str(fill.brokerage),
                "notional": str(fill.notional),
                "cash_delta": str(fill.cash_delta),
                "filled_at": _datetime_to_iso(fill.filled_at),
                "idempotency_key": fill.idempotency_key,
                "paper_order_id": fill.paper_order_id,
            }
            for fill in result.fills
        ],
        "capital_snapshot": {
            "cash": str(result.capital_snapshot.cash),
            "starting_cash": str(result.capital_snapshot.starting_cash),
            "cumulative_brokerage": str(result.capital_snapshot.cumulative_brokerage),
            "cumulative_realized_pnl": str(result.capital_snapshot.cumulative_realized_pnl),
            "as_of": _datetime_to_iso(result.capital_snapshot.as_of),
            "capital_fingerprint": result.capital_snapshot.capital_fingerprint,
        },
        "position_book": {
            "as_of": _datetime_to_iso(result.position_book.as_of),
            "book_fingerprint": result.position_book.book_fingerprint,
            "positions": [
                {
                    "position_id": position.position_id,
                    "instrument_key": position.instrument_key,
                    "quantity": position.quantity,
                    "average_price": str(position.average_price),
                    "realized_pnl": str(position.realized_pnl),
                    "unrealized_pnl": str(position.unrealized_pnl),
                    "mark_price": str(position.mark_price) if position.mark_price is not None else None,
                }
                for position in result.position_book.positions
            ],
        },
        "portfolio_view": {
            "total_realized_pnl": str(result.portfolio_view.total_realized_pnl),
            "total_unrealized_pnl": str(result.portfolio_view.total_unrealized_pnl),
            "total_pnl": str(result.portfolio_view.total_pnl),
            "open_position_count": result.portfolio_view.open_position_count,
            "gross_notional": str(result.portfolio_view.gross_notional),
            "as_of": _datetime_to_iso(result.portfolio_view.as_of),
            "portfolio_fingerprint": result.portfolio_view.portfolio_fingerprint,
        },
        "order_tracker": {
            "submission_id": result.order_tracker.submission_id,
            "plan_id": result.order_tracker.plan_id,
            "correlation_id": result.order_tracker.correlation_id,
            "plan_fingerprint": result.order_tracker.plan_fingerprint,
            "aggregate_status": result.order_tracker.aggregate_status.value,
            "tracker_fingerprint": result.order_tracker.tracker_fingerprint,
            "leg_states": [
                {
                    "leg_index": state.leg_index,
                    "lifecycle_status": state.lifecycle_status.value,
                    "filled_quantity": state.filled_quantity,
                    "remaining_quantity": state.remaining_quantity,
                    "broker_order_id": state.broker_order_id,
                    "average_fill_price": state.average_fill_price,
                }
                for state in result.order_tracker.leg_states
            ],
        },
        "execution_fingerprint": result.execution_fingerprint,
        "simulated_at": _datetime_to_iso(result.simulated_at),
        "duration_ms": result.duration_ms,
        "errors": [{"code": error.code, "message": error.message} for error in result.errors],
        "warnings": [{"code": warning.code, "message": warning.message} for warning in result.warnings],
    }


def deserialize_paper_execution_result(data: Mapping[str, object]) -> PaperExecutionResult:
    """Deserialize mapping to :class:`PaperExecutionResult`.

    Raises:
        PaperTradingSerializationError: When schema version is unsupported or
            full OrderTracker reconstruction is not available.
    """
    schema_version = data.get("schema_version")
    if schema_version != PAPER_SCHEMA_VERSION:
        raise PaperTradingSerializationError(
            f"Unsupported schema version: {schema_version}.",
            code=ERROR_SERIALIZATION_UNSUPPORTED,
        )
    raise PaperTradingSerializationError(
        "Full PaperExecutionResult deserialization is not supported in v1.",
        code=ERROR_SERIALIZATION_UNSUPPORTED,
    )

