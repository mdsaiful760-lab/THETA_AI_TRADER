"""Institutional adaptive position management engine for THETA AI TRADER v1.0.

Consumes immutable portfolio and position artifacts from Portfolio Manager and
Position Manager together with orchestrator-injected market intelligence hints,
and produces sealed management decisions with full explainability.
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
from typing import Any, Final, Mapping

from core.event_bus import EventBus, EventEnvelope
from portfolio.portfolio_manager import (
    PortfolioEvent,
    PortfolioEventType,
    PortfolioExposure,
    PortfolioMetrics,
    PortfolioPositionSummary,
    PortfolioSnapshot,
    PositionGreekHint,
)
from portfolio.position_manager import PositionSnapshot
from strategy.signals import (
    ExitLogic,
    ExitTriggerType,
    StopLossHint,
    StopLossHintType,
    StrategyExecutionMode,
    StrategyFamily,
    TargetHint,
    TargetHintType,
)

APME_VERSION: Final[str] = "1.0.0"
APME_SCHEMA_VERSION: Final[str] = "1.0.0"
PRODUCER_NAME: Final[str] = "adaptive_position_management_engine"
EXIT_PROB_MODEL_VERSION: Final[str] = "APME_EXIT_PROB_V1"
SCORE_ROUND_DECIMALS: Final[int] = 4
PROB_ROUND_DECIMALS: Final[int] = 4
FRACTION_ROUND_DECIMALS: Final[int] = 4

ERROR_CONFIG_INVALID: Final[str] = "APME.CONFIG.INVALID"
ERROR_CONTEXT_INVALID: Final[str] = "APME.CONTEXT.INVALID"
ERROR_CONTEXT_NAIVE_TIMESTAMP: Final[str] = "APME.CONTEXT.NAIVE_TIMESTAMP"
ERROR_CONTEXT_CORRELATION_MISMATCH: Final[str] = "APME.CONTEXT.CORRELATION_MISMATCH"
ERROR_CONTEXT_ACCOUNT_MISMATCH: Final[str] = "APME.CONTEXT.ACCOUNT_MISMATCH"
ERROR_SNAPSHOT_MISSING: Final[str] = "APME.SNAPSHOT.MISSING"
ERROR_SNAPSHOT_INVALID: Final[str] = "APME.SNAPSHOT.INVALID"
ERROR_RESULT_INVALID: Final[str] = "APME.RESULT.INVALID"
ERROR_RESULT_FINGERPRINT_MISMATCH: Final[str] = "APME.RESULT.FINGERPRINT_MISMATCH"
ERROR_SERIALIZATION_UNSUPPORTED_VERSION: Final[str] = "APME.SERIALIZATION.UNSUPPORTED_VERSION"
ERROR_SERIALIZATION_MALFORMED: Final[str] = "APME.SERIALIZATION.MALFORMED"
ERROR_COOLDOWN_ACTIVE: Final[str] = "APME.COOLDOWN.ACTIVE"

WARN_HINT_STALE: Final[str] = "APME.HINT.STALE"
WARN_HINT_MISSING: Final[str] = "APME.HINT.MISSING"
WARN_SIGNAL_METADATA_MISSING: Final[str] = "APME.SIGNAL.METADATA_MISSING"
WARN_ARBITRATION_CONFLICT: Final[str] = "APME.ARBITRATION.CONFLICT"
WARN_QUALITY_INSUFFICIENT_DATA: Final[str] = "APME.QUALITY.INSUFFICIENT_DATA"

_logger = logging.getLogger("apme.adaptive_position_management_engine")


class APMEError(Exception):
    """Base APME exception."""

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


class APMEConfigurationError(APMEError):
    """Raised when APME configuration is invalid."""


class APMEValidationError(APMEError):
    """Raised when input or output validation fails."""


class APMEContextError(APMEError):
    """Raised when evaluation context is invalid."""


class APMEEvaluationError(APMEError):
    """Raised when evaluation pipeline fails irrecoverably."""


class APMEEvaluationStatus(str, Enum):
    """Overall status of an APME evaluation run."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    NOOP = "noop"
    REJECTED = "rejected"
    FAILED = "failed"


class ManagementAction(str, Enum):
    """Recommended management action for a position."""

    HOLD = "hold"
    MONITOR = "monitor"
    PARTIAL_EXIT = "partial_exit"
    FULL_EXIT = "full_exit"
    ADJUST = "adjust"
    ROLL = "roll"
    HEDGE = "hedge"
    PROTECT_PROFIT = "protect_profit"
    ESCALATE = "escalate"
    DEFER = "defer"


class ActionUrgency(str, Enum):
    """Urgency classification for management actions."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HealthStatus(str, Enum):
    """Position health status band."""

    HEALTHY = "healthy"
    WATCH = "watch"
    STRESSED = "stressed"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class AdjustmentType(str, Enum):
    """Structure adjustment classification."""

    NONE = "none"
    REDUCE_SHORT_WING = "reduce_short_wing"
    WIDEN_WINGS = "widen_wings"
    DELTA_NEUTRALIZE = "delta_neutralize"
    CONVERT_TO_DEFINED_RISK = "convert_to_defined_risk"
    REDUCE_QUANTITY = "reduce_quantity"
    ADD_PROTECTIVE_LONG = "add_protective_long"


class ProfitProtectionType(str, Enum):
    """Profit protection mechanism classification."""

    NONE = "none"
    TRAIL_STOP = "trail_stop"
    PARTIAL_PROFIT_LOCK = "partial_profit_lock"
    PREMIUM_DECAY_TARGET = "premium_decay_target"
    BREAK_EVEN_STOP = "break_even_stop"
    TIME_DECAY_LOCK = "time_decay_lock"


class RollDirection(str, Enum):
    """Roll direction classification."""

    NONE = "none"
    OUT = "out"
    IN = "in"
    UP = "up"
    DOWN = "down"
    OUT_AND_UP = "out_and_up"
    OUT_AND_DOWN = "out_and_down"


class HedgeType(str, Enum):
    """Hedge classification."""

    NONE = "none"
    DELTA_HEDGE = "delta_hedge"
    GAMMA_HEDGE = "gamma_hedge"
    VEGA_HEDGE = "vega_hedge"
    TAIL_HEDGE = "tail_hedge"
    CORRELATION_HEDGE = "correlation_hedge"


class QualityScoreBand(str, Enum):
    """Quality score band derived from overall score."""

    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    CRITICAL = "critical"


class APMEEngineId(str, Enum):
    """Sub-engine identifiers."""

    POSITION_HEALTH = "position_health"
    PROFIT_PROTECTION = "profit_protection"
    EXIT_INTELLIGENCE = "exit_intelligence"
    ADJUSTMENT = "adjustment"
    ROLLING = "rolling"
    HEDGING = "hedging"
    BREAK_EVEN = "break_even"
    DYNAMIC_STOP = "dynamic_stop"
    VOLATILITY_EXIT = "volatility_exit"
    TIME_EXIT = "time_exit"
    TREND_REVERSAL_EXIT = "trend_reversal_exit"
    NEWS_EXIT = "news_exit"
    PORTFOLIO_PROTECTION = "portfolio_protection"
    RISK_ESCALATION = "risk_escalation"
    QUALITY_SCORE = "quality_score"
    EXIT_PROBABILITY = "exit_probability"


class APMEEventType(str, Enum):
    """APME lifecycle event discriminator with associated topic."""

    EVALUATION_RECEIVED = "evaluation_received"
    EVALUATION_REJECTED = "evaluation_rejected"
    EVALUATION_COMPLETED = "evaluation_completed"
    DECISION_PUBLISHED = "decision_published"
    EXIT_RECOMMENDED = "exit_recommended"
    ADJUSTMENT_RECOMMENDED = "adjustment_recommended"
    ROLL_RECOMMENDED = "roll_recommended"
    HEDGE_RECOMMENDED = "hedge_recommended"
    PROFIT_PROTECTION_ACTIVATED = "profit_protection_activated"
    HEALTH_DEGRADED = "health_degraded"
    QUALITY_SCORE_UPDATED = "quality_score_updated"
    PORTFOLIO_PROTECTION_TRIGGERED = "portfolio_protection_triggered"
    RISK_ESCALATED = "risk_escalated"
    REPORT_PUBLISHED = "report_published"
    APME_ERROR = "apme_error"

    @property
    def topic(self) -> str:
        """Return hierarchical event bus topic for this event type."""
        mapping = {
            APMEEventType.EVALUATION_RECEIVED: "apme.evaluation.received",
            APMEEventType.EVALUATION_REJECTED: "apme.evaluation.rejected",
            APMEEventType.EVALUATION_COMPLETED: "apme.evaluation.completed",
            APMEEventType.DECISION_PUBLISHED: "apme.decision.published",
            APMEEventType.EXIT_RECOMMENDED: "apme.exit.recommended",
            APMEEventType.ADJUSTMENT_RECOMMENDED: "apme.adjustment.recommended",
            APMEEventType.ROLL_RECOMMENDED: "apme.roll.recommended",
            APMEEventType.HEDGE_RECOMMENDED: "apme.hedge.recommended",
            APMEEventType.PROFIT_PROTECTION_ACTIVATED: "apme.profit_protection.activated",
            APMEEventType.HEALTH_DEGRADED: "apme.health.degraded",
            APMEEventType.QUALITY_SCORE_UPDATED: "apme.quality.updated",
            APMEEventType.PORTFOLIO_PROTECTION_TRIGGERED: "apme.portfolio.protection.triggered",
            APMEEventType.RISK_ESCALATED: "apme.risk.escalated",
            APMEEventType.REPORT_PUBLISHED: "apme.report.published",
            APMEEventType.APME_ERROR: "apme.error",
        }
        return mapping[self]


class APMEEvaluationStageId(str, Enum):
    """Ordered evaluation pipeline stage identifiers."""

    INPUT_GATE = "input_gate"
    SNAPSHOT_INTEGRITY = "snapshot_integrity"
    CONTEXT_HYDRATION = "context_hydration"
    POSITION_HEALTH = "position_health"
    QUALITY_SCORING = "quality_scoring"
    EXIT_PROBABILITY = "exit_probability"
    PROFIT_PROTECTION = "profit_protection"
    DYNAMIC_STOP = "dynamic_stop"
    BREAK_EVEN = "break_even"
    VOLATILITY_EXIT = "volatility_exit"
    TIME_EXIT = "time_exit"
    TREND_REVERSAL_EXIT = "trend_reversal_exit"
    NEWS_EXIT_HOOKS = "news_exit_hooks"
    ADJUSTMENT_INTELLIGENCE = "adjustment_intelligence"
    ROLLING_INTELLIGENCE = "rolling_intelligence"
    HEDGING_INTELLIGENCE = "hedging_intelligence"
    PORTFOLIO_PROTECTION = "portfolio_protection"
    RISK_ESCALATION = "risk_escalation"
    DECISION_ARBITRATION = "decision_arbitration"
    EXPLAINABILITY_ASSEMBLY = "explainability_assembly"
    REPORT_ASSEMBLY = "report_assembly"
    OUTPUT_VALIDATION = "output_validation"


STAGE_ORDER: Final[tuple[APMEEvaluationStageId, ...]] = (
    APMEEvaluationStageId.INPUT_GATE,
    APMEEvaluationStageId.SNAPSHOT_INTEGRITY,
    APMEEvaluationStageId.CONTEXT_HYDRATION,
    APMEEvaluationStageId.POSITION_HEALTH,
    APMEEvaluationStageId.QUALITY_SCORING,
    APMEEvaluationStageId.EXIT_PROBABILITY,
    APMEEvaluationStageId.PROFIT_PROTECTION,
    APMEEvaluationStageId.DYNAMIC_STOP,
    APMEEvaluationStageId.BREAK_EVEN,
    APMEEvaluationStageId.VOLATILITY_EXIT,
    APMEEvaluationStageId.TIME_EXIT,
    APMEEvaluationStageId.TREND_REVERSAL_EXIT,
    APMEEvaluationStageId.NEWS_EXIT_HOOKS,
    APMEEvaluationStageId.ADJUSTMENT_INTELLIGENCE,
    APMEEvaluationStageId.ROLLING_INTELLIGENCE,
    APMEEvaluationStageId.HEDGING_INTELLIGENCE,
    APMEEvaluationStageId.PORTFOLIO_PROTECTION,
    APMEEvaluationStageId.RISK_ESCALATION,
    APMEEvaluationStageId.DECISION_ARBITRATION,
    APMEEvaluationStageId.EXPLAINABILITY_ASSEMBLY,
    APMEEvaluationStageId.REPORT_ASSEMBLY,
    APMEEvaluationStageId.OUTPUT_VALIDATION,
)

_URGENCY_RANK: Final[dict[ActionUrgency, int]] = {
    ActionUrgency.NONE: 0,
    ActionUrgency.LOW: 1,
    ActionUrgency.MEDIUM: 2,
    ActionUrgency.HIGH: 3,
    ActionUrgency.CRITICAL: 4,
}


def default_health_weights() -> Mapping[str, float]:
    """Return default position health dimension weights."""
    return MappingProxyType(
        {
            "structural": 0.25,
            "liquidity": 0.15,
            "time_decay": 0.15,
            "distance_to_risk": 0.20,
            "pnl_health": 0.15,
            "greek_health": 0.10,
        }
    )


def default_quality_weights() -> Mapping[str, float]:
    """Return default quality score component weights."""
    return MappingProxyType(
        {
            "profitability": 0.30,
            "risk": 0.25,
            "time": 0.20,
            "liquidity": 0.15,
            "structure": 0.10,
        }
    )


def default_exit_prob_weights() -> Mapping[str, float]:
    """Return default exit probability logistic weights."""
    return MappingProxyType(
        {
            "health_inverse": 1.2,
            "exit_hint_count": 0.8,
            "max_urgency": 1.0,
            "vol_stress": 0.6,
            "time_pressure": 0.9,
        }
    )


@dataclass(frozen=True)
class APMEConfig:
    """Configuration for APME behavior."""

    strict_correlation: bool = True
    strict_output_validation: bool = True
    deterministic_fingerprint: bool = True
    publish_lifecycle_events: bool = True
    idempotent_evaluate: bool = True
    require_signal_metadata: bool = False
    enable_portfolio_protection: bool = True
    enable_risk_escalation: bool = True
    enable_news_exit_hooks: bool = True
    decision_cooldown_seconds: int = 60
    hint_max_age_seconds: int = 120
    exit_probability_horizon_minutes: int = 60
    drawdown_reduce_threshold_pct: float = 5.0
    drawdown_halt_threshold_pct: float = 10.0
    margin_stress_threshold_pct: float = 85.0
    underlying_concentration_limit_pct: float = 40.0
    min_dte_short_premium: int = 1
    session_exit_minutes_before_close: int = 30
    health_weights: Mapping[str, float] = field(default_factory=default_health_weights)
    quality_weights: Mapping[str, float] = field(default_factory=default_quality_weights)
    exit_prob_weights: Mapping[str, float] = field(default_factory=default_exit_prob_weights)
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.decision_cooldown_seconds < 0:
            raise APMEConfigurationError(
                "decision_cooldown_seconds must be non-negative.",
                code=ERROR_CONFIG_INVALID,
                field="decision_cooldown_seconds",
            )
        if self.hint_max_age_seconds < 0:
            raise APMEConfigurationError(
                "hint_max_age_seconds must be non-negative.",
                code=ERROR_CONFIG_INVALID,
                field="hint_max_age_seconds",
            )


@dataclass(frozen=True)
class VolatilityHints:
    """Orchestrator-supplied volatility regime hints."""

    as_of: datetime
    source: str = "orchestrator"
    iv_rank: float | None = None
    iv_percentile: float | None = None
    vix_level: float | None = None
    vix_regime: str | None = None
    vol_of_vol: float | None = None


@dataclass(frozen=True)
class RegimeHints:
    """Orchestrator-supplied market regime hints."""

    as_of: datetime
    market_regime: str | None = None
    volatility_regime: str | None = None
    regime_confidence: float | None = None


@dataclass(frozen=True)
class TrendHints:
    """Orchestrator-supplied trend hints for one underlying."""

    underlying: str
    as_of: datetime
    trend_direction: str | None = None
    trend_strength: float | None = None
    reversal_detected: bool = False


@dataclass(frozen=True)
class NewsEventFlag:
    """Orchestrator-supplied news or event flag."""

    event_id: str
    event_type: str
    severity: str
    affected_underlyings: tuple[str, ...]
    valid_from: datetime
    valid_until: datetime | None = None
    action_hint: str | None = None


@dataclass(frozen=True)
class SignalManagementMetadata:
    """Original signal exit/stop/target metadata for one position."""

    position_id: str
    signal_id: str | None = None
    exit_logic: ExitLogic | None = None
    stop_loss_hint: StopLossHint | None = None
    target_hint: TargetHint | None = None
    max_hold_minutes: int | None = None
    plan_id: str | None = None


@dataclass(frozen=True)
class SessionContext:
    """Trading session context for time-based rules."""

    session_date: str
    timezone: str = "Asia/Kolkata"
    minutes_to_close: int | None = None
    is_expiry_day: bool = False


@dataclass(frozen=True)
class APMEEvaluationContext:
    """Immutable per-run evaluation inputs."""

    correlation_id: str
    reference_time: datetime
    execution_mode: StrategyExecutionMode
    account_id: str
    portfolio_snapshot_id: str
    price_hints: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    underlying_marks: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
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
    session_context: SessionContext | None = None
    prior_report_fingerprint: str | None = None
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class HealthIssueRecord:
    """Structured health issue for one position."""

    issue_code: str
    severity: str
    message: str
    dimension: str


@dataclass(frozen=True)
class PositionHealth:
    """Multi-dimensional health assessment for one position."""

    position_id: str
    health_status: HealthStatus
    health_score: float
    structural_integrity_score: float
    liquidity_score: float
    time_decay_score: float
    distance_to_risk_score: float
    pnl_health_score: float
    assessed_at: datetime
    health_fingerprint: str
    greek_health_score: float | None = None
    issues: tuple[HealthIssueRecord, ...] = ()


@dataclass(frozen=True)
class ExplainabilityRecord:
    """Single attributed reason with evidence."""

    record_id: str
    engine_id: APMEEngineId
    reason_code: str
    message: str
    evidence: Mapping[str, str]
    weight: float


@dataclass(frozen=True)
class ExitDecision:
    """Structured exit recommendation."""

    decision_id: str
    position_id: str
    recommended: bool
    exit_trigger: ExitTriggerType
    exit_fraction: float
    urgency: ActionUrgency
    trigger_engine: APMEEngineId
    reason_codes: tuple[str, ...]
    explainability: tuple[ExplainabilityRecord, ...] = ()
    target_exit_by: datetime | None = None
    roll_alternative: RollDecision | None = None


@dataclass(frozen=True)
class AdjustmentDecision:
    """Structure modification recommendation."""

    decision_id: str
    position_id: str
    recommended: bool
    adjustment_type: AdjustmentType
    adjustment_fraction: float
    urgency: ActionUrgency
    reason_codes: tuple[str, ...]
    explainability: tuple[ExplainabilityRecord, ...] = ()
    target_delta_hint: float | None = None
    wing_adjustment_hint: str | None = None


@dataclass(frozen=True)
class ProfitProtectionDecision:
    """Profit lock or trail activation recommendation."""

    decision_id: str
    position_id: str
    recommended: bool
    protection_type: ProfitProtectionType
    urgency: ActionUrgency
    reason_codes: tuple[str, ...]
    activated: bool = False
    explainability: tuple[ExplainabilityRecord, ...] = ()
    trail_level_hint: float | None = None
    lock_fraction: float | None = None
    premium_decay_target_pct: float | None = None


@dataclass(frozen=True)
class RollDecision:
    """Roll recommendation."""

    decision_id: str
    position_id: str
    recommended: bool
    roll_direction: RollDirection
    roll_fraction: float
    urgency: ActionUrgency
    reason_codes: tuple[str, ...]
    explainability: tuple[ExplainabilityRecord, ...] = ()
    target_expiry: str | None = None
    target_strike_hint: float | None = None


@dataclass(frozen=True)
class HedgeDecision:
    """Hedge recommendation at position or portfolio scope."""

    decision_id: str
    scope: str
    recommended: bool
    hedge_type: HedgeType
    urgency: ActionUrgency
    reason_codes: tuple[str, ...]
    explainability: tuple[ExplainabilityRecord, ...] = ()
    position_id: str | None = None
    hedge_quantity_hint: int | None = None
    hedge_instrument_hint: str | None = None


@dataclass(frozen=True)
class DynamicStopState:
    """Live dynamic stop state for one position."""

    position_id: str
    stop_active: bool
    stop_type: str
    last_updated_at: datetime
    stop_level_hint: float | None = None
    stop_basis: str | None = None
    breached: bool = False
    distance_to_stop_pct: float | None = None


@dataclass(frozen=True)
class ExitProbability:
    """Deterministic exit probability estimate."""

    position_id: str
    probability: float
    horizon_minutes: int
    model_version: str
    computed_at: datetime
    contributing_factors: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class PositionQualityScore:
    """Composite quality ranking metric."""

    position_id: str
    overall_score: float
    profitability_component: float
    risk_component: float
    time_component: float
    liquidity_component: float
    structure_component: float
    score_band: QualityScoreBand
    computed_at: datetime
    score_fingerprint: str
    rank_percentile: float | None = None


@dataclass(frozen=True)
class PositionManagementDecision:
    """Complete per-position management decision bundle."""

    decision_id: str
    position_id: str
    instrument_key: str
    underlying: str
    strategy_id: str
    strategy_family: StrategyFamily
    primary_action: ManagementAction
    action_urgency: ActionUrgency
    health: PositionHealth
    quality_score: PositionQualityScore
    exit_probability: ExitProbability
    decision_fingerprint: str
    explainability: tuple[ExplainabilityRecord, ...] = ()
    engine_contributions: Mapping[APMEEngineId, float] = field(
        default_factory=lambda: MappingProxyType({})
    )
    position_group_id: str | None = None
    exit_decision: ExitDecision | None = None
    adjustment_decision: AdjustmentDecision | None = None
    profit_protection_decision: ProfitProtectionDecision | None = None
    roll_decision: RollDecision | None = None
    hedge_decision: HedgeDecision | None = None
    stop_state: DynamicStopState | None = None
    cooldown_until: datetime | None = None


@dataclass(frozen=True)
class PortfolioProtectionAction:
    """Account-level portfolio protection action."""

    action_id: str
    action_type: str
    trigger_code: str
    affected_scope: str
    urgency: ActionUrgency
    explainability: tuple[ExplainabilityRecord, ...] = ()
    target_reduction_pct: float | None = None


@dataclass(frozen=True)
class RiskEscalationRecord:
    """Risk escalation record for orchestrator review."""

    escalation_id: str
    escalation_level: str
    trigger_code: str
    position_ids: tuple[str, ...]
    message: str
    requires_human_ack: bool = False


@dataclass(frozen=True)
class PositionGroupDecision:
    """Optional group-level decision spanning multiple legs."""

    group_id: str
    position_ids: tuple[str, ...]
    primary_action: ManagementAction
    net_health_score: float
    explainability: tuple[ExplainabilityRecord, ...] = ()
    group_exit_decision: ExitDecision | None = None


@dataclass(frozen=True)
class APMEWarningRecord:
    """Non-fatal warning emitted during evaluation."""

    code: str
    message: str
    stage_id: APMEEvaluationStageId | None = None
    field: str | None = None
    position_id: str | None = None


@dataclass(frozen=True)
class APMEErrorRecord:
    """Structured error emitted during evaluation."""

    code: str
    message: str
    stage_id: APMEEvaluationStageId | None = None
    field: str | None = None
    position_id: str | None = None


@dataclass(frozen=True)
class APMEValidationResult:
    """Validation outcome for context or report checks."""

    errors: tuple[APMEErrorRecord, ...] = ()
    warnings: tuple[APMEWarningRecord, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return True when no errors are present."""
        return not self.errors


@dataclass(frozen=True)
class APMEStageResult:
    """Audit record for one pipeline stage."""

    stage_id: APMEEvaluationStageId
    passed: bool
    rejection_code: str | None
    message: str | None
    duration_ms: float
    details: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class APMEPipelineResult:
    """Pipeline stage audit summary."""

    total_stages: int
    passed_stages: int
    failed_stage_id: APMEEvaluationStageId | None
    stages: tuple[APMEStageResult, ...]
    short_circuited: bool


@dataclass(frozen=True)
class APMEDecisionReport:
    """Immutable sealed APME evaluation report."""

    report_id: str
    correlation_id: str
    source_portfolio_snapshot_id: str
    as_of: datetime
    account_id: str
    status: APMEEvaluationStatus
    decisions: tuple[PositionManagementDecision, ...]
    pipeline_summary: APMEPipelineResult
    submitted_at: datetime
    report_fingerprint: str
    group_decisions: tuple[PositionGroupDecision, ...] = ()
    portfolio_actions: tuple[PortfolioProtectionAction, ...] = ()
    escalations: tuple[RiskEscalationRecord, ...] = ()
    warnings: tuple[APMEWarningRecord, ...] = ()
    errors: tuple[APMEErrorRecord, ...] = ()
    primary_error_code: str | None = None
    completed_at: datetime | None = None
    duration_ms: float = 0.0


@dataclass(frozen=True)
class APMEEvent:
    """Structured APME lifecycle event payload."""

    event_type: APMEEventType
    topic: str
    report_id: str
    correlation_id: str
    occurred_at: datetime
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    report: APMEDecisionReport | None = None
    position_id: str | None = None


@dataclass(frozen=True)
class ExitHint:
    """Internal exit hint from a sub-engine."""

    engine_id: APMEEngineId
    exit_fraction: float
    urgency: ActionUrgency
    trigger: ExitTriggerType
    reason_code: str
    message: str
    evidence: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class APMEPositionContext:
    """Hydrated per-position evaluation context."""

    position: PortfolioPositionSummary
    mark_price: float | None
    underlying_mark: float | None
    greek_hint: PositionGreekHint | None
    signal_metadata: SignalManagementMetadata | None
    trend_hint: TrendHints | None
    dte: int | None
    is_short_premium: bool
    position_group_id: str | None = None


@dataclass(frozen=True)
class APMEPortfolioContext:
    """Hydrated portfolio-level evaluation context."""

    snapshot: PortfolioSnapshot
    eval_context: APMEEvaluationContext
    position_contexts: Mapping[str, APMEPositionContext]


@dataclass(frozen=True)
class _PositionPartialResults:
    """Partial engine outputs for one position."""

    health: PositionHealth | None = None
    quality_score: PositionQualityScore | None = None
    exit_probability: ExitProbability | None = None
    profit_protection: ProfitProtectionDecision | None = None
    stop_state: DynamicStopState | None = None
    break_even_crossed: bool = False
    exit_hints: tuple[ExitHint, ...] = ()
    adjustment: AdjustmentDecision | None = None
    roll: RollDecision | None = None
    hedge: HedgeDecision | None = None
    exit_decision: ExitDecision | None = None
    explainability: tuple[ExplainabilityRecord, ...] = ()


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

    snapshot: PortfolioSnapshot
    context: APMEEvaluationContext
    config: APMEConfig
    report_id: str
    started_at: datetime
    position_snapshot: PositionSnapshot | None = None
    portfolio_context: APMEPortfolioContext | None = None
    partials: dict[str, _PositionPartialResults] = field(default_factory=dict)
    portfolio_actions: list[PortfolioProtectionAction] = field(default_factory=list)
    escalations: list[RiskEscalationRecord] = field(default_factory=list)
    group_decisions: list[PositionGroupDecision] = field(default_factory=list)
    decisions: list[PositionManagementDecision] = field(default_factory=list)
    warnings: list[APMEWarningRecord] = field(default_factory=list)
    errors: list[APMEErrorRecord] = field(default_factory=list)
    primary_error_code: str | None = None
    pre_eval_rejected: bool = False
    idempotent_noop: bool = False
    status: APMEEvaluationStatus = APMEEvaluationStatus.COMPLETED
    prior_health_status: dict[str, HealthStatus] = field(default_factory=dict)


def default_apme_config() -> APMEConfig:
    """Return production-default APME configuration."""
    return APMEConfig(
        strict_correlation=True,
        strict_output_validation=True,
        deterministic_fingerprint=True,
        publish_lifecycle_events=True,
        idempotent_evaluate=True,
        require_signal_metadata=False,
        enable_portfolio_protection=True,
        enable_risk_escalation=True,
        enable_news_exit_hooks=True,
        decision_cooldown_seconds=60,
        hint_max_age_seconds=120,
        exit_probability_horizon_minutes=60,
        drawdown_reduce_threshold_pct=5.0,
        drawdown_halt_threshold_pct=10.0,
        margin_stress_threshold_pct=85.0,
        underlying_concentration_limit_pct=40.0,
        min_dte_short_premium=1,
        session_exit_minutes_before_close=30,
        health_weights=default_health_weights(),
        quality_weights=default_quality_weights(),
        exit_prob_weights=default_exit_prob_weights(),
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
        raise APMEValidationError(
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
        raise APMEValidationError(
            "deserialized datetime must be timezone-aware.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return parsed


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp float to inclusive range."""
    return max(low, min(high, value))


def _round_score(value: float) -> float:
    """Round score to configured precision."""
    return round(_clamp(value), SCORE_ROUND_DECIMALS)


def _round_fraction(value: float) -> float:
    """Round fraction to configured precision."""
    return round(_clamp(value), FRACTION_ROUND_DECIMALS)


def config_fingerprint(config: APMEConfig) -> str:
    """Compute deterministic fingerprint for configuration."""
    payload = {
        "strict_correlation": config.strict_correlation,
        "strict_output_validation": config.strict_output_validation,
        "deterministic_fingerprint": config.deterministic_fingerprint,
        "idempotent_evaluate": config.idempotent_evaluate,
        "enable_portfolio_protection": config.enable_portfolio_protection,
        "enable_risk_escalation": config.enable_risk_escalation,
        "enable_news_exit_hooks": config.enable_news_exit_hooks,
        "decision_cooldown_seconds": config.decision_cooldown_seconds,
        "hint_max_age_seconds": config.hint_max_age_seconds,
        "exit_probability_horizon_minutes": config.exit_probability_horizon_minutes,
        "drawdown_reduce_threshold_pct": config.drawdown_reduce_threshold_pct,
        "drawdown_halt_threshold_pct": config.drawdown_halt_threshold_pct,
        "margin_stress_threshold_pct": config.margin_stress_threshold_pct,
        "underlying_concentration_limit_pct": config.underlying_concentration_limit_pct,
        "min_dte_short_premium": config.min_dte_short_premium,
        "session_exit_minutes_before_close": config.session_exit_minutes_before_close,
        "health_weights": dict(sorted(config.health_weights.items())),
        "quality_weights": dict(sorted(config.quality_weights.items())),
        "exit_prob_weights": dict(sorted(config.exit_prob_weights.items())),
        "metadata": dict(sorted(config.metadata.items())),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _quality_band(score: float) -> QualityScoreBand:
    """Map overall score to quality band."""
    if score >= 0.80:
        return QualityScoreBand.EXCELLENT
    if score >= 0.60:
        return QualityScoreBand.GOOD
    if score >= 0.40:
        return QualityScoreBand.FAIR
    if score >= 0.20:
        return QualityScoreBand.POOR
    return QualityScoreBand.CRITICAL


def _health_status_from_score(score: float) -> HealthStatus:
    """Map health score to status band."""
    if score >= 0.75:
        return HealthStatus.HEALTHY
    if score >= 0.55:
        return HealthStatus.WATCH
    if score >= 0.35:
        return HealthStatus.STRESSED
    if score > 0.0:
        return HealthStatus.CRITICAL
    return HealthStatus.UNKNOWN


def _derive_dte(position: PortfolioPositionSummary, reference_time: datetime) -> int | None:
    """Derive days-to-expiry from metadata or expiry field."""
    raw_dte = position.metadata.get("dte")
    if raw_dte is not None:
        try:
            return int(raw_dte)
        except (TypeError, ValueError):
            pass
    expiry = position.expiry
    if not expiry or expiry == "UNKNOWN":
        return None
    try:
        expiry_date = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        ref_date = reference_time.astimezone(timezone.utc).date()
        delta = (expiry_date.date() - ref_date).days
        return max(delta, 0)
    except ValueError:
        return None


def _is_short_premium(position: PortfolioPositionSummary) -> bool:
    """Return True when position is short premium."""
    return position.side.lower() == "short"


def _make_explainability(
    *,
    engine_id: APMEEngineId,
    reason_code: str,
    message: str,
    evidence: Mapping[str, str] | None = None,
    weight: float = 0.5,
) -> ExplainabilityRecord:
    """Build one explainability record."""
    payload = dict(evidence or {})
    if not payload:
        payload = {"fact": message[:120]}
    return ExplainabilityRecord(
        record_id=f"exp-{uuid.uuid4().hex[:12]}",
        engine_id=engine_id,
        reason_code=reason_code,
        message=message,
        evidence=MappingProxyType(payload),
        weight=_round_score(weight),
    )


def _generate_report_id(context: APMEEvaluationContext, config: APMEConfig) -> str:
    """Generate evaluation report identifier."""
    if config.deterministic_fingerprint:
        payload = {
            "correlation_id": context.correlation_id,
            "portfolio_snapshot_id": context.portfolio_snapshot_id,
            "as_of": _datetime_to_iso(context.reference_time),
        }
        digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:16]
        return f"apme-{digest}"
    return f"apme-{uuid.uuid4().hex[:16]}"


def _generate_decision_id(position_id: str, report_id: str) -> str:
    """Generate stable decision identifier."""
    payload = f"{report_id}|{position_id}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"apmd-{digest}"


def compute_health_fingerprint(
    position_id: str,
    scores: Mapping[str, float],
    assessed_at: datetime,
) -> str:
    """Compute deterministic health fingerprint."""
    payload = {
        "position_id": position_id,
        "scores": dict(sorted(scores.items())),
        "assessed_at": _datetime_to_iso(assessed_at),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_quality_fingerprint(
    position_id: str,
    components: Mapping[str, float],
    computed_at: datetime,
) -> str:
    """Compute deterministic quality score fingerprint."""
    payload = {
        "position_id": position_id,
        "components": dict(sorted(components.items())),
        "computed_at": _datetime_to_iso(computed_at),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_decision_fingerprint(
    position_id: str,
    primary_action: ManagementAction,
    sub_fingerprints: Mapping[str, str],
) -> str:
    """Compute deterministic decision fingerprint."""
    payload = {
        "position_id": position_id,
        "primary_action": primary_action.value,
        "sub_fingerprints": dict(sorted(sub_fingerprints.items())),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_report_fingerprint(
    portfolio_snapshot: PortfolioSnapshot,
    report: APMEDecisionReport,
    config: APMEConfig,
) -> str:
    """Compute SHA-256 over canonical JSON of management outcomes."""
    payload = {
        "portfolio_snapshot_fingerprint": portfolio_snapshot.snapshot_fingerprint,
        "decision_outcomes": {
            "report_id": report.report_id,
            "decision_count": len(report.decisions),
            "primary_actions": sorted(
                (d.position_id, d.primary_action.value) for d in report.decisions
            ),
            "escalation_count": len(report.escalations),
            "portfolio_action_count": len(report.portfolio_actions),
        },
        "config_hash": config_fingerprint(config),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def validate_evaluation_context(
    context: APMEEvaluationContext,
    snapshot: PortfolioSnapshot,
    config: APMEConfig,
) -> APMEValidationResult:
    """Validate evaluation context and snapshot before evaluation."""
    errors: list[APMEErrorRecord] = []
    warnings: list[APMEWarningRecord] = []

    if not _is_timezone_aware(context.reference_time):
        errors.append(
            APMEErrorRecord(
                code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
                message="reference_time must be timezone-aware.",
                field="reference_time",
            )
        )
    if config.strict_correlation and not context.correlation_id:
        errors.append(
            APMEErrorRecord(
                code=ERROR_CONTEXT_CORRELATION_MISMATCH,
                message="correlation_id must be non-empty.",
                field="correlation_id",
            )
        )
    if (
        config.strict_correlation
        and context.correlation_id
        and snapshot.correlation_id
        and context.correlation_id != snapshot.correlation_id
    ):
        errors.append(
            APMEErrorRecord(
                code=ERROR_CONTEXT_CORRELATION_MISMATCH,
                message="correlation_id mismatch with portfolio snapshot.",
                field="correlation_id",
            )
        )
    if (
        config.strict_correlation
        and context.execution_mode is StrategyExecutionMode.LIVE
        and context.account_id != snapshot.account_id
    ):
        errors.append(
            APMEErrorRecord(
                code=ERROR_CONTEXT_ACCOUNT_MISMATCH,
                message="account_id mismatch with portfolio snapshot.",
                field="account_id",
            )
        )
    if not isinstance(context.execution_mode, StrategyExecutionMode):
        errors.append(
            APMEErrorRecord(
                code=ERROR_CONTEXT_INVALID,
                message="execution_mode is invalid.",
                field="execution_mode",
            )
        )
    return APMEValidationResult(errors=tuple(errors), warnings=tuple(warnings))


def validate_apme_decision_report(report: APMEDecisionReport) -> APMEValidationResult:
    """Validate sealed APME decision report."""
    errors: list[APMEErrorRecord] = []
    warnings: list[APMEWarningRecord] = []

    if not report.report_id:
        errors.append(
            APMEErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="report_id must be non-empty.",
                field="report_id",
            )
        )
    if report.duration_ms < 0:
        errors.append(
            APMEErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="duration_ms must be non-negative.",
                field="duration_ms",
            )
        )
    decision_ids = [d.decision_id for d in report.decisions]
    if len(decision_ids) != len(set(decision_ids)):
        errors.append(
            APMEErrorRecord(
                code=ERROR_RESULT_INVALID,
                message="duplicate decision_id values within report.",
            )
        )
    for decision in report.decisions:
        if not (0.0 <= decision.health.health_score <= 1.0):
            errors.append(
                APMEErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="health_score out of range.",
                    position_id=decision.position_id,
                )
            )
        if not (0.0 <= decision.quality_score.overall_score <= 1.0):
            errors.append(
                APMEErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="overall_score out of range.",
                    position_id=decision.position_id,
                )
            )
        if decision.primary_action is not ManagementAction.HOLD and not decision.explainability:
            errors.append(
                APMEErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="non-HOLD decision requires explainability.",
                    position_id=decision.position_id,
                )
            )
        if decision.exit_decision and decision.exit_decision.recommended:
            if not decision.exit_decision.reason_codes:
                errors.append(
                    APMEErrorRecord(
                        code=ERROR_RESULT_INVALID,
                        message="recommended exit requires reason_codes.",
                        position_id=decision.position_id,
                    )
                )
    return APMEValidationResult(errors=tuple(errors), warnings=tuple(warnings))


def assert_valid_apme_decision_report(report: APMEDecisionReport) -> None:
    """Raise APMEValidationError when report is invalid."""
    validation = validate_apme_decision_report(report)
    if not validation.is_valid:
        primary = validation.errors[0]
        raise APMEValidationError(
            primary.message,
            code=primary.code,
            field=primary.field,
        )


def compute_position_health(
    context: APMEPositionContext,
    config: APMEConfig,
    *,
    reference_time: datetime | None = None,
) -> PositionHealth:
    """Assess structural and temporal health for one position.

    Args:
        context: Hydrated per-position evaluation context.
        config: APME configuration with health dimension weights.
        reference_time: Evaluation timestamp; defaults to UTC now.

    Returns:
        Immutable PositionHealth assessment.
    """
    assessed_at = reference_time if reference_time is not None else _utc_now()
    position = context.position
    weights = config.health_weights
    issues: list[HealthIssueRecord] = []

    structural = 0.85
    if context.is_short_premium and context.dte is not None and context.dte <= 1:
        structural = min(structural, 0.55)
        issues.append(
            HealthIssueRecord(
                issue_code="APME.HEALTH.STRUCTURE.SHORT_DTE",
                severity="HIGH",
                message="Short premium position within 1 DTE.",
                dimension="structural",
            )
        )

    liquidity = 0.75
    spread_hint = position.metadata.get("bid_ask_spread_pct")
    if spread_hint is not None:
        try:
            spread = float(spread_hint)
            liquidity = _round_score(1.0 - min(spread / 10.0, 1.0))
        except (TypeError, ValueError):
            pass
    elif context.mark_price is None:
        liquidity = 0.50
        issues.append(
            HealthIssueRecord(
                issue_code="APME.HEALTH.LIQUIDITY.MARK_MISSING",
                severity="MEDIUM",
                message="Mark price unavailable for liquidity assessment.",
                dimension="liquidity",
            )
        )

    time_decay = 0.80
    if context.dte is not None:
        if context.is_short_premium and context.dte <= config.min_dte_short_premium:
            time_decay = 0.20
            issues.append(
                HealthIssueRecord(
                    issue_code="APME.HEALTH.TIME.GAMMA_RISK",
                    severity="HIGH",
                    message="Short premium near expiry gamma risk.",
                    dimension="time_decay",
                )
            )
        else:
            time_decay = _round_score(min(context.dte / 30.0, 1.0))

    distance_to_risk = 0.80
    strike_distance_pct = position.metadata.get("short_strike_distance_pct")
    if strike_distance_pct is not None:
        try:
            distance = abs(float(strike_distance_pct))
            distance_to_risk = _round_score(min(distance / 5.0, 1.0))
            if distance < 0.5:
                issues.append(
                    HealthIssueRecord(
                        issue_code="APME.HEALTH.DISTANCE.SHORT_STRIKE_TESTED",
                        severity="CRITICAL",
                        message="Underlying within threshold of short strike.",
                        dimension="distance_to_risk",
                    )
                )
                distance_to_risk = min(distance_to_risk, 0.25)
        except (TypeError, ValueError):
            pass

    pnl_health = 0.70
    max_loss_hint = position.metadata.get("max_loss")
    if max_loss_hint is not None:
        try:
            max_loss = abs(float(max_loss_hint))
            if max_loss > 0:
                pnl_ratio = position.unrealized_pnl / max_loss
                pnl_health = _round_score(0.5 + (0.5 * (1.0 - _clamp(pnl_ratio, -1.0, 1.0))))
        except (TypeError, ValueError):
            pass
    elif position.unrealized_pnl >= 0:
        pnl_health = 0.85
    else:
        pnl_health = 0.45

    greek_health: float | None = None
    if context.greek_hint is not None:
        delta = context.greek_hint.delta
        if delta is not None:
            greek_health = _round_score(1.0 - min(abs(delta) / 0.50, 1.0))
            if abs(delta) > 0.40:
                issues.append(
                    HealthIssueRecord(
                        issue_code="APME.HEALTH.GREEK.DELTA_STRESS",
                        severity="MEDIUM",
                        message="Elevated delta exposure.",
                        dimension="greek_health",
                    )
                )

    score_components = {
        "structural": _round_score(structural),
        "liquidity": _round_score(liquidity),
        "time_decay": _round_score(time_decay),
        "distance_to_risk": _round_score(distance_to_risk),
        "pnl_health": _round_score(pnl_health),
    }
    weighted = (
        weights["structural"] * score_components["structural"]
        + weights["liquidity"] * score_components["liquidity"]
        + weights["time_decay"] * score_components["time_decay"]
        + weights["distance_to_risk"] * score_components["distance_to_risk"]
        + weights["pnl_health"] * score_components["pnl_health"]
    )
    if greek_health is not None:
        weighted += weights["greek_health"] * greek_health
        score_components["greek_health"] = greek_health
    else:
        weight_sum = (
            weights["structural"]
            + weights["liquidity"]
            + weights["time_decay"]
            + weights["distance_to_risk"]
            + weights["pnl_health"]
        )
        weighted = weighted / weight_sum if weight_sum else weighted

    health_score = _round_score(weighted)
    health_status = _health_status_from_score(health_score)
    if health_status is HealthStatus.CRITICAL and not any(
        i.severity in ("CRITICAL", "HIGH") for i in issues
    ):
        issues.append(
            HealthIssueRecord(
                issue_code="APME.HEALTH.GENERAL.CRITICAL",
                severity="CRITICAL",
                message="Composite health score in critical band.",
                dimension="composite",
            )
        )

    return PositionHealth(
        position_id=position.position_id,
        health_status=health_status,
        health_score=health_score,
        structural_integrity_score=score_components["structural"],
        liquidity_score=score_components["liquidity"],
        time_decay_score=score_components["time_decay"],
        distance_to_risk_score=score_components["distance_to_risk"],
        pnl_health_score=score_components["pnl_health"],
        greek_health_score=greek_health,
        issues=tuple(issues),
        health_fingerprint=compute_health_fingerprint(
            position.position_id, score_components, assessed_at
        ),
        assessed_at=assessed_at,
    )


def _max_urgency_normalized(exit_hints: tuple[ExitHint, ...]) -> float:
    """Normalize maximum urgency from exit hints."""
    if not exit_hints:
        return 0.0
    return max(_URGENCY_RANK[h.urgency] for h in exit_hints) / 4.0


def _vol_stress_factor(volatility_hints: VolatilityHints | None) -> float:
    """Compute volatility stress factor."""
    if volatility_hints is None:
        return 0.0
    regime = (volatility_hints.vix_regime or "").upper()
    mapping = {"LOW": 0.1, "NORMAL": 0.25, "ELEVATED": 0.6, "CRISIS": 1.0}
    return mapping.get(regime, 0.3)


def _time_pressure_factor(health: PositionHealth, horizon_minutes: int) -> float:
    """Compute time pressure factor from health time decay."""
    base = 1.0 - health.time_decay_score
    horizon_factor = 1.0 - min(horizon_minutes / 240.0, 1.0)
    return _round_score(base * 0.7 + horizon_factor * 0.3)


def compute_exit_probability(
    health: PositionHealth,
    exit_hints: tuple[ExitHint, ...],
    volatility_hints: VolatilityHints | None,
    horizon_minutes: int,
    config: APMEConfig,
) -> ExitProbability:
    """Deterministic logistic model over normalized factor vector."""
    factors = {
        "health_inverse": 1.0 - health.health_score,
        "exit_hint_count": min(len(exit_hints), 5) / 5.0,
        "max_urgency": _max_urgency_normalized(exit_hints),
        "vol_stress": _vol_stress_factor(volatility_hints),
        "time_pressure": _time_pressure_factor(health, horizon_minutes),
    }
    logit = sum(config.exit_prob_weights[k] * v for k, v in factors.items())
    probability = _round_score(1.0 / (1.0 + math.exp(-logit)))
    return ExitProbability(
        position_id=health.position_id,
        probability=probability,
        horizon_minutes=horizon_minutes,
        model_version=EXIT_PROB_MODEL_VERSION,
        contributing_factors=MappingProxyType(
            {k: round(v, PROB_ROUND_DECIMALS) for k, v in factors.items()}
        ),
        computed_at=health.assessed_at,
    )


def _engine_quality_score(
    position_context: APMEPositionContext,
    health: PositionHealth,
    config: APMEConfig,
    *,
    computed_at: datetime,
) -> PositionQualityScore:
    """Compute composite quality score for one position."""
    position = position_context.position
    weights = config.quality_weights

    profitability = _round_score(0.5 + (position.unrealized_pnl / max(abs(position.notional_exposure), 1.0)) * 0.5)
    profitability = _clamp(profitability)
    risk = _round_score(health.distance_to_risk_score * 0.5 + health.structural_integrity_score * 0.5)
    time_component = health.time_decay_score
    liquidity = health.liquidity_score
    structure = health.structural_integrity_score

    overall = (
        weights["profitability"] * profitability
        + weights["risk"] * risk
        + weights["time"] * time_component
        + weights["liquidity"] * liquidity
        + weights["structure"] * structure
    )
    components = {
        "profitability": profitability,
        "risk": risk,
        "time": time_component,
        "liquidity": liquidity,
        "structure": structure,
        "overall": _round_score(overall),
    }
    return PositionQualityScore(
        position_id=position.position_id,
        overall_score=components["overall"],
        profitability_component=profitability,
        risk_component=risk,
        time_component=time_component,
        liquidity_component=liquidity,
        structure_component=structure,
        rank_percentile=None,
        score_band=_quality_band(components["overall"]),
        score_fingerprint=compute_quality_fingerprint(
            position.position_id, components, computed_at
        ),
        computed_at=computed_at,
    )


def _engine_profit_protection(
    position_context: APMEPositionContext,
    config: APMEConfig,
    *,
    break_even_crossed: bool,
) -> ProfitProtectionDecision:
    """Evaluate profit protection triggers."""
    position = position_context.position
    metadata = position_context.signal_metadata
    decision_id = f"pp-{position.position_id[:8]}-{uuid.uuid4().hex[:8]}"
    target = metadata.target_hint if metadata else None

    if position.unrealized_pnl < 0 and not break_even_crossed:
        return ProfitProtectionDecision(
            decision_id=decision_id,
            position_id=position.position_id,
            recommended=False,
            protection_type=ProfitProtectionType.NONE,
            urgency=ActionUrgency.NONE,
            reason_codes=(),
        )

    max_profit = position.metadata.get("max_profit")
    if max_profit is not None:
        try:
            max_val = abs(float(max_profit))
            if max_val > 0 and position.unrealized_pnl / max_val >= 0.50:
                return ProfitProtectionDecision(
                    decision_id=decision_id,
                    position_id=position.position_id,
                    recommended=True,
                    protection_type=ProfitProtectionType.PARTIAL_PROFIT_LOCK,
                    urgency=ActionUrgency.MEDIUM,
                    reason_codes=("APME.PROFIT.LOCK.MILESTONE",),
                    activated=True,
                    lock_fraction=0.50,
                    explainability=(
                        _make_explainability(
                            engine_id=APMEEngineId.PROFIT_PROTECTION,
                            reason_code="APME.PROFIT.LOCK.MILESTONE",
                            message="Unrealized profit reached 50% of max profit.",
                            evidence={"unrealized_pnl": str(position.unrealized_pnl)},
                            weight=0.7,
                        ),
                    ),
                )
        except (TypeError, ValueError):
            pass

    if target and target.hint_type is TargetHintType.PREMIUM_DECAY_PERCENT and target.value:
        return ProfitProtectionDecision(
            decision_id=decision_id,
            position_id=position.position_id,
            recommended=True,
            protection_type=ProfitProtectionType.PREMIUM_DECAY_TARGET,
            urgency=ActionUrgency.LOW,
            reason_codes=("APME.PROFIT.DECAY.TARGET",),
            activated=True,
            premium_decay_target_pct=float(target.value),
            explainability=(
                _make_explainability(
                    engine_id=APMEEngineId.PROFIT_PROTECTION,
                    reason_code="APME.PROFIT.DECAY.TARGET",
                    message="Premium decay target milestone configured.",
                    evidence={"target_pct": str(target.value)},
                    weight=0.6,
                ),
            ),
        )

    if break_even_crossed and position.unrealized_pnl > 0:
        return ProfitProtectionDecision(
            decision_id=decision_id,
            position_id=position.position_id,
            recommended=True,
            protection_type=ProfitProtectionType.BREAK_EVEN_STOP,
            urgency=ActionUrgency.MEDIUM,
            reason_codes=("APME.PROFIT.BREAKEVEN.STOP",),
            activated=True,
            explainability=(
                _make_explainability(
                    engine_id=APMEEngineId.PROFIT_PROTECTION,
                    reason_code="APME.PROFIT.BREAKEVEN.STOP",
                    message="Break-even crossed with open profit.",
                    weight=0.65,
                ),
            ),
        )

    return ProfitProtectionDecision(
        decision_id=decision_id,
        position_id=position.position_id,
        recommended=False,
        protection_type=ProfitProtectionType.NONE,
        urgency=ActionUrgency.NONE,
        reason_codes=(),
    )


def _engine_dynamic_stop(
    position_context: APMEPositionContext,
    *,
    reference_time: datetime,
) -> tuple[DynamicStopState, ExitHint | None]:
    """Translate StopLossHint to live stop state."""
    position = position_context.position
    metadata = position_context.signal_metadata
    stop_hint = metadata.stop_loss_hint if metadata else None
    stop_type = StopLossHintType.NONE.value

    if stop_hint is None or stop_hint.hint_type is StopLossHintType.NONE:
        return (
            DynamicStopState(
                position_id=position.position_id,
                stop_active=False,
                stop_type=stop_type,
                last_updated_at=reference_time,
            ),
            None,
        )

    stop_type = stop_hint.hint_type.value
    breached = False
    stop_level: float | None = stop_hint.value
    distance_pct: float | None = None
    exit_hint: ExitHint | None = None

    if stop_hint.hint_type is StopLossHintType.UNDERLYING_LEVEL and stop_hint.value is not None:
        mark = position_context.underlying_mark
        if mark is not None:
            distance_pct = abs((mark - stop_hint.value) / stop_hint.value) * 100.0
            if position_context.is_short_premium:
                breached = mark >= stop_hint.value
            else:
                breached = mark <= stop_hint.value
    elif stop_hint.hint_type is StopLossHintType.PREMIUM_MULTIPLE and stop_hint.value is not None:
        entry = position.metadata.get("entry_premium")
        if entry is not None:
            try:
                loss = abs(min(position.unrealized_pnl, 0.0))
                breached = loss >= float(entry) * stop_hint.value
            except (TypeError, ValueError):
                pass
    elif stop_hint.hint_type is StopLossHintType.STRUCTURE_BREACH:
        strike_distance = position.metadata.get("short_strike_distance_pct")
        if strike_distance is not None:
            try:
                breached = abs(float(strike_distance)) < 0.1
            except (TypeError, ValueError):
                pass

    if breached:
        exit_hint = ExitHint(
            engine_id=APMEEngineId.DYNAMIC_STOP,
            exit_fraction=1.0,
            urgency=ActionUrgency.CRITICAL,
            trigger=ExitTriggerType.STOP_LOSS,
            reason_code="APME.STOP.BREACH.UNDERLYING_LEVEL",
            message="Dynamic stop condition breached.",
            evidence=MappingProxyType({"stop_type": stop_type}),
        )

    return (
        DynamicStopState(
            position_id=position.position_id,
            stop_active=True,
            stop_level_hint=stop_level,
            stop_basis=stop_hint.basis,
            stop_type=stop_type,
            breached=breached,
            distance_to_stop_pct=distance_pct,
            last_updated_at=reference_time,
        ),
        exit_hint,
    )


def _engine_break_even(position_context: APMEPositionContext) -> bool:
    """Detect break-even crossing."""
    prior = position_context.position.metadata.get("was_unprofitable", "false")
    was_unprofitable = prior.lower() == "true"
    currently_profitable = position_context.position.unrealized_pnl > 0
    return was_unprofitable and currently_profitable


def _engine_volatility_exit(
    position_context: APMEPositionContext,
    volatility_hints: VolatilityHints | None,
) -> ExitHint | None:
    """Evaluate volatility regime exit triggers."""
    if volatility_hints is None:
        return None
    regime = (volatility_hints.vix_regime or "NORMAL").upper()
    is_short = position_context.is_short_premium
    if regime == "CRISIS":
        fraction = 1.0 if is_short else 0.50
        return ExitHint(
            engine_id=APMEEngineId.VOLATILITY_EXIT,
            exit_fraction=fraction,
            urgency=ActionUrgency.HIGH,
            trigger=ExitTriggerType.VOLATILITY_SHIFT,
            reason_code="APME.VOL.EXIT.CRISIS_REGIME",
            message="VIX crisis regime triggered exit.",
            evidence=MappingProxyType({"vix_regime": regime}),
        )
    if regime == "ELEVATED" and is_short:
        return ExitHint(
            engine_id=APMEEngineId.VOLATILITY_EXIT,
            exit_fraction=0.25,
            urgency=ActionUrgency.MEDIUM,
            trigger=ExitTriggerType.VOLATILITY_SHIFT,
            reason_code="APME.VOL.EXIT.ELEVATED_REGIME",
            message="Elevated vol regime — tighten risk.",
            evidence=MappingProxyType({"vix_regime": regime}),
        )
    return None


def _engine_time_exit(
    position_context: APMEPositionContext,
    eval_context: APMEEvaluationContext,
    config: APMEConfig,
) -> ExitHint | None:
    """Evaluate time-based exit triggers."""
    dte = position_context.dte
    if (
        position_context.is_short_premium
        and dte is not None
        and dte <= config.min_dte_short_premium
    ):
        return ExitHint(
            engine_id=APMEEngineId.TIME_EXIT,
            exit_fraction=1.0,
            urgency=ActionUrgency.HIGH,
            trigger=ExitTriggerType.EXPIRY_APPROACH,
            reason_code="APME.TIME.EXIT.DTE_THRESHOLD",
            message="Days-to-expiry below minimum for short premium.",
            evidence=MappingProxyType({"dte": str(dte)}),
        )

    session = eval_context.session_context
    if session and session.minutes_to_close is not None:
        if session.minutes_to_close <= config.session_exit_minutes_before_close:
            return ExitHint(
                engine_id=APMEEngineId.TIME_EXIT,
                exit_fraction=1.0,
                urgency=ActionUrgency.HIGH,
                trigger=ExitTriggerType.TIME_DECAY,
                reason_code="APME.TIME.EXIT.SESSION_CUTOFF",
                message="Session cutoff approaching.",
                evidence=MappingProxyType(
                    {"minutes_to_close": str(session.minutes_to_close)}
                ),
            )

    metadata = position_context.signal_metadata
    if metadata and metadata.max_hold_minutes and position_context.position.opened_at:
        elapsed = (
            eval_context.reference_time - position_context.position.opened_at
        ).total_seconds() / 60.0
        if elapsed >= metadata.max_hold_minutes:
            return ExitHint(
                engine_id=APMEEngineId.TIME_EXIT,
                exit_fraction=1.0,
                urgency=ActionUrgency.MEDIUM,
                trigger=ExitTriggerType.TIME_DECAY,
                reason_code="APME.TIME.EXIT.MAX_HOLD",
                message="Maximum hold duration exceeded.",
                evidence=MappingProxyType({"elapsed_minutes": str(int(elapsed))}),
            )
    return None


def _engine_trend_reversal_exit(position_context: APMEPositionContext) -> ExitHint | None:
    """Evaluate trend reversal exit triggers."""
    trend = position_context.trend_hint
    if trend is None:
        return None
    if not trend.reversal_detected:
        return None
    strength = trend.trend_strength or 0.0
    if position_context.is_short_premium and strength > 0.7:
        return ExitHint(
            engine_id=APMEEngineId.TREND_REVERSAL_EXIT,
            exit_fraction=0.25,
            urgency=ActionUrgency.MEDIUM,
            trigger=ExitTriggerType.DELTA_BREACH,
            reason_code="APME.TREND.EXIT.REVERSAL",
            message="Adverse trend reversal detected for short premium.",
            evidence=MappingProxyType({"trend_strength": str(strength)}),
        )
    if strength > 0.5:
        return ExitHint(
            engine_id=APMEEngineId.TREND_REVERSAL_EXIT,
            exit_fraction=0.50,
            urgency=ActionUrgency.HIGH,
            trigger=ExitTriggerType.DELTA_BREACH,
            reason_code="APME.TREND.EXIT.REVERSAL",
            message="Trend reversal detected.",
            evidence=MappingProxyType({"trend_strength": str(strength)}),
        )
    return None


def _engine_news_exit(
    position_context: APMEPositionContext,
    news_flags: tuple[NewsEventFlag, ...],
    eval_context: APMEEvaluationContext,
    config: APMEConfig,
) -> ExitHint | None:
    """Evaluate news-driven exit hooks."""
    if not config.enable_news_exit_hooks:
        return None
    underlying = position_context.position.underlying.upper()
    ref_time = eval_context.reference_time
    for flag in news_flags:
        if ref_time < flag.valid_from:
            continue
        if flag.valid_until is not None and ref_time > flag.valid_until:
            continue
        if underlying not in {u.upper() for u in flag.affected_underlyings}:
            continue
        severity = flag.severity.upper()
        if severity == "CRITICAL":
            return ExitHint(
                engine_id=APMEEngineId.NEWS_EXIT,
                exit_fraction=1.0,
                urgency=ActionUrgency.CRITICAL,
                trigger=ExitTriggerType.MANUAL,
                reason_code="APME.NEWS.EXIT.CRITICAL",
                message=f"Critical news event: {flag.event_type}.",
                evidence=MappingProxyType({"event_id": flag.event_id}),
            )
        if severity == "HIGH":
            return ExitHint(
                engine_id=APMEEngineId.NEWS_EXIT,
                exit_fraction=0.50,
                urgency=ActionUrgency.HIGH,
                trigger=ExitTriggerType.MANUAL,
                reason_code="APME.NEWS.EXIT.HIGH_SEVERITY",
                message=f"High severity news: {flag.event_type}.",
                evidence=MappingProxyType({"event_id": flag.event_id}),
            )
        if severity == "MEDIUM":
            return ExitHint(
                engine_id=APMEEngineId.NEWS_EXIT,
                exit_fraction=0.25,
                urgency=ActionUrgency.MEDIUM,
                trigger=ExitTriggerType.MANUAL,
                reason_code="APME.NEWS.EXIT.MEDIUM_SEVERITY",
                message=f"Medium severity news: {flag.event_type}.",
                evidence=MappingProxyType({"event_id": flag.event_id}),
            )
    return None


def _engine_adjustment(position_context: APMEPositionContext) -> AdjustmentDecision:
    """Evaluate structure adjustment triggers."""
    position = position_context.position
    decision_id = f"adj-{position.position_id[:8]}-{uuid.uuid4().hex[:8]}"
    strike_distance = position.metadata.get("short_strike_distance_pct")
    if strike_distance is not None:
        try:
            if abs(float(strike_distance)) < 0.5:
                return AdjustmentDecision(
                    decision_id=decision_id,
                    position_id=position.position_id,
                    recommended=True,
                    adjustment_type=AdjustmentType.REDUCE_SHORT_WING,
                    adjustment_fraction=0.25,
                    urgency=ActionUrgency.HIGH,
                    reason_codes=("APME.ADJUST.WING.STRESS",),
                    wing_adjustment_hint="reduce_short_wing",
                    explainability=(
                        _make_explainability(
                            engine_id=APMEEngineId.ADJUSTMENT,
                            reason_code="APME.ADJUST.WING.STRESS",
                            message="Short strike within 0.5% of underlying.",
                            evidence={"distance_pct": strike_distance},
                            weight=0.7,
                        ),
                    ),
                )
        except (TypeError, ValueError):
            pass

    if position_context.greek_hint and position_context.greek_hint.delta is not None:
        if abs(position_context.greek_hint.delta) > 0.35:
            return AdjustmentDecision(
                decision_id=decision_id,
                position_id=position.position_id,
                recommended=True,
                adjustment_type=AdjustmentType.DELTA_NEUTRALIZE,
                adjustment_fraction=0.30,
                urgency=ActionUrgency.MEDIUM,
                reason_codes=("APME.ADJUST.DELTA.DRIFT",),
                target_delta_hint=0.0,
                explainability=(
                    _make_explainability(
                        engine_id=APMEEngineId.ADJUSTMENT,
                        reason_code="APME.ADJUST.DELTA.DRIFT",
                        message="Net delta drift exceeds policy threshold.",
                        evidence={"delta": str(position_context.greek_hint.delta)},
                        weight=0.65,
                    ),
                ),
            )

    return AdjustmentDecision(
        decision_id=decision_id,
        position_id=position.position_id,
        recommended=False,
        adjustment_type=AdjustmentType.NONE,
        adjustment_fraction=0.0,
        urgency=ActionUrgency.NONE,
        reason_codes=(),
    )


def _engine_rolling(
    position_context: APMEPositionContext,
    exit_hints: tuple[ExitHint, ...],
) -> RollDecision:
    """Evaluate roll vs exit preference."""
    position = position_context.position
    decision_id = f"roll-{position.position_id[:8]}-{uuid.uuid4().hex[:8]}"
    dte = position_context.dte
    has_time_pressure = any(h.engine_id is APMEEngineId.TIME_EXIT for h in exit_hints)
    losing = position.unrealized_pnl < 0

    if has_time_pressure and dte is not None and dte <= 2 and not losing:
        return RollDecision(
            decision_id=decision_id,
            position_id=position.position_id,
            recommended=True,
            roll_direction=RollDirection.OUT,
            roll_fraction=1.0,
            urgency=ActionUrgency.MEDIUM,
            reason_codes=("APME.ROLL.EXPIRY.APPROACH",),
            explainability=(
                _make_explainability(
                    engine_id=APMEEngineId.ROLLING,
                    reason_code="APME.ROLL.EXPIRY.APPROACH",
                    message="Profitable position near expiry — roll out preferred.",
                    evidence={"dte": str(dte)},
                    weight=0.7,
                ),
            ),
        )

    strike_distance = position.metadata.get("short_strike_distance_pct")
    if strike_distance is not None and not losing:
        try:
            if abs(float(strike_distance)) < 1.0:
                return RollDecision(
                    decision_id=decision_id,
                    position_id=position.position_id,
                    recommended=True,
                    roll_direction=RollDirection.UP if position.side.lower() == "short" else RollDirection.DOWN,
                    roll_fraction=1.0,
                    urgency=ActionUrgency.LOW,
                    reason_codes=("APME.ROLL.STRIKE.TESTED",),
                    explainability=(
                        _make_explainability(
                            engine_id=APMEEngineId.ROLLING,
                            reason_code="APME.ROLL.STRIKE.TESTED",
                            message="Strike tested but structure intact — roll considered.",
                            weight=0.55,
                        ),
                    ),
                )
        except (TypeError, ValueError):
            pass

    return RollDecision(
        decision_id=decision_id,
        position_id=position.position_id,
        recommended=False,
        roll_direction=RollDirection.NONE,
        roll_fraction=0.0,
        urgency=ActionUrgency.NONE,
        reason_codes=(),
    )


def _engine_hedging(
    snapshot: PortfolioSnapshot,
    position_context: APMEPositionContext | None,
    volatility_hints: VolatilityHints | None,
    config: APMEConfig,
) -> HedgeDecision | None:
    """Evaluate portfolio or position hedge triggers."""
    metrics = snapshot.metrics
    decision_id = f"hdg-{uuid.uuid4().hex[:12]}"
    equity = metrics.equity_hint or 1.0
    delta = metrics.portfolio_delta
    if delta is not None and abs(delta) > 0.30 * equity / max(equity, 1.0):
        return HedgeDecision(
            decision_id=decision_id,
            scope="PORTFOLIO",
            position_id=None,
            recommended=True,
            hedge_type=HedgeType.DELTA_HEDGE,
            urgency=ActionUrgency.MEDIUM,
            reason_codes=("APME.HEDGE.DELTA.BREACH",),
            explainability=(
                _make_explainability(
                    engine_id=APMEEngineId.HEDGING,
                    reason_code="APME.HEDGE.DELTA.BREACH",
                    message="Portfolio delta exceeds normalized threshold.",
                    evidence={"portfolio_delta": str(delta)},
                    weight=0.7,
                ),
            ),
        )

    if (
        volatility_hints
        and (volatility_hints.vix_regime or "").upper() == "CRISIS"
        and metrics.portfolio_vega is not None
        and abs(metrics.portfolio_vega) > 0
    ):
        return HedgeDecision(
            decision_id=decision_id,
            scope="PORTFOLIO",
            position_id=None,
            recommended=True,
            hedge_type=HedgeType.VEGA_HEDGE,
            urgency=ActionUrgency.HIGH,
            reason_codes=("APME.HEDGE.VEGA.CRISIS",),
            explainability=(
                _make_explainability(
                    engine_id=APMEEngineId.HEDGING,
                    reason_code="APME.HEDGE.VEGA.CRISIS",
                    message="Vega exposure in crisis regime.",
                    weight=0.75,
                ),
            ),
        )

    if position_context is not None:
        tail_score = position_context.position.metadata.get("tail_risk_score")
        if tail_score is not None:
            try:
                if float(tail_score) > 0.8:
                    return HedgeDecision(
                        decision_id=decision_id,
                        scope="POSITION",
                        position_id=position_context.position.position_id,
                        recommended=True,
                        hedge_type=HedgeType.TAIL_HEDGE,
                        urgency=ActionUrgency.HIGH,
                        reason_codes=("APME.HEDGE.TAIL.RISK",),
                        explainability=(
                            _make_explainability(
                                engine_id=APMEEngineId.HEDGING,
                                reason_code="APME.HEDGE.TAIL.RISK",
                                message="Tail risk score exceeds threshold.",
                                evidence={"tail_risk_score": tail_score},
                                weight=0.8,
                            ),
                        ),
                    )
            except (TypeError, ValueError):
                pass
    _ = config
    return None


def _engine_portfolio_protection(
    snapshot: PortfolioSnapshot,
    config: APMEConfig,
) -> list[PortfolioProtectionAction]:
    """Evaluate account-level portfolio protection triggers."""
    if not config.enable_portfolio_protection:
        return []
    actions: list[PortfolioProtectionAction] = []
    metrics = snapshot.metrics
    peak = metrics.peak_equity_hint
    equity = metrics.equity_hint
    if peak and peak > 0:
        drawdown_pct = ((peak - equity) / peak) * 100.0
        if drawdown_pct >= config.drawdown_halt_threshold_pct:
            actions.append(
                PortfolioProtectionAction(
                    action_id=f"ppa-{uuid.uuid4().hex[:12]}",
                    action_type="HALT_NEW_ENTRIES",
                    trigger_code="APME.PORTFOLIO.DRAWDOWN.LIMIT",
                    affected_scope="ACCOUNT",
                    target_reduction_pct=50.0,
                    urgency=ActionUrgency.CRITICAL,
                    explainability=(
                        _make_explainability(
                            engine_id=APMEEngineId.PORTFOLIO_PROTECTION,
                            reason_code="APME.PORTFOLIO.DRAWDOWN.LIMIT",
                            message="Drawdown halt threshold breached.",
                            evidence={"drawdown_pct": str(round(drawdown_pct, 2))},
                            weight=0.95,
                        ),
                    ),
                )
            )
        elif drawdown_pct >= config.drawdown_reduce_threshold_pct:
            actions.append(
                PortfolioProtectionAction(
                    action_id=f"ppa-{uuid.uuid4().hex[:12]}",
                    action_type="REDUCE_GROSS_EXPOSURE",
                    trigger_code="APME.PORTFOLIO.DRAWDOWN.REDUCE",
                    affected_scope="ACCOUNT",
                    target_reduction_pct=25.0,
                    urgency=ActionUrgency.HIGH,
                    explainability=(
                        _make_explainability(
                            engine_id=APMEEngineId.PORTFOLIO_PROTECTION,
                            reason_code="APME.PORTFOLIO.DRAWDOWN.REDUCE",
                            message="Drawdown reduce threshold breached.",
                            evidence={"drawdown_pct": str(round(drawdown_pct, 2))},
                            weight=0.8,
                        ),
                    ),
                )
            )

    margin_util = metrics.margin_utilization_pct
    if margin_util is not None and margin_util >= config.margin_stress_threshold_pct:
        actions.append(
            PortfolioProtectionAction(
                action_id=f"ppa-{uuid.uuid4().hex[:12]}",
                action_type="REDUCE_GROSS_EXPOSURE",
                trigger_code="APME.PORTFOLIO.MARGIN.STRESS",
                affected_scope="ACCOUNT",
                target_reduction_pct=30.0,
                urgency=ActionUrgency.HIGH,
                explainability=(
                    _make_explainability(
                        engine_id=APMEEngineId.PORTFOLIO_PROTECTION,
                        reason_code="APME.PORTFOLIO.MARGIN.STRESS",
                        message="Margin utilization stress threshold breached.",
                        evidence={"margin_utilization_pct": str(margin_util)},
                        weight=0.85,
                    ),
                ),
            )
        )

    if snapshot.exposure.largest_underlying_weight_pct >= config.underlying_concentration_limit_pct:
        actions.append(
            PortfolioProtectionAction(
                action_id=f"ppa-{uuid.uuid4().hex[:12]}",
                action_type="REDUCE_UNDERLYING_EXPOSURE",
                trigger_code="APME.PORTFOLIO.CONCENTRATION.UNDERLYING",
                affected_scope="UNDERLYING",
                target_reduction_pct=20.0,
                urgency=ActionUrgency.MEDIUM,
                explainability=(
                    _make_explainability(
                        engine_id=APMEEngineId.PORTFOLIO_PROTECTION,
                        reason_code="APME.PORTFOLIO.CONCENTRATION.UNDERLYING",
                        message="Underlying concentration limit breached.",
                        evidence={
                            "weight_pct": str(snapshot.exposure.largest_underlying_weight_pct)
                        },
                        weight=0.7,
                    ),
                ),
            )
        )
    return actions


def _engine_risk_escalation(
    portfolio_actions: list[PortfolioProtectionAction],
    partials: Mapping[str, _PositionPartialResults],
    config: APMEConfig,
) -> list[RiskEscalationRecord]:
    """Evaluate risk escalation triggers."""
    if not config.enable_risk_escalation:
        return []
    escalations: list[RiskEscalationRecord] = []
    critical_health_positions = [
        pid
        for pid, partial in partials.items()
        if partial.health and partial.health.health_status is HealthStatus.CRITICAL
    ]
    if len(critical_health_positions) >= 3:
        escalations.append(
            RiskEscalationRecord(
                escalation_id=f"esc-{uuid.uuid4().hex[:12]}",
                escalation_level="REVIEW_REQUIRED",
                trigger_code="APME.RISK.HEALTH.CLUSTER",
                position_ids=tuple(critical_health_positions),
                message="Cluster of critical health positions detected.",
                requires_human_ack=True,
            )
        )

    for action in portfolio_actions:
        if action.action_type == "HALT_NEW_ENTRIES":
            escalations.append(
                RiskEscalationRecord(
                    escalation_id=f"esc-{uuid.uuid4().hex[:12]}",
                    escalation_level="HALT",
                    trigger_code=action.trigger_code,
                    position_ids=(),
                    message="Portfolio protection triggered halt escalation.",
                    requires_human_ack=True,
                )
            )
        elif action.urgency is ActionUrgency.CRITICAL:
            escalations.append(
                RiskEscalationRecord(
                    escalation_id=f"esc-{uuid.uuid4().hex[:12]}",
                    escalation_level="REVIEW_REQUIRED",
                    trigger_code=action.trigger_code,
                    position_ids=(),
                    message="Critical portfolio protection action emitted.",
                    requires_human_ack=True,
                )
            )
    return escalations


def _engine_exit_intelligence(
    position_id: str,
    exit_hints: tuple[ExitHint, ...],
    roll: RollDecision | None,
) -> ExitDecision:
    """Synthesize exit recommendations from sub-engine exit hints."""
    decision_id = f"exit-{position_id[:8]}-{uuid.uuid4().hex[:8]}"
    if not exit_hints:
        return ExitDecision(
            decision_id=decision_id,
            position_id=position_id,
            recommended=False,
            exit_trigger=ExitTriggerType.NOT_APPLICABLE,
            exit_fraction=0.0,
            urgency=ActionUrgency.NONE,
            trigger_engine=APMEEngineId.EXIT_INTELLIGENCE,
            reason_codes=(),
        )

    dominant = max(
        exit_hints,
        key=lambda h: (_URGENCY_RANK[h.urgency], h.exit_fraction, h.reason_code),
    )
    exit_fraction = _round_fraction(max(h.exit_fraction for h in exit_hints))
    explainability = tuple(
        _make_explainability(
            engine_id=h.engine_id,
            reason_code=h.reason_code,
            message=h.message,
            evidence=h.evidence,
            weight=0.5 + h.exit_fraction * 0.3,
        )
        for h in sorted(exit_hints, key=lambda x: (-x.exit_fraction, x.reason_code))
    )
    roll_alt = roll if roll and roll.recommended and exit_fraction < 1.0 else None
    return ExitDecision(
        decision_id=decision_id,
        position_id=position_id,
        recommended=exit_fraction > 0,
        exit_trigger=dominant.trigger,
        exit_fraction=exit_fraction,
        urgency=dominant.urgency,
        trigger_engine=dominant.engine_id,
        reason_codes=tuple(sorted({h.reason_code for h in exit_hints})),
        roll_alternative=roll_alt,
        explainability=explainability,
    )


@dataclass(frozen=True)
class _ArbitrationCandidate:
    """Internal arbitration candidate."""

    priority: int
    action: ManagementAction
    urgency: ActionUrgency
    reason_code: str
    engine_id: APMEEngineId


def _arbitrate_position_decision(
    position_context: APMEPositionContext,
    partial: _PositionPartialResults,
    escalations: tuple[RiskEscalationRecord, ...],
    portfolio_actions: tuple[PortfolioProtectionAction, ...],
) -> tuple[ManagementAction, ActionUrgency, tuple[ExplainabilityRecord, ...], Mapping[APMEEngineId, float]]:
    """Resolve conflicting engine recommendations deterministically."""
    candidates: list[_ArbitrationCandidate] = []
    position_id = position_context.position.position_id

    if any(position_id in esc.position_ids for esc in escalations):
        candidates.append(
            _ArbitrationCandidate(1, ManagementAction.ESCALATE, ActionUrgency.CRITICAL, "APME.RISK.ESCALATE", APMEEngineId.RISK_ESCALATION)
        )
    if any(a.urgency is ActionUrgency.CRITICAL for a in portfolio_actions):
        candidates.append(
            _ArbitrationCandidate(2, ManagementAction.PARTIAL_EXIT, ActionUrgency.CRITICAL, "APME.PORTFOLIO.PROTECT", APMEEngineId.PORTFOLIO_PROTECTION)
        )

    for hint in partial.exit_hints:
        if hint.engine_id is APMEEngineId.NEWS_EXIT and hint.urgency is ActionUrgency.CRITICAL:
            candidates.append(
                _ArbitrationCandidate(3, ManagementAction.FULL_EXIT, hint.urgency, hint.reason_code, hint.engine_id)
            )
        elif hint.engine_id is APMEEngineId.DYNAMIC_STOP and hint.exit_fraction >= 1.0:
            candidates.append(
                _ArbitrationCandidate(4, ManagementAction.FULL_EXIT, hint.urgency, hint.reason_code, hint.engine_id)
            )
        elif hint.engine_id is APMEEngineId.VOLATILITY_EXIT and hint.exit_fraction >= 1.0:
            action = ManagementAction.FULL_EXIT if hint.exit_fraction >= 1.0 else ManagementAction.HEDGE
            candidates.append(
                _ArbitrationCandidate(5, action, hint.urgency, hint.reason_code, hint.engine_id)
            )
        elif hint.engine_id is APMEEngineId.TIME_EXIT:
            action = ManagementAction.ROLL if partial.roll and partial.roll.recommended else ManagementAction.FULL_EXIT
            candidates.append(
                _ArbitrationCandidate(6, action, hint.urgency, hint.reason_code, hint.engine_id)
            )
        elif hint.engine_id is APMEEngineId.TREND_REVERSAL_EXIT:
            action = ManagementAction.FULL_EXIT if hint.exit_fraction >= 1.0 else ManagementAction.PARTIAL_EXIT
            candidates.append(
                _ArbitrationCandidate(7, action, hint.urgency, hint.reason_code, hint.engine_id)
            )

    if partial.exit_decision and partial.exit_decision.recommended:
        action = ManagementAction.FULL_EXIT if partial.exit_decision.exit_fraction >= 1.0 else ManagementAction.PARTIAL_EXIT
        candidates.append(
            _ArbitrationCandidate(
                8,
                action,
                partial.exit_decision.urgency,
                partial.exit_decision.reason_codes[0] if partial.exit_decision.reason_codes else "APME.EXIT.SYNTH",
                APMEEngineId.EXIT_INTELLIGENCE,
            )
        )

    if partial.profit_protection and partial.profit_protection.recommended:
        candidates.append(
            _ArbitrationCandidate(
                9,
                ManagementAction.PROTECT_PROFIT,
                partial.profit_protection.urgency,
                partial.profit_protection.reason_codes[0] if partial.profit_protection.reason_codes else "APME.PROFIT.PROTECT",
                APMEEngineId.PROFIT_PROTECTION,
            )
        )
    if partial.roll and partial.roll.recommended:
        candidates.append(
            _ArbitrationCandidate(
                10,
                ManagementAction.ROLL,
                partial.roll.urgency,
                partial.roll.reason_codes[0] if partial.roll.reason_codes else "APME.ROLL.RECOMMEND",
                APMEEngineId.ROLLING,
            )
        )
    if partial.adjustment and partial.adjustment.recommended:
        candidates.append(
            _ArbitrationCandidate(
                11,
                ManagementAction.ADJUST,
                partial.adjustment.urgency,
                partial.adjustment.reason_codes[0] if partial.adjustment.reason_codes else "APME.ADJUST.RECOMMEND",
                APMEEngineId.ADJUSTMENT,
            )
        )
    if partial.hedge and partial.hedge.recommended and partial.hedge.position_id == position_id:
        candidates.append(
            _ArbitrationCandidate(
                12,
                ManagementAction.HEDGE,
                partial.hedge.urgency,
                partial.hedge.reason_codes[0] if partial.hedge.reason_codes else "APME.HEDGE.RECOMMEND",
                APMEEngineId.HEDGING,
            )
        )

    if not candidates:
        monitor = partial.health and partial.health.health_status in (HealthStatus.WATCH, HealthStatus.STRESSED)
        action = ManagementAction.MONITOR if monitor else ManagementAction.HOLD
        urgency = ActionUrgency.LOW if monitor else ActionUrgency.NONE
        explainability = ()
        if monitor and partial.health:
            explainability = (
                _make_explainability(
                    engine_id=APMEEngineId.POSITION_HEALTH,
                    reason_code="APME.HEALTH.WATCH",
                    message="Position health warrants elevated monitoring.",
                    weight=0.4,
                ),
            )
        contributions = MappingProxyType({APMEEngineId.POSITION_HEALTH: partial.health.health_score if partial.health else 0.5})
        return action, urgency, explainability, contributions

    winner = min(
        candidates,
        key=lambda c: (c.priority, -_URGENCY_RANK[c.urgency], c.reason_code),
    )
    explainability: list[ExplainabilityRecord] = []
    if partial.exit_decision and partial.exit_decision.explainability:
        explainability.extend(partial.exit_decision.explainability)
    if partial.profit_protection and partial.profit_protection.explainability:
        explainability.extend(partial.profit_protection.explainability)
    if partial.adjustment and partial.adjustment.explainability:
        explainability.extend(partial.adjustment.explainability)
    if partial.roll and partial.roll.explainability:
        explainability.extend(partial.roll.explainability)
    if not explainability:
        explainability.append(
            _make_explainability(
                engine_id=winner.engine_id,
                reason_code=winner.reason_code,
                message=f"Arbitration selected {winner.action.value}.",
                weight=0.8,
            )
        )
    contributions: dict[APMEEngineId, float] = {}
    if partial.health:
        contributions[APMEEngineId.POSITION_HEALTH] = partial.health.health_score
    if partial.quality_score:
        contributions[APMEEngineId.QUALITY_SCORE] = partial.quality_score.overall_score
    if partial.exit_probability:
        contributions[APMEEngineId.EXIT_PROBABILITY] = partial.exit_probability.probability
    contributions[winner.engine_id] = max(contributions.get(winner.engine_id, 0.0), 0.7)
    return winner.action, winner.urgency, tuple(explainability), MappingProxyType(contributions)


def _assemble_explainability(
    partial: _PositionPartialResults,
    arbitration_records: tuple[ExplainabilityRecord, ...],
) -> tuple[ExplainabilityRecord, ...]:
    """Merge engine partial explainability into ordered evidence chain."""
    records: list[ExplainabilityRecord] = list(arbitration_records)
    for item in partial.profit_protection.explainability if partial.profit_protection else ():
        records.append(item)
    for item in partial.adjustment.explainability if partial.adjustment else ():
        records.append(item)
    for item in partial.roll.explainability if partial.roll else ():
        records.append(item)
    if partial.exit_decision:
        records.extend(partial.exit_decision.explainability)
    deduped = {r.record_id: r for r in records}
    return tuple(sorted(deduped.values(), key=lambda r: (-r.weight, r.reason_code)))


def _hydrate_portfolio_context(
    snapshot: PortfolioSnapshot,
    eval_context: APMEEvaluationContext,
    config: APMEConfig,
    warnings: list[APMEWarningRecord],
) -> APMEPortfolioContext:
    """Build hydrated portfolio and per-position contexts."""
    position_contexts: dict[str, APMEPositionContext] = {}
    for position in snapshot.positions:
        mark = eval_context.price_hints.get(position.instrument_key)
        if mark is None:
            warnings.append(
                APMEWarningRecord(
                    code=WARN_HINT_MISSING,
                    message="No mark price hint for position.",
                    position_id=position.position_id,
                    stage_id=APMEEvaluationStageId.CONTEXT_HYDRATION,
                )
            )
        greek_hint = eval_context.greek_hints.get(position.position_id)
        if greek_hint and config.hint_max_age_seconds >= 0:
            age = (eval_context.reference_time - greek_hint.as_of).total_seconds()
            if age > config.hint_max_age_seconds:
                warnings.append(
                    APMEWarningRecord(
                        code=WARN_HINT_STALE,
                        message="Greek hint is stale.",
                        position_id=position.position_id,
                        stage_id=APMEEvaluationStageId.CONTEXT_HYDRATION,
                    )
                )
        signal_meta = eval_context.signal_metadata.get(position.position_id)
        if signal_meta is None:
            warnings.append(
                APMEWarningRecord(
                    code=WARN_SIGNAL_METADATA_MISSING,
                    message="No signal metadata for position.",
                    position_id=position.position_id,
                    stage_id=APMEEvaluationStageId.CONTEXT_HYDRATION,
                )
            )
        elif config.require_signal_metadata and signal_meta.signal_id is None:
            warnings.append(
                APMEWarningRecord(
                    code=WARN_SIGNAL_METADATA_MISSING,
                    message="Signal metadata incomplete.",
                    position_id=position.position_id,
                    stage_id=APMEEvaluationStageId.CONTEXT_HYDRATION,
                )
            )
        trend_hint = eval_context.trend_hints.get(position.underlying)
        dte = _derive_dte(position, eval_context.reference_time)
        group_id = position.metadata.get("position_group_id")
        position_contexts[position.position_id] = APMEPositionContext(
            position=position,
            mark_price=mark,
            underlying_mark=eval_context.underlying_marks.get(position.underlying),
            greek_hint=greek_hint,
            signal_metadata=signal_meta,
            trend_hint=trend_hint,
            dte=dte,
            is_short_premium=_is_short_premium(position),
            position_group_id=group_id,
        )
    if eval_context.volatility_hints and config.hint_max_age_seconds >= 0:
        age = (eval_context.reference_time - eval_context.volatility_hints.as_of).total_seconds()
        if age > config.hint_max_age_seconds:
            warnings.append(
                APMEWarningRecord(
                    code=WARN_HINT_STALE,
                    message="Volatility hints are stale.",
                    stage_id=APMEEvaluationStageId.CONTEXT_HYDRATION,
                )
            )
    return APMEPortfolioContext(
        snapshot=snapshot,
        eval_context=eval_context,
        position_contexts=MappingProxyType(position_contexts),
    )


def _apply_rank_percentiles(partials: dict[str, _PositionPartialResults]) -> None:
    """Compute rank percentiles across open positions."""
    scores = [
        (pid, partial.quality_score.overall_score)
        for pid, partial in partials.items()
        if partial.quality_score is not None
    ]
    if len(scores) <= 1:
        return
    ordered = sorted(scores, key=lambda item: item[1])
    n = len(ordered)
    for rank, (pid, _) in enumerate(ordered):
        percentile = round(rank / (n - 1), SCORE_ROUND_DECIMALS)
        qs = partials[pid].quality_score
        assert qs is not None
        partials[pid] = replace(
            partials[pid],
            quality_score=replace(qs, rank_percentile=percentile),
        )


def _build_position_decision(
    position_context: APMEPositionContext,
    partial: _PositionPartialResults,
    report_id: str,
    escalations: tuple[RiskEscalationRecord, ...],
    portfolio_actions: tuple[PortfolioProtectionAction, ...],
) -> PositionManagementDecision:
    """Assemble final PositionManagementDecision for one position."""
    position = position_context.position
    assert partial.health is not None
    assert partial.quality_score is not None
    assert partial.exit_probability is not None

    primary_action, urgency, arb_expl, contributions = _arbitrate_position_decision(
        position_context,
        partial,
        escalations,
        portfolio_actions,
    )
    if any(
        esc.escalation_level in ("HALT", "REVIEW_REQUIRED") and not esc.position_ids
        for esc in escalations
    ):
        primary_action = ManagementAction.ESCALATE
        urgency = ActionUrgency.CRITICAL

    explainability = _assemble_explainability(partial, arb_expl)
    sub_fps = {
        "health": partial.health.health_fingerprint,
        "quality": partial.quality_score.score_fingerprint,
        "exit_prob": str(partial.exit_probability.probability),
    }
    return PositionManagementDecision(
        decision_id=_generate_decision_id(position.position_id, report_id),
        position_id=position.position_id,
        position_group_id=position_context.position_group_id,
        instrument_key=position.instrument_key,
        underlying=position.underlying,
        strategy_id=position.strategy_id,
        strategy_family=position.strategy_family,
        primary_action=primary_action,
        action_urgency=urgency,
        health=partial.health,
        quality_score=partial.quality_score,
        exit_probability=partial.exit_probability,
        exit_decision=partial.exit_decision,
        adjustment_decision=partial.adjustment,
        profit_protection_decision=partial.profit_protection,
        roll_decision=partial.roll,
        hedge_decision=partial.hedge if partial.hedge and partial.hedge.position_id == position.position_id else None,
        stop_state=partial.stop_state,
        explainability=explainability,
        engine_contributions=contributions,
        decision_fingerprint=compute_decision_fingerprint(
            position.position_id, primary_action, MappingProxyType(sub_fps)
        ),
    )


def _empty_report(
    run_state: _PipelineRunState,
    *,
    pipeline_summary: APMEPipelineResult,
) -> APMEDecisionReport:
    """Build empty or rejected report."""
    completed_at = run_state.context.reference_time
    if not _is_timezone_aware(completed_at):
        completed_at = run_state.started_at
    duration_ms = (completed_at - run_state.started_at).total_seconds() * 1000.0
    return APMEDecisionReport(
        report_id=run_state.report_id,
        correlation_id=run_state.context.correlation_id,
        source_portfolio_snapshot_id=run_state.snapshot.snapshot_id,
        as_of=run_state.context.reference_time,
        account_id=run_state.context.account_id,
        status=run_state.status,
        decisions=(),
        group_decisions=(),
        portfolio_actions=(),
        escalations=(),
        pipeline_summary=pipeline_summary,
        warnings=tuple(run_state.warnings),
        errors=tuple(run_state.errors),
        primary_error_code=run_state.primary_error_code,
        submitted_at=run_state.started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        report_fingerprint="",
    )


class APMEEvaluationPipeline:
    """Stateless ordered multi-stage APME evaluation executor."""

    def execute(
        self,
        run_state: _PipelineRunState,
        config: APMEConfig,
        *,
        event_bus: EventBus | None = None,
        applied_snapshots: set[str] | None = None,
        prior_report: APMEDecisionReport | None = None,
    ) -> APMEDecisionReport:
        """Execute full evaluation pipeline."""
        publisher = _EventPublisher(
            event_bus,
            enabled=config.publish_lifecycle_events,
            report_id=run_state.report_id,
            correlation_id=run_state.context.correlation_id,
        )
        stages: list[APMEStageResult] = []
        short_circuit = False

        for stage_id in STAGE_ORDER:
            if short_circuit and stage_id not in (
                APMEEvaluationStageId.REPORT_ASSEMBLY,
                APMEEvaluationStageId.OUTPUT_VALIDATION,
            ):
                if run_state.idempotent_noop:
                    continue
            stage_started = time.perf_counter()
            outcome = self._run_stage(stage_id, run_state, applied_snapshots or set(), prior_report)
            duration_ms = (time.perf_counter() - stage_started) * 1000.0
            stages.append(
                APMEStageResult(
                    stage_id=stage_id,
                    passed=outcome.passed,
                    rejection_code=outcome.rejection_code,
                    message=outcome.message,
                    duration_ms=duration_ms,
                    details=outcome.details,
                )
            )
            if not outcome.passed and stage_id is APMEEvaluationStageId.INPUT_GATE:
                run_state.pre_eval_rejected = True
                run_state.status = APMEEvaluationStatus.REJECTED
                run_state.primary_error_code = outcome.rejection_code
                run_state.errors.append(
                    APMEErrorRecord(
                        code=outcome.rejection_code or ERROR_RESULT_INVALID,
                        message=outcome.message or "Stage failed.",
                        stage_id=stage_id,
                    )
                )
                publisher.publish(
                    APMEEventType.EVALUATION_REJECTED,
                    occurred_at=run_state.context.reference_time,
                    metadata=MappingProxyType(
                        {"error_code": outcome.rejection_code or ERROR_RESULT_INVALID}
                    ),
                )
                short_circuit = True
            elif run_state.idempotent_noop and stage_id is APMEEvaluationStageId.SNAPSHOT_INTEGRITY:
                short_circuit = True

        pipeline_summary = APMEPipelineResult(
            total_stages=len(stages),
            passed_stages=sum(1 for stage in stages if stage.passed),
            failed_stage_id=next((stage.stage_id for stage in stages if not stage.passed), None),
            stages=tuple(stages),
            short_circuited=short_circuit,
        )

        if run_state.pre_eval_rejected:
            report = _empty_report(run_state, pipeline_summary=pipeline_summary)
            publisher.flush()
            return report

        if run_state.idempotent_noop and prior_report is not None:
            run_state.status = APMEEvaluationStatus.NOOP
            report = replace(
                prior_report,
                status=APMEEvaluationStatus.NOOP,
                pipeline_summary=pipeline_summary,
                submitted_at=run_state.started_at,
                completed_at=run_state.context.reference_time,
            )
            publisher.publish(
                APMEEventType.EVALUATION_COMPLETED,
                occurred_at=run_state.context.reference_time,
                report=report,
                metadata=MappingProxyType({"status": report.status.value}),
            )
            publisher.flush()
            return report

        if not run_state.pre_eval_rejected:
            publisher.publish(
                APMEEventType.EVALUATION_RECEIVED,
                occurred_at=run_state.context.reference_time,
                metadata=MappingProxyType(
                    {
                        "portfolio_snapshot_id": run_state.snapshot.snapshot_id,
                        "position_count": str(len(run_state.snapshot.positions)),
                    }
                ),
            )

        completed_at = run_state.context.reference_time
        duration_ms = (completed_at - run_state.started_at).total_seconds() * 1000.0

        if run_state.warnings and run_state.status is APMEEvaluationStatus.COMPLETED:
            run_state.status = APMEEvaluationStatus.PARTIAL

        completed_at = run_state.context.reference_time
        duration_ms = (completed_at - run_state.started_at).total_seconds() * 1000.0

        report = APMEDecisionReport(
            report_id=run_state.report_id,
            correlation_id=run_state.context.correlation_id,
            source_portfolio_snapshot_id=run_state.snapshot.snapshot_id,
            as_of=run_state.context.reference_time,
            account_id=run_state.context.account_id,
            status=run_state.status,
            decisions=tuple(run_state.decisions),
            group_decisions=tuple(run_state.group_decisions),
            portfolio_actions=tuple(run_state.portfolio_actions),
            escalations=tuple(run_state.escalations),
            pipeline_summary=pipeline_summary,
            warnings=tuple(run_state.warnings),
            errors=tuple(run_state.errors),
            primary_error_code=run_state.primary_error_code,
            submitted_at=run_state.started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            report_fingerprint="",
        )
        if config.deterministic_fingerprint:
            report = replace(
                report,
                report_fingerprint=compute_report_fingerprint(
                    run_state.snapshot, report, config
                ),
            )

        if config.strict_output_validation:
            validation = validate_apme_decision_report(report)
            if not validation.is_valid:
                run_state.primary_error_code = validation.errors[0].code
                report = replace(
                    report,
                    status=APMEEvaluationStatus.FAILED,
                    errors=report.errors + validation.errors,
                    primary_error_code=validation.errors[0].code,
                )
            elif config.deterministic_fingerprint and report.report_fingerprint:
                recomputed = compute_report_fingerprint(run_state.snapshot, report, config)
                if recomputed != report.report_fingerprint:
                    mismatch = APMEErrorRecord(
                        code=ERROR_RESULT_FINGERPRINT_MISMATCH,
                        message="Report fingerprint mismatch.",
                        stage_id=APMEEvaluationStageId.OUTPUT_VALIDATION,
                    )
                    report = replace(
                        report,
                        status=APMEEvaluationStatus.FAILED,
                        errors=report.errors + (mismatch,),
                        primary_error_code=ERROR_RESULT_FINGERPRINT_MISMATCH,
                    )

        publisher.publish(
            APMEEventType.EVALUATION_COMPLETED,
            occurred_at=completed_at,
            report=report,
            metadata=MappingProxyType({"status": report.status.value, "duration_ms": str(report.duration_ms)}),
        )
        publisher.publish(
            APMEEventType.REPORT_PUBLISHED,
            occurred_at=completed_at,
            report=report,
            metadata=MappingProxyType(
                {"report_id": report.report_id, "decision_count": str(len(report.decisions))}
            ),
        )
        for decision in report.decisions:
            if decision.primary_action is not ManagementAction.HOLD:
                publisher.publish(
                    APMEEventType.DECISION_PUBLISHED,
                    occurred_at=completed_at,
                    report=report,
                    position_id=decision.position_id,
                    metadata=MappingProxyType(
                        {
                            "position_id": decision.position_id,
                            "primary_action": decision.primary_action.value,
                        }
                    ),
                )
            if decision.exit_decision and decision.exit_decision.recommended:
                publisher.publish(
                    APMEEventType.EXIT_RECOMMENDED,
                    occurred_at=completed_at,
                    report=report,
                    position_id=decision.position_id,
                    metadata=MappingProxyType(
                        {
                            "exit_fraction": str(decision.exit_decision.exit_fraction),
                            "trigger": decision.exit_decision.exit_trigger.value,
                        }
                    ),
                )
            prior_status = run_state.prior_health_status.get(decision.position_id)
            if prior_status and prior_status != decision.health.health_status:
                if decision.health.health_status in (
                    HealthStatus.STRESSED,
                    HealthStatus.CRITICAL,
                ):
                    publisher.publish(
                        APMEEventType.HEALTH_DEGRADED,
                        occurred_at=completed_at,
                        report=report,
                        position_id=decision.position_id,
                        metadata=MappingProxyType(
                            {
                                "prior_status": prior_status.value,
                                "new_status": decision.health.health_status.value,
                            }
                        ),
                    )
            publisher.publish(
                APMEEventType.QUALITY_SCORE_UPDATED,
                occurred_at=completed_at,
                report=report,
                position_id=decision.position_id,
                metadata=MappingProxyType(
                    {
                        "overall_score": str(decision.quality_score.overall_score),
                        "band": decision.quality_score.score_band.value,
                    }
                ),
            )
        for action in report.portfolio_actions:
            publisher.publish(
                APMEEventType.PORTFOLIO_PROTECTION_TRIGGERED,
                occurred_at=completed_at,
                report=report,
                metadata=MappingProxyType(
                    {"action_type": action.action_type, "trigger_code": action.trigger_code}
                ),
            )
        for escalation in report.escalations:
            publisher.publish(
                APMEEventType.RISK_ESCALATED,
                occurred_at=completed_at,
                report=report,
                metadata=MappingProxyType(
                    {
                        "escalation_level": escalation.escalation_level,
                        "trigger_code": escalation.trigger_code,
                    }
                ),
            )
        publisher.flush()
        return report

    def _run_stage(
        self,
        stage_id: APMEEvaluationStageId,
        run_state: _PipelineRunState,
        applied_snapshots: set[str],
        prior_report: APMEDecisionReport | None,
    ) -> _StageOutcome:
        """Execute one pipeline stage."""
        if run_state.idempotent_noop and stage_id not in (
            APMEEvaluationStageId.REPORT_ASSEMBLY,
            APMEEvaluationStageId.OUTPUT_VALIDATION,
        ):
            return _StageOutcome(passed=True)

        handlers = {
            APMEEvaluationStageId.INPUT_GATE: lambda: _stage_input_gate(run_state),
            APMEEvaluationStageId.SNAPSHOT_INTEGRITY: lambda: _stage_snapshot_integrity(
                run_state, applied_snapshots, prior_report
            ),
            APMEEvaluationStageId.CONTEXT_HYDRATION: lambda: _stage_context_hydration(run_state),
            APMEEvaluationStageId.POSITION_HEALTH: lambda: _stage_position_health(run_state),
            APMEEvaluationStageId.QUALITY_SCORING: lambda: _stage_quality_scoring(run_state),
            APMEEvaluationStageId.EXIT_PROBABILITY: lambda: _stage_exit_probability(run_state),
            APMEEvaluationStageId.PROFIT_PROTECTION: lambda: _stage_profit_protection(run_state),
            APMEEvaluationStageId.DYNAMIC_STOP: lambda: _stage_dynamic_stop(run_state),
            APMEEvaluationStageId.BREAK_EVEN: lambda: _stage_break_even(run_state),
            APMEEvaluationStageId.VOLATILITY_EXIT: lambda: _stage_volatility_exit(run_state),
            APMEEvaluationStageId.TIME_EXIT: lambda: _stage_time_exit(run_state),
            APMEEvaluationStageId.TREND_REVERSAL_EXIT: lambda: _stage_trend_reversal_exit(run_state),
            APMEEvaluationStageId.NEWS_EXIT_HOOKS: lambda: _stage_news_exit(run_state),
            APMEEvaluationStageId.ADJUSTMENT_INTELLIGENCE: lambda: _stage_adjustment(run_state),
            APMEEvaluationStageId.ROLLING_INTELLIGENCE: lambda: _stage_rolling(run_state),
            APMEEvaluationStageId.HEDGING_INTELLIGENCE: lambda: _stage_hedging(run_state),
            APMEEvaluationStageId.PORTFOLIO_PROTECTION: lambda: _stage_portfolio_protection(run_state),
            APMEEvaluationStageId.RISK_ESCALATION: lambda: _stage_risk_escalation(run_state),
            APMEEvaluationStageId.DECISION_ARBITRATION: lambda: _stage_decision_arbitration(run_state),
            APMEEvaluationStageId.EXPLAINABILITY_ASSEMBLY: lambda: _StageOutcome(passed=True),
            APMEEvaluationStageId.REPORT_ASSEMBLY: lambda: _StageOutcome(passed=True),
            APMEEvaluationStageId.OUTPUT_VALIDATION: lambda: _StageOutcome(passed=True),
        }
        handler = handlers.get(stage_id)
        if handler is None:
            return _StageOutcome(
                passed=False,
                rejection_code=ERROR_RESULT_INVALID,
                message="Unknown stage.",
            )
        return handler()


def _stage_input_gate(run_state: _PipelineRunState) -> _StageOutcome:
    """Validate evaluation inputs."""
    if run_state.snapshot is None:
        return _StageOutcome(
            passed=False,
            rejection_code=ERROR_SNAPSHOT_MISSING,
            message="PortfolioSnapshot is required.",
        )
    validation = validate_evaluation_context(
        run_state.context, run_state.snapshot, run_state.config
    )
    if not validation.is_valid:
        primary = validation.errors[0]
        return _StageOutcome(
            passed=False,
            rejection_code=primary.code,
            message=primary.message,
        )
    run_state.warnings.extend(
        APMEWarningRecord(
            code=w.code,
            message=w.message,
            field=w.field,
            stage_id=APMEEvaluationStageId.INPUT_GATE,
        )
        for w in validation.warnings
    )
    return _StageOutcome(passed=True)


def _stage_snapshot_integrity(
    run_state: _PipelineRunState,
    applied_snapshots: set[str],
    prior_report: APMEDecisionReport | None,
) -> _StageOutcome:
    """Validate portfolio snapshot integrity."""
    snapshot = run_state.snapshot
    if not snapshot.snapshot_id:
        return _StageOutcome(
            passed=False,
            rejection_code=ERROR_SNAPSHOT_INVALID,
            message="snapshot_id must be non-empty.",
        )
    if len(snapshot.positions) != snapshot.metrics.open_position_count:
        return _StageOutcome(
            passed=False,
            rejection_code=ERROR_SNAPSHOT_INVALID,
            message="open_position_count inconsistent with positions length.",
        )
    if (
        run_state.config.idempotent_evaluate
        and snapshot.snapshot_fingerprint
        and snapshot.snapshot_fingerprint in applied_snapshots
        and prior_report is not None
    ):
        run_state.idempotent_noop = True
        run_state.status = APMEEvaluationStatus.NOOP
    if run_state.config.deterministic_fingerprint and not snapshot.snapshot_fingerprint:
        run_state.warnings.append(
            APMEWarningRecord(
                code=ERROR_SNAPSHOT_INVALID,
                message="snapshot_fingerprint missing.",
                stage_id=APMEEvaluationStageId.SNAPSHOT_INTEGRITY,
            )
        )
    return _StageOutcome(passed=True)


def _stage_context_hydration(run_state: _PipelineRunState) -> _StageOutcome:
    """Hydrate per-position and portfolio contexts."""
    run_state.portfolio_context = _hydrate_portfolio_context(
        run_state.snapshot,
        run_state.context,
        run_state.config,
        run_state.warnings,
    )
    for position in run_state.snapshot.positions:
        run_state.partials[position.position_id] = _PositionPartialResults()
    return _StageOutcome(passed=True)


def _stage_position_health(run_state: _PipelineRunState) -> _StageOutcome:
    """Run position health engine for all positions."""
    assert run_state.portfolio_context is not None
    ref_time = run_state.context.reference_time
    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        health = compute_position_health(pctx, run_state.config, reference_time=ref_time)
        run_state.partials[pid] = replace(run_state.partials[pid], health=health)
        _logger.debug(
            "apme.engine.health",
            extra={"event": "apme.engine.health", "position_id": pid, "score": health.health_score},
        )
    return _StageOutcome(passed=True)


def _stage_quality_scoring(run_state: _PipelineRunState) -> _StageOutcome:
    """Run quality scoring engine."""
    assert run_state.portfolio_context is not None
    ref_time = run_state.context.reference_time
    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        partial = run_state.partials[pid]
        assert partial.health is not None
        qs = _engine_quality_score(pctx, partial.health, run_state.config, computed_at=ref_time)
        run_state.partials[pid] = replace(partial, quality_score=qs)
    _apply_rank_percentiles(run_state.partials)
    return _StageOutcome(passed=True)


def _stage_exit_probability(run_state: _PipelineRunState) -> _StageOutcome:
    """Run exit probability engine."""
    vol = run_state.context.volatility_hints
    horizon = run_state.config.exit_probability_horizon_minutes
    for pid, partial in run_state.partials.items():
        assert partial.health is not None
        ep = compute_exit_probability(partial.health, partial.exit_hints, vol, horizon, run_state.config)
        run_state.partials[pid] = replace(partial, exit_probability=ep)
    return _StageOutcome(passed=True)


def _stage_profit_protection(run_state: _PipelineRunState) -> _StageOutcome:
    """Run profit protection engine."""
    assert run_state.portfolio_context is not None
    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        partial = run_state.partials[pid]
        pp = _engine_profit_protection(
            pctx, run_state.config, break_even_crossed=partial.break_even_crossed
        )
        run_state.partials[pid] = replace(partial, profit_protection=pp)
    return _StageOutcome(passed=True)


def _stage_dynamic_stop(run_state: _PipelineRunState) -> _StageOutcome:
    """Run dynamic stop management engine."""
    assert run_state.portfolio_context is not None
    ref_time = run_state.context.reference_time
    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        partial = run_state.partials[pid]
        stop_state, hint = _engine_dynamic_stop(pctx, reference_time=ref_time)
        hints = partial.exit_hints + ((hint,) if hint else ())
        run_state.partials[pid] = replace(partial, stop_state=stop_state, exit_hints=hints)
    return _StageOutcome(passed=True)


def _stage_break_even(run_state: _PipelineRunState) -> _StageOutcome:
    """Run break-even detection engine."""
    assert run_state.portfolio_context is not None
    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        partial = run_state.partials[pid]
        crossed = _engine_break_even(pctx)
        run_state.partials[pid] = replace(partial, break_even_crossed=crossed)
    return _StageOutcome(passed=True)


def _stage_volatility_exit(run_state: _PipelineRunState) -> _StageOutcome:
    """Run volatility exit engine."""
    assert run_state.portfolio_context is not None
    vol = run_state.context.volatility_hints
    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        partial = run_state.partials[pid]
        hint = _engine_volatility_exit(pctx, vol)
        hints = partial.exit_hints + ((hint,) if hint else ())
        run_state.partials[pid] = replace(partial, exit_hints=hints)
    return _StageOutcome(passed=True)


def _stage_time_exit(run_state: _PipelineRunState) -> _StageOutcome:
    """Run time exit engine."""
    assert run_state.portfolio_context is not None
    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        partial = run_state.partials[pid]
        hint = _engine_time_exit(pctx, run_state.context, run_state.config)
        hints = partial.exit_hints + ((hint,) if hint else ())
        run_state.partials[pid] = replace(partial, exit_hints=hints)
    return _StageOutcome(passed=True)


def _stage_trend_reversal_exit(run_state: _PipelineRunState) -> _StageOutcome:
    """Run trend reversal exit engine."""
    assert run_state.portfolio_context is not None
    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        partial = run_state.partials[pid]
        hint = _engine_trend_reversal_exit(pctx)
        hints = partial.exit_hints + ((hint,) if hint else ())
        run_state.partials[pid] = replace(partial, exit_hints=hints)
    return _StageOutcome(passed=True)


def _stage_news_exit(run_state: _PipelineRunState) -> _StageOutcome:
    """Run news exit hooks engine."""
    assert run_state.portfolio_context is not None
    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        partial = run_state.partials[pid]
        hint = _engine_news_exit(
            pctx, run_state.context.news_flags, run_state.context, run_state.config
        )
        hints = partial.exit_hints + ((hint,) if hint else ())
        run_state.partials[pid] = replace(partial, exit_hints=hints)
    return _StageOutcome(passed=True)


def _stage_adjustment(run_state: _PipelineRunState) -> _StageOutcome:
    """Run adjustment intelligence engine."""
    assert run_state.portfolio_context is not None
    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        partial = run_state.partials[pid]
        adj = _engine_adjustment(pctx)
        run_state.partials[pid] = replace(partial, adjustment=adj)
    return _StageOutcome(passed=True)


def _stage_rolling(run_state: _PipelineRunState) -> _StageOutcome:
    """Run rolling intelligence engine."""
    assert run_state.portfolio_context is not None
    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        partial = run_state.partials[pid]
        roll = _engine_rolling(pctx, partial.exit_hints)
        run_state.partials[pid] = replace(partial, roll=roll)
    return _StageOutcome(passed=True)


def _stage_hedging(run_state: _PipelineRunState) -> _StageOutcome:
    """Run hedging intelligence engine."""
    assert run_state.portfolio_context is not None
    portfolio_hedge = _engine_hedging(
        run_state.snapshot, None, run_state.context.volatility_hints, run_state.config
    )
    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        partial = run_state.partials[pid]
        hedge = _engine_hedging(
            run_state.snapshot, pctx, run_state.context.volatility_hints, run_state.config
        )
        if hedge is None:
            hedge = portfolio_hedge
        run_state.partials[pid] = replace(partial, hedge=hedge)
    return _StageOutcome(passed=True)


def _stage_portfolio_protection(run_state: _PipelineRunState) -> _StageOutcome:
    """Run portfolio protection engine."""
    run_state.portfolio_actions = _engine_portfolio_protection(
        run_state.snapshot, run_state.config
    )
    return _StageOutcome(passed=True)


def _stage_risk_escalation(run_state: _PipelineRunState) -> _StageOutcome:
    """Run risk escalation engine."""
    run_state.escalations = _engine_risk_escalation(
        run_state.portfolio_actions, run_state.partials, run_state.config
    )
    return _StageOutcome(passed=True)


def _stage_decision_arbitration(run_state: _PipelineRunState) -> _StageOutcome:
    """Synthesize exit intelligence and assemble position decisions."""
    assert run_state.portfolio_context is not None
    escalations_tuple = tuple(run_state.escalations)
    actions_tuple = tuple(run_state.portfolio_actions)

    for pid, pctx in run_state.portfolio_context.position_contexts.items():
        partial = run_state.partials[pid]
        assert partial.health is not None
        ep = compute_exit_probability(
            partial.health,
            partial.exit_hints,
            run_state.context.volatility_hints,
            run_state.config.exit_probability_horizon_minutes,
            run_state.config,
        )
        partial = replace(partial, exit_probability=ep)
        exit_decision = _engine_exit_intelligence(pid, partial.exit_hints, partial.roll)
        partial = replace(partial, exit_decision=exit_decision)
        run_state.partials[pid] = partial
        decision = _build_position_decision(
            pctx, partial, run_state.report_id, escalations_tuple, actions_tuple
        )
        run_state.decisions.append(decision)
        _logger.debug(
            "apme.engine.arbitration",
            extra={
                "event": "apme.engine.arbitration",
                "position_id": pid,
                "primary_action": decision.primary_action.value,
            },
        )

    group_map: dict[str, list[str]] = {}
    for pctx in run_state.portfolio_context.position_contexts.values():
        if pctx.position_group_id:
            group_map.setdefault(pctx.position_group_id, []).append(pctx.position.position_id)
    for group_id, position_ids in group_map.items():
        group_decisions = [d for d in run_state.decisions if d.position_id in position_ids]
        if not group_decisions:
            continue
        net_health = sum(d.health.health_score for d in group_decisions) / len(group_decisions)
        primary_actions = [d.primary_action for d in group_decisions]
        group_action = (
            ManagementAction.ESCALATE
            if ManagementAction.ESCALATE in primary_actions
            else max(primary_actions, key=lambda a: list(ManagementAction).index(a))
        )
        run_state.group_decisions.append(
            PositionGroupDecision(
                group_id=group_id,
                position_ids=tuple(sorted(position_ids)),
                primary_action=group_action,
                net_health_score=_round_score(net_health),
                explainability=(),
            )
        )
    return _StageOutcome(passed=True)


class _EventPublisher:
    """Lifecycle event publisher with graceful no-op when bus absent."""

    def __init__(
        self,
        event_bus: EventBus | None,
        *,
        enabled: bool,
        report_id: str,
        correlation_id: str,
    ) -> None:
        self._event_bus = event_bus
        self._enabled = enabled and event_bus is not None
        self._report_id = report_id
        self._correlation_id = correlation_id
        self._pending: list[APMEEvent] = []

    def publish(
        self,
        event_type: APMEEventType,
        *,
        occurred_at: datetime,
        report: APMEDecisionReport | None = None,
        position_id: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Queue lifecycle event for ordered dispatch."""
        event = APMEEvent(
            event_type=event_type,
            topic=event_type.topic,
            report_id=self._report_id,
            correlation_id=self._correlation_id,
            occurred_at=occurred_at,
            report=report,
            position_id=position_id,
            metadata=MappingProxyType(dict(metadata or {})),
        )
        self._pending.append(event)

    def flush(self) -> tuple[APMEEvent, ...]:
        """Publish queued events in order."""
        if not self._enabled or self._event_bus is None:
            self._pending.clear()
            return ()
        published: list[APMEEvent] = []
        for event in self._pending:
            envelope = EventEnvelope(
                event_id=str(uuid.uuid4()),
                topic=event.topic,
                payload=event,
                correlation_id=self._correlation_id,
                producer=PRODUCER_NAME,
                occurred_at=event.occurred_at,
                published_at=_utc_now(),
                producer_version=APME_VERSION,
                payload_type="APMEEvent",
            )
            self._event_bus.publish(envelope)
            published.append(event)
        self._pending.clear()
        return tuple(published)


def _explainability_to_dict(record: ExplainabilityRecord) -> dict[str, Any]:
    """Serialize explainability record."""
    return {
        "record_id": record.record_id,
        "engine_id": record.engine_id.value,
        "reason_code": record.reason_code,
        "message": record.message,
        "evidence": dict(sorted(record.evidence.items())),
        "weight": record.weight,
    }


def _explainability_from_dict(data: Mapping[str, Any]) -> ExplainabilityRecord:
    """Deserialize explainability record."""
    return ExplainabilityRecord(
        record_id=str(data["record_id"]),
        engine_id=APMEEngineId(str(data["engine_id"])),
        reason_code=str(data["reason_code"]),
        message=str(data["message"]),
        evidence=MappingProxyType(dict(data.get("evidence", {}))),
        weight=float(data["weight"]),
    )


def _health_to_dict(health: PositionHealth) -> dict[str, Any]:
    """Serialize position health."""
    return {
        "position_id": health.position_id,
        "health_status": health.health_status.value,
        "health_score": health.health_score,
        "structural_integrity_score": health.structural_integrity_score,
        "liquidity_score": health.liquidity_score,
        "time_decay_score": health.time_decay_score,
        "distance_to_risk_score": health.distance_to_risk_score,
        "pnl_health_score": health.pnl_health_score,
        "greek_health_score": health.greek_health_score,
        "issues": [
            {
                "issue_code": i.issue_code,
                "severity": i.severity,
                "message": i.message,
                "dimension": i.dimension,
            }
            for i in health.issues
        ],
        "health_fingerprint": health.health_fingerprint,
        "assessed_at": _datetime_to_iso(health.assessed_at),
    }


def _health_from_dict(data: Mapping[str, Any]) -> PositionHealth:
    """Deserialize position health."""
    return PositionHealth(
        position_id=str(data["position_id"]),
        health_status=HealthStatus(str(data["health_status"])),
        health_score=float(data["health_score"]),
        structural_integrity_score=float(data["structural_integrity_score"]),
        liquidity_score=float(data["liquidity_score"]),
        time_decay_score=float(data["time_decay_score"]),
        distance_to_risk_score=float(data["distance_to_risk_score"]),
        pnl_health_score=float(data["pnl_health_score"]),
        greek_health_score=(
            float(data["greek_health_score"]) if data.get("greek_health_score") is not None else None
        ),
        issues=tuple(
            HealthIssueRecord(
                issue_code=str(i["issue_code"]),
                severity=str(i["severity"]),
                message=str(i["message"]),
                dimension=str(i["dimension"]),
            )
            for i in data.get("issues", [])
        ),
        health_fingerprint=str(data["health_fingerprint"]),
        assessed_at=_datetime_from_iso(str(data["assessed_at"])),
    )


def _quality_to_dict(score: PositionQualityScore) -> dict[str, Any]:
    """Serialize quality score."""
    return {
        "position_id": score.position_id,
        "overall_score": score.overall_score,
        "profitability_component": score.profitability_component,
        "risk_component": score.risk_component,
        "time_component": score.time_component,
        "liquidity_component": score.liquidity_component,
        "structure_component": score.structure_component,
        "rank_percentile": score.rank_percentile,
        "score_band": score.score_band.value,
        "score_fingerprint": score.score_fingerprint,
        "computed_at": _datetime_to_iso(score.computed_at),
    }


def _quality_from_dict(data: Mapping[str, Any]) -> PositionQualityScore:
    """Deserialize quality score."""
    return PositionQualityScore(
        position_id=str(data["position_id"]),
        overall_score=float(data["overall_score"]),
        profitability_component=float(data["profitability_component"]),
        risk_component=float(data["risk_component"]),
        time_component=float(data["time_component"]),
        liquidity_component=float(data["liquidity_component"]),
        structure_component=float(data["structure_component"]),
        rank_percentile=(
            float(data["rank_percentile"]) if data.get("rank_percentile") is not None else None
        ),
        score_band=QualityScoreBand(str(data["score_band"])),
        score_fingerprint=str(data["score_fingerprint"]),
        computed_at=_datetime_from_iso(str(data["computed_at"])),
    )


def _exit_probability_to_dict(ep: ExitProbability) -> dict[str, Any]:
    """Serialize exit probability."""
    return {
        "position_id": ep.position_id,
        "probability": ep.probability,
        "horizon_minutes": ep.horizon_minutes,
        "model_version": ep.model_version,
        "contributing_factors": dict(sorted(ep.contributing_factors.items())),
        "computed_at": _datetime_to_iso(ep.computed_at),
    }


def _exit_probability_from_dict(data: Mapping[str, Any]) -> ExitProbability:
    """Deserialize exit probability."""
    return ExitProbability(
        position_id=str(data["position_id"]),
        probability=float(data["probability"]),
        horizon_minutes=int(data["horizon_minutes"]),
        model_version=str(data["model_version"]),
        contributing_factors=MappingProxyType(dict(data.get("contributing_factors", {}))),
        computed_at=_datetime_from_iso(str(data["computed_at"])),
    )


def _exit_decision_to_dict(decision: ExitDecision) -> dict[str, Any]:
    """Serialize exit decision."""
    return {
        "decision_id": decision.decision_id,
        "position_id": decision.position_id,
        "recommended": decision.recommended,
        "exit_trigger": decision.exit_trigger.value,
        "exit_fraction": decision.exit_fraction,
        "urgency": decision.urgency.value,
        "trigger_engine": decision.trigger_engine.value,
        "reason_codes": list(decision.reason_codes),
        "target_exit_by": (
            _datetime_to_iso(decision.target_exit_by) if decision.target_exit_by else None
        ),
        "roll_alternative": (
            _roll_decision_to_dict(decision.roll_alternative)
            if decision.roll_alternative
            else None
        ),
        "explainability": [_explainability_to_dict(r) for r in decision.explainability],
    }


def _roll_decision_to_dict(decision: RollDecision) -> dict[str, Any]:
    """Serialize roll decision."""
    return {
        "decision_id": decision.decision_id,
        "position_id": decision.position_id,
        "recommended": decision.recommended,
        "roll_direction": decision.roll_direction.value,
        "target_expiry": decision.target_expiry,
        "target_strike_hint": decision.target_strike_hint,
        "roll_fraction": decision.roll_fraction,
        "urgency": decision.urgency.value,
        "reason_codes": list(decision.reason_codes),
        "explainability": [_explainability_to_dict(r) for r in decision.explainability],
    }


def _roll_decision_from_dict(data: Mapping[str, Any]) -> RollDecision:
    """Deserialize roll decision."""
    return RollDecision(
        decision_id=str(data["decision_id"]),
        position_id=str(data["position_id"]),
        recommended=bool(data["recommended"]),
        roll_direction=RollDirection(str(data["roll_direction"])),
        roll_fraction=float(data["roll_fraction"]),
        urgency=ActionUrgency(str(data["urgency"])),
        reason_codes=tuple(str(c) for c in data.get("reason_codes", [])),
        explainability=tuple(
            _explainability_from_dict(item) for item in data.get("explainability", [])
        ),
        target_expiry=str(data["target_expiry"]) if data.get("target_expiry") else None,
        target_strike_hint=(
            float(data["target_strike_hint"]) if data.get("target_strike_hint") is not None else None
        ),
    )


def _exit_decision_from_dict(data: Mapping[str, Any]) -> ExitDecision:
    """Deserialize exit decision."""
    roll_raw = data.get("roll_alternative")
    target_raw = data.get("target_exit_by")
    return ExitDecision(
        decision_id=str(data["decision_id"]),
        position_id=str(data["position_id"]),
        recommended=bool(data["recommended"]),
        exit_trigger=ExitTriggerType(str(data["exit_trigger"])),
        exit_fraction=float(data["exit_fraction"]),
        urgency=ActionUrgency(str(data["urgency"])),
        trigger_engine=APMEEngineId(str(data["trigger_engine"])),
        reason_codes=tuple(str(c) for c in data.get("reason_codes", [])),
        explainability=tuple(
            _explainability_from_dict(item) for item in data.get("explainability", [])
        ),
        target_exit_by=_datetime_from_iso(str(target_raw)) if target_raw else None,
        roll_alternative=_roll_decision_from_dict(roll_raw) if roll_raw else None,
    )


def _adjustment_to_dict(decision: AdjustmentDecision) -> dict[str, Any]:
    """Serialize adjustment decision."""
    return {
        "decision_id": decision.decision_id,
        "position_id": decision.position_id,
        "recommended": decision.recommended,
        "adjustment_type": decision.adjustment_type.value,
        "adjustment_fraction": decision.adjustment_fraction,
        "target_delta_hint": decision.target_delta_hint,
        "wing_adjustment_hint": decision.wing_adjustment_hint,
        "urgency": decision.urgency.value,
        "reason_codes": list(decision.reason_codes),
        "explainability": [_explainability_to_dict(r) for r in decision.explainability],
    }


def _adjustment_from_dict(data: Mapping[str, Any]) -> AdjustmentDecision:
    """Deserialize adjustment decision."""
    return AdjustmentDecision(
        decision_id=str(data["decision_id"]),
        position_id=str(data["position_id"]),
        recommended=bool(data["recommended"]),
        adjustment_type=AdjustmentType(str(data["adjustment_type"])),
        adjustment_fraction=float(data["adjustment_fraction"]),
        urgency=ActionUrgency(str(data["urgency"])),
        reason_codes=tuple(str(c) for c in data.get("reason_codes", [])),
        explainability=tuple(
            _explainability_from_dict(item) for item in data.get("explainability", [])
        ),
        target_delta_hint=(
            float(data["target_delta_hint"]) if data.get("target_delta_hint") is not None else None
        ),
        wing_adjustment_hint=(
            str(data["wing_adjustment_hint"]) if data.get("wing_adjustment_hint") else None
        ),
    )


def _profit_protection_to_dict(decision: ProfitProtectionDecision) -> dict[str, Any]:
    """Serialize profit protection decision."""
    return {
        "decision_id": decision.decision_id,
        "position_id": decision.position_id,
        "recommended": decision.recommended,
        "protection_type": decision.protection_type.value,
        "trail_level_hint": decision.trail_level_hint,
        "lock_fraction": decision.lock_fraction,
        "premium_decay_target_pct": decision.premium_decay_target_pct,
        "activated": decision.activated,
        "urgency": decision.urgency.value,
        "reason_codes": list(decision.reason_codes),
        "explainability": [_explainability_to_dict(r) for r in decision.explainability],
    }


def _profit_protection_from_dict(data: Mapping[str, Any]) -> ProfitProtectionDecision:
    """Deserialize profit protection decision."""
    return ProfitProtectionDecision(
        decision_id=str(data["decision_id"]),
        position_id=str(data["position_id"]),
        recommended=bool(data["recommended"]),
        protection_type=ProfitProtectionType(str(data["protection_type"])),
        urgency=ActionUrgency(str(data["urgency"])),
        reason_codes=tuple(str(c) for c in data.get("reason_codes", [])),
        activated=bool(data.get("activated", False)),
        explainability=tuple(
            _explainability_from_dict(item) for item in data.get("explainability", [])
        ),
        trail_level_hint=(
            float(data["trail_level_hint"]) if data.get("trail_level_hint") is not None else None
        ),
        lock_fraction=(
            float(data["lock_fraction"]) if data.get("lock_fraction") is not None else None
        ),
        premium_decay_target_pct=(
            float(data["premium_decay_target_pct"])
            if data.get("premium_decay_target_pct") is not None
            else None
        ),
    )


def position_management_decision_to_dict(
    decision: PositionManagementDecision,
) -> dict[str, Any]:
    """Convert position management decision to serializable dictionary."""
    return {
        "decision_id": decision.decision_id,
        "position_id": decision.position_id,
        "position_group_id": decision.position_group_id,
        "instrument_key": decision.instrument_key,
        "underlying": decision.underlying,
        "strategy_id": decision.strategy_id,
        "strategy_family": decision.strategy_family.value,
        "primary_action": decision.primary_action.value,
        "action_urgency": decision.action_urgency.value,
        "health": _health_to_dict(decision.health),
        "quality_score": _quality_to_dict(decision.quality_score),
        "exit_probability": _exit_probability_to_dict(decision.exit_probability),
        "exit_decision": (
            _exit_decision_to_dict(decision.exit_decision) if decision.exit_decision else None
        ),
        "adjustment_decision": (
            _adjustment_to_dict(decision.adjustment_decision)
            if decision.adjustment_decision
            else None
        ),
        "profit_protection_decision": (
            _profit_protection_to_dict(decision.profit_protection_decision)
            if decision.profit_protection_decision
            else None
        ),
        "roll_decision": (
            _roll_decision_to_dict(decision.roll_decision) if decision.roll_decision else None
        ),
        "stop_state": (
            {
                "position_id": decision.stop_state.position_id,
                "stop_active": decision.stop_state.stop_active,
                "stop_level_hint": decision.stop_state.stop_level_hint,
                "stop_basis": decision.stop_state.stop_basis,
                "stop_type": decision.stop_state.stop_type,
                "breached": decision.stop_state.breached,
                "distance_to_stop_pct": decision.stop_state.distance_to_stop_pct,
                "last_updated_at": _datetime_to_iso(decision.stop_state.last_updated_at),
            }
            if decision.stop_state
            else None
        ),
        "explainability": [_explainability_to_dict(r) for r in decision.explainability],
        "engine_contributions": {
            k.value: v for k, v in sorted(decision.engine_contributions.items(), key=lambda x: x[0].value)
        },
        "decision_fingerprint": decision.decision_fingerprint,
        "cooldown_until": (
            _datetime_to_iso(decision.cooldown_until) if decision.cooldown_until else None
        ),
    }


def position_management_decision_from_dict(
    data: Mapping[str, Any],
) -> PositionManagementDecision:
    """Deserialize position management decision."""
    exit_raw = data.get("exit_decision")
    adj_raw = data.get("adjustment_decision")
    pp_raw = data.get("profit_protection_decision")
    roll_raw = data.get("roll_decision")
    stop_raw = data.get("stop_state")
    cooldown_raw = data.get("cooldown_until")
    contributions = {
        APMEEngineId(k): float(v)
        for k, v in data.get("engine_contributions", {}).items()
    }
    stop_state = None
    if stop_raw:
        stop_state = DynamicStopState(
            position_id=str(stop_raw["position_id"]),
            stop_active=bool(stop_raw["stop_active"]),
            stop_type=str(stop_raw["stop_type"]),
            last_updated_at=_datetime_from_iso(str(stop_raw["last_updated_at"])),
            stop_level_hint=(
                float(stop_raw["stop_level_hint"])
                if stop_raw.get("stop_level_hint") is not None
                else None
            ),
            stop_basis=str(stop_raw["stop_basis"]) if stop_raw.get("stop_basis") else None,
            breached=bool(stop_raw.get("breached", False)),
            distance_to_stop_pct=(
                float(stop_raw["distance_to_stop_pct"])
                if stop_raw.get("distance_to_stop_pct") is not None
                else None
            ),
        )
    return PositionManagementDecision(
        decision_id=str(data["decision_id"]),
        position_id=str(data["position_id"]),
        position_group_id=str(data["position_group_id"]) if data.get("position_group_id") else None,
        instrument_key=str(data["instrument_key"]),
        underlying=str(data["underlying"]),
        strategy_id=str(data["strategy_id"]),
        strategy_family=StrategyFamily(str(data["strategy_family"])),
        primary_action=ManagementAction(str(data["primary_action"])),
        action_urgency=ActionUrgency(str(data["action_urgency"])),
        health=_health_from_dict(data["health"]),
        quality_score=_quality_from_dict(data["quality_score"]),
        exit_probability=_exit_probability_from_dict(data["exit_probability"]),
        exit_decision=_exit_decision_from_dict(exit_raw) if exit_raw else None,
        adjustment_decision=_adjustment_from_dict(adj_raw) if adj_raw else None,
        profit_protection_decision=_profit_protection_from_dict(pp_raw) if pp_raw else None,
        roll_decision=_roll_decision_from_dict(roll_raw) if roll_raw else None,
        hedge_decision=None,
        stop_state=stop_state,
        explainability=tuple(
            _explainability_from_dict(item) for item in data.get("explainability", [])
        ),
        engine_contributions=MappingProxyType(contributions),
        decision_fingerprint=str(data["decision_fingerprint"]),
        cooldown_until=_datetime_from_iso(str(cooldown_raw)) if cooldown_raw else None,
    )


def apme_decision_report_to_dict(report: APMEDecisionReport) -> dict[str, Any]:
    """Convert APME decision report to serializable dictionary."""
    return {
        "schema_version": APME_SCHEMA_VERSION,
        "report_id": report.report_id,
        "correlation_id": report.correlation_id,
        "source_portfolio_snapshot_id": report.source_portfolio_snapshot_id,
        "as_of": _datetime_to_iso(report.as_of),
        "account_id": report.account_id,
        "status": report.status.value,
        "decisions": [position_management_decision_to_dict(d) for d in report.decisions],
        "group_decisions": [
            {
                "group_id": g.group_id,
                "position_ids": list(g.position_ids),
                "primary_action": g.primary_action.value,
                "net_health_score": g.net_health_score,
                "explainability": [_explainability_to_dict(r) for r in g.explainability],
                "group_exit_decision": (
                    _exit_decision_to_dict(g.group_exit_decision) if g.group_exit_decision else None
                ),
            }
            for g in report.group_decisions
        ],
        "portfolio_actions": [
            {
                "action_id": a.action_id,
                "action_type": a.action_type,
                "trigger_code": a.trigger_code,
                "affected_scope": a.affected_scope,
                "target_reduction_pct": a.target_reduction_pct,
                "urgency": a.urgency.value,
                "explainability": [_explainability_to_dict(r) for r in a.explainability],
            }
            for a in report.portfolio_actions
        ],
        "escalations": [
            {
                "escalation_id": e.escalation_id,
                "escalation_level": e.escalation_level,
                "trigger_code": e.trigger_code,
                "position_ids": list(e.position_ids),
                "message": e.message,
                "requires_human_ack": e.requires_human_ack,
            }
            for e in report.escalations
        ],
        "pipeline_summary": {
            "total_stages": report.pipeline_summary.total_stages,
            "passed_stages": report.pipeline_summary.passed_stages,
            "failed_stage_id": (
                report.pipeline_summary.failed_stage_id.value
                if report.pipeline_summary.failed_stage_id
                else None
            ),
            "stages": [
                {
                    "stage_id": s.stage_id.value,
                    "passed": s.passed,
                    "rejection_code": s.rejection_code,
                    "message": s.message,
                    "duration_ms": s.duration_ms,
                }
                for s in report.pipeline_summary.stages
            ],
            "short_circuited": report.pipeline_summary.short_circuited,
        },
        "warnings": [
            {
                "code": w.code,
                "message": w.message,
                "stage_id": w.stage_id.value if w.stage_id else None,
                "field": w.field,
                "position_id": w.position_id,
            }
            for w in report.warnings
        ],
        "errors": [
            {
                "code": e.code,
                "message": e.message,
                "stage_id": e.stage_id.value if e.stage_id else None,
                "field": e.field,
                "position_id": e.position_id,
            }
            for e in report.errors
        ],
        "primary_error_code": report.primary_error_code,
        "submitted_at": _datetime_to_iso(report.submitted_at),
        "completed_at": _datetime_to_iso(report.completed_at) if report.completed_at else None,
        "duration_ms": report.duration_ms,
        "report_fingerprint": report.report_fingerprint,
    }


def apme_decision_report_from_dict(data: Mapping[str, Any]) -> APMEDecisionReport:
    """Deserialize APME decision report from dictionary."""
    schema = data.get("schema_version")
    if schema != APME_SCHEMA_VERSION:
        raise APMEValidationError(
            f"Unsupported schema version: {schema}",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
        )
    pipeline_data = data["pipeline_summary"]
    stages = tuple(
        APMEStageResult(
            stage_id=APMEEvaluationStageId(item["stage_id"]),
            passed=bool(item["passed"]),
            rejection_code=str(item["rejection_code"]) if item.get("rejection_code") else None,
            message=str(item["message"]) if item.get("message") else None,
            duration_ms=float(item["duration_ms"]),
        )
        for item in pipeline_data["stages"]
    )
    pipeline = APMEPipelineResult(
        total_stages=int(pipeline_data["total_stages"]),
        passed_stages=int(pipeline_data["passed_stages"]),
        failed_stage_id=(
            APMEEvaluationStageId(pipeline_data["failed_stage_id"])
            if pipeline_data.get("failed_stage_id")
            else None
        ),
        stages=stages,
        short_circuited=bool(pipeline_data["short_circuited"]),
    )
    completed_raw = data.get("completed_at")
    return APMEDecisionReport(
        report_id=str(data["report_id"]),
        correlation_id=str(data["correlation_id"]),
        source_portfolio_snapshot_id=str(data["source_portfolio_snapshot_id"]),
        as_of=_datetime_from_iso(str(data["as_of"])),
        account_id=str(data["account_id"]),
        status=APMEEvaluationStatus(str(data["status"])),
        decisions=tuple(
            position_management_decision_from_dict(item) for item in data.get("decisions", [])
        ),
        group_decisions=tuple(
            PositionGroupDecision(
                group_id=str(g["group_id"]),
                position_ids=tuple(str(p) for p in g["position_ids"]),
                primary_action=ManagementAction(str(g["primary_action"])),
                net_health_score=float(g["net_health_score"]),
                explainability=tuple(
                    _explainability_from_dict(r) for r in g.get("explainability", [])
                ),
                group_exit_decision=(
                    _exit_decision_from_dict(g["group_exit_decision"])
                    if g.get("group_exit_decision")
                    else None
                ),
            )
            for g in data.get("group_decisions", [])
        ),
        portfolio_actions=tuple(
            PortfolioProtectionAction(
                action_id=str(a["action_id"]),
                action_type=str(a["action_type"]),
                trigger_code=str(a["trigger_code"]),
                affected_scope=str(a["affected_scope"]),
                urgency=ActionUrgency(str(a["urgency"])),
                explainability=tuple(
                    _explainability_from_dict(r) for r in a.get("explainability", [])
                ),
                target_reduction_pct=(
                    float(a["target_reduction_pct"])
                    if a.get("target_reduction_pct") is not None
                    else None
                ),
            )
            for a in data.get("portfolio_actions", [])
        ),
        escalations=tuple(
            RiskEscalationRecord(
                escalation_id=str(e["escalation_id"]),
                escalation_level=str(e["escalation_level"]),
                trigger_code=str(e["trigger_code"]),
                position_ids=tuple(str(p) for p in e.get("position_ids", [])),
                message=str(e["message"]),
                requires_human_ack=bool(e.get("requires_human_ack", False)),
            )
            for e in data.get("escalations", [])
        ),
        pipeline_summary=pipeline,
        warnings=tuple(
            APMEWarningRecord(
                code=str(w["code"]),
                message=str(w["message"]),
                stage_id=(
                    APMEEvaluationStageId(w["stage_id"]) if w.get("stage_id") else None
                ),
                field=str(w["field"]) if w.get("field") else None,
                position_id=str(w["position_id"]) if w.get("position_id") else None,
            )
            for w in data.get("warnings", [])
        ),
        errors=tuple(
            APMEErrorRecord(
                code=str(e["code"]),
                message=str(e["message"]),
                stage_id=(
                    APMEEvaluationStageId(e["stage_id"]) if e.get("stage_id") else None
                ),
                field=str(e["field"]) if e.get("field") else None,
                position_id=str(e["position_id"]) if e.get("position_id") else None,
            )
            for e in data.get("errors", [])
        ),
        primary_error_code=str(data["primary_error_code"]) if data.get("primary_error_code") else None,
        submitted_at=_datetime_from_iso(str(data["submitted_at"])),
        completed_at=_datetime_from_iso(completed_raw) if completed_raw else None,
        duration_ms=float(data["duration_ms"]),
        report_fingerprint=str(data["report_fingerprint"]),
    )


def serialize_apme_decision_report(report: APMEDecisionReport) -> str:
    """Serialize APME decision report to canonical JSON."""
    return _canonical_json(apme_decision_report_to_dict(report))


def deserialize_apme_decision_report(payload: str) -> APMEDecisionReport:
    """Deserialize APME decision report from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise APMEValidationError(
            "Malformed JSON payload.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(data, dict):
        raise APMEValidationError(
            "JSON payload must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return apme_decision_report_from_dict(data)


def serialize_position_management_decision(decision: PositionManagementDecision) -> str:
    """Serialize position management decision to canonical JSON."""
    return _canonical_json(position_management_decision_to_dict(decision))


def deserialize_position_management_decision(payload: str) -> PositionManagementDecision:
    """Deserialize position management decision from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise APMEValidationError(
            "Malformed JSON payload.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(data, dict):
        raise APMEValidationError(
            "JSON payload must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return position_management_decision_from_dict(data)


def _build_rejected_report(
    context: APMEEvaluationContext,
    config: APMEConfig,
    snapshot: PortfolioSnapshot,
    *,
    error_code: str,
    message: str,
) -> APMEDecisionReport:
    """Build rejected report without mutating registry."""
    as_of = context.reference_time if _is_timezone_aware(context.reference_time) else _utc_now()
    run_state = _PipelineRunState(
        snapshot=snapshot,
        context=context,
        config=config,
        report_id=f"apme-rejected-{uuid.uuid4().hex[:12]}",
        started_at=as_of,
        pre_eval_rejected=True,
        status=APMEEvaluationStatus.REJECTED,
        primary_error_code=error_code,
        errors=[APMEErrorRecord(code=error_code, message=message)],
    )
    pipeline = APMEPipelineResult(
        total_stages=1,
        passed_stages=0,
        failed_stage_id=APMEEvaluationStageId.INPUT_GATE,
        stages=(),
        short_circuited=True,
    )
    return _empty_report(run_state, pipeline_summary=pipeline)


class AdaptivePositionManagementEngine:
    """Institutional adaptive position management engine for THETA AI TRADER.

    Continuously evaluates every live open position after execution and
    produces sealed management decisions with full explainability.

    Consumes PortfolioSnapshot from Portfolio Manager. Never selects
    strategies, collects market data, authenticates with brokers, or
    submits orders.

    Args:
        config: Injected immutable configuration.
        event_bus: Optional EventBus for lifecycle event publishing.
    """

    def __init__(
        self,
        config: APMEConfig | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._config = config or default_apme_config()
        self._event_bus = event_bus
        self._registry_lock = threading.RLock()
        self._latest_report: APMEDecisionReport | None = None
        self._applied_snapshots: set[str] = set()
        self._decision_cooldowns: dict[str, datetime] = {}
        self._prior_health_status: dict[str, HealthStatus] = {}
        self._pipeline = APMEEvaluationPipeline()

    @property
    def config(self) -> APMEConfig:
        """Return engine configuration."""
        return self._config

    def evaluate(
        self,
        portfolio_snapshot: PortfolioSnapshot,
        context: APMEEvaluationContext,
        *,
        position_snapshot: PositionSnapshot | None = None,
    ) -> APMEDecisionReport:
        """Evaluate all open positions and produce management decisions."""
        _logger.info(
            "apme.evaluate.start",
            extra={
                "event": "apme.evaluate.start",
                "portfolio_snapshot_id": portfolio_snapshot.snapshot_id,
            },
        )
        if not _is_timezone_aware(context.reference_time):
            _logger.info(
                "apme.evaluate.rejected",
                extra={"event": "apme.evaluate.rejected"},
            )
            return _build_rejected_report(
                context,
                self._config,
                portfolio_snapshot,
                error_code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
                message="reference_time must be timezone-aware.",
            )

        with self._registry_lock:
            prior_report = self._latest_report
            prior_health = dict(self._prior_health_status)
            run_state = _PipelineRunState(
                snapshot=portfolio_snapshot,
                context=context,
                config=self._config,
                report_id=_generate_report_id(context, self._config),
                started_at=context.reference_time,
                position_snapshot=position_snapshot,
                prior_health_status=prior_health,
            )
            report = self._pipeline.execute(
                run_state,
                self._config,
                event_bus=self._event_bus,
                applied_snapshots=set(self._applied_snapshots),
                prior_report=prior_report,
            )
            if report.status not in (
                APMEEvaluationStatus.REJECTED,
                APMEEvaluationStatus.FAILED,
                APMEEvaluationStatus.NOOP,
            ):
                self._latest_report = report
                if portfolio_snapshot.snapshot_fingerprint:
                    self._applied_snapshots.add(portfolio_snapshot.snapshot_fingerprint)
                for decision in report.decisions:
                    self._prior_health_status[decision.position_id] = decision.health.health_status
            elif report.status is APMEEvaluationStatus.NOOP and self._latest_report:
                pass

        _logger.info(
            "apme.evaluate.complete",
            extra={
                "event": "apme.evaluate.complete",
                "status": report.status.value,
                "report_id": report.report_id,
            },
        )
        return report

    def evaluate_on_portfolio_event(
        self,
        event: PortfolioEvent,
        portfolio_snapshot: PortfolioSnapshot,
        context: APMEEvaluationContext,
    ) -> APMEDecisionReport:
        """Evaluate triggered by portfolio lifecycle event."""
        if event.event_type is not PortfolioEventType.SNAPSHOT_PUBLISHED:
            return self.evaluate(portfolio_snapshot, context)
        return self.evaluate(portfolio_snapshot, context)

    def get_latest_report(self) -> APMEDecisionReport | None:
        """Return latest immutable decision report."""
        with self._registry_lock:
            return self._latest_report

    def get_position_decision(self, position_id: str) -> PositionManagementDecision | None:
        """Return decision for specific position from latest report."""
        with self._registry_lock:
            if self._latest_report is None:
                return None
            for decision in self._latest_report.decisions:
                if decision.position_id == position_id:
                    return decision
            return None

    def on_portfolio_snapshot_event(self, event: PortfolioEvent) -> None:
        """Optional handler for portfolio.snapshot.published events."""
        if event.event_type is not PortfolioEventType.SNAPSHOT_PUBLISHED:
            return
        _logger.debug(
            "apme.portfolio_event.received",
            extra={"event": "apme.portfolio_event.received", "topic": event.topic},
        )

    def validate_evaluation_context(
        self,
        context: APMEEvaluationContext,
        portfolio_snapshot: PortfolioSnapshot,
    ) -> APMEValidationResult:
        """Validate context and snapshot without mutating state."""
        return validate_evaluation_context(context, portfolio_snapshot, self._config)

    def validate_report(self, report: APMEDecisionReport) -> APMEValidationResult:
        """Validate sealed decision report."""
        return validate_apme_decision_report(report)

