"""Multi-strategy evaluation engine for THETA AI TRADER.

Evaluates every enabled strategy plugin against a :class:`MarketSnapshot` and
produces immutable, ranked :class:`StrategyEvaluationReport` instances for the
Trade Decision Engine. Never places orders, enforces risk, or calls brokers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Final, Mapping

from core.base_engine import BaseEngine
from core.engine_context import EngineContext
from core.engine_metadata import EngineMetadata
from core.engine_result import EngineErrorRecord, EngineResult, EngineWarningRecord
from core.enums import EngineSeverity, EngineStatus
from core.exceptions import EngineExecutionError, EngineValidationError
from market_data.market_snapshot import MarketSnapshot, SnapshotValidationStatus, is_live_trade_ready
from strategy.base_strategy import (
    BaseStrategy,
    StrategyContext,
    StrategyContextError,
    StrategySignalError,
)
from strategy.registry import (
    RegistrySnapshot,
    StrategyRegistrationRecord,
    StrategyRegistry,
    StrategyRegistryNotFoundError,
)
from strategy.signals import (
    ConfidenceBand,
    MarginIntensityHint,
    RiskLevelHint,
    RiskProfileHint,
    SignalAction,
    SignalConfidence,
    SignalValidationContext,
    StrategyExecutionMode,
    StrategyFamily,
    TargetHintType,
    TradingSignal,
    confidence_band_for_score,
    from_dict as signal_from_dict,
    to_dict as signal_to_dict,
    validate_trading_signal,
)

STRATEGY_EVALUATION_ENGINE_VERSION: Final[str] = "1.0.0"
STRATEGY_EVALUATION_SCHEMA_VERSION: Final[str] = "1.0.0"
DEFAULT_MAX_PARALLELISM: Final[int] = 4
DEFAULT_PLUGIN_TIMEOUT_MS: Final[int] = 5000
SCORE_MIN: Final[float] = 0.0
SCORE_MAX: Final[float] = 100.0
POP_MIN: Final[float] = 0.0
POP_MAX: Final[float] = 1.0
RANKING_SCORE_EPSILON: Final[float] = 1e-9

ERROR_CONFIG_INVALID: Final[str] = "STRATEGY_EVALUATION.CONFIG.INVALID"
ERROR_CONTEXT_INVALID: Final[str] = "STRATEGY_EVALUATION.CONTEXT.INVALID"
ERROR_CONTEXT_SNAPSHOT_MISSING: Final[str] = "STRATEGY_EVALUATION.CONTEXT.SNAPSHOT_MISSING"
ERROR_CONTEXT_SNAPSHOT_INVALID: Final[str] = "STRATEGY_EVALUATION.CONTEXT.SNAPSHOT_INVALID"
ERROR_REGISTRY_PLUGIN_MISSING: Final[str] = "STRATEGY_EVALUATION.REGISTRY.PLUGIN_MISSING"
ERROR_REGISTRY_FINGERPRINT_MISMATCH: Final[str] = "STRATEGY_EVALUATION.REGISTRY.FINGERPRINT_MISMATCH"
ERROR_PLUGIN_CONTEXT_INVALID: Final[str] = "STRATEGY_EVALUATION.PLUGIN.CONTEXT_INVALID"
ERROR_PLUGIN_SIGNAL_INVALID: Final[str] = "STRATEGY_EVALUATION.PLUGIN.SIGNAL_INVALID"
ERROR_PLUGIN_EXECUTION_FAILED: Final[str] = "STRATEGY_EVALUATION.PLUGIN.EXECUTION_FAILED"
ERROR_PLUGIN_UNEXPECTED: Final[str] = "STRATEGY_EVALUATION.PLUGIN.UNEXPECTED"
ERROR_PLUGIN_TIMEOUT: Final[str] = "STRATEGY_EVALUATION.PLUGIN.TIMEOUT"
ERROR_BUNDLE_INVALID: Final[str] = "STRATEGY_EVALUATION.BUNDLE.INVALID"
ERROR_EMPTY_ENABLED_SET: Final[str] = "STRATEGY_EVALUATION.EMPTY_ENABLED_SET"
ERROR_NO_ACTIONABLE: Final[str] = "STRATEGY_EVALUATION.NO_ACTIONABLE"
ERROR_PARTIAL_FAILURE: Final[str] = "STRATEGY_EVALUATION.PARTIAL_FAILURE"
ERROR_ALL_ABSTAIN: Final[str] = "STRATEGY_EVALUATION.ALL_ABSTAIN"
ERROR_SERIALIZATION_UNSUPPORTED_VERSION: Final[str] = "STRATEGY_EVALUATION.SERIALIZATION.UNSUPPORTED_VERSION"
ERROR_SERIALIZATION_MALFORMED: Final[str] = "STRATEGY_EVALUATION.SERIALIZATION.MALFORMED"
WARN_PARTIAL_QUALITY: Final[str] = "STRATEGY_EVALUATION.SNAPSHOT.PARTIAL_QUALITY"
WARN_STALE: Final[str] = "STRATEGY_EVALUATION.SNAPSHOT.STALE"
WARN_METADATA_DRIFT: Final[str] = "STRATEGY_EVALUATION.REGISTRY.METADATA_DRIFT"
WARN_LOW_SUITABILITY: Final[str] = "STRATEGY_EVALUATION.SCORE.LOW_SUITABILITY"

_DEFAULT_FAMILY_POP_PRIORS: Final[dict[StrategyFamily, float]] = {
    StrategyFamily.SHORT_STRANGLE: 0.62,
    StrategyFamily.IRON_CONDOR: 0.58,
    StrategyFamily.BULL_PUT_SPREAD: 0.55,
    StrategyFamily.BEAR_CALL_SPREAD: 0.55,
    StrategyFamily.BROKEN_WING_BUTTERFLY: 0.52,
    StrategyFamily.JADE_LIZARD: 0.60,
    StrategyFamily.LONG_VOLATILITY: 0.42,
    StrategyFamily.CUSTOM: 0.50,
    StrategyFamily.NO_STRATEGY: 0.0,
}

_logger = logging.getLogger(__name__)


class EvaluationStatus(str, Enum):
    """Per-strategy evaluation lifecycle outcome."""

    SUCCESS = "success"
    ABSTAIN = "abstain"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


class EvaluationOutcomeClass(str, Enum):
    """High-level actionability tier for ranking."""

    ACTIONABLE = "actionable"
    MONITOR = "monitor"
    NO_TRADE = "no_trade"
    ERROR = "error"


class RiskEstimateCategory(str, Enum):
    """Informational risk category."""

    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    ELEVATED = "elevated"
    HIGH = "high"
    UNDEFINED = "undefined"
    UNKNOWN = "unknown"


class RewardEstimateCategory(str, Enum):
    """Informational reward category."""

    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"
    UNKNOWN = "unknown"


class CapitalEstimateCategory(str, Enum):
    """Informational capital allocation band."""

    MINIMAL = "minimal"
    SMALL = "small"
    MODERATE = "moderate"
    LARGE = "large"
    VERY_LARGE = "very_large"
    UNKNOWN = "unknown"


class EvaluationParallelismMode(str, Enum):
    """Plugin evaluation parallelism policy."""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


class EvaluationFailureMode(str, Enum):
    """Failure handling during multi-strategy evaluation."""

    CONTINUE = "continue"
    FAIL_FAST = "fail_fast"


class StrategyEvaluationError(Exception):
    """Base exception for strategy evaluation failures."""

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


class StrategyEvaluationConfigurationError(StrategyEvaluationError):
    """Raised when engine configuration is invalid."""


class StrategyEvaluationValidationError(StrategyEvaluationError):
    """Raised when input or output validation fails."""


class StrategyEvaluationContextError(StrategyEvaluationError):
    """Raised when evaluation run context is invalid."""


class StrategyEvaluationRegistryError(StrategyEvaluationError):
    """Raised when registry resolution or fingerprint checks fail."""


@dataclass(frozen=True)
class EvaluationScoringPolicy:
    """Immutable scoring weights and priors for evaluation metrics."""

    weight_suitability: float = 0.40
    weight_confidence: float = 0.30
    weight_pop: float = 0.20
    weight_priority: float = 0.10
    family_pop_priors: Mapping[StrategyFamily, float] = field(
        default_factory=lambda: MappingProxyType(dict(_DEFAULT_FAMILY_POP_PRIORS))
    )
    default_capital_pool_hint: float = 100.0
    low_suitability_threshold: float = 30.0
    abstain_suitability_cap: float = 25.0
    deterministic_fingerprint: bool = True

    def __post_init__(self) -> None:
        total = self.weight_suitability + self.weight_confidence + self.weight_pop + self.weight_priority
        if total <= 0:
            raise StrategyEvaluationConfigurationError(
                "Scoring weights must sum to a positive value.",
                code=ERROR_CONFIG_INVALID,
                field="weight_suitability",
            )


@dataclass(frozen=True)
class EvaluationSkipPolicy:
    """Immutable skip rules applied before plugin execution."""

    skip_unsupported_underlying: bool = True
    skip_insufficient_contracts: bool = True
    skip_missing_volatility: bool = True


@dataclass(frozen=True)
class StrategyEvaluationEngineConfig:
    """Immutable configuration for :class:`StrategyEvaluationEngine`."""

    scoring_policy: EvaluationScoringPolicy = field(default_factory=EvaluationScoringPolicy)
    skip_policy: EvaluationSkipPolicy = field(default_factory=EvaluationSkipPolicy)
    parallelism_mode: EvaluationParallelismMode = EvaluationParallelismMode.SEQUENTIAL
    failure_mode: EvaluationFailureMode = EvaluationFailureMode.CONTINUE
    max_parallelism: int = DEFAULT_MAX_PARALLELISM
    plugin_timeout_ms: int = DEFAULT_PLUGIN_TIMEOUT_MS
    strict_registry_match: bool = False
    reject_invalid_snapshot_live: bool = True
    allow_invalid_snapshot_analysis: bool = True
    enforce_plugin_timeout: bool = False

    def __post_init__(self) -> None:
        if self.max_parallelism <= 0:
            raise StrategyEvaluationConfigurationError(
                "max_parallelism must be positive.",
                code=ERROR_CONFIG_INVALID,
                field="max_parallelism",
            )


@dataclass(frozen=True)
class EvaluationRunContext:
    """Immutable inputs for one multi-strategy evaluation run."""

    correlation_id: str
    as_of: datetime
    snapshot: MarketSnapshot
    registry_snapshot: RegistrySnapshot
    execution_mode: StrategyExecutionMode = StrategyExecutionMode.LIVE
    reference_time: datetime | None = None
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class EvaluationFactor:
    """Machine-readable scoring factor."""

    factor_id: str
    label: str
    weight: float
    raw_value: float
    normalized_value: float
    direction: str
    notes: str | None = None


@dataclass(frozen=True)
class EvaluationWarningRecord:
    """Non-fatal evaluation warning."""

    code: str
    message: str
    strategy_id: str | None = None
    field: str | None = None
    severity: str = "WARNING"


@dataclass(frozen=True)
class EvaluationErrorRecord:
    """Structured evaluation error."""

    code: str
    message: str
    strategy_id: str | None = None
    field: str | None = None


@dataclass(frozen=True)
class EvaluationConfidence:
    """Enriched confidence score for an evaluation report."""

    overall_score: float
    band: ConfidenceBand
    signal_confidence: SignalConfidence
    engine_adjustment: float
    method: str
    components: tuple[EvaluationFactor, ...]


@dataclass(frozen=True)
class ExpectedRiskEstimate:
    """Informational risk estimate — not enforcement."""

    category: RiskEstimateCategory
    normalized_score: float
    profile_hint: RiskProfileHint | None = None
    max_loss_category: str | None = None
    tail_risk_hint: RiskLevelHint | None = None
    gamma_risk_hint: RiskLevelHint | None = None
    vega_risk_hint: RiskLevelHint | None = None
    method: str = "heuristic_v1"
    factors: tuple[EvaluationFactor, ...] = ()
    notes: str | None = None


@dataclass(frozen=True)
class ExpectedRewardEstimate:
    """Informational reward estimate."""

    category: RewardEstimateCategory
    normalized_score: float
    target_hint_type: TargetHintType | None = None
    risk_reward_hint: float | None = None
    method: str = "heuristic_v1"
    factors: tuple[EvaluationFactor, ...] = ()
    notes: str | None = None


@dataclass(frozen=True)
class CapitalEstimate:
    """Informational capital allocation hint."""

    category: CapitalEstimateCategory
    normalized_score: float
    allocation_percent_hint: float | None = None
    margin_intensity: MarginIntensityHint | None = None
    method: str = "heuristic_v1"
    factors: tuple[EvaluationFactor, ...] = ()
    notes: str | None = None


@dataclass(frozen=True)
class EvaluationScoreResult:
    """Complete scoring output from :class:`EvaluationScorer`."""

    suitability_score: float
    expected_pop: float
    confidence: EvaluationConfidence
    expected_risk: ExpectedRiskEstimate
    expected_reward: ExpectedRewardEstimate
    capital_estimate: CapitalEstimate
    ranking_score: float
    factors: tuple[EvaluationFactor, ...]
    warnings: tuple[EvaluationWarningRecord, ...]


@dataclass(frozen=True)
class StrategyEvaluationReport:
    """Immutable per-strategy evaluation outcome."""

    report_id: str
    strategy_id: str
    strategy_version: str
    strategy_family: StrategyFamily
    display_name: str
    plugin_priority: int
    evaluation_status: EvaluationStatus
    outcome_class: EvaluationOutcomeClass
    signal: TradingSignal | None
    suitability_score: float
    ranking_score: float
    confidence: EvaluationConfidence
    expected_pop: float
    expected_risk: ExpectedRiskEstimate
    expected_reward: ExpectedRewardEstimate
    capital_estimate: CapitalEstimate
    reasons: tuple[str, ...]
    factors: tuple[EvaluationFactor, ...]
    warnings: tuple[EvaluationWarningRecord, ...]
    errors: tuple[EvaluationErrorRecord, ...]
    evaluated_at: datetime
    duration_ms: float
    registry_record_fingerprint: str
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class EvaluationBundleSummary:
    """Denormalized aggregate statistics for a bundle."""

    total_registered: int
    total_enabled: int
    total_evaluated: int
    total_success: int
    total_abstain: int
    total_failed: int
    total_skipped: int
    total_actionable: int
    top_strategy_id: str | None
    top_suitability_score: float | None
    top_ranking_score: float | None


@dataclass(frozen=True)
class StrategyEvaluationBundle:
    """Immutable ranked collection of evaluation reports."""

    bundle_id: str
    correlation_id: str
    snapshot_id: str
    registry_fingerprint: str
    execution_mode: StrategyExecutionMode
    evaluated_at: datetime
    reports: tuple[StrategyEvaluationReport, ...]
    ranked_reports: tuple[StrategyEvaluationReport, ...]
    summary: EvaluationBundleSummary
    bundle_fingerprint: str
    warnings: tuple[EvaluationWarningRecord, ...]
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class EvaluationValidationResult:
    """Outcome of bundle validation."""

    errors: tuple[EvaluationErrorRecord, ...] = ()
    warnings: tuple[EvaluationWarningRecord, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return ``True`` when no validation errors exist."""
        return not self.errors


def _utc_now() -> datetime:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp ``value`` to ``[minimum, maximum]``."""
    return max(minimum, min(maximum, value))


def _round_score(value: float) -> float:
    """Round score to four decimal places for fingerprint stability."""
    return round(value, 4)


def _normalize_priority(priority: int) -> float:
    """Normalize registry priority to ``0..100``."""
    return _clamp(priority / 10.0, SCORE_MIN, SCORE_MAX)


def _snapshot_quality_multiplier(snapshot: MarketSnapshot) -> float:
    """Map snapshot validation status to suitability multiplier."""
    status = snapshot.quality.validation_status
    if status is SnapshotValidationStatus.VALID:
        return 1.0
    if status is SnapshotValidationStatus.PARTIAL:
        return 0.70
    return 0.0


def _freshness_multiplier(snapshot: MarketSnapshot) -> float:
    """Map snapshot freshness usability to multiplier."""
    if snapshot.freshness.is_usable_for_live_decisions:
        return 1.0
    return 0.5


def _underlying_symbol(snapshot: MarketSnapshot) -> str:
    """Return normalized underlying symbol."""
    return snapshot.option_chain.metadata.underlying.strip().upper()


def _liquidity_hint_score(snapshot: MarketSnapshot) -> float:
    """Estimate liquidity quality from ATM contract spreads."""
    contracts = snapshot.option_chain.contracts
    if not contracts:
        return 0.0
    spreads: list[float] = []
    for contract in contracts[:4]:
        if contract.bid > 0 and contract.ask >= contract.bid:
            mid = (contract.bid + contract.ask) / 2.0
            if mid > 0:
                spreads.append((contract.ask - contract.bid) / mid)
    if not spreads:
        return 50.0
    avg_spread = sum(spreads) / len(spreads)
    return _clamp(100.0 - (avg_spread * 1000.0), SCORE_MIN, SCORE_MAX)


def _family_fit_score(signal: TradingSignal, snapshot: MarketSnapshot) -> float:
    """Heuristic family fit score from signal direction and snapshot context."""
    family = signal.strategy_family
    direction = signal.direction
    base = 50.0
    if family is StrategyFamily.SHORT_STRANGLE:
        base = 65.0 if direction.value == "neutral" else 45.0
    elif family is StrategyFamily.IRON_CONDOR:
        base = 70.0 if direction.value == "neutral" else 50.0
    elif family is StrategyFamily.BULL_PUT_SPREAD:
        base = 70.0 if direction.value == "bullish" else 40.0
    elif family is StrategyFamily.BEAR_CALL_SPREAD:
        base = 70.0 if direction.value == "bearish" else 40.0
    elif family is StrategyFamily.LONG_VOLATILITY:
        base = 65.0 if direction.value == "long_vol" else 45.0
    elif family is StrategyFamily.NO_STRATEGY:
        return 0.0
    if snapshot.volatility is not None and snapshot.volatility.last_price is not None:
        base += 5.0
    return _clamp(base, SCORE_MIN, SCORE_MAX)


def _vol_adjustment(snapshot: MarketSnapshot) -> float:
    """Volatility context adjustment for POP estimation."""
    if snapshot.volatility is None or snapshot.volatility.last_price is None:
        return 0.0
    vix = snapshot.volatility.last_price
    if vix >= 20.0:
        return 0.05
    if vix <= 12.0:
        return -0.03
    return 0.0


def _action_pop_adjustment(action: SignalAction) -> float:
    """Action-based POP adjustment."""
    if action is SignalAction.EVALUATE:
        return 0.05
    if action is SignalAction.WAIT:
        return -0.10
    return 0.0


def classify_outcome(
    signal: TradingSignal | None,
    *,
    evaluation_status: EvaluationStatus,
) -> EvaluationOutcomeClass:
    """Classify report outcome tier from signal action and status."""
    if evaluation_status is EvaluationStatus.FAILED:
        return EvaluationOutcomeClass.ERROR
    if evaluation_status is EvaluationStatus.SKIPPED:
        return EvaluationOutcomeClass.ERROR
    if signal is None:
        return EvaluationOutcomeClass.ERROR
    if signal.action is SignalAction.EVALUATE:
        return EvaluationOutcomeClass.ACTIONABLE
    if signal.action is SignalAction.WAIT:
        return EvaluationOutcomeClass.MONITOR
    return EvaluationOutcomeClass.NO_TRADE


def _apply_outcome_tier_cap(ranking_score: float, outcome_class: EvaluationOutcomeClass) -> float:
    """Apply ranking tier caps per specification."""
    if outcome_class is EvaluationOutcomeClass.ERROR:
        return 0.0
    if outcome_class is EvaluationOutcomeClass.NO_TRADE:
        return min(ranking_score, 20.0)
    if outcome_class is EvaluationOutcomeClass.MONITOR:
        return min(ranking_score, 60.0)
    return ranking_score


class EvaluationScorer:
    """Stateless scorer for strategy evaluation metrics."""

    def score(
        self,
        *,
        signal: TradingSignal,
        snapshot: MarketSnapshot,
        record: StrategyRegistrationRecord,
        policy: EvaluationScoringPolicy,
        outcome_class: EvaluationOutcomeClass,
        execution_mode: StrategyExecutionMode,
    ) -> EvaluationScoreResult:
        """Compute suitability, POP, risk, reward, capital, and ranking scores."""
        warnings: list[EvaluationWarningRecord] = []
        factors: list[EvaluationFactor] = []

        quality_mult = _snapshot_quality_multiplier(snapshot)
        freshness_mult = _freshness_multiplier(snapshot)
        signal_conf = signal.confidence.score
        family_fit = _family_fit_score(signal, snapshot)
        liquidity = _liquidity_hint_score(snapshot)

        suitability = (
            0.35 * signal_conf
            + 0.20 * (quality_mult * 100.0)
            + 0.15 * (freshness_mult * 100.0)
            + 0.20 * family_fit
            + 0.10 * liquidity
        )
        if signal.action in (SignalAction.ABSTAIN, SignalAction.NO_TRADE):
            suitability = min(suitability, policy.abstain_suitability_cap)
        if quality_mult == 0.0 and execution_mode is StrategyExecutionMode.LIVE:
            suitability = min(suitability, 10.0)
            warnings.append(
                EvaluationWarningRecord(
                    code=WARN_PARTIAL_QUALITY,
                    message="Snapshot quality invalid for LIVE evaluation.",
                    strategy_id=record.strategy_id,
                    severity="WARNING",
                )
            )
        suitability = _round_score(_clamp(suitability, SCORE_MIN, SCORE_MAX))
        if suitability < policy.low_suitability_threshold:
            warnings.append(
                EvaluationWarningRecord(
                    code=WARN_LOW_SUITABILITY,
                    message=f"Suitability score {suitability} below threshold.",
                    strategy_id=record.strategy_id,
                    severity="INFO",
                )
            )

        family_prior = policy.family_pop_priors.get(record.strategy_family, 0.50)
        base_pop = signal_conf / 100.0
        expected_pop = (
            base_pop * 0.5
            + family_prior * 0.3
            + _vol_adjustment(snapshot)
            + _action_pop_adjustment(signal.action)
        )
        if signal.action in (SignalAction.ABSTAIN, SignalAction.NO_TRADE):
            expected_pop = min(expected_pop, 0.25)
        expected_pop = _round_score(_clamp(expected_pop, POP_MIN, POP_MAX))

        engine_adjustment = 0.0
        if quality_mult >= 1.0:
            engine_adjustment += 2.0
        elif quality_mult <= 0.0:
            engine_adjustment -= 10.0
        else:
            engine_adjustment -= 5.0
        if freshness_mult < 1.0:
            engine_adjustment -= 5.0
            warnings.append(
                EvaluationWarningRecord(
                    code=WARN_STALE,
                    message="Snapshot freshness below live decision threshold.",
                    strategy_id=record.strategy_id,
                    severity="WARNING",
                )
            )
        if (
            execution_mode is StrategyExecutionMode.LIVE
            and snapshot.quality.validation_status is SnapshotValidationStatus.PARTIAL
        ):
            engine_adjustment -= 20.0

        overall_confidence = _round_score(
            _clamp(signal_conf + engine_adjustment, SCORE_MIN, SCORE_MAX)
        )
        confidence = EvaluationConfidence(
            overall_score=overall_confidence,
            band=confidence_band_for_score(overall_confidence),
            signal_confidence=signal.confidence,
            engine_adjustment=engine_adjustment,
            method="evaluation_engine_v1",
            components=(
                EvaluationFactor(
                    factor_id="snapshot_quality",
                    label="Snapshot quality",
                    weight=1.0,
                    raw_value=quality_mult,
                    normalized_value=quality_mult * 100.0,
                    direction="POSITIVE" if quality_mult >= 0.7 else "NEGATIVE",
                ),
            ),
        )

        expected_risk = self._estimate_risk(signal, record)
        expected_reward = self._estimate_reward(signal, expected_risk)
        capital_estimate = self._estimate_capital(signal, record, policy)

        weight_total = (
            policy.weight_suitability
            + policy.weight_confidence
            + policy.weight_pop
            + policy.weight_priority
        )
        raw_ranking = (
            policy.weight_suitability * suitability
            + policy.weight_confidence * overall_confidence
            + policy.weight_pop * (expected_pop * 100.0)
            + policy.weight_priority * _normalize_priority(record.priority)
        ) / weight_total
        ranking_score = _apply_outcome_tier_cap(
            _round_score(_clamp(raw_ranking, SCORE_MIN, SCORE_MAX)),
            outcome_class,
        )

        factors.extend(
            [
                EvaluationFactor(
                    factor_id="signal_confidence",
                    label="Signal confidence",
                    weight=0.35,
                    raw_value=signal_conf,
                    normalized_value=signal_conf,
                    direction="POSITIVE",
                ),
                EvaluationFactor(
                    factor_id="snapshot_quality",
                    label="Snapshot quality",
                    weight=0.20,
                    raw_value=quality_mult,
                    normalized_value=quality_mult * 100.0,
                    direction="POSITIVE" if quality_mult >= 0.7 else "NEGATIVE",
                ),
                EvaluationFactor(
                    factor_id="family_fit",
                    label="Strategy family fit",
                    weight=0.20,
                    raw_value=family_fit,
                    normalized_value=family_fit,
                    direction="POSITIVE",
                ),
                EvaluationFactor(
                    factor_id="expected_pop",
                    label="Expected POP",
                    weight=0.20,
                    raw_value=expected_pop,
                    normalized_value=expected_pop * 100.0,
                    direction="POSITIVE",
                ),
            ]
        )

        return EvaluationScoreResult(
            suitability_score=suitability,
            expected_pop=expected_pop,
            confidence=confidence,
            expected_risk=expected_risk,
            expected_reward=expected_reward,
            capital_estimate=capital_estimate,
            ranking_score=ranking_score,
            factors=tuple(factors),
            warnings=tuple(warnings),
        )

    def _estimate_risk(
        self,
        signal: TradingSignal,
        record: StrategyRegistrationRecord,
    ) -> ExpectedRiskEstimate:
        """Derive informational risk estimate from signal metadata."""
        risk_meta = signal.risk
        if risk_meta is not None and risk_meta.profile is RiskProfileHint.UNDEFINED:
            return ExpectedRiskEstimate(
                category=RiskEstimateCategory.UNDEFINED,
                normalized_score=80.0,
                profile_hint=RiskProfileHint.UNDEFINED,
                max_loss_category=risk_meta.max_loss_category,
                tail_risk_hint=risk_meta.tail_risk,
                gamma_risk_hint=risk_meta.gamma_risk,
                vega_risk_hint=risk_meta.vega_risk,
                notes="Undefined risk structure.",
            )

        category = RiskEstimateCategory.MODERATE
        score = 45.0
        if record.strategy_family is StrategyFamily.SHORT_STRANGLE:
            category = RiskEstimateCategory.ELEVATED
            score = 65.0
        elif record.strategy_family is StrategyFamily.LONG_VOLATILITY:
            category = RiskEstimateCategory.MODERATE
            score = 50.0

        if risk_meta is not None:
            if risk_meta.max_loss_category:
                loss_cat = risk_meta.max_loss_category.upper()
                if loss_cat == "HIGH":
                    category = RiskEstimateCategory.HIGH
                    score = 80.0
                elif loss_cat == "LOW":
                    category = RiskEstimateCategory.LOW
                    score = 25.0
            for hint in (risk_meta.tail_risk, risk_meta.gamma_risk, risk_meta.vega_risk):
                if hint is RiskLevelHint.HIGH:
                    score = min(100.0, score + 10.0)
                    if category is RiskEstimateCategory.MODERATE:
                        category = RiskEstimateCategory.ELEVATED

        return ExpectedRiskEstimate(
            category=category,
            normalized_score=_round_score(score),
            profile_hint=risk_meta.profile if risk_meta else None,
            max_loss_category=risk_meta.max_loss_category if risk_meta else None,
            tail_risk_hint=risk_meta.tail_risk if risk_meta else None,
            gamma_risk_hint=risk_meta.gamma_risk if risk_meta else None,
            vega_risk_hint=risk_meta.vega_risk if risk_meta else None,
        )

    def _estimate_reward(
        self,
        signal: TradingSignal,
        expected_risk: ExpectedRiskEstimate,
    ) -> ExpectedRewardEstimate:
        """Derive informational reward estimate."""
        score = _clamp(signal.confidence.score * 0.8, SCORE_MIN, SCORE_MAX)
        category = RewardEstimateCategory.MODERATE
        target_type = signal.target.hint_type if signal.target else None
        if target_type is TargetHintType.PREMIUM_DECAY_PERCENT:
            score = min(100.0, score + 15.0)
            category = RewardEstimateCategory.HIGH
        elif signal.strategy_family in (
            StrategyFamily.SHORT_STRANGLE,
            StrategyFamily.IRON_CONDOR,
            StrategyFamily.JADE_LIZARD,
        ):
            score = min(100.0, score + 10.0)
            category = RewardEstimateCategory.HIGH

        rr_hint = score / max(expected_risk.normalized_score, 1.0)
        return ExpectedRewardEstimate(
            category=category,
            normalized_score=_round_score(score),
            target_hint_type=target_type,
            risk_reward_hint=_round_score(rr_hint),
        )

    def _estimate_capital(
        self,
        signal: TradingSignal,
        record: StrategyRegistrationRecord,
        policy: EvaluationScoringPolicy,
    ) -> CapitalEstimate:
        """Derive informational capital allocation hint."""
        margin = signal.risk.margin_intensity if signal.risk else None
        category = CapitalEstimateCategory.UNKNOWN
        score = 50.0
        allocation = None
        if margin is MarginIntensityHint.LOW:
            category = CapitalEstimateCategory.SMALL
            score = 25.0
            allocation = 1.0
        elif margin is MarginIntensityHint.MODERATE:
            category = CapitalEstimateCategory.MODERATE
            score = 50.0
            allocation = 2.5
        elif margin is MarginIntensityHint.HIGH:
            category = CapitalEstimateCategory.LARGE
            score = 75.0
            allocation = 5.0
        elif record.strategy_family is StrategyFamily.SHORT_STRANGLE:
            category = CapitalEstimateCategory.LARGE
            score = 70.0
            allocation = 4.0
        elif record.strategy_family in (
            StrategyFamily.IRON_CONDOR,
            StrategyFamily.BULL_PUT_SPREAD,
            StrategyFamily.BEAR_CALL_SPREAD,
        ):
            category = CapitalEstimateCategory.MODERATE
            score = 45.0
            allocation = 2.0

        leg_count = signal.structure_hint.leg_count if signal.structure_hint else 2
        score = min(100.0, score + max(0, leg_count - 2) * 5.0)
        if allocation is not None and policy.default_capital_pool_hint > 0:
            allocation = _round_score(allocation)

        return CapitalEstimate(
            category=category,
            normalized_score=_round_score(score),
            allocation_percent_hint=allocation,
            margin_intensity=margin,
        )


def rank_reports(
    reports: tuple[StrategyEvaluationReport, ...],
) -> tuple[StrategyEvaluationReport, ...]:
    """Rank reports by composite score with deterministic tie-breakers."""

    def sort_key(report: StrategyEvaluationReport) -> tuple[float, float, float, float, int, str]:
        return (
            -report.ranking_score,
            -report.suitability_score,
            -report.confidence.overall_score,
            -report.expected_pop,
            -report.plugin_priority,
            report.strategy_id,
        )

    return tuple(sorted(reports, key=sort_key))


def _report_fingerprint_payload(
    report: StrategyEvaluationReport,
    *,
    deterministic: bool,
) -> dict[str, Any]:
    """Build canonical fingerprint payload for one report."""
    payload: dict[str, Any] = {
        "strategy_id": report.strategy_id,
        "evaluation_status": report.evaluation_status.value,
        "outcome_class": report.outcome_class.value,
        "suitability_score": _round_score(report.suitability_score),
        "ranking_score": _round_score(report.ranking_score),
        "expected_pop": _round_score(report.expected_pop),
        "confidence": _round_score(report.confidence.overall_score),
        "registry_record_fingerprint": report.registry_record_fingerprint,
    }
    if not deterministic:
        payload["evaluated_at"] = report.evaluated_at.isoformat()
        payload["duration_ms"] = _round_score(report.duration_ms)
    if report.signal is not None:
        payload["signal_id"] = report.signal.signal_id
        payload["signal_action"] = report.signal.action.value
    return payload


def evaluation_fingerprint(
    reports: tuple[StrategyEvaluationReport, ...],
    *,
    deterministic: bool = True,
) -> str:
    """Compute deterministic SHA-256 fingerprint over evaluation reports."""
    sorted_reports = sorted(reports, key=lambda item: item.strategy_id)
    payload = [
        _report_fingerprint_payload(report, deterministic=deterministic)
        for report in sorted_reports
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bundle_id_from(fingerprint: str, correlation_id: str) -> str:
    """Build deterministic bundle identifier."""
    material = f"{fingerprint}|{correlation_id}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"eval-bnd-{digest}"


def _report_id(strategy_id: str, snapshot_id: str) -> str:
    """Build deterministic report identifier."""
    material = f"{strategy_id}|{snapshot_id}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"eval-rpt-{strategy_id}-{digest}"


def _zero_confidence() -> EvaluationConfidence:
    """Return zero confidence for skipped/failed reports."""
    zero = SignalConfidence(score=0.0, band=ConfidenceBand.LOW, method="none")
    return EvaluationConfidence(
        overall_score=0.0,
        band=ConfidenceBand.LOW,
        signal_confidence=zero,
        engine_adjustment=0.0,
        method="evaluation_engine_v1",
        components=(),
    )


def _zero_risk() -> ExpectedRiskEstimate:
    return ExpectedRiskEstimate(
        category=RiskEstimateCategory.UNKNOWN,
        normalized_score=0.0,
    )


def _zero_reward() -> ExpectedRewardEstimate:
    return ExpectedRewardEstimate(
        category=RewardEstimateCategory.UNKNOWN,
        normalized_score=0.0,
    )


def _zero_capital() -> CapitalEstimate:
    return CapitalEstimate(
        category=CapitalEstimateCategory.UNKNOWN,
        normalized_score=0.0,
    )


class EvaluationReportBuilder:
    """Assembles :class:`StrategyEvaluationReport` instances."""

    @staticmethod
    def build_success(
        *,
        signal: TradingSignal,
        record: StrategyRegistrationRecord,
        score_result: EvaluationScoreResult,
        run_context: EvaluationRunContext,
        evaluation_status: EvaluationStatus,
        outcome_class: EvaluationOutcomeClass,
        evaluated_at: datetime,
        duration_ms: float,
        extra_warnings: tuple[EvaluationWarningRecord, ...] = (),
    ) -> StrategyEvaluationReport:
        """Build a successful or abstaining evaluation report."""
        reasons = tuple(signal.reasons) + (
            f"evaluation engine scored suitability={score_result.suitability_score}",
        )
        warnings = score_result.warnings + extra_warnings
        return StrategyEvaluationReport(
            report_id=_report_id(record.strategy_id, run_context.snapshot.provenance.snapshot_id),
            strategy_id=record.strategy_id,
            strategy_version=record.strategy_version,
            strategy_family=record.strategy_family,
            display_name=record.display_name,
            plugin_priority=record.priority,
            evaluation_status=evaluation_status,
            outcome_class=outcome_class,
            signal=signal,
            suitability_score=score_result.suitability_score,
            ranking_score=score_result.ranking_score,
            confidence=score_result.confidence,
            expected_pop=score_result.expected_pop,
            expected_risk=score_result.expected_risk,
            expected_reward=score_result.expected_reward,
            capital_estimate=score_result.capital_estimate,
            reasons=reasons,
            factors=score_result.factors,
            warnings=warnings,
            errors=(),
            evaluated_at=evaluated_at,
            duration_ms=duration_ms,
            registry_record_fingerprint=record.metadata_fingerprint,
        )

    @staticmethod
    def build_failed(
        *,
        record: StrategyRegistrationRecord,
        run_context: EvaluationRunContext,
        error: EvaluationErrorRecord,
        evaluated_at: datetime,
        duration_ms: float,
    ) -> StrategyEvaluationReport:
        """Build a failed evaluation report."""
        return StrategyEvaluationReport(
            report_id=_report_id(record.strategy_id, run_context.snapshot.provenance.snapshot_id),
            strategy_id=record.strategy_id,
            strategy_version=record.strategy_version,
            strategy_family=record.strategy_family,
            display_name=record.display_name,
            plugin_priority=record.priority,
            evaluation_status=EvaluationStatus.FAILED,
            outcome_class=EvaluationOutcomeClass.ERROR,
            signal=None,
            suitability_score=0.0,
            ranking_score=0.0,
            confidence=_zero_confidence(),
            expected_pop=0.0,
            expected_risk=_zero_risk(),
            expected_reward=_zero_reward(),
            capital_estimate=_zero_capital(),
            reasons=(error.message,),
            factors=(),
            warnings=(),
            errors=(error,),
            evaluated_at=evaluated_at,
            duration_ms=duration_ms,
            registry_record_fingerprint=record.metadata_fingerprint,
        )

    @staticmethod
    def build_skipped(
        *,
        record: StrategyRegistrationRecord,
        run_context: EvaluationRunContext,
        reason: str,
        warning: EvaluationWarningRecord,
        evaluated_at: datetime,
    ) -> StrategyEvaluationReport:
        """Build a skipped evaluation report."""
        return StrategyEvaluationReport(
            report_id=_report_id(record.strategy_id, run_context.snapshot.provenance.snapshot_id),
            strategy_id=record.strategy_id,
            strategy_version=record.strategy_version,
            strategy_family=record.strategy_family,
            display_name=record.display_name,
            plugin_priority=record.priority,
            evaluation_status=EvaluationStatus.SKIPPED,
            outcome_class=EvaluationOutcomeClass.ERROR,
            signal=None,
            suitability_score=0.0,
            ranking_score=0.0,
            confidence=_zero_confidence(),
            expected_pop=0.0,
            expected_risk=_zero_risk(),
            expected_reward=_zero_reward(),
            capital_estimate=_zero_capital(),
            reasons=(reason,),
            factors=(),
            warnings=(warning,),
            errors=(),
            evaluated_at=evaluated_at,
            duration_ms=0.0,
            registry_record_fingerprint=record.metadata_fingerprint,
        )


def _should_skip_record(
    record: StrategyRegistrationRecord,
    snapshot: MarketSnapshot,
    skip_policy: EvaluationSkipPolicy,
) -> tuple[bool, str, EvaluationWarningRecord | None]:
    """Return whether evaluation should skip this registration record."""
    underlying = _underlying_symbol(snapshot)
    metadata = record.metadata
    if (
        skip_policy.skip_unsupported_underlying
        and metadata.supported_underlyings
        and underlying not in {item.strip().upper() for item in metadata.supported_underlyings}
    ):
        warning = EvaluationWarningRecord(
            code=ERROR_CONTEXT_INVALID,
            message=f"Underlying '{underlying}' not supported by strategy.",
            strategy_id=record.strategy_id,
            severity="INFO",
        )
        return True, warning.message, warning
    contract_count = len(snapshot.option_chain.contracts)
    if skip_policy.skip_insufficient_contracts and contract_count < metadata.min_contracts_required:
        warning = EvaluationWarningRecord(
            code=ERROR_CONTEXT_SNAPSHOT_INVALID,
            message="Insufficient option contracts for strategy requirements.",
            strategy_id=record.strategy_id,
            severity="INFO",
        )
        return True, warning.message, warning
    if skip_policy.skip_missing_volatility and metadata.requires_volatility_snapshot and snapshot.volatility is None:
        warning = EvaluationWarningRecord(
            code=ERROR_CONTEXT_SNAPSHOT_INVALID,
            message="Strategy requires volatility snapshot.",
            strategy_id=record.strategy_id,
            severity="INFO",
        )
        return True, warning.message, warning
    return False, "", None


def _build_summary(
    registry_snapshot: RegistrySnapshot,
    reports: tuple[StrategyEvaluationReport, ...],
    ranked_reports: tuple[StrategyEvaluationReport, ...],
) -> EvaluationBundleSummary:
    """Build bundle summary statistics."""
    total_success = sum(1 for r in reports if r.evaluation_status is EvaluationStatus.SUCCESS)
    total_abstain = sum(1 for r in reports if r.evaluation_status is EvaluationStatus.ABSTAIN)
    total_failed = sum(1 for r in reports if r.evaluation_status is EvaluationStatus.FAILED)
    total_skipped = sum(1 for r in reports if r.evaluation_status is EvaluationStatus.SKIPPED)
    total_actionable = sum(1 for r in reports if r.outcome_class is EvaluationOutcomeClass.ACTIONABLE)
    total_evaluated = sum(
        1 for r in reports if r.evaluation_status not in (EvaluationStatus.SKIPPED,)
    )
    top = next((r for r in ranked_reports if r.outcome_class is EvaluationOutcomeClass.ACTIONABLE), None)
    return EvaluationBundleSummary(
        total_registered=registry_snapshot.plugin_count,
        total_enabled=registry_snapshot.enabled_count,
        total_evaluated=total_evaluated,
        total_success=total_success,
        total_abstain=total_abstain,
        total_failed=total_failed,
        total_skipped=total_skipped,
        total_actionable=total_actionable,
        top_strategy_id=top.strategy_id if top else None,
        top_suitability_score=top.suitability_score if top else None,
        top_ranking_score=top.ranking_score if top else None,
    )


class StrategyEvaluationEngine(BaseEngine):
    """Evaluates all enabled strategy plugins and produces ranked reports."""

    def __init__(
        self,
        config: StrategyEvaluationEngineConfig | None = None,
        registry: StrategyRegistry | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        scorer: EvaluationScorer | None = None,
        re_raise_on_failure: bool = False,
    ) -> None:
        """Initialize the evaluation engine.

        Args:
            config: Engine policy configuration.
            registry: Strategy registry for plugin instance lookup.
            clock: Injectable clock for deterministic timestamps.
            scorer: Optional custom scorer implementation.
            re_raise_on_failure: Whether to re-raise unexpected failures.
        """
        self._eval_config = config or StrategyEvaluationEngineConfig()
        if registry is None:
            raise StrategyEvaluationConfigurationError(
                "StrategyRegistry instance is required.",
                code=ERROR_CONFIG_INVALID,
                field="registry",
            )
        self._registry = registry
        self._clock = clock or _utc_now
        self._scorer = scorer or EvaluationScorer()
        self._run_lock = threading.RLock()
        super().__init__(
            config=MappingProxyType({"engine": "strategy_evaluation_engine"}),
            re_raise_on_failure=re_raise_on_failure,
        )

    @property
    def eval_config(self) -> StrategyEvaluationEngineConfig:
        """Return immutable evaluation engine configuration."""
        return self._eval_config

    @property
    def registry(self) -> StrategyRegistry:
        """Return injected strategy registry."""
        return self._registry

    @property
    def engine_name(self) -> str:
        """Return stable engine identifier."""
        return "strategy_evaluation_engine"

    @property
    def engine_version(self) -> str:
        """Return semantic engine version."""
        return STRATEGY_EVALUATION_ENGINE_VERSION

    def validate_configuration(self) -> None:
        """Validate static engine configuration."""
        _ = self._eval_config

    def validate_run_context(self, run_context: EvaluationRunContext) -> None:
        """Validate evaluation run context.

        Raises:
            StrategyEvaluationContextError: If context is invalid.
        """
        if run_context.snapshot is None:
            raise StrategyEvaluationContextError(
                "snapshot must not be None.",
                code=ERROR_CONTEXT_SNAPSHOT_MISSING,
                field="snapshot",
            )
        if run_context.registry_snapshot is None:
            raise StrategyEvaluationContextError(
                "registry_snapshot must not be None.",
                code=ERROR_CONTEXT_INVALID,
                field="registry_snapshot",
            )
        if not run_context.correlation_id.strip():
            raise StrategyEvaluationContextError(
                "correlation_id must be non-empty.",
                code=ERROR_CONTEXT_INVALID,
                field="correlation_id",
            )
        if run_context.as_of.tzinfo is None or run_context.as_of.tzinfo.utcoffset(run_context.as_of) is None:
            raise StrategyEvaluationContextError(
                "as_of must be timezone-aware.",
                code=ERROR_CONTEXT_INVALID,
                field="as_of",
            )
        provenance = run_context.snapshot.provenance
        if provenance.as_of.tzinfo is None or provenance.as_of.tzinfo.utcoffset(provenance.as_of) is None:
            raise StrategyEvaluationContextError(
                "snapshot.provenance.as_of must be timezone-aware.",
                code=ERROR_CONTEXT_INVALID,
                field="snapshot.provenance.as_of",
            )
        chain_underlying = run_context.snapshot.option_chain.metadata.underlying.strip().upper()
        spot_symbol = run_context.snapshot.underlying.symbol.strip().upper()
        if chain_underlying != spot_symbol:
            raise StrategyEvaluationContextError(
                "Underlying mismatch between spot and option chain.",
                code=ERROR_CONTEXT_SNAPSHOT_INVALID,
                field="snapshot.underlying",
            )
        invalid = run_context.snapshot.quality.validation_status is SnapshotValidationStatus.INVALID
        if invalid:
            if run_context.execution_mode is StrategyExecutionMode.LIVE and self._eval_config.reject_invalid_snapshot_live:
                raise StrategyEvaluationContextError(
                    "Snapshot quality is INVALID for LIVE evaluation.",
                    code=ERROR_CONTEXT_SNAPSHOT_INVALID,
                    field="snapshot.quality.validation_status",
                )
            if not self._eval_config.allow_invalid_snapshot_analysis:
                raise StrategyEvaluationContextError(
                    "Snapshot quality is INVALID.",
                    code=ERROR_CONTEXT_SNAPSHOT_INVALID,
                    field="snapshot.quality.validation_status",
                )

    def evaluate(self, context: EngineContext | EvaluationRunContext) -> EngineResult:  # type: ignore[override]
        """Evaluate enabled strategies and return an :class:`EngineResult`.

        Args:
            context: :class:`EngineContext` with :class:`EvaluationRunContext` payload,
                or a direct :class:`EvaluationRunContext`.

        Returns:
            Engine result containing :class:`StrategyEvaluationBundle` payload.
        """
        if isinstance(context, EvaluationRunContext):
            run_context = context
            correlation_id = run_context.correlation_id
            as_of = run_context.as_of
        elif isinstance(context, EngineContext):
            if not isinstance(context.payload, EvaluationRunContext):
                raise StrategyEvaluationValidationError(
                    "EngineContext.payload must be EvaluationRunContext.",
                    code=ERROR_CONTEXT_INVALID,
                    field="payload",
                )
            run_context = context.payload
            correlation_id = context.correlation_id
            as_of = context.as_of
        else:
            raise StrategyEvaluationValidationError(
                "Context must be EngineContext or EvaluationRunContext.",
                code=ERROR_CONTEXT_INVALID,
                field="context",
            )

        started_at = self._clock()
        start_perf = time.perf_counter()
        try:
            bundle = self.evaluate_bundle(run_context)
        except StrategyEvaluationError as exc:
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
                errors=(
                    EngineErrorRecord(
                        code=exc.code,
                        message=str(exc),
                        field=exc.field,
                    ),
                ),
            )

        completed_at = self._clock()
        duration_ms = (time.perf_counter() - start_perf) * 1000.0
        status = self._map_bundle_status(bundle)
        engine_warnings = tuple(
            EngineWarningRecord(code=w.code, message=w.message, field=w.field)
            for w in bundle.warnings
        )
        engine_errors: tuple[EngineErrorRecord, ...] = ()
        if status is EngineStatus.FAILED:
            engine_errors = tuple(
                EngineErrorRecord(
                    code=error.code,
                    message=error.message,
                    field=error.field,
                )
                for report in bundle.reports
                for error in report.errors
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
            payload=bundle,
            warnings=engine_warnings,
            errors=engine_errors,
        )

    def evaluate_bundle(self, run_context: EvaluationRunContext) -> StrategyEvaluationBundle:
        """Evaluate all enabled strategies and return an immutable bundle."""
        with self._run_lock:
            return self._evaluate_bundle_locked(run_context)

    def _evaluate_bundle_locked(self, run_context: EvaluationRunContext) -> StrategyEvaluationBundle:
        self.validate_run_context(run_context)
        bundle_warnings: list[EvaluationWarningRecord] = []

        if self._eval_config.strict_registry_match:
            live_fingerprint = self._registry.snapshot().registry_fingerprint
            if live_fingerprint != run_context.registry_snapshot.registry_fingerprint:
                raise StrategyEvaluationRegistryError(
                    "Live registry fingerprint does not match registry snapshot.",
                    code=ERROR_REGISTRY_FINGERPRINT_MISMATCH,
                )

        if run_context.registry_snapshot.enabled_count == 0:
            bundle_warnings.append(
                EvaluationWarningRecord(
                    code=ERROR_EMPTY_ENABLED_SET,
                    message="Registry snapshot has zero enabled strategies.",
                    severity="WARNING",
                )
            )

        _logger.info(
            "strategy.evaluation.start",
            extra={
                "correlation_id": run_context.correlation_id,
                "snapshot_id": run_context.snapshot.provenance.snapshot_id,
                "registry_fingerprint": run_context.registry_snapshot.registry_fingerprint,
            },
        )

        records = run_context.registry_snapshot.enabled_records
        if self._eval_config.parallelism_mode is EvaluationParallelismMode.PARALLEL:
            reports = self._evaluate_parallel(records, run_context)
        else:
            reports = self._evaluate_sequential(records, run_context)

        ranked = rank_reports(reports)
        summary = _build_summary(run_context.registry_snapshot, reports, ranked)

        if summary.total_failed > 0 and summary.total_success + summary.total_abstain > 0:
            bundle_warnings.append(
                EvaluationWarningRecord(
                    code=ERROR_PARTIAL_FAILURE,
                    message="Some strategy evaluations failed.",
                    severity="WARNING",
                )
            )
        if summary.total_actionable == 0 and reports:
            bundle_warnings.append(
                EvaluationWarningRecord(
                    code=ERROR_NO_ACTIONABLE,
                    message="No actionable strategy reports produced.",
                    severity="WARNING",
                )
            )
        if summary.total_abstain == summary.total_enabled and summary.total_enabled > 0:
            bundle_warnings.append(
                EvaluationWarningRecord(
                    code=ERROR_ALL_ABSTAIN,
                    message="All enabled strategies abstained.",
                    severity="INFO",
                )
            )

        evaluated_at = self._clock()
        deterministic = self._eval_config.scoring_policy.deterministic_fingerprint
        fingerprint = evaluation_fingerprint(reports, deterministic=deterministic)
        bundle = StrategyEvaluationBundle(
            bundle_id=_bundle_id_from(fingerprint, run_context.correlation_id),
            correlation_id=run_context.correlation_id,
            snapshot_id=run_context.snapshot.provenance.snapshot_id,
            registry_fingerprint=run_context.registry_snapshot.registry_fingerprint,
            execution_mode=run_context.execution_mode,
            evaluated_at=evaluated_at,
            reports=reports,
            ranked_reports=ranked,
            summary=summary,
            bundle_fingerprint=fingerprint,
            warnings=tuple(bundle_warnings),
        )
        self.assert_valid_bundle(bundle)
        _logger.info(
            "strategy.evaluation.complete",
            extra={
                "correlation_id": run_context.correlation_id,
                "snapshot_id": run_context.snapshot.provenance.snapshot_id,
                "plugin_count": len(reports),
            },
        )
        return bundle

    def _evaluate_sequential(
        self,
        records: tuple[StrategyRegistrationRecord, ...],
        run_context: EvaluationRunContext,
    ) -> tuple[StrategyEvaluationReport, ...]:
        reports: list[StrategyEvaluationReport] = []
        for record in records:
            report = self._evaluate_record(record, run_context)
            reports.append(report)
            if (
                report.evaluation_status is EvaluationStatus.FAILED
                and self._eval_config.failure_mode is EvaluationFailureMode.FAIL_FAST
            ):
                break
        return tuple(reports)

    def _evaluate_parallel(
        self,
        records: tuple[StrategyRegistrationRecord, ...],
        run_context: EvaluationRunContext,
    ) -> tuple[StrategyEvaluationReport, ...]:
        if not records:
            return ()
        indexed: dict[int, StrategyEvaluationReport] = {}
        fail_fast = self._eval_config.failure_mode is EvaluationFailureMode.FAIL_FAST
        stop = threading.Event()

        def worker(index: int, record: StrategyRegistrationRecord) -> None:
            if stop.is_set():
                return
            indexed[index] = self._evaluate_record(record, run_context)
            if fail_fast and indexed[index].evaluation_status is EvaluationStatus.FAILED:
                stop.set()

        max_workers = min(self._eval_config.max_parallelism, len(records))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(worker, index, record)
                for index, record in enumerate(records)
            ]
            for future in as_completed(futures):
                future.result()

        return tuple(indexed[index] for index in range(len(records)) if index in indexed)

    def _evaluate_record(
        self,
        record: StrategyRegistrationRecord,
        run_context: EvaluationRunContext,
    ) -> StrategyEvaluationReport:
        skip, reason, warning = _should_skip_record(
            record,
            run_context.snapshot,
            self._eval_config.skip_policy,
        )
        if skip and warning is not None:
            _logger.info(
                "strategy.evaluation.plugin.skipped",
                extra={"strategy_id": record.strategy_id},
            )
            return EvaluationReportBuilder.build_skipped(
                record=record,
                run_context=run_context,
                reason=reason,
                warning=warning,
                evaluated_at=self._clock(),
            )

        start_perf = time.perf_counter()
        try:
            strategy = self._registry.get(record.strategy_id)
        except StrategyRegistryNotFoundError as exc:
            return EvaluationReportBuilder.build_failed(
                record=record,
                run_context=run_context,
                error=EvaluationErrorRecord(
                    code=ERROR_REGISTRY_PLUGIN_MISSING,
                    message=str(exc),
                    strategy_id=record.strategy_id,
                ),
                evaluated_at=self._clock(),
                duration_ms=(time.perf_counter() - start_perf) * 1000.0,
            )

        extra_warnings: list[EvaluationWarningRecord] = []
        if strategy.metadata_fingerprint() != record.metadata_fingerprint:
            extra_warnings.append(
                EvaluationWarningRecord(
                    code=WARN_METADATA_DRIFT,
                    message="Live plugin metadata fingerprint differs from registry record.",
                    strategy_id=record.strategy_id,
                    severity="WARNING",
                )
            )

        merged_tags = dict(run_context.tags)
        merged_tags.update(record.tags)
        strategy_context = StrategyContext(
            correlation_id=run_context.correlation_id,
            as_of=run_context.as_of,
            snapshot=run_context.snapshot,
            execution_mode=run_context.execution_mode,
            tags=MappingProxyType(merged_tags),
        )

        _logger.debug(
            "strategy.evaluation.plugin.start",
            extra={"strategy_id": record.strategy_id},
        )
        try:
            signal = strategy.run(strategy_context)
        except StrategyContextError as exc:
            return EvaluationReportBuilder.build_failed(
                record=record,
                run_context=run_context,
                error=EvaluationErrorRecord(
                    code=ERROR_PLUGIN_CONTEXT_INVALID,
                    message=str(exc),
                    strategy_id=record.strategy_id,
                    field=getattr(exc, "field", None),
                ),
                evaluated_at=self._clock(),
                duration_ms=(time.perf_counter() - start_perf) * 1000.0,
            )
        except StrategySignalError as exc:
            return EvaluationReportBuilder.build_failed(
                record=record,
                run_context=run_context,
                error=EvaluationErrorRecord(
                    code=ERROR_PLUGIN_SIGNAL_INVALID,
                    message=str(exc),
                    strategy_id=record.strategy_id,
                    field=getattr(exc, "field", None),
                ),
                evaluated_at=self._clock(),
                duration_ms=(time.perf_counter() - start_perf) * 1000.0,
            )
        except EngineExecutionError as exc:
            return EvaluationReportBuilder.build_failed(
                record=record,
                run_context=run_context,
                error=EvaluationErrorRecord(
                    code=ERROR_PLUGIN_EXECUTION_FAILED,
                    message=str(exc),
                    strategy_id=record.strategy_id,
                ),
                evaluated_at=self._clock(),
                duration_ms=(time.perf_counter() - start_perf) * 1000.0,
            )
        except Exception as exc:
            return EvaluationReportBuilder.build_failed(
                record=record,
                run_context=run_context,
                error=EvaluationErrorRecord(
                    code=ERROR_PLUGIN_UNEXPECTED,
                    message=str(exc),
                    strategy_id=record.strategy_id,
                ),
                evaluated_at=self._clock(),
                duration_ms=(time.perf_counter() - start_perf) * 1000.0,
            )

        duration_ms = (time.perf_counter() - start_perf) * 1000.0
        if signal is None:
            return EvaluationReportBuilder.build_failed(
                record=record,
                run_context=run_context,
                error=EvaluationErrorRecord(
                    code=ERROR_PLUGIN_SIGNAL_INVALID,
                    message="Strategy returned None signal.",
                    strategy_id=record.strategy_id,
                ),
                evaluated_at=self._clock(),
                duration_ms=duration_ms,
            )

        validation = validate_trading_signal(
            signal,
            context=SignalValidationContext(
                snapshot_id=run_context.snapshot.provenance.snapshot_id,
                underlying=_underlying_symbol(run_context.snapshot),
                execution_mode=run_context.execution_mode.value,
                reference_time=run_context.reference_time or run_context.as_of,
            ),
        )
        if not validation.is_valid or signal.strategy_id != record.strategy_id:
            message = "Signal validation failed."
            if validation.errors:
                message = validation.errors[0].message
            elif signal.strategy_id != record.strategy_id:
                message = "signal.strategy_id mismatch with registry record."
            return EvaluationReportBuilder.build_failed(
                record=record,
                run_context=run_context,
                error=EvaluationErrorRecord(
                    code=ERROR_PLUGIN_SIGNAL_INVALID,
                    message=message,
                    strategy_id=record.strategy_id,
                ),
                evaluated_at=self._clock(),
                duration_ms=duration_ms,
            )

        if signal.action in (SignalAction.ABSTAIN, SignalAction.NO_TRADE):
            eval_status = EvaluationStatus.ABSTAIN
        else:
            eval_status = EvaluationStatus.SUCCESS
        outcome_class = classify_outcome(signal, evaluation_status=eval_status)
        score_result = self._scorer.score(
            signal=signal,
            snapshot=run_context.snapshot,
            record=record,
            policy=self._eval_config.scoring_policy,
            outcome_class=outcome_class,
            execution_mode=run_context.execution_mode,
        )
        _logger.info(
            "strategy.evaluation.plugin.success",
            extra={"strategy_id": record.strategy_id, "duration_ms": duration_ms},
        )
        return EvaluationReportBuilder.build_success(
            signal=signal,
            record=record,
            score_result=score_result,
            run_context=run_context,
            evaluation_status=eval_status,
            outcome_class=outcome_class,
            evaluated_at=self._clock(),
            duration_ms=duration_ms,
            extra_warnings=tuple(extra_warnings),
        )

    def validate_bundle(self, bundle: StrategyEvaluationBundle) -> EvaluationValidationResult:
        """Validate sealed evaluation bundle consistency."""
        errors: list[EvaluationErrorRecord] = []
        warnings: list[EvaluationWarningRecord] = []
        seen: set[str] = set()
        for report in bundle.reports:
            if report.strategy_id in seen:
                errors.append(
                    EvaluationErrorRecord(
                        code=ERROR_BUNDLE_INVALID,
                        message=f"Duplicate strategy_id '{report.strategy_id}'.",
                        strategy_id=report.strategy_id,
                    )
                )
            seen.add(report.strategy_id)
            if not (SCORE_MIN <= report.suitability_score <= SCORE_MAX):
                errors.append(
                    EvaluationErrorRecord(
                        code=ERROR_BUNDLE_INVALID,
                        message="suitability_score out of bounds.",
                        strategy_id=report.strategy_id,
                        field="suitability_score",
                    )
                )
            if report.evaluation_status is EvaluationStatus.SUCCESS and report.signal is None:
                errors.append(
                    EvaluationErrorRecord(
                        code=ERROR_BUNDLE_INVALID,
                        message="SUCCESS report missing signal.",
                        strategy_id=report.strategy_id,
                    )
                )
            if report.evaluation_status is EvaluationStatus.FAILED and not report.errors:
                errors.append(
                    EvaluationErrorRecord(
                        code=ERROR_BUNDLE_INVALID,
                        message="FAILED report missing errors.",
                        strategy_id=report.strategy_id,
                    )
                )
        if sorted(bundle.reports, key=lambda r: r.strategy_id) != sorted(
            bundle.ranked_reports, key=lambda r: r.strategy_id
        ):
            errors.append(
                EvaluationErrorRecord(
                    code=ERROR_BUNDLE_INVALID,
                    message="ranked_reports is not a permutation of reports.",
                )
            )
        recomputed = evaluation_fingerprint(
            bundle.reports,
            deterministic=self._eval_config.scoring_policy.deterministic_fingerprint,
        )
        if recomputed != bundle.bundle_fingerprint:
            errors.append(
                EvaluationErrorRecord(
                    code=ERROR_BUNDLE_INVALID,
                    message="bundle_fingerprint mismatch.",
                    field="bundle_fingerprint",
                )
            )
        return EvaluationValidationResult(errors=tuple(errors), warnings=tuple(warnings))

    def assert_valid_bundle(self, bundle: StrategyEvaluationBundle) -> None:
        """Validate bundle and raise on failure."""
        result = self.validate_bundle(bundle)
        if not result.is_valid:
            first = result.errors[0]
            raise StrategyEvaluationValidationError(
                first.message,
                code=ERROR_BUNDLE_INVALID,
                strategy_id=first.strategy_id,
                field=first.field,
            )

    def _map_bundle_status(self, bundle: StrategyEvaluationBundle) -> EngineStatus:
        summary = bundle.summary
        if summary.total_enabled == 0:
            return EngineStatus.SUCCESS
        if summary.total_failed == summary.total_enabled:
            return EngineStatus.FAILED
        if summary.total_failed > 0:
            return EngineStatus.PARTIAL
        return EngineStatus.SUCCESS


def report_to_dict(report: StrategyEvaluationReport, *, omit_nulls: bool = True) -> dict[str, Any]:
    """Serialize an evaluation report to a dictionary."""
    payload: dict[str, Any] = {
        "report_id": report.report_id,
        "strategy_id": report.strategy_id,
        "strategy_version": report.strategy_version,
        "strategy_family": report.strategy_family.value,
        "display_name": report.display_name,
        "plugin_priority": report.plugin_priority,
        "evaluation_status": report.evaluation_status.value,
        "outcome_class": report.outcome_class.value,
        "suitability_score": report.suitability_score,
        "ranking_score": report.ranking_score,
        "expected_pop": report.expected_pop,
        "confidence": {
            "overall_score": report.confidence.overall_score,
            "band": report.confidence.band.value,
            "engine_adjustment": report.confidence.engine_adjustment,
            "method": report.confidence.method,
        },
        "expected_risk": {
            "category": report.expected_risk.category.value,
            "normalized_score": report.expected_risk.normalized_score,
        },
        "expected_reward": {
            "category": report.expected_reward.category.value,
            "normalized_score": report.expected_reward.normalized_score,
            "risk_reward_hint": report.expected_reward.risk_reward_hint,
        },
        "capital_estimate": {
            "category": report.capital_estimate.category.value,
            "normalized_score": report.capital_estimate.normalized_score,
            "allocation_percent_hint": report.capital_estimate.allocation_percent_hint,
        },
        "reasons": list(report.reasons),
        "evaluated_at": report.evaluated_at.isoformat(),
        "duration_ms": report.duration_ms,
        "registry_record_fingerprint": report.registry_record_fingerprint,
    }
    if report.signal is not None:
        payload["signal"] = signal_to_dict(report.signal, omit_nulls=omit_nulls)
    if omit_nulls:
        return {key: value for key, value in payload.items() if value is not None}
    return payload


def bundle_to_dict(bundle: StrategyEvaluationBundle, *, omit_nulls: bool = True) -> dict[str, Any]:
    """Serialize evaluation bundle to dictionary."""
    return {
        "schema_version": STRATEGY_EVALUATION_SCHEMA_VERSION,
        "bundle_id": bundle.bundle_id,
        "correlation_id": bundle.correlation_id,
        "snapshot_id": bundle.snapshot_id,
        "registry_fingerprint": bundle.registry_fingerprint,
        "execution_mode": bundle.execution_mode.value,
        "evaluated_at": bundle.evaluated_at.isoformat(),
        "bundle_fingerprint": bundle.bundle_fingerprint,
        "summary": {
            "total_registered": bundle.summary.total_registered,
            "total_enabled": bundle.summary.total_enabled,
            "total_evaluated": bundle.summary.total_evaluated,
            "total_success": bundle.summary.total_success,
            "total_abstain": bundle.summary.total_abstain,
            "total_failed": bundle.summary.total_failed,
            "total_skipped": bundle.summary.total_skipped,
            "total_actionable": bundle.summary.total_actionable,
            "top_strategy_id": bundle.summary.top_strategy_id,
            "top_suitability_score": bundle.summary.top_suitability_score,
            "top_ranking_score": bundle.summary.top_ranking_score,
        },
        "ranked_reports": [report_to_dict(report, omit_nulls=omit_nulls) for report in bundle.ranked_reports],
    }


def bundle_to_json(bundle: StrategyEvaluationBundle, *, omit_nulls: bool = True) -> str:
    """Serialize evaluation bundle to JSON."""
    return json.dumps(bundle_to_dict(bundle, omit_nulls=omit_nulls), sort_keys=True)


def bundle_from_json(payload: str) -> StrategyEvaluationBundle:
    """Deserialize evaluation bundle from JSON (audit-only)."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise StrategyEvaluationValidationError(
            "Malformed evaluation bundle JSON.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(data, dict):
        raise StrategyEvaluationValidationError(
            "Evaluation bundle JSON root must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    schema_version = str(data.get("schema_version", ""))
    if schema_version != STRATEGY_EVALUATION_SCHEMA_VERSION:
        raise StrategyEvaluationValidationError(
            f"Unsupported schema version: {schema_version!r}.",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
            field="schema_version",
        )
    raise StrategyEvaluationValidationError(
        "Full bundle deserialization is audit-only in v1.",
        code=ERROR_SERIALIZATION_MALFORMED,
    )


__all__ = [
    "DEFAULT_MAX_PARALLELISM",
    "DEFAULT_PLUGIN_TIMEOUT_MS",
    "ERROR_ALL_ABSTAIN",
    "ERROR_BUNDLE_INVALID",
    "ERROR_CONFIG_INVALID",
    "ERROR_CONTEXT_INVALID",
    "ERROR_CONTEXT_SNAPSHOT_INVALID",
    "ERROR_CONTEXT_SNAPSHOT_MISSING",
    "ERROR_EMPTY_ENABLED_SET",
    "ERROR_NO_ACTIONABLE",
    "ERROR_PARTIAL_FAILURE",
    "ERROR_PLUGIN_CONTEXT_INVALID",
    "ERROR_PLUGIN_EXECUTION_FAILED",
    "ERROR_PLUGIN_SIGNAL_INVALID",
    "ERROR_PLUGIN_UNEXPECTED",
    "ERROR_REGISTRY_FINGERPRINT_MISMATCH",
    "ERROR_REGISTRY_PLUGIN_MISSING",
    "ERROR_SERIALIZATION_MALFORMED",
    "ERROR_SERIALIZATION_UNSUPPORTED_VERSION",
    "POP_MAX",
    "POP_MIN",
    "RANKING_SCORE_EPSILON",
    "SCORE_MAX",
    "SCORE_MIN",
    "STRATEGY_EVALUATION_ENGINE_VERSION",
    "STRATEGY_EVALUATION_SCHEMA_VERSION",
    "CapitalEstimate",
    "CapitalEstimateCategory",
    "EvaluationBundleSummary",
    "EvaluationConfidence",
    "EvaluationErrorRecord",
    "EvaluationFactor",
    "EvaluationFailureMode",
    "EvaluationOutcomeClass",
    "EvaluationParallelismMode",
    "EvaluationReportBuilder",
    "EvaluationRunContext",
    "EvaluationScoreResult",
    "EvaluationScorer",
    "EvaluationSkipPolicy",
    "EvaluationScoringPolicy",
    "EvaluationStatus",
    "EvaluationValidationResult",
    "EvaluationWarningRecord",
    "ExpectedRewardEstimate",
    "ExpectedRiskEstimate",
    "RewardEstimateCategory",
    "RiskEstimateCategory",
    "StrategyEvaluationBundle",
    "StrategyEvaluationConfigurationError",
    "StrategyEvaluationContextError",
    "StrategyEvaluationEngine",
    "StrategyEvaluationEngineConfig",
    "StrategyEvaluationError",
    "StrategyEvaluationRegistryError",
    "StrategyEvaluationReport",
    "StrategyEvaluationValidationError",
    "bundle_from_json",
    "bundle_to_dict",
    "bundle_to_json",
    "classify_outcome",
    "evaluation_fingerprint",
    "rank_reports",
    "report_to_dict",
]
