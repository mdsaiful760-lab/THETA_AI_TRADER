"""Institutional system orchestrator for THETA AI TRADER v1.0.

Root coordinator that wires Event Bus subscriptions, executes pre-trade and
post-fill pipelines, monitors health, and performs graceful shutdown.
Never performs market analysis, strategy selection, broker API calls, APME
logic, or risk calculations.
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
from typing import Any, Callable, Final, Mapping, TypeVar

from apme.adaptive_position_management_engine import (
    APMEDecisionReport,
    APMEEvaluationContext,
    NewsEventFlag,
    RegimeHints,
    SignalManagementMetadata,
    TrendHints,
    VolatilityHints,
)
from core.engine_context import EngineContext
from core.engine_result import EngineResult, EngineStatus
from core.event_bus import EventBus, EventEnvelope, SubscriptionHandle
from decision.trade_decision_engine import (
    DecisionMode,
    DecisionRunContext,
    DecisionStatus,
    TradeDecisionResult,
    UserPreferences,
    default_user_preferences,
)
from execution.execution_engine import (
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionRunContext,
)
from execution.order_manager import (
    OrderSubmissionContext,
    OrderSubmissionResult,
    OrderTracker,
)
from market_data.market_snapshot import MarketSnapshot
from portfolio.portfolio_manager import (
    PortfolioIngestContext,
    PortfolioSnapshot as PortfolioManagerSnapshot,
    PortfolioUpdateResult,
    PortfolioUpdateStatus,
    PositionGreekHint,
)
from portfolio.position_manager import (
    PositionUpdateContext,
    PositionUpdateResult,
    PositionUpdateStatus,
)
from risk.risk_engine import (
    PortfolioExposureSummary,
    PortfolioPosition,
    PortfolioSnapshot as RiskPortfolioSnapshot,
    RiskDecisionResult,
    RiskRunContext,
    RiskVerdict,
    UserRiskProfile,
    default_user_risk_profile,
)
from strategy.registry import RegistrySnapshot
from strategy.signals import StrategyExecutionMode
from strategy.strategy_evaluation_engine import (
    EvaluationRunContext,
    StrategyEvaluationBundle,
)

T = TypeVar("T")

ORCHESTRATOR_VERSION: Final[str] = "1.0.0"
ORCHESTRATOR_SCHEMA_VERSION: Final[str] = "1.0.0"
PRODUCER_NAME: Final[str] = "system_orchestrator"

ERROR_CONFIG_INVALID: Final[str] = "ORCHESTRATOR.CONFIG.INVALID"
ERROR_CONTEXT_INVALID: Final[str] = "ORCHESTRATOR.CONTEXT.INVALID"
ERROR_CONTEXT_NAIVE_TIMESTAMP: Final[str] = "ORCHESTRATOR.CONTEXT.NAIVE_TIMESTAMP"
ERROR_CONTEXT_CORRELATION_MISMATCH: Final[str] = "ORCHESTRATOR.CONTEXT.CORRELATION_MISMATCH"
ERROR_STATE_INVALID: Final[str] = "ORCHESTRATOR.STATE.INVALID"
ERROR_CYCLE_OVERLAP: Final[str] = "ORCHESTRATOR.CYCLE.OVERLAP"
ERROR_CYCLE_TIMEOUT: Final[str] = "ORCHESTRATOR.CYCLE.TIMEOUT"
ERROR_ENGINE_FAILURE: Final[str] = "ORCHESTRATOR.ENGINE.FAILURE"
ERROR_ENGINE_NOT_READY: Final[str] = "ORCHESTRATOR.ENGINE.NOT_READY"
ERROR_SNAPSHOT_STALE: Final[str] = "ORCHESTRATOR.SNAPSHOT.STALE"
ERROR_SHUTDOWN_DRAIN_TIMEOUT: Final[str] = "ORCHESTRATOR.SHUTDOWN.DRAIN_TIMEOUT"
ERROR_RESULT_INVALID: Final[str] = "ORCHESTRATOR.RESULT.INVALID"
ERROR_RESULT_FINGERPRINT_MISMATCH: Final[str] = "ORCHESTRATOR.RESULT.FINGERPRINT_MISMATCH"
ERROR_SERIALIZATION_UNSUPPORTED_VERSION: Final[str] = "ORCHESTRATOR.SERIALIZATION.UNSUPPORTED_VERSION"
ERROR_SERIALIZATION_MALFORMED: Final[str] = "ORCHESTRATOR.SERIALIZATION.MALFORMED"

_logger = logging.getLogger("system.system_orchestrator")


class OrchestratorError(Exception):
    """Base orchestrator exception."""

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


class OrchestratorConfigurationError(OrchestratorError):
    """Raised when orchestrator configuration is invalid."""


class OrchestratorValidationError(OrchestratorError):
    """Raised when input or output validation fails."""


class OrchestratorStateError(OrchestratorError):
    """Raised when lifecycle state does not permit an operation."""


class OrchestratorState(str, Enum):
    """Lifecycle state of the system orchestrator."""

    UNINITIALIZED = "uninitialized"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class CycleStatus(str, Enum):
    """Overall outcome of a trading or post-fill cycle."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMEOUT = "timeout"


class CycleTrigger(str, Enum):
    """What initiated a pipeline cycle."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"
    MARKET_SNAPSHOT = "market_snapshot"
    ORDER_COMPLETED = "order_completed"
    PORTFOLIO_UPDATED = "portfolio_updated"
    APME_ESCALATION = "apme_escalation"


class EngineId(str, Enum):
    """Identifiers for coordinated platform engines."""

    EVENT_BUS = "event_bus"
    MARKET_DATA = "market_data"
    STRATEGY_EVALUATION = "strategy_evaluation"
    TRADE_DECISION = "trade_decision"
    RISK = "risk"
    EXECUTION = "execution"
    ORDER_MANAGER = "order_manager"
    POSITION_MANAGER = "position_manager"
    PORTFOLIO_MANAGER = "portfolio_manager"
    APME = "apme"


class OrchestratorEventType(str, Enum):
    """Orchestrator lifecycle and pipeline event types."""

    STARTUP_STARTED = "startup_started"
    STARTUP_COMPLETED = "startup_completed"
    STARTUP_FAILED = "startup_failed"
    SHUTDOWN_STARTED = "shutdown_started"
    SHUTDOWN_COMPLETED = "shutdown_completed"
    STATE_CHANGED = "state_changed"
    CYCLE_STARTED = "cycle_started"
    CYCLE_COMPLETED = "cycle_completed"
    CYCLE_FAILED = "cycle_failed"
    HEALTH_DEGRADED = "health_degraded"
    HEALTH_RECOVERED = "health_recovered"
    ENGINE_FAILURE = "engine_failure"
    ORCHESTRATOR_ERROR = "orchestrator_error"


class PreTradeCycleStageId(str, Enum):
    """Ordered pre-trade pipeline stage identifiers."""

    INPUT_GATE = "input_gate"
    MARKET_DATA_REFRESH = "market_data_refresh"
    STRATEGY_EVALUATION = "strategy_evaluation"
    TRADE_DECISION = "trade_decision"
    PORTFOLIO_RISK_MAPPING = "portfolio_risk_mapping"
    RISK_REVIEW = "risk_review"
    EXECUTION_PLANNING = "execution_planning"
    ORDER_SUBMISSION = "order_submission"
    RESULT_ASSEMBLY = "result_assembly"
    OUTPUT_VALIDATION = "output_validation"


class PostFillCycleStageId(str, Enum):
    """Ordered post-fill pipeline stage identifiers."""

    INPUT_GATE = "input_gate"
    POSITION_UPDATE = "position_update"
    PORTFOLIO_INGEST = "portfolio_ingest"
    APME_EVALUATION = "apme_evaluation"
    MANAGEMENT_DECISION_HANDOFF = "management_decision_handoff"
    RESULT_ASSEMBLY = "result_assembly"
    OUTPUT_VALIDATION = "output_validation"


class StartupStatus(str, Enum):
    """Outcome of orchestrator startup."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ShutdownStatus(str, Enum):
    """Outcome of orchestrator shutdown."""

    SUCCESS = "success"
    FORCED = "forced"
    FAILED = "failed"


class HealthStatus(str, Enum):
    """Aggregated health band for orchestrator or engine."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


_CRITICAL_ENGINES: Final[frozenset[EngineId]] = frozenset(
    {
        EngineId.EVENT_BUS,
        EngineId.MARKET_DATA,
        EngineId.RISK,
        EngineId.ORDER_MANAGER,
        EngineId.POSITION_MANAGER,
        EngineId.PORTFOLIO_MANAGER,
    }
)

_DEFAULT_SUBSCRIPTION_PATTERNS: Final[tuple[str, ...]] = (
    "market.snapshot.published",
    "order.plan.completed",
    "portfolio.snapshot.published",
    "apme.risk.escalated",
)

_PRE_TRADE_STAGE_ORDER: Final[tuple[str, ...]] = tuple(s.value for s in PreTradeCycleStageId)
_POST_FILL_STAGE_ORDER: Final[tuple[str, ...]] = tuple(s.value for s in PostFillCycleStageId)


@dataclass(frozen=True)
class SystemOrchestratorConfig:
    """Immutable orchestrator configuration."""

    execution_mode: StrategyExecutionMode = StrategyExecutionMode.LIVE
    account_id: str = ""
    enable_pre_trade_cycle: bool = True
    enable_post_fill_cycle: bool = True
    enable_event_driven_cycles: bool = True
    serial_cycle_execution: bool = True
    cycle_timeout_seconds: int = 120
    shutdown_drain_timeout_seconds: int = 60
    health_probe_interval_seconds: int = 30
    stale_snapshot_max_age_seconds: int = 60
    strict_correlation: bool = True
    deterministic_fingerprint: bool = True
    publish_system_events: bool = True
    fail_fast_on_engine_error: bool = False
    block_pre_trade_in_degraded: bool = True
    subscription_patterns: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class EngineRegistry:
    """Injected engine instances for orchestrator coordination."""

    event_bus: EventBus | None = None
    market_data: object | None = None
    strategy_evaluation: object | None = None
    trade_decision: object | None = None
    risk: object | None = None
    execution: object | None = None
    order_manager: object | None = None
    position_manager: object | None = None
    portfolio_manager: object | None = None
    apme: object | None = None


@dataclass(frozen=True)
class TradingCycleContext:
    """Immutable inputs for one pre-trade pipeline run."""

    correlation_id: str
    reference_time: datetime
    execution_mode: StrategyExecutionMode
    account_id: str
    trigger: CycleTrigger = CycleTrigger.MANUAL
    market_snapshot: MarketSnapshot | None = None
    registry_snapshot: RegistrySnapshot | None = None
    user_risk_profile: UserRiskProfile | None = None
    decision_mode: DecisionMode = DecisionMode.AUTONOMOUS
    user_preferences: UserPreferences | None = None
    account_equity_hint: float | None = None
    account_cash_hint: float | None = None
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class PostFillCycleContext:
    """Immutable inputs for one post-fill pipeline run."""

    correlation_id: str
    reference_time: datetime
    execution_mode: StrategyExecutionMode
    account_id: str
    order_tracker: OrderTracker
    price_hints: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    equity_hint: float = 0.0
    cash_available_hint: float = 0.0
    margin_used_hint: float = 0.0
    margin_available_hint: float | None = None
    greek_hints: Mapping[str, PositionGreekHint] = field(
        default_factory=lambda: MappingProxyType({})
    )
    volatility_hints: VolatilityHints | None = None
    regime_hints: RegimeHints | None = None
    trend_hints: Mapping[str, TrendHints] = field(default_factory=lambda: MappingProxyType({}))
    news_flags: tuple[NewsEventFlag, ...] = ()
    signal_metadata: Mapping[str, SignalManagementMetadata] = field(
        default_factory=lambda: MappingProxyType({})
    )
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class OrchestratorWarningRecord:
    """Non-fatal orchestrator warning."""

    code: str
    message: str
    stage_id: str | None = None
    engine_id: EngineId | None = None
    field: str | None = None


@dataclass(frozen=True)
class OrchestratorErrorRecord:
    """Structured orchestrator error."""

    code: str
    message: str
    stage_id: str | None = None
    engine_id: EngineId | None = None
    field: str | None = None


@dataclass(frozen=True)
class CycleStageResult:
    """Audit record for one pipeline stage."""

    stage_id: str
    engine_id: EngineId | None
    passed: bool
    rejection_code: str | None
    message: str | None
    duration_ms: float
    details: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class TradingCycleResult:
    """Immutable sealed pre-trade cycle outcome."""

    cycle_id: str
    correlation_id: str
    status: CycleStatus
    trigger: CycleTrigger
    stages: tuple[CycleStageResult, ...]
    market_snapshot_id: str | None
    strategy_result_id: str | None
    decision_result_id: str | None
    risk_verdict: str | None
    execution_plan_id: str | None
    order_submission_id: str | None
    warnings: tuple[OrchestratorWarningRecord, ...]
    errors: tuple[OrchestratorErrorRecord, ...]
    primary_error_code: str | None
    submitted_at: datetime
    completed_at: datetime | None
    duration_ms: float
    cycle_fingerprint: str


@dataclass(frozen=True)
class PostFillCycleResult:
    """Immutable sealed post-fill cycle outcome."""

    cycle_id: str
    correlation_id: str
    status: CycleStatus
    position_update_id: str | None
    portfolio_update_id: str | None
    apme_report_id: str | None
    stages: tuple[CycleStageResult, ...]
    warnings: tuple[OrchestratorWarningRecord, ...]
    errors: tuple[OrchestratorErrorRecord, ...]
    primary_error_code: str | None
    submitted_at: datetime
    completed_at: datetime | None
    duration_ms: float
    cycle_fingerprint: str


@dataclass(frozen=True)
class EngineHealthStatus:
    """Health snapshot for one coordinated engine."""

    engine_id: EngineId
    status: HealthStatus
    last_success_at: datetime | None
    last_failure_at: datetime | None
    consecutive_failures: int
    message: str | None = None


@dataclass(frozen=True)
class EventBusHealthMetrics:
    """Event bus health metrics for system health report."""

    publish_count: int
    delivery_count: int
    subscriber_failure_count: int
    active_subscriptions: int


@dataclass(frozen=True)
class HealthIssueRecord:
    """Structured health issue for system health report."""

    issue_code: str
    severity: str
    message: str
    engine_id: EngineId | None = None


@dataclass(frozen=True)
class SystemHealthReport:
    """Aggregated platform health snapshot."""

    report_id: str
    as_of: datetime
    orchestrator_state: OrchestratorState
    engine_health: Mapping[EngineId, EngineHealthStatus]
    event_bus_metrics: EventBusHealthMetrics
    last_cycle_at: datetime | None
    last_cycle_status: CycleStatus | None
    stale_snapshot: bool
    issues: tuple[HealthIssueRecord, ...]
    overall_status: HealthStatus


@dataclass(frozen=True)
class SystemStartupResult:
    """Immutable startup outcome."""

    startup_id: str
    status: StartupStatus
    engines_started: tuple[EngineId, ...]
    engines_failed: tuple[EngineId, ...]
    subscriptions_registered: int
    warnings: tuple[OrchestratorWarningRecord, ...]
    errors: tuple[OrchestratorErrorRecord, ...]
    started_at: datetime
    completed_at: datetime
    duration_ms: float


@dataclass(frozen=True)
class SystemShutdownResult:
    """Immutable shutdown outcome."""

    shutdown_id: str
    status: ShutdownStatus
    cycles_drained: int
    subscriptions_removed: int
    engines_stopped: tuple[EngineId, ...]
    warnings: tuple[OrchestratorWarningRecord, ...]
    errors: tuple[OrchestratorErrorRecord, ...]
    started_at: datetime
    completed_at: datetime
    duration_ms: float


@dataclass(frozen=True)
class OrchestratorEvent:
    """Structured orchestrator lifecycle event payload."""

    event_type: OrchestratorEventType
    topic: str
    correlation_id: str
    occurred_at: datetime
    orchestrator_state: OrchestratorState
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class OrchestratorValidationResult:
    """Validation outcome for orchestrator artifacts."""

    errors: tuple[OrchestratorErrorRecord, ...] = ()
    warnings: tuple[OrchestratorWarningRecord, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return True when no errors are present."""
        return not self.errors


def _utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def _is_timezone_aware(value: datetime) -> bool:
    """Return True when datetime carries timezone information."""
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _canonical_json(payload: Mapping[str, object]) -> str:
    """Serialize mapping to canonical JSON."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _isoformat_utc(value: datetime) -> str:
    """Serialize datetime as ISO-8601 UTC with Z suffix."""
    if not _is_timezone_aware(value):
        raise OrchestratorValidationError(
            "datetime must be timezone-aware.",
            code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
            field="datetime",
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_datetime(value: str) -> datetime:
    """Parse ISO datetime string."""
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def default_orchestrator_config() -> SystemOrchestratorConfig:
    """Return default orchestrator configuration."""
    return SystemOrchestratorConfig()


def config_fingerprint(config: SystemOrchestratorConfig) -> str:
    """Compute deterministic configuration fingerprint."""
    payload = {
        "execution_mode": config.execution_mode.value,
        "account_id": config.account_id,
        "enable_pre_trade_cycle": config.enable_pre_trade_cycle,
        "enable_post_fill_cycle": config.enable_post_fill_cycle,
        "serial_cycle_execution": config.serial_cycle_execution,
        "strict_correlation": config.strict_correlation,
        "deterministic_fingerprint": config.deterministic_fingerprint,
        "metadata": dict(sorted(config.metadata.items())),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def validate_orchestrator_config(config: SystemOrchestratorConfig) -> OrchestratorValidationResult:
    """Validate orchestrator configuration."""
    errors: list[OrchestratorErrorRecord] = []
    if config.cycle_timeout_seconds < 1:
        errors.append(
            OrchestratorErrorRecord(
                code=ERROR_CONFIG_INVALID,
                message="cycle_timeout_seconds must be >= 1.",
                field="cycle_timeout_seconds",
            )
        )
    if config.shutdown_drain_timeout_seconds < 0:
        errors.append(
            OrchestratorErrorRecord(
                code=ERROR_CONFIG_INVALID,
                message="shutdown_drain_timeout_seconds must be >= 0.",
                field="shutdown_drain_timeout_seconds",
            )
        )
    if config.stale_snapshot_max_age_seconds < 0:
        errors.append(
            OrchestratorErrorRecord(
                code=ERROR_CONFIG_INVALID,
                message="stale_snapshot_max_age_seconds must be >= 0.",
                field="stale_snapshot_max_age_seconds",
            )
        )
    return OrchestratorValidationResult(errors=tuple(errors))


def map_portfolio_snapshot_for_risk(
    portfolio_snapshot: PortfolioManagerSnapshot,
    *,
    account_equity: float,
    account_cash: float,
) -> RiskPortfolioSnapshot:
    """Map Portfolio Manager output to Risk Engine input contract.

    Explicit orchestrator responsibility — neither Portfolio Manager nor
    Risk Engine performs this mapping.

    Args:
        portfolio_snapshot: Latest portfolio manager snapshot.
        account_equity: Authoritative account equity hint.
        account_cash: Authoritative cash available hint.

    Returns:
        Immutable risk engine portfolio snapshot.
    """
    metrics = portfolio_snapshot.metrics
    exposure = portfolio_snapshot.exposure
    open_positions = tuple(
        PortfolioPosition(
            position_id=position.position_id,
            underlying=position.underlying,
            notional_exposure=position.notional_exposure,
            unrealized_pnl=position.unrealized_pnl,
            opened_at=position.opened_at or portfolio_snapshot.as_of,
            strategy_id=position.strategy_id,
            strategy_family=position.strategy_family,
            direction=position.side,
            margin_at_risk_hint=None,
            expires_at=None,
            metadata=position.metadata,
        )
        for position in portfolio_snapshot.positions
    )
    exposure_summary = PortfolioExposureSummary(
        gross_notional=exposure.gross_notional,
        net_notional_by_underlying=exposure.net_notional_by_underlying,
        gross_notional_by_underlying=exposure.gross_notional_by_underlying,
        exposure_by_family=exposure.exposure_by_strategy_family,
        open_position_count=exposure.open_position_count,
        open_position_count_by_underlying=exposure.open_position_count_by_underlying,
    )
    peak_equity = metrics.peak_equity_hint or account_equity
    consecutive_losses = 0
    return RiskPortfolioSnapshot(
        snapshot_id=portfolio_snapshot.snapshot_id,
        correlation_id=portfolio_snapshot.correlation_id,
        as_of=portfolio_snapshot.as_of,
        account_id=portfolio_snapshot.account_id,
        equity=account_equity,
        cash_available=account_cash,
        daily_realized_pnl=metrics.total_realized_pnl_session,
        daily_unrealized_pnl=metrics.total_unrealized_pnl,
        peak_equity=peak_equity,
        consecutive_losses=consecutive_losses,
        open_positions=open_positions,
        exposure_summary=exposure_summary,
        portfolio_fingerprint=portfolio_snapshot.snapshot_fingerprint,
        margin_used_hint=metrics.margin_used_hint,
        margin_available_hint=metrics.margin_available_hint,
        metadata=MappingProxyType({"source": PRODUCER_NAME}),
    )


def compute_cycle_fingerprint(
    context: TradingCycleContext,
    result: TradingCycleResult,
    config: SystemOrchestratorConfig,
) -> str:
    """Compute SHA-256 fingerprint over canonical cycle outcomes."""
    payload: dict[str, object] = {
        "correlation_id": context.correlation_id,
        "trigger": context.trigger.value,
        "status": result.status.value,
        "risk_verdict": result.risk_verdict,
        "execution_plan_id": result.execution_plan_id,
        "config_hash": config_fingerprint(config),
    }
    if not config.deterministic_fingerprint:
        payload["submitted_at"] = _isoformat_utc(result.submitted_at)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _compute_post_fill_fingerprint(
    context: PostFillCycleContext,
    result: PostFillCycleResult,
    config: SystemOrchestratorConfig,
) -> str:
    """Compute fingerprint for post-fill cycle."""
    payload: dict[str, object] = {
        "correlation_id": context.correlation_id,
        "status": result.status.value,
        "position_update_id": result.position_update_id,
        "portfolio_update_id": result.portfolio_update_id,
        "apme_report_id": result.apme_report_id,
        "config_hash": config_fingerprint(config),
    }
    if not config.deterministic_fingerprint:
        payload["submitted_at"] = _isoformat_utc(result.submitted_at)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def validate_trading_cycle_result(result: TradingCycleResult) -> OrchestratorValidationResult:
    """Validate sealed trading cycle result."""
    errors: list[OrchestratorErrorRecord] = []
    if not result.cycle_id.strip():
        errors.append(
            OrchestratorErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="cycle_id must be non-empty.",
                field="cycle_id",
            )
        )
    if not result.correlation_id.strip():
        errors.append(
            OrchestratorErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="correlation_id must be non-empty.",
                field="correlation_id",
            )
        )
    if not _is_timezone_aware(result.submitted_at):
        errors.append(
            OrchestratorErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="submitted_at must be timezone-aware.",
                field="submitted_at",
            )
        )
    return OrchestratorValidationResult(errors=tuple(errors))


def _serialize_stage(stage: CycleStageResult) -> dict[str, object]:
    return {
        "stage_id": stage.stage_id,
        "engine_id": stage.engine_id.value if stage.engine_id else None,
        "passed": stage.passed,
        "rejection_code": stage.rejection_code,
        "message": stage.message,
        "duration_ms": stage.duration_ms,
        "details": dict(sorted(stage.details.items())),
    }


def serialize_trading_cycle_result(result: TradingCycleResult) -> str:
    """Serialize trading cycle result to JSON."""
    payload = {
        "schema_version": ORCHESTRATOR_SCHEMA_VERSION,
        "cycle_id": result.cycle_id,
        "correlation_id": result.correlation_id,
        "status": result.status.value,
        "trigger": result.trigger.value,
        "stages": [_serialize_stage(stage) for stage in result.stages],
        "market_snapshot_id": result.market_snapshot_id,
        "strategy_result_id": result.strategy_result_id,
        "decision_result_id": result.decision_result_id,
        "risk_verdict": result.risk_verdict,
        "execution_plan_id": result.execution_plan_id,
        "order_submission_id": result.order_submission_id,
        "warnings": [
            {"code": w.code, "message": w.message, "stage_id": w.stage_id}
            for w in result.warnings
        ],
        "errors": [
            {"code": e.code, "message": e.message, "stage_id": e.stage_id}
            for e in result.errors
        ],
        "primary_error_code": result.primary_error_code,
        "submitted_at": _isoformat_utc(result.submitted_at),
        "completed_at": _isoformat_utc(result.completed_at) if result.completed_at else None,
        "duration_ms": result.duration_ms,
        "cycle_fingerprint": result.cycle_fingerprint,
    }
    return json.dumps(payload, sort_keys=True)


def deserialize_trading_cycle_result(payload: str) -> TradingCycleResult:
    """Deserialize trading cycle result from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OrchestratorValidationError(
            f"Malformed JSON: {exc}",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if data.get("schema_version") != ORCHESTRATOR_SCHEMA_VERSION:
        raise OrchestratorValidationError(
            "Unsupported schema version.",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
        )
    stages = tuple(
        CycleStageResult(
            stage_id=str(item["stage_id"]),
            engine_id=EngineId(item["engine_id"]) if item.get("engine_id") else None,
            passed=bool(item["passed"]),
            rejection_code=item.get("rejection_code"),
            message=item.get("message"),
            duration_ms=float(item["duration_ms"]),
            details=MappingProxyType(dict(item.get("details") or {})),
        )
        for item in data["stages"]
    )
    return TradingCycleResult(
        cycle_id=str(data["cycle_id"]),
        correlation_id=str(data["correlation_id"]),
        status=CycleStatus(data["status"]),
        trigger=CycleTrigger(data["trigger"]),
        stages=stages,
        market_snapshot_id=data.get("market_snapshot_id"),
        strategy_result_id=data.get("strategy_result_id"),
        decision_result_id=data.get("decision_result_id"),
        risk_verdict=data.get("risk_verdict"),
        execution_plan_id=data.get("execution_plan_id"),
        order_submission_id=data.get("order_submission_id"),
        warnings=tuple(
            OrchestratorWarningRecord(code=w["code"], message=w["message"], stage_id=w.get("stage_id"))
            for w in data.get("warnings", [])
        ),
        errors=tuple(
            OrchestratorErrorRecord(code=e["code"], message=e["message"], stage_id=e.get("stage_id"))
            for e in data.get("errors", [])
        ),
        primary_error_code=data.get("primary_error_code"),
        submitted_at=_parse_iso_datetime(data["submitted_at"]),
        completed_at=_parse_iso_datetime(data["completed_at"]) if data.get("completed_at") else None,
        duration_ms=float(data["duration_ms"]),
        cycle_fingerprint=str(data["cycle_fingerprint"]),
    )


def serialize_system_health_report(report: SystemHealthReport) -> str:
    """Serialize system health report to JSON."""
    payload = {
        "schema_version": ORCHESTRATOR_SCHEMA_VERSION,
        "report_id": report.report_id,
        "as_of": _isoformat_utc(report.as_of),
        "orchestrator_state": report.orchestrator_state.value,
        "overall_status": report.overall_status.value,
        "stale_snapshot": report.stale_snapshot,
        "last_cycle_at": _isoformat_utc(report.last_cycle_at) if report.last_cycle_at else None,
        "last_cycle_status": report.last_cycle_status.value if report.last_cycle_status else None,
        "event_bus_metrics": {
            "publish_count": report.event_bus_metrics.publish_count,
            "delivery_count": report.event_bus_metrics.delivery_count,
            "subscriber_failure_count": report.event_bus_metrics.subscriber_failure_count,
            "active_subscriptions": report.event_bus_metrics.active_subscriptions,
        },
        "issues": [
            {
                "issue_code": issue.issue_code,
                "severity": issue.severity,
                "message": issue.message,
                "engine_id": issue.engine_id.value if issue.engine_id else None,
            }
            for issue in report.issues
        ],
    }
    return json.dumps(payload, sort_keys=True)


def deserialize_system_health_report(payload: str) -> SystemHealthReport:
    """Deserialize system health report from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OrchestratorValidationError(
            f"Malformed JSON: {exc}",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if data.get("schema_version") != ORCHESTRATOR_SCHEMA_VERSION:
        raise OrchestratorValidationError(
            "Unsupported schema version.",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
        )
    metrics = data["event_bus_metrics"]
    return SystemHealthReport(
        report_id=str(data["report_id"]),
        as_of=_parse_iso_datetime(data["as_of"]),
        orchestrator_state=OrchestratorState(data["orchestrator_state"]),
        engine_health=MappingProxyType({}),
        event_bus_metrics=EventBusHealthMetrics(
            publish_count=int(metrics["publish_count"]),
            delivery_count=int(metrics["delivery_count"]),
            subscriber_failure_count=int(metrics["subscriber_failure_count"]),
            active_subscriptions=int(metrics["active_subscriptions"]),
        ),
        last_cycle_at=_parse_iso_datetime(data["last_cycle_at"]) if data.get("last_cycle_at") else None,
        last_cycle_status=CycleStatus(data["last_cycle_status"]) if data.get("last_cycle_status") else None,
        stale_snapshot=bool(data["stale_snapshot"]),
        issues=tuple(
            HealthIssueRecord(
                issue_code=item["issue_code"],
                severity=item["severity"],
                message=item["message"],
                engine_id=EngineId(item["engine_id"]) if item.get("engine_id") else None,
            )
            for item in data.get("issues", [])
        ),
        overall_status=HealthStatus(data["overall_status"]),
    )


def _stage_result(
    stage_id: str,
    *,
    engine_id: EngineId | None,
    passed: bool,
    rejection_code: str | None = None,
    message: str | None = None,
    duration_ms: float = 0.0,
    details: Mapping[str, str] | None = None,
) -> CycleStageResult:
    """Build a cycle stage audit record."""
    return CycleStageResult(
        stage_id=stage_id,
        engine_id=engine_id,
        passed=passed,
        rejection_code=rejection_code,
        message=message,
        duration_ms=duration_ms,
        details=MappingProxyType(dict(details or {})),
    )


def _abstain_statuses() -> frozenset[DecisionStatus]:
    return frozenset(
        {
            DecisionStatus.ABSTAIN,
            DecisionStatus.WINDOW_CLOSED,
            DecisionStatus.NO_CANDIDATES,
        }
    )


def _resolve_registry_snapshot(
    context: TradingCycleContext,
    strategy_engine: object | None,
) -> RegistrySnapshot | None:
    """Resolve strategy registry snapshot from context or engine."""
    if context.registry_snapshot is not None:
        return context.registry_snapshot
    if strategy_engine is not None and hasattr(strategy_engine, "registry"):
        registry = getattr(strategy_engine, "registry")
        if hasattr(registry, "snapshot"):
            return registry.snapshot()
    return None


def _build_portfolio_ingest_context(context: PostFillCycleContext) -> PortfolioIngestContext:
    """Assemble portfolio ingest context from post-fill cycle context."""
    return PortfolioIngestContext(
        correlation_id=context.correlation_id,
        reference_time=context.reference_time,
        execution_mode=context.execution_mode,
        account_id=context.account_id,
        equity_hint=context.equity_hint,
        cash_available_hint=context.cash_available_hint,
        margin_used_hint=context.margin_used_hint,
        margin_available_hint=context.margin_available_hint,
        greek_hints=context.greek_hints,
        price_hints=context.price_hints,
        tags=context.tags,
    )


def _build_apme_evaluation_context(
    context: PostFillCycleContext,
    *,
    portfolio_snapshot_id: str,
) -> APMEEvaluationContext:
    """Assemble APME evaluation context from post-fill cycle context."""
    return APMEEvaluationContext(
        correlation_id=context.correlation_id,
        reference_time=context.reference_time,
        execution_mode=context.execution_mode,
        account_id=context.account_id,
        portfolio_snapshot_id=portfolio_snapshot_id,
        price_hints=context.price_hints,
        greek_hints=context.greek_hints,
        volatility_hints=context.volatility_hints,
        regime_hints=context.regime_hints,
        trend_hints=context.trend_hints,
        news_flags=context.news_flags,
        signal_metadata=context.signal_metadata,
        tags=context.tags,
    )


class CycleExecutor:
    """Executes pre-trade and post-fill pipeline stages."""

    def __init__(
        self,
        config: SystemOrchestratorConfig,
        registry: EngineRegistry,
        *,
        broker_client: object | None = None,
        clock: Callable[[], datetime] | None = None,
        record_engine_failure: Callable[[EngineId, Exception, str], None] | None = None,
        invoke_engine: Callable[[EngineId, Callable[[], T], str], T | None] | None = None,
    ) -> None:
        """Initialize cycle executor."""
        self._config = config
        self._registry = registry
        self._broker_client = broker_client
        self._clock = clock or _utc_now
        self._record_engine_failure = record_engine_failure
        self._invoke_engine = invoke_engine

    def execute_pre_trade(self, context: TradingCycleContext) -> TradingCycleResult:
        """Execute pre-trade entry pipeline."""
        cycle_id = str(uuid.uuid4())
        submitted_at = self._clock()
        start_perf = time.perf_counter()
        stages: list[CycleStageResult] = []
        warnings: list[OrchestratorWarningRecord] = []
        errors: list[OrchestratorErrorRecord] = []

        market_snapshot_id: str | None = None
        strategy_result_id: str | None = None
        decision_result_id: str | None = None
        risk_verdict: str | None = None
        execution_plan_id: str | None = None
        order_submission_id: str | None = None
        status = CycleStatus.COMPLETED
        short_circuit = False

        # Stage 1: INPUT_GATE
        stage_start = time.perf_counter()
        if not _is_timezone_aware(context.reference_time):
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.INPUT_GATE.value,
                    engine_id=None,
                    passed=False,
                    rejection_code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
                    message="reference_time must be timezone-aware.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._finalize_pre_trade(
                cycle_id=cycle_id,
                context=context,
                status=CycleStatus.REJECTED,
                stages=stages,
                warnings=warnings,
                errors=[
                    OrchestratorErrorRecord(
                        code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
                        message="reference_time must be timezone-aware.",
                        stage_id=PreTradeCycleStageId.INPUT_GATE.value,
                    )
                ],
                submitted_at=submitted_at,
                start_perf=start_perf,
                market_snapshot_id=market_snapshot_id,
                strategy_result_id=strategy_result_id,
                decision_result_id=decision_result_id,
                risk_verdict=risk_verdict,
                execution_plan_id=execution_plan_id,
                order_submission_id=order_submission_id,
            )

        if self._config.strict_correlation and not context.correlation_id.strip():
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.INPUT_GATE.value,
                    engine_id=None,
                    passed=False,
                    rejection_code=ERROR_CONTEXT_CORRELATION_MISMATCH,
                    message="correlation_id must be non-empty.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._finalize_pre_trade(
                cycle_id=cycle_id,
                context=context,
                status=CycleStatus.REJECTED,
                stages=stages,
                warnings=warnings,
                errors=[
                    OrchestratorErrorRecord(
                        code=ERROR_CONTEXT_CORRELATION_MISMATCH,
                        message="correlation_id must be non-empty.",
                        stage_id=PreTradeCycleStageId.INPUT_GATE.value,
                    )
                ],
                submitted_at=submitted_at,
                start_perf=start_perf,
                market_snapshot_id=market_snapshot_id,
                strategy_result_id=strategy_result_id,
                decision_result_id=decision_result_id,
                risk_verdict=risk_verdict,
                execution_plan_id=execution_plan_id,
                order_submission_id=order_submission_id,
            )

        if not self._config.enable_pre_trade_cycle:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.INPUT_GATE.value,
                    engine_id=None,
                    passed=True,
                    message="Pre-trade cycle disabled.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._finalize_pre_trade(
                cycle_id=cycle_id,
                context=context,
                status=CycleStatus.SKIPPED,
                stages=stages,
                warnings=warnings,
                errors=errors,
                submitted_at=submitted_at,
                start_perf=start_perf,
                market_snapshot_id=market_snapshot_id,
                strategy_result_id=strategy_result_id,
                decision_result_id=decision_result_id,
                risk_verdict=risk_verdict,
                execution_plan_id=execution_plan_id,
                order_submission_id=order_submission_id,
            )

        stages.append(
            _stage_result(
                PreTradeCycleStageId.INPUT_GATE.value,
                engine_id=None,
                passed=True,
                duration_ms=(time.perf_counter() - stage_start) * 1000.0,
            )
        )

        # Stage 2: MARKET_DATA_REFRESH
        stage_start = time.perf_counter()
        market_snapshot = context.market_snapshot
        if market_snapshot is None:
            market_engine = self._registry.market_data
            if market_engine is None:
                stages.append(
                    _stage_result(
                        PreTradeCycleStageId.MARKET_DATA_REFRESH.value,
                        engine_id=EngineId.MARKET_DATA,
                        passed=False,
                        rejection_code=ERROR_ENGINE_NOT_READY,
                        message="Market data engine not available.",
                        duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                    )
                )
                return self._failed_pre_trade(
                    cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                    ERROR_ENGINE_NOT_READY, "Market data engine not available.",
                    PreTradeCycleStageId.MARKET_DATA_REFRESH.value,
                )
            publish_fn = getattr(market_engine, "publish_snapshot", None)
            if publish_fn is None:
                stages.append(
                    _stage_result(
                        PreTradeCycleStageId.MARKET_DATA_REFRESH.value,
                        engine_id=EngineId.MARKET_DATA,
                        passed=False,
                        rejection_code=ERROR_ENGINE_FAILURE,
                        message="Market data engine missing publish_snapshot.",
                        duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                    )
                )
                return self._failed_pre_trade(
                    cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                    ERROR_ENGINE_FAILURE, "Market data engine missing publish_snapshot.",
                    PreTradeCycleStageId.MARKET_DATA_REFRESH.value,
                )
            try:
                publish_event = publish_fn(
                    correlation_id=context.correlation_id,
                    as_of=context.reference_time,
                )
                market_snapshot = publish_event.snapshot
            except Exception as exc:
                if self._record_engine_failure:
                    self._record_engine_failure(EngineId.MARKET_DATA, exc, cycle_id)
                stages.append(
                    _stage_result(
                        PreTradeCycleStageId.MARKET_DATA_REFRESH.value,
                        engine_id=EngineId.MARKET_DATA,
                        passed=False,
                        rejection_code=ERROR_ENGINE_FAILURE,
                        message=str(exc),
                        duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                    )
                )
                return self._failed_pre_trade(
                    cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                    ERROR_ENGINE_FAILURE, str(exc), PreTradeCycleStageId.MARKET_DATA_REFRESH.value,
                )

        if market_snapshot is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.MARKET_DATA_REFRESH.value,
                    engine_id=EngineId.MARKET_DATA,
                    passed=False,
                    rejection_code=ERROR_SNAPSHOT_STALE,
                    message="No market snapshot available.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_SNAPSHOT_STALE, "No market snapshot available.",
                PreTradeCycleStageId.MARKET_DATA_REFRESH.value,
            )

        snapshot_age = (
            context.reference_time - market_snapshot.provenance.as_of
        ).total_seconds()
        if (
            context.execution_mode is StrategyExecutionMode.LIVE
            and snapshot_age > self._config.stale_snapshot_max_age_seconds
        ):
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.MARKET_DATA_REFRESH.value,
                    engine_id=EngineId.MARKET_DATA,
                    passed=False,
                    rejection_code=ERROR_SNAPSHOT_STALE,
                    message="Market snapshot is stale.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_SNAPSHOT_STALE, "Market snapshot is stale.",
                PreTradeCycleStageId.MARKET_DATA_REFRESH.value,
            )

        market_snapshot_id = market_snapshot.provenance.snapshot_id
        stages.append(
            _stage_result(
                PreTradeCycleStageId.MARKET_DATA_REFRESH.value,
                engine_id=EngineId.MARKET_DATA,
                passed=True,
                duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                details={"snapshot_id": market_snapshot_id},
            )
        )

        if short_circuit:
            pass  # pragma: no cover

        # Stage 3: STRATEGY_EVALUATION
        stage_start = time.perf_counter()
        strategy_engine = self._registry.strategy_evaluation
        bundle: StrategyEvaluationBundle | None = None
        if strategy_engine is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.STRATEGY_EVALUATION.value,
                    engine_id=EngineId.STRATEGY_EVALUATION,
                    passed=False,
                    rejection_code=ERROR_ENGINE_NOT_READY,
                    message="Strategy evaluation engine not available.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_ENGINE_NOT_READY, "Strategy evaluation engine not available.",
                PreTradeCycleStageId.STRATEGY_EVALUATION.value,
            )

        registry_snapshot = _resolve_registry_snapshot(context, strategy_engine)
        if registry_snapshot is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.STRATEGY_EVALUATION.value,
                    engine_id=EngineId.STRATEGY_EVALUATION,
                    passed=False,
                    rejection_code=ERROR_CONTEXT_INVALID,
                    message="Registry snapshot unavailable.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_CONTEXT_INVALID, "Registry snapshot unavailable.",
                PreTradeCycleStageId.STRATEGY_EVALUATION.value,
            )

        eval_context = EvaluationRunContext(
            correlation_id=context.correlation_id,
            as_of=context.reference_time,
            snapshot=market_snapshot,
            registry_snapshot=registry_snapshot,
            execution_mode=context.execution_mode,
            reference_time=context.reference_time,
            tags=context.tags,
        )
        engine_context = EngineContext(
            correlation_id=context.correlation_id,
            as_of=context.reference_time,
            payload=eval_context,
            tags=context.tags,
            source=PRODUCER_NAME,
        )

        def _run_strategy() -> EngineResult:
            run_fn = getattr(strategy_engine, "run", None)
            if run_fn is not None:
                return run_fn(engine_context)
            evaluate_fn = getattr(strategy_engine, "evaluate", None)
            if evaluate_fn is not None:
                return evaluate_fn(engine_context)
            raise RuntimeError("Strategy engine missing run/evaluate.")

        strategy_result = self._call_engine(EngineId.STRATEGY_EVALUATION, _run_strategy, cycle_id)
        if strategy_result is None or strategy_result.payload is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.STRATEGY_EVALUATION.value,
                    engine_id=EngineId.STRATEGY_EVALUATION,
                    passed=False,
                    rejection_code=ERROR_ENGINE_FAILURE,
                    message="Strategy evaluation failed.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_ENGINE_FAILURE, "Strategy evaluation failed.",
                PreTradeCycleStageId.STRATEGY_EVALUATION.value,
            )

        bundle = strategy_result.payload
        if bundle is not None and hasattr(bundle, "bundle_id"):
            strategy_result_id = str(bundle.bundle_id)
        stages.append(
            _stage_result(
                PreTradeCycleStageId.STRATEGY_EVALUATION.value,
                engine_id=EngineId.STRATEGY_EVALUATION,
                passed=True,
                duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                details={"bundle_id": strategy_result_id or ""},
            )
        )

        # Stage 4: TRADE_DECISION
        stage_start = time.perf_counter()
        decision_engine = self._registry.trade_decision
        trade_decision: TradeDecisionResult | None = None
        if decision_engine is None or bundle is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.TRADE_DECISION.value,
                    engine_id=EngineId.TRADE_DECISION,
                    passed=False,
                    rejection_code=ERROR_ENGINE_NOT_READY,
                    message="Trade decision engine not available.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_ENGINE_NOT_READY, "Trade decision engine not available.",
                PreTradeCycleStageId.TRADE_DECISION.value,
            )

        decision_context = DecisionRunContext(
            correlation_id=context.correlation_id,
            as_of=context.reference_time,
            bundle=bundle,
            mode=context.decision_mode,
            preferences=context.user_preferences or default_user_preferences(),
            execution_mode=context.execution_mode,
            reference_time=context.reference_time,
            snapshot=market_snapshot,
            tags=context.tags,
        )
        decision_engine_context = EngineContext(
            correlation_id=context.correlation_id,
            as_of=context.reference_time,
            payload=decision_context,
            tags=context.tags,
            source=PRODUCER_NAME,
        )

        def _run_decision() -> EngineResult:
            run_fn = getattr(decision_engine, "run", None)
            if run_fn is not None:
                return run_fn(decision_engine_context)
            decide_fn = getattr(decision_engine, "decide", None)
            if decide_fn is not None:
                result = decide_fn(decision_context)
                return EngineResult(
                    status=EngineStatus.SUCCESS,
                    metadata=None,  # type: ignore[arg-type]
                    payload=result,
                )
            raise RuntimeError("Trade decision engine missing run/decide.")

        decision_engine_result = self._call_engine(EngineId.TRADE_DECISION, _run_decision, cycle_id)
        if decision_engine_result is None or decision_engine_result.payload is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.TRADE_DECISION.value,
                    engine_id=EngineId.TRADE_DECISION,
                    passed=False,
                    rejection_code=ERROR_ENGINE_FAILURE,
                    message="Trade decision failed.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_ENGINE_FAILURE, "Trade decision failed.",
                PreTradeCycleStageId.TRADE_DECISION.value,
            )

        payload = decision_engine_result.payload
        if payload is not None and hasattr(payload, "decision_status"):
            trade_decision = payload  # type: ignore[assignment]
            decision_result_id = str(getattr(payload, "decision_id", "") or "")

        stages.append(
            _stage_result(
                PreTradeCycleStageId.TRADE_DECISION.value,
                engine_id=EngineId.TRADE_DECISION,
                passed=True,
                duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                details={"decision_id": decision_result_id or ""},
            )
        )

        if trade_decision and trade_decision.decision_status in _abstain_statuses():
            for skipped in (
                PreTradeCycleStageId.PORTFOLIO_RISK_MAPPING,
                PreTradeCycleStageId.RISK_REVIEW,
                PreTradeCycleStageId.EXECUTION_PLANNING,
                PreTradeCycleStageId.ORDER_SUBMISSION,
            ):
                stages.append(
                    _stage_result(
                        skipped.value,
                        engine_id=None,
                        passed=True,
                        message="Skipped due to abstain decision.",
                        duration_ms=0.0,
                    )
                )
            return self._finalize_pre_trade(
                cycle_id=cycle_id,
                context=context,
                status=CycleStatus.COMPLETED,
                stages=stages,
                warnings=warnings,
                errors=errors,
                submitted_at=submitted_at,
                start_perf=start_perf,
                market_snapshot_id=market_snapshot_id,
                strategy_result_id=strategy_result_id,
                decision_result_id=decision_result_id,
                risk_verdict=risk_verdict,
                execution_plan_id=execution_plan_id,
                order_submission_id=order_submission_id,
            )

        # Stage 5: PORTFOLIO_RISK_MAPPING
        stage_start = time.perf_counter()
        portfolio_manager = self._registry.portfolio_manager
        if portfolio_manager is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.PORTFOLIO_RISK_MAPPING.value,
                    engine_id=EngineId.PORTFOLIO_MANAGER,
                    passed=False,
                    rejection_code=ERROR_ENGINE_NOT_READY,
                    message="Portfolio manager not available.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_ENGINE_NOT_READY, "Portfolio manager not available.",
                PreTradeCycleStageId.PORTFOLIO_RISK_MAPPING.value,
            )

        if trade_decision is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.PORTFOLIO_RISK_MAPPING.value,
                    engine_id=EngineId.PORTFOLIO_MANAGER,
                    passed=False,
                    rejection_code=ERROR_CONTEXT_INVALID,
                    message="Trade decision unavailable.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_CONTEXT_INVALID, "Trade decision unavailable.",
                PreTradeCycleStageId.PORTFOLIO_RISK_MAPPING.value,
            )

        get_snapshot = getattr(portfolio_manager, "get_snapshot", None)
        pm_snapshot = get_snapshot() if get_snapshot else None
        if pm_snapshot is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.PORTFOLIO_RISK_MAPPING.value,
                    engine_id=EngineId.PORTFOLIO_MANAGER,
                    passed=False,
                    rejection_code=ERROR_CONTEXT_INVALID,
                    message="Portfolio snapshot unavailable.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_CONTEXT_INVALID, "Portfolio snapshot unavailable.",
                PreTradeCycleStageId.PORTFOLIO_RISK_MAPPING.value,
            )

        equity = context.account_equity_hint if context.account_equity_hint is not None else pm_snapshot.metrics.equity_hint
        cash = context.account_cash_hint if context.account_cash_hint is not None else pm_snapshot.metrics.cash_available_hint
        risk_snapshot = map_portfolio_snapshot_for_risk(
            pm_snapshot,
            account_equity=equity,
            account_cash=cash,
        )
        stages.append(
            _stage_result(
                PreTradeCycleStageId.PORTFOLIO_RISK_MAPPING.value,
                engine_id=EngineId.PORTFOLIO_MANAGER,
                passed=True,
                duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                details={"snapshot_id": risk_snapshot.snapshot_id},
            )
        )

        # Stage 6: RISK_REVIEW
        stage_start = time.perf_counter()
        risk_engine = self._registry.risk
        risk_decision: RiskDecisionResult | None = None
        if risk_engine is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.RISK_REVIEW.value,
                    engine_id=EngineId.RISK,
                    passed=False,
                    rejection_code=ERROR_ENGINE_NOT_READY,
                    message="Risk engine not available.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_ENGINE_NOT_READY, "Risk engine not available.",
                PreTradeCycleStageId.RISK_REVIEW.value,
            )

        risk_context = RiskRunContext(
            correlation_id=context.correlation_id,
            as_of=context.reference_time,
            trade_decision=trade_decision,
            portfolio=risk_snapshot,
            user_risk_profile=context.user_risk_profile or default_user_risk_profile(
                profile_id=f"profile-{context.account_id or 'default'}"
            ),
            execution_mode=context.execution_mode,
            reference_time=context.reference_time,
            tags=context.tags,
        )

        def _run_risk() -> RiskDecisionResult:
            review_fn = getattr(risk_engine, "review", None)
            if review_fn is None:
                raise RuntimeError("Risk engine missing review.")
            return review_fn(risk_context)

        risk_decision = self._call_engine(EngineId.RISK, _run_risk, cycle_id)
        if risk_decision is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.RISK_REVIEW.value,
                    engine_id=EngineId.RISK,
                    passed=False,
                    rejection_code=ERROR_ENGINE_FAILURE,
                    message="Risk review failed.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_ENGINE_FAILURE, "Risk review failed.",
                PreTradeCycleStageId.RISK_REVIEW.value,
            )

        risk_verdict = risk_decision.verdict.value
        stages.append(
            _stage_result(
                PreTradeCycleStageId.RISK_REVIEW.value,
                engine_id=EngineId.RISK,
                passed=True,
                duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                details={"verdict": risk_verdict},
            )
        )

        if risk_decision.verdict is RiskVerdict.REJECTED:
            for skipped in (
                PreTradeCycleStageId.EXECUTION_PLANNING,
                PreTradeCycleStageId.ORDER_SUBMISSION,
            ):
                stages.append(
                    _stage_result(
                        skipped.value,
                        engine_id=None,
                        passed=True,
                        message="Skipped due to risk rejection.",
                        duration_ms=0.0,
                    )
                )
            return self._finalize_pre_trade(
                cycle_id=cycle_id,
                context=context,
                status=CycleStatus.PARTIAL,
                stages=stages,
                warnings=warnings,
                errors=errors,
                submitted_at=submitted_at,
                start_perf=start_perf,
                market_snapshot_id=market_snapshot_id,
                strategy_result_id=strategy_result_id,
                decision_result_id=decision_result_id,
                risk_verdict=risk_verdict,
                execution_plan_id=execution_plan_id,
                order_submission_id=order_submission_id,
            )

        # Stage 7: EXECUTION_PLANNING
        stage_start = time.perf_counter()
        execution_engine = self._registry.execution
        if execution_engine is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.EXECUTION_PLANNING.value,
                    engine_id=EngineId.EXECUTION,
                    passed=False,
                    rejection_code=ERROR_ENGINE_NOT_READY,
                    message="Execution engine not available.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_ENGINE_NOT_READY, "Execution engine not available.",
                PreTradeCycleStageId.EXECUTION_PLANNING.value,
            )

        exec_run_context = ExecutionRunContext(
            correlation_id=context.correlation_id,
            as_of=context.reference_time,
            risk_decision=risk_decision,
            market_snapshot=market_snapshot,
            execution_mode=context.execution_mode,
            reference_time=context.reference_time,
            tags=context.tags,
        )
        exec_engine_context = EngineContext(
            correlation_id=context.correlation_id,
            as_of=context.reference_time,
            payload=exec_run_context,
            tags=context.tags,
            source=PRODUCER_NAME,
        )

        def _run_execution() -> EngineResult | ExecutionPlan:
            plan_fn = getattr(execution_engine, "plan_from_run_context", None)
            if plan_fn is not None:
                return plan_fn(exec_run_context)
            run_fn = getattr(execution_engine, "plan", None) or getattr(execution_engine, "run", None)
            if run_fn is not None:
                result = run_fn(exec_engine_context)
                if isinstance(result, EngineResult):
                    return result.payload if isinstance(result.payload, ExecutionPlan) else result
                return result
            raise RuntimeError("Execution engine missing plan.")

        exec_result = self._call_engine(EngineId.EXECUTION, _run_execution, cycle_id)
        if exec_result is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.EXECUTION_PLANNING.value,
                    engine_id=EngineId.EXECUTION,
                    passed=False,
                    rejection_code=ERROR_ENGINE_FAILURE,
                    message="Execution planning failed.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_ENGINE_FAILURE, "Execution planning failed.",
                PreTradeCycleStageId.EXECUTION_PLANNING.value,
            )

        execution_plan: ExecutionPlan | object | None = None
        if isinstance(exec_result, EngineResult):
            execution_plan = exec_result.payload
        elif hasattr(exec_result, "plan_id"):
            execution_plan = exec_result

        if execution_plan is None or not hasattr(execution_plan, "plan_id"):
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.EXECUTION_PLANNING.value,
                    engine_id=EngineId.EXECUTION,
                    passed=False,
                    rejection_code=ERROR_ENGINE_FAILURE,
                    message="Execution plan missing from result.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_ENGINE_FAILURE, "Execution plan missing from result.",
                PreTradeCycleStageId.EXECUTION_PLANNING.value,
            )

        execution_plan_id = str(execution_plan.plan_id)
        stages.append(
            _stage_result(
                PreTradeCycleStageId.EXECUTION_PLANNING.value,
                engine_id=EngineId.EXECUTION,
                passed=True,
                duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                details={"plan_id": execution_plan_id},
            )
        )

        if execution_plan.status in (ExecutionPlanStatus.NO_PLAN, ExecutionPlanStatus.SKIPPED):
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.ORDER_SUBMISSION.value,
                    engine_id=EngineId.ORDER_MANAGER,
                    passed=True,
                    message="Skipped due to no execution plan.",
                    duration_ms=0.0,
                )
            )
            return self._finalize_pre_trade(
                cycle_id=cycle_id,
                context=context,
                status=CycleStatus.COMPLETED,
                stages=stages,
                warnings=warnings,
                errors=errors,
                submitted_at=submitted_at,
                start_perf=start_perf,
                market_snapshot_id=market_snapshot_id,
                strategy_result_id=strategy_result_id,
                decision_result_id=decision_result_id,
                risk_verdict=risk_verdict,
                execution_plan_id=execution_plan_id,
                order_submission_id=order_submission_id,
            )

        # Stage 8: ORDER_SUBMISSION
        stage_start = time.perf_counter()
        order_manager = self._registry.order_manager
        if order_manager is None or self._broker_client is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.ORDER_SUBMISSION.value,
                    engine_id=EngineId.ORDER_MANAGER,
                    passed=False,
                    rejection_code=ERROR_ENGINE_NOT_READY,
                    message="Order manager or broker client not available.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_ENGINE_NOT_READY, "Order manager or broker client not available.",
                PreTradeCycleStageId.ORDER_SUBMISSION.value,
            )

        submission_context = OrderSubmissionContext(
            correlation_id=context.correlation_id,
            reference_time=context.reference_time,
            execution_mode=context.execution_mode,
            tags=context.tags,
        )

        def _run_order() -> OrderSubmissionResult:
            submit_fn = getattr(order_manager, "submit_plan", None)
            if submit_fn is None:
                raise RuntimeError("Order manager missing submit_plan.")
            return submit_fn(execution_plan, self._broker_client, submission_context)

        order_result = self._call_engine(EngineId.ORDER_MANAGER, _run_order, cycle_id)
        if order_result is None:
            stages.append(
                _stage_result(
                    PreTradeCycleStageId.ORDER_SUBMISSION.value,
                    engine_id=EngineId.ORDER_MANAGER,
                    passed=False,
                    rejection_code=ERROR_ENGINE_FAILURE,
                    message="Order submission failed.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._failed_pre_trade(
                cycle_id, context, stages, warnings, errors, submitted_at, start_perf,
                ERROR_ENGINE_FAILURE, "Order submission failed.",
                PreTradeCycleStageId.ORDER_SUBMISSION.value,
            )

        order_submission_id = order_result.submission_id
        stages.append(
            _stage_result(
                PreTradeCycleStageId.ORDER_SUBMISSION.value,
                engine_id=EngineId.ORDER_MANAGER,
                passed=True,
                duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                details={"submission_id": order_submission_id},
            )
        )

        return self._finalize_pre_trade(
            cycle_id=cycle_id,
            context=context,
            status=status,
            stages=stages,
            warnings=warnings,
            errors=errors,
            submitted_at=submitted_at,
            start_perf=start_perf,
            market_snapshot_id=market_snapshot_id,
            strategy_result_id=strategy_result_id,
            decision_result_id=decision_result_id,
            risk_verdict=risk_verdict,
            execution_plan_id=execution_plan_id,
            order_submission_id=order_submission_id,
        )

    def execute_post_fill(self, context: PostFillCycleContext) -> PostFillCycleResult:
        """Execute post-fill position → portfolio → APME pipeline."""
        cycle_id = str(uuid.uuid4())
        submitted_at = self._clock()
        start_perf = time.perf_counter()
        stages: list[CycleStageResult] = []
        warnings: list[OrchestratorWarningRecord] = []
        errors: list[OrchestratorErrorRecord] = []

        position_update_id: str | None = None
        portfolio_update_id: str | None = None
        apme_report_id: str | None = None

        # INPUT_GATE
        stage_start = time.perf_counter()
        if context.order_tracker is None:
            stages.append(
                _stage_result(
                    PostFillCycleStageId.INPUT_GATE.value,
                    engine_id=None,
                    passed=False,
                    rejection_code=ERROR_CONTEXT_INVALID,
                    message="order_tracker must not be None.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._finalize_post_fill(
                cycle_id, context, CycleStatus.REJECTED, stages, warnings,
                [OrchestratorErrorRecord(code=ERROR_CONTEXT_INVALID, message="order_tracker required.")],
                submitted_at, start_perf, position_update_id, portfolio_update_id, apme_report_id,
            )

        if not self._config.enable_post_fill_cycle:
            stages.append(
                _stage_result(
                    PostFillCycleStageId.INPUT_GATE.value,
                    engine_id=None,
                    passed=True,
                    message="Post-fill cycle disabled.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._finalize_post_fill(
                cycle_id, context, CycleStatus.SKIPPED, stages, warnings, errors,
                submitted_at, start_perf, position_update_id, portfolio_update_id, apme_report_id,
            )

        if self._config.strict_correlation and not context.correlation_id.strip():
            stages.append(
                _stage_result(
                    PostFillCycleStageId.INPUT_GATE.value,
                    engine_id=None,
                    passed=False,
                    rejection_code=ERROR_CONTEXT_CORRELATION_MISMATCH,
                    message="correlation_id must be non-empty.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._finalize_post_fill(
                cycle_id, context, CycleStatus.REJECTED, stages, warnings,
                [OrchestratorErrorRecord(code=ERROR_CONTEXT_CORRELATION_MISMATCH, message="correlation_id required.")],
                submitted_at, start_perf, position_update_id, portfolio_update_id, apme_report_id,
            )

        if not _is_timezone_aware(context.reference_time):
            stages.append(
                _stage_result(
                    PostFillCycleStageId.INPUT_GATE.value,
                    engine_id=None,
                    passed=False,
                    rejection_code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
                    message="reference_time must be timezone-aware.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._finalize_post_fill(
                cycle_id, context, CycleStatus.REJECTED, stages, warnings,
                [OrchestratorErrorRecord(code=ERROR_CONTEXT_NAIVE_TIMESTAMP, message="naive timestamp.")],
                submitted_at, start_perf, position_update_id, portfolio_update_id, apme_report_id,
            )

        stages.append(
            _stage_result(
                PostFillCycleStageId.INPUT_GATE.value,
                engine_id=None,
                passed=True,
                duration_ms=(time.perf_counter() - stage_start) * 1000.0,
            )
        )

        # POSITION_UPDATE
        stage_start = time.perf_counter()
        position_manager = self._registry.position_manager
        if position_manager is None:
            stages.append(
                _stage_result(
                    PostFillCycleStageId.POSITION_UPDATE.value,
                    engine_id=EngineId.POSITION_MANAGER,
                    passed=False,
                    rejection_code=ERROR_ENGINE_NOT_READY,
                    message="Position manager not available.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._finalize_post_fill(
                cycle_id, context, CycleStatus.FAILED, stages, warnings,
                [OrchestratorErrorRecord(code=ERROR_ENGINE_NOT_READY, message="Position manager missing.")],
                submitted_at, start_perf, position_update_id, portfolio_update_id, apme_report_id,
            )

        position_context = PositionUpdateContext(
            correlation_id=context.correlation_id,
            reference_time=context.reference_time,
            execution_mode=context.execution_mode,
            price_hints=context.price_hints,
            tags=context.tags,
            account_id=context.account_id,
        )

        def _run_position() -> PositionUpdateResult:
            apply_fn = getattr(position_manager, "apply_order_tracker", None)
            if apply_fn is None:
                raise RuntimeError("Position manager missing apply_order_tracker.")
            return apply_fn(context.order_tracker, position_context)

        position_result = self._call_engine(EngineId.POSITION_MANAGER, _run_position, cycle_id)
        if position_result is None or position_result.status is PositionUpdateStatus.REJECTED:
            stages.append(
                _stage_result(
                    PostFillCycleStageId.POSITION_UPDATE.value,
                    engine_id=EngineId.POSITION_MANAGER,
                    passed=False,
                    rejection_code=ERROR_ENGINE_FAILURE,
                    message="Position update rejected.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._finalize_post_fill(
                cycle_id, context, CycleStatus.FAILED, stages, warnings,
                [OrchestratorErrorRecord(code=ERROR_ENGINE_FAILURE, message="Position update rejected.")],
                submitted_at, start_perf, position_update_id, portfolio_update_id, apme_report_id,
            )

        position_update_id = position_result.update_id
        stages.append(
            _stage_result(
                PostFillCycleStageId.POSITION_UPDATE.value,
                engine_id=EngineId.POSITION_MANAGER,
                passed=True,
                duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                details={"update_id": position_update_id},
            )
        )

        # PORTFOLIO_INGEST
        stage_start = time.perf_counter()
        portfolio_manager = self._registry.portfolio_manager
        if portfolio_manager is None:
            stages.append(
                _stage_result(
                    PostFillCycleStageId.PORTFOLIO_INGEST.value,
                    engine_id=EngineId.PORTFOLIO_MANAGER,
                    passed=False,
                    rejection_code=ERROR_ENGINE_NOT_READY,
                    message="Portfolio manager not available.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._finalize_post_fill(
                cycle_id, context, CycleStatus.FAILED, stages, warnings,
                [OrchestratorErrorRecord(code=ERROR_ENGINE_NOT_READY, message="Portfolio manager missing.")],
                submitted_at, start_perf, position_update_id, portfolio_update_id, apme_report_id,
            )

        portfolio_context = _build_portfolio_ingest_context(context)

        def _run_portfolio() -> PortfolioUpdateResult:
            ingest_fn = getattr(portfolio_manager, "ingest_position_snapshot", None)
            if ingest_fn is None:
                raise RuntimeError("Portfolio manager missing ingest_position_snapshot.")
            return ingest_fn(position_result.snapshot, portfolio_context)

        portfolio_result = self._call_engine(EngineId.PORTFOLIO_MANAGER, _run_portfolio, cycle_id)
        if portfolio_result is None or portfolio_result.status is PortfolioUpdateStatus.REJECTED:
            stages.append(
                _stage_result(
                    PostFillCycleStageId.PORTFOLIO_INGEST.value,
                    engine_id=EngineId.PORTFOLIO_MANAGER,
                    passed=False,
                    rejection_code=ERROR_ENGINE_FAILURE,
                    message="Portfolio ingest rejected.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
            return self._finalize_post_fill(
                cycle_id, context, CycleStatus.FAILED, stages, warnings,
                [OrchestratorErrorRecord(code=ERROR_ENGINE_FAILURE, message="Portfolio ingest rejected.")],
                submitted_at, start_perf, position_update_id, portfolio_update_id, apme_report_id,
            )

        portfolio_update_id = portfolio_result.update_id
        stages.append(
            _stage_result(
                PostFillCycleStageId.PORTFOLIO_INGEST.value,
                engine_id=EngineId.PORTFOLIO_MANAGER,
                passed=True,
                duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                details={"update_id": portfolio_update_id},
            )
        )

        # APME_EVALUATION
        stage_start = time.perf_counter()
        apme_engine = self._registry.apme
        if apme_engine is None:
            warnings.append(
                OrchestratorWarningRecord(
                    code=ERROR_ENGINE_NOT_READY,
                    message="APME engine not available; skipping evaluation.",
                    stage_id=PostFillCycleStageId.APME_EVALUATION.value,
                    engine_id=EngineId.APME,
                )
            )
            stages.append(
                _stage_result(
                    PostFillCycleStageId.APME_EVALUATION.value,
                    engine_id=EngineId.APME,
                    passed=True,
                    message="APME skipped — engine unavailable.",
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                )
            )
        else:
            apme_context = _build_apme_evaluation_context(
                context,
                portfolio_snapshot_id=portfolio_result.snapshot.snapshot_id,
            )

            def _run_apme() -> APMEDecisionReport:
                evaluate_fn = getattr(apme_engine, "evaluate", None)
                if evaluate_fn is None:
                    raise RuntimeError("APME engine missing evaluate.")
                return evaluate_fn(
                    portfolio_result.snapshot,
                    apme_context,
                    position_snapshot=position_result.snapshot,
                )

            apme_report = self._call_engine(EngineId.APME, _run_apme, cycle_id)
            if apme_report is None:
                stages.append(
                    _stage_result(
                        PostFillCycleStageId.APME_EVALUATION.value,
                        engine_id=EngineId.APME,
                        passed=False,
                        rejection_code=ERROR_ENGINE_FAILURE,
                        message="APME evaluation failed.",
                        duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                    )
                )
                return self._finalize_post_fill(
                    cycle_id, context, CycleStatus.FAILED, stages, warnings,
                    [OrchestratorErrorRecord(code=ERROR_ENGINE_FAILURE, message="APME evaluation failed.")],
                    submitted_at, start_perf, position_update_id, portfolio_update_id, apme_report_id,
                )

            apme_report_id = apme_report.report_id
            stages.append(
                _stage_result(
                    PostFillCycleStageId.APME_EVALUATION.value,
                    engine_id=EngineId.APME,
                    passed=True,
                    duration_ms=(time.perf_counter() - stage_start) * 1000.0,
                    details={"report_id": apme_report_id},
                )
            )

        # MANAGEMENT_DECISION_HANDOFF
        stage_start = time.perf_counter()
        stages.append(
            _stage_result(
                PostFillCycleStageId.MANAGEMENT_DECISION_HANDOFF.value,
                engine_id=None,
                passed=True,
                duration_ms=(time.perf_counter() - stage_start) * 1000.0,
            )
        )

        status = CycleStatus.PARTIAL if warnings else CycleStatus.COMPLETED
        return self._finalize_post_fill(
            cycle_id, context, status, stages, warnings, errors,
            submitted_at, start_perf, position_update_id, portfolio_update_id, apme_report_id,
        )

    def _call_engine(
        self,
        engine_id: EngineId,
        callable_fn: Callable[[], T],
        cycle_id: str,
    ) -> T | None:
        """Invoke engine with error isolation."""
        if self._invoke_engine is not None:
            return self._invoke_engine(engine_id, callable_fn, cycle_id)
        try:
            return callable_fn()
        except Exception as exc:
            if self._record_engine_failure:
                self._record_engine_failure(engine_id, exc, cycle_id)
            if self._config.fail_fast_on_engine_error:
                raise
            return None

    def _finalize_pre_trade(
        self,
        *,
        cycle_id: str,
        context: TradingCycleContext,
        status: CycleStatus,
        stages: list[CycleStageResult],
        warnings: list[OrchestratorWarningRecord],
        errors: list[OrchestratorErrorRecord],
        submitted_at: datetime,
        start_perf: float,
        market_snapshot_id: str | None,
        strategy_result_id: str | None,
        decision_result_id: str | None,
        risk_verdict: str | None,
        execution_plan_id: str | None,
        order_submission_id: str | None,
    ) -> TradingCycleResult:
        """Assemble and validate pre-trade cycle result."""
        completed_at = self._clock()
        duration_ms = (time.perf_counter() - start_perf) * 1000.0

        # RESULT_ASSEMBLY + OUTPUT_VALIDATION stages
        stages.append(
            _stage_result(
                PreTradeCycleStageId.RESULT_ASSEMBLY.value,
                engine_id=None,
                passed=True,
                duration_ms=0.0,
            )
        )
        stages.append(
            _stage_result(
                PreTradeCycleStageId.OUTPUT_VALIDATION.value,
                engine_id=None,
                passed=True,
                duration_ms=0.0,
            )
        )

        primary_error = errors[0].code if errors else None
        preliminary = TradingCycleResult(
            cycle_id=cycle_id,
            correlation_id=context.correlation_id,
            status=status,
            trigger=context.trigger,
            stages=tuple(stages),
            market_snapshot_id=market_snapshot_id,
            strategy_result_id=strategy_result_id,
            decision_result_id=decision_result_id,
            risk_verdict=risk_verdict,
            execution_plan_id=execution_plan_id,
            order_submission_id=order_submission_id,
            warnings=tuple(warnings),
            errors=tuple(errors),
            primary_error_code=primary_error,
            submitted_at=submitted_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            cycle_fingerprint="",
        )
        fingerprint = compute_cycle_fingerprint(context, preliminary, self._config)
        return replace(preliminary, cycle_fingerprint=fingerprint)

    def _failed_pre_trade(
        self,
        cycle_id: str,
        context: TradingCycleContext,
        stages: list[CycleStageResult],
        warnings: list[OrchestratorWarningRecord],
        errors: list[OrchestratorErrorRecord],
        submitted_at: datetime,
        start_perf: float,
        error_code: str,
        message: str,
        stage_id: str,
    ) -> TradingCycleResult:
        """Build failed pre-trade cycle result."""
        errors.append(
            OrchestratorErrorRecord(
                code=error_code,
                message=message,
                stage_id=stage_id,
            )
        )
        return self._finalize_pre_trade(
            cycle_id=cycle_id,
            context=context,
            status=CycleStatus.FAILED,
            stages=stages,
            warnings=warnings,
            errors=errors,
            submitted_at=submitted_at,
            start_perf=start_perf,
            market_snapshot_id=None,
            strategy_result_id=None,
            decision_result_id=None,
            risk_verdict=None,
            execution_plan_id=None,
            order_submission_id=None,
        )

    def _finalize_post_fill(
        self,
        cycle_id: str,
        context: PostFillCycleContext,
        status: CycleStatus,
        stages: list[CycleStageResult],
        warnings: list[OrchestratorWarningRecord],
        errors: list[OrchestratorErrorRecord],
        submitted_at: datetime,
        start_perf: float,
        position_update_id: str | None,
        portfolio_update_id: str | None,
        apme_report_id: str | None,
    ) -> PostFillCycleResult:
        """Assemble post-fill cycle result."""
        completed_at = self._clock()
        duration_ms = (time.perf_counter() - start_perf) * 1000.0
        stages.append(
            _stage_result(
                PostFillCycleStageId.RESULT_ASSEMBLY.value,
                engine_id=None,
                passed=True,
                duration_ms=0.0,
            )
        )
        stages.append(
            _stage_result(
                PostFillCycleStageId.OUTPUT_VALIDATION.value,
                engine_id=None,
                passed=True,
                duration_ms=0.0,
            )
        )
        primary_error = errors[0].code if errors else None
        preliminary = PostFillCycleResult(
            cycle_id=cycle_id,
            correlation_id=context.correlation_id,
            status=status,
            position_update_id=position_update_id,
            portfolio_update_id=portfolio_update_id,
            apme_report_id=apme_report_id,
            stages=tuple(stages),
            warnings=tuple(warnings),
            errors=tuple(errors),
            primary_error_code=primary_error,
            submitted_at=submitted_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            cycle_fingerprint="",
        )
        fingerprint = _compute_post_fill_fingerprint(context, preliminary, self._config)
        return replace(preliminary, cycle_fingerprint=fingerprint)


class SystemOrchestrator:
    """Root coordinator for THETA AI TRADER institutional pipeline.

    Initializes engines, wires Event Bus subscriptions, executes
    trading cycles, monitors health, and performs graceful shutdown.
    Never performs market analysis, strategy selection, broker API
    calls, APME logic, or risk calculations.
    """

    def __init__(
        self,
        config: SystemOrchestratorConfig | None = None,
        *,
        event_bus: EventBus | None = None,
        broker_client: object | None = None,
        engine_registry: EngineRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize orchestrator with injected configuration and engines."""
        self._config = config or default_orchestrator_config()
        self._clock = clock or _utc_now
        self._lock = threading.RLock()
        self._cycle_lock = threading.Lock()
        self._state = OrchestratorState.UNINITIALIZED
        self._in_flight_cycles = 0
        self._broker_client = broker_client
        self._subscription_handles: list[SubscriptionHandle] = []
        self._engine_health: dict[EngineId, EngineHealthStatus] = {}
        self._last_cycle_at: datetime | None = None
        self._last_cycle_status: CycleStatus | None = None
        self._latest_cycle_result: TradingCycleResult | None = None
        self._latest_post_fill_result: PostFillCycleResult | None = None
        self._publish_count = 0
        self._subscriber_failure_count = 0
        self._last_market_snapshot_at: datetime | None = None

        bus = event_bus
        if engine_registry is not None and engine_registry.event_bus is not None:
            bus = engine_registry.event_bus
        self._event_bus = bus or EventBus()

        registry = engine_registry or EngineRegistry()
        if registry.event_bus is None:
            registry = replace(registry, event_bus=self._event_bus)
        self._registry = registry

        self._cycle_executor = CycleExecutor(
            self._config,
            self._registry,
            broker_client=self._broker_client,
            clock=self._clock,
            record_engine_failure=self._record_engine_failure,
            invoke_engine=self._invoke_engine,
        )

    @property
    def config(self) -> SystemOrchestratorConfig:
        """Return immutable orchestrator configuration."""
        return self._config

    @property
    def event_bus(self) -> EventBus:
        """Return coordinated event bus instance."""
        return self._event_bus

    def get_state(self) -> OrchestratorState:
        """Return current lifecycle state."""
        with self._lock:
            return self._state

    def get_latest_cycle_result(self) -> TradingCycleResult | None:
        """Return most recent pre-trade cycle result."""
        with self._lock:
            return self._latest_cycle_result

    def start(self) -> SystemStartupResult:
        """Initialize engines, wire subscriptions, transition to RUNNING."""
        startup_id = str(uuid.uuid4())
        started_at = self._clock()
        start_perf = time.perf_counter()
        warnings: list[OrchestratorWarningRecord] = []
        errors: list[OrchestratorErrorRecord] = []
        engines_started: list[EngineId] = []
        engines_failed: list[EngineId] = []

        with self._lock:
            if self._state not in (OrchestratorState.UNINITIALIZED, OrchestratorState.STOPPED):
                raise OrchestratorStateError(
                    "start() only permitted from UNINITIALIZED or STOPPED.",
                    code=ERROR_STATE_INVALID,
                )
            self._state = OrchestratorState.STARTING

        _logger.info("orchestrator.startup.start", extra={"event": "orchestrator.startup.start"})
        self._publish_system_event(
            OrchestratorEventType.STARTUP_STARTED,
            "system.startup.started",
            startup_id,
        )

        validation = validate_orchestrator_config(self._config)
        if not validation.is_valid:
            errors.extend(validation.errors)
            return self._finish_startup(
                startup_id, StartupStatus.FAILED, engines_started, engines_failed,
                0, warnings, errors, started_at, start_perf, OrchestratorState.FAILED,
            )

        if self._config.account_id == "" and self._config.execution_mode is StrategyExecutionMode.LIVE:
            warnings.append(
                OrchestratorWarningRecord(
                    code=ERROR_CONFIG_INVALID,
                    message="account_id empty in LIVE mode.",
                    field="account_id",
                )
            )

        for engine_id in EngineId:
            engine = self._engine_instance(engine_id)
            if engine is None:
                if engine_id in _CRITICAL_ENGINES:
                    engines_failed.append(engine_id)
                continue
            try:
                validate_fn = getattr(engine, "validate_configuration", None)
                if validate_fn is not None:
                    validate_fn()
                engines_started.append(engine_id)
                self._engine_health[engine_id] = EngineHealthStatus(
                    engine_id=engine_id,
                    status=HealthStatus.HEALTHY,
                    last_success_at=started_at,
                    last_failure_at=None,
                    consecutive_failures=0,
                )
            except Exception as exc:
                engines_failed.append(engine_id)
                errors.append(
                    OrchestratorErrorRecord(
                        code=ERROR_ENGINE_FAILURE,
                        message=str(exc),
                        engine_id=engine_id,
                    )
                )
                self._engine_health[engine_id] = EngineHealthStatus(
                    engine_id=engine_id,
                    status=HealthStatus.UNHEALTHY,
                    last_success_at=None,
                    last_failure_at=started_at,
                    consecutive_failures=1,
                    message=str(exc),
                )

        subscriptions_registered = 0
        try:
            subscriptions_registered = self._wire_subscriptions()
        except Exception as exc:
            warnings.append(
                OrchestratorWarningRecord(
                    code=ERROR_ENGINE_FAILURE,
                    message=f"Subscription wiring partial failure: {exc}",
                )
            )

        critical_failed = any(eid in engines_failed for eid in _CRITICAL_ENGINES)
        if critical_failed:
            status = StartupStatus.FAILED
            final_state = OrchestratorState.FAILED
            self._publish_system_event(
                OrchestratorEventType.STARTUP_FAILED,
                "system.startup.failed",
                startup_id,
            )
        elif engines_failed:
            status = StartupStatus.PARTIAL
            final_state = OrchestratorState.DEGRADED
            self._publish_system_event(
                OrchestratorEventType.STARTUP_COMPLETED,
                "system.startup.completed",
                startup_id,
            )
        else:
            status = StartupStatus.SUCCESS
            final_state = OrchestratorState.RUNNING
            self._publish_system_event(
                OrchestratorEventType.STARTUP_COMPLETED,
                "system.startup.completed",
                startup_id,
            )

        result = self._finish_startup(
            startup_id, status, engines_started, engines_failed,
            subscriptions_registered, warnings, errors, started_at, start_perf, final_state,
        )
        _logger.info(
            "orchestrator.startup.complete",
            extra={"event": "orchestrator.startup.complete", "status": status.value},
        )
        return result

    def stop(self) -> SystemShutdownResult:
        """Gracefully shutdown; drain in-flight cycles."""
        shutdown_id = str(uuid.uuid4())
        started_at = self._clock()
        start_perf = time.perf_counter()
        warnings: list[OrchestratorWarningRecord] = []
        errors: list[OrchestratorErrorRecord] = []

        with self._lock:
            if self._state not in (OrchestratorState.RUNNING, OrchestratorState.DEGRADED):
                raise OrchestratorStateError(
                    "stop() only permitted from RUNNING or DEGRADED.",
                    code=ERROR_STATE_INVALID,
                )
            self._state = OrchestratorState.STOPPING

        _logger.info("orchestrator.shutdown.start", extra={"event": "orchestrator.shutdown.start"})
        self._publish_system_event(
            OrchestratorEventType.SHUTDOWN_STARTED,
            "system.shutdown.started",
            shutdown_id,
        )

        drain_deadline = time.perf_counter() + self._config.shutdown_drain_timeout_seconds
        forced = False
        while True:
            with self._lock:
                in_flight = self._in_flight_cycles
            if in_flight == 0:
                break
            if time.perf_counter() >= drain_deadline:
                forced = True
                warnings.append(
                    OrchestratorWarningRecord(
                        code=ERROR_SHUTDOWN_DRAIN_TIMEOUT,
                        message="Shutdown drain timeout exceeded.",
                    )
                )
                break
            time.sleep(0.01)

        subscriptions_removed = self._unwire_subscriptions()
        engines_stopped = tuple(EngineId)

        with self._lock:
            self._state = OrchestratorState.STOPPED

        self._publish_system_event(
            OrchestratorEventType.SHUTDOWN_COMPLETED,
            "system.shutdown.completed",
            shutdown_id,
        )
        completed_at = self._clock()
        _logger.info("orchestrator.shutdown.complete", extra={"event": "orchestrator.shutdown.complete"})

        return SystemShutdownResult(
            shutdown_id=shutdown_id,
            status=ShutdownStatus.FORCED if forced else ShutdownStatus.SUCCESS,
            cycles_drained=0 if forced else 1,
            subscriptions_removed=subscriptions_removed,
            engines_stopped=engines_stopped,
            warnings=tuple(warnings),
            errors=tuple(errors),
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=(time.perf_counter() - start_perf) * 1000.0,
        )

    def run_trading_cycle(self, context: TradingCycleContext) -> TradingCycleResult:
        """Execute pre-trade entry pipeline."""
        with self._lock:
            if self._state not in (OrchestratorState.RUNNING, OrchestratorState.DEGRADED):
                raise OrchestratorStateError(
                    "Cycle execution requires RUNNING or DEGRADED state.",
                    code=ERROR_STATE_INVALID,
                )
            if (
                self._state is OrchestratorState.DEGRADED
                and self._config.block_pre_trade_in_degraded
            ):
                raise OrchestratorStateError(
                    "Pre-trade cycle blocked in degraded mode.",
                    code=ERROR_STATE_INVALID,
                )

        if self._config.serial_cycle_execution:
            acquired = self._cycle_lock.acquire(blocking=False)
            if not acquired:
                submitted_at = self._clock()
                return TradingCycleResult(
                    cycle_id=str(uuid.uuid4()),
                    correlation_id=context.correlation_id,
                    status=CycleStatus.REJECTED,
                    trigger=context.trigger,
                    stages=(
                        _stage_result(
                            PreTradeCycleStageId.INPUT_GATE.value,
                            engine_id=None,
                            passed=False,
                            rejection_code=ERROR_CYCLE_OVERLAP,
                            message="Concurrent cycle rejected.",
                        ),
                    ),
                    market_snapshot_id=None,
                    strategy_result_id=None,
                    decision_result_id=None,
                    risk_verdict=None,
                    execution_plan_id=None,
                    order_submission_id=None,
                    warnings=(),
                    errors=(
                        OrchestratorErrorRecord(
                            code=ERROR_CYCLE_OVERLAP,
                            message="Concurrent cycle rejected.",
                        ),
                    ),
                    primary_error_code=ERROR_CYCLE_OVERLAP,
                    submitted_at=submitted_at,
                    completed_at=submitted_at,
                    duration_ms=0.0,
                    cycle_fingerprint=compute_cycle_fingerprint(
                        context,
                        TradingCycleResult(
                            cycle_id="",
                            correlation_id=context.correlation_id,
                            status=CycleStatus.REJECTED,
                            trigger=context.trigger,
                            stages=(),
                            market_snapshot_id=None,
                            strategy_result_id=None,
                            decision_result_id=None,
                            risk_verdict=None,
                            execution_plan_id=None,
                            order_submission_id=None,
                            warnings=(),
                            errors=(),
                            primary_error_code=ERROR_CYCLE_OVERLAP,
                            submitted_at=submitted_at,
                            completed_at=submitted_at,
                            duration_ms=0.0,
                            cycle_fingerprint="",
                        ),
                        self._config,
                    ),
                )

        try:
            with self._lock:
                self._in_flight_cycles += 1
            _logger.info(
                "orchestrator.cycle.start",
                extra={"event": "orchestrator.cycle.start", "correlation_id": context.correlation_id},
            )
            self._publish_pipeline_event("pipeline.cycle.started", context.correlation_id)

            result = self._cycle_executor.execute_pre_trade(context)

            topic = (
                "pipeline.cycle.completed"
                if result.status in (CycleStatus.COMPLETED, CycleStatus.PARTIAL, CycleStatus.SKIPPED)
                else "pipeline.cycle.failed"
            )
            self._publish_pipeline_event(topic, context.correlation_id, metadata={"status": result.status.value})

            with self._lock:
                self._latest_cycle_result = result
                self._last_cycle_at = result.completed_at
                self._last_cycle_status = result.status

            if result.status is CycleStatus.FAILED:
                _logger.error(
                    "orchestrator.cycle.failed",
                    extra={"event": "orchestrator.cycle.failed", "correlation_id": context.correlation_id},
                )
            else:
                _logger.info(
                    "orchestrator.cycle.complete",
                    extra={"event": "orchestrator.cycle.complete", "correlation_id": context.correlation_id},
                )
            return result
        finally:
            with self._lock:
                self._in_flight_cycles = max(0, self._in_flight_cycles - 1)
            if self._config.serial_cycle_execution:
                try:
                    self._cycle_lock.release()
                except RuntimeError:
                    pass

    def run_post_fill_cycle(self, context: PostFillCycleContext) -> PostFillCycleResult:
        """Execute post-fill position → portfolio → APME pipeline."""
        with self._lock:
            if self._state not in (OrchestratorState.RUNNING, OrchestratorState.DEGRADED):
                raise OrchestratorStateError(
                    "Cycle execution requires RUNNING or DEGRADED state.",
                    code=ERROR_STATE_INVALID,
                )

        with self._lock:
            self._in_flight_cycles += 1
        try:
            result = self._cycle_executor.execute_post_fill(context)
            with self._lock:
                self._latest_post_fill_result = result
                self._last_cycle_at = result.completed_at
                self._last_cycle_status = result.status
            return result
        finally:
            with self._lock:
                self._in_flight_cycles = max(0, self._in_flight_cycles - 1)

    def get_health(self) -> SystemHealthReport:
        """Return aggregated platform health snapshot."""
        with self._lock:
            state = self._state
            engine_health = MappingProxyType(dict(self._engine_health))
            last_cycle_at = self._last_cycle_at
            last_cycle_status = self._last_cycle_status
            last_market = self._last_market_snapshot_at

        now = self._clock()
        stale = False
        if last_market is not None:
            age = (now - last_market).total_seconds()
            stale = age > self._config.stale_snapshot_max_age_seconds

        issues: list[HealthIssueRecord] = []
        overall = HealthStatus.HEALTHY
        if state is OrchestratorState.DEGRADED:
            overall = HealthStatus.DEGRADED
        elif state in (OrchestratorState.FAILED, OrchestratorState.STOPPING):
            overall = HealthStatus.UNHEALTHY
        elif state is OrchestratorState.UNINITIALIZED:
            overall = HealthStatus.UNKNOWN

        for health in engine_health.values():
            if health.status is HealthStatus.UNHEALTHY:
                issues.append(
                    HealthIssueRecord(
                        issue_code=ERROR_ENGINE_FAILURE,
                        severity="ERROR",
                        message=health.message or "Engine unhealthy.",
                        engine_id=health.engine_id,
                    )
                )
                if overall is HealthStatus.HEALTHY:
                    overall = HealthStatus.DEGRADED

        if stale:
            issues.append(
                HealthIssueRecord(
                    issue_code=ERROR_SNAPSHOT_STALE,
                    severity="WARNING",
                    message="Market snapshot is stale.",
                )
            )

        return SystemHealthReport(
            report_id=str(uuid.uuid4()),
            as_of=now,
            orchestrator_state=state,
            engine_health=engine_health,
            event_bus_metrics=EventBusHealthMetrics(
                publish_count=self._publish_count,
                delivery_count=self._publish_count,
                subscriber_failure_count=self._subscriber_failure_count,
                active_subscriptions=len(self._subscription_handles),
            ),
            last_cycle_at=last_cycle_at,
            last_cycle_status=last_cycle_status,
            stale_snapshot=stale,
            issues=tuple(issues),
            overall_status=overall,
        )

    def on_market_snapshot_event(self, envelope: EventEnvelope) -> None:
        """Handle market.snapshot.published for optional cycle trigger."""
        try:
            self._last_market_snapshot_at = envelope.occurred_at
            if not self._config.enable_event_driven_cycles:
                return
            if self._state not in (OrchestratorState.RUNNING, OrchestratorState.DEGRADED):
                return
            correlation_id = envelope.correlation_id
            context = TradingCycleContext(
                correlation_id=correlation_id,
                reference_time=envelope.occurred_at,
                execution_mode=self._config.execution_mode,
                account_id=self._config.account_id,
                trigger=CycleTrigger.MARKET_SNAPSHOT,
                market_snapshot=envelope.payload if isinstance(envelope.payload, MarketSnapshot) else None,
            )
            self.run_trading_cycle(context)
        except Exception as exc:
            self._handle_event_error(exc)

    def _engine_instance(self, engine_id: EngineId) -> object | None:
        """Resolve engine instance by identifier."""
        mapping = {
            EngineId.EVENT_BUS: self._event_bus,
            EngineId.MARKET_DATA: self._registry.market_data,
            EngineId.STRATEGY_EVALUATION: self._registry.strategy_evaluation,
            EngineId.TRADE_DECISION: self._registry.trade_decision,
            EngineId.RISK: self._registry.risk,
            EngineId.EXECUTION: self._registry.execution,
            EngineId.ORDER_MANAGER: self._registry.order_manager,
            EngineId.POSITION_MANAGER: self._registry.position_manager,
            EngineId.PORTFOLIO_MANAGER: self._registry.portfolio_manager,
            EngineId.APME: self._registry.apme,
        }
        return mapping.get(engine_id)

    def _invoke_engine(
        self,
        engine_id: EngineId,
        callable_fn: Callable[[], T],
        cycle_id: str,
    ) -> T | None:
        """Invoke engine with error isolation."""
        try:
            result = callable_fn()
            now = self._clock()
            prior = self._engine_health.get(engine_id)
            self._engine_health[engine_id] = EngineHealthStatus(
                engine_id=engine_id,
                status=HealthStatus.HEALTHY,
                last_success_at=now,
                last_failure_at=prior.last_failure_at if prior else None,
                consecutive_failures=0,
            )
            return result
        except Exception as exc:
            self._record_engine_failure(engine_id, exc, cycle_id)
            if self._config.fail_fast_on_engine_error:
                raise
            return None

    def _record_engine_failure(self, engine_id: EngineId, exc: Exception, cycle_id: str) -> None:
        """Record isolated engine failure."""
        now = self._clock()
        prior = self._engine_health.get(engine_id)
        consecutive = (prior.consecutive_failures + 1) if prior else 1
        self._engine_health[engine_id] = EngineHealthStatus(
            engine_id=engine_id,
            status=HealthStatus.UNHEALTHY if consecutive >= 3 else HealthStatus.DEGRADED,
            last_success_at=prior.last_success_at if prior else None,
            last_failure_at=now,
            consecutive_failures=consecutive,
            message=str(exc),
        )
        _logger.error(
            "orchestrator.engine.failure",
            extra={
                "event": "orchestrator.engine.failure",
                "engine_id": engine_id.value,
                "cycle_id": cycle_id,
                "error": str(exc),
            },
        )
        self._publish_system_event(
            OrchestratorEventType.ENGINE_FAILURE,
            "system.engine.failure",
            cycle_id,
            metadata={"engine_id": engine_id.value, "error": str(exc)},
        )
        with self._lock:
            if self._state is OrchestratorState.RUNNING and consecutive >= 3:
                if engine_id in _CRITICAL_ENGINES:
                    self._transition_state(OrchestratorState.DEGRADED)

    def _transition_state(self, new_state: OrchestratorState) -> None:
        """Transition orchestrator state and publish event."""
        old_state = self._state
        self._state = new_state
        _logger.info(
            "orchestrator.state.changed",
            extra={
                "event": "orchestrator.state.changed",
                "from": old_state.value,
                "to": new_state.value,
            },
        )
        self._publish_system_event(
            OrchestratorEventType.STATE_CHANGED,
            "system.state.changed",
            str(uuid.uuid4()),
            metadata={"from": old_state.value, "to": new_state.value},
        )

    def _wire_subscriptions(self) -> int:
        """Register orchestrator event handlers."""
        patterns = self._config.subscription_patterns or _DEFAULT_SUBSCRIPTION_PATTERNS
        count = 0
        for pattern in patterns:
            if pattern == "market.snapshot.published":
                handle = self._event_bus.subscribe(pattern, self._dispatch_event)
            else:
                handle = self._event_bus.subscribe(pattern, self._dispatch_event)
            self._subscription_handles.append(handle)
            count += 1
        return count

    def _unwire_subscriptions(self) -> int:
        """Remove orchestrator subscriptions."""
        removed = 0
        for handle in self._subscription_handles:
            try:
                self._event_bus.unsubscribe(handle)
                removed += 1
            except Exception:
                pass
        self._subscription_handles.clear()
        return removed

    def _dispatch_event(self, envelope: EventEnvelope) -> None:
        """Route bus events to orchestrator handlers."""
        try:
            if envelope.topic == "market.snapshot.published":
                self.on_market_snapshot_event(envelope)
            elif envelope.topic == "order.plan.completed":
                pass
            elif envelope.topic == "apme.risk.escalated":
                _logger.warning(
                    "orchestrator.apme.escalation",
                    extra={"correlation_id": envelope.correlation_id},
                )
        except Exception as exc:
            self._handle_event_error(exc)

    def _handle_event_error(self, exc: Exception) -> None:
        """Isolate handler exceptions."""
        self._subscriber_failure_count += 1
        _logger.error(
            "orchestrator.error",
            extra={"event": "orchestrator.error", "error": str(exc)},
        )

    def _publish_system_event(
        self,
        event_type: OrchestratorEventType,
        topic: str,
        correlation_id: str,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Publish orchestrator system event when enabled."""
        if not self._config.publish_system_events:
            return
        occurred_at = self._clock()
        payload = OrchestratorEvent(
            event_type=event_type,
            topic=topic,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            orchestrator_state=self.get_state(),
            metadata=MappingProxyType(dict(metadata or {})),
        )
        try:
            self._event_bus.publish(
                topic=topic,
                payload=payload,
                correlation_id=correlation_id,
                producer=PRODUCER_NAME,
                producer_version=ORCHESTRATOR_VERSION,
                occurred_at=occurred_at,
            )
            self._publish_count += 1
        except Exception as exc:
            _logger.warning("orchestrator.publish.failed", extra={"error": str(exc)})

    def _publish_pipeline_event(
        self,
        topic: str,
        correlation_id: str,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Publish pipeline cycle event."""
        if not self._config.publish_system_events:
            return
        occurred_at = self._clock()
        try:
            self._event_bus.publish(
                topic=topic,
                payload=MappingProxyType(dict(metadata or {})),
                correlation_id=correlation_id,
                producer=PRODUCER_NAME,
                producer_version=ORCHESTRATOR_VERSION,
                occurred_at=occurred_at,
            )
            self._publish_count += 1
        except Exception:
            pass

    def _finish_startup(
        self,
        startup_id: str,
        status: StartupStatus,
        engines_started: list[EngineId],
        engines_failed: list[EngineId],
        subscriptions_registered: int,
        warnings: list[OrchestratorWarningRecord],
        errors: list[OrchestratorErrorRecord],
        started_at: datetime,
        start_perf: float,
        final_state: OrchestratorState,
    ) -> SystemStartupResult:
        """Complete startup sequence."""
        with self._lock:
            self._state = final_state
        completed_at = self._clock()
        return SystemStartupResult(
            startup_id=startup_id,
            status=status,
            engines_started=tuple(engines_started),
            engines_failed=tuple(engines_failed),
            subscriptions_registered=subscriptions_registered,
            warnings=tuple(warnings),
            errors=tuple(errors),
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=(time.perf_counter() - start_perf) * 1000.0,
        )
