"""Deterministic short-strangle strategy recommendation plugin.

The module evaluates immutable market and evidence snapshots only.  It does not
fetch market data, manage risk, submit orders, or otherwise perform I/O.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from core.engine_context import EngineContext
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
    SignalRiskMetadata,
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
_RISK_WARNING = "UNDEFINED_UNLIMITED: naked short strangle maximum loss is not finite."


class PremiumPricePolicy(str, Enum):
    """Quote side used for the theoretical received premium."""

    MID = "MID"
    ASK = "ASK"


class EntryRecommendationState(str, Enum):
    """Outcome of the short-strangle entry evaluation."""

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
            raise ValueError("CFG-SSS-010: TimeWindow must be a same-day increasing interval.")
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError("CFG-SSS-010: TimeWindow timezone is invalid.") from exc


@dataclass(frozen=True)
class ShortStrangleContext:
    """Typed evidence extension without changing ``StrategyContext.tags``."""

    strategy_context: StrategyContext
    regime_evidence: MarketRegimeEvidence
    event_risk_evidence: EventRiskEvidence
    iv_rank: Decimal | None = None


@dataclass(frozen=True)
class ShortStrangleConfiguration:
    """Immutable short-strangle suitability and selection policy."""

    target_delta: Decimal = Decimal("0.16")
    call_target_delta: Decimal | None = None
    put_target_delta: Decimal | None = None
    minimum_iv_rank: Decimal = Decimal("50")
    maximum_spread_width: Decimal | None = None
    maximum_relative_spread_width: Decimal = Decimal("0.15")
    minimum_premium: Decimal = Decimal("0")
    minimum_open_interest: int = 1
    minimum_volume: int = 1
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
    delta_selection_tolerance: Decimal = Decimal("0.03")
    premium_price_policy: PremiumPricePolicy = PremiumPricePolicy.MID
    minimum_dte: int = 0
    maximum_dte: int = 45
    require_iv_rank: bool = True
    require_greeks: bool = True
    require_open_interest: bool = True
    require_volume: bool = True
    iv_rank_lookback_observations: int = 252

    def __post_init__(self) -> None:
        """Validate and normalize configuration independent of market inputs."""
        decimals = (
            self.target_delta, self.call_target_delta, self.put_target_delta,
            self.minimum_iv_rank, self.maximum_spread_width,
            self.maximum_relative_spread_width, self.minimum_premium,
            self.delta_selection_tolerance,
        )
        if any(value is not None and not value.is_finite() for value in decimals):
            raise ValueError("CFG-SSS-001: Decimal configuration must be finite.")
        targets = (self.target_delta, self.call_target_delta or self.target_delta,
                   self.put_target_delta or self.target_delta)
        if any(not (_ZERO < abs(value) < Decimal("0.50")) for value in targets):
            raise ValueError("CFG-SSS-004: target deltas must be within (0, 0.50).")
        if not (_ZERO <= self.minimum_iv_rank <= _HUNDRED):
            raise ValueError("CFG-SSS-001: minimum_iv_rank must be within [0, 100].")
        if self.maximum_spread_width is not None and self.maximum_spread_width <= _ZERO:
            raise ValueError("CFG-SSS-009: maximum_spread_width must be positive.")
        if not (_ZERO < self.maximum_relative_spread_width <= _ONE):
            raise ValueError("CFG-SSS-001: maximum_relative_spread_width must be within (0, 1].")
        if self.minimum_premium < _ZERO or self.delta_selection_tolerance < _ZERO:
            raise ValueError("CFG-SSS-001: premium and tolerance must be non-negative.")
        if self.delta_selection_tolerance >= Decimal("0.50"):
            raise ValueError("CFG-SSS-001: delta_selection_tolerance must be below 0.50.")
        if self.minimum_open_interest < 0 or self.minimum_volume < 0:
            raise ValueError("CFG-SSS-001: liquidity floors must be non-negative.")
        if self.max_snapshot_age_seconds <= 0 or self.iv_rank_lookback_observations <= 0:
            raise ValueError("CFG-SSS-001: configured counts must be positive.")
        if self.minimum_dte < 0 or self.maximum_dte < self.minimum_dte:
            raise ValueError("CFG-SSS-008: invalid DTE bounds.")
        normalized = frozenset(item.strip().upper() for item in self.supported_underlyings if item.strip())
        if not normalized:
            raise ValueError("CFG-SSS-006: supported_underlyings must not be empty.")
        if self.scoring_profile_name != "PREMIUM_SELLING":
            raise ValueError("CFG-SSS-001: only PREMIUM_SELLING is supported.")
        if not isinstance(self.premium_price_policy, PremiumPricePolicy):
            raise ValueError("CFG-SSS-001: invalid premium price policy.")
        object.__setattr__(self, "supported_underlyings", normalized)


def default_short_strangle_configuration() -> ShortStrangleConfiguration:
    """Return the documented immutable v1.0 default policy."""
    return ShortStrangleConfiguration()


@dataclass(frozen=True)
class ShortStrangleStrikeSelection:
    """The selected same-expiry OTM option legs."""

    underlying: str
    spot: Decimal
    expiry: date
    call_strike: Decimal
    put_strike: Decimal
    call_symbol: str | None
    put_symbol: str | None
    call_token: int | str | None
    put_token: int | str | None
    call_delta: Decimal
    put_delta: Decimal
    call_delta_error: Decimal
    put_delta_error: Decimal


@dataclass(frozen=True)
class ShortStrangleRiskMetrics:
    """Theoretical credit and explicitly undefined naked-risk metrics."""

    call_credit: Decimal
    put_credit: Decimal
    net_credit: Decimal
    max_profit: Decimal
    max_loss: None = None
    max_loss_label: str = "UNDEFINED_UNLIMITED"
    probability_of_profit: Decimal = _ZERO
    call_otm_probability: Decimal = _ZERO
    put_otm_probability: Decimal = _ZERO
    contract_multiplier: Decimal = _ONE
    risk_warning: str = _RISK_WARNING


@dataclass(frozen=True)
class ShortStrangleRecommendation:
    """Complete immutable recommendation, including abstentions and rejects."""

    recommendation_id: str
    state: EntryRecommendationState
    strategy_id: str
    observed_at: datetime
    selection: ShortStrangleStrikeSelection | None
    risk_metrics: ShortStrangleRiskMetrics | None
    score: StrategyScore | None
    confidence: ConfidenceReport | None
    explanation: StrategyExplanation | None
    exit_window_hint: TimeWindow
    reasons: tuple[str, ...]

    def to_json(self) -> str:
        """Serialize this recommendation as canonical versioned JSON."""
        return to_json(self)

    @classmethod
    def from_json(cls, payload: str) -> ShortStrangleRecommendation:
        """Deserialize a recommendation previously produced by :meth:`to_json`."""
        return from_json(payload)


@dataclass(frozen=True)
class ShortStrangleEvaluationResult:
    """Private sealed evaluation artifact used to map one recommendation."""

    recommendation: ShortStrangleRecommendation
    factor_bundle: FactorInputBundle | None
    signal: TradingSignal


def _decimal(value: object) -> Decimal:
    """Convert a finite boundary numeric value to Decimal."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
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


class ShortStrangleStrategy(BaseStrategy):
    """Evaluate injected evidence for a naked short-strangle candidate."""

    def __init__(
        self,
        configuration: ShortStrangleConfiguration,
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
        if isinstance(context, (StrategyContext, ShortStrangleContext)):
            return self._evaluate_recommendation(context)
        return super().evaluate(context)  # type: ignore[arg-type]

    def evaluate_recommendation(
        self, context: StrategyContext | ShortStrangleContext
    ) -> ShortStrangleRecommendation:
        """Return the complete deterministic recommendation artifact."""
        return self._evaluate_recommendation(context)

    def evaluate_short_strangle(
        self, context: StrategyContext | ShortStrangleContext
    ) -> ShortStrangleRecommendation:
        """Alias :meth:`evaluate_recommendation` for explicit callers."""
        return self._evaluate_recommendation(context)

    def _execute(self, context: StrategyContext) -> TradingSignal:
        """Map the shared recommendation path into the canonical signal contract."""
        return self._evaluate_result(context).signal

    def _evaluate_recommendation(
        self, context: StrategyContext | ShortStrangleContext
    ) -> ShortStrangleRecommendation:
        """Evaluate the strategy without mutating shared state."""
        return self._evaluate_result(context).recommendation

    def _evaluate_result(
        self, supplied: StrategyContext | ShortStrangleContext
    ) -> ShortStrangleEvaluationResult:
        context, regime, event, iv_rank = self._resolve(supplied)
        failure = self._validate_context(context)
        if failure:
            return self._failure(context, failure, EntryRecommendationState.REJECT)
        snapshot = context.snapshot
        if snapshot.freshness.age_seconds > self._configuration.max_snapshot_age_seconds:
            return self._failure(context, "SSS.SNAPSHOT.STALE", EntryRecommendationState.REJECT)
        local_time = context.as_of.astimezone(ZoneInfo(self._configuration.entry_time_window.timezone)).timetz().replace(tzinfo=None)
        window = self._configuration.entry_time_window
        if not (window.start <= local_time < window.end):
            return self._failure(context, "SSS.TIME.OUTSIDE_ENTRY_WINDOW", EntryRecommendationState.ABSTAIN)
        if regime is None:
            return self._failure(context, "SSS.REGIME.MISSING", EntryRecommendationState.REJECT)
        tag = regime.tag.strip().upper()
        if tag == "HIGH_VOLATILITY_CRISIS":
            return self._failure(context, "SSS.REGIME.CRISIS", EntryRecommendationState.REJECT)
        if tag not in {"RANGE_BOUND", "MEAN_REVERTING"}:
            return self._failure(context, "SSS.REGIME.UNSUITABLE", EntryRecommendationState.ABSTAIN)
        if event is not None and event.adverse:
            return self._failure(context, "SSS.EVENT.ADVERSE", EntryRecommendationState.ABSTAIN)
        if iv_rank is None and self._configuration.require_iv_rank:
            return self._failure(context, "SSS.IV_RANK.MISSING", EntryRecommendationState.REJECT)
        if iv_rank is not None and (not iv_rank.is_finite() or not _ZERO <= iv_rank <= _HUNDRED):
            return self._failure(context, "SSS.METRIC.NON_FINITE", EntryRecommendationState.REJECT)
        if iv_rank is not None and iv_rank < self._configuration.minimum_iv_rank:
            return self._failure(context, "SSS.IV_RANK.LOW", EntryRecommendationState.ABSTAIN)
        selection_data = self._select(snapshot, context.as_of.date())
        if isinstance(selection_data, str):
            return self._failure(
                context, selection_data,
                EntryRecommendationState.REJECT if selection_data in {"SSS.CHAIN.INCOMPLETE", "SSS.GREEKS.MISSING"} else EntryRecommendationState.ABSTAIN,
            )
        call, put, expiry, spot = selection_data
        call_delta, put_delta = _decimal(call.delta), _decimal(put.delta)
        selection = ShortStrangleStrikeSelection(
            snapshot.option_chain.metadata.underlying.strip().upper(), spot, expiry,
            _decimal(call.strike), _decimal(put.strike), call.tradingsymbol, put.tradingsymbol,
            call.instrument_token, put.instrument_token, call_delta, put_delta,
            abs(abs(call_delta) - self._call_target), abs(abs(put_delta) - self._put_target),
        )
        call_credit, put_credit = self._credit(call), self._credit(put)
        net_credit = call_credit + put_credit
        if net_credit < self._configuration.minimum_premium:
            return self._failure(context, "SSS.PREMIUM.BELOW_MINIMUM", EntryRecommendationState.ABSTAIN)
        call_probability, put_probability = _ONE - abs(call_delta), _ONE - abs(put_delta)
        pop = max(_ZERO, call_probability + put_probability - _ONE)
        pop = min(_ONE, pop + min(net_credit / max(spot, Decimal("0.00000001")), Decimal("0.05")))
        multiplier = _decimal(call.lot_size)
        metrics = ShortStrangleRiskMetrics(
            call_credit, put_credit, net_credit, net_credit * multiplier,
            probability_of_profit=pop, call_otm_probability=call_probability,
            put_otm_probability=put_probability, contract_multiplier=multiplier,
        )
        bundle = self._factor_bundle(tag, iv_rank or _ZERO, call, put, selection, metrics, event)
        score = self._scoring_framework.score(
            ScoreRequest("short_strangle", bundle, self._configuration.scoring_profile_name)
        )
        reasons = ("SSS.GATES.PASS", "SSS.RISK.UNDEFINED_UNLIMITED")
        recommendation = ShortStrangleRecommendation(
            self._recommendation_id(context, EntryRecommendationState.ENTER),
            EntryRecommendationState.ENTER, "short_strangle", context.as_of, selection, metrics,
            score, score.confidence, score.explanation, self._configuration.exit_time_window, reasons,
        )
        return ShortStrangleEvaluationResult(recommendation, bundle, self._signal(context, recommendation))

    @property
    def _call_target(self) -> Decimal:
        return abs(self._configuration.call_target_delta or self._configuration.target_delta)

    @property
    def _put_target(self) -> Decimal:
        return abs(self._configuration.put_target_delta or self._configuration.target_delta)

    def _resolve(self, supplied: StrategyContext | ShortStrangleContext) -> tuple[StrategyContext, MarketRegimeEvidence | None, EventRiskEvidence | None, Decimal | None]:
        if isinstance(supplied, ShortStrangleContext):
            return supplied.strategy_context, supplied.regime_evidence, supplied.event_risk_evidence, supplied.iv_rank
        tags = supplied.tags
        regime_value = tags.get("regime_tag", "").strip()
        regime = MarketRegimeEvidence(regime_value, supplied.as_of, "tags") if regime_value else None
        event_text = tags.get("event_adverse", "false").strip().lower()
        event = EventRiskEvidence(event_text == "true", supplied.as_of, "tags") if event_text in {"true", "false"} else None
        try:
            iv_rank = _decimal(tags["iv_rank"]) if "iv_rank" in tags else None
        except ValueError:
            iv_rank = Decimal("NaN")
        return supplied, regime, event, iv_rank

    def _validate_context(self, context: StrategyContext) -> str | None:
        if context.snapshot is None:
            return "SSS.SNAPSHOT.MISSING"
        if not isinstance(context.snapshot, MarketSnapshot):
            return "SSS.SNAPSHOT.INVALID"
        if context.as_of.tzinfo is None or context.as_of.utcoffset() is None:
            return "SSS.SNAPSHOT.INVALID"
        snapshot = context.snapshot
        if self._configuration.require_valid_snapshot and (
            snapshot.quality.validation_status is not SnapshotValidationStatus.VALID
            or validate_market_snapshot(snapshot).validation_status is SnapshotValidationStatus.INVALID
        ):
            return "SSS.SNAPSHOT.INVALID"
        underlying = snapshot.option_chain.metadata.underlying.strip().upper()
        if underlying not in self._configuration.supported_underlyings:
            return "SSS.UNDERLYING.UNSUPPORTED"
        try:
            if _decimal(snapshot.underlying.last_price) <= _ZERO:
                return "SSS.SPOT.INVALID"
        except ValueError:
            return "SSS.SPOT.INVALID"
        return None

    def _select(self, snapshot: MarketSnapshot, observation_date: date) -> tuple[OptionContractSnapshot, OptionContractSnapshot, date, Decimal] | str:
        contracts = snapshot.option_chain.contracts
        if not contracts:
            return "SSS.CHAIN.MISSING"
        spot = _decimal(snapshot.underlying.last_price)
        groups: dict[tuple[str, date], list[OptionContractSnapshot]] = {}
        for contract in contracts:
            expiry = _parse_expiry(contract.expiry)
            if expiry is None:
                return "SSS.CHAIN.INCOMPLETE"
            dte = (expiry - observation_date).days
            if self._configuration.minimum_dte <= dte <= self._configuration.maximum_dte:
                groups.setdefault((contract.expiry, expiry), []).append(contract)
        for (_, expiry), group in sorted(groups.items(), key=lambda item: ((item[0][1] - observation_date).days, item[0][1], item[0][0])):
            calls = self._candidates(group, OptionType.CE, spot, self._call_target)
            puts = self._candidates(group, OptionType.PE, spot, self._put_target)
            if calls and puts:
                return calls[0], puts[0], expiry, spot
        if any(contract.delta is None for contract in contracts) and self._configuration.require_greeks:
            return "SSS.GREEKS.MISSING"
        return "SSS.STRIKE.NO_ELIGIBLE_CANDIDATE"

    def _candidates(self, contracts: list[OptionContractSnapshot], side: OptionType, spot: Decimal, target: Decimal) -> list[OptionContractSnapshot]:
        valid = [
            contract for contract in contracts
            if contract.option_type is side
            and ((_decimal(contract.strike) > spot) if side is OptionType.CE else (_decimal(contract.strike) < spot))
            and self._liquid(contract)
            and contract.delta is not None
            and abs(abs(_decimal(contract.delta)) - target) <= self._configuration.delta_selection_tolerance
        ]
        return sorted(valid, key=lambda item: (
            abs(abs(_decimal(item.delta)) - target), self._relative_spread(item),
            -item.open_interest, -item.volume, _decimal(item.strike), item.tradingsymbol,
        ))

    def _liquid(self, contract: OptionContractSnapshot) -> bool:
        try:
            bid, ask = _decimal(contract.bid), _decimal(contract.ask)
            midpoint = (bid + ask) / Decimal("2")
            if bid < _ZERO or ask <= _ZERO or ask < bid or midpoint <= _ZERO:
                return False
            width = ask - bid
            if self._configuration.maximum_spread_width is not None and width > self._configuration.maximum_spread_width:
                return False
            if width / midpoint > self._configuration.maximum_relative_spread_width:
                return False
            return ((not self._configuration.require_open_interest or contract.open_interest >= self._configuration.minimum_open_interest)
                    and (not self._configuration.require_volume or contract.volume >= self._configuration.minimum_volume))
        except ValueError:
            return False

    def _relative_spread(self, contract: OptionContractSnapshot) -> Decimal:
        bid, ask = _decimal(contract.bid), _decimal(contract.ask)
        return (ask - bid) / ((bid + ask) / Decimal("2"))

    def _credit(self, contract: OptionContractSnapshot) -> Decimal:
        bid, ask = _decimal(contract.bid), _decimal(contract.ask)
        return ask if self._configuration.premium_price_policy is PremiumPricePolicy.ASK else (bid + ask) / Decimal("2")

    def _factor_bundle(self, tag: str, iv_rank: Decimal, call: OptionContractSnapshot, put: OptionContractSnapshot, selection: ShortStrangleStrikeSelection, metrics: ShortStrangleRiskMetrics, event: EventRiskEvidence | None) -> FactorInputBundle:
        liquidity = max(_ZERO, _HUNDRED * (_ONE - (self._relative_spread(call) + self._relative_spread(put)) / Decimal("2")))
        greek_quality = max(_ZERO, _HUNDRED * (_ONE - (selection.call_delta_error + selection.put_delta_error) / (Decimal("2") * Decimal("0.50"))))
        risk_reward = min(Decimal("5"), metrics.probability_of_profit * Decimal("5"))
        return FactorInputBundle((
            FactorInput(FactorCategory.MARKET_REGIME, "regime", "RANGE_BOUND", RawValueKind.LABEL, "regime:" + tag),
            FactorInput(FactorCategory.TREND_ALIGNMENT, "trend_alignment", 80, RawValueKind.SCORE_0_100, "regime:" + tag),
            FactorInput(FactorCategory.VOLATILITY, "iv_rank", float(iv_rank), RawValueKind.SCORE_0_100, "context:iv_rank"),
            FactorInput(FactorCategory.LIQUIDITY, "selected_leg_liquidity", float(liquidity), RawValueKind.SCORE_0_100, "snapshot:quotes"),
            FactorInput(FactorCategory.GREEKS, "delta_proximity", float(greek_quality), RawValueKind.SCORE_0_100, "snapshot:greeks"),
            FactorInput(FactorCategory.RISK_REWARD, "pop_heuristic", float(risk_reward), RawValueKind.RATIO, "derived:pop"),
            FactorInput(FactorCategory.EVENT_RISK, "adverse_event", bool(event and event.adverse), RawValueKind.BOOLEAN, "event:" + (event.provenance if event else "none")),
        ))

    def _failure(self, context: StrategyContext, code: str, state: EntryRecommendationState) -> ShortStrangleEvaluationResult:
        recommendation = ShortStrangleRecommendation(
            self._recommendation_id(context, state), state, "short_strangle", context.as_of,
            None, None, None, None, None, self._configuration.exit_time_window, (code,),
        )
        return ShortStrangleEvaluationResult(recommendation, None, self._signal(context, recommendation))

    def _signal(self, context: StrategyContext, recommendation: ShortStrangleRecommendation) -> TradingSignal:
        entered = recommendation.state is EntryRecommendationState.ENTER
        score = recommendation.score.overall_score if recommendation.score else 0.0
        action = SignalAction.EVALUATE if entered else (SignalAction.NO_TRADE if recommendation.state is EntryRecommendationState.REJECT else SignalAction.ABSTAIN)
        selection = recommendation.selection
        return TradingSignal(
            signal_id=recommendation.recommendation_id, as_of=context.as_of, action=action,
            direction=SignalDirection.SHORT_VOL if entered else SignalDirection.UNKNOWN,
            strategy_id=self.metadata.strategy_id, strategy_version=self.metadata.version,
            strategy_family=self.metadata.strategy_family,
            confidence=SignalConfidence(float(score), confidence_band_for_score(float(score)), "short_strangle"),
            market=market_context_from_snapshot(context.snapshot), reasons=recommendation.reasons,
            structure_hint=StructureHint("short_strangle", 2, "delta_ranked_otm", float(self._configuration.target_delta), 1, (OptionType.CE, OptionType.PE)) if entered else None,
            risk=SignalRiskMetadata(RiskProfileHint.UNDEFINED, "UNDEFINED_UNLIMITED", notes=_RISK_WARNING) if entered else None,
            metadata=MappingProxyType({"risk_warning": "UNDEFINED_UNLIMITED"}) if entered else MappingProxyType({}),
        )

    @staticmethod
    def _recommendation_id(context: StrategyContext, state: EntryRecommendationState) -> str:
        material = "|".join((context.correlation_id, context.snapshot.provenance.snapshot_id, context.as_of.isoformat(), state.value))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _plugin_config_for(configuration: ShortStrangleConfiguration) -> StrategyPluginConfig:
    """Build immutable BaseStrategy metadata from short-strangle policy."""
    return StrategyPluginConfig(StrategyMetadata(
        strategy_id="short_strangle", display_name="Short Strangle", version="1.0.0",
        strategy_family=__import__("strategy.signals", fromlist=["StrategyFamily"]).StrategyFamily.SHORT_STRANGLE,
        category="income", supported_underlyings=tuple(sorted(configuration.supported_underlyings)),
        requires_volatility_snapshot=True, min_contracts_required=2,
        risk_profile_hint=StrategyRiskProfileHint.UNDEFINED,
    ), require_valid_snapshot=configuration.require_valid_snapshot)


def to_json(recommendation: ShortStrangleRecommendation) -> str:
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
            return {"start": value.start.isoformat(), "end": value.end.isoformat(), "timezone": value.timezone}
        if hasattr(value, "__dataclass_fields__"):
            return {key: encode(getattr(value, key)) for key in value.__dataclass_fields__}
        if isinstance(value, tuple):
            return [encode(item) for item in value]
        return value
    payload = encode(recommendation)
    payload["schema_version"] = "1.0"
    if recommendation.score is not None:
        payload["score"] = json.loads(StrategyScoringFramework(default_scoring_framework_config()).serialize(recommendation.score))
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def from_json(payload: str) -> ShortStrangleRecommendation:
    """Deserialize and validate a canonical recommendation JSON document."""
    try:
        raw = json.loads(payload)
        if raw["schema_version"] != "1.0":
            raise ValueError("unsupported schema")
        selection_raw, metrics_raw = raw.get("selection"), raw.get("risk_metrics")
        selection = ShortStrangleStrikeSelection(
            selection_raw["underlying"], _decimal(selection_raw["spot"]), date.fromisoformat(selection_raw["expiry"]),
            _decimal(selection_raw["call_strike"]), _decimal(selection_raw["put_strike"]), selection_raw["call_symbol"],
            selection_raw["put_symbol"], selection_raw["call_token"], selection_raw["put_token"],
            _decimal(selection_raw["call_delta"]), _decimal(selection_raw["put_delta"]),
            _decimal(selection_raw["call_delta_error"]), _decimal(selection_raw["put_delta_error"]),
        ) if selection_raw else None
        metrics = ShortStrangleRiskMetrics(
            _decimal(metrics_raw["call_credit"]), _decimal(metrics_raw["put_credit"]), _decimal(metrics_raw["net_credit"]),
            _decimal(metrics_raw["max_profit"]), None, metrics_raw["max_loss_label"],
            _decimal(metrics_raw["probability_of_profit"]), _decimal(metrics_raw["call_otm_probability"]),
            _decimal(metrics_raw["put_otm_probability"]), _decimal(metrics_raw["contract_multiplier"]), metrics_raw["risk_warning"],
        ) if metrics_raw else None
        score = StrategyScoringFramework(default_scoring_framework_config()).deserialize(json.dumps(raw["score"])) if raw.get("score") else None
        window = raw["exit_window_hint"]
        return ShortStrangleRecommendation(
            str(raw["recommendation_id"]), EntryRecommendationState(raw["state"]), str(raw["strategy_id"]),
            datetime.fromisoformat(raw["observed_at"]).astimezone(timezone.utc), selection, metrics, score,
            score.confidence if score else None, score.explanation if score else None,
            TimeWindow(time.fromisoformat(window["start"]), time.fromisoformat(window["end"]), window["timezone"]),
            tuple(raw["reasons"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("SSS.SERIALIZATION.INVALID") from exc


__all__ = [
    "EntryRecommendationState", "EventRiskEvidence", "MarketRegimeEvidence",
    "PremiumPricePolicy", "ShortStrangleConfiguration", "ShortStrangleContext",
    "ShortStrangleEvaluationResult", "ShortStrangleRecommendation", "ShortStrangleRiskMetrics",
    "ShortStrangleStrategy", "ShortStrangleStrikeSelection", "TimeWindow",
    "default_short_strangle_configuration", "from_json", "to_json",
]
