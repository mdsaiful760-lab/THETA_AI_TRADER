"""Deterministic bull-put-spread strategy recommendation plugin.

The module evaluates immutable market and evidence snapshots only.  It does not
fetch market data, manage portfolio risk, submit orders, or otherwise perform
I/O.  Maximum loss is always finite and labeled ``DEFINED_RISK``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

from market_data.market_snapshot import (
    MarketSnapshot,
    OptionContractSnapshot,
    OptionType,
    SnapshotValidationStatus,
    validate_market_snapshot,
)
from strategy.base_strategy import (
    BaseStrategy,
    StrategyContext,
    StrategyMetadata,
    StrategyPluginConfig,
    StrategyRiskProfileHint,
)
from strategy.signals import (
    RiskProfileHint,
    SignalAction,
    SignalConfidence,
    SignalDirection,
    SignalMarketContext,
    SignalRiskMetadata,
    StrategyFamily,
    StructureHint,
    TradingSignal,
    confidence_band_for_score,
    market_context_from_snapshot,
)
from strategy.strategy_scoring_framework import (
    ConfidenceReport,
    FactorCategory,
    FactorInput,
    FactorInputBundle,
    RawValueKind,
    ScoreRequest,
    StrategyExplanation,
    StrategyScore,
    StrategyScoringFramework,
    default_scoring_framework_config,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")
_HALF = Decimal("0.5")
_EPSILON = Decimal("0.00000001")
_SCHEMA_VERSION = "1.0"
_RISK_STATEMENT = (
    "DEFINED_RISK: bull put spread maximum loss is finite and wing-capped."
)
_PASS_REGIMES = frozenset({"TRENDING_UP", "BULLISH", "MEAN_REVERTING", "RANGE_BOUND"})
_BEARISH_DIRECTIONS = frozenset({"BEARISH", "TRENDING_DOWN"})


class PremiumPricePolicy(str, Enum):
    """Quote policy used for theoretical credit and debit prices."""

    MID = "MID"
    CONSERVATIVE = "CONSERVATIVE"
    ASK_CREDIT = "ASK_CREDIT"


class WingSelectionPolicy(str, Enum):
    """Long-put / wing selection policy."""

    DELTA_TARGET = "DELTA_TARGET"
    FIXED_WIDTH = "FIXED_WIDTH"
    WIDTH_THEN_DELTA = "WIDTH_THEN_DELTA"


class EntryRecommendationState(str, Enum):
    """Outcome of the bull-put-spread entry evaluation."""

    ENTER = "ENTER"
    ABSTAIN = "ABSTAIN"
    REJECT = "REJECT"


@dataclass(frozen=True)
class MarketRegimeEvidence:
    """Injected, timestamped market-regime observation."""

    tag: str
    observed_at: datetime
    provenance: str = "test"


@dataclass(frozen=True)
class TrendEvidence:
    """Injected trend direction and/or strength observation."""

    observed_at: datetime
    direction: str | None = None
    strength: Decimal | None = None
    provenance: str = "test"


@dataclass(frozen=True)
class EventRiskEvidence:
    """Injected, timestamped event-risk observation."""

    adverse: bool
    observed_at: datetime
    provenance: str = "test"
    label: str = "NONE"


@dataclass(frozen=True)
class TimeWindow:
    """Exchange-local, same-day time interval."""

    start: time
    end: time
    timezone: str = "Asia/Kolkata"

    def __post_init__(self) -> None:
        """Validate a non-empty, same-day interval and timezone."""
        if self.start >= self.end:
            raise ValueError(
                "CFG-BPS-011: TimeWindow must be a same-day increasing interval."
            )
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError("CFG-BPS-011: TimeWindow timezone is invalid.") from exc


@dataclass(frozen=True)
class BullPutSpreadContext:
    """Typed evidence extension without changing ``StrategyContext.tags``."""

    strategy_context: StrategyContext
    regime_evidence: MarketRegimeEvidence
    event_risk_evidence: EventRiskEvidence
    trend_evidence: TrendEvidence | None = None
    iv_rank: Decimal | None = None


@dataclass(frozen=True)
class BullPutSpreadConfiguration:
    """Immutable bull-put-spread suitability and selection policy."""

    short_target_delta: Decimal = Decimal("0.25")
    long_target_delta: Decimal = Decimal("0.10")
    short_put_target_delta: Decimal | None = None
    long_put_target_delta: Decimal | None = None
    wing_selection_policy: WingSelectionPolicy = WingSelectionPolicy.WIDTH_THEN_DELTA
    target_wing_width: Decimal | None = None
    minimum_wing_width: Decimal | None = None
    maximum_wing_width: Decimal | None = None
    require_short_otm: bool = True
    minimum_iv_rank: Decimal = Decimal("40")
    maximum_bearish_trend_strength: Decimal = Decimal("0.55")
    require_trend_strength: bool = True
    require_trend_evidence: bool = True
    maximum_spread_width: Decimal | None = None
    maximum_relative_spread_width: Decimal = Decimal("0.15")
    minimum_premium: Decimal = Decimal("0")
    minimum_open_interest: int = 1
    minimum_volume: int = 1
    minimum_liquidity_score: Decimal | None = None
    entry_time_window: TimeWindow = field(
        default_factory=lambda: TimeWindow(time(9, 15), time(15, 30))
    )
    exit_time_window: TimeWindow = field(
        default_factory=lambda: TimeWindow(time(15, 0), time(15, 30))
    )
    scoring_profile_name: str = "PREMIUM_SELLING"
    supported_underlyings: frozenset[str] = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})
    max_snapshot_age_seconds: int = 5
    require_valid_snapshot: bool = True
    short_delta_selection_tolerance: Decimal = Decimal("0.03")
    long_delta_selection_tolerance: Decimal = Decimal("0.03")
    premium_price_policy: PremiumPricePolicy = PremiumPricePolicy.MID
    minimum_dte: int = 0
    maximum_dte: int = 45
    require_iv_rank: bool = True
    require_greeks: bool = True
    require_open_interest: bool = True
    require_volume: bool = True
    iv_rank_lookback_observations: int = 252
    contract_multiplier: Decimal = _ONE

    def __post_init__(self) -> None:
        """Validate and normalize configuration independent of market inputs."""
        decimals = (
            self.short_target_delta,
            self.long_target_delta,
            self.short_put_target_delta,
            self.long_put_target_delta,
            self.target_wing_width,
            self.minimum_wing_width,
            self.maximum_wing_width,
            self.minimum_iv_rank,
            self.maximum_bearish_trend_strength,
            self.maximum_spread_width,
            self.maximum_relative_spread_width,
            self.minimum_premium,
            self.minimum_liquidity_score,
            self.short_delta_selection_tolerance,
            self.long_delta_selection_tolerance,
            self.contract_multiplier,
        )
        if any(value is not None and not value.is_finite() for value in decimals):
            raise ValueError("CFG-BPS-001: Decimal configuration must be finite.")
        short_targets = (
            self.short_target_delta,
            self.short_put_target_delta or self.short_target_delta,
        )
        if any(not (_ZERO < abs(value) < Decimal("0.50")) for value in short_targets):
            raise ValueError("CFG-BPS-004: short target deltas must be within (0, 0.50).")
        if not (_ZERO < abs(self.long_target_delta) < abs(self.short_target_delta)):
            raise ValueError(
                "CFG-BPS-012: long_target_delta must be within (0, short_target_delta)."
            )
        long_put = abs(self.long_put_target_delta or self.long_target_delta)
        short_put = abs(self.short_put_target_delta or self.short_target_delta)
        if not (_ZERO < long_put < short_put):
            raise ValueError(
                "CFG-BPS-005: long targets must be strictly below short targets."
            )
        if not (_ZERO <= self.minimum_iv_rank <= _HUNDRED):
            raise ValueError("CFG-BPS-001: minimum_iv_rank must be within [0, 100].")
        if not (_ZERO <= self.maximum_bearish_trend_strength <= _ONE):
            raise ValueError(
                "CFG-BPS-013: maximum_bearish_trend_strength must be within [0, 1]."
            )
        if self.maximum_spread_width is not None and self.maximum_spread_width <= _ZERO:
            raise ValueError("CFG-BPS-010: maximum_spread_width must be positive.")
        if not (_ZERO < self.maximum_relative_spread_width <= _ONE):
            raise ValueError(
                "CFG-BPS-001: maximum_relative_spread_width must be within (0, 1]."
            )
        if self.minimum_premium < _ZERO:
            raise ValueError("CFG-BPS-001: minimum_premium must be non-negative.")
        if (
            self.short_delta_selection_tolerance < _ZERO
            or self.long_delta_selection_tolerance < _ZERO
        ):
            raise ValueError("CFG-BPS-001: delta tolerances must be non-negative.")
        if (
            self.short_delta_selection_tolerance >= Decimal("0.50")
            or self.long_delta_selection_tolerance >= Decimal("0.50")
        ):
            raise ValueError("CFG-BPS-001: delta tolerances must be below 0.50.")
        if self.minimum_open_interest < 0 or self.minimum_volume < 0:
            raise ValueError("CFG-BPS-001: liquidity floors must be non-negative.")
        if self.max_snapshot_age_seconds <= 0 or self.iv_rank_lookback_observations <= 0:
            raise ValueError("CFG-BPS-001: configured counts must be positive.")
        if self.minimum_dte < 0 or self.maximum_dte < self.minimum_dte:
            raise ValueError("CFG-BPS-009: invalid DTE bounds.")
        if self.contract_multiplier <= _ZERO:
            raise ValueError("CFG-BPS-001: contract_multiplier must be positive.")
        for width in (self.target_wing_width, self.minimum_wing_width, self.maximum_wing_width):
            if width is not None and width <= _ZERO:
                raise ValueError("CFG-BPS-001: wing widths must be positive when set.")
        if (
            self.minimum_wing_width is not None
            and self.maximum_wing_width is not None
            and self.maximum_wing_width < self.minimum_wing_width
        ):
            raise ValueError(
                "CFG-BPS-001: maximum_wing_width must be at least minimum_wing_width."
            )
        if self.minimum_liquidity_score is not None and not (
            _ZERO <= self.minimum_liquidity_score <= _ONE
        ):
            raise ValueError(
                "CFG-BPS-001: minimum_liquidity_score must be within [0, 1]."
            )
        if not isinstance(self.wing_selection_policy, WingSelectionPolicy):
            raise ValueError("CFG-BPS-001: invalid wing selection policy.")
        if not isinstance(self.premium_price_policy, PremiumPricePolicy):
            raise ValueError("CFG-BPS-001: invalid premium price policy.")
        if self.scoring_profile_name != "PREMIUM_SELLING":
            raise ValueError("CFG-BPS-014: only PREMIUM_SELLING is supported.")
        normalized = frozenset(
            item.strip().upper() for item in self.supported_underlyings if item.strip()
        )
        if not normalized:
            raise ValueError("CFG-BPS-007: supported_underlyings must not be empty.")
        object.__setattr__(self, "supported_underlyings", normalized)


def default_bull_put_spread_configuration() -> BullPutSpreadConfiguration:
    """Return the documented immutable v1.0 default policy."""
    return BullPutSpreadConfiguration()


@dataclass(frozen=True)
class BullPutSpreadStrikeSelection:
    """The selected same-expiry two-leg bear call credit spread."""

    underlying: str
    spot: Decimal
    expiry: date
    short_put_strike: Decimal
    long_put_strike: Decimal
    short_put_instrument_id: str
    long_put_instrument_id: str
    short_put_delta: Decimal
    long_put_delta: Decimal
    wing_width: Decimal
    dte: int


@dataclass(frozen=True)
class BullPutSpreadRiskMetrics:
    """Theoretical credit and finite defined-risk metrics."""

    net_credit: Decimal
    max_profit: Decimal
    max_loss: Decimal
    max_loss_label: str
    probability_of_profit: Decimal
    reward_risk_ratio: Decimal
    lower_breakeven: Decimal | None
    short_put_credit: Decimal
    long_put_debit: Decimal
    contract_multiplier: Decimal
    risk_statement: str = _RISK_STATEMENT


@dataclass(frozen=True)
class BullPutSpreadRecommendation:
    """Complete immutable recommendation, including abstentions and rejects."""

    recommendation_id: str
    state: EntryRecommendationState
    strategy_id: str
    as_of: datetime
    strike_selection: BullPutSpreadStrikeSelection | None
    risk_metrics: BullPutSpreadRiskMetrics | None
    strategy_score: StrategyScore | None
    confidence: ConfidenceReport | None
    explanation: StrategyExplanation | None
    reasons: tuple[str, ...]
    exit_window_hint: TimeWindow
    schema_version: str = _SCHEMA_VERSION

    def to_json(self) -> str:
        """Serialize this recommendation as canonical versioned JSON."""
        return to_json(self)

    @classmethod
    def from_json(cls, payload: str) -> BullPutSpreadRecommendation:
        """Deserialize a recommendation previously produced by :meth:`to_json`."""
        return from_json(payload)


@dataclass(frozen=True)
class BullPutSpreadEvaluationResult:
    """Sealed evaluation artifact used to map one recommendation and signal."""

    recommendation: BullPutSpreadRecommendation
    factor_bundle: FactorInputBundle | None
    signal: TradingSignal


def _decimal(value: object) -> Decimal:
    """Convert a finite boundary numeric value to Decimal."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("non-finite numeric value") from exc
    if not result.is_finite():
        raise ValueError("non-finite numeric value")
    return result


def _parse_expiry(value: str) -> date | None:
    """Parse the normalized market-snapshot expiry representation."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _instrument_id(contract: OptionContractSnapshot) -> str:
    """Return a stable instrument identity for ranking and sealing."""
    if contract.instrument_token is not None:
        return str(contract.instrument_token)
    return contract.tradingsymbol


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    """Clamp a decimal into an inclusive interval."""
    return max(lower, min(upper, value))


class BullPutSpreadStrategy(BaseStrategy):
    """Evaluate injected evidence for a defined-risk bull put credit spread."""

    def __init__(
        self,
        configuration: BullPutSpreadConfiguration,
        scoring_framework: StrategyScoringFramework,
        *,
        plugin_config: StrategyPluginConfig | None = None,
        event_sink: object | None = None,
    ) -> None:
        """Create a stateless strategy with immutable collaborators."""
        self._configuration = configuration
        self._scoring_framework = scoring_framework
        self._event_sink = event_sink
        super().__init__(plugin_config or _plugin_config_for(configuration))

    def evaluate(self, context: object) -> object:
        """Dispatch recommendation contexts; retain BaseStrategy generic behavior."""
        if isinstance(context, (StrategyContext, BullPutSpreadContext)):
            return self._evaluate_recommendation(context)
        return super().evaluate(context)  # type: ignore[arg-type]

    def evaluate_recommendation(
        self, context: StrategyContext | BullPutSpreadContext
    ) -> BullPutSpreadRecommendation:
        """Return the complete deterministic recommendation artifact."""
        return self._evaluate_recommendation(context)

    def evaluate_bull_put_spread(
        self, context: StrategyContext | BullPutSpreadContext
    ) -> BullPutSpreadRecommendation:
        """Alias :meth:`evaluate_recommendation` for explicit callers."""
        return self._evaluate_recommendation(context)

    def _execute(self, context: StrategyContext) -> TradingSignal:
        """Map the shared recommendation path into the canonical signal contract."""
        return self._evaluate_result(context).signal

    def _evaluate_recommendation(
        self, context: StrategyContext | BullPutSpreadContext
    ) -> BullPutSpreadRecommendation:
        """Evaluate the strategy without mutating shared state."""
        return self._evaluate_result(context).recommendation

    def _evaluate_result(
        self, supplied: StrategyContext | BullPutSpreadContext
    ) -> BullPutSpreadEvaluationResult:
        context, regime, event, trend, iv_rank = self._resolve(supplied)
        failure = self._validate_context(context)
        if failure:
            return self._failure(context, failure, EntryRecommendationState.REJECT)
        snapshot = context.snapshot
        if snapshot.freshness.age_seconds > self._configuration.max_snapshot_age_seconds:
            return self._failure(context, "BPS.SNAPSHOT.STALE", EntryRecommendationState.REJECT)
        local_time = (
            context.as_of.astimezone(ZoneInfo(self._configuration.entry_time_window.timezone))
            .timetz()
            .replace(tzinfo=None)
        )
        window = self._configuration.entry_time_window
        if not (window.start <= local_time < window.end):
            return self._failure(
                context, "BPS.TIME.OUTSIDE_ENTRY_WINDOW", EntryRecommendationState.ABSTAIN
            )
        if regime is None:
            return self._failure(context, "BPS.REGIME.MISSING", EntryRecommendationState.REJECT)
        tag = regime.tag.strip().upper()
        if tag == "HIGH_VOLATILITY_CRISIS":
            return self._failure(context, "BPS.REGIME.CRISIS", EntryRecommendationState.REJECT)
        if tag not in _PASS_REGIMES:
            return self._failure(context, "BPS.REGIME.UNSUITABLE", EntryRecommendationState.ABSTAIN)
        if event is not None and event.adverse:
            return self._failure(context, "BPS.EVENT.ADVERSE", EntryRecommendationState.ABSTAIN)
        trend_failure = self._evaluate_trend(trend)
        if trend_failure is not None:
            code, state = trend_failure
            return self._failure(context, code, state)
        if iv_rank is None and self._configuration.require_iv_rank:
            return self._failure(context, "BPS.IV_RANK.MISSING", EntryRecommendationState.REJECT)
        if iv_rank is not None and (not iv_rank.is_finite() or not _ZERO <= iv_rank <= _HUNDRED):
            return self._failure(context, "BPS.METRIC.NON_FINITE", EntryRecommendationState.REJECT)
        if iv_rank is not None and iv_rank < self._configuration.minimum_iv_rank:
            return self._failure(context, "BPS.IV_RANK.LOW", EntryRecommendationState.ABSTAIN)
        selection_data = self._select(snapshot, context.as_of.date())
        if isinstance(selection_data, str):
            reject_codes = {
                "BPS.CHAIN.INCOMPLETE",
                "BPS.CHAIN.MISSING",
                "BPS.GREEKS.MISSING",
                "BPS.STRUCTURE.INVALID_GEOMETRY",
            }
            state = (
                EntryRecommendationState.REJECT
                if selection_data in reject_codes
                else EntryRecommendationState.ABSTAIN
            )
            return self._failure(context, selection_data, state)
        short_put, long_put, expiry, spot, dte = selection_data
        selection = self._strike_selection(snapshot, spot, expiry, dte, short_put, long_put)
        metrics_or_code = self._risk_metrics(selection, short_put, long_put)
        if isinstance(metrics_or_code, str):
            state = (
                EntryRecommendationState.REJECT
                if metrics_or_code == "BPS.RISK.NON_POSITIVE_MAX_LOSS"
                else EntryRecommendationState.ABSTAIN
            )
            return self._failure(context, metrics_or_code, state)
        metrics = metrics_or_code
        trend_strength = (
            trend.strength if trend is not None and trend.strength is not None else _ZERO
        )
        bundle = self._factor_bundle(
            tag, iv_rank or _ZERO, trend_strength, short_put, long_put, selection, metrics, event
        )
        score = self._scoring_framework.score(
            ScoreRequest("bull_put_spread", bundle, self._configuration.scoring_profile_name)
        )
        reasons = ("BPS.GATES.PASS", "BPS.RISK.DEFINED", "BPS.STRUCTURE.TWO_LEGS")
        recommendation = BullPutSpreadRecommendation(
            self._recommendation_id(context, EntryRecommendationState.ENTER),
            EntryRecommendationState.ENTER,
            "bull_put_spread",
            context.as_of,
            selection,
            metrics,
            score,
            score.confidence,
            score.explanation,
            reasons,
            self._configuration.exit_time_window,
        )
        result = BullPutSpreadEvaluationResult(
            recommendation, bundle, self._signal(context, recommendation)
        )
        self._publish(result)
        return result

    @property
    def _short_target(self) -> Decimal:
        return abs(self._configuration.short_put_target_delta or self._configuration.short_target_delta)

    @property
    def _long_target(self) -> Decimal:
        return abs(self._configuration.long_put_target_delta or self._configuration.long_target_delta)

    def _evaluate_trend(
        self, trend: TrendEvidence | None
    ) -> tuple[str, EntryRecommendationState] | None:
        if trend is None:
            if self._configuration.require_trend_evidence:
                return "BPS.TREND.MISSING", EntryRecommendationState.REJECT
            return None
        direction = (trend.direction or "").strip().upper()
        if direction in _BEARISH_DIRECTIONS:
            return "BPS.TREND.STRONG_BEARISH", EntryRecommendationState.ABSTAIN
        if trend.strength is None:
            if self._configuration.require_trend_strength:
                return "BPS.TREND.MISSING", EntryRecommendationState.REJECT
            return None
        strength = trend.strength
        if not strength.is_finite() or not (_ZERO <= strength <= _ONE):
            return "BPS.METRIC.NON_FINITE", EntryRecommendationState.REJECT
        if direction == "" and strength >= self._configuration.maximum_bearish_trend_strength:
            return "BPS.TREND.STRONG_BEARISH", EntryRecommendationState.ABSTAIN
        return None

    def _resolve(
        self, supplied: StrategyContext | BullPutSpreadContext
    ) -> tuple[
        StrategyContext,
        MarketRegimeEvidence | None,
        EventRiskEvidence | None,
        TrendEvidence | None,
        Decimal | None,
    ]:
        if isinstance(supplied, BullPutSpreadContext):
            return (
                supplied.strategy_context,
                supplied.regime_evidence,
                supplied.event_risk_evidence,
                supplied.trend_evidence,
                supplied.iv_rank,
            )
        tags = supplied.tags
        regime_value = tags.get("regime_tag", "").strip()
        regime = (
            MarketRegimeEvidence(regime_value, supplied.as_of, "tags") if regime_value else None
        )
        event_text = tags.get("event_adverse", "false").strip().lower()
        event = (
            EventRiskEvidence(event_text == "true", supplied.as_of, "tags")
            if event_text in {"true", "false"}
            else None
        )
        direction = tags.get("trend_direction", "").strip() or None
        strength: Decimal | None
        if "trend_strength" in tags:
            try:
                strength = _decimal(tags["trend_strength"])
            except ValueError:
                strength = Decimal("NaN")
        else:
            strength = None
        trend = (
            TrendEvidence(supplied.as_of, direction, strength, "tags")
            if direction is not None or strength is not None
            else None
        )
        try:
            iv_rank = _decimal(tags["iv_rank"]) if "iv_rank" in tags else None
        except ValueError:
            iv_rank = Decimal("NaN")
        return supplied, regime, event, trend, iv_rank

    def _validate_context(self, context: StrategyContext) -> str | None:
        if context.snapshot is None:
            return "BPS.SNAPSHOT.MISSING"
        if not isinstance(context.snapshot, MarketSnapshot):
            return "BPS.CONTEXT.INVALID"
        if context.as_of.tzinfo is None or context.as_of.utcoffset() is None:
            return "BPS.CONTEXT.INVALID"
        snapshot = context.snapshot
        if self._configuration.require_valid_snapshot and (
            snapshot.quality.validation_status is not SnapshotValidationStatus.VALID
            or validate_market_snapshot(snapshot).validation_status
            is SnapshotValidationStatus.INVALID
        ):
            return "BPS.CONTEXT.INVALID"
        underlying = snapshot.option_chain.metadata.underlying.strip().upper()
        if underlying not in self._configuration.supported_underlyings:
            return "BPS.UNDERLYING.UNSUPPORTED"
        try:
            if _decimal(snapshot.underlying.last_price) <= _ZERO:
                return "BPS.CONTEXT.INVALID"
        except ValueError:
            return "BPS.CONTEXT.INVALID"
        return None

    def _select(
        self, snapshot: MarketSnapshot, observation_date: date
    ) -> tuple[OptionContractSnapshot, OptionContractSnapshot, date, Decimal, int] | str:
        contracts = snapshot.option_chain.contracts
        if not contracts:
            return "BPS.CHAIN.MISSING"
        try:
            spot = _decimal(snapshot.underlying.last_price)
        except ValueError:
            return "BPS.CHAIN.INCOMPLETE"
        groups: dict[tuple[str, date], list[OptionContractSnapshot]] = {}
        for contract in contracts:
            expiry = _parse_expiry(contract.expiry)
            if expiry is None:
                return "BPS.CHAIN.INCOMPLETE"
            dte = (expiry - observation_date).days
            if self._configuration.minimum_dte <= dte <= self._configuration.maximum_dte:
                groups.setdefault((contract.expiry, expiry), []).append(contract)
        missing_greeks = False
        for (_, expiry), group in sorted(
            groups.items(),
            key=lambda item: ((item[0][1] - observation_date).days, item[0][1], item[0][0]),
        ):
            puts = [item for item in group if item.option_type is OptionType.PE]
            if any(item.delta is None for item in puts) and self._configuration.require_greeks:
                missing_greeks = True
            shorts = self._short_candidates(puts, spot, self._short_target)
            if not shorts:
                continue
            short_put = shorts[0]
            longs = self._long_candidates(puts, spot, short_put, self._long_target)
            if not longs:
                continue
            long_put = longs[0]
            if _instrument_id(short_put) == _instrument_id(long_put):
                return "BPS.STRUCTURE.INVALID_GEOMETRY"
            short_strike = _decimal(short_put.strike)
            long_strike = _decimal(long_put.strike)
            if not (long_strike < short_strike):
                return "BPS.STRUCTURE.INVALID_GEOMETRY"
            if self._configuration.require_short_otm and not (short_strike < spot):
                continue
            if abs(_decimal(long_put.delta)) >= abs(_decimal(short_put.delta)):
                continue
            dte = (expiry - observation_date).days
            return short_put, long_put, expiry, spot, dte
        if missing_greeks:
            return "BPS.GREEKS.MISSING"
        if not groups:
            return "BPS.CHAIN.INCOMPLETE"
        return "BPS.STRIKE.NO_ELIGIBLE_SHORT"

    def _short_candidates(
        self,
        contracts: list[OptionContractSnapshot],
        spot: Decimal,
        target: Decimal,
    ) -> list[OptionContractSnapshot]:
        valid = [
            contract
            for contract in contracts
            if contract.option_type is OptionType.PE
            and (not self._configuration.require_short_otm or _decimal(contract.strike) < spot)
            and self._liquid(contract)
            and contract.delta is not None
            and abs(abs(_decimal(contract.delta)) - target)
            <= self._configuration.short_delta_selection_tolerance
        ]
        return sorted(valid, key=self._short_rank_key(target))

    def _long_candidates(
        self,
        contracts: list[OptionContractSnapshot],
        spot: Decimal,
        short_leg: OptionContractSnapshot,
        target: Decimal,
    ) -> list[OptionContractSnapshot]:
        short_strike = _decimal(short_leg.strike)
        valid: list[OptionContractSnapshot] = []
        for contract in contracts:
            if contract.option_type is not OptionType.PE or contract.delta is None:
                continue
            strike = _decimal(contract.strike)
            if strike >= short_strike:
                continue
            wing_width = short_strike - strike
            if wing_width <= _ZERO:
                continue
            if not self._wing_width_allowed(wing_width):
                continue
            if not self._liquid(contract):
                continue
            delta_error = abs(abs(_decimal(contract.delta)) - target)
            if (
                self._configuration.wing_selection_policy is WingSelectionPolicy.DELTA_TARGET
                and delta_error > self._configuration.long_delta_selection_tolerance
            ):
                continue
            if (
                self._configuration.wing_selection_policy is WingSelectionPolicy.WIDTH_THEN_DELTA
                and delta_error > self._configuration.long_delta_selection_tolerance
                and self._configuration.target_wing_width is None
            ):
                continue
            if self._configuration.wing_selection_policy is WingSelectionPolicy.FIXED_WIDTH:
                if self._configuration.target_wing_width is None:
                    continue
                if wing_width != self._configuration.target_wing_width:
                    continue
            valid.append(contract)
        return sorted(valid, key=self._long_rank_key(target, short_strike))

    def _wing_width_allowed(self, wing_width: Decimal) -> bool:
        if (
            self._configuration.minimum_wing_width is not None
            and wing_width < self._configuration.minimum_wing_width
        ):
            return False
        if (
            self._configuration.maximum_wing_width is not None
            and wing_width > self._configuration.maximum_wing_width
        ):
            return False
        return True

    def _short_rank_key(self, target: Decimal):
        def key(item: OptionContractSnapshot) -> tuple[Decimal, Decimal, int, int, Decimal, str]:
            return (
                abs(abs(_decimal(item.delta)) - target),
                self._relative_spread(item),
                -item.open_interest,
                -item.volume,
                _decimal(item.strike),
                _instrument_id(item),
            )

        return key

    def _long_rank_key(self, target: Decimal, short_strike: Decimal):
        def key(
            item: OptionContractSnapshot,
        ) -> tuple[Decimal, Decimal, Decimal, int, int, Decimal, str]:
            strike = _decimal(item.strike)
            wing_width = short_strike - strike
            width_error = (
                abs(wing_width - self._configuration.target_wing_width)
                if self._configuration.target_wing_width is not None
                else _ZERO
            )
            if self._configuration.wing_selection_policy is WingSelectionPolicy.FIXED_WIDTH:
                delta_term = _ZERO
            else:
                delta_term = abs(abs(_decimal(item.delta)) - target)
            return (
                delta_term,
                width_error,
                self._relative_spread(item),
                -item.open_interest,
                -item.volume,
                strike,
                _instrument_id(item),
            )

        return key

    def _liquid(self, contract: OptionContractSnapshot) -> bool:
        try:
            bid, ask = _decimal(contract.bid), _decimal(contract.ask)
            midpoint = (bid + ask) / Decimal("2")
            if bid < _ZERO or ask <= _ZERO or ask < bid or midpoint <= _ZERO:
                return False
            width = ask - bid
            if (
                self._configuration.maximum_spread_width is not None
                and width > self._configuration.maximum_spread_width
            ):
                return False
            if width / midpoint > self._configuration.maximum_relative_spread_width:
                return False
            if (
                self._configuration.require_open_interest
                and contract.open_interest < self._configuration.minimum_open_interest
            ):
                return False
            if (
                self._configuration.require_volume
                and contract.volume < self._configuration.minimum_volume
            ):
                return False
            return True
        except ValueError:
            return False

    def _relative_spread(self, contract: OptionContractSnapshot) -> Decimal:
        bid, ask = _decimal(contract.bid), _decimal(contract.ask)
        return (ask - bid) / ((bid + ask) / Decimal("2"))

    def _price_short(self, contract: OptionContractSnapshot) -> Decimal:
        bid, ask = _decimal(contract.bid), _decimal(contract.ask)
        policy = self._configuration.premium_price_policy
        if policy is PremiumPricePolicy.CONSERVATIVE:
            return bid
        if policy is PremiumPricePolicy.ASK_CREDIT:
            return ask
        return (bid + ask) / Decimal("2")

    def _price_long(self, contract: OptionContractSnapshot) -> Decimal:
        bid, ask = _decimal(contract.bid), _decimal(contract.ask)
        policy = self._configuration.premium_price_policy
        if policy is PremiumPricePolicy.CONSERVATIVE:
            return ask
        if policy is PremiumPricePolicy.ASK_CREDIT:
            return bid
        return (bid + ask) / Decimal("2")

    def _strike_selection(
        self,
        snapshot: MarketSnapshot,
        spot: Decimal,
        expiry: date,
        dte: int,
        short_put: OptionContractSnapshot,
        long_put: OptionContractSnapshot,
    ) -> BullPutSpreadStrikeSelection:
        short_strike = _decimal(short_put.strike)
        long_strike = _decimal(long_put.strike)
        return BullPutSpreadStrikeSelection(
            snapshot.option_chain.metadata.underlying.strip().upper(),
            spot,
            expiry,
            short_strike,
            long_strike,
            _instrument_id(short_put),
            _instrument_id(long_put),
            _decimal(short_put.delta),
            _decimal(long_put.delta),
            short_strike - long_strike,
            dte,
        )

    def _risk_metrics(
        self,
        selection: BullPutSpreadStrikeSelection,
        short_put: OptionContractSnapshot,
        long_put: OptionContractSnapshot,
    ) -> BullPutSpreadRiskMetrics | str:
        short_credit = self._price_short(short_put)
        long_debit = self._price_long(long_put)
        net_credit = short_credit - long_debit
        if net_credit <= _ZERO:
            return "BPS.PREMIUM.BELOW_MINIMUM"
        if net_credit < self._configuration.minimum_premium:
            return "BPS.PREMIUM.BELOW_MINIMUM"
        wing = selection.wing_width
        if wing <= net_credit:
            return "BPS.RISK.NON_POSITIVE_MAX_LOSS"
        multiplier = self._configuration.contract_multiplier
        max_profit = net_credit * multiplier
        max_loss = (wing - net_credit) * multiplier
        if max_loss <= _ZERO:
            return "BPS.RISK.NON_POSITIVE_MAX_LOSS"
        short_otm = _clamp(_ONE - abs(selection.short_put_delta), _ZERO, _ONE)
        credit_adj = min(net_credit / max(selection.spot, _EPSILON), Decimal("0.05"))
        defined_adj = min(net_credit / max(max_loss / multiplier, _EPSILON), Decimal("0.05"))
        pop = _clamp(short_otm + _HALF * credit_adj + _HALF * defined_adj, _ZERO, _ONE)
        return BullPutSpreadRiskMetrics(
            net_credit,
            max_profit,
            max_loss,
            "DEFINED_RISK",
            pop,
            max_profit / max_loss,
            selection.short_put_strike - net_credit,
            short_credit,
            long_debit,
            multiplier,
        )

    def _factor_bundle(
        self,
        tag: str,
        iv_rank: Decimal,
        trend_strength: Decimal,
        short_put: OptionContractSnapshot,
        long_put: OptionContractSnapshot,
        selection: BullPutSpreadStrikeSelection,
        metrics: BullPutSpreadRiskMetrics,
        event: EventRiskEvidence | None,
    ) -> FactorInputBundle:
        spreads = (
            self._relative_spread(short_put) + self._relative_spread(long_put)
        ) / Decimal("2")
        liquidity = max(_ZERO, _HUNDRED * (_ONE - spreads))
        short_error = abs(abs(selection.short_put_delta) - self._short_target)
        long_error = abs(abs(selection.long_put_delta) - self._long_target)
        greek_quality = max(
            _ZERO, _HUNDRED * (_ONE - (short_error + long_error) / Decimal("0.50"))
        )
        trend_score = max(_ZERO, _HUNDRED * (_ONE - trend_strength))
        risk_reward = min(Decimal("5"), metrics.reward_risk_ratio)
        regime_label = {
            "TRENDING_UP": "TRENDING_UP",
            "TRENDING_DOWN": "TRENDING_DOWN",
            "RANGE_BOUND": "RANGE_BOUND",
            "BULLISH": "TRENDING_UP",
            "MEAN_REVERTING": "RANGE_BOUND",
        }.get(tag, "RANGE_BOUND")
        return FactorInputBundle(
            (
                FactorInput(
                    FactorCategory.MARKET_REGIME,
                    "regime",
                    regime_label,
                    RawValueKind.LABEL,
                    "regime:" + tag,
                ),
                FactorInput(
                    FactorCategory.TREND_ALIGNMENT,
                    "trend_alignment",
                    float(trend_score),
                    RawValueKind.SCORE_0_100,
                    "trend:" + str(trend_strength),
                ),
                FactorInput(
                    FactorCategory.VOLATILITY,
                    "iv_rank",
                    float(iv_rank),
                    RawValueKind.SCORE_0_100,
                    "context:iv_rank",
                ),
                FactorInput(
                    FactorCategory.LIQUIDITY,
                    "selected_leg_liquidity",
                    float(liquidity),
                    RawValueKind.SCORE_0_100,
                    "snapshot:quotes",
                ),
                FactorInput(
                    FactorCategory.GREEKS,
                    "delta_proximity",
                    float(greek_quality),
                    RawValueKind.SCORE_0_100,
                    "snapshot:greeks",
                ),
                FactorInput(
                    FactorCategory.RISK_REWARD,
                    "defined_reward_risk",
                    float(risk_reward),
                    RawValueKind.RATIO,
                    "derived:defined_risk",
                ),
                FactorInput(
                    FactorCategory.EVENT_RISK,
                    "adverse_event",
                    bool(event and event.adverse),
                    RawValueKind.BOOLEAN,
                    "event:" + (event.provenance if event else "none"),
                ),
            )
        )

    def _failure(
        self, context: StrategyContext, code: str, state: EntryRecommendationState
    ) -> BullPutSpreadEvaluationResult:
        recommendation = BullPutSpreadRecommendation(
            self._recommendation_id(context, state),
            state,
            "bull_put_spread",
            context.as_of,
            None,
            None,
            None,
            None,
            None,
            (code,),
            self._configuration.exit_time_window,
        )
        result = BullPutSpreadEvaluationResult(
            recommendation, None, self._signal(context, recommendation)
        )
        self._publish(result)
        return result

    def _signal(
        self, context: StrategyContext, recommendation: BullPutSpreadRecommendation
    ) -> TradingSignal:
        entered = recommendation.state is EntryRecommendationState.ENTER
        score = (
            recommendation.strategy_score.overall_score if recommendation.strategy_score else 0.0
        )
        if entered:
            action = SignalAction.EVALUATE
        elif recommendation.state is EntryRecommendationState.REJECT:
            action = SignalAction.NO_TRADE
        else:
            action = SignalAction.ABSTAIN
        selection = recommendation.strike_selection
        metadata: dict[str, str] = {}
        if entered and selection is not None and recommendation.risk_metrics is not None:
            metadata = {
                "risk_warning": "DEFINED_RISK",
                "max_loss_label": "DEFINED_RISK",
                "leg0_instrument_id": selection.short_put_instrument_id,
                "leg1_instrument_id": selection.long_put_instrument_id,
                "leg0_side": "SELL",
                "leg1_side": "BUY",
                "wing_width": str(selection.wing_width),
                "net_credit": str(recommendation.risk_metrics.net_credit),
                "max_loss": str(recommendation.risk_metrics.max_loss),
            }
        if isinstance(context.snapshot, MarketSnapshot):
            market = market_context_from_snapshot(context.snapshot)
        else:
            market = SignalMarketContext(
                snapshot_id="missing-snapshot",
                underlying="UNKNOWN",
            )
        return TradingSignal(
            signal_id=recommendation.recommendation_id,
            as_of=context.as_of,
            action=action,
            direction=SignalDirection.BULLISH if entered else SignalDirection.UNKNOWN,
            strategy_id=self.metadata.strategy_id,
            strategy_version=self.metadata.version,
            strategy_family=self.metadata.strategy_family,
            confidence=SignalConfidence(
                float(score), confidence_band_for_score(float(score)), "bull_put_spread"
            ),
            market=market,
            reasons=recommendation.reasons,
            structure_hint=(
                StructureHint(
                    "bull_put_spread",
                    2,
                    "delta_ranked_put_vertical",
                    float(self._configuration.short_target_delta),
                    1,
                    (OptionType.PE, OptionType.PE),
                )
                if entered
                else None
            ),
            risk=(
                SignalRiskMetadata(
                    RiskProfileHint.DEFINED,
                    "DEFINED_RISK",
                    notes=_RISK_STATEMENT,
                )
                if entered
                else None
            ),
            metadata=MappingProxyType(metadata),
        )

    def _publish(self, result: BullPutSpreadEvaluationResult) -> None:
        sink = self._event_sink
        if sink is None:
            return
        topic = {
            EntryRecommendationState.ENTER: "strategy.bull_put_spread.entered_candidate",
            EntryRecommendationState.ABSTAIN: "strategy.bull_put_spread.abstained",
            EntryRecommendationState.REJECT: "strategy.bull_put_spread.rejected",
        }[result.recommendation.state]
        try:
            publish = getattr(sink, "publish", None)
            if callable(publish):
                publish(topic, result.recommendation)
        except Exception:
            return

    @staticmethod
    def _recommendation_id(
        context: StrategyContext, state: EntryRecommendationState
    ) -> str:
        snapshot_id = (
            context.snapshot.provenance.snapshot_id
            if isinstance(context.snapshot, MarketSnapshot)
            else "missing-snapshot"
        )
        material = "|".join(
            (
                context.correlation_id,
                snapshot_id,
                context.as_of.isoformat(),
                state.value,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _plugin_config_for(configuration: BullPutSpreadConfiguration) -> StrategyPluginConfig:
    """Build immutable BaseStrategy metadata from bull-put-spread policy."""
    return StrategyPluginConfig(
        StrategyMetadata(
            strategy_id="bull_put_spread",
            display_name="Bull Put Spread",
            version="1.0.0",
            strategy_family=StrategyFamily.BULL_PUT_SPREAD,
            category="directional",
            supported_underlyings=tuple(sorted(configuration.supported_underlyings)),
            requires_volatility_snapshot=True,
            min_contracts_required=2,
            risk_profile_hint=StrategyRiskProfileHint.DEFINED,
        ),
        require_valid_snapshot=configuration.require_valid_snapshot,
    )


def to_json(recommendation: BullPutSpreadRecommendation) -> str:
    """Serialize a recommendation using canonical JSON and string Decimals."""

    def encode(value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, TimeWindow):
            return {
                "start": value.start.isoformat(),
                "end": value.end.isoformat(),
                "timezone": value.timezone,
            }
        if hasattr(value, "__dataclass_fields__"):
            return {key: encode(getattr(value, key)) for key in value.__dataclass_fields__}
        if isinstance(value, tuple):
            return [encode(item) for item in value]
        return value

    payload = encode(recommendation)
    payload["schema_version"] = _SCHEMA_VERSION
    if recommendation.strategy_score is not None:
        payload["strategy_score"] = json.loads(
            StrategyScoringFramework(default_scoring_framework_config()).serialize(
                recommendation.strategy_score
            )
        )
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def from_json(payload: str) -> BullPutSpreadRecommendation:
    """Deserialize and validate a canonical recommendation JSON document."""
    try:
        raw = json.loads(payload)
        if raw["schema_version"] != _SCHEMA_VERSION:
            raise ValueError("unsupported schema")
        selection_raw = raw.get("strike_selection")
        metrics_raw = raw.get("risk_metrics")
        selection = (
            BullPutSpreadStrikeSelection(
                selection_raw["underlying"],
                _decimal(selection_raw["spot"]),
                date.fromisoformat(selection_raw["expiry"]),
                _decimal(selection_raw["short_put_strike"]),
                _decimal(selection_raw["long_put_strike"]),
                selection_raw["short_put_instrument_id"],
                selection_raw["long_put_instrument_id"],
                _decimal(selection_raw["short_put_delta"]),
                _decimal(selection_raw["long_put_delta"]),
                _decimal(selection_raw["wing_width"]),
                int(selection_raw["dte"]),
            )
            if selection_raw
            else None
        )
        metrics = (
            BullPutSpreadRiskMetrics(
                _decimal(metrics_raw["net_credit"]),
                _decimal(metrics_raw["max_profit"]),
                _decimal(metrics_raw["max_loss"]),
                metrics_raw["max_loss_label"],
                _decimal(metrics_raw["probability_of_profit"]),
                _decimal(metrics_raw["reward_risk_ratio"]),
                _decimal(metrics_raw["lower_breakeven"])
                if metrics_raw.get("lower_breakeven") is not None
                else None,
                _decimal(metrics_raw["short_put_credit"]),
                _decimal(metrics_raw["long_put_debit"]),
                _decimal(metrics_raw["contract_multiplier"]),
                metrics_raw.get("risk_statement", _RISK_STATEMENT),
            )
            if metrics_raw
            else None
        )
        score = (
            StrategyScoringFramework(default_scoring_framework_config()).deserialize(
                json.dumps(raw["strategy_score"])
            )
            if raw.get("strategy_score")
            else None
        )
        window = raw["exit_window_hint"]
        return BullPutSpreadRecommendation(
            str(raw["recommendation_id"]),
            EntryRecommendationState(raw["state"]),
            str(raw["strategy_id"]),
            datetime.fromisoformat(raw["as_of"]).astimezone(timezone.utc),
            selection,
            metrics,
            score,
            score.confidence if score else None,
            score.explanation if score else None,
            tuple(raw["reasons"]),
            TimeWindow(
                time.fromisoformat(window["start"]),
                time.fromisoformat(window["end"]),
                window["timezone"],
            ),
            str(raw.get("schema_version", _SCHEMA_VERSION)),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("BPS.SERIALIZATION.INVALID") from exc


__all__ = [
    "BullPutSpreadConfiguration",
    "BullPutSpreadContext",
    "BullPutSpreadEvaluationResult",
    "BullPutSpreadRecommendation",
    "BullPutSpreadRiskMetrics",
    "BullPutSpreadStrategy",
    "BullPutSpreadStrikeSelection",
    "EntryRecommendationState",
    "EventRiskEvidence",
    "MarketRegimeEvidence",
    "PremiumPricePolicy",
    "TimeWindow",
    "TrendEvidence",
    "WingSelectionPolicy",
    "default_bull_put_spread_configuration",
    "from_json",
    "to_json",
]
