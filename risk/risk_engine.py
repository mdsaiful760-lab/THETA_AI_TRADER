"""Institutional risk enforcement engine for THETA AI TRADER v1.0.

Consumes immutable :class:`TradeDecisionResult` outputs and produces a single
authoritative risk verdict expressed as :class:`RiskDecisionResult`. Never
places orders, computes lot quantities, or communicates with brokers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, time as dt_time, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Final, Mapping
from zoneinfo import ZoneInfo

from core.base_engine import BaseEngine
from core.engine_context import EngineContext
from core.engine_metadata import EngineMetadata
from core.engine_result import EngineErrorRecord, EngineResult, EngineWarningRecord
from core.enums import EngineStatus
from decision.trade_decision_engine import (
    DecisionOutcomeClass,
    DecisionStatus,
    TradeDecisionResult,
)
from strategy.signals import (
    MarginIntensityHint,
    RiskProfileHint,
    SignalAction,
    StrategyExecutionMode,
    StrategyFamily,
    TradingSignal,
    is_signal_expired,
    signal_fingerprint,
    to_dict as signal_to_dict,
    from_dict as signal_from_dict,
    validate_trading_signal,
)
from strategy.strategy_evaluation_engine import (
    CapitalEstimateCategory,
    RiskEstimateCategory,
    StrategyEvaluationReport,
)

RISK_ENGINE_VERSION: Final[str] = "1.0.0"
RISK_ENGINE_SCHEMA_VERSION: Final[str] = "1.0.0"
RISK_SCORE_EPSILON: Final[float] = 1e-9
DEFAULT_MARGIN_TOLERANCE_PCT: Final[float] = 0.05
DEFAULT_NEAR_LIMIT_THRESHOLD: Final[float] = 0.80
DEFAULT_NEW_TRADE_CUTOFF_MINUTES: Final[int] = 30
DEFAULT_EXPIRY_CUTOFF_MINUTES: Final[int] = 60
PERCENT_MIN: Final[float] = 0.0
PERCENT_MAX: Final[float] = 100.0

ERROR_CONFIG_INVALID: Final[str] = "RISK.CONFIG.INVALID"
ERROR_CONTEXT_INVALID: Final[str] = "RISK.CONTEXT.INVALID"
ERROR_CONTEXT_DECISION_MISSING: Final[str] = "RISK.CONTEXT.DECISION_MISSING"
ERROR_CONTEXT_PORTFOLIO_MISSING: Final[str] = "RISK.CONTEXT.PORTFOLIO_MISSING"
ERROR_CONTEXT_PROFILE_MISSING: Final[str] = "RISK.CONTEXT.PROFILE_MISSING"
ERROR_CONTEXT_CORRELATION_MISMATCH: Final[str] = "RISK.CONTEXT.CORRELATION_MISMATCH"
ERROR_CONTEXT_NAIVE_TIMESTAMP: Final[str] = "RISK.CONTEXT.NAIVE_TIMESTAMP"
ERROR_CONTEXT_INTEGRITY_FAILED: Final[str] = "RISK.CONTEXT.INTEGRITY_FAILED"
ERROR_KILL_SWITCH_ACTIVE: Final[str] = "RISK.KILL_SWITCH.ACTIVE"
ERROR_DECISION_NOT_SELECTED: Final[str] = "RISK.DECISION.NOT_SELECTED"
ERROR_DECISION_NOT_TRADE_CANDIDATE: Final[str] = "RISK.DECISION.NOT_TRADE_CANDIDATE"
ERROR_DECISION_INTEGRITY_FAILED: Final[str] = "RISK.DECISION.INTEGRITY_FAILED"
ERROR_SIGNAL_INVALID: Final[str] = "RISK.SIGNAL.INVALID"
ERROR_SIGNAL_EXPIRED: Final[str] = "RISK.SIGNAL.EXPIRED"
ERROR_CAPITAL_INSUFFICIENT: Final[str] = "RISK.CAPITAL.INSUFFICIENT"
ERROR_CAPITAL_BUDGET_EXCEEDED: Final[str] = "RISK.CAPITAL.BUDGET_EXCEEDED"
ERROR_CAPITAL_EQUITY_NON_POSITIVE: Final[str] = "RISK.CAPITAL.EQUITY_NON_POSITIVE"
ERROR_MARGIN_INSUFFICIENT: Final[str] = "RISK.MARGIN.INSUFFICIENT"
ERROR_MARGIN_UNKNOWN: Final[str] = "RISK.MARGIN.UNKNOWN"
ERROR_EXPOSURE_LIMIT_EXCEEDED: Final[str] = "RISK.EXPOSURE.LIMIT_EXCEEDED"
ERROR_PORTFOLIO_MAX_POSITIONS: Final[str] = "RISK.PORTFOLIO.MAX_POSITIONS"
ERROR_PORTFOLIO_CONCENTRATION: Final[str] = "RISK.PORTFOLIO.CONCENTRATION"
ERROR_DAILY_LOSS_LIMIT: Final[str] = "RISK.DAILY_LOSS.LIMIT_EXCEEDED"
ERROR_DRAWDOWN_LIMIT: Final[str] = "RISK.DRAWDOWN.LIMIT_EXCEEDED"
ERROR_CONSECUTIVE_LOSSES: Final[str] = "RISK.CONSECUTIVE_LOSSES.LIMIT_EXCEEDED"
ERROR_SIZING_HINT_REQUIRED: Final[str] = "RISK.SIZING.HINT_REQUIRED"
ERROR_SIZING_EXCEEDS_BUDGET: Final[str] = "RISK.SIZING.EXCEEDS_BUDGET"
ERROR_SIZING_INVALID_HINT: Final[str] = "RISK.SIZING.INVALID_HINT"
ERROR_STRATEGY_BLOCKED: Final[str] = "RISK.STRATEGY.BLOCKED"
ERROR_STRATEGY_UNDEFINED_RISK: Final[str] = "RISK.STRATEGY.UNDEFINED_RISK"
ERROR_UNDERLYING_BLOCKED: Final[str] = "RISK.UNDERLYING.BLOCKED"
ERROR_UNDERLYING_MISSING: Final[str] = "RISK.UNDERLYING.MISSING"
ERROR_WINDOW_OUTSIDE_SESSION: Final[str] = "RISK.WINDOW.OUTSIDE_SESSION"
ERROR_WINDOW_NEAR_CLOSE: Final[str] = "RISK.WINDOW.NEAR_CLOSE"
ERROR_WINDOW_EXPIRY_CUTOFF: Final[str] = "RISK.WINDOW.EXPIRY_CUTOFF"
ERROR_WINDOW_BLACKOUT: Final[str] = "RISK.WINDOW.BLACKOUT"
ERROR_EXPIRY_DAY_LIMIT: Final[str] = "RISK.EXPIRY_DAY.LIMIT_EXCEEDED"
ERROR_RESULT_INVALID: Final[str] = "RISK.RESULT.INVALID"
ERROR_SERIALIZATION_UNSUPPORTED_VERSION: Final[str] = "RISK.SERIALIZATION.UNSUPPORTED_VERSION"
ERROR_SERIALIZATION_MALFORMED: Final[str] = "RISK.SERIALIZATION.MALFORMED"

WARN_CAPITAL_NEAR_LIMIT: Final[str] = "RISK.CAPITAL.NEAR_LIMIT"
WARN_EXPOSURE_NEAR_LIMIT: Final[str] = "RISK.EXPOSURE.NEAR_LIMIT"
WARN_DAILY_LOSS_NEAR_LIMIT: Final[str] = "RISK.DAILY_LOSS.NEAR_LIMIT"
WARN_DRAWDOWN_NEAR_LIMIT: Final[str] = "RISK.DRAWDOWN.NEAR_LIMIT"
WARN_MARGIN_UNKNOWN_PASSED: Final[str] = "RISK.MARGIN.UNKNOWN_PASSED"
WARN_WINDOW_NEAR_CUTOFF: Final[str] = "RISK.WINDOW.NEAR_CUTOFF"
WARN_DECISION_SIGNAL_MISMATCH: Final[str] = "RISK.DECISION.SIGNAL_ACTION_MISMATCH"
WARN_SIZING_HEURISTIC_FALLBACK: Final[str] = "RISK.SIZING.HEURISTIC_FALLBACK"
WARN_PORTFOLIO_DUPLICATE_STRATEGY: Final[str] = "RISK.PORTFOLIO.DUPLICATE_STRATEGY"

_DEFAULT_MARGIN_INTENSITY_MAP: Final[Mapping[str, float]] = MappingProxyType(
    {
        MarginIntensityHint.LOW.value: 0.25,
        MarginIntensityHint.MODERATE.value: 0.50,
        MarginIntensityHint.HIGH.value: 0.75,
        MarginIntensityHint.UNKNOWN.value: 0.60,
    }
)
_DEFAULT_CATEGORY_BOOST_MAP: Final[Mapping[str, float]] = MappingProxyType(
    {
        CapitalEstimateCategory.MINIMAL.value: 0.1,
        CapitalEstimateCategory.SMALL.value: 0.2,
        CapitalEstimateCategory.MODERATE.value: 0.35,
        CapitalEstimateCategory.LARGE.value: 0.55,
        CapitalEstimateCategory.VERY_LARGE.value: 0.75,
        CapitalEstimateCategory.UNKNOWN.value: 0.35,
    }
)

_logger = logging.getLogger(__name__)


class RiskVerdict(str, Enum):
    """Authoritative risk review outcome."""

    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class RiskStageId(str, Enum):
    """Ordered risk validation pipeline stage identifiers."""

    DECISION_ELIGIBILITY = "decision_eligibility"
    KILL_SWITCH = "kill_switch"
    INPUT_INTEGRITY = "input_integrity"
    SIGNAL_FRESHNESS = "signal_freshness"
    CAPITAL = "capital"
    MARGIN_HEURISTIC = "margin_heuristic"
    EXPOSURE = "exposure"
    PORTFOLIO_LIMITS = "portfolio_limits"
    DAILY_LOSS = "daily_loss"
    DRAWDOWN = "drawdown"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    USER_RISK_PROFILE = "user_risk_profile"
    POSITION_SIZING_HINT = "position_sizing_hint"
    STRATEGY_RESTRICTIONS = "strategy_restrictions"
    ALLOWED_UNDERLYINGS = "allowed_underlyings"
    TRADING_WINDOW = "trading_window"
    EXPIRY_DAY = "expiry_day"


STAGE_ORDER = (
    RiskStageId.DECISION_ELIGIBILITY,
    RiskStageId.KILL_SWITCH,
    RiskStageId.INPUT_INTEGRITY,
    RiskStageId.SIGNAL_FRESHNESS,
    RiskStageId.CAPITAL,
    RiskStageId.MARGIN_HEURISTIC,
    RiskStageId.EXPOSURE,
    RiskStageId.PORTFOLIO_LIMITS,
    RiskStageId.DAILY_LOSS,
    RiskStageId.DRAWDOWN,
    RiskStageId.CONSECUTIVE_LOSSES,
    RiskStageId.USER_RISK_PROFILE,
    RiskStageId.POSITION_SIZING_HINT,
    RiskStageId.STRATEGY_RESTRICTIONS,
    RiskStageId.ALLOWED_UNDERLYINGS,
    RiskStageId.TRADING_WINDOW,
    RiskStageId.EXPIRY_DAY,
)


class RiskRejectionSeverity(str, Enum):
    """Rejection severity classification."""

    HARD = "hard"
    POLICY = "policy"
    INFORMATIONAL = "informational"


class RiskProfileTier(str, Enum):
    """User risk profile tier."""

    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"
    CUSTOM = "custom"


class MarginValidationOutcome(str, Enum):
    """Heuristic margin validation outcome."""

    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    UNKNOWN = "unknown"


class SizingHintValidationOutcome(str, Enum):
    """Position sizing hint validation outcome."""

    WITHIN_BUDGET = "within_budget"
    EXCEEDS_BUDGET = "exceeds_budget"
    MISSING_HINT = "missing_hint"
    INVALID_HINT = "invalid_hint"


class SkipReasonCode(str, Enum):
    """Structured skip reason codes."""

    DECISION_ABSTAIN = "decision_abstain"
    DECISION_NOT_SELECTED = "decision_not_selected"
    NOT_TRADE_CANDIDATE = "not_trade_candidate"
    ORCHESTRATOR_SKIP = "orchestrator_skip"
    ANALYSIS_MODE_SKIP = "analysis_mode_skip"
    WINDOW_CLOSED_DECISION = "window_closed_decision"
    MANUAL_INVALID_DECISION = "manual_invalid_decision"


class RiskEngineError(Exception):
    """Base exception for risk engine failures."""

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


class RiskEngineConfigurationError(RiskEngineError):
    """Raised when engine configuration is invalid."""


class RiskEngineValidationError(RiskEngineError):
    """Raised when input or output validation fails."""


class RiskEngineContextError(RiskEngineError):
    """Raised when risk run context is invalid."""


class RiskEngineDecisionError(RiskEngineError):
    """Raised when trade decision integrity fails."""


@dataclass(frozen=True)
class CapitalPolicy:
    """Capital validation parameters."""

    margin_tolerance_pct: float = DEFAULT_MARGIN_TOLERANCE_PCT
    strict_large_capital_reject: bool = False
    near_limit_threshold: float = DEFAULT_NEAR_LIMIT_THRESHOLD

    def __post_init__(self) -> None:
        if not (PERCENT_MIN <= self.near_limit_threshold <= 1.0):
            raise RiskEngineConfigurationError(
                "near_limit_threshold must be in [0, 1].",
                code=ERROR_CONFIG_INVALID,
                field="capital_policy.near_limit_threshold",
            )


@dataclass(frozen=True)
class MarginPolicy:
    """Heuristic margin validation parameters."""

    method: str = "heuristic_v1"
    intensity_map: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType(dict(_DEFAULT_MARGIN_INTENSITY_MAP))
    )
    category_boost_map: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType(dict(_DEFAULT_CATEGORY_BOOST_MAP))
    )


@dataclass(frozen=True)
class ExposurePolicy:
    """Exposure limit defaults."""

    default_max_gross_exposure_pct: float = 200.0
    default_max_underlying_exposure_pct: float = 100.0
    default_max_family_exposure_pct: float = 150.0
    near_limit_threshold: float = 0.90


@dataclass(frozen=True)
class PortfolioLimitPolicy:
    """Portfolio limit parameters."""

    max_positions_per_underlying: int = 2
    max_single_underlying_concentration_pct: float | None = None
    warn_duplicate_strategy_position: bool = True


@dataclass(frozen=True)
class LossLimitPolicy:
    """Daily loss limit parameters."""

    near_limit_threshold: float = DEFAULT_NEAR_LIMIT_THRESHOLD
    disable_in_backtest: bool = True


@dataclass(frozen=True)
class DrawdownPolicy:
    """Drawdown limit parameters."""

    near_limit_threshold: float = DEFAULT_NEAR_LIMIT_THRESHOLD


@dataclass(frozen=True)
class StrategyRestrictionPolicy:
    """Strategy restriction defaults."""

    reject_undefined_risk_category: bool = True


@dataclass(frozen=True)
class RiskTimeWindow:
    """Named intraday blackout interval for risk windows."""

    window_id: str
    start_time: dt_time
    end_time: dt_time


@dataclass(frozen=True)
class RiskTradingWindowPolicy:
    """Risk-specific trading window policy."""

    timezone: str = "Asia/Kolkata"
    session_start: dt_time = dt_time(9, 15)
    session_end: dt_time = dt_time(15, 30)
    new_trade_cutoff_minutes_before_close: int = DEFAULT_NEW_TRADE_CUTOFF_MINUTES
    expiry_day_cutoff_minutes_before_close: int = DEFAULT_EXPIRY_CUTOFF_MINUTES
    blackout_windows: tuple[RiskTimeWindow, ...] = ()
    allow_analysis_outside_session: bool = True
    near_cutoff_warning_minutes: int = 15


@dataclass(frozen=True)
class RiskEngineConfig:
    """Immutable configuration for :class:`RiskEngine`."""

    kill_switch_active: bool = False
    kill_switch_reason: str | None = None
    short_circuit_on_failure: bool = True
    strict_correlation_match: bool = True
    strict_decision_integrity: bool = True
    strict_portfolio_fingerprint: bool = False
    require_sizing_hint_in_live: bool = True
    reject_unknown_margin: bool = False
    reject_unknown_capital: bool = False
    skip_review_in_analysis: bool = False
    allow_invalid_signal_in_analysis: bool = False
    deterministic_fingerprint: bool = True
    capital_policy: CapitalPolicy = field(default_factory=CapitalPolicy)
    margin_policy: MarginPolicy = field(default_factory=MarginPolicy)
    exposure_policy: ExposurePolicy = field(default_factory=ExposurePolicy)
    portfolio_limit_policy: PortfolioLimitPolicy = field(default_factory=PortfolioLimitPolicy)
    loss_limit_policy: LossLimitPolicy = field(default_factory=LossLimitPolicy)
    drawdown_policy: DrawdownPolicy = field(default_factory=DrawdownPolicy)
    strategy_restriction_policy: StrategyRestrictionPolicy = field(
        default_factory=StrategyRestrictionPolicy
    )
    trading_window_policy: RiskTradingWindowPolicy = field(
        default_factory=RiskTradingWindowPolicy
    )
    apply_confidence_risk_multiplier: bool = False
    medium_confidence_threshold: float = 50.0
    medium_confidence_multiplier: float = 0.75
    analysis_mode_limit_multiplier: float = 1.0
    default_margin_availability_ratio: float = 0.5
    undefined_risk_exposure_multiplier: float = 1.25
    absolute_floor_max_risk_per_trade_pct: float = 0.25
    absolute_floor_max_daily_loss_pct: float = 0.5
    absolute_floor_max_drawdown_pct: float = 2.0

    def __post_init__(self) -> None:
        if not (0.0 < self.medium_confidence_multiplier <= 1.0):
            raise RiskEngineConfigurationError(
                "medium_confidence_multiplier must be in (0, 1].",
                code=ERROR_CONFIG_INVALID,
                field="medium_confidence_multiplier",
            )
        if not (0.0 < self.analysis_mode_limit_multiplier <= 10.0):
            raise RiskEngineConfigurationError(
                "analysis_mode_limit_multiplier must be positive.",
                code=ERROR_CONFIG_INVALID,
                field="analysis_mode_limit_multiplier",
            )

    @classmethod
    def with_kill_switch(cls, *, reason: str, base: RiskEngineConfig | None = None) -> RiskEngineConfig:
        """Return config with kill switch activated."""
        source = base or default_risk_engine_config()
        return replace(source, kill_switch_active=True, kill_switch_reason=reason)


@dataclass(frozen=True)
class PortfolioExposureSummary:
    """Pre-aggregated portfolio exposure metrics."""

    gross_notional: float
    net_notional_by_underlying: Mapping[str, float]
    gross_notional_by_underlying: Mapping[str, float]
    exposure_by_family: Mapping[str, float]
    open_position_count: int
    open_position_count_by_underlying: Mapping[str, int]


@dataclass(frozen=True)
class PortfolioPosition:
    """Open portfolio position record."""

    position_id: str
    underlying: str
    notional_exposure: float
    unrealized_pnl: float
    opened_at: datetime
    strategy_id: str | None = None
    strategy_family: StrategyFamily | None = None
    direction: object | None = None
    margin_at_risk_hint: float | None = None
    expires_at: datetime | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Orchestrator-supplied portfolio state snapshot."""

    snapshot_id: str
    correlation_id: str
    as_of: datetime
    account_id: str
    equity: float
    cash_available: float
    daily_realized_pnl: float
    daily_unrealized_pnl: float
    peak_equity: float
    consecutive_losses: int
    open_positions: tuple[PortfolioPosition, ...]
    exposure_summary: PortfolioExposureSummary
    portfolio_fingerprint: str
    margin_used_hint: float = 0.0
    margin_available_hint: float | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class UserRiskProfile:
    """User-specific risk tolerance and limit overrides."""

    profile_id: str
    profile_tier: RiskProfileTier
    max_risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_drawdown_pct: float
    max_open_positions: int
    max_consecutive_losses: int
    allowed_families: frozenset[StrategyFamily] | None = None
    blocked_strategy_ids: frozenset[str] = frozenset()
    blocked_families: frozenset[StrategyFamily] = frozenset()
    allowed_underlyings: frozenset[str] | None = None
    blocked_underlyings: frozenset[str] = frozenset()
    allow_undefined_risk: bool = False
    max_gross_exposure_pct: float | None = None
    max_underlying_exposure_pct: float | None = None
    max_family_exposure_pct: float | None = None
    expiry_day_multiplier: float = 0.5
    caution_multiplier: float = 0.5
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class PositionSizingHint:
    """Orchestrator-supplied position sizing hint."""

    hint_id: str
    proposed_risk_amount: float
    proposed_risk_pct: float
    sizing_method: str
    proposed_notional: float | None = None
    proposed_margin_hint: float | None = None
    proposed_units_hint: float | None = None
    within_decision_capital_hint: bool | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class RiskRunContext:
    """Immutable per-run risk review inputs."""

    correlation_id: str
    as_of: datetime
    trade_decision: TradeDecisionResult
    portfolio: PortfolioSnapshot
    user_risk_profile: UserRiskProfile
    position_sizing_hint: PositionSizingHint | None = None
    execution_mode: StrategyExecutionMode | None = None
    reference_time: datetime | None = None
    force_skip: bool = False
    available_capital: float | None = None
    available_margin_hint: float | None = None
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class RiskFactor:
    """Machine-readable risk factor."""

    factor_id: str
    label: str
    weight: float
    raw_value: float
    normalized_value: float
    direction: str
    stage_id: RiskStageId | None = None
    limit_value: float | None = None
    notes: str | None = None


@dataclass(frozen=True)
class RiskReason:
    """Human-readable risk explanation bullet."""

    code: str
    message: str
    severity: str
    stage_id: RiskStageId | None = None


@dataclass(frozen=True)
class RiskWarningRecord:
    """Non-fatal risk review warning."""

    code: str
    message: str
    severity: str = "WARNING"
    stage_id: RiskStageId | None = None
    field: str | None = None


@dataclass(frozen=True)
class RiskErrorRecord:
    """Structured risk review error."""

    code: str
    message: str
    field: str | None = None
    stage_id: RiskStageId | None = None


@dataclass(frozen=True)
class RiskStageResult:
    """Single pipeline stage outcome."""

    stage_id: RiskStageId
    passed: bool
    rejection_code: str | None = None
    message: str | None = None
    duration_ms: float = 0.0
    details: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class RiskPipelineResult:
    """Pipeline audit summary."""

    total_stages: int
    passed_stages: int
    failed_stage_id: RiskStageId | None
    stages: tuple[RiskStageResult, ...]
    short_circuited: bool


@dataclass(frozen=True)
class RiskDecisionResult:
    """Immutable sealed risk review outcome."""

    risk_id: str
    correlation_id: str
    decision_id: str
    decision_fingerprint: str
    portfolio_snapshot_id: str
    portfolio_fingerprint: str
    verdict: RiskVerdict
    trading_signal: TradingSignal
    execution_mode: StrategyExecutionMode
    reasons: tuple[RiskReason, ...]
    factors: tuple[RiskFactor, ...]
    pipeline_summary: RiskPipelineResult
    reviewed_at: datetime
    duration_ms: float
    risk_fingerprint: str
    warnings: tuple[RiskWarningRecord, ...]
    errors: tuple[RiskErrorRecord, ...]
    primary_rejection_code: str | None = None
    skip_reason_code: SkipReasonCode | None = None
    evaluation_report: StrategyEvaluationReport | None = None
    approved_risk_budget: float | None = None
    approved_risk_pct: float | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class RiskValidationResult:
    """Output validation outcome."""

    errors: tuple[RiskErrorRecord, ...] = ()
    warnings: tuple[RiskWarningRecord, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return ``True`` when no validation errors exist."""
        return not self.errors


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

    run_context: RiskRunContext
    config: RiskEngineConfig
    warnings: list[RiskWarningRecord] = field(default_factory=list)
    factors: list[RiskFactor] = field(default_factory=list)
    approved_budget: float = 0.0
    approved_pct: float = 0.0
    proposed_risk: float = 0.0
    proposed_risk_pct: float = 0.0
    proposed_notional: float = 0.0
    margin_demand: float = 0.0
    margin_available: float = 0.0
    underlying: str = ""


@dataclass(frozen=True)
class _EligibilityOutcome:
    """Decision eligibility gate result."""

    eligible: bool
    skip_reason: SkipReasonCode | None = None


def _utc_now() -> datetime:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


def _is_timezone_aware(value: datetime) -> bool:
    """Return whether datetime is timezone-aware."""
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _is_finite(value: float) -> bool:
    """Return whether value is finite."""
    return math.isfinite(value)


def _round_money(value: float) -> float:
    """Round monetary value to two decimal places."""
    return round(value, 2)


def _round_pct(value: float) -> float:
    """Round percent value to four decimal places."""
    return round(value, 4)


def default_risk_engine_config() -> RiskEngineConfig:
    """Return default risk engine configuration."""
    return RiskEngineConfig()


def default_user_risk_profile(
    *,
    profile_id: str = "profile-moderate-default",
    tier: RiskProfileTier = RiskProfileTier.MODERATE,
) -> UserRiskProfile:
    """Return user risk profile with tier defaults."""
    tier_defaults: dict[RiskProfileTier, tuple[float, float, float, int, int]] = {
        RiskProfileTier.CONSERVATIVE: (0.5, 1.5, 5.0, 2, 2),
        RiskProfileTier.MODERATE: (1.0, 3.0, 10.0, 3, 3),
        RiskProfileTier.AGGRESSIVE: (2.0, 5.0, 15.0, 5, 5),
    }
    if tier is RiskProfileTier.CUSTOM:
        raise RiskEngineConfigurationError(
            "CUSTOM tier requires explicit profile construction.",
            code=ERROR_CONFIG_INVALID,
            field="profile_tier",
        )
    risk, daily, dd, positions, losses = tier_defaults[tier]
    return UserRiskProfile(
        profile_id=profile_id,
        profile_tier=tier,
        max_risk_per_trade_pct=risk,
        max_daily_loss_pct=daily,
        max_drawdown_pct=dd,
        max_open_positions=positions,
        max_consecutive_losses=losses,
    )


def compute_daily_loss_pct(*, daily_pnl: float, equity: float) -> float:
    """Compute daily loss percentage from P&L and equity."""
    if equity <= 0:
        return PERCENT_MAX
    daily_loss = min(daily_pnl, 0.0)
    return _round_pct(abs(daily_loss) / equity * 100.0)


def compute_drawdown_pct(*, equity: float, peak_equity: float) -> float:
    """Compute drawdown percentage from current and peak equity."""
    peak = max(peak_equity, equity)
    if peak <= 0:
        return PERCENT_MAX
    drawdown = peak - equity
    return _round_pct(drawdown / peak * 100.0)


def compute_heuristic_margin_demand(
    *,
    equity: float,
    margin_intensity: MarginIntensityHint | None,
    capital_category: CapitalEstimateCategory | None,
    proposed_margin_hint: float | None,
    margin_policy: MarginPolicy,
) -> float:
    """Compute heuristic margin demand in INR."""
    intensity_key = (margin_intensity or MarginIntensityHint.UNKNOWN).value
    intensity_score = margin_policy.intensity_map.get(intensity_key, 0.60)
    category_key = (capital_category or CapitalEstimateCategory.UNKNOWN).value
    category_boost = margin_policy.category_boost_map.get(category_key, 0.35)
    base_demand = equity * intensity_score * category_boost
    if proposed_margin_hint is not None:
        return _round_money(max(base_demand, proposed_margin_hint))
    return _round_money(base_demand)


def portfolio_fingerprint(snapshot: PortfolioSnapshot) -> str:
    """Compute deterministic SHA-256 fingerprint for portfolio snapshot."""
    payload = {
        "schema_version": RISK_ENGINE_SCHEMA_VERSION,
        "snapshot_id": snapshot.snapshot_id,
        "correlation_id": snapshot.correlation_id,
        "account_id": snapshot.account_id,
        "equity": _round_money(snapshot.equity),
        "cash_available": _round_money(snapshot.cash_available),
        "daily_realized_pnl": _round_money(snapshot.daily_realized_pnl),
        "daily_unrealized_pnl": _round_money(snapshot.daily_unrealized_pnl),
        "peak_equity": _round_money(snapshot.peak_equity),
        "consecutive_losses": snapshot.consecutive_losses,
        "open_position_count": snapshot.exposure_summary.open_position_count,
        "gross_notional": _round_money(snapshot.exposure_summary.gross_notional),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_portfolio_snapshot(snapshot: PortfolioSnapshot) -> None:
    """Validate portfolio snapshot invariants."""
    if not _is_finite(snapshot.equity):
        raise RiskEngineContextError(
            "Portfolio equity must be finite.",
            code=ERROR_CONTEXT_INVALID,
            field="portfolio.equity",
        )
    if snapshot.exposure_summary.open_position_count < 0:
        raise RiskEngineContextError(
            "open_position_count must be non-negative.",
            code=ERROR_CONTEXT_INVALID,
            field="portfolio.exposure_summary.open_position_count",
        )
    if not _is_timezone_aware(snapshot.as_of):
        raise RiskEngineContextError(
            "portfolio.as_of must be timezone-aware.",
            code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
            field="portfolio.as_of",
        )


def validate_user_risk_profile(profile: UserRiskProfile) -> None:
    """Validate user risk profile invariants."""
    for name, value in (
        ("max_risk_per_trade_pct", profile.max_risk_per_trade_pct),
        ("max_daily_loss_pct", profile.max_daily_loss_pct),
        ("max_drawdown_pct", profile.max_drawdown_pct),
    ):
        if value <= 0 or value > PERCENT_MAX:
            raise RiskEngineContextError(
                f"{name} must be in (0, 100].",
                code=ERROR_CONTEXT_INVALID,
                field=name,
            )
    if profile.max_open_positions <= 0:
        raise RiskEngineContextError(
            "max_open_positions must be positive.",
            code=ERROR_CONTEXT_INVALID,
            field="max_open_positions",
        )
    if profile.max_consecutive_losses < 0:
        raise RiskEngineContextError(
            "max_consecutive_losses must be non-negative.",
            code=ERROR_CONTEXT_INVALID,
            field="max_consecutive_losses",
        )


def validate_position_sizing_hint(hint: PositionSizingHint) -> None:
    """Validate position sizing hint structure."""
    if not _is_finite(hint.proposed_risk_amount) or hint.proposed_risk_amount <= 0:
        raise RiskEngineContextError(
            "proposed_risk_amount must be positive and finite.",
            code=ERROR_CONTEXT_INVALID,
            field="position_sizing_hint.proposed_risk_amount",
        )
    if not _is_finite(hint.proposed_risk_pct) or hint.proposed_risk_pct <= 0:
        raise RiskEngineContextError(
            "proposed_risk_pct must be positive and finite.",
            code=ERROR_CONTEXT_INVALID,
            field="position_sizing_hint.proposed_risk_pct",
        )


def validate_run_context(run_context: RiskRunContext) -> None:
    """Validate risk run context before review."""
    if run_context is None:
        raise RiskEngineContextError("Run context is required.", code=ERROR_CONTEXT_INVALID)
    if not run_context.correlation_id.strip():
        raise RiskEngineContextError(
            "correlation_id is required.",
            code=ERROR_CONTEXT_INVALID,
            field="correlation_id",
        )
    if not _is_timezone_aware(run_context.as_of):
        raise RiskEngineContextError(
            "as_of must be timezone-aware.",
            code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
            field="as_of",
        )
    if run_context.trade_decision is None:
        raise RiskEngineContextError(
            "trade_decision is required.",
            code=ERROR_CONTEXT_DECISION_MISSING,
        )
    if run_context.portfolio is None:
        raise RiskEngineContextError(
            "portfolio is required.",
            code=ERROR_CONTEXT_PORTFOLIO_MISSING,
        )
    if run_context.user_risk_profile is None:
        raise RiskEngineContextError(
            "user_risk_profile is required.",
            code=ERROR_CONTEXT_PROFILE_MISSING,
        )
    if run_context.reference_time is not None and not _is_timezone_aware(run_context.reference_time):
        raise RiskEngineContextError(
            "reference_time must be timezone-aware.",
            code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
            field="reference_time",
        )
    if (
        run_context.trade_decision.correlation_id != run_context.correlation_id
        and run_context.tags.get("allow_correlation_mismatch") != "true"
    ):
        raise RiskEngineContextError(
            "correlation_id mismatch between context and decision.",
            code=ERROR_CONTEXT_CORRELATION_MISMATCH,
            field="correlation_id",
        )
    validate_portfolio_snapshot(run_context.portfolio)
    validate_user_risk_profile(run_context.user_risk_profile)
    if run_context.position_sizing_hint is not None:
        validate_position_sizing_hint(run_context.position_sizing_hint)


def _resolved_execution_mode(context: RiskRunContext) -> StrategyExecutionMode:
    """Resolve execution mode from context or decision."""
    if context.execution_mode is not None:
        return context.execution_mode
    return context.trade_decision.execution_mode


def _resolved_reference_time(context: RiskRunContext) -> datetime:
    """Resolve reference time for freshness and window checks."""
    return context.reference_time or context.as_of


def _resolved_available_capital(context: RiskRunContext) -> float:
    """Resolve available capital from context or portfolio."""
    if context.available_capital is not None:
        return context.available_capital
    return context.portfolio.cash_available


def _resolved_available_margin(context: RiskRunContext, config: RiskEngineConfig, equity: float) -> float:
    """Resolve available margin hint from context, portfolio, or default ratio."""
    if context.available_margin_hint is not None:
        return context.available_margin_hint
    if context.portfolio.margin_available_hint is not None:
        return context.portfolio.margin_available_hint
    return equity * config.default_margin_availability_ratio


def _effective_multiplier(context: RiskRunContext, config: RiskEngineConfig) -> float:
    """Compute effective risk budget multiplier from tags and mode."""
    multiplier = 1.0
    if context.tags.get("expiry_day") == "true":
        multiplier *= context.user_risk_profile.expiry_day_multiplier
    if context.tags.get("market_caution") == "true":
        multiplier *= context.user_risk_profile.caution_multiplier
    if _resolved_execution_mode(context) is StrategyExecutionMode.ANALYSIS:
        multiplier *= config.analysis_mode_limit_multiplier
    if config.apply_confidence_risk_multiplier:
        confidence = context.trade_decision.confidence.overall_score
        if confidence < config.medium_confidence_threshold:
            multiplier *= config.medium_confidence_multiplier
    return multiplier


def _compute_approved_budget(context: RiskRunContext, config: RiskEngineConfig) -> tuple[float, float]:
    """Compute approved risk budget and percent."""
    equity = context.portfolio.equity
    profile = context.user_risk_profile
    multiplier = _effective_multiplier(context, config)
    approved_pct = profile.max_risk_per_trade_pct * multiplier
    approved_budget = equity * (approved_pct / 100.0)
    hint = context.position_sizing_hint
    if hint is not None:
        approved_budget = min(approved_budget, hint.proposed_risk_amount)
        approved_pct = min(approved_pct, hint.proposed_risk_pct)
    return _round_money(approved_budget), _round_pct(approved_pct)


def _resolve_underlying(context: RiskRunContext, signal: TradingSignal) -> str | None:
    """Resolve underlying symbol from signal or tags."""
    if signal.market.underlying:
        return signal.market.underlying.upper()
    tag_underlying = context.tags.get("underlying")
    if tag_underlying:
        return tag_underlying.upper()
    return None


def _estimate_proposed_risk(context: RiskRunContext, config: RiskEngineConfig) -> tuple[float, float]:
    """Estimate proposed risk amount and percent."""
    equity = context.portfolio.equity
    hint = context.position_sizing_hint
    if hint is not None:
        return _round_money(hint.proposed_risk_amount), _round_pct(hint.proposed_risk_pct)
    report = context.trade_decision.selected_report
    if report is not None and report.capital_estimate.allocation_percent_hint is not None:
        pct = report.capital_estimate.allocation_percent_hint
        return _round_money(equity * pct / 100.0), _round_pct(pct)
    if report is not None:
        profile_pct = context.user_risk_profile.max_risk_per_trade_pct
        heuristic_pct = report.expected_risk.normalized_score / 100.0 * profile_pct
        return _round_money(equity * heuristic_pct / 100.0), _round_pct(heuristic_pct)
    profile_pct = context.user_risk_profile.max_risk_per_trade_pct
    return _round_money(equity * profile_pct / 100.0), _round_pct(profile_pct)


def _estimate_proposed_notional(context: RiskRunContext, equity: float) -> float:
    """Estimate proposed notional exposure increment."""
    hint = context.position_sizing_hint
    if hint is not None and hint.proposed_notional is not None:
        return _round_money(hint.proposed_notional)
    report = context.trade_decision.selected_report
    if report is not None and report.capital_estimate.allocation_percent_hint is not None:
        return _round_money(equity * report.capital_estimate.allocation_percent_hint / 100.0)
    return _round_money(equity * 0.05)


def _evaluate_eligibility(decision: TradeDecisionResult) -> _EligibilityOutcome:
    """Evaluate whether decision is eligible for full risk review."""
    if decision.decision_status in (DecisionStatus.ABSTAIN, DecisionStatus.NO_CANDIDATES):
        return _EligibilityOutcome(False, SkipReasonCode.DECISION_ABSTAIN)
    if decision.decision_status is DecisionStatus.WINDOW_CLOSED:
        return _EligibilityOutcome(False, SkipReasonCode.WINDOW_CLOSED_DECISION)
    if decision.decision_status is DecisionStatus.MANUAL_INVALID:
        return _EligibilityOutcome(False, SkipReasonCode.MANUAL_INVALID_DECISION)
    if decision.decision_status is not DecisionStatus.SELECTED:
        return _EligibilityOutcome(False, SkipReasonCode.DECISION_NOT_SELECTED)
    if decision.outcome_class is not DecisionOutcomeClass.TRADE_CANDIDATE:
        return _EligibilityOutcome(False, SkipReasonCode.NOT_TRADE_CANDIDATE)
    return _EligibilityOutcome(True)


def _minutes_to_session_close(local_time: dt_time, session_end: dt_time) -> float:
    """Return minutes remaining until session end."""
    close_minutes = session_end.hour * 60 + session_end.minute
    current_minutes = local_time.hour * 60 + local_time.minute + local_time.second / 60.0
    return close_minutes - current_minutes


def _in_blackout(local_time: dt_time, blackout: RiskTimeWindow) -> bool:
    """Return whether local time falls inside blackout window."""
    if blackout.start_time <= blackout.end_time:
        return blackout.start_time <= local_time < blackout.end_time
    return local_time >= blackout.start_time or local_time < blackout.end_time


@dataclass(frozen=True)
class _PipelineApplyResult:
    """Internal pipeline apply outcome."""

    pipeline: RiskPipelineResult
    warnings: tuple[RiskWarningRecord, ...]
    factors: tuple[RiskFactor, ...]


class RiskValidationPipeline:
    """Stateless ordered multi-stage risk validation pipeline."""

    def apply(
        self,
        run_context: RiskRunContext,
        *,
        config: RiskEngineConfig,
    ) -> _PipelineApplyResult:
        """Apply ordered risk validation stages."""
        stages: list[RiskStageResult] = []
        state = _PipelineState(run_context=run_context, config=config)
        state.approved_budget, state.approved_pct = _compute_approved_budget(run_context, config)
        state.proposed_risk, state.proposed_risk_pct = _estimate_proposed_risk(run_context, config)
        state.proposed_notional = _estimate_proposed_notional(run_context, run_context.portfolio.equity)
        signal = run_context.trade_decision.selected_signal
        state.underlying = _resolve_underlying(run_context, signal) or ""

        for stage_id in STAGE_ORDER:
            started = time.perf_counter()
            outcome = self._handlers[stage_id](state)
            duration_ms = (time.perf_counter() - started) * 1000.0
            stage_result = RiskStageResult(
                stage_id=stage_id,
                passed=outcome.passed,
                rejection_code=outcome.rejection_code,
                message=outcome.message,
                duration_ms=duration_ms,
                details=outcome.details,
            )
            stages.append(stage_result)
            if not outcome.passed and config.short_circuit_on_failure:
                break

        passed_count = sum(1 for stage in stages if stage.passed)
        failed_stage = next((stage.stage_id for stage in stages if not stage.passed), None)
        pipeline = RiskPipelineResult(
            total_stages=len(stages),
            passed_stages=passed_count,
            failed_stage_id=failed_stage,
            stages=tuple(stages),
            short_circuited=failed_stage is not None and config.short_circuit_on_failure,
        )
        return _PipelineApplyResult(
            pipeline=pipeline,
            warnings=tuple(state.warnings),
            factors=tuple(state.factors),
        )

    def __init__(self) -> None:
        self._handlers = {
            RiskStageId.DECISION_ELIGIBILITY: self._stage_decision_eligibility,
            RiskStageId.KILL_SWITCH: self._stage_kill_switch,
            RiskStageId.INPUT_INTEGRITY: self._stage_input_integrity,
            RiskStageId.SIGNAL_FRESHNESS: self._stage_signal_freshness,
            RiskStageId.CAPITAL: self._stage_capital,
            RiskStageId.MARGIN_HEURISTIC: self._stage_margin_heuristic,
            RiskStageId.EXPOSURE: self._stage_exposure,
            RiskStageId.PORTFOLIO_LIMITS: self._stage_portfolio_limits,
            RiskStageId.DAILY_LOSS: self._stage_daily_loss,
            RiskStageId.DRAWDOWN: self._stage_drawdown,
            RiskStageId.CONSECUTIVE_LOSSES: self._stage_consecutive_losses,
            RiskStageId.USER_RISK_PROFILE: self._stage_user_risk_profile,
            RiskStageId.POSITION_SIZING_HINT: self._stage_position_sizing_hint,
            RiskStageId.STRATEGY_RESTRICTIONS: self._stage_strategy_restrictions,
            RiskStageId.ALLOWED_UNDERLYINGS: self._stage_allowed_underlyings,
            RiskStageId.TRADING_WINDOW: self._stage_trading_window,
            RiskStageId.EXPIRY_DAY: self._stage_expiry_day,
        }

    def _stage_decision_eligibility(self, state: _PipelineState) -> _StageOutcome:
        decision = state.run_context.trade_decision
        if decision.decision_status is not DecisionStatus.SELECTED:
            return _StageOutcome(False, ERROR_DECISION_NOT_SELECTED, "Decision not SELECTED.")
        if decision.outcome_class is not DecisionOutcomeClass.TRADE_CANDIDATE:
            return _StageOutcome(
                False,
                ERROR_DECISION_NOT_TRADE_CANDIDATE,
                "Outcome class not TRADE_CANDIDATE.",
            )
        return _StageOutcome(True, message="Decision eligible for risk review.")

    def _stage_kill_switch(self, state: _PipelineState) -> _StageOutcome:
        if state.config.kill_switch_active:
            reason = state.config.kill_switch_reason or "Emergency halt active."
            return _StageOutcome(False, ERROR_KILL_SWITCH_ACTIVE, f"Kill switch active: {reason}.")
        return _StageOutcome(True, message="Kill switch inactive.")

    def _stage_input_integrity(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        decision = context.trade_decision
        if (
            config.strict_correlation_match
            and decision.correlation_id != context.correlation_id
            and context.tags.get("allow_correlation_mismatch") != "true"
        ):
            return _StageOutcome(
                False,
                ERROR_CONTEXT_INTEGRITY_FAILED,
                "Decision correlation_id mismatch.",
            )
        if config.strict_portfolio_fingerprint:
            recomputed = portfolio_fingerprint(context.portfolio)
            if recomputed != context.portfolio.portfolio_fingerprint:
                return _StageOutcome(
                    False,
                    ERROR_CONTEXT_INTEGRITY_FAILED,
                    "Portfolio fingerprint drift detected.",
                )
        signal = decision.selected_signal
        if decision.decision_status is DecisionStatus.SELECTED and signal.action in (
            SignalAction.ABSTAIN,
            SignalAction.NO_TRADE,
        ):
            if config.strict_decision_integrity:
                return _StageOutcome(
                    False,
                    ERROR_DECISION_INTEGRITY_FAILED,
                    "Signal action inconsistent with SELECTED decision.",
                )
            state.warnings.append(
                RiskWarningRecord(
                    code=WARN_DECISION_SIGNAL_MISMATCH,
                    message="Signal action inconsistent with SELECTED decision.",
                    severity="WARNING",
                    stage_id=RiskStageId.INPUT_INTEGRITY,
                )
            )
        return _StageOutcome(True, message="Input integrity checks passed.")

    def _stage_signal_freshness(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        signal = context.trade_decision.selected_signal
        ref_time = _resolved_reference_time(context)
        mode = _resolved_execution_mode(context)
        if is_signal_expired(signal, reference_time=ref_time):
            return _StageOutcome(False, ERROR_SIGNAL_EXPIRED, "Trading signal expired.")
        validation = validate_trading_signal(signal)
        if not validation.is_valid:
            if mode is StrategyExecutionMode.ANALYSIS and config.allow_invalid_signal_in_analysis:
                return _StageOutcome(True, message="Invalid signal allowed in ANALYSIS mode.")
            return _StageOutcome(False, ERROR_SIGNAL_INVALID, "Trading signal validation failed.")
        return _StageOutcome(True, message="Signal fresh and valid.")

    def _stage_capital(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        mode = _resolved_execution_mode(context)
        equity = context.portfolio.equity
        available = _resolved_available_capital(context)
        hint = context.position_sizing_hint
        if equity <= 0:
            return _StageOutcome(False, ERROR_CAPITAL_EQUITY_NON_POSITIVE, "Equity non-positive.")
        if mode is StrategyExecutionMode.LIVE and available <= 0:
            return _StageOutcome(False, ERROR_CAPITAL_INSUFFICIENT, "Available capital must be positive.")
        if state.proposed_risk > available + RISK_SCORE_EPSILON:
            return _StageOutcome(
                False,
                ERROR_CAPITAL_INSUFFICIENT,
                f"Insufficient capital: required {state.proposed_risk:.0f} INR, available {available:.0f} INR.",
            )
        max_pct = context.user_risk_profile.max_risk_per_trade_pct * _effective_multiplier(context, config)
        defer_budget_pct = (
            hint is None
            and mode is StrategyExecutionMode.LIVE
            and config.require_sizing_hint_in_live
        )
        if not defer_budget_pct and state.proposed_risk_pct > max_pct + RISK_SCORE_EPSILON:
            return _StageOutcome(
                False,
                ERROR_CAPITAL_BUDGET_EXCEEDED,
                f"Per-trade budget exceeded: {state.proposed_risk_pct:.2f}% > {max_pct:.2f}%.",
            )
        report = context.trade_decision.selected_report
        if (
            report is not None
            and report.capital_estimate.category is CapitalEstimateCategory.VERY_LARGE
            and config.capital_policy.strict_large_capital_reject
        ):
            return _StageOutcome(False, ERROR_CAPITAL_BUDGET_EXCEEDED, "VERY_LARGE capital category rejected.")
        utilization = state.proposed_risk / state.approved_budget if state.approved_budget > 0 else 0.0
        state.factors.append(
            RiskFactor(
                factor_id="available_capital",
                label="Available Capital",
                weight=1.0,
                raw_value=available,
                normalized_value=min(utilization, 1.0),
                direction="NEUTRAL",
                stage_id=RiskStageId.CAPITAL,
            )
        )
        if utilization >= config.capital_policy.near_limit_threshold:
            state.warnings.append(
                RiskWarningRecord(
                    code=WARN_CAPITAL_NEAR_LIMIT,
                    message=f"Budget utilization {utilization * 100:.1f}% near limit.",
                    severity="WARNING",
                    stage_id=RiskStageId.CAPITAL,
                )
            )
        return _StageOutcome(True, message="Capital validation passed.")

    def _stage_margin_heuristic(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        mode = _resolved_execution_mode(context)
        signal = context.trade_decision.selected_signal
        report = context.trade_decision.selected_report
        equity = context.portfolio.equity
        margin_intensity = signal.risk.margin_intensity if signal.risk else None
        capital_category = report.capital_estimate.category if report else None
        hint = context.position_sizing_hint
        proposed_margin_hint = hint.proposed_margin_hint if hint else None
        demand = compute_heuristic_margin_demand(
            equity=equity,
            margin_intensity=margin_intensity,
            capital_category=capital_category,
            proposed_margin_hint=proposed_margin_hint,
            margin_policy=config.margin_policy,
        )
        available = _resolved_available_margin(context, config, equity)
        state.margin_demand = demand
        state.margin_available = available
        state.factors.append(
            RiskFactor(
                factor_id="margin_demand_heuristic",
                label="Heuristic Margin Demand",
                weight=1.0,
                raw_value=demand,
                normalized_value=demand / available if available > 0 else 1.0,
                direction="NEUTRAL",
                stage_id=RiskStageId.MARGIN_HEURISTIC,
            )
        )
        if available <= 0 and margin_intensity is MarginIntensityHint.UNKNOWN:
            if config.reject_unknown_margin and mode is StrategyExecutionMode.LIVE:
                return _StageOutcome(False, ERROR_MARGIN_UNKNOWN, "Unknown margin rejected by policy.")
            state.warnings.append(
                RiskWarningRecord(
                    code=WARN_MARGIN_UNKNOWN_PASSED,
                    message="Unknown margin passed by policy.",
                    severity="WARNING",
                    stage_id=RiskStageId.MARGIN_HEURISTIC,
                )
            )
            return _StageOutcome(True, message="Unknown margin passed by policy.")
        tolerance = 1.0 + config.capital_policy.margin_tolerance_pct
        if mode is StrategyExecutionMode.LIVE and demand > available * tolerance + RISK_SCORE_EPSILON:
            return _StageOutcome(
                False,
                ERROR_MARGIN_INSUFFICIENT,
                f"Heuristic margin insufficient: demand {demand:.0f} INR > available {available:.0f} INR.",
            )
        return _StageOutcome(
            True,
            message="Heuristic margin validation passed; broker margin not queried (v1 policy).",
        )

    def _stage_exposure(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        profile = context.user_risk_profile
        equity = context.portfolio.equity
        exposure = context.portfolio.exposure_summary
        increment = state.proposed_notional
        signal = context.trade_decision.selected_signal
        if signal.risk and signal.risk.profile is RiskProfileHint.UNDEFINED:
            increment *= config.undefined_risk_exposure_multiplier
        projected_gross = exposure.gross_notional + increment
        gross_limit_pct = profile.max_gross_exposure_pct or config.exposure_policy.default_max_gross_exposure_pct
        gross_pct = projected_gross / equity * 100.0 if equity > 0 else PERCENT_MAX
        if gross_pct > gross_limit_pct + RISK_SCORE_EPSILON:
            return _StageOutcome(
                False,
                ERROR_EXPOSURE_LIMIT_EXCEEDED,
                f"Gross exposure {gross_pct:.1f}% exceeds limit {gross_limit_pct:.1f}%.",
            )
        underlying = state.underlying
        if underlying:
            current_underlying = exposure.gross_notional_by_underlying.get(underlying, 0.0)
            underlying_gross = current_underlying + increment
            underlying_limit = profile.max_underlying_exposure_pct or config.exposure_policy.default_max_underlying_exposure_pct
            underlying_pct = underlying_gross / equity * 100.0 if equity > 0 else PERCENT_MAX
            if underlying_pct > underlying_limit + RISK_SCORE_EPSILON:
                return _StageOutcome(
                    False,
                    ERROR_EXPOSURE_LIMIT_EXCEEDED,
                    f"Underlying exposure {underlying_pct:.1f}% exceeds limit {underlying_limit:.1f}%.",
                )
        family = signal.strategy_family.value
        current_family = exposure.exposure_by_family.get(family, 0.0)
        family_gross = current_family + increment
        family_limit = profile.max_family_exposure_pct or config.exposure_policy.default_max_family_exposure_pct
        family_pct = family_gross / equity * 100.0 if equity > 0 else PERCENT_MAX
        if family_pct > family_limit + RISK_SCORE_EPSILON:
            return _StageOutcome(
                False,
                ERROR_EXPOSURE_LIMIT_EXCEEDED,
                f"Family exposure {family_pct:.1f}% exceeds limit {family_limit:.1f}%.",
            )
        state.factors.append(
            RiskFactor(
                factor_id="gross_exposure_pct",
                label="Gross Exposure %",
                weight=1.0,
                raw_value=gross_pct,
                normalized_value=gross_pct / gross_limit_pct if gross_limit_pct > 0 else 1.0,
                direction="NEUTRAL",
                stage_id=RiskStageId.EXPOSURE,
                limit_value=gross_limit_pct,
            )
        )
        if gross_pct >= gross_limit_pct * config.exposure_policy.near_limit_threshold:
            state.warnings.append(
                RiskWarningRecord(
                    code=WARN_EXPOSURE_NEAR_LIMIT,
                    message=f"Exposure {gross_pct:.1f}% near limit {gross_limit_pct:.1f}%.",
                    severity="WARNING",
                    stage_id=RiskStageId.EXPOSURE,
                )
            )
        return _StageOutcome(True, message="Exposure validation passed.")

    def _stage_portfolio_limits(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        profile = context.user_risk_profile
        exposure = context.portfolio.exposure_summary
        projected_count = exposure.open_position_count + 1
        if projected_count > profile.max_open_positions:
            return _StageOutcome(
                False,
                ERROR_PORTFOLIO_MAX_POSITIONS,
                f"Max open positions exceeded: {projected_count} > {profile.max_open_positions}.",
            )
        underlying = state.underlying
        if underlying:
            underlying_count = exposure.open_position_count_by_underlying.get(underlying, 0) + 1
            max_per_underlying = config.portfolio_limit_policy.max_positions_per_underlying
            if underlying_count > max_per_underlying:
                return _StageOutcome(
                    False,
                    ERROR_PORTFOLIO_MAX_POSITIONS,
                    f"Max positions per underlying exceeded for {underlying}.",
                )
        policy = config.portfolio_limit_policy
        if policy.max_single_underlying_concentration_pct is not None and underlying:
            projected_gross = exposure.gross_notional + state.proposed_notional
            underlying_gross = exposure.gross_notional_by_underlying.get(underlying, 0.0) + state.proposed_notional
            if projected_gross > 0:
                concentration = underlying_gross / projected_gross * 100.0
                if concentration > policy.max_single_underlying_concentration_pct + RISK_SCORE_EPSILON:
                    return _StageOutcome(
                        False,
                        ERROR_PORTFOLIO_CONCENTRATION,
                        f"Concentration {concentration:.1f}% exceeds limit.",
                    )
        if policy.warn_duplicate_strategy_position:
            strategy_id = context.trade_decision.selected_signal.strategy_id
            for position in context.portfolio.open_positions:
                if position.strategy_id == strategy_id:
                    state.warnings.append(
                        RiskWarningRecord(
                            code=WARN_PORTFOLIO_DUPLICATE_STRATEGY,
                            message=f"Duplicate open position for strategy {strategy_id}.",
                            severity="WARNING",
                            stage_id=RiskStageId.PORTFOLIO_LIMITS,
                        )
                    )
                    break
        state.factors.append(
            RiskFactor(
                factor_id="open_position_count",
                label="Open Position Count",
                weight=1.0,
                raw_value=float(projected_count),
                normalized_value=projected_count / profile.max_open_positions,
                direction="NEUTRAL",
                stage_id=RiskStageId.PORTFOLIO_LIMITS,
                limit_value=float(profile.max_open_positions),
            )
        )
        return _StageOutcome(True, message="Portfolio limits passed.")

    def _stage_daily_loss(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        mode = _resolved_execution_mode(context)
        if mode is StrategyExecutionMode.BACKTEST and config.loss_limit_policy.disable_in_backtest:
            return _StageOutcome(True, message="Daily loss check disabled in BACKTEST.")
        equity = context.portfolio.equity
        if equity <= 0:
            return _StageOutcome(False, ERROR_CAPITAL_EQUITY_NON_POSITIVE, "Equity non-positive.")
        daily_pnl = context.portfolio.daily_realized_pnl + context.portfolio.daily_unrealized_pnl
        loss_pct = compute_daily_loss_pct(daily_pnl=daily_pnl, equity=equity)
        limit = context.user_risk_profile.max_daily_loss_pct
        state.factors.append(
            RiskFactor(
                factor_id="daily_loss_pct",
                label="Daily Loss %",
                weight=1.0,
                raw_value=loss_pct,
                normalized_value=loss_pct / limit if limit > 0 else 1.0,
                direction="NEGATIVE",
                stage_id=RiskStageId.DAILY_LOSS,
                limit_value=limit,
            )
        )
        if loss_pct >= limit - RISK_SCORE_EPSILON:
            return _StageOutcome(
                False,
                ERROR_DAILY_LOSS_LIMIT,
                f"Daily loss {loss_pct:.2f}% exceeds limit {limit:.2f}%.",
            )
        if loss_pct >= limit * config.loss_limit_policy.near_limit_threshold:
            state.warnings.append(
                RiskWarningRecord(
                    code=WARN_DAILY_LOSS_NEAR_LIMIT,
                    message=f"Daily loss {loss_pct:.2f}% near limit {limit:.2f}%.",
                    severity="WARNING",
                    stage_id=RiskStageId.DAILY_LOSS,
                )
            )
        return _StageOutcome(True, message="Daily loss within limit.")

    def _stage_drawdown(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        equity = context.portfolio.equity
        peak = max(context.portfolio.peak_equity, equity)
        if peak <= 0:
            return _StageOutcome(False, ERROR_CONTEXT_INTEGRITY_FAILED, "Peak equity non-positive.")
        dd_pct = compute_drawdown_pct(equity=equity, peak_equity=peak)
        limit = context.user_risk_profile.max_drawdown_pct
        state.factors.append(
            RiskFactor(
                factor_id="drawdown_pct",
                label="Drawdown %",
                weight=1.0,
                raw_value=dd_pct,
                normalized_value=dd_pct / limit if limit > 0 else 1.0,
                direction="NEGATIVE",
                stage_id=RiskStageId.DRAWDOWN,
                limit_value=limit,
            )
        )
        if dd_pct >= limit - RISK_SCORE_EPSILON:
            return _StageOutcome(
                False,
                ERROR_DRAWDOWN_LIMIT,
                f"Drawdown {dd_pct:.2f}% exceeds limit {limit:.2f}%.",
            )
        if dd_pct >= limit * config.drawdown_policy.near_limit_threshold:
            state.warnings.append(
                RiskWarningRecord(
                    code=WARN_DRAWDOWN_NEAR_LIMIT,
                    message=f"Drawdown {dd_pct:.2f}% near limit {limit:.2f}%.",
                    severity="WARNING",
                    stage_id=RiskStageId.DRAWDOWN,
                )
            )
        return _StageOutcome(True, message="Drawdown within limit.")

    def _stage_consecutive_losses(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        streak = context.portfolio.consecutive_losses
        limit = context.user_risk_profile.max_consecutive_losses
        state.factors.append(
            RiskFactor(
                factor_id="consecutive_losses",
                label="Consecutive Losses",
                weight=1.0,
                raw_value=float(streak),
                normalized_value=streak / limit if limit > 0 else 1.0,
                direction="NEGATIVE",
                stage_id=RiskStageId.CONSECUTIVE_LOSSES,
                limit_value=float(limit),
            )
        )
        if streak > limit:
            return _StageOutcome(
                False,
                ERROR_CONSECUTIVE_LOSSES,
                f"Consecutive losses {streak} exceeds limit {limit}.",
            )
        return _StageOutcome(True, message="Consecutive loss limit not exceeded.")

    def _stage_user_risk_profile(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        profile = context.user_risk_profile
        floors = (
            ("max_risk_per_trade_pct", profile.max_risk_per_trade_pct, config.absolute_floor_max_risk_per_trade_pct),
            ("max_daily_loss_pct", profile.max_daily_loss_pct, config.absolute_floor_max_daily_loss_pct),
            ("max_drawdown_pct", profile.max_drawdown_pct, config.absolute_floor_max_drawdown_pct),
        )
        for name, value, floor in floors:
            if value < floor - RISK_SCORE_EPSILON:
                return _StageOutcome(
                    False,
                    ERROR_CONTEXT_INTEGRITY_FAILED,
                    f"Profile {name} below absolute floor {floor}.",
                )
        if profile.max_risk_per_trade_pct > profile.max_daily_loss_pct:
            state.warnings.append(
                RiskWarningRecord(
                    code="RISK.PROFILE.LIMIT_ORDERING",
                    message="max_risk_per_trade_pct exceeds max_daily_loss_pct.",
                    severity="INFO",
                    stage_id=RiskStageId.USER_RISK_PROFILE,
                )
            )
        return _StageOutcome(True, message="User risk profile validated.")

    def _stage_position_sizing_hint(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        mode = _resolved_execution_mode(context)
        hint = context.position_sizing_hint
        if hint is None:
            if mode is StrategyExecutionMode.LIVE and config.require_sizing_hint_in_live:
                return _StageOutcome(False, ERROR_SIZING_HINT_REQUIRED, "Position sizing hint required in LIVE.")
            state.warnings.append(
                RiskWarningRecord(
                    code=WARN_SIZING_HEURISTIC_FALLBACK,
                    message="No sizing hint; heuristic risk estimate used.",
                    severity="WARNING",
                    stage_id=RiskStageId.POSITION_SIZING_HINT,
                )
            )
            return _StageOutcome(True, message="Sizing hint not required; heuristic used.")
        if hint.proposed_risk_amount <= 0 or hint.proposed_risk_pct <= 0:
            return _StageOutcome(False, ERROR_SIZING_INVALID_HINT, "Invalid sizing hint values.")
        if hint.proposed_risk_amount > state.approved_budget + RISK_SCORE_EPSILON:
            excess = hint.proposed_risk_amount - state.approved_budget
            return _StageOutcome(
                False,
                ERROR_SIZING_EXCEEDS_BUDGET,
                f"Position sizing hint exceeds approved budget by {excess:.0f} INR.",
            )
        if hint.proposed_risk_pct > state.approved_pct + RISK_SCORE_EPSILON:
            return _StageOutcome(
                False,
                ERROR_SIZING_EXCEEDS_BUDGET,
                "Position sizing hint risk percent exceeds approved budget.",
            )
        state.factors.append(
            RiskFactor(
                factor_id="approved_risk_budget",
                label="Approved Risk Budget",
                weight=1.0,
                raw_value=state.approved_budget,
                normalized_value=hint.proposed_risk_amount / state.approved_budget
                if state.approved_budget > 0
                else 1.0,
                direction="POSITIVE",
                stage_id=RiskStageId.POSITION_SIZING_HINT,
            )
        )
        return _StageOutcome(True, message=f"Position sizing hint {hint.hint_id} within approved budget.")

    def _stage_strategy_restrictions(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        profile = context.user_risk_profile
        signal = context.trade_decision.selected_signal
        report = context.trade_decision.selected_report
        if signal.strategy_family is StrategyFamily.NO_STRATEGY:
            return _StageOutcome(False, ERROR_STRATEGY_BLOCKED, "NO_STRATEGY family blocked.")
        if signal.strategy_id in profile.blocked_strategy_ids:
            return _StageOutcome(
                False,
                ERROR_STRATEGY_BLOCKED,
                f"Strategy {signal.strategy_id} blocked by risk profile.",
            )
        if signal.strategy_family in profile.blocked_families:
            return _StageOutcome(
                False,
                ERROR_STRATEGY_BLOCKED,
                f"Strategy family {signal.strategy_family.value} blocked.",
            )
        if profile.allowed_families is not None and signal.strategy_family not in profile.allowed_families:
            return _StageOutcome(
                False,
                ERROR_STRATEGY_BLOCKED,
                f"Strategy family {signal.strategy_family.value} not in allowed set.",
            )
        if signal.risk and signal.risk.profile is RiskProfileHint.UNDEFINED and not profile.allow_undefined_risk:
            return _StageOutcome(
                False,
                ERROR_STRATEGY_UNDEFINED_RISK,
                f"Undefined-risk structure not permitted for profile {profile.profile_id}.",
            )
        if (
            report is not None
            and report.expected_risk.category is RiskEstimateCategory.UNDEFINED
            and config.strategy_restriction_policy.reject_undefined_risk_category
            and not profile.allow_undefined_risk
        ):
            return _StageOutcome(
                False,
                ERROR_STRATEGY_UNDEFINED_RISK,
                "Undefined expected risk category not permitted.",
            )
        return _StageOutcome(
            True,
            message=f"Strategy {signal.strategy_id} ({signal.strategy_family.value}) permitted.",
        )

    def _stage_allowed_underlyings(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        mode = _resolved_execution_mode(context)
        underlying = state.underlying
        profile = context.user_risk_profile
        if not underlying:
            if mode is StrategyExecutionMode.LIVE:
                return _StageOutcome(False, ERROR_UNDERLYING_MISSING, "Underlying not resolved.")
            return _StageOutcome(True, message="Underlying missing; allowed in non-LIVE mode.")
        if underlying in profile.blocked_underlyings:
            return _StageOutcome(False, ERROR_UNDERLYING_BLOCKED, f"Underlying {underlying} blocked.")
        if profile.allowed_underlyings is not None and underlying not in profile.allowed_underlyings:
            return _StageOutcome(False, ERROR_UNDERLYING_BLOCKED, f"Underlying {underlying} not allowed.")
        return _StageOutcome(True, message=f"Underlying {underlying} permitted.")

    def _stage_trading_window(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        config = state.config
        mode = _resolved_execution_mode(context)
        policy = config.trading_window_policy
        if mode is StrategyExecutionMode.ANALYSIS and policy.allow_analysis_outside_session:
            return _StageOutcome(True, message="Trading window bypassed in ANALYSIS mode.")
        ref = _resolved_reference_time(context)
        tz = ZoneInfo(policy.timezone)
        local_dt = ref.astimezone(tz)
        local_time = local_dt.time()
        expiry_day = context.tags.get("expiry_day") == "true"
        if mode is StrategyExecutionMode.LIVE:
            if local_time < policy.session_start or local_time >= policy.session_end:
                return _StageOutcome(
                    False,
                    ERROR_WINDOW_OUTSIDE_SESSION,
                    "Outside allowed risk trading window: outside session.",
                )
            minutes_to_close = _minutes_to_session_close(local_time, policy.session_end)
            cutoff = (
                policy.expiry_day_cutoff_minutes_before_close
                if expiry_day
                else policy.new_trade_cutoff_minutes_before_close
            )
            if minutes_to_close <= cutoff:
                code = ERROR_WINDOW_EXPIRY_CUTOFF if expiry_day else ERROR_WINDOW_NEAR_CLOSE
                return _StageOutcome(
                    False,
                    code,
                    f"Inside near-close cutoff ({minutes_to_close:.0f} min to close).",
                )
            if minutes_to_close <= policy.near_cutoff_warning_minutes:
                state.warnings.append(
                    RiskWarningRecord(
                        code=WARN_WINDOW_NEAR_CUTOFF,
                        message=f"Within {policy.near_cutoff_warning_minutes} minutes of cutoff.",
                        severity="WARNING",
                        stage_id=RiskStageId.TRADING_WINDOW,
                    )
                )
            for blackout in policy.blackout_windows:
                if _in_blackout(local_time, blackout):
                    return _StageOutcome(
                        False,
                        ERROR_WINDOW_BLACKOUT,
                        f"Inside blackout window {blackout.window_id}.",
                    )
        return _StageOutcome(True, message="Trading window validation passed.")

    def _stage_expiry_day(self, state: _PipelineState) -> _StageOutcome:
        context = state.run_context
        if context.tags.get("expiry_day") != "true":
            return _StageOutcome(True, message="Not an expiry day; check skipped.")
        profile = context.user_risk_profile
        max_expiry_pct = profile.max_risk_per_trade_pct * profile.expiry_day_multiplier
        if state.proposed_risk_pct > max_expiry_pct + RISK_SCORE_EPSILON:
            return _StageOutcome(
                False,
                ERROR_EXPIRY_DAY_LIMIT,
                f"Expiry-day risk {state.proposed_risk_pct:.2f}% exceeds limit {max_expiry_pct:.2f}%.",
            )
        return _StageOutcome(True, message="Expiry-day limits satisfied.")


class RiskExplanationBuilder:
    """Assembles risk reasons and factors for sealed results."""

    def build_reasons(
        self,
        *,
        verdict: RiskVerdict,
        pipeline_result: RiskPipelineResult,
        run_context: RiskRunContext,
        skip_reason_code: SkipReasonCode | None = None,
    ) -> list[RiskReason]:
        """Build human-readable reasons for a verdict."""
        if verdict is RiskVerdict.APPROVED:
            return [
                RiskReason(
                    code="RISK.APPROVE.ALL_CHECKS_PASSED",
                    message=f"All {pipeline_result.passed_stages} risk validation stages passed.",
                    severity="INFO",
                ),
                RiskReason(
                    code="RISK.APPROVE.MARGIN_HEURISTIC",
                    message="Heuristic margin validation passed; broker margin not queried (v1 policy).",
                    severity="INFO",
                    stage_id=RiskStageId.MARGIN_HEURISTIC,
                ),
            ]
        if verdict is RiskVerdict.REJECTED:
            failed = pipeline_result.failed_stage_id
            failed_stage = next(
                (stage for stage in pipeline_result.stages if stage.stage_id is failed),
                None,
            )
            code = failed_stage.rejection_code if failed_stage and failed_stage.rejection_code else "RISK.REJECT.GENERIC"
            message = failed_stage.message if failed_stage and failed_stage.message else "Risk review rejected."
            return [
                RiskReason(
                    code=code,
                    message=message,
                    severity="CRITICAL",
                    stage_id=failed,
                )
            ]
        skip_messages = {
            SkipReasonCode.DECISION_ABSTAIN: (
                "RISK.SKIP.ABSTAIN",
                f"Trade decision abstained ({run_context.trade_decision.abstain_reason_code}); risk review skipped.",
            ),
            SkipReasonCode.ORCHESTRATOR_SKIP: (
                "RISK.SKIP.ORCHESTRATOR",
                "Risk review skipped by orchestrator request.",
            ),
            SkipReasonCode.NOT_TRADE_CANDIDATE: (
                "RISK.SKIP.NOT_CANDIDATE",
                f"Decision outcome class {run_context.trade_decision.outcome_class.value} is not TRADE_CANDIDATE.",
            ),
            SkipReasonCode.ANALYSIS_MODE_SKIP: (
                "RISK.SKIP.ANALYSIS",
                "Risk review skipped in ANALYSIS mode per config.",
            ),
        }
        if skip_reason_code in skip_messages:
            reason_code, message = skip_messages[skip_reason_code]
        else:
            reason_code = "RISK.SKIP.GENERIC"
            message = f"Risk review skipped: {skip_reason_code.value if skip_reason_code else 'unknown'}."
        return [RiskReason(code=reason_code, message=message, severity="INFO")]

    def build_factors(
        self,
        *,
        pipeline_result: RiskPipelineResult,
        run_context: RiskRunContext,
        extra_factors: tuple[RiskFactor, ...] = (),
    ) -> list[RiskFactor]:
        """Build machine-readable factors."""
        factors: list[RiskFactor] = list(extra_factors)
        equity = run_context.portfolio.equity
        factors.append(
            RiskFactor(
                factor_id="equity",
                label="Account Equity",
                weight=1.0,
                raw_value=equity,
                normalized_value=1.0,
                direction="NEUTRAL",
            )
        )
        factors.append(
            RiskFactor(
                factor_id="confidence_score",
                label="Decision Confidence",
                weight=1.0,
                raw_value=run_context.trade_decision.confidence.overall_score,
                normalized_value=run_context.trade_decision.confidence.overall_score / 100.0,
                direction="POSITIVE",
            )
        )
        return factors


def risk_fingerprint(result: RiskDecisionResult) -> str:
    """Compute deterministic SHA-256 fingerprint for RiskDecisionResult."""
    payload = {
        "schema_version": RISK_ENGINE_SCHEMA_VERSION,
        "correlation_id": result.correlation_id,
        "decision_fingerprint": result.decision_fingerprint,
        "portfolio_fingerprint": result.portfolio_fingerprint,
        "verdict": result.verdict.value,
        "primary_rejection_code": result.primary_rejection_code,
        "skip_reason_code": result.skip_reason_code.value if result.skip_reason_code else None,
        "approved_risk_budget": _round_money(result.approved_risk_budget)
        if result.approved_risk_budget is not None
        else None,
        "approved_risk_pct": _round_pct(result.approved_risk_pct)
        if result.approved_risk_pct is not None
        else None,
        "signal_fingerprint": signal_fingerprint(result.trading_signal),
        "pipeline_passed": result.pipeline_summary.passed_stages,
        "pipeline_failed_stage": (
            result.pipeline_summary.failed_stage_id.value
            if result.pipeline_summary.failed_stage_id
            else None
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _pipeline_to_dict(pipeline: RiskPipelineResult) -> dict[str, Any]:
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


def _reason_to_dict(reason: RiskReason) -> dict[str, Any]:
    return {
        "code": reason.code,
        "message": reason.message,
        "severity": reason.severity,
        "stage_id": reason.stage_id.value if reason.stage_id else None,
    }


def _factor_to_dict(factor: RiskFactor) -> dict[str, Any]:
    return {
        "factor_id": factor.factor_id,
        "label": factor.label,
        "weight": factor.weight,
        "raw_value": factor.raw_value,
        "normalized_value": factor.normalized_value,
        "direction": factor.direction,
        "stage_id": factor.stage_id.value if factor.stage_id else None,
        "limit_value": factor.limit_value,
        "notes": factor.notes,
    }


def risk_to_dict(result: RiskDecisionResult) -> dict[str, Any]:
    """Convert RiskDecisionResult to JSON-serializable dict."""
    return {
        "schema_version": RISK_ENGINE_SCHEMA_VERSION,
        "risk_id": result.risk_id,
        "correlation_id": result.correlation_id,
        "decision_id": result.decision_id,
        "decision_fingerprint": result.decision_fingerprint,
        "portfolio_snapshot_id": result.portfolio_snapshot_id,
        "portfolio_fingerprint": result.portfolio_fingerprint,
        "verdict": result.verdict.value,
        "primary_rejection_code": result.primary_rejection_code,
        "skip_reason_code": result.skip_reason_code.value if result.skip_reason_code else None,
        "execution_mode": result.execution_mode.value,
        "approved_risk_budget": result.approved_risk_budget,
        "approved_risk_pct": result.approved_risk_pct,
        "risk_fingerprint": result.risk_fingerprint,
        "trading_signal": signal_to_dict(result.trading_signal),
        "pipeline_summary": _pipeline_to_dict(result.pipeline_summary),
        "reasons": [_reason_to_dict(reason) for reason in result.reasons],
        "factors": [_factor_to_dict(factor) for factor in result.factors],
        "reviewed_at": result.reviewed_at.isoformat(),
        "duration_ms": result.duration_ms,
    }


def risk_from_dict(data: Mapping[str, Any]) -> RiskDecisionResult:
    """Deserialize RiskDecisionResult from mapping."""
    schema_version = data.get("schema_version")
    if schema_version != RISK_ENGINE_SCHEMA_VERSION:
        raise RiskEngineValidationError(
            f"Unsupported schema version: {schema_version!r}.",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
        )
    pipeline_data = data["pipeline_summary"]
    stages = tuple(
        RiskStageResult(
            stage_id=RiskStageId(stage["stage_id"]),
            passed=bool(stage["passed"]),
            rejection_code=stage.get("rejection_code"),
            message=stage.get("message"),
            duration_ms=float(stage.get("duration_ms", 0.0)),
        )
        for stage in pipeline_data.get("stages", [])
    )
    pipeline = RiskPipelineResult(
        total_stages=int(pipeline_data["total_stages"]),
        passed_stages=int(pipeline_data["passed_stages"]),
        failed_stage_id=RiskStageId(pipeline_data["failed_stage_id"])
        if pipeline_data.get("failed_stage_id")
        else None,
        stages=stages,
        short_circuited=bool(pipeline_data.get("short_circuited", False)),
    )
    skip_raw = data.get("skip_reason_code")
    return RiskDecisionResult(
        risk_id=str(data["risk_id"]),
        correlation_id=str(data["correlation_id"]),
        decision_id=str(data["decision_id"]),
        decision_fingerprint=str(data["decision_fingerprint"]),
        portfolio_snapshot_id=str(data["portfolio_snapshot_id"]),
        portfolio_fingerprint=str(data["portfolio_fingerprint"]),
        verdict=RiskVerdict(str(data["verdict"])),
        trading_signal=signal_from_dict(data["trading_signal"]),
        execution_mode=StrategyExecutionMode(str(data["execution_mode"])),
        reasons=tuple(
            RiskReason(
                code=str(item["code"]),
                message=str(item["message"]),
                severity=str(item["severity"]),
                stage_id=RiskStageId(item["stage_id"]) if item.get("stage_id") else None,
            )
            for item in data.get("reasons", [])
        ),
        factors=tuple(
            RiskFactor(
                factor_id=str(item["factor_id"]),
                label=str(item["label"]),
                weight=float(item["weight"]),
                raw_value=float(item["raw_value"]),
                normalized_value=float(item["normalized_value"]),
                direction=str(item["direction"]),
                stage_id=RiskStageId(item["stage_id"]) if item.get("stage_id") else None,
                limit_value=float(item["limit_value"]) if item.get("limit_value") is not None else None,
                notes=item.get("notes"),
            )
            for item in data.get("factors", [])
        ),
        pipeline_summary=pipeline,
        reviewed_at=datetime.fromisoformat(str(data["reviewed_at"])),
        duration_ms=float(data["duration_ms"]),
        risk_fingerprint=str(data["risk_fingerprint"]),
        warnings=(),
        errors=(),
        primary_rejection_code=data.get("primary_rejection_code"),
        skip_reason_code=SkipReasonCode(skip_raw) if skip_raw else None,
        approved_risk_budget=data.get("approved_risk_budget"),
        approved_risk_pct=data.get("approved_risk_pct"),
    )


def risk_to_json(result: RiskDecisionResult, *, indent: int | None = None) -> str:
    """Serialize RiskDecisionResult to JSON string."""
    return json.dumps(risk_to_dict(result), indent=indent, sort_keys=True)


def risk_from_json(payload: str) -> RiskDecisionResult:
    """Deserialize RiskDecisionResult from JSON string."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RiskEngineValidationError(
            "Malformed risk JSON payload.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(data, dict):
        raise RiskEngineValidationError(
            "Risk JSON root must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return risk_from_dict(data)


class RiskEngine(BaseEngine):
    """Authoritative risk enforcement engine for THETA AI TRADER v1.0."""

    def __init__(
        self,
        config: RiskEngineConfig | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        pipeline: RiskValidationPipeline | None = None,
        re_raise_on_failure: bool = False,
    ) -> None:
        """Initialize risk engine with injected policies."""
        self._risk_config = config or default_risk_engine_config()
        self._clock = clock or _utc_now
        self._pipeline = pipeline or RiskValidationPipeline()
        self._explanation = RiskExplanationBuilder()
        super().__init__(
            config=MappingProxyType({"engine": "risk_engine"}),
            re_raise_on_failure=re_raise_on_failure,
        )

    @property
    def risk_config(self) -> RiskEngineConfig:
        """Return immutable risk engine configuration."""
        return self._risk_config

    @property
    def engine_name(self) -> str:
        """Return stable engine identifier."""
        return "risk_engine"

    @property
    def engine_version(self) -> str:
        """Return semantic engine version."""
        return RISK_ENGINE_VERSION

    def validate_configuration(self) -> None:
        """Validate static engine configuration."""
        _ = self._risk_config

    def validate_context(self, context: EngineContext) -> None:
        """Validate engine context wrapping risk run context."""
        super().validate_context(context)
        if not isinstance(context.payload, RiskRunContext):
            raise RiskEngineContextError(
                "EngineContext.payload must be RiskRunContext.",
                code=ERROR_CONTEXT_INVALID,
                field="payload",
            )

    def validate_run_context(self, run_context: RiskRunContext) -> None:
        """Validate risk run inputs; raise on fatal issues."""
        validate_run_context(run_context)

    def validate_risk_decision(self, result: RiskDecisionResult) -> RiskValidationResult:
        """Validate sealed risk decision output."""
        errors: list[RiskErrorRecord] = []
        warnings: list[RiskWarningRecord] = []
        if not result.reasons:
            errors.append(
                RiskErrorRecord(code=ERROR_RESULT_INVALID, message="reasons must not be empty.", field="reasons")
            )
        if result.verdict is RiskVerdict.APPROVED:
            if result.approved_risk_budget is None or result.approved_risk_pct is None:
                errors.append(
                    RiskErrorRecord(
                        code=ERROR_RESULT_INVALID,
                        message="APPROVED requires approved_risk_budget and approved_risk_pct.",
                    )
                )
            if result.pipeline_summary.failed_stage_id is not None:
                errors.append(
                    RiskErrorRecord(
                        code=ERROR_RESULT_INVALID,
                        message="APPROVED must not have failed pipeline stage.",
                    )
                )
        if result.verdict is RiskVerdict.REJECTED and not result.primary_rejection_code:
            errors.append(
                RiskErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="REJECTED requires primary_rejection_code.",
                    field="primary_rejection_code",
                )
            )
        if result.verdict is RiskVerdict.SKIPPED and result.skip_reason_code is None:
            errors.append(
                RiskErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="SKIPPED requires skip_reason_code.",
                    field="skip_reason_code",
                )
            )
        recomputed = risk_fingerprint(result)
        if recomputed != result.risk_fingerprint:
            errors.append(
                RiskErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="risk_fingerprint mismatch.",
                    field="risk_fingerprint",
                )
            )
        return RiskValidationResult(errors=tuple(errors), warnings=tuple(warnings))

    def assert_valid_risk_decision(self, result: RiskDecisionResult) -> None:
        """Raise when risk output is invalid."""
        validation = self.validate_risk_decision(result)
        if not validation.is_valid:
            first = validation.errors[0]
            raise RiskEngineValidationError(first.message, code=first.code, field=first.field)

    def evaluate(self, context: EngineContext | RiskRunContext) -> EngineResult:  # type: ignore[override]
        """Execute risk review and return engine result."""
        if isinstance(context, RiskRunContext):
            run_context = context
            correlation_id = context.correlation_id
            as_of = context.as_of
        elif isinstance(context, EngineContext):
            run_context = context.payload
            if not isinstance(run_context, RiskRunContext):
                raise RiskEngineContextError(
                    "EngineContext.payload must be RiskRunContext.",
                    code=ERROR_CONTEXT_INVALID,
                    field="payload",
                )
            correlation_id = context.correlation_id
            as_of = context.as_of
        else:
            raise RiskEngineContextError(
                "Context must be EngineContext or RiskRunContext.",
                code=ERROR_CONTEXT_INVALID,
                field="context",
            )

        started_at = self._clock()
        start_perf = time.perf_counter()
        decision = run_context.trade_decision
        _logger.info(
            "risk.review.start",
            extra={
                "event": "risk.review.start",
                "correlation_id": correlation_id,
                "decision_id": decision.decision_id,
                "decision_fingerprint": decision.decision_fingerprint,
            },
        )
        try:
            result = self.review(run_context)
            validation = self.validate_risk_decision(result)
            if not validation.is_valid:
                completed_at = self._clock()
                return EngineResult(
                    status=EngineStatus.REJECTED,
                    metadata=EngineMetadata(
                        engine_name=self.engine_name,
                        engine_version=self.engine_version,
                        correlation_id=correlation_id,
                        execution_id=correlation_id,
                        started_at=started_at,
                        completed_at=completed_at,
                        duration_ms=(time.perf_counter() - start_perf) * 1000.0,
                    ),
                    payload=None,
                    errors=tuple(
                        EngineErrorRecord(code=item.code, message=item.message, field=item.field)
                        for item in validation.errors
                    ),
                )
            completed_at = self._clock()
            duration_ms = (time.perf_counter() - start_perf) * 1000.0
            engine_warnings = tuple(
                EngineWarningRecord(code=item.code, message=item.message, field=item.field)
                for item in (*result.warnings, *validation.warnings)
            )
            log_event = {
                RiskVerdict.APPROVED: "risk.review.approved",
                RiskVerdict.REJECTED: "risk.review.rejected",
                RiskVerdict.SKIPPED: "risk.review.skip",
            }[result.verdict]
            _logger.info(
                "risk.review.complete",
                extra={
                    "event": "risk.review.complete",
                    "correlation_id": correlation_id,
                    "verdict": result.verdict.value,
                    "duration_ms": duration_ms,
                    "review_event": log_event,
                },
            )
            return EngineResult(
                status=EngineStatus.SUCCESS,
                metadata=EngineMetadata(
                    engine_name=self.engine_name,
                    engine_version=self.engine_version,
                    correlation_id=correlation_id,
                    execution_id=correlation_id,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                ),
                payload=result,
                warnings=engine_warnings,
            )
        except RiskEngineError as exc:
            completed_at = self._clock()
            _logger.error(
                "risk.review.failed",
                extra={"event": "risk.review.failed", "error_code": exc.code},
            )
            return EngineResult(
                status=EngineStatus.REJECTED,
                metadata=EngineMetadata(
                    engine_name=self.engine_name,
                    engine_version=self.engine_version,
                    correlation_id=correlation_id,
                    execution_id=correlation_id,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=(time.perf_counter() - start_perf) * 1000.0,
                ),
                payload=None,
                errors=(EngineErrorRecord(code=exc.code, message=str(exc), field=exc.field),),
            )
        except Exception as exc:
            completed_at = self._clock()
            _logger.error("risk.review.failed", extra={"event": "risk.review.failed"})
            return EngineResult(
                status=EngineStatus.FAILED,
                metadata=EngineMetadata(
                    engine_name=self.engine_name,
                    engine_version=self.engine_version,
                    correlation_id=correlation_id,
                    execution_id=correlation_id,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=(time.perf_counter() - start_perf) * 1000.0,
                ),
                payload=None,
                errors=(
                    EngineErrorRecord(
                        code=ERROR_RESULT_INVALID,
                        message=f"Unhandled risk review failure: {exc}",
                    ),
                ),
            )

    def review(self, run_context: RiskRunContext) -> RiskDecisionResult:
        """Core risk review returning sealed RiskDecisionResult."""
        started = time.perf_counter()
        self.validate_run_context(run_context)
        config = self._risk_config
        decision = run_context.trade_decision

        if run_context.force_skip:
            return self._build_skipped_result(
                run_context,
                skip_reason_code=SkipReasonCode.ORCHESTRATOR_SKIP,
                duration_ms=0.0,
            )

        eligibility = _evaluate_eligibility(decision)
        if not eligibility.eligible:
            return self._build_skipped_result(
                run_context,
                skip_reason_code=eligibility.skip_reason or SkipReasonCode.DECISION_NOT_SELECTED,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )

        execution_mode = _resolved_execution_mode(run_context)
        if (
            config.skip_review_in_analysis
            and execution_mode is StrategyExecutionMode.ANALYSIS
            and run_context.tags.get("force_review_in_analysis") != "true"
        ):
            return self._build_skipped_result(
                run_context,
                skip_reason_code=SkipReasonCode.ANALYSIS_MODE_SKIP,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )

        pipeline_apply = self._pipeline.apply(run_context, config=config)
        pipeline_result = pipeline_apply.pipeline
        verdict = (
            RiskVerdict.APPROVED
            if pipeline_result.failed_stage_id is None
            else RiskVerdict.REJECTED
        )
        approved_budget: float | None = None
        approved_pct: float | None = None
        if verdict is RiskVerdict.APPROVED:
            approved_budget, approved_pct = _compute_approved_budget(run_context, config)

        reasons = self._explanation.build_reasons(
            verdict=verdict,
            pipeline_result=pipeline_result,
            run_context=run_context,
        )
        factors = self._explanation.build_factors(
            pipeline_result=pipeline_result,
            run_context=run_context,
            extra_factors=pipeline_apply.factors,
        )
        duration_ms = (time.perf_counter() - started) * 1000.0
        result = RiskDecisionResult(
            risk_id=self._generate_risk_id(run_context),
            correlation_id=run_context.correlation_id,
            decision_id=decision.decision_id,
            decision_fingerprint=decision.decision_fingerprint,
            portfolio_snapshot_id=run_context.portfolio.snapshot_id,
            portfolio_fingerprint=run_context.portfolio.portfolio_fingerprint,
            verdict=verdict,
            primary_rejection_code=self._primary_rejection_code(pipeline_result),
            skip_reason_code=None,
            trading_signal=decision.selected_signal,
            evaluation_report=decision.selected_report,
            execution_mode=execution_mode,
            approved_risk_budget=approved_budget,
            approved_risk_pct=approved_pct,
            reasons=tuple(reasons),
            factors=tuple(factors),
            pipeline_summary=pipeline_result,
            reviewed_at=self._clock(),
            duration_ms=duration_ms,
            risk_fingerprint="",
            warnings=pipeline_apply.warnings,
            errors=(),
        )
        sealed = replace(result, risk_fingerprint=risk_fingerprint(result))
        self.assert_valid_risk_decision(sealed)
        return sealed

    def _primary_rejection_code(self, pipeline_result: RiskPipelineResult) -> str | None:
        """Return primary rejection code from first failed stage."""
        if pipeline_result.failed_stage_id is None:
            return None
        failed = next(
            (stage for stage in pipeline_result.stages if not stage.passed),
            None,
        )
        return failed.rejection_code if failed else None

    def _generate_risk_id(self, run_context: RiskRunContext) -> str:
        """Generate deterministic risk review identifier."""
        token = f"{run_context.correlation_id}:{run_context.trade_decision.decision_id}:{run_context.portfolio.snapshot_id}"
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
        reviewed = self._clock().strftime("%Y%m%d-%H%M%S")
        return f"risk-{reviewed}-{digest}"

    def _build_skipped_result(
        self,
        run_context: RiskRunContext,
        *,
        skip_reason_code: SkipReasonCode,
        duration_ms: float,
    ) -> RiskDecisionResult:
        """Build SKIPPED risk decision result."""
        decision = run_context.trade_decision
        eligibility_stage = RiskStageResult(
            stage_id=RiskStageId.DECISION_ELIGIBILITY,
            passed=False,
            message=f"Skipped: {skip_reason_code.value}.",
            duration_ms=0.0,
        )
        pipeline = RiskPipelineResult(
            total_stages=1,
            passed_stages=0,
            failed_stage_id=None,
            stages=(eligibility_stage,),
            short_circuited=True,
        )
        reasons = self._explanation.build_reasons(
            verdict=RiskVerdict.SKIPPED,
            pipeline_result=pipeline,
            run_context=run_context,
            skip_reason_code=skip_reason_code,
        )
        factors = self._explanation.build_factors(pipeline_result=pipeline, run_context=run_context)
        result = RiskDecisionResult(
            risk_id=self._generate_risk_id(run_context),
            correlation_id=run_context.correlation_id,
            decision_id=decision.decision_id,
            decision_fingerprint=decision.decision_fingerprint,
            portfolio_snapshot_id=run_context.portfolio.snapshot_id,
            portfolio_fingerprint=run_context.portfolio.portfolio_fingerprint,
            verdict=RiskVerdict.SKIPPED,
            primary_rejection_code=None,
            skip_reason_code=skip_reason_code,
            trading_signal=decision.selected_signal,
            evaluation_report=decision.selected_report,
            execution_mode=_resolved_execution_mode(run_context),
            approved_risk_budget=None,
            approved_risk_pct=None,
            reasons=tuple(reasons),
            factors=tuple(factors),
            pipeline_summary=pipeline,
            reviewed_at=self._clock(),
            duration_ms=duration_ms,
            risk_fingerprint="",
            warnings=(),
            errors=(),
        )
        sealed = replace(result, risk_fingerprint=risk_fingerprint(result))
        self.assert_valid_risk_decision(sealed)
        _logger.info(
            "risk.review.skip",
            extra={
                "event": "risk.review.skip",
                "correlation_id": run_context.correlation_id,
                "skip_reason_code": skip_reason_code.value,
            },
        )
        return sealed


__all__ = [
    "CapitalPolicy",
    "DEFAULT_EXPIRY_CUTOFF_MINUTES",
    "DEFAULT_MARGIN_TOLERANCE_PCT",
    "DEFAULT_NEAR_LIMIT_THRESHOLD",
    "DEFAULT_NEW_TRADE_CUTOFF_MINUTES",
    "DrawdownPolicy",
    "ExposurePolicy",
    "LossLimitPolicy",
    "MarginPolicy",
    "MarginValidationOutcome",
    "PERCENT_MAX",
    "PERCENT_MIN",
    "PortfolioExposureSummary",
    "PortfolioLimitPolicy",
    "PortfolioPosition",
    "PortfolioSnapshot",
    "PositionSizingHint",
    "RISK_ENGINE_SCHEMA_VERSION",
    "RISK_ENGINE_VERSION",
    "RISK_SCORE_EPSILON",
    "RiskDecisionResult",
    "RiskEngine",
    "RiskEngineConfig",
    "RiskEngineConfigurationError",
    "RiskEngineContextError",
    "RiskEngineDecisionError",
    "RiskEngineError",
    "RiskEngineValidationError",
    "RiskExplanationBuilder",
    "RiskFactor",
    "RiskPipelineResult",
    "RiskProfileTier",
    "RiskReason",
    "RiskRejectionSeverity",
    "RiskRunContext",
    "RiskStageId",
    "RiskStageResult",
    "RiskTimeWindow",
    "RiskTradingWindowPolicy",
    "RiskValidationPipeline",
    "RiskValidationResult",
    "RiskVerdict",
    "RiskWarningRecord",
    "RiskErrorRecord",
    "SizingHintValidationOutcome",
    "SkipReasonCode",
    "StrategyRestrictionPolicy",
    "UserRiskProfile",
    "compute_daily_loss_pct",
    "compute_drawdown_pct",
    "compute_heuristic_margin_demand",
    "default_risk_engine_config",
    "default_user_risk_profile",
    "portfolio_fingerprint",
    "risk_fingerprint",
    "risk_from_dict",
    "risk_from_json",
    "risk_to_dict",
    "risk_to_json",
    "validate_portfolio_snapshot",
    "validate_position_sizing_hint",
    "validate_run_context",
    "validate_user_risk_profile",
]
