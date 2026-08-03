"""Institutional trade decision engine for THETA AI TRADER v1.0.

Consumes immutable :class:`StrategyEvaluationBundle` outputs from the Strategy
Evaluation Engine and produces a single authoritative trade decision expressed
as a selected or abstain :class:`TradingSignal`. Never places orders, enforces
margin, or communicates with brokers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
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
from core.exceptions import EngineValidationError
from strategy.signals import (
    ConfidenceBand,
    RiskProfileHint,
    SignalAction,
    SignalConfidence,
    SignalDirection,
    SignalMarketContext,
    SignalStrength,
    StrategyExecutionMode,
    StrategyFamily,
    TradingSignal,
    confidence_band_for_score,
    from_dict as signal_from_dict,
    is_signal_expired,
    remaining_validity_seconds,
    signal_fingerprint,
    to_dict as signal_to_dict,
)
from strategy.strategy_evaluation_engine import (
    CapitalEstimateCategory,
    EvaluationOutcomeClass,
    EvaluationStatus,
    RiskEstimateCategory,
    StrategyEvaluationBundle,
    StrategyEvaluationReport,
    evaluation_fingerprint,
)

TRADE_DECISION_ENGINE_VERSION: Final[str] = "1.0.0"
TRADE_DECISION_SCHEMA_VERSION: Final[str] = "1.0.0"
DECISION_SCORE_EPSILON: Final[float] = 1e-9
DEFAULT_MIN_CONFIDENCE: Final[float] = 40.0
DEFAULT_MIN_SUITABILITY: Final[float] = 50.0
DEFAULT_MIN_RANKING: Final[float] = 50.0
DEFAULT_PREFERENCE_BOOST: Final[float] = 0.5
DEFAULT_MAX_BUNDLE_AGE_SECONDS: Final[int] = 300
SCORE_MIN: Final[float] = 0.0
SCORE_MAX: Final[float] = 100.0
POP_MIN: Final[float] = 0.0
POP_MAX: Final[float] = 1.0

_STRATEGY_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

ERROR_CONFIG_INVALID: Final[str] = "TRADE_DECISION.CONFIG.INVALID"
ERROR_CONTEXT_INVALID: Final[str] = "TRADE_DECISION.CONTEXT.INVALID"
ERROR_CONTEXT_BUNDLE_MISSING: Final[str] = "TRADE_DECISION.CONTEXT.BUNDLE_MISSING"
ERROR_CONTEXT_CORRELATION_MISMATCH: Final[str] = "TRADE_DECISION.CONTEXT.CORRELATION_MISMATCH"
ERROR_CONTEXT_MODE_INVALID: Final[str] = "TRADE_DECISION.CONTEXT.MODE_INVALID"
ERROR_CONTEXT_MANUAL_ID_MISSING: Final[str] = "TRADE_DECISION.CONTEXT.MANUAL_ID_MISSING"
ERROR_CONTEXT_NAIVE_TIMESTAMP: Final[str] = "TRADE_DECISION.CONTEXT.NAIVE_TIMESTAMP"
ERROR_BUNDLE_EMPTY: Final[str] = "TRADE_DECISION.BUNDLE.EMPTY"
ERROR_BUNDLE_STALE: Final[str] = "TRADE_DECISION.BUNDLE.STALE"
ERROR_BUNDLE_FINGERPRINT_DRIFT: Final[str] = "TRADE_DECISION.BUNDLE.FINGERPRINT_DRIFT"
ERROR_BUNDLE_INVALID: Final[str] = "TRADE_DECISION.BUNDLE.INVALID"
ERROR_MANUAL_NOT_IN_BUNDLE: Final[str] = "TRADE_DECISION.MANUAL.NOT_IN_BUNDLE"
ERROR_MANUAL_FILTER_REJECTED: Final[str] = "TRADE_DECISION.MANUAL.FILTER_REJECTED"
ERROR_FILTER_OUTCOME_CLASS: Final[str] = "TRADE_DECISION.FILTER.OUTCOME_CLASS"
ERROR_FILTER_EVALUATION_STATUS: Final[str] = "TRADE_DECISION.FILTER.EVALUATION_STATUS"
ERROR_FILTER_SIGNAL_ACTION: Final[str] = "TRADE_DECISION.FILTER.SIGNAL_ACTION"
ERROR_FILTER_SIGNAL_EXPIRED: Final[str] = "TRADE_DECISION.FILTER.SIGNAL_EXPIRED"
ERROR_FILTER_PREFERENCE_BLOCKED: Final[str] = "TRADE_DECISION.FILTER.PREFERENCE.BLOCKED"
ERROR_FILTER_PREFERENCE_FAMILY: Final[str] = "TRADE_DECISION.FILTER.PREFERENCE.FAMILY"
ERROR_FILTER_PREFERENCE_DIRECTION: Final[str] = "TRADE_DECISION.FILTER.PREFERENCE.DIRECTION"
ERROR_FILTER_PREFERENCE_UNDEFINED_RISK: Final[str] = "TRADE_DECISION.FILTER.PREFERENCE.UNDEFINED_RISK"
ERROR_FILTER_THRESHOLD_SUITABILITY: Final[str] = "TRADE_DECISION.FILTER.THRESHOLD.SUITABILITY"
ERROR_FILTER_THRESHOLD_RANKING: Final[str] = "TRADE_DECISION.FILTER.THRESHOLD.RANKING"
ERROR_FILTER_THRESHOLD_CONFIDENCE: Final[str] = "TRADE_DECISION.FILTER.THRESHOLD.CONFIDENCE"
ERROR_FILTER_RISK_REWARD_RISK: Final[str] = "TRADE_DECISION.FILTER.RISK_REWARD.RISK"
ERROR_FILTER_RISK_REWARD_REWARD: Final[str] = "TRADE_DECISION.FILTER.RISK_REWARD.REWARD"
ERROR_FILTER_CAPITAL_SCORE: Final[str] = "TRADE_DECISION.FILTER.CAPITAL.SCORE"
ERROR_FILTER_CAPITAL_ALLOCATION: Final[str] = "TRADE_DECISION.FILTER.CAPITAL.ALLOCATION"
ERROR_FILTER_WINDOW_SESSION: Final[str] = "TRADE_DECISION.FILTER.WINDOW.SESSION"
ERROR_FILTER_WINDOW_CUTOFF: Final[str] = "TRADE_DECISION.FILTER.WINDOW.CUTOFF"
ERROR_FILTER_WINDOW_BLACKOUT: Final[str] = "TRADE_DECISION.FILTER.WINDOW.BLACKOUT"
ERROR_FILTER_MANUAL_NOT_FOUND: Final[str] = "TRADE_DECISION.FILTER.MANUAL.NOT_FOUND"
ERROR_SELECT_NO_CANDIDATES: Final[str] = "TRADE_DECISION.SELECT.NO_CANDIDATES"
ERROR_RESULT_INVALID: Final[str] = "TRADE_DECISION.RESULT.INVALID"
ERROR_SERIALIZATION_UNSUPPORTED_VERSION: Final[str] = "TRADE_DECISION.SERIALIZATION.UNSUPPORTED_VERSION"
ERROR_SERIALIZATION_MALFORMED: Final[str] = "TRADE_DECISION.SERIALIZATION.MALFORMED"
WARN_BUNDLE_EMPTY: Final[str] = "TRADE_DECISION.BUNDLE.EMPTY"
WARN_BUNDLE_STALE: Final[str] = "TRADE_DECISION.BUNDLE.STALE"
WARN_BUNDLE_FINGERPRINT_DRIFT: Final[str] = "TRADE_DECISION.BUNDLE.FINGERPRINT_DRIFT"
WARN_SELECT_NO_CANDIDATES: Final[str] = "TRADE_DECISION.SELECT.NO_CANDIDATES"
WARN_WINDOW_NEAR_CUTOFF: Final[str] = "TRADE_DECISION.WINDOW.NEAR_CUTOFF"
WARN_MANUAL_OVERRIDE: Final[str] = "TRADE_DECISION.MANUAL.OVERRIDE_APPLIED"
WARN_CAPITAL_NEAR_LIMIT: Final[str] = "TRADE_DECISION.CAPITAL.NEAR_LIMIT"
WARN_MODE_IGNORED_MANUAL_ID: Final[str] = "TRADE_DECISION.MODE.IGNORED_MANUAL_ID"
WARN_CONFIDENCE_DOWNGRADED: Final[str] = "TRADE_DECISION.CONFIDENCE.DOWNGRADED"
WARN_FORCE_ABSTAIN_MANUAL: Final[str] = "TRADE_DECISION.MODE.FORCE_ABSTAIN_MANUAL"

_STRENGTH_ORDINAL: Final[dict[SignalStrength, int]] = {
    SignalStrength.NONE: 0,
    SignalStrength.WEAK: 1,
    SignalStrength.MODERATE: 2,
    SignalStrength.STRONG: 3,
    SignalStrength.EXCEPTIONAL: 4,
}

_HARD_FILTER_STAGES: Final[frozenset[FilterStageId]] = frozenset()  # patched after FilterStageId

_logger = logging.getLogger(__name__)


class DecisionMode(str, Enum):
    """Autonomous or manual strategy selection mode."""

    AUTONOMOUS = "autonomous"
    MANUAL = "manual"


class DecisionStatus(str, Enum):
    """High-level trade decision outcome."""

    SELECTED = "selected"
    ABSTAIN = "abstain"
    REJECTED = "rejected"
    MANUAL_INVALID = "manual_invalid"
    WINDOW_CLOSED = "window_closed"
    NO_CANDIDATES = "no_candidates"


class DecisionOutcomeClass(str, Enum):
    """Downstream actionability classification."""

    TRADE_CANDIDATE = "trade_candidate"
    MONITOR_ONLY = "monitor_only"
    NO_TRADE = "no_trade"
    ERROR = "error"


class FilterStageId(str, Enum):
    """Ordered filter pipeline stage identifiers."""

    OUTCOME_CLASS = "outcome_class"
    EVALUATION_STATUS = "evaluation_status"
    SIGNAL_ACTION = "signal_action"
    SIGNAL_FRESHNESS = "signal_freshness"
    USER_PREFERENCES = "user_preferences"
    SUITABILITY_THRESHOLD = "suitability_threshold"
    RISK_REWARD_BAND = "risk_reward_band"
    CAPITAL_PRECHECK = "capital_precheck"
    TRADING_WINDOW = "trading_window"
    MANUAL_TARGET = "manual_target"


class ManualOverridePolicy(str, Enum):
    """Manual selection override behavior."""

    STRICT = "strict"
    ALLOW_WITH_WARNING = "allow_with_warning"
    ALLOW_WINDOW_OVERRIDE = "allow_window_override"


class AbstainReasonCode(str, Enum):
    """Structured abstain reason codes."""

    NO_ACTIONABLE_REPORTS = "no_actionable_reports"
    ALL_FILTERED = "all_filtered"
    BELOW_MIN_CONFIDENCE = "below_min_confidence"
    CAPITAL_PRECHECK_FAILED = "capital_precheck_failed"
    TRADING_WINDOW_CLOSED = "trading_window_closed"
    MANUAL_STRATEGY_INELIGIBLE = "manual_strategy_ineligible"
    USER_BLOCKED = "user_blocked"
    SIGNAL_EXPIRED = "signal_expired"
    EMPTY_BUNDLE = "empty_bundle"
    POLICY_ABSTAIN = "policy_abstain"


# Populate hard filter stages after FilterStageId is defined.
_HARD_FILTER_STAGES = frozenset(
    {
        FilterStageId.EVALUATION_STATUS,
        FilterStageId.SIGNAL_FRESHNESS,
    }
)


class TradeDecisionError(Exception):
    """Base exception for trade decision failures."""

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


class TradeDecisionConfigurationError(TradeDecisionError):
    """Raised when engine configuration is invalid."""


class TradeDecisionValidationError(TradeDecisionError):
    """Raised when input or output validation fails."""


class TradeDecisionContextError(TradeDecisionError):
    """Raised when decision run context is invalid."""


class TradeDecisionBundleError(TradeDecisionError):
    """Raised when evaluation bundle integrity fails."""


@dataclass(frozen=True)
class DecisionFilterPolicy:
    """Filter stage thresholds and behavior."""

    allowed_outcome_classes_live: frozenset[EvaluationOutcomeClass] = frozenset(
        {EvaluationOutcomeClass.ACTIONABLE}
    )
    allowed_outcome_classes_analysis: frozenset[EvaluationOutcomeClass] = frozenset(
        {EvaluationOutcomeClass.ACTIONABLE, EvaluationOutcomeClass.MONITOR}
    )
    allowed_signal_actions_live: frozenset[SignalAction] = frozenset({SignalAction.EVALUATE})
    allowed_signal_actions_analysis: frozenset[SignalAction] = frozenset(
        {SignalAction.EVALUATE, SignalAction.WAIT}
    )
    preference_boost_score: float = DEFAULT_PREFERENCE_BOOST
    default_min_confidence: float = DEFAULT_MIN_CONFIDENCE
    default_min_suitability: float = DEFAULT_MIN_SUITABILITY
    default_min_ranking: float = DEFAULT_MIN_RANKING

    def __post_init__(self) -> None:
        for name, value in (
            ("preference_boost_score", self.preference_boost_score),
            ("default_min_confidence", self.default_min_confidence),
            ("default_min_suitability", self.default_min_suitability),
            ("default_min_ranking", self.default_min_ranking),
        ):
            if not (SCORE_MIN <= value <= SCORE_MAX):
                raise TradeDecisionConfigurationError(
                    f"{name} must be in [0, 100].",
                    code=ERROR_CONFIG_INVALID,
                    field=name,
                )
        if not self.allowed_outcome_classes_live:
            raise TradeDecisionConfigurationError(
                "allowed_outcome_classes_live must not be empty.",
                code=ERROR_CONFIG_INVALID,
                field="allowed_outcome_classes_live",
            )
        if not self.allowed_outcome_classes_analysis:
            raise TradeDecisionConfigurationError(
                "allowed_outcome_classes_analysis must not be empty.",
                code=ERROR_CONFIG_INVALID,
                field="allowed_outcome_classes_analysis",
            )


@dataclass(frozen=True)
class BlackoutWindow:
    """Named intraday blackout interval."""

    window_id: str
    start_time: dt_time
    end_time: dt_time
    days_of_week: frozenset[int] | None = None
    underlying_scope: frozenset[str] | None = None
    reason: str = ""


@dataclass(frozen=True)
class TradingWindowPolicy:
    """NSE session and blackout validation policy."""

    timezone: str = "Asia/Kolkata"
    regular_session_open: dt_time = dt_time(9, 15)
    regular_session_close: dt_time = dt_time(15, 30)
    live_entry_cutoff: dt_time = dt_time(15, 15)
    live_force_exit: dt_time = dt_time(15, 20)
    blackout_windows: tuple[BlackoutWindow, ...] = ()
    allow_analysis_outside_session: bool = True
    allow_backtest_any_time: bool = True
    reject_partial_snapshot_outside_session: bool = False


@dataclass(frozen=True)
class CapitalPolicy:
    """Informational capital pre-check bounds."""

    enabled: bool = True
    max_capital_normalized_score: float = 85.0
    max_allocation_percent_hint: float | None = 10.0
    min_available_capital_hint: float | None = None
    reject_unknown_capital: bool = False
    evaluation_capital_pool_hint: float | None = 1_000_000.0


@dataclass(frozen=True)
class UserPreferences:
    """User preference constraints applied during filtering."""

    allowed_families: frozenset[StrategyFamily] | None = None
    blocked_strategy_ids: frozenset[str] = frozenset()
    preferred_strategy_ids: frozenset[str] = frozenset()
    min_confidence_score: float = DEFAULT_MIN_CONFIDENCE
    min_suitability_score: float = DEFAULT_MIN_SUITABILITY
    min_ranking_score: float = DEFAULT_MIN_RANKING
    min_expected_pop: float | None = None
    allowed_directions: frozenset[SignalDirection] | None = None
    exclude_undefined_risk: bool = True
    max_risk_normalized_score: float | None = None
    min_reward_normalized_score: float | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class TradeDecisionEngineConfig:
    """Immutable configuration for :class:`TradeDecisionEngine`."""

    filter_policy: DecisionFilterPolicy = field(default_factory=DecisionFilterPolicy)
    window_policy: TradingWindowPolicy = field(default_factory=TradingWindowPolicy)
    capital_policy: CapitalPolicy = field(default_factory=CapitalPolicy)
    manual_override_policy: ManualOverridePolicy = ManualOverridePolicy.STRICT
    strict_correlation: bool = True
    strict_bundle_freshness: bool = False
    max_bundle_age_seconds: int = DEFAULT_MAX_BUNDLE_AGE_SECONDS
    abstain_action: SignalAction = SignalAction.ABSTAIN
    allow_monitor_in_live: bool = False
    allow_expired_in_analysis: bool = True
    deterministic_fingerprint: bool = True
    strict_no_actionable: bool = False

    def __post_init__(self) -> None:
        if self.max_bundle_age_seconds <= 0:
            raise TradeDecisionConfigurationError(
                "max_bundle_age_seconds must be positive.",
                code=ERROR_CONFIG_INVALID,
                field="max_bundle_age_seconds",
            )
        if self.abstain_action not in (SignalAction.ABSTAIN, SignalAction.NO_TRADE):
            raise TradeDecisionConfigurationError(
                "abstain_action must be ABSTAIN or NO_TRADE.",
                code=ERROR_CONFIG_INVALID,
                field="abstain_action",
            )


@dataclass(frozen=True)
class DecisionRunContext:
    """Immutable per-run decision inputs."""

    correlation_id: str
    as_of: datetime
    bundle: StrategyEvaluationBundle
    mode: DecisionMode
    preferences: UserPreferences
    execution_mode: StrategyExecutionMode | None = None
    reference_time: datetime | None = None
    manual_strategy_id: str | None = None
    force_abstain: bool = False
    snapshot: object | None = None
    available_capital_hint: float | None = None
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class DecisionFactor:
    """Machine-readable decision factor."""

    factor_id: str
    label: str
    weight: float
    raw_value: float
    normalized_value: float
    direction: str
    stage_id: FilterStageId | None = None
    notes: str | None = None


@dataclass(frozen=True)
class DecisionReason:
    """Human-readable decision explanation bullet."""

    code: str
    message: str
    severity: str
    strategy_id: str | None = None


@dataclass(frozen=True)
class DecisionConfidence:
    """Propagated decision confidence."""

    overall_score: float
    band: ConfidenceBand
    decision_adjustment: float
    method: str
    components: tuple[DecisionFactor, ...]
    evaluation_confidence: float | None = None
    signal_confidence: float | None = None


@dataclass(frozen=True)
class DecisionWarningRecord:
    """Non-fatal decision warning."""

    code: str
    message: str
    severity: str = "WARNING"
    strategy_id: str | None = None
    field: str | None = None


@dataclass(frozen=True)
class DecisionErrorRecord:
    """Structured decision error."""

    code: str
    message: str
    strategy_id: str | None = None
    field: str | None = None


@dataclass(frozen=True)
class FilterStageResult:
    """Per-stage filter audit record."""

    stage_id: FilterStageId
    input_count: int
    output_count: int
    eliminated: tuple[str, ...]
    elimination_reasons: Mapping[str, str]
    duration_ms: float


@dataclass(frozen=True)
class FilterPipelineResult:
    """Complete filter pipeline audit trail."""

    initial_count: int
    final_count: int
    stages: tuple[FilterStageResult, ...]
    eliminated_strategy_ids: frozenset[str]
    remaining_strategy_ids: tuple[str, ...]


@dataclass(frozen=True)
class SelectionOutcome:
    """Internal selection result before sealing."""

    selected_report: StrategyEvaluationReport | None = None
    abstain_reason_code: AbstainReasonCode | None = None
    manual_invalid_code: str | None = None
    manual_strategy_id: str | None = None
    alternatives: tuple[StrategyEvaluationReport, ...] = ()
    override_warnings: tuple[DecisionWarningRecord, ...] = ()

    @classmethod
    def selected(
        cls,
        report: StrategyEvaluationReport,
        *,
        alternatives: tuple[StrategyEvaluationReport, ...] = (),
        override_warnings: tuple[DecisionWarningRecord, ...] = (),
    ) -> SelectionOutcome:
        """Return a successful selection outcome."""
        return cls(
            selected_report=report,
            alternatives=alternatives,
            override_warnings=override_warnings,
        )

    @classmethod
    def abstain(cls, code: AbstainReasonCode) -> SelectionOutcome:
        """Return an abstain outcome."""
        return cls(abstain_reason_code=code)

    @classmethod
    def manual_invalid(cls, *, code: str, strategy_id: str) -> SelectionOutcome:
        """Return a manual invalid outcome."""
        return cls(manual_invalid_code=code, manual_strategy_id=strategy_id)

    @property
    def is_selected(self) -> bool:
        """Return whether a report was selected."""
        return self.selected_report is not None

    @property
    def is_abstain(self) -> bool:
        """Return whether the outcome is abstain."""
        return self.abstain_reason_code is not None

    @property
    def is_manual_invalid(self) -> bool:
        """Return whether manual selection was invalid."""
        return self.manual_invalid_code is not None


@dataclass(frozen=True)
class DecisionSummary:
    """Compact decision summary for serialization."""

    decision_status: DecisionStatus
    selected_strategy_id: str | None
    abstain_reason_code: AbstainReasonCode | None
    filter_initial_count: int
    filter_final_count: int


@dataclass(frozen=True)
class TradeDecisionResult:
    """Immutable sealed trade decision outcome."""

    decision_id: str
    correlation_id: str
    bundle_id: str
    bundle_fingerprint: str
    decision_status: DecisionStatus
    outcome_class: DecisionOutcomeClass
    mode: DecisionMode
    execution_mode: StrategyExecutionMode
    selected_signal: TradingSignal
    confidence: DecisionConfidence
    reasons: tuple[DecisionReason, ...]
    factors: tuple[DecisionFactor, ...]
    filter_summary: FilterPipelineResult
    decided_at: datetime
    duration_ms: float
    decision_fingerprint: str
    warnings: tuple[DecisionWarningRecord, ...]
    errors: tuple[DecisionErrorRecord, ...]
    selected_report: StrategyEvaluationReport | None = None
    selected_strategy_id: str | None = None
    abstain_reason_code: AbstainReasonCode | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class DecisionValidationResult:
    """Output validation outcome."""

    errors: tuple[DecisionErrorRecord, ...] = ()
    warnings: tuple[DecisionWarningRecord, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return ``True`` when no validation errors exist."""
        return not self.errors


def default_user_preferences() -> UserPreferences:
    """Return conservative default user preferences for v1."""
    return UserPreferences(
        allowed_families=None,
        blocked_strategy_ids=frozenset(),
        preferred_strategy_ids=frozenset(),
        min_confidence_score=DEFAULT_MIN_CONFIDENCE,
        min_suitability_score=DEFAULT_MIN_SUITABILITY,
        min_ranking_score=DEFAULT_MIN_RANKING,
        min_expected_pop=None,
        allowed_directions=None,
        exclude_undefined_risk=True,
        max_risk_normalized_score=None,
        min_reward_normalized_score=None,
    )


def _utc_now() -> datetime:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp value to inclusive bounds."""
    return max(minimum, min(maximum, value))


def _round_score(value: float) -> float:
    """Round score to four decimal places."""
    return round(value, 4)


def _validate_strategy_id_set(ids: frozenset[str], *, field: str) -> None:
    """Validate strategy ID format for preference sets."""
    for strategy_id in ids:
        if not _STRATEGY_ID_PATTERN.match(strategy_id):
            raise TradeDecisionValidationError(
                f"Invalid strategy_id format in {field}: {strategy_id!r}.",
                code=ERROR_CONTEXT_INVALID,
                field=field,
            )


def _validate_user_preferences(preferences: UserPreferences) -> None:
    """Validate user preference internal consistency."""
    for name, value in (
        ("min_confidence_score", preferences.min_confidence_score),
        ("min_suitability_score", preferences.min_suitability_score),
        ("min_ranking_score", preferences.min_ranking_score),
    ):
        if not (SCORE_MIN <= value <= SCORE_MAX):
            raise TradeDecisionValidationError(
                f"{name} must be in [0, 100].",
                code=ERROR_CONTEXT_INVALID,
                field=name,
            )
    if preferences.min_expected_pop is not None and not (POP_MIN <= preferences.min_expected_pop <= POP_MAX):
        raise TradeDecisionValidationError(
            "min_expected_pop must be in [0, 1].",
            code=ERROR_CONTEXT_INVALID,
            field="min_expected_pop",
        )
    if preferences.allowed_families is not None and len(preferences.allowed_families) == 0:
        raise TradeDecisionValidationError(
            "allowed_families must be None (all) or non-empty.",
            code=ERROR_CONTEXT_INVALID,
            field="allowed_families",
        )
    if preferences.allowed_directions is not None and len(preferences.allowed_directions) == 0:
        raise TradeDecisionValidationError(
            "allowed_directions must be None (all) or non-empty.",
            code=ERROR_CONTEXT_INVALID,
            field="allowed_directions",
        )
    overlap = preferences.blocked_strategy_ids & preferences.preferred_strategy_ids
    if overlap:
        raise TradeDecisionValidationError(
            "blocked_strategy_ids and preferred_strategy_ids must not overlap.",
            code=ERROR_CONTEXT_INVALID,
            field="blocked_strategy_ids",
        )
    _validate_strategy_id_set(preferences.blocked_strategy_ids, field="blocked_strategy_ids")
    _validate_strategy_id_set(preferences.preferred_strategy_ids, field="preferred_strategy_ids")


def _is_timezone_aware(value: datetime) -> bool:
    """Return whether datetime is timezone-aware."""
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _resolved_reference_time(context: DecisionRunContext) -> datetime:
    """Return effective reference time for staleness and window checks."""
    return context.reference_time or context.as_of


def _resolved_execution_mode(context: DecisionRunContext) -> StrategyExecutionMode:
    """Return effective execution mode."""
    return context.execution_mode or context.bundle.execution_mode


def _default_blackout_windows() -> tuple[BlackoutWindow, ...]:
    """Return default v1 blackout windows."""
    return (
        BlackoutWindow(
            window_id="opening_volatility",
            start_time=dt_time(9, 15),
            end_time=dt_time(9, 30),
            reason="Opening volatility blackout for short premium",
        ),
        BlackoutWindow(
            window_id="expiry_day_last_hour",
            start_time=dt_time(14, 30),
            end_time=dt_time(15, 30),
            reason="Expiry day gamma risk blackout",
        ),
    )


def default_trading_window_policy() -> TradingWindowPolicy:
    """Return NSE-default trading window policy with standard blackouts."""
    return TradingWindowPolicy(blackout_windows=_default_blackout_windows())


def default_trade_decision_engine_config() -> TradeDecisionEngineConfig:
    """Return production-default engine configuration."""
    return TradeDecisionEngineConfig(window_policy=default_trading_window_policy())


def _strength_ordinal(signal: TradingSignal | None) -> int:
    """Return ordinal for signal strength tie-breaking."""
    if signal is None:
        return 0
    strength = signal.resolved_strength
    return _STRENGTH_ORDINAL.get(strength, 0)


def _selection_sort_key(
    report: StrategyEvaluationReport,
    preferences: UserPreferences,
    policy: DecisionFilterPolicy,
) -> tuple[float, ...]:
    """Build deterministic descending selection sort key."""
    boost = (
        policy.preference_boost_score
        if report.strategy_id in preferences.preferred_strategy_ids
        else 0.0
    )
    pref_match = 1.0 if report.strategy_id in preferences.preferred_strategy_ids else 0.0
    strength_ord = float(_strength_ordinal(report.signal))
    return (
        -_round_score(report.ranking_score + boost),
        -_round_score(report.suitability_score),
        -_round_score(report.confidence.overall_score),
        -_round_score(report.expected_pop),
        -float(report.plugin_priority),
        -_round_score(report.expected_reward.normalized_score),
        _round_score(report.expected_risk.normalized_score),
        _round_score(report.capital_estimate.normalized_score),
        -pref_match,
        -strength_ord,
        report.strategy_id,
    )


def _allowed_outcome_classes(
    execution_mode: StrategyExecutionMode,
    policy: DecisionFilterPolicy,
    *,
    allow_monitor_in_live: bool,
) -> frozenset[EvaluationOutcomeClass]:
    """Resolve allowed outcome classes for execution mode."""
    if execution_mode is StrategyExecutionMode.LIVE:
        allowed = set(policy.allowed_outcome_classes_live)
        if allow_monitor_in_live:
            allowed.add(EvaluationOutcomeClass.MONITOR)
        return frozenset(allowed)
    return policy.allowed_outcome_classes_analysis


def _allowed_signal_actions(
    execution_mode: StrategyExecutionMode,
    policy: DecisionFilterPolicy,
) -> frozenset[SignalAction]:
    """Resolve allowed signal actions for execution mode."""
    if execution_mode is StrategyExecutionMode.LIVE:
        return policy.allowed_signal_actions_live
    return policy.allowed_signal_actions_analysis


def _is_expiry_day(context: DecisionRunContext, local_date: object) -> bool:
    """Detect expiry day from tags or snapshot metadata."""
    if context.tags.get("is_expiry_day", "").lower() == "true":
        return True
    if context.tags.get("session_tag", "").lower() == "expiry_day":
        return True
    return False


def _in_blackout(
    *,
    local_time: dt_time,
    weekday: int,
    underlying: str,
    blackout: BlackoutWindow,
    is_expiry_day: bool,
) -> bool:
    """Return whether local time falls in blackout window."""
    if blackout.window_id == "expiry_day_last_hour" and not is_expiry_day:
        return False
    if blackout.days_of_week is not None and weekday not in blackout.days_of_week:
        return False
    if blackout.underlying_scope is not None:
        if underlying.upper() not in {u.upper() for u in blackout.underlying_scope}:
            return False
    if blackout.start_time <= local_time < blackout.end_time:
        return True
    return False


@dataclass(frozen=True)
class _WindowCheckResult:
    """Trading window validation outcome."""

    passed: bool
    reason_code: str | None = None
    near_cutoff: bool = False


def _validate_trading_window(
    *,
    context: DecisionRunContext,
    report: StrategyEvaluationReport,
    window_policy: TradingWindowPolicy,
    execution_mode: StrategyExecutionMode,
) -> _WindowCheckResult:
    """Validate trading window for one report."""
    if execution_mode is StrategyExecutionMode.BACKTEST and window_policy.allow_backtest_any_time:
        return _WindowCheckResult(passed=True)
    if execution_mode is StrategyExecutionMode.ANALYSIS and window_policy.allow_analysis_outside_session:
        return _WindowCheckResult(passed=True)

    ref = _resolved_reference_time(context)
    tz = ZoneInfo(window_policy.timezone)
    local_dt = ref.astimezone(tz)
    local_time = local_dt.time()
    weekday = local_dt.isoweekday()
    underlying = report.signal.market.underlying if report.signal else "UNKNOWN"
    expiry_day = _is_expiry_day(context, local_dt.date())

    if local_time < window_policy.regular_session_open or local_time >= window_policy.regular_session_close:
        return _WindowCheckResult(passed=False, reason_code=ERROR_FILTER_WINDOW_SESSION)
    if execution_mode is StrategyExecutionMode.LIVE and local_time >= window_policy.live_entry_cutoff:
        return _WindowCheckResult(passed=False, reason_code=ERROR_FILTER_WINDOW_CUTOFF)

    near_cutoff = (
        execution_mode is StrategyExecutionMode.LIVE
        and window_policy.live_entry_cutoff <= local_time < window_policy.live_force_exit
    )

    for blackout in window_policy.blackout_windows:
        if _in_blackout(
            local_time=local_time,
            weekday=weekday,
            underlying=underlying,
            blackout=blackout,
            is_expiry_day=expiry_day,
        ):
            return _WindowCheckResult(passed=False, reason_code=ERROR_FILTER_WINDOW_BLACKOUT)

    if report.signal is not None and report.signal.time_validity is not None:
        cutoff = report.signal.time_validity.expiry_session_cutoff
        if cutoff is not None and local_time >= cutoff:
            return _WindowCheckResult(passed=False, reason_code=ERROR_FILTER_WINDOW_CUTOFF)

    return _WindowCheckResult(passed=True, near_cutoff=near_cutoff)


def _capital_precheck_passes(
    report: StrategyEvaluationReport,
    context: DecisionRunContext,
    policy: CapitalPolicy,
) -> tuple[bool, str | None]:
    """Return whether report passes informational capital pre-check."""
    if not policy.enabled:
        return True, None
    estimate = report.capital_estimate
    if estimate.category is CapitalEstimateCategory.UNKNOWN and policy.reject_unknown_capital:
        return False, ERROR_FILTER_CAPITAL_SCORE
    if estimate.normalized_score > policy.max_capital_normalized_score:
        return False, ERROR_FILTER_CAPITAL_SCORE
    if (
        policy.max_allocation_percent_hint is not None
        and estimate.allocation_percent_hint is not None
        and estimate.allocation_percent_hint > policy.max_allocation_percent_hint
    ):
        return False, ERROR_FILTER_CAPITAL_ALLOCATION
    if (
        context.available_capital_hint is not None
        and policy.min_available_capital_hint is not None
        and context.available_capital_hint < policy.min_available_capital_hint
    ):
        return False, ERROR_FILTER_CAPITAL_ALLOCATION
    return True, None


class StrategyFilterPipeline:
    """Stateless ordered filter pipeline for evaluation reports."""

    def apply(
        self,
        reports: tuple[StrategyEvaluationReport, ...],
        *,
        context: DecisionRunContext,
        policy: DecisionFilterPolicy,
        engine_config: TradeDecisionEngineConfig,
    ) -> FilterPipelineResult:
        """Apply all filter stages in order; return audit trail and remaining IDs."""
        stage_results: list[FilterStageResult] = []
        remaining: list[StrategyEvaluationReport] = list(reports)
        all_eliminated: set[str] = set()
        execution_mode = _resolved_execution_mode(context)
        ref = _resolved_reference_time(context)
        allowed_outcomes = _allowed_outcome_classes(
            execution_mode,
            policy,
            allow_monitor_in_live=engine_config.allow_monitor_in_live,
        )
        allowed_actions = _allowed_signal_actions(execution_mode, policy)
        preferences = context.preferences

        stages: list[tuple[FilterStageId, Callable[[list[StrategyEvaluationReport]], tuple[list[StrategyEvaluationReport], dict[str, str]]]]] = [
            (FilterStageId.OUTCOME_CLASS, lambda r: self._stage_outcome_class(r, allowed_outcomes)),
            (FilterStageId.EVALUATION_STATUS, self._stage_evaluation_status),
            (FilterStageId.SIGNAL_ACTION, lambda r: self._stage_signal_action(r, allowed_actions)),
            (
                FilterStageId.SIGNAL_FRESHNESS,
                lambda r: self._stage_signal_freshness(
                    r,
                    reference_time=ref,
                    execution_mode=execution_mode,
                    allow_expired_in_analysis=engine_config.allow_expired_in_analysis,
                ),
            ),
            (FilterStageId.USER_PREFERENCES, lambda r: self._stage_user_preferences(r, preferences)),
            (FilterStageId.SUITABILITY_THRESHOLD, lambda r: self._stage_suitability_threshold(r, preferences, policy)),
            (FilterStageId.RISK_REWARD_BAND, lambda r: self._stage_risk_reward(r, preferences)),
            (FilterStageId.CAPITAL_PRECHECK, lambda r: self._stage_capital_precheck(r, context, engine_config.capital_policy)),
            (
                FilterStageId.TRADING_WINDOW,
                lambda r: self._stage_trading_window(
                    r,
                    context=context,
                    window_policy=engine_config.window_policy,
                    execution_mode=execution_mode,
                ),
            ),
        ]

        for stage_id, stage_fn in stages:
            started = time.perf_counter()
            input_count = len(remaining)
            before_ids = {r.strategy_id for r in remaining}
            remaining, reasons = stage_fn(remaining)
            after_ids = {r.strategy_id for r in remaining}
            stage_eliminated = tuple(sorted(before_ids - after_ids))
            all_eliminated.update(stage_eliminated)
            stage_results.append(
                FilterStageResult(
                    stage_id=stage_id,
                    input_count=input_count,
                    output_count=len(remaining),
                    eliminated=stage_eliminated,
                    elimination_reasons=MappingProxyType(dict(reasons)),
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
            )
            _logger.debug(
                "trade.decision.filter.stage",
                extra={
                    "event": "trade.decision.filter.stage",
                    "stage_id": stage_id.value,
                    "input_count": input_count,
                    "output_count": len(remaining),
                },
            )

        if context.mode is DecisionMode.MANUAL:
            started = time.perf_counter()
            input_count = len(remaining)
            before_ids = {r.strategy_id for r in remaining}
            remaining, reasons = self._stage_manual_target(remaining, context.manual_strategy_id)
            after_ids = {r.strategy_id for r in remaining}
            stage_eliminated = tuple(sorted(before_ids - after_ids))
            all_eliminated.update(stage_eliminated)
            stage_results.append(
                FilterStageResult(
                    stage_id=FilterStageId.MANUAL_TARGET,
                    input_count=input_count,
                    output_count=len(remaining),
                    eliminated=stage_eliminated,
                    elimination_reasons=MappingProxyType(dict(reasons)),
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
            )

        remaining_ids = {r.strategy_id for r in remaining}
        ordered_remaining = tuple(r for r in reports if r.strategy_id in remaining_ids)
        return FilterPipelineResult(
            initial_count=len(reports),
            final_count=len(ordered_remaining),
            stages=tuple(stage_results),
            eliminated_strategy_ids=frozenset(all_eliminated),
            remaining_strategy_ids=tuple(r.strategy_id for r in ordered_remaining),
        )

    @staticmethod
    def _filter_stage(
        reports: list[StrategyEvaluationReport],
        predicate: Callable[[StrategyEvaluationReport], str | None],
    ) -> tuple[list[StrategyEvaluationReport], dict[str, str]]:
        """Apply predicate; return survivors and elimination reasons."""
        survivors: list[StrategyEvaluationReport] = []
        reasons: dict[str, str] = {}
        for report in reports:
            reason = predicate(report)
            if reason is None:
                survivors.append(report)
            else:
                reasons[report.strategy_id] = reason
        return survivors, reasons

    def _stage_outcome_class(
        self,
        reports: list[StrategyEvaluationReport],
        allowed: frozenset[EvaluationOutcomeClass],
    ) -> tuple[list[StrategyEvaluationReport], dict[str, str]]:
        return self._filter_stage(
            reports,
            lambda r: None if r.outcome_class in allowed else ERROR_FILTER_OUTCOME_CLASS,
        )

    def _stage_evaluation_status(
        self,
        reports: list[StrategyEvaluationReport],
    ) -> tuple[list[StrategyEvaluationReport], dict[str, str]]:
        bad = {EvaluationStatus.FAILED, EvaluationStatus.SKIPPED, EvaluationStatus.TIMEOUT}
        return self._filter_stage(
            reports,
            lambda r: None if r.evaluation_status not in bad else ERROR_FILTER_EVALUATION_STATUS,
        )

    def _stage_signal_action(
        self,
        reports: list[StrategyEvaluationReport],
        allowed: frozenset[SignalAction],
    ) -> tuple[list[StrategyEvaluationReport], dict[str, str]]:
        return self._filter_stage(
            reports,
            lambda r: (
                None
                if r.signal is not None and r.signal.action in allowed
                else ERROR_FILTER_SIGNAL_ACTION
            ),
        )

    def _stage_signal_freshness(
        self,
        reports: list[StrategyEvaluationReport],
        *,
        reference_time: datetime,
        execution_mode: StrategyExecutionMode,
        allow_expired_in_analysis: bool,
    ) -> tuple[list[StrategyEvaluationReport], dict[str, str]]:
        if execution_mode is StrategyExecutionMode.ANALYSIS and allow_expired_in_analysis:
            return reports, {}

        def check(report: StrategyEvaluationReport) -> str | None:
            if report.signal is None:
                return ERROR_FILTER_SIGNAL_EXPIRED
            if is_signal_expired(report.signal, reference_time=reference_time):
                return ERROR_FILTER_SIGNAL_EXPIRED
            return None

        return self._filter_stage(reports, check)

    def _stage_user_preferences(
        self,
        reports: list[StrategyEvaluationReport],
        preferences: UserPreferences,
    ) -> tuple[list[StrategyEvaluationReport], dict[str, str]]:
        def check(report: StrategyEvaluationReport) -> str | None:
            if report.strategy_id in preferences.blocked_strategy_ids:
                return ERROR_FILTER_PREFERENCE_BLOCKED
            if (
                preferences.allowed_families is not None
                and report.strategy_family not in preferences.allowed_families
            ):
                return ERROR_FILTER_PREFERENCE_FAMILY
            if report.signal is not None and preferences.allowed_directions is not None:
                if report.signal.direction not in preferences.allowed_directions:
                    return ERROR_FILTER_PREFERENCE_DIRECTION
            if preferences.exclude_undefined_risk:
                if report.expected_risk.category is RiskEstimateCategory.UNDEFINED:
                    return ERROR_FILTER_PREFERENCE_UNDEFINED_RISK
                if report.signal is not None and report.signal.risk is not None:
                    if report.signal.risk.profile is RiskProfileHint.UNDEFINED:
                        return ERROR_FILTER_PREFERENCE_UNDEFINED_RISK
            return None

        return self._filter_stage(reports, check)

    def _stage_suitability_threshold(
        self,
        reports: list[StrategyEvaluationReport],
        preferences: UserPreferences,
        policy: DecisionFilterPolicy,
    ) -> tuple[list[StrategyEvaluationReport], dict[str, str]]:
        min_conf = preferences.min_confidence_score or policy.default_min_confidence
        min_suit = preferences.min_suitability_score or policy.default_min_suitability
        min_rank = preferences.min_ranking_score or policy.default_min_ranking

        def check(report: StrategyEvaluationReport) -> str | None:
            if report.suitability_score < min_suit:
                return ERROR_FILTER_THRESHOLD_SUITABILITY
            if report.ranking_score < min_rank:
                return ERROR_FILTER_THRESHOLD_RANKING
            if report.confidence.overall_score < min_conf:
                return ERROR_FILTER_THRESHOLD_CONFIDENCE
            if preferences.min_expected_pop is not None and report.expected_pop < preferences.min_expected_pop:
                return ERROR_FILTER_THRESHOLD_RANKING
            return None

        return self._filter_stage(reports, check)

    def _stage_risk_reward(
        self,
        reports: list[StrategyEvaluationReport],
        preferences: UserPreferences,
    ) -> tuple[list[StrategyEvaluationReport], dict[str, str]]:
        def check(report: StrategyEvaluationReport) -> str | None:
            if (
                preferences.max_risk_normalized_score is not None
                and report.expected_risk.normalized_score > preferences.max_risk_normalized_score
            ):
                return ERROR_FILTER_RISK_REWARD_RISK
            if (
                preferences.min_reward_normalized_score is not None
                and report.expected_reward.normalized_score < preferences.min_reward_normalized_score
            ):
                return ERROR_FILTER_RISK_REWARD_REWARD
            return None

        return self._filter_stage(reports, check)

    def _stage_capital_precheck(
        self,
        reports: list[StrategyEvaluationReport],
        context: DecisionRunContext,
        policy: CapitalPolicy,
    ) -> tuple[list[StrategyEvaluationReport], dict[str, str]]:
        survivors: list[StrategyEvaluationReport] = []
        reasons: dict[str, str] = {}
        for report in reports:
            passed, code = _capital_precheck_passes(report, context, policy)
            if passed:
                survivors.append(report)
            elif code is not None:
                reasons[report.strategy_id] = code
        return survivors, reasons

    def _stage_trading_window(
        self,
        reports: list[StrategyEvaluationReport],
        *,
        context: DecisionRunContext,
        window_policy: TradingWindowPolicy,
        execution_mode: StrategyExecutionMode,
    ) -> tuple[list[StrategyEvaluationReport], dict[str, str]]:
        survivors: list[StrategyEvaluationReport] = []
        reasons: dict[str, str] = {}
        for report in reports:
            result = _validate_trading_window(
                context=context,
                report=report,
                window_policy=window_policy,
                execution_mode=execution_mode,
            )
            if result.passed:
                survivors.append(report)
            elif result.reason_code is not None:
                reasons[report.strategy_id] = result.reason_code
        return survivors, reasons

    def _stage_manual_target(
        self,
        reports: list[StrategyEvaluationReport],
        manual_strategy_id: str | None,
    ) -> tuple[list[StrategyEvaluationReport], dict[str, str]]:
        if manual_strategy_id is None:
            return reports, {}
        survivors = [r for r in reports if r.strategy_id == manual_strategy_id]
        reasons: dict[str, str] = {}
        if not survivors:
            for report in reports:
                reasons[report.strategy_id] = ERROR_FILTER_MANUAL_NOT_FOUND
        return survivors, reasons


class DecisionSelector:
    """Stateless strategy selector for autonomous and manual modes."""

    def sort_candidates(
        self,
        candidates: tuple[StrategyEvaluationReport, ...],
        *,
        preferences: UserPreferences,
        policy: DecisionFilterPolicy,
    ) -> tuple[StrategyEvaluationReport, ...]:
        """Return candidates sorted by selection key descending."""
        return tuple(
            sorted(
                candidates,
                key=lambda report: _selection_sort_key(report, preferences, policy),
            )
        )

    def select(
        self,
        candidates: tuple[StrategyEvaluationReport, ...],
        *,
        context: DecisionRunContext,
        policy: DecisionFilterPolicy,
        engine_config: TradeDecisionEngineConfig,
        filter_result: FilterPipelineResult,
        all_reports: tuple[StrategyEvaluationReport, ...],
    ) -> SelectionOutcome:
        """Select strategy from pre-filtered candidates."""
        if context.mode is DecisionMode.MANUAL:
            return self._select_manual(
                context,
                filter_result=filter_result,
                all_reports=all_reports,
                engine_config=engine_config,
            )
        return self._select_autonomous(context, candidates, policy)

    def _select_autonomous(
        self,
        context: DecisionRunContext,
        candidates: tuple[StrategyEvaluationReport, ...],
        policy: DecisionFilterPolicy,
    ) -> SelectionOutcome:
        if context.force_abstain:
            return SelectionOutcome.abstain(AbstainReasonCode.POLICY_ABSTAIN)
        if not candidates:
            return SelectionOutcome.abstain(AbstainReasonCode.ALL_FILTERED)
        ranked = self.sort_candidates(candidates, preferences=context.preferences, policy=policy)
        top = ranked[0]
        ref = _resolved_reference_time(context)
        assert top.signal is not None
        if is_signal_expired(top.signal, reference_time=ref):
            return SelectionOutcome.abstain(AbstainReasonCode.SIGNAL_EXPIRED)
        return SelectionOutcome.selected(top, alternatives=ranked[1:5])

    def _select_manual(
        self,
        context: DecisionRunContext,
        *,
        filter_result: FilterPipelineResult,
        all_reports: tuple[StrategyEvaluationReport, ...],
        engine_config: TradeDecisionEngineConfig,
    ) -> SelectionOutcome:
        strategy_id = context.manual_strategy_id
        assert strategy_id is not None
        report = next((r for r in all_reports if r.strategy_id == strategy_id), None)
        if report is None:
            return SelectionOutcome.manual_invalid(
                code=ERROR_MANUAL_NOT_IN_BUNDLE,
                strategy_id=strategy_id,
            )

        if strategy_id in filter_result.remaining_strategy_ids:
            assert report.signal is not None
            return SelectionOutcome.selected(report)

        override_policy = engine_config.manual_override_policy
        elimination_stage = _find_elimination_stage(filter_result, strategy_id)

        if override_policy is ManualOverridePolicy.STRICT:
            return SelectionOutcome.manual_invalid(
                code=ERROR_MANUAL_FILTER_REJECTED,
                strategy_id=strategy_id,
            )

        if elimination_stage is None:
            return SelectionOutcome.manual_invalid(
                code=ERROR_MANUAL_FILTER_REJECTED,
                strategy_id=strategy_id,
            )

        if report.strategy_id in context.preferences.blocked_strategy_ids:
            return SelectionOutcome.manual_invalid(
                code=ERROR_MANUAL_FILTER_REJECTED,
                strategy_id=strategy_id,
            )

        if elimination_stage is FilterStageId.EVALUATION_STATUS:
            return SelectionOutcome.manual_invalid(
                code=ERROR_MANUAL_FILTER_REJECTED,
                strategy_id=strategy_id,
            )

        execution_mode = _resolved_execution_mode(context)
        ref = _resolved_reference_time(context)
        if (
            elimination_stage is FilterStageId.SIGNAL_FRESHNESS
            and report.signal is not None
            and is_signal_expired(report.signal, reference_time=ref)
            and not (
                execution_mode is StrategyExecutionMode.ANALYSIS
                and engine_config.allow_expired_in_analysis
            )
        ):
            return SelectionOutcome.manual_invalid(
                code=ERROR_MANUAL_FILTER_REJECTED,
                strategy_id=strategy_id,
            )

        if (
            elimination_stage is FilterStageId.TRADING_WINDOW
            and override_policy is ManualOverridePolicy.ALLOW_WINDOW_OVERRIDE
            and execution_mode is StrategyExecutionMode.ANALYSIS
        ):
            warning = DecisionWarningRecord(
                code=WARN_MANUAL_OVERRIDE,
                message=f"Manual override applied for trading window on {strategy_id}.",
                strategy_id=strategy_id,
                field="trading_window",
            )
            assert report.signal is not None
            return SelectionOutcome.selected(report, override_warnings=(warning,))

        if override_policy is ManualOverridePolicy.ALLOW_WITH_WARNING:
            reason = _elimination_reason(filter_result, strategy_id)
            warning = DecisionWarningRecord(
                code=WARN_MANUAL_OVERRIDE,
                message=f"Manual override applied at stage {elimination_stage.value}: {reason}.",
                strategy_id=strategy_id,
                field=elimination_stage.value,
            )
            assert report.signal is not None
            return SelectionOutcome.selected(report, override_warnings=(warning,))

        return SelectionOutcome.manual_invalid(
            code=ERROR_MANUAL_FILTER_REJECTED,
            strategy_id=strategy_id,
        )


def _find_elimination_stage(
    filter_result: FilterPipelineResult,
    strategy_id: str,
) -> FilterStageId | None:
    """Return stage that eliminated strategy_id."""
    for stage in filter_result.stages:
        if strategy_id in stage.eliminated:
            return stage.stage_id
    return None


def _elimination_reason(filter_result: FilterPipelineResult, strategy_id: str) -> str:
    """Return elimination reason code for strategy."""
    for stage in filter_result.stages:
        if strategy_id in stage.elimination_reasons:
            return stage.elimination_reasons[strategy_id]
    return "unknown"


def _window_closed_abstain(filter_result: FilterPipelineResult) -> bool:
    """Return whether all eliminations are due to trading window."""
    if filter_result.final_count > 0:
        return False
    window_codes = {
        ERROR_FILTER_WINDOW_SESSION,
        ERROR_FILTER_WINDOW_CUTOFF,
        ERROR_FILTER_WINDOW_BLACKOUT,
    }
    for stage in filter_result.stages:
        if stage.stage_id is FilterStageId.TRADING_WINDOW and stage.eliminated:
            return all(
                stage.elimination_reasons.get(sid, "") in window_codes for sid in stage.eliminated
            )
    return False


class ConfidencePropagator:
    """Propagates evaluation confidence into decision confidence."""

    def propagate(
        self,
        *,
        report: StrategyEvaluationReport | None,
        context: DecisionRunContext,
        outcome: SelectionOutcome,
        engine_config: TradeDecisionEngineConfig,
        filter_result: FilterPipelineResult,
    ) -> DecisionConfidence:
        """Compute decision confidence for selected or abstain outcome."""
        if report is None or outcome.is_abstain or outcome.is_manual_invalid:
            return DecisionConfidence(
                overall_score=0.0,
                band=ConfidenceBand.LOW,
                decision_adjustment=0.0,
                method="trade_decision_abstain_v1",
                components=(),
            )

        base = report.confidence.overall_score
        adjustment = 0.0
        components: list[DecisionFactor] = [
            DecisionFactor(
                factor_id="evaluation_base",
                label="Evaluation base confidence",
                weight=1.0,
                raw_value=base,
                normalized_value=_round_score(base),
                direction="NEUTRAL",
            )
        ]

        if report.strategy_id in context.preferences.preferred_strategy_ids:
            bonus = min(3.0, engine_config.filter_policy.preference_boost_score * 6.0)
            adjustment += bonus
            components.append(
                DecisionFactor(
                    factor_id="preference_boost",
                    label="Preferred strategy bonus",
                    weight=0.1,
                    raw_value=bonus,
                    normalized_value=bonus,
                    direction="POSITIVE",
                )
            )

        execution_mode = _resolved_execution_mode(context)
        if report.signal is not None and execution_mode is StrategyExecutionMode.LIVE:
            window = _validate_trading_window(
                context=context,
                report=report,
                window_policy=engine_config.window_policy,
                execution_mode=execution_mode,
            )
            if window.near_cutoff:
                penalty = -3.0
                adjustment += penalty
                components.append(
                    DecisionFactor(
                        factor_id="window_penalty",
                        label="Near entry cutoff penalty",
                        weight=0.1,
                        raw_value=penalty,
                        normalized_value=abs(penalty),
                        direction="NEGATIVE",
                        stage_id=FilterStageId.TRADING_WINDOW,
                    )
                )

        if outcome.override_warnings:
            penalty = -5.0
            adjustment += penalty
            components.append(
                DecisionFactor(
                    factor_id="manual_override",
                    label="Manual override penalty",
                    weight=0.15,
                    raw_value=penalty,
                    normalized_value=abs(penalty),
                    direction="NEGATIVE",
                )
            )

        if report.signal is not None:
            remaining = remaining_validity_seconds(report.signal, reference_time=_resolved_reference_time(context))
            if remaining != float("inf") and remaining < 300:
                penalty = -min(15.0, max(0.0, (300.0 - remaining) / 20.0))
                adjustment += penalty
                components.append(
                    DecisionFactor(
                        factor_id="signal_freshness",
                        label="Short remaining validity penalty",
                        weight=0.1,
                        raw_value=penalty,
                        normalized_value=abs(penalty),
                        direction="NEGATIVE",
                        stage_id=FilterStageId.SIGNAL_FRESHNESS,
                    )
                )

        capital_passed, _ = _capital_precheck_passes(report, context, engine_config.capital_policy)
        if capital_passed and report.capital_estimate.normalized_score > engine_config.capital_policy.max_capital_normalized_score * 0.85:
            penalty = -2.0
            adjustment += penalty
            components.append(
                DecisionFactor(
                    factor_id="capital_warning",
                    label="Near capital limit penalty",
                    weight=0.05,
                    raw_value=penalty,
                    normalized_value=abs(penalty),
                    direction="NEGATIVE",
                    stage_id=FilterStageId.CAPITAL_PRECHECK,
                )
            )

        overall = _clamp(base + adjustment, SCORE_MIN, SCORE_MAX)
        signal_conf = report.signal.confidence.score if report.signal is not None else None
        return DecisionConfidence(
            overall_score=overall,
            band=confidence_band_for_score(overall),
            evaluation_confidence=base,
            signal_confidence=signal_conf,
            decision_adjustment=adjustment,
            method="trade_decision_v1",
            components=tuple(components),
        )


class DecisionExplanationBuilder:
    """Assembles explainability artifacts for a trade decision."""

    def build(
        self,
        *,
        outcome: SelectionOutcome,
        context: DecisionRunContext,
        filter_result: FilterPipelineResult,
        confidence: DecisionConfidence,
    ) -> tuple[tuple[DecisionReason, ...], tuple[DecisionFactor, ...]]:
        """Build human reasons and machine factors."""
        reasons: list[DecisionReason] = []
        factors: list[DecisionFactor] = list(confidence.components)

        if outcome.is_selected and outcome.selected_report is not None:
            sid = outcome.selected_report.strategy_id
            if context.mode is DecisionMode.AUTONOMOUS:
                reasons.append(
                    DecisionReason(
                        code="TRADE_DECISION.SELECT.AUTONOMOUS_TOP_RANK",
                        message=f"Selected highest-ranked eligible strategy {sid}.",
                        severity="INFO",
                        strategy_id=sid,
                    )
                )
            else:
                reasons.append(
                    DecisionReason(
                        code="TRADE_DECISION.SELECT.MANUAL",
                        message=f"Manual selection of {sid} accepted.",
                        severity="INFO",
                        strategy_id=sid,
                    )
                )
            report = outcome.selected_report
            reasons.append(
                DecisionReason(
                    code="TRADE_DECISION.SELECT.SCORES",
                    message=(
                        f"suitability={report.suitability_score:.2f}, "
                        f"ranking={report.ranking_score:.2f}, "
                        f"confidence={report.confidence.overall_score:.2f}."
                    ),
                    severity="INFO",
                    strategy_id=sid,
                )
            )
            for warning in outcome.override_warnings:
                reasons.append(
                    DecisionReason(
                        code=warning.code,
                        message=warning.message,
                        severity="WARNING",
                        strategy_id=warning.strategy_id,
                    )
                )
        elif outcome.is_manual_invalid:
            reasons.append(
                DecisionReason(
                    code=outcome.manual_invalid_code or ERROR_MANUAL_FILTER_REJECTED,
                    message=f"Manual strategy {outcome.manual_strategy_id} is ineligible.",
                    severity="CRITICAL",
                    strategy_id=outcome.manual_strategy_id,
                )
            )
        elif outcome.is_abstain and outcome.abstain_reason_code is not None:
            reasons.append(
                DecisionReason(
                    code=f"TRADE_DECISION.ABSTAIN.{outcome.abstain_reason_code.value.upper()}",
                    message=f"Abstaining: {outcome.abstain_reason_code.value}.",
                    severity="INFO",
                )
            )

        if filter_result.initial_count > filter_result.final_count:
            reasons.append(
                DecisionReason(
                    code="TRADE_DECISION.ABSTAIN.FILTER_SUMMARY",
                    message=(
                        f"{filter_result.initial_count - filter_result.final_count} of "
                        f"{filter_result.initial_count} strategies eliminated by filters."
                    ),
                    severity="INFO",
                )
            )

        if not reasons:
            reasons.append(
                DecisionReason(
                    code="TRADE_DECISION.RESULT.GENERIC",
                    message="Trade decision completed.",
                    severity="INFO",
                )
            )

        factors.append(
            DecisionFactor(
                factor_id="ranking_score",
                label="Ranking score",
                weight=0.3,
                raw_value=outcome.selected_report.ranking_score if outcome.selected_report else 0.0,
                normalized_value=_round_score(outcome.selected_report.ranking_score if outcome.selected_report else 0.0),
                direction="POSITIVE" if outcome.is_selected else "NEUTRAL",
            )
        )
        return tuple(reasons), tuple(factors)


def build_decision_abstain_signal(
    *,
    context: DecisionRunContext,
    abstain_code: AbstainReasonCode,
    reasons: tuple[str, ...],
    abstain_action: SignalAction = SignalAction.ABSTAIN,
) -> TradingSignal:
    """Factory for decision-layer abstain signals."""
    bundle = context.bundle
    ref = _resolved_reference_time(context)
    reason_text = reasons or (f"Trade decision abstain: {abstain_code.value}",)
    underlying = "UNKNOWN"
    if bundle.ranked_reports and bundle.ranked_reports[0].signal is not None:
        underlying = bundle.ranked_reports[0].signal.market.underlying
    elif context.snapshot is not None:
        try:
            underlying = context.snapshot.underlying.symbol  # type: ignore[attr-defined]
        except AttributeError:
            pass
    return TradingSignal(
        signal_id=f"dec-abstain-{abstain_code.value}",
        strategy_id="trade_decision_engine",
        strategy_version=TRADE_DECISION_ENGINE_VERSION,
        strategy_family=StrategyFamily.NO_STRATEGY,
        action=abstain_action,
        direction=SignalDirection.UNKNOWN,
        confidence=SignalConfidence(
            score=0.0,
            band=ConfidenceBand.LOW,
            method="trade_decision_abstain_v1",
        ),
        market=SignalMarketContext(
            snapshot_id=bundle.snapshot_id,
            underlying=underlying,
        ),
        as_of=context.as_of,
        reasons=reason_text,
    )


def _build_output_signal(
    report: StrategyEvaluationReport,
    decision_confidence: DecisionConfidence,
) -> TradingSignal:
    """Build enriched output signal from selected report."""
    assert report.signal is not None
    new_confidence = SignalConfidence(
        score=decision_confidence.overall_score,
        band=decision_confidence.band,
        method=f"trade_decision_v1:{report.signal.confidence.method}",
        components=report.signal.confidence.components,
    )
    return replace(report.signal, confidence=new_confidence)


def _decision_id(bundle_id: str, mode: DecisionMode, selected_strategy_id: str | None) -> str:
    """Build deterministic decision identifier."""
    material = f"{bundle_id}|{mode.value}|{selected_strategy_id or 'none'}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"dec-{digest}"


def _map_outcome_class(
    status: DecisionStatus,
    signal: TradingSignal,
    report: StrategyEvaluationReport | None,
) -> DecisionOutcomeClass:
    """Map decision status to outcome class."""
    if status is DecisionStatus.SELECTED:
        if signal.action is SignalAction.EVALUATE:
            return DecisionOutcomeClass.TRADE_CANDIDATE
        if report is not None and report.outcome_class is EvaluationOutcomeClass.MONITOR:
            return DecisionOutcomeClass.MONITOR_ONLY
        return DecisionOutcomeClass.TRADE_CANDIDATE
    if status in (DecisionStatus.ABSTAIN, DecisionStatus.WINDOW_CLOSED, DecisionStatus.NO_CANDIDATES):
        return DecisionOutcomeClass.NO_TRADE
    return DecisionOutcomeClass.ERROR


def decision_fingerprint(
    result: TradeDecisionResult,
    *,
    deterministic: bool = True,
) -> str:
    """Compute SHA-256 fingerprint over semantic decision fields."""
    payload: dict[str, Any] = {
        "bundle_fingerprint": result.bundle_fingerprint,
        "decision_status": result.decision_status.value,
        "selected_strategy_id": result.selected_strategy_id,
        "signal_fingerprint": signal_fingerprint(result.selected_signal),
        "confidence": _round_score(result.confidence.overall_score),
        "mode": result.mode.value,
        "abstain_reason_code": (
            result.abstain_reason_code.value if result.abstain_reason_code else None
        ),
        "remaining_strategy_ids": list(result.filter_summary.remaining_strategy_ids),
    }
    if not deterministic:
        payload["decided_at"] = result.decided_at.isoformat()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TradeDecisionEngine(BaseEngine):
    """Institutional trade decision engine for THETA AI TRADER v1.0."""

    def __init__(
        self,
        config: TradeDecisionEngineConfig | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        filter_pipeline: StrategyFilterPipeline | None = None,
        selector: DecisionSelector | None = None,
        re_raise_on_failure: bool = False,
    ) -> None:
        """Initialize trade decision engine with injected policies."""
        self._decision_config = config or default_trade_decision_engine_config()
        self._clock = clock or _utc_now
        self._filter_pipeline = filter_pipeline or StrategyFilterPipeline()
        self._selector = selector or DecisionSelector()
        self._confidence = ConfidencePropagator()
        self._explanation = DecisionExplanationBuilder()
        super().__init__(
            config=MappingProxyType({"engine": "trade_decision_engine"}),
            re_raise_on_failure=re_raise_on_failure,
        )

    @property
    def decision_config(self) -> TradeDecisionEngineConfig:
        """Return immutable decision engine configuration."""
        return self._decision_config

    @property
    def engine_name(self) -> str:
        """Return stable engine identifier."""
        return "trade_decision_engine"

    @property
    def engine_version(self) -> str:
        """Return semantic engine version."""
        return TRADE_DECISION_ENGINE_VERSION

    def validate_configuration(self) -> None:
        """Validate static engine configuration."""
        _ = self._decision_config

    def validate_context(self, context: EngineContext) -> None:
        """Validate engine context wrapping decision run context."""
        super().validate_context(context)
        if not isinstance(context.payload, DecisionRunContext):
            raise EngineValidationError(
                "EngineContext.payload must be DecisionRunContext.",
                code=ERROR_CONTEXT_INVALID,
                field="payload",
                engine_name=self.engine_name,
            )

    def validate_run_context(self, context: DecisionRunContext) -> None:
        """Validate decision run inputs; raise on fatal issues."""
        if context.bundle is None:
            raise TradeDecisionContextError(
                "bundle must not be None.",
                code=ERROR_CONTEXT_BUNDLE_MISSING,
                field="bundle",
            )
        if not context.correlation_id.strip():
            raise TradeDecisionContextError(
                "correlation_id must be non-empty.",
                code=ERROR_CONTEXT_INVALID,
                field="correlation_id",
            )
        if not _is_timezone_aware(context.as_of):
            raise TradeDecisionContextError(
                "as_of must be timezone-aware.",
                code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
                field="as_of",
            )
        if context.reference_time is not None and not _is_timezone_aware(context.reference_time):
            raise TradeDecisionContextError(
                "reference_time must be timezone-aware.",
                code=ERROR_CONTEXT_NAIVE_TIMESTAMP,
                field="reference_time",
            )
        if context.mode is DecisionMode.MANUAL:
            if not context.manual_strategy_id or not context.manual_strategy_id.strip():
                raise TradeDecisionContextError(
                    "manual_strategy_id required in MANUAL mode.",
                    code=ERROR_CONTEXT_MANUAL_ID_MISSING,
                    field="manual_strategy_id",
                )
            if not _STRATEGY_ID_PATTERN.match(context.manual_strategy_id):
                raise TradeDecisionContextError(
                    "manual_strategy_id format invalid.",
                    code=ERROR_CONTEXT_INVALID,
                    field="manual_strategy_id",
                )
        _validate_user_preferences(context.preferences)
        if (
            self._decision_config.strict_correlation
            and context.bundle.correlation_id != context.correlation_id
        ):
            raise TradeDecisionContextError(
                "correlation_id mismatch with bundle.",
                code=ERROR_CONTEXT_CORRELATION_MISMATCH,
                field="correlation_id",
            )
        bundle_age = (
            _resolved_reference_time(context) - context.bundle.evaluated_at
        ).total_seconds()
        if bundle_age > self._decision_config.max_bundle_age_seconds:
            if self._decision_config.strict_bundle_freshness:
                raise TradeDecisionBundleError(
                    "Evaluation bundle is stale.",
                    code=ERROR_BUNDLE_STALE,
                    field="bundle.evaluated_at",
                )

    def validate_decision(self, result: TradeDecisionResult) -> DecisionValidationResult:
        """Validate sealed decision output."""
        errors: list[DecisionErrorRecord] = []
        warnings: list[DecisionWarningRecord] = []
        if result.selected_signal is None:
            errors.append(
                DecisionErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="selected_signal must not be None.",
                    field="selected_signal",
                )
            )
        if result.decision_status is DecisionStatus.SELECTED:
            if result.selected_report is None or result.selected_strategy_id is None:
                errors.append(
                    DecisionErrorRecord(
                        code=ERROR_RESULT_INVALID,
                        message="SELECTED requires selected_report.",
                        field="selected_report",
                    )
                )
            elif (
                result.selected_signal is not None
                and result.selected_report is not None
                and result.selected_signal.strategy_id != result.selected_report.strategy_id
            ):
                errors.append(
                    DecisionErrorRecord(
                        code=ERROR_RESULT_INVALID,
                        message="selected_signal strategy_id mismatch.",
                        field="selected_signal.strategy_id",
                    )
                )
        if not (SCORE_MIN <= result.confidence.overall_score <= SCORE_MAX):
            errors.append(
                DecisionErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="confidence.overall_score out of bounds.",
                    field="confidence.overall_score",
                )
            )
        if result.confidence.band is not confidence_band_for_score(result.confidence.overall_score):
            errors.append(
                DecisionErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="confidence band mismatch.",
                    field="confidence.band",
                )
            )
        if not result.reasons:
            errors.append(
                DecisionErrorRecord(
                    code=ERROR_RESULT_INVALID,
                    message="reasons must not be empty.",
                    field="reasons",
                )
            )
        if result.selected_signal is not None:
            recomputed = decision_fingerprint(
                result,
                deterministic=self._decision_config.deterministic_fingerprint,
            )
            if recomputed != result.decision_fingerprint:
                errors.append(
                    DecisionErrorRecord(
                        code=ERROR_RESULT_INVALID,
                        message="decision_fingerprint mismatch.",
                        field="decision_fingerprint",
                    )
                )
        if result.decision_status in (
            DecisionStatus.ABSTAIN,
            DecisionStatus.WINDOW_CLOSED,
            DecisionStatus.NO_CANDIDATES,
        ) and result.abstain_reason_code is None:
            warnings.append(
                DecisionWarningRecord(
                    code=ERROR_RESULT_INVALID,
                    message="Abstain outcome missing abstain_reason_code.",
                    field="abstain_reason_code",
                )
            )
        return DecisionValidationResult(errors=tuple(errors), warnings=tuple(warnings))

    def assert_valid_decision(self, result: TradeDecisionResult) -> None:
        """Raise when decision output is invalid."""
        validation = self.validate_decision(result)
        if not validation.is_valid:
            first = validation.errors[0]
            raise TradeDecisionValidationError(
                first.message,
                code=first.code,
                strategy_id=first.strategy_id,
                field=first.field,
            )

    def evaluate(self, context: EngineContext | DecisionRunContext) -> EngineResult:  # type: ignore[override]
        """Execute trade decision and return engine result."""
        if isinstance(context, DecisionRunContext):
            run_context = context
            correlation_id = context.correlation_id
            as_of = context.as_of
        elif isinstance(context, EngineContext):
            if not isinstance(context.payload, DecisionRunContext):
                raise TradeDecisionValidationError(
                    "EngineContext.payload must be DecisionRunContext.",
                    code=ERROR_CONTEXT_INVALID,
                    field="payload",
                )
            run_context = context.payload
            correlation_id = context.correlation_id
            as_of = context.as_of
        else:
            raise TradeDecisionValidationError(
                "Context must be EngineContext or DecisionRunContext.",
                code=ERROR_CONTEXT_INVALID,
                field="context",
            )

        started_at = self._clock()
        start_perf = time.perf_counter()
        _logger.info(
            "trade.decision.start",
            extra={
                "event": "trade.decision.start",
                "correlation_id": correlation_id,
                "bundle_id": run_context.bundle.bundle_id,
                "mode": run_context.mode.value,
            },
        )
        try:
            self.validate_run_context(run_context)
            result = self.decide(run_context)
            validation = self.validate_decision(result)
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
                        EngineErrorRecord(
                            code=e.code,
                            message=e.message,
                            field=e.field,
                        )
                        for e in validation.errors
                    ),
                )

            status = EngineStatus.SUCCESS
            engine_errors: tuple[EngineErrorRecord, ...] = ()
            if result.decision_status is DecisionStatus.MANUAL_INVALID:
                status = EngineStatus.REJECTED
                engine_errors = tuple(
                    EngineErrorRecord(
                        code=e.code,
                        message=e.message,
                        field=e.field,
                    )
                    for e in result.errors
                )

            completed_at = self._clock()
            duration_ms = (time.perf_counter() - start_perf) * 1000.0
            engine_warnings = tuple(
                EngineWarningRecord(code=w.code, message=w.message, field=w.field)
                for w in (*result.warnings, *validation.warnings)
            )
            _logger.info(
                "trade.decision.complete",
                extra={
                    "event": "trade.decision.complete",
                    "correlation_id": correlation_id,
                    "decision_status": result.decision_status.value,
                    "selected_strategy_id": result.selected_strategy_id,
                    "duration_ms": duration_ms,
                },
            )
            return EngineResult(
                status=status,
                metadata=EngineMetadata(
                    engine_name=self.engine_name,
                    engine_version=self.engine_version,
                    correlation_id=correlation_id,
                    execution_id=correlation_id,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                ),
                payload=result if status is EngineStatus.SUCCESS else None,
                errors=engine_errors,
                warnings=engine_warnings,
            )
        except TradeDecisionError as exc:
            completed_at = self._clock()
            _logger.error(
                "trade.decision.rejected",
                extra={"event": "trade.decision.rejected", "error_code": exc.code},
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
                errors=(
                    EngineErrorRecord(
                        code=exc.code,
                        message=str(exc),
                        field=exc.field,
                    ),
                ),
            )
        except Exception as exc:
            completed_at = self._clock()
            _logger.error("trade.decision.failed", extra={"event": "trade.decision.failed"})
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
                        message=f"Unhandled trade decision failure: {exc}",
                    ),
                ),
            )

    def decide(self, run_context: DecisionRunContext) -> TradeDecisionResult:
        """Execute filter, select, propagate, and seal pipeline."""
        start_perf = time.perf_counter()
        warnings: list[DecisionWarningRecord] = []
        errors: list[DecisionErrorRecord] = []
        bundle = run_context.bundle

        if run_context.mode is DecisionMode.AUTONOMOUS and run_context.manual_strategy_id:
            warnings.append(
                DecisionWarningRecord(
                    code=WARN_MODE_IGNORED_MANUAL_ID,
                    message="manual_strategy_id ignored in autonomous mode.",
                    severity="INFO",
                )
            )
        if run_context.force_abstain and run_context.mode is DecisionMode.MANUAL:
            warnings.append(
                DecisionWarningRecord(
                    code=WARN_FORCE_ABSTAIN_MANUAL,
                    message="force_abstain takes precedence over manual mode.",
                    severity="WARNING",
                )
            )

        recomputed_fp = evaluation_fingerprint(bundle.reports, deterministic=True)
        if recomputed_fp != bundle.bundle_fingerprint:
            warnings.append(
                DecisionWarningRecord(
                    code=WARN_BUNDLE_FINGERPRINT_DRIFT,
                    message="Bundle fingerprint drift detected.",
                    severity="WARNING",
                    field="bundle_fingerprint",
                )
            )

        if not bundle.ranked_reports or bundle.summary.total_enabled == 0:
            warnings.append(
                DecisionWarningRecord(
                    code=WARN_BUNDLE_EMPTY,
                    message="Evaluation bundle is empty.",
                    severity="INFO",
                )
            )
            return self._seal_abstain(
                run_context,
                AbstainReasonCode.EMPTY_BUNDLE,
                filter_result=_empty_filter_result(),
                warnings=tuple(warnings),
                duration_ms=(time.perf_counter() - start_perf) * 1000.0,
            )

        if run_context.force_abstain:
            return self._seal_abstain(
                run_context,
                AbstainReasonCode.POLICY_ABSTAIN,
                filter_result=_empty_filter_result(len(bundle.ranked_reports)),
                warnings=tuple(warnings),
                duration_ms=(time.perf_counter() - start_perf) * 1000.0,
            )

        if (
            self._decision_config.strict_no_actionable
            and bundle.summary.total_actionable == 0
        ):
            return self._seal_abstain(
                run_context,
                AbstainReasonCode.NO_ACTIONABLE_REPORTS,
                filter_result=_empty_filter_result(len(bundle.ranked_reports)),
                warnings=tuple(warnings),
                duration_ms=(time.perf_counter() - start_perf) * 1000.0,
            )

        candidates = bundle.ranked_reports
        filter_result = self._filter_pipeline.apply(
            candidates,
            context=run_context,
            policy=self._decision_config.filter_policy,
            engine_config=self._decision_config,
        )
        remaining = tuple(r for r in candidates if r.strategy_id in filter_result.remaining_strategy_ids)

        outcome = self._selector.select(
            remaining,
            context=run_context,
            policy=self._decision_config.filter_policy,
            engine_config=self._decision_config,
            filter_result=filter_result,
            all_reports=candidates,
        )

        duration_ms = (time.perf_counter() - start_perf) * 1000.0
        return self._seal_result(
            run_context,
            outcome,
            filter_result,
            warnings=tuple(warnings),
            errors=tuple(errors),
            duration_ms=duration_ms,
        )

    def _seal_abstain(
        self,
        context: DecisionRunContext,
        code: AbstainReasonCode,
        *,
        filter_result: FilterPipelineResult,
        warnings: tuple[DecisionWarningRecord, ...],
        duration_ms: float,
    ) -> TradeDecisionResult:
        """Seal an abstain decision result."""
        abstain_reasons = (f"Trade decision abstain: {code.value}",)
        signal = build_decision_abstain_signal(
            context=context,
            abstain_code=code,
            reasons=abstain_reasons,
            abstain_action=self._decision_config.abstain_action,
        )
        confidence = self._confidence.propagate(
            report=None,
            context=context,
            outcome=SelectionOutcome.abstain(code),
            engine_config=self._decision_config,
            filter_result=filter_result,
        )
        reasons, factors = self._explanation.build(
            outcome=SelectionOutcome.abstain(code),
            context=context,
            filter_result=filter_result,
            confidence=confidence,
        )
        status = DecisionStatus.WINDOW_CLOSED if code is AbstainReasonCode.TRADING_WINDOW_CLOSED else DecisionStatus.ABSTAIN
        if code is AbstainReasonCode.EMPTY_BUNDLE:
            status = DecisionStatus.ABSTAIN
        return self._assemble_result(
            context=context,
            status=status,
            signal=signal,
            report=None,
            confidence=confidence,
            reasons=reasons,
            factors=factors,
            filter_result=filter_result,
            abstain_code=code,
            warnings=warnings,
            errors=(),
            duration_ms=duration_ms,
            outcome=SelectionOutcome.abstain(code),
        )

    def _seal_result(
        self,
        context: DecisionRunContext,
        outcome: SelectionOutcome,
        filter_result: FilterPipelineResult,
        *,
        warnings: tuple[DecisionWarningRecord, ...],
        errors: tuple[DecisionErrorRecord, ...],
        duration_ms: float,
    ) -> TradeDecisionResult:
        """Seal selected, abstain, or manual-invalid decision."""
        if outcome.is_manual_invalid:
            signal = build_decision_abstain_signal(
                context=context,
                abstain_code=AbstainReasonCode.MANUAL_STRATEGY_INELIGIBLE,
                reasons=(f"Manual selection invalid: {outcome.manual_invalid_code}",),
                abstain_action=self._decision_config.abstain_action,
            )
            confidence = self._confidence.propagate(
                report=None,
                context=context,
                outcome=outcome,
                engine_config=self._decision_config,
                filter_result=filter_result,
            )
            reasons, factors = self._explanation.build(
                outcome=outcome,
                context=context,
                filter_result=filter_result,
                confidence=confidence,
            )
            manual_errors = (
                DecisionErrorRecord(
                    code=outcome.manual_invalid_code or ERROR_MANUAL_FILTER_REJECTED,
                    message=f"Manual strategy {outcome.manual_strategy_id} ineligible.",
                    strategy_id=outcome.manual_strategy_id,
                ),
            )
            return self._assemble_result(
                context=context,
                status=DecisionStatus.MANUAL_INVALID,
                signal=signal,
                report=None,
                confidence=confidence,
                reasons=reasons,
                factors=factors,
                filter_result=filter_result,
                abstain_code=AbstainReasonCode.MANUAL_STRATEGY_INELIGIBLE,
                warnings=warnings + outcome.override_warnings,
                errors=manual_errors,
                duration_ms=duration_ms,
                outcome=outcome,
            )

        if outcome.is_abstain:
            code = outcome.abstain_reason_code or AbstainReasonCode.ALL_FILTERED
            extra_warnings = list(warnings)
            if filter_result.final_count == 0 and filter_result.initial_count > 0:
                extra_warnings.append(
                    DecisionWarningRecord(
                        code=WARN_SELECT_NO_CANDIDATES,
                        message="All strategies filtered out.",
                        severity="INFO",
                    )
                )
            if _window_closed_abstain(filter_result):
                code = AbstainReasonCode.TRADING_WINDOW_CLOSED
            return self._seal_abstain(
                context,
                code,
                filter_result=filter_result,
                warnings=tuple(extra_warnings),
                duration_ms=duration_ms,
            )

        report = outcome.selected_report
        assert report is not None
        confidence = self._confidence.propagate(
            report=report,
            context=context,
            outcome=outcome,
            engine_config=self._decision_config,
            filter_result=filter_result,
        )
        signal = _build_output_signal(report, confidence)
        reasons, factors = self._explanation.build(
            outcome=outcome,
            context=context,
            filter_result=filter_result,
            confidence=confidence,
        )
        result_warnings = list(warnings) + list(outcome.override_warnings)
        if confidence.decision_adjustment < -1.0:
            result_warnings.append(
                DecisionWarningRecord(
                    code=WARN_CONFIDENCE_DOWNGRADED,
                    message="Decision confidence downgraded by penalties.",
                    severity="INFO",
                    strategy_id=report.strategy_id,
                )
            )
        return self._assemble_result(
            context=context,
            status=DecisionStatus.SELECTED,
            signal=signal,
            report=report,
            confidence=confidence,
            reasons=reasons,
            factors=factors,
            filter_result=filter_result,
            abstain_code=None,
            warnings=tuple(result_warnings),
            errors=errors,
            duration_ms=duration_ms,
            outcome=outcome,
        )

    def _assemble_result(
        self,
        *,
        context: DecisionRunContext,
        status: DecisionStatus,
        signal: TradingSignal,
        report: StrategyEvaluationReport | None,
        confidence: DecisionConfidence,
        reasons: tuple[DecisionReason, ...],
        factors: tuple[DecisionFactor, ...],
        filter_result: FilterPipelineResult,
        abstain_code: AbstainReasonCode | None,
        warnings: tuple[DecisionWarningRecord, ...],
        errors: tuple[DecisionErrorRecord, ...],
        duration_ms: float,
        outcome: SelectionOutcome,
    ) -> TradeDecisionResult:
        """Assemble immutable trade decision result."""
        bundle = context.bundle
        selected_id = report.strategy_id if report is not None else None
        decided_at = self._clock()
        runner_ups = tuple(r.strategy_id for r in outcome.alternatives)
        metadata = MappingProxyType(
            {
                "runner_up_strategy_ids": ",".join(runner_ups) if runner_ups else "",
                "manual_strategy_id": context.manual_strategy_id or "",
            }
        )
        result = TradeDecisionResult(
            decision_id=_decision_id(bundle.bundle_id, context.mode, selected_id),
            correlation_id=context.correlation_id,
            bundle_id=bundle.bundle_id,
            bundle_fingerprint=bundle.bundle_fingerprint,
            decision_status=status,
            outcome_class=_map_outcome_class(status, signal, report),
            mode=context.mode,
            execution_mode=_resolved_execution_mode(context),
            selected_signal=signal,
            selected_report=report,
            selected_strategy_id=selected_id,
            confidence=confidence,
            reasons=reasons,
            factors=factors,
            filter_summary=filter_result,
            abstain_reason_code=abstain_code,
            decided_at=decided_at,
            duration_ms=duration_ms,
            decision_fingerprint="",
            warnings=warnings,
            errors=errors,
            metadata=metadata,
        )
        fp = decision_fingerprint(result, deterministic=self._decision_config.deterministic_fingerprint)
        return replace(result, decision_fingerprint=fp)


def _empty_filter_result(initial: int = 0) -> FilterPipelineResult:
    """Return empty filter pipeline result."""
    return FilterPipelineResult(
        initial_count=initial,
        final_count=0,
        stages=(),
        eliminated_strategy_ids=frozenset(),
        remaining_strategy_ids=(),
    )


def decision_to_dict(result: TradeDecisionResult, *, omit_nulls: bool = True) -> dict[str, Any]:
    """Serialize trade decision result to dictionary."""
    payload: dict[str, Any] = {
        "schema_version": TRADE_DECISION_SCHEMA_VERSION,
        "decision_id": result.decision_id,
        "correlation_id": result.correlation_id,
        "bundle_id": result.bundle_id,
        "bundle_fingerprint": result.bundle_fingerprint,
        "decision_status": result.decision_status.value,
        "outcome_class": result.outcome_class.value,
        "mode": result.mode.value,
        "execution_mode": result.execution_mode.value,
        "selected_strategy_id": result.selected_strategy_id,
        "decision_fingerprint": result.decision_fingerprint,
        "decided_at": result.decided_at.isoformat(),
        "duration_ms": result.duration_ms,
        "confidence": {
            "overall_score": result.confidence.overall_score,
            "band": result.confidence.band.value,
            "decision_adjustment": result.confidence.decision_adjustment,
            "method": result.confidence.method,
        },
        "abstain_reason_code": result.abstain_reason_code.value if result.abstain_reason_code else None,
        "selected_signal": signal_to_dict(result.selected_signal, omit_nulls=omit_nulls),
        "filter_summary": {
            "initial_count": result.filter_summary.initial_count,
            "final_count": result.filter_summary.final_count,
            "remaining_strategy_ids": list(result.filter_summary.remaining_strategy_ids),
        },
        "reasons": [
            {
                "code": reason.code,
                "message": reason.message,
                "severity": reason.severity,
                "strategy_id": reason.strategy_id,
            }
            for reason in result.reasons
        ],
        "warnings": [
            {
                "code": warning.code,
                "message": warning.message,
                "severity": warning.severity,
                "strategy_id": warning.strategy_id,
                "field": warning.field,
            }
            for warning in result.warnings
        ],
    }
    if omit_nulls:
        return {key: value for key, value in payload.items() if value is not None}
    return payload


def decision_to_json(result: TradeDecisionResult, *, omit_nulls: bool = True) -> str:
    """Serialize trade decision result to JSON."""
    return json.dumps(decision_to_dict(result, omit_nulls=omit_nulls), sort_keys=True)


def decision_from_dict(data: Mapping[str, Any]) -> TradeDecisionResult:
    """Deserialize trade decision result from dictionary."""
    schema_version = str(data.get("schema_version", ""))
    if schema_version != TRADE_DECISION_SCHEMA_VERSION:
        raise TradeDecisionValidationError(
            f"Unsupported schema version: {schema_version!r}.",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
            field="schema_version",
        )
    confidence_data = data.get("confidence", {})
    if not isinstance(confidence_data, dict):
        raise TradeDecisionValidationError(
            "confidence must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
            field="confidence",
        )
    filter_data = data.get("filter_summary", {})
    if not isinstance(filter_data, dict):
        raise TradeDecisionValidationError(
            "filter_summary must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
            field="filter_summary",
        )
    signal_data = data.get("selected_signal")
    if not isinstance(signal_data, dict):
        raise TradeDecisionValidationError(
            "selected_signal must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
            field="selected_signal",
        )
    decided_at_raw = data.get("decided_at")
    if not isinstance(decided_at_raw, str):
        raise TradeDecisionValidationError(
            "decided_at must be ISO string.",
            code=ERROR_SERIALIZATION_MALFORMED,
            field="decided_at",
        )
    decided_at = datetime.fromisoformat(decided_at_raw)
    reasons_raw = data.get("reasons", [])
    reasons: tuple[DecisionReason, ...] = tuple(
        DecisionReason(
            code=str(item["code"]),
            message=str(item["message"]),
            severity=str(item.get("severity", "INFO")),
            strategy_id=item.get("strategy_id"),
        )
        for item in reasons_raw
        if isinstance(item, dict)
    )
    warnings_raw = data.get("warnings", [])
    warnings: tuple[DecisionWarningRecord, ...] = tuple(
        DecisionWarningRecord(
            code=str(item["code"]),
            message=str(item["message"]),
            severity=str(item.get("severity", "WARNING")),
            strategy_id=item.get("strategy_id"),
            field=item.get("field"),
        )
        for item in warnings_raw
        if isinstance(item, dict)
    )
    abstain_raw = data.get("abstain_reason_code")
    abstain_code = AbstainReasonCode(abstain_raw) if abstain_raw else None
    confidence_score = float(confidence_data.get("overall_score", 0.0))
    result = TradeDecisionResult(
        decision_id=str(data["decision_id"]),
        correlation_id=str(data["correlation_id"]),
        bundle_id=str(data["bundle_id"]),
        bundle_fingerprint=str(data["bundle_fingerprint"]),
        decision_status=DecisionStatus(str(data["decision_status"])),
        outcome_class=DecisionOutcomeClass(str(data["outcome_class"])),
        mode=DecisionMode(str(data["mode"])),
        execution_mode=StrategyExecutionMode(str(data["execution_mode"])),
        selected_signal=signal_from_dict(signal_data),
        selected_strategy_id=data.get("selected_strategy_id"),
        confidence=DecisionConfidence(
            overall_score=confidence_score,
            band=confidence_band_for_score(confidence_score),
            decision_adjustment=float(confidence_data.get("decision_adjustment", 0.0)),
            method=str(confidence_data.get("method", "trade_decision_v1")),
            components=(),
        ),
        reasons=reasons,
        factors=(),
        filter_summary=FilterPipelineResult(
            initial_count=int(filter_data.get("initial_count", 0)),
            final_count=int(filter_data.get("final_count", 0)),
            stages=(),
            eliminated_strategy_ids=frozenset(),
            remaining_strategy_ids=tuple(str(x) for x in filter_data.get("remaining_strategy_ids", [])),
        ),
        abstain_reason_code=abstain_code,
        decided_at=decided_at,
        duration_ms=float(data.get("duration_ms", 0.0)),
        decision_fingerprint=str(data.get("decision_fingerprint", "")),
        warnings=warnings,
        errors=(),
    )
    fp = decision_fingerprint(result, deterministic=True)
    return replace(result, decision_fingerprint=fp)


def decision_from_json(payload: str) -> TradeDecisionResult:
    """Deserialize trade decision result from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TradeDecisionValidationError(
            "Malformed decision JSON.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(data, dict):
        raise TradeDecisionValidationError(
            "Decision JSON root must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return decision_from_dict(data)


__all__ = [
    "AbstainReasonCode",
    "BlackoutWindow",
    "CapitalPolicy",
    "ConfidencePropagator",
    "DECISION_SCORE_EPSILON",
    "DecisionConfidence",
    "DecisionExplanationBuilder",
    "DecisionFactor",
    "DecisionFilterPolicy",
    "DecisionMode",
    "DecisionOutcomeClass",
    "DecisionReason",
    "DecisionRunContext",
    "DecisionSelector",
    "DecisionStatus",
    "DecisionSummary",
    "DecisionValidationResult",
    "DecisionWarningRecord",
    "DecisionErrorRecord",
    "DEFAULT_MAX_BUNDLE_AGE_SECONDS",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_MIN_RANKING",
    "DEFAULT_MIN_SUITABILITY",
    "DEFAULT_PREFERENCE_BOOST",
    "FilterPipelineResult",
    "FilterStageId",
    "FilterStageResult",
    "ManualOverridePolicy",
    "SelectionOutcome",
    "StrategyFilterPipeline",
    "TRADE_DECISION_ENGINE_VERSION",
    "TRADE_DECISION_SCHEMA_VERSION",
    "TradeDecisionBundleError",
    "TradeDecisionConfigurationError",
    "TradeDecisionContextError",
    "TradeDecisionEngine",
    "TradeDecisionEngineConfig",
    "TradeDecisionError",
    "TradeDecisionResult",
    "TradeDecisionValidationError",
    "TradingWindowPolicy",
    "UserPreferences",
    "build_decision_abstain_signal",
    "decision_fingerprint",
    "decision_from_dict",
    "decision_from_json",
    "decision_to_dict",
    "decision_to_json",
    "default_trade_decision_engine_config",
    "default_trading_window_policy",
    "default_user_preferences",
]

