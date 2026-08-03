"""Canonical immutable trading signal contract for THETA AI TRADER.

This module defines :class:`TradingSignal` and supporting types — the
standardized unit of strategy intent consumed by downstream risk, decision,
and execution layers. Signals express *what* and *why*, never broker orders.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Mapping, TypeVar

from market_data.market_snapshot import MarketSnapshot, OptionType

TRADING_SIGNAL_SCHEMA_VERSION: Final[str] = "1.0.0"

ERROR_SCHEMA_MISSING_FIELD: Final[str] = "TRADING_SIGNAL.SCHEMA.MISSING_FIELD"
ERROR_SCHEMA_INVALID_ID: Final[str] = "TRADING_SIGNAL.SCHEMA.INVALID_ID"
ERROR_SCHEMA_NAIVE_TIMESTAMP: Final[str] = "TRADING_SIGNAL.SCHEMA.NAIVE_TIMESTAMP"
ERROR_SCHEMA_INVALID_SCORE: Final[str] = "TRADING_SIGNAL.SCHEMA.INVALID_SCORE"
ERROR_SCHEMA_BAND_MISMATCH: Final[str] = "TRADING_SIGNAL.SCHEMA.BAND_MISMATCH"
ERROR_SCHEMA_EMPTY_REASONS: Final[str] = "TRADING_SIGNAL.SCHEMA.EMPTY_REASONS"
ERROR_SCHEMA_INVALID_ENUM: Final[str] = "TRADING_SIGNAL.SCHEMA.INVALID_ENUM"
ERROR_SCHEMA_INVALID_EXPIRY: Final[str] = "TRADING_SIGNAL.SCHEMA.INVALID_EXPIRY"
ERROR_SEMANTIC_SNAPSHOT_MISMATCH: Final[str] = "TRADING_SIGNAL.SEMANTIC.SNAPSHOT_MISMATCH"
ERROR_SEMANTIC_UNDERLYING_MISMATCH: Final[str] = "TRADING_SIGNAL.SEMANTIC.UNDERLYING_MISMATCH"
ERROR_SEMANTIC_FAMILY_CONFLICT: Final[str] = "TRADING_SIGNAL.SEMANTIC.FAMILY_CONFLICT"
ERROR_SEMANTIC_STALE_CONTEXT: Final[str] = "TRADING_SIGNAL.SEMANTIC.STALE_CONTEXT"
ERROR_SEMANTIC_FORBIDDEN_FIELD: Final[str] = "TRADING_SIGNAL.SEMANTIC.FORBIDDEN_FIELD"
ERROR_DIRECTION_FAMILY_MISMATCH: Final[str] = "TRADING_SIGNAL.DIRECTION.FAMILY_MISMATCH"
ERROR_EXPIRED: Final[str] = "TRADING_SIGNAL.EXPIRED"
ERROR_SERIALIZATION_UNSUPPORTED_VERSION: Final[str] = "TRADING_SIGNAL.SERIALIZATION.UNSUPPORTED_VERSION"
ERROR_SERIALIZATION_MALFORMED: Final[str] = "TRADING_SIGNAL.SERIALIZATION.MALFORMED"
ERROR_BUNDLE_DUPLICATE_ID: Final[str] = "TRADING_SIGNAL.BUNDLE.DUPLICATE_ID"
ERROR_BUNDLE_LIMIT_EXCEEDED: Final[str] = "TRADING_SIGNAL.BUNDLE.LIMIT_EXCEEDED"

_DEFAULT_BUNDLE_LIMIT: Final[int] = 32
_STRATEGY_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SEMVER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_REFERENCE_LABEL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FORBIDDEN_METADATA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "tradingsymbol",
        "instrument_token",
        "order_id",
        "quantity",
        "product",
        "exchange_order_id",
        "variety",
    }
)

_logger = logging.getLogger(__name__)

_E = TypeVar("_E", bound=Enum)


class TradingSignalValidationError(Exception):
    """Raised when a trading signal fails validation."""

    def __init__(self, message: str, *, code: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


class TradingSignalExpiredError(Exception):
    """Raised when a trading signal is past its expiration time."""

    def __init__(self, message: str, *, code: str = ERROR_EXPIRED, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


class TradingSignalSerializationError(Exception):
    """Raised when signal serialization or deserialization fails."""

    def __init__(self, message: str, *, code: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


class SignalAction(str, Enum):
    """High-level trading signal intent."""

    EVALUATE = "evaluate"
    WAIT = "wait"
    NO_TRADE = "no_trade"
    ABSTAIN = "abstain"


class SignalDirection(str, Enum):
    """Directional bias implied by a strategy signal."""

    NEUTRAL = "neutral"
    BULLISH = "bullish"
    BEARISH = "bearish"
    LONG_VOL = "long_vol"
    SHORT_VOL = "short_vol"
    UNKNOWN = "unknown"


class SignalType(str, Enum):
    """Lifecycle classification for a trading signal."""

    SETUP = "setup"
    ENTRY = "entry"
    ADJUSTMENT = "adjustment"
    EXIT = "exit"
    HEDGE = "hedge"
    MONITOR = "monitor"
    ABSTAIN = "abstain"
    INFORMATIONAL = "informational"


class SignalStrength(str, Enum):
    """Ordinal classification of setup quality."""

    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    EXCEPTIONAL = "exceptional"


class ConfidenceBand(str, Enum):
    """Normalized confidence band derived from score."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class EntryTriggerType(str, Enum):
    """Entry trigger classification."""

    IMMEDIATE = "immediate"
    LIMIT_TOUCH = "limit_touch"
    TIME_WINDOW = "time_window"
    VOLATILITY_REGIME = "volatility_regime"
    CONFIRMATION = "confirmation"
    MANUAL_REVIEW = "manual_review"
    NOT_APPLICABLE = "not_applicable"


class ExitTriggerType(str, Enum):
    """Exit trigger classification."""

    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    TIME_DECAY = "time_decay"
    EXPIRY_APPROACH = "expiry_approach"
    VOLATILITY_SHIFT = "volatility_shift"
    DELTA_BREACH = "delta_breach"
    MANUAL = "manual"
    NOT_APPLICABLE = "not_applicable"


class ConditionOperator(str, Enum):
    """Comparison operator for entry/exit conditions."""

    EQ = "eq"
    GT = "gt"
    LT = "lt"
    GTE = "gte"
    LTE = "lte"
    IN = "in"
    BETWEEN = "between"


class StopLossHintType(str, Enum):
    """Stop-loss hint classification."""

    UNDERLYING_LEVEL = "underlying_level"
    PREMIUM_MULTIPLE = "premium_multiple"
    PERCENT_OF_CAPITAL = "percent_of_capital"
    STRUCTURE_BREACH = "structure_breach"
    TIME_STOP = "time_stop"
    NONE = "none"


class TargetHintType(str, Enum):
    """Target hint classification."""

    PREMIUM_DECAY_PERCENT = "premium_decay_percent"
    PREMIUM_MULTIPLE = "premium_multiple"
    UNDERLYING_LEVEL = "underlying_level"
    RISK_REWARD_RATIO = "risk_reward_ratio"
    TIME_TARGET = "time_target"
    NONE = "none"


class ValueUnit(str, Enum):
    """Unit for numeric stop/target hints."""

    POINTS = "points"
    PERCENT = "percent"
    MULTIPLE = "multiple"
    ABSOLUTE_PREMIUM = "absolute_premium"


class SessionScope(str, Enum):
    """Session binding for signal time validity."""

    REGULAR = "regular"
    PRE_OPEN = "pre_open"
    POST_CLOSE = "post_close"
    FULL_DAY = "full_day"
    MULTI_DAY = "multi_day"


class RiskProfileHint(str, Enum):
    """Defined vs undefined risk structure hint."""

    DEFINED = "defined"
    UNDEFINED = "undefined"


class MarginIntensityHint(str, Enum):
    """Qualitative margin demand hint."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    UNKNOWN = "unknown"


class RiskLevelHint(str, Enum):
    """Qualitative risk level hint."""

    LOW = "low"
    MODERATE = "moderate"
    ELEVATED = "elevated"
    HIGH = "high"
    UNKNOWN = "unknown"


class StrategyFamily(str, Enum):
    """Canonical strategy family identifiers."""

    SHORT_STRANGLE = "short_strangle"
    IRON_CONDOR = "iron_condor"
    BULL_PUT_SPREAD = "bull_put_spread"
    BEAR_CALL_SPREAD = "bear_call_spread"
    BROKEN_WING_BUTTERFLY = "broken_wing_butterfly"
    JADE_LIZARD = "jade_lizard"
    LONG_VOLATILITY = "long_volatility"
    CUSTOM = "custom"
    NO_STRATEGY = "no_strategy"


class StrategyExecutionMode(str, Enum):
    """Execution mode for strategy evaluation."""

    LIVE = "live"
    ANALYSIS = "analysis"
    BACKTEST = "backtest"


class AggregationMode(str, Enum):
    """Signal aggregation mode."""

    PRIMARY_SECONDARY = "primary_secondary"
    MULTI_SIGNAL = "multi_signal"
    SINGLE_WINNER = "single_winner"
    NO_TRADE_DEFAULT = "no_trade_default"


@dataclass(frozen=True)
class ConfidenceComponent:
    """Weighted factor in a confidence score breakdown."""

    name: str
    weight: float
    score: float
    contribution: float
    description: str


@dataclass(frozen=True)
class SignalConfidence:
    """Explainable confidence score attached to a trading signal."""

    score: float
    band: ConfidenceBand
    method: str = "strategy_plugin"
    components: tuple[ConfidenceComponent, ...] = ()


@dataclass(frozen=True)
class SignalFactor:
    """Machine-readable scoring factor attached to a signal."""

    name: str
    weight: float
    score: float
    description: str


@dataclass(frozen=True)
class EntryCondition:
    """Single entry condition descriptor."""

    condition_id: str
    operator: ConditionOperator
    reference: str
    value: float | str | tuple[float, float] | None
    met: bool | None
    description: str


@dataclass(frozen=True)
class ExitCondition:
    """Single exit condition descriptor."""

    condition_id: str
    operator: ConditionOperator
    reference: str
    value: float | str | tuple[float, float] | None
    met: bool | None
    description: str


@dataclass(frozen=True)
class SessionWindow:
    """Preferred session time window."""

    start_time: time
    end_time: time
    timezone: str
    label: str


@dataclass(frozen=True)
class EntryLogic:
    """Descriptive entry conditions for a trading signal."""

    trigger_type: EntryTriggerType
    conditions: tuple[EntryCondition, ...]
    preferred_session_window: SessionWindow | None = None
    reentry_allowed: bool = False
    notes: str | None = None


@dataclass(frozen=True)
class ExitLogic:
    """Descriptive exit conditions for a trading signal."""

    trigger_type: ExitTriggerType
    conditions: tuple[ExitCondition, ...]
    exit_fraction: float | None = None
    roll_to_expiry: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class StopLossHint:
    """Abstract protective level hint — not a broker stop order."""

    hint_type: StopLossHintType
    reference: str
    value: float | None = None
    value_unit: ValueUnit | None = None
    basis: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class TargetHint:
    """Abstract profit objective hint — not a limit order."""

    hint_type: TargetHintType
    reference: str
    value: float | None = None
    value_unit: ValueUnit | None = None
    basis: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class SignalTimeValidity:
    """Window during which a signal intent should be acted upon."""

    valid_from: datetime | None = None
    valid_until: datetime | None = None
    session_scope: SessionScope | None = None
    intraday_only: bool = True
    expiry_session_cutoff: time | None = None
    timezone: str = "Asia/Kolkata"


@dataclass(frozen=True)
class SignalRiskMetadata:
    """Informational risk characteristics — not enforcement."""

    profile: RiskProfileHint
    max_loss_category: str | None = None
    max_profit_category: str | None = None
    margin_intensity: MarginIntensityHint | None = None
    gamma_risk: RiskLevelHint | None = None
    vega_risk: RiskLevelHint | None = None
    tail_risk: RiskLevelHint | None = None
    notes: str | None = None


@dataclass(frozen=True)
class SignalStrategyMetadata:
    """Embedded strategy provenance snapshot."""

    strategy_id: str
    strategy_version: str
    strategy_family: StrategyFamily
    display_name: str | None = None
    plugin_priority: int | None = None
    execution_mode: StrategyExecutionMode | None = None


@dataclass(frozen=True)
class SignalMarketContext:
    """Lightweight market observation references."""

    snapshot_id: str
    underlying: str
    expiry: str | None = None
    spot_at_signal: float | None = None
    vix_at_signal: float | None = None
    atm_strike: float | None = None
    snapshot_as_of: datetime | None = None
    snapshot_validation_status: str | None = None
    freshness_status: str | None = None


@dataclass(frozen=True)
class StructureHint:
    """Abstract multi-leg structure layout guidance."""

    structure_type: str
    leg_count: int
    strike_selection_policy: str | None = None
    target_delta: float | None = None
    strikes_each_side: int | None = None
    option_types: tuple[OptionType, ...] | None = None


@dataclass(frozen=True)
class TradingSignal:
    """Immutable standardized trading signal expressing strategy intent."""

    signal_id: str
    as_of: datetime
    action: SignalAction
    direction: SignalDirection
    strategy_id: str
    strategy_version: str
    strategy_family: StrategyFamily
    confidence: SignalConfidence
    market: SignalMarketContext
    reasons: tuple[str, ...]
    valid_until: datetime | None = None
    signal_type: SignalType | None = None
    strength: SignalStrength | None = None
    strategy: SignalStrategyMetadata | None = None
    structure_hint: StructureHint | None = None
    entry: EntryLogic | None = None
    exit: ExitLogic | None = None
    stop_loss: StopLossHint | None = None
    target: TargetHint | None = None
    risk: SignalRiskMetadata | None = None
    time_validity: SignalTimeValidity | None = None
    factors: tuple[SignalFactor, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def underlying(self) -> str:
        """Return canonical underlying symbol from market context."""
        return self.market.underlying

    @property
    def snapshot_id(self) -> str:
        """Return source snapshot identifier from market context."""
        return self.market.snapshot_id

    @property
    def expiry(self) -> str | None:
        """Return option expiry from market context when present."""
        return self.market.expiry

    @property
    def resolved_signal_type(self) -> SignalType:
        """Return explicit or inferred signal type."""
        if self.signal_type is not None:
            return self.signal_type
        return infer_signal_type(
            self.action,
            has_entry=self.entry is not None,
            has_exit=self.exit is not None,
        )

    @property
    def resolved_strength(self) -> SignalStrength:
        """Return explicit or inferred signal strength."""
        if self.strength is not None:
            return self.strength
        return infer_signal_strength(self.confidence, self.action)


@dataclass(frozen=True)
class SignalBundle:
    """Ordered collection of trading signals from one evaluation pass."""

    signals: tuple[TradingSignal, ...]


@dataclass(frozen=True)
class AggregationMetadata:
    """Metadata describing how signals were aggregated."""

    aggregation_mode: AggregationMode
    signal_count: int
    conflict_count: int = 0


@dataclass(frozen=True)
class AggregatedSignalResult:
    """Post-aggregation multi-strategy signal output."""

    aggregate_confidence: SignalConfidence
    aggregation_metadata: AggregationMetadata
    primary_signal: TradingSignal | None = None
    secondary_signals: tuple[TradingSignal, ...] = ()
    abstain_signals: tuple[TradingSignal, ...] = ()


@dataclass(frozen=True)
class SignalValidationRecord:
    """Single validation error or warning."""

    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True)
class SignalValidationResult:
    """Outcome of trading signal validation."""

    is_valid: bool
    errors: tuple[SignalValidationRecord, ...]
    warnings: tuple[SignalValidationRecord, ...]


@dataclass(frozen=True)
class SignalValidationContext:
    """Optional context for semantic signal validation."""

    snapshot_id: str | None = None
    underlying: str | None = None
    execution_mode: str | None = None
    reference_time: datetime | None = None
    strict: bool = False
    strict_direction_check: bool = False


@dataclass(frozen=True)
class SignalExpirationPolicy:
    """Configurable default expiration offsets by execution mode."""

    live_seconds: float = 120.0
    analysis_seconds: float = 86400.0
    backtest_seconds: float = 1.0


@dataclass(frozen=True)
class ValidationPolicy:
    """Configurable validation strictness."""

    strict_direction_check: bool = False
    max_bundle_size: int = _DEFAULT_BUNDLE_LIMIT
    treat_stale_as_error: bool = False


def confidence_band_for_score(score: float) -> ConfidenceBand:
    """Map a confidence score to a confidence band.

    Args:
        score: Confidence score in ``0.0..100.0``.

    Returns:
        Derived confidence band.
    """
    if score < 40.0:
        return ConfidenceBand.LOW
    if score < 60.0:
        return ConfidenceBand.MEDIUM
    if score < 80.0:
        return ConfidenceBand.HIGH
    return ConfidenceBand.VERY_HIGH


def infer_signal_type(
    action: SignalAction,
    *,
    has_entry: bool = False,
    has_exit: bool = False,
) -> SignalType:
    """Infer signal type from action and populated logic blocks.

    Args:
        action: High-level signal action.
        has_entry: Whether entry logic is populated.
        has_exit: Whether exit logic is populated.

    Returns:
        Inferred :class:`SignalType`.
    """
    if action in (SignalAction.ABSTAIN, SignalAction.NO_TRADE):
        return SignalType.ABSTAIN
    if action is SignalAction.WAIT:
        return SignalType.MONITOR
    if action is SignalAction.EVALUATE and has_exit:
        return SignalType.EXIT
    if action is SignalAction.EVALUATE and has_entry:
        return SignalType.ENTRY
    if action is SignalAction.EVALUATE:
        return SignalType.SETUP
    return SignalType.INFORMATIONAL


def infer_signal_strength(confidence: SignalConfidence, action: SignalAction) -> SignalStrength:
    """Infer signal strength from confidence and action.

    Args:
        confidence: Confidence object attached to the signal.
        action: High-level signal action.

    Returns:
        Inferred :class:`SignalStrength`.
    """
    if action in (SignalAction.ABSTAIN, SignalAction.NO_TRADE):
        return SignalStrength.NONE
    if confidence.score < 40.0:
        return SignalStrength.WEAK
    if confidence.score < 60.0:
        return SignalStrength.MODERATE
    if confidence.score < 80.0:
        return SignalStrength.STRONG
    return SignalStrength.EXCEPTIONAL


def are_directions_opposed(first: SignalDirection, second: SignalDirection) -> bool:
    """Return whether two directions are directionally opposed.

    Args:
        first: First direction.
        second: Second direction.

    Returns:
        ``True`` when directions conflict for multi-strategy resolution.
    """
    opposed_pairs = {
        (SignalDirection.BULLISH, SignalDirection.BEARISH),
        (SignalDirection.BEARISH, SignalDirection.BULLISH),
        (SignalDirection.LONG_VOL, SignalDirection.SHORT_VOL),
        (SignalDirection.SHORT_VOL, SignalDirection.LONG_VOL),
    }
    return (first, second) in opposed_pairs


def market_context_from_snapshot(snapshot: MarketSnapshot) -> SignalMarketContext:
    """Build lightweight market context references from a snapshot.

    Args:
        snapshot: Canonical immutable market snapshot.

    Returns:
        :class:`SignalMarketContext` with summary fields only.
    """
    underlying = snapshot.option_chain.metadata.underlying.strip().upper()
    vix = snapshot.volatility.last_price if snapshot.volatility is not None else None
    return SignalMarketContext(
        snapshot_id=snapshot.provenance.snapshot_id,
        underlying=underlying,
        expiry=snapshot.option_chain.metadata.expiry,
        spot_at_signal=snapshot.underlying.last_price,
        vix_at_signal=vix,
        atm_strike=snapshot.option_chain.metadata.atm_strike,
        snapshot_as_of=snapshot.provenance.as_of,
        snapshot_validation_status=snapshot.quality.validation_status.value,
        freshness_status=snapshot.freshness.status.value,
    )


def apply_default_valid_until(
    signal: TradingSignal,
    *,
    policy: SignalExpirationPolicy | None = None,
    execution_mode: StrategyExecutionMode | None = None,
) -> TradingSignal:
    """Return a signal copy with default ``valid_until`` when absent.

    Args:
        signal: Source trading signal.
        policy: Expiration policy overrides.
        execution_mode: Execution mode hint; defaults to nested strategy metadata.

    Returns:
        Signal with ``valid_until`` populated when previously ``None``.
    """
    if signal.valid_until is not None:
        return signal
    resolved_policy = policy or SignalExpirationPolicy()
    mode = execution_mode
    if mode is None and signal.strategy is not None:
        mode = signal.strategy.execution_mode
    if mode is None:
        mode = StrategyExecutionMode.LIVE
    if mode is StrategyExecutionMode.LIVE:
        delta = timedelta(seconds=resolved_policy.live_seconds)
    elif mode is StrategyExecutionMode.ANALYSIS:
        delta = timedelta(seconds=resolved_policy.analysis_seconds)
    else:
        delta = timedelta(seconds=resolved_policy.backtest_seconds)
    return replace(signal, valid_until=signal.as_of + delta)


def is_signal_expired(signal: TradingSignal, *, reference_time: datetime) -> bool:
    """Return whether the signal is past its expiration timestamp.

    Args:
        signal: Trading signal to evaluate.
        reference_time: Comparison timestamp (timezone-aware).

    Returns:
        ``True`` when ``valid_until`` is set and ``reference_time`` exceeds it.
    """
    if signal.valid_until is None:
        return False
    return reference_time > signal.valid_until


def remaining_validity_seconds(signal: TradingSignal, *, reference_time: datetime) -> float:
    """Return seconds until signal expiration.

    Args:
        signal: Trading signal to evaluate.
        reference_time: Comparison timestamp (timezone-aware).

    Returns:
        Seconds remaining; negative when expired; ``inf`` when no expiration set.
    """
    if signal.valid_until is None:
        return math.inf
    return (signal.valid_until - reference_time).total_seconds()


def assert_signal_fresh(signal: TradingSignal, *, reference_time: datetime) -> None:
    """Raise when the signal is expired at ``reference_time``.

    Args:
        signal: Trading signal to evaluate.
        reference_time: Comparison timestamp (timezone-aware).

    Raises:
        TradingSignalExpiredError: When the signal is expired.
    """
    if is_signal_expired(signal, reference_time=reference_time):
        raise TradingSignalExpiredError(
            "Trading signal is expired.",
            code=ERROR_EXPIRED,
            field="valid_until",
        )


def signal_fingerprint(signal: TradingSignal, *, include_signal_id: bool = False) -> str:
    """Return deterministic SHA-256 fingerprint of signal content.

    Args:
        signal: Trading signal to fingerprint.
        include_signal_id: Whether to include ``signal_id`` in the hash material.

    Returns:
        Hex digest suitable for deduplication and replay verification.
    """
    payload = to_dict(signal, omit_nulls=True)
    if not include_signal_id:
        payload.pop("signal_id", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _parse_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise TradingSignalSerializationError(
            f"{field} must be an ISO datetime string.",
            code=ERROR_SERIALIZATION_MALFORMED,
            field=field,
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TradingSignalSerializationError(
            f"Invalid datetime for {field}.",
            code=ERROR_SERIALIZATION_MALFORMED,
            field=field,
        ) from exc
    if not _is_timezone_aware(parsed):
        raise TradingSignalSerializationError(
            f"{field} must be timezone-aware.",
            code=ERROR_SCHEMA_NAIVE_TIMESTAMP,
            field=field,
        )
    return parsed


def _parse_time(value: object, *, field: str) -> time:
    if not isinstance(value, str):
        raise TradingSignalSerializationError(
            f"{field} must be an ISO time string.",
            code=ERROR_SERIALIZATION_MALFORMED,
            field=field,
        )
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise TradingSignalSerializationError(
            f"Invalid time for {field}.",
            code=ERROR_SERIALIZATION_MALFORMED,
            field=field,
        ) from exc


def _enum_from_value(enum_cls: type[_E], value: object, *, field: str) -> _E:
    if isinstance(value, enum_cls):
        return value
    if not isinstance(value, str):
        raise TradingSignalSerializationError(
            f"{field} must be a string enum value.",
            code=ERROR_SCHEMA_INVALID_ENUM,
            field=field,
        )
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise TradingSignalSerializationError(
            f"Invalid enum value for {field}: {value}.",
            code=ERROR_SCHEMA_INVALID_ENUM,
            field=field,
        ) from exc


def _record(
    errors: list[SignalValidationRecord],
    *,
    code: str,
    message: str,
    field: str | None = None,
) -> None:
    errors.append(SignalValidationRecord(code=code, message=message, field=field))


def _record_warning(
    warnings: list[SignalValidationRecord],
    *,
    code: str,
    message: str,
    field: str | None = None,
) -> None:
    warnings.append(SignalValidationRecord(code=code, message=message, field=field))


def _validate_forbidden_metadata(metadata: Mapping[str, str]) -> list[SignalValidationRecord]:
    errors: list[SignalValidationRecord] = []
    for key in metadata:
        if key.lower() in _FORBIDDEN_METADATA_KEYS:
            _record(
                errors,
                code=ERROR_SEMANTIC_FORBIDDEN_FIELD,
                message=f"Forbidden broker metadata key '{key}'.",
                field=f"metadata.{key}",
            )
    return errors


def validate_trading_signal_schema(signal: TradingSignal) -> SignalValidationResult:
    """Validate trading signal schema invariants.

    Args:
        signal: Candidate trading signal.

    Returns:
        Validation result with errors and warnings.
    """
    errors: list[SignalValidationRecord] = []
    warnings: list[SignalValidationRecord] = []

    if not signal.signal_id.strip():
        _record(errors, code=ERROR_SCHEMA_INVALID_ID, message="signal_id must be non-empty.", field="signal_id")

    if not _is_timezone_aware(signal.as_of):
        _record(errors, code=ERROR_SCHEMA_NAIVE_TIMESTAMP, message="as_of must be timezone-aware.", field="as_of")

    if signal.valid_until is not None:
        if not _is_timezone_aware(signal.valid_until):
            _record(
                errors,
                code=ERROR_SCHEMA_NAIVE_TIMESTAMP,
                message="valid_until must be timezone-aware.",
                field="valid_until",
            )
        elif _is_timezone_aware(signal.as_of) and signal.valid_until < signal.as_of:
            _record(
                errors,
                code=ERROR_SCHEMA_INVALID_EXPIRY,
                message="valid_until must not precede as_of.",
                field="valid_until",
            )

    if not signal.reasons:
        _record(errors, code=ERROR_SCHEMA_EMPTY_REASONS, message="reasons must be non-empty.", field="reasons")

    if not math.isfinite(signal.confidence.score) or not (0.0 <= signal.confidence.score <= 100.0):
        _record(
            errors,
            code=ERROR_SCHEMA_INVALID_SCORE,
            message="confidence.score must be within [0.0, 100.0].",
            field="confidence.score",
        )
    elif signal.confidence.band is not confidence_band_for_score(signal.confidence.score):
        _record(
            errors,
            code=ERROR_SCHEMA_BAND_MISMATCH,
            message="confidence.band must match score-derived band.",
            field="confidence.band",
        )

    if not _STRATEGY_ID_PATTERN.match(signal.strategy_id.strip()):
        _record(
            errors,
            code=ERROR_SCHEMA_MISSING_FIELD,
            message="strategy_id must match ^[a-z][a-z0-9_]{1,63}$.",
            field="strategy_id",
        )

    if not _SEMVER_PATTERN.match(signal.strategy_version.strip()):
        _record(
            errors,
            code=ERROR_SCHEMA_MISSING_FIELD,
            message="strategy_version must be a valid semantic version.",
            field="strategy_version",
        )

    if not signal.market.snapshot_id.strip():
        _record(
            errors,
            code=ERROR_SCHEMA_MISSING_FIELD,
            message="market.snapshot_id must be non-empty.",
            field="market.snapshot_id",
        )

    if not signal.market.underlying.strip():
        _record(
            errors,
            code=ERROR_SCHEMA_MISSING_FIELD,
            message="market.underlying must be non-empty.",
            field="market.underlying",
        )

    if signal.market.spot_at_signal is not None:
        if not math.isfinite(signal.market.spot_at_signal) or signal.market.spot_at_signal <= 0:
            _record(
                errors,
                code=ERROR_SCHEMA_MISSING_FIELD,
                message="market.spot_at_signal must be finite and > 0.",
                field="market.spot_at_signal",
            )

    resolved_type = signal.resolved_signal_type
    if resolved_type is SignalType.ABSTAIN and signal.action not in (SignalAction.ABSTAIN, SignalAction.NO_TRADE):
        _record(
            errors,
            code=ERROR_SCHEMA_INVALID_ENUM,
            message="signal_type=ABSTAIN requires action ABSTAIN or NO_TRADE.",
            field="action",
        )
    if signal.action is SignalAction.EVALUATE and resolved_type is SignalType.ABSTAIN:
        _record(
            errors,
            code=ERROR_SCHEMA_INVALID_ENUM,
            message="action=EVALUATE is incompatible with signal_type=ABSTAIN.",
            field="signal_type",
        )

    resolved_strength = signal.resolved_strength
    if resolved_strength is SignalStrength.NONE and signal.action is SignalAction.EVALUATE:
        _record(
            warnings,
            code=ERROR_SCHEMA_INVALID_ENUM,
            message="strength=NONE with action=EVALUATE is unusual.",
            field="strength",
        )
    if resolved_strength is SignalStrength.EXCEPTIONAL and signal.confidence.score < 75.0:
        _record(
            warnings,
            code=ERROR_SCHEMA_INVALID_SCORE,
            message="strength=EXCEPTIONAL implies confidence.score >= 75.",
            field="strength",
        )

    if signal.strategy is not None:
        if signal.strategy.strategy_id != signal.strategy_id:
            _record(
                errors,
                code=ERROR_SCHEMA_MISSING_FIELD,
                message="strategy.strategy_id must match top-level strategy_id.",
                field="strategy.strategy_id",
            )
        if signal.strategy.strategy_version != signal.strategy_version:
            _record(
                errors,
                code=ERROR_SCHEMA_MISSING_FIELD,
                message="strategy.strategy_version must match top-level strategy_version.",
                field="strategy.strategy_version",
            )
        if signal.strategy.strategy_family != signal.strategy_family:
            _record(
                errors,
                code=ERROR_SCHEMA_MISSING_FIELD,
                message="strategy.strategy_family must match top-level strategy_family.",
                field="strategy.strategy_family",
            )

    if signal.entry is not None:
        for index, condition in enumerate(signal.entry.conditions):
            if not _REFERENCE_LABEL_PATTERN.match(condition.reference):
                _record(
                    errors,
                    code=ERROR_SCHEMA_MISSING_FIELD,
                    message="entry condition reference must match label pattern.",
                    field=f"entry.conditions[{index}].reference",
                )

    if signal.exit is not None and signal.exit.exit_fraction is not None:
        if not (0.0 <= signal.exit.exit_fraction <= 1.0):
            _record(
                errors,
                code=ERROR_SCHEMA_MISSING_FIELD,
                message="exit.exit_fraction must be within [0.0, 1.0].",
                field="exit.exit_fraction",
            )

    if signal.stop_loss is not None and signal.stop_loss.value is not None:
        if not math.isfinite(signal.stop_loss.value) or signal.stop_loss.value <= 0:
            _record(
                errors,
                code=ERROR_SCHEMA_MISSING_FIELD,
                message="stop_loss.value must be finite and > 0.",
                field="stop_loss.value",
            )
        if signal.stop_loss.hint_type is StopLossHintType.NONE:
            _record(
                warnings,
                code=ERROR_SCHEMA_MISSING_FIELD,
                message="stop_loss hint_type=NONE should not include value.",
                field="stop_loss.value",
            )

    if signal.target is not None and signal.target.value is not None:
        if not math.isfinite(signal.target.value) or signal.target.value <= 0:
            _record(
                errors,
                code=ERROR_SCHEMA_MISSING_FIELD,
                message="target.value must be finite and > 0.",
                field="target.value",
            )

    if signal.time_validity is not None:
        tv = signal.time_validity
        if tv.valid_from is not None and not _is_timezone_aware(tv.valid_from):
            _record(
                errors,
                code=ERROR_SCHEMA_NAIVE_TIMESTAMP,
                message="time_validity.valid_from must be timezone-aware.",
                field="time_validity.valid_from",
            )
        if tv.valid_until is not None and not _is_timezone_aware(tv.valid_until):
            _record(
                errors,
                code=ERROR_SCHEMA_NAIVE_TIMESTAMP,
                message="time_validity.valid_until must be timezone-aware.",
                field="time_validity.valid_until",
            )
        if (
            tv.valid_from is not None
            and tv.valid_until is not None
            and _is_timezone_aware(tv.valid_from)
            and _is_timezone_aware(tv.valid_until)
            and tv.valid_until < tv.valid_from
        ):
            _record(
                errors,
                code=ERROR_SCHEMA_INVALID_EXPIRY,
                message="time_validity.valid_until must not precede valid_from.",
                field="time_validity.valid_until",
            )
        if (
            signal.valid_until is not None
            and tv.valid_until is not None
            and signal.valid_until != tv.valid_until
        ):
            _record(
                errors,
                code=ERROR_SCHEMA_INVALID_EXPIRY,
                message="valid_until must agree with time_validity.valid_until.",
                field="valid_until",
            )

    if signal.action is SignalAction.EVALUATE and signal.strategy_family is StrategyFamily.NO_STRATEGY:
        _record(
            errors,
            code=ERROR_SEMANTIC_FAMILY_CONFLICT,
            message="action=EVALUATE is incompatible with strategy_family=NO_STRATEGY.",
            field="strategy_family",
        )

    errors.extend(_validate_forbidden_metadata(signal.metadata))
    return SignalValidationResult(
        is_valid=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def validate_trading_signal_semantics(
    signal: TradingSignal,
    *,
    context: SignalValidationContext | None = None,
) -> SignalValidationResult:
    """Validate trading signal semantic rules against optional context.

    Args:
        signal: Candidate trading signal.
        context: Optional semantic validation context.

    Returns:
        Validation result with errors and warnings.
    """
    errors: list[SignalValidationRecord] = []
    warnings: list[SignalValidationRecord] = []
    ctx = context or SignalValidationContext()

    if ctx.snapshot_id is not None and signal.market.snapshot_id != ctx.snapshot_id:
        _record(
            errors,
            code=ERROR_SEMANTIC_SNAPSHOT_MISMATCH,
            message="market.snapshot_id mismatch with validation context.",
            field="market.snapshot_id",
        )

    if ctx.underlying is not None and signal.market.underlying.strip().upper() != ctx.underlying.strip().upper():
        _record(
            errors,
            code=ERROR_SEMANTIC_UNDERLYING_MISMATCH,
            message="market.underlying mismatch with validation context.",
            field="market.underlying",
        )

    if ctx.reference_time is not None and is_signal_expired(signal, reference_time=ctx.reference_time):
        _record(
            errors,
            code=ERROR_EXPIRED,
            message="signal is expired at reference_time.",
            field="valid_until",
        )

    if (
        ctx.execution_mode == StrategyExecutionMode.LIVE.value
        and signal.market.freshness_status == "STALE"
    ):
        message = "market context freshness_status is STALE in LIVE mode."
        if ctx.strict:
            _record(errors, code=ERROR_SEMANTIC_STALE_CONTEXT, message=message, field="market.freshness_status")
        else:
            _record_warning(warnings, code=ERROR_SEMANTIC_STALE_CONTEXT, message=message, field="market.freshness_status")

    if ctx.strict_direction_check or (context is not None and context.strict_direction_check):
        expected = _expected_directions_for_family(signal.strategy_family)
        if expected and signal.direction not in expected:
            _record(
                warnings if not ctx.strict else errors,
                code=ERROR_DIRECTION_FAMILY_MISMATCH,
                message="direction may be inconsistent with strategy_family.",
                field="direction",
            )

    if signal.action is SignalAction.EVALUATE and signal.resolved_signal_type is SignalType.ENTRY and signal.entry is None:
        _record_warning(
            warnings,
            code=ERROR_SCHEMA_MISSING_FIELD,
            message="signal_type=ENTRY should include entry logic.",
            field="entry",
        )

    if signal.resolved_signal_type is SignalType.EXIT and signal.exit is None:
        _record_warning(
            warnings,
            code=ERROR_SCHEMA_MISSING_FIELD,
            message="signal_type=EXIT should include exit logic.",
            field="exit",
        )

    if (
        signal.stop_loss is not None
        and signal.target is not None
        and signal.stop_loss.basis
        and signal.target.basis
        and signal.stop_loss.basis != signal.target.basis
    ):
        _record_warning(
            warnings,
            code=ERROR_SCHEMA_MISSING_FIELD,
            message="stop_loss and target basis should be consistent when paired.",
            field="target.basis",
        )

    return SignalValidationResult(
        is_valid=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _expected_directions_for_family(family: StrategyFamily) -> set[SignalDirection]:
    mapping: dict[StrategyFamily, set[SignalDirection]] = {
        StrategyFamily.SHORT_STRANGLE: {SignalDirection.NEUTRAL, SignalDirection.SHORT_VOL},
        StrategyFamily.IRON_CONDOR: {SignalDirection.NEUTRAL, SignalDirection.SHORT_VOL},
        StrategyFamily.BULL_PUT_SPREAD: {SignalDirection.BULLISH},
        StrategyFamily.BEAR_CALL_SPREAD: {SignalDirection.BEARISH},
        StrategyFamily.LONG_VOLATILITY: {SignalDirection.LONG_VOL},
        StrategyFamily.NO_STRATEGY: {SignalDirection.UNKNOWN},
    }
    return mapping.get(family, set())


def validate_trading_signal(
    signal: TradingSignal,
    *,
    context: SignalValidationContext | None = None,
) -> SignalValidationResult:
    """Validate schema and semantic rules for a trading signal.

    Args:
        signal: Candidate trading signal.
        context: Optional semantic validation context.

    Returns:
        Combined validation result.
    """
    schema = validate_trading_signal_schema(signal)
    semantics = validate_trading_signal_semantics(signal, context=context)
    errors = schema.errors + semantics.errors
    warnings = schema.warnings + semantics.warnings
    if context is not None and context.strict:
        errors = errors + warnings
        warnings = ()
    return SignalValidationResult(is_valid=not errors, errors=errors, warnings=warnings)


def assert_valid_trading_signal(
    signal: TradingSignal,
    *,
    context: SignalValidationContext | None = None,
) -> TradingSignal:
    """Validate signal and raise on failure.

    Args:
        signal: Candidate trading signal.
        context: Optional semantic validation context.

    Returns:
        The validated signal unchanged.

    Raises:
        TradingSignalValidationError: When validation fails.
    """
    result = validate_trading_signal(signal, context=context)
    if result.is_valid:
        return signal
    first = result.errors[0]
    raise TradingSignalValidationError(first.message, code=first.code, field=first.field)


def validate_signal_bundle(
    bundle: SignalBundle,
    *,
    policy: ValidationPolicy | None = None,
) -> SignalValidationResult:
    """Validate an ordered signal bundle.

    Args:
        bundle: Signal bundle to validate.
        policy: Validation policy overrides.

    Returns:
        Validation result for bundle-level constraints.
    """
    resolved = policy or ValidationPolicy()
    errors: list[SignalValidationRecord] = []
    warnings: list[SignalValidationRecord] = []

    if len(bundle.signals) > resolved.max_bundle_size:
        _record(
            errors,
            code=ERROR_BUNDLE_LIMIT_EXCEEDED,
            message=f"bundle exceeds max size of {resolved.max_bundle_size}.",
            field="signals",
        )

    seen: set[str] = set()
    for index, signal in enumerate(bundle.signals):
        if signal.signal_id in seen:
            _record(
                errors,
                code=ERROR_BUNDLE_DUPLICATE_ID,
                message=f"duplicate signal_id at index {index}.",
                field=f"signals[{index}].signal_id",
            )
        seen.add(signal.signal_id)
        item_result = validate_trading_signal(
            signal,
            context=SignalValidationContext(strict_direction_check=resolved.strict_direction_check),
        )
        errors.extend(item_result.errors)
        warnings.extend(item_result.warnings)

    return SignalValidationResult(is_valid=not errors, errors=tuple(errors), warnings=tuple(warnings))


def validate_aggregated_result(result: AggregatedSignalResult) -> SignalValidationResult:
    """Validate aggregated signal result consistency.

    Args:
        result: Aggregated signal output.

    Returns:
        Validation result.
    """
    errors: list[SignalValidationRecord] = []
    warnings: list[SignalValidationRecord] = []

    if result.aggregate_confidence.band is not confidence_band_for_score(result.aggregate_confidence.score):
        _record(
            errors,
            code=ERROR_SCHEMA_BAND_MISMATCH,
            message="aggregate_confidence.band must match score.",
            field="aggregate_confidence.band",
        )

    if result.primary_signal is not None:
        primary_result = validate_trading_signal(result.primary_signal)
        errors.extend(primary_result.errors)
        warnings.extend(primary_result.warnings)
        primary_id = result.primary_signal.signal_id
        for index, secondary in enumerate(result.secondary_signals):
            if secondary.signal_id == primary_id:
                _record(
                    errors,
                    code=ERROR_BUNDLE_DUPLICATE_ID,
                    message="primary signal duplicated in secondary_signals.",
                    field=f"secondary_signals[{index}]",
                )

    for signal in result.secondary_signals:
        item = validate_trading_signal(signal)
        errors.extend(item.errors)
        warnings.extend(item.warnings)

    for signal in result.abstain_signals:
        item = validate_trading_signal(signal)
        errors.extend(item.errors)
        warnings.extend(item.warnings)

    expected_count = (
        (1 if result.primary_signal is not None else 0)
        + len(result.secondary_signals)
        + len(result.abstain_signals)
    )
    if result.aggregation_metadata.signal_count != expected_count:
        _record_warning(
            warnings,
            code=ERROR_SCHEMA_MISSING_FIELD,
            message="aggregation_metadata.signal_count does not match contained signals.",
            field="aggregation_metadata.signal_count",
        )

    return SignalValidationResult(is_valid=not errors, errors=tuple(errors), warnings=tuple(warnings))


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat()


def _serialize_time(value: time) -> str:
    return value.isoformat()


def _serialize_enum(value: Enum) -> str:
    return value.value


def _serialize_mapping(value: Mapping[str, str]) -> dict[str, str]:
    return dict(sorted(value.items()))


def _serialize_dataclass(obj: object, *, omit_nulls: bool) -> dict[str, Any]:
    if not is_dataclass(obj):
        raise TypeError(f"Expected dataclass instance, got {type(obj)!r}")
    payload: dict[str, Any] = {}
    for item in fields(obj):
        value = getattr(obj, item.name)
        serialized = _serialize_value(value, omit_nulls=omit_nulls)
        if omit_nulls and serialized is None:
            continue
        payload[item.name] = serialized
    return payload


def _serialize_value(value: object, *, omit_nulls: bool) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return _serialize_enum(value)
    if isinstance(value, datetime):
        return _serialize_datetime(value)
    if isinstance(value, time):
        return _serialize_time(value)
    if isinstance(value, Mapping):
        return _serialize_mapping(value)  # type: ignore[arg-type]
    if isinstance(value, tuple):
        if not value:
            return []
        first = value[0]
        if is_dataclass(first):
            return [_serialize_dataclass(item, omit_nulls=omit_nulls) for item in value]
        if isinstance(first, Enum):
            return [_serialize_enum(item) for item in value]  # type: ignore[arg-type]
        if isinstance(first, (float, int, str)) or first is None:
            return list(value)
        return [_serialize_value(item, omit_nulls=omit_nulls) for item in value]
    if is_dataclass(value):
        return _serialize_dataclass(value, omit_nulls=omit_nulls)
    return value


def to_dict(signal: TradingSignal, *, omit_nulls: bool = True) -> dict[str, Any]:
    """Serialize a trading signal to a dictionary.

    Args:
        signal: Trading signal instance.
        omit_nulls: Whether to omit ``None`` optional fields.

    Returns:
        JSON-compatible dictionary with schema version.
    """
    payload = _serialize_dataclass(signal, omit_nulls=omit_nulls)
    payload["schema_version"] = TRADING_SIGNAL_SCHEMA_VERSION
    if signal.signal_type is None:
        payload["signal_type"] = _serialize_enum(signal.resolved_signal_type)
    if signal.strength is None:
        payload["strength"] = _serialize_enum(signal.resolved_strength)
    return payload


def to_json(signal: TradingSignal, *, omit_nulls: bool = True) -> str:
    """Serialize a trading signal to JSON.

    Args:
        signal: Trading signal instance.
        omit_nulls: Whether to omit ``None`` optional fields.

    Returns:
        JSON string.
    """
    return json.dumps(to_dict(signal, omit_nulls=omit_nulls), sort_keys=True)


def _ensure_supported_schema_version(data: Mapping[str, Any]) -> None:
    version = data.get("schema_version", TRADING_SIGNAL_SCHEMA_VERSION)
    if not isinstance(version, str):
        raise TradingSignalSerializationError(
            "schema_version must be a string.",
            code=ERROR_SERIALIZATION_MALFORMED,
            field="schema_version",
        )
    major = version.split(".", 1)[0]
    if major != TRADING_SIGNAL_SCHEMA_VERSION.split(".", 1)[0]:
        raise TradingSignalSerializationError(
            f"Unsupported schema major version: {version}.",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
            field="schema_version",
        )


def _deserialize_confidence_component(data: Mapping[str, Any]) -> ConfidenceComponent:
    return ConfidenceComponent(
        name=str(data["name"]),
        weight=float(data["weight"]),
        score=float(data["score"]),
        contribution=float(data["contribution"]),
        description=str(data["description"]),
    )


def _deserialize_signal_confidence(data: Mapping[str, Any]) -> SignalConfidence:
    components_raw = data.get("components", [])
    components = tuple(
        _deserialize_confidence_component(item)
        for item in components_raw
        if isinstance(item, Mapping)
    )
    return SignalConfidence(
        score=float(data["score"]),
        band=_enum_from_value(ConfidenceBand, data["band"], field="confidence.band"),
        method=str(data["method"]),
        components=components,
    )


def _deserialize_condition(data: Mapping[str, Any], *, prefix: str) -> EntryCondition:
    raw_value = data.get("value")
    value: float | str | tuple[float, float] | None
    if isinstance(raw_value, list) and len(raw_value) == 2:
        value = (float(raw_value[0]), float(raw_value[1]))
    elif raw_value is None:
        value = None
    else:
        value = raw_value if isinstance(raw_value, str) else float(raw_value)  # type: ignore[arg-type]
    return EntryCondition(
        condition_id=str(data["condition_id"]),
        operator=_enum_from_value(ConditionOperator, data["operator"], field=f"{prefix}.operator"),
        reference=str(data["reference"]),
        value=value,
        met=data.get("met"),
        description=str(data["description"]),
    )


def _deserialize_exit_condition(data: Mapping[str, Any], *, prefix: str) -> ExitCondition:
    entry = _deserialize_condition(data, prefix=prefix)
    return ExitCondition(
        condition_id=entry.condition_id,
        operator=entry.operator,
        reference=entry.reference,
        value=entry.value,
        met=entry.met,
        description=entry.description,
    )


def _deserialize_session_window(data: Mapping[str, Any]) -> SessionWindow:
    return SessionWindow(
        start_time=_parse_time(data["start_time"], field="session_window.start_time"),
        end_time=_parse_time(data["end_time"], field="session_window.end_time"),
        timezone=str(data["timezone"]),
        label=str(data["label"]),
    )


def _deserialize_entry_logic(data: Mapping[str, Any]) -> EntryLogic:
    conditions = tuple(
        _deserialize_condition(item, prefix="entry.conditions")
        for item in data.get("conditions", [])
        if isinstance(item, Mapping)
    )
    window_raw = data.get("preferred_session_window")
    window = _deserialize_session_window(window_raw) if isinstance(window_raw, Mapping) else None
    return EntryLogic(
        trigger_type=_enum_from_value(EntryTriggerType, data["trigger_type"], field="entry.trigger_type"),
        conditions=conditions,
        preferred_session_window=window,
        reentry_allowed=bool(data.get("reentry_allowed", False)),
        notes=data.get("notes"),
    )


def _deserialize_exit_logic(data: Mapping[str, Any]) -> ExitLogic:
    conditions = tuple(
        _deserialize_exit_condition(item, prefix="exit.conditions")
        for item in data.get("conditions", [])
        if isinstance(item, Mapping)
    )
    return ExitLogic(
        trigger_type=_enum_from_value(ExitTriggerType, data["trigger_type"], field="exit.trigger_type"),
        conditions=conditions,
        exit_fraction=float(data["exit_fraction"]) if data.get("exit_fraction") is not None else None,
        roll_to_expiry=data.get("roll_to_expiry"),
        notes=data.get("notes"),
    )


def _deserialize_stop_loss(data: Mapping[str, Any]) -> StopLossHint:
    return StopLossHint(
        hint_type=_enum_from_value(StopLossHintType, data["hint_type"], field="stop_loss.hint_type"),
        reference=str(data["reference"]),
        value=float(data["value"]) if data.get("value") is not None else None,
        value_unit=_enum_from_value(ValueUnit, data["value_unit"], field="stop_loss.value_unit")
        if data.get("value_unit") is not None
        else None,
        basis=data.get("basis"),
        description=data.get("description"),
    )


def _deserialize_target(data: Mapping[str, Any]) -> TargetHint:
    return TargetHint(
        hint_type=_enum_from_value(TargetHintType, data["hint_type"], field="target.hint_type"),
        reference=str(data["reference"]),
        value=float(data["value"]) if data.get("value") is not None else None,
        value_unit=_enum_from_value(ValueUnit, data["value_unit"], field="target.value_unit")
        if data.get("value_unit") is not None
        else None,
        basis=data.get("basis"),
        description=data.get("description"),
    )


def _deserialize_time_validity(data: Mapping[str, Any]) -> SignalTimeValidity:
    return SignalTimeValidity(
        valid_from=_parse_datetime(data["valid_from"], field="time_validity.valid_from")
        if data.get("valid_from") is not None
        else None,
        valid_until=_parse_datetime(data["valid_until"], field="time_validity.valid_until")
        if data.get("valid_until") is not None
        else None,
        session_scope=_enum_from_value(SessionScope, data["session_scope"], field="time_validity.session_scope")
        if data.get("session_scope") is not None
        else None,
        intraday_only=bool(data.get("intraday_only", True)),
        expiry_session_cutoff=_parse_time(data["expiry_session_cutoff"], field="time_validity.expiry_session_cutoff")
        if data.get("expiry_session_cutoff") is not None
        else None,
        timezone=str(data.get("timezone", "Asia/Kolkata")),
    )


def _deserialize_risk(data: Mapping[str, Any]) -> SignalRiskMetadata:
    return SignalRiskMetadata(
        profile=_enum_from_value(RiskProfileHint, data["profile"], field="risk.profile"),
        max_loss_category=data.get("max_loss_category"),
        max_profit_category=data.get("max_profit_category"),
        margin_intensity=_enum_from_value(MarginIntensityHint, data["margin_intensity"], field="risk.margin_intensity")
        if data.get("margin_intensity") is not None
        else None,
        gamma_risk=_enum_from_value(RiskLevelHint, data["gamma_risk"], field="risk.gamma_risk")
        if data.get("gamma_risk") is not None
        else None,
        vega_risk=_enum_from_value(RiskLevelHint, data["vega_risk"], field="risk.vega_risk")
        if data.get("vega_risk") is not None
        else None,
        tail_risk=_enum_from_value(RiskLevelHint, data["tail_risk"], field="risk.tail_risk")
        if data.get("tail_risk") is not None
        else None,
        notes=data.get("notes"),
    )


def _deserialize_strategy_metadata(data: Mapping[str, Any]) -> SignalStrategyMetadata:
    return SignalStrategyMetadata(
        strategy_id=str(data["strategy_id"]),
        strategy_version=str(data["strategy_version"]),
        strategy_family=_enum_from_value(StrategyFamily, data["strategy_family"], field="strategy.strategy_family"),
        display_name=data.get("display_name"),
        plugin_priority=int(data["plugin_priority"]) if data.get("plugin_priority") is not None else None,
        execution_mode=_enum_from_value(StrategyExecutionMode, data["execution_mode"], field="strategy.execution_mode")
        if data.get("execution_mode") is not None
        else None,
    )


def _deserialize_market_context(data: Mapping[str, Any]) -> SignalMarketContext:
    return SignalMarketContext(
        snapshot_id=str(data["snapshot_id"]),
        underlying=str(data["underlying"]),
        expiry=data.get("expiry"),
        spot_at_signal=float(data["spot_at_signal"]) if data.get("spot_at_signal") is not None else None,
        vix_at_signal=float(data["vix_at_signal"]) if data.get("vix_at_signal") is not None else None,
        atm_strike=float(data["atm_strike"]) if data.get("atm_strike") is not None else None,
        snapshot_as_of=_parse_datetime(data["snapshot_as_of"], field="market.snapshot_as_of")
        if data.get("snapshot_as_of") is not None
        else None,
        snapshot_validation_status=data.get("snapshot_validation_status"),
        freshness_status=data.get("freshness_status"),
    )


def _deserialize_structure_hint(data: Mapping[str, Any]) -> StructureHint:
    option_types_raw = data.get("option_types")
    option_types: tuple[OptionType, ...] | None = None
    if isinstance(option_types_raw, list):
        option_types = tuple(
            _enum_from_value(OptionType, item, field="structure_hint.option_types") for item in option_types_raw
        )
    return StructureHint(
        structure_type=str(data["structure_type"]),
        leg_count=int(data["leg_count"]),
        strike_selection_policy=data.get("strike_selection_policy"),
        target_delta=float(data["target_delta"]) if data.get("target_delta") is not None else None,
        strikes_each_side=int(data["strikes_each_side"]) if data.get("strikes_each_side") is not None else None,
        option_types=option_types,
    )


def _deserialize_signal_factor(data: Mapping[str, Any]) -> SignalFactor:
    return SignalFactor(
        name=str(data["name"]),
        weight=float(data["weight"]),
        score=float(data["score"]),
        description=str(data["description"]),
    )


def from_dict(data: Mapping[str, Any]) -> TradingSignal:
    """Deserialize a trading signal from a dictionary.

    Args:
        data: JSON-compatible dictionary.

    Returns:
        Immutable :class:`TradingSignal`.

    Raises:
        TradingSignalSerializationError: When payload is malformed or unsupported.
    """
    _ensure_supported_schema_version(data)
    required = (
        "signal_id",
        "as_of",
        "action",
        "direction",
        "strategy_id",
        "strategy_version",
        "strategy_family",
        "confidence",
        "market",
        "reasons",
    )
    for key in required:
        if key not in data:
            raise TradingSignalSerializationError(
                f"Missing required field '{key}'.",
                code=ERROR_SCHEMA_MISSING_FIELD,
                field=key,
            )

    confidence_raw = data["confidence"]
    market_raw = data["market"]
    if not isinstance(confidence_raw, Mapping) or not isinstance(market_raw, Mapping):
        raise TradingSignalSerializationError(
            "confidence and market must be mappings.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )

    strategy_raw = data.get("strategy")
    entry_raw = data.get("entry")
    exit_raw = data.get("exit")
    stop_raw = data.get("stop_loss")
    target_raw = data.get("target")
    risk_raw = data.get("risk")
    validity_raw = data.get("time_validity")
    structure_raw = data.get("structure_hint")
    metadata_raw = data.get("metadata", {})
    factors_raw = data.get("factors", [])

    signal = TradingSignal(
        signal_id=str(data["signal_id"]),
        as_of=_parse_datetime(data["as_of"], field="as_of"),
        valid_until=_parse_datetime(data["valid_until"], field="valid_until")
        if data.get("valid_until") is not None
        else None,
        action=_enum_from_value(SignalAction, data["action"], field="action"),
        direction=_enum_from_value(SignalDirection, data["direction"], field="direction"),
        signal_type=_enum_from_value(SignalType, data["signal_type"], field="signal_type")
        if data.get("signal_type") is not None
        else None,
        strength=_enum_from_value(SignalStrength, data["strength"], field="strength")
        if data.get("strength") is not None
        else None,
        strategy_id=str(data["strategy_id"]),
        strategy_version=str(data["strategy_version"]),
        strategy_family=_enum_from_value(StrategyFamily, data["strategy_family"], field="strategy_family"),
        strategy=_deserialize_strategy_metadata(strategy_raw)
        if isinstance(strategy_raw, Mapping)
        else None,
        confidence=_deserialize_signal_confidence(confidence_raw),
        market=_deserialize_market_context(market_raw),
        structure_hint=_deserialize_structure_hint(structure_raw)
        if isinstance(structure_raw, Mapping)
        else None,
        entry=_deserialize_entry_logic(entry_raw) if isinstance(entry_raw, Mapping) else None,
        exit=_deserialize_exit_logic(exit_raw) if isinstance(exit_raw, Mapping) else None,
        stop_loss=_deserialize_stop_loss(stop_raw) if isinstance(stop_raw, Mapping) else None,
        target=_deserialize_target(target_raw) if isinstance(target_raw, Mapping) else None,
        risk=_deserialize_risk(risk_raw) if isinstance(risk_raw, Mapping) else None,
        time_validity=_deserialize_time_validity(validity_raw) if isinstance(validity_raw, Mapping) else None,
        reasons=tuple(str(item) for item in data["reasons"]),
        factors=tuple(
            _deserialize_signal_factor(item) for item in factors_raw if isinstance(item, Mapping)
        ),
        metadata=MappingProxyType(dict(metadata_raw)) if isinstance(metadata_raw, Mapping) else MappingProxyType({}),
    )
    return signal


def from_json(text: str) -> TradingSignal:
    """Deserialize a trading signal from JSON.

    Args:
        text: JSON document.

    Returns:
        Immutable :class:`TradingSignal`.

    Raises:
        TradingSignalSerializationError: When JSON is malformed.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TradingSignalSerializationError(
            "Malformed JSON payload.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(payload, dict):
        raise TradingSignalSerializationError(
            "JSON root must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return from_dict(payload)


def bundle_to_dict(bundle: SignalBundle, *, omit_nulls: bool = True) -> dict[str, Any]:
    """Serialize a signal bundle to a dictionary."""
    return {
        "schema_version": TRADING_SIGNAL_SCHEMA_VERSION,
        "signals": [to_dict(signal, omit_nulls=omit_nulls) for signal in bundle.signals],
    }


def bundle_from_dict(data: Mapping[str, Any]) -> SignalBundle:
    """Deserialize a signal bundle from a dictionary."""
    _ensure_supported_schema_version(data)
    raw = data.get("signals", [])
    if not isinstance(raw, list):
        raise TradingSignalSerializationError(
            "signals must be a list.",
            code=ERROR_SERIALIZATION_MALFORMED,
            field="signals",
        )
    return SignalBundle(signals=tuple(from_dict(item) for item in raw if isinstance(item, Mapping)))


def bundle_to_json(bundle: SignalBundle, *, omit_nulls: bool = True) -> str:
    """Serialize a signal bundle to JSON."""
    return json.dumps(bundle_to_dict(bundle, omit_nulls=omit_nulls), sort_keys=True)


def bundle_from_json(text: str) -> SignalBundle:
    """Deserialize a signal bundle from JSON."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TradingSignalSerializationError(
            "Malformed JSON payload.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(payload, dict):
        raise TradingSignalSerializationError(
            "JSON root must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return bundle_from_dict(payload)


def aggregated_to_dict(result: AggregatedSignalResult, *, omit_nulls: bool = True) -> dict[str, Any]:
    """Serialize an aggregated signal result to a dictionary."""
    payload: dict[str, Any] = {
        "schema_version": TRADING_SIGNAL_SCHEMA_VERSION,
        "aggregate_confidence": _serialize_dataclass(result.aggregate_confidence, omit_nulls=omit_nulls),
        "aggregation_metadata": _serialize_dataclass(result.aggregation_metadata, omit_nulls=omit_nulls),
        "secondary_signals": [to_dict(item, omit_nulls=omit_nulls) for item in result.secondary_signals],
        "abstain_signals": [to_dict(item, omit_nulls=omit_nulls) for item in result.abstain_signals],
    }
    if result.primary_signal is not None or not omit_nulls:
        payload["primary_signal"] = (
            to_dict(result.primary_signal, omit_nulls=omit_nulls) if result.primary_signal is not None else None
        )
    if omit_nulls and result.primary_signal is None:
        payload.pop("primary_signal", None)
    return payload


def aggregated_from_dict(data: Mapping[str, Any]) -> AggregatedSignalResult:
    """Deserialize an aggregated signal result from a dictionary."""
    _ensure_supported_schema_version(data)
    confidence_raw = data.get("aggregate_confidence")
    metadata_raw = data.get("aggregation_metadata")
    if not isinstance(confidence_raw, Mapping) or not isinstance(metadata_raw, Mapping):
        raise TradingSignalSerializationError(
            "aggregate_confidence and aggregation_metadata must be mappings.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    primary_raw = data.get("primary_signal")
    secondary_raw = data.get("secondary_signals", [])
    abstain_raw = data.get("abstain_signals", [])
    return AggregatedSignalResult(
        primary_signal=from_dict(primary_raw) if isinstance(primary_raw, Mapping) else None,
        secondary_signals=tuple(
            from_dict(item) for item in secondary_raw if isinstance(item, Mapping)
        ),
        abstain_signals=tuple(
            from_dict(item) for item in abstain_raw if isinstance(item, Mapping)
        ),
        aggregate_confidence=_deserialize_signal_confidence(confidence_raw),
        aggregation_metadata=AggregationMetadata(
            aggregation_mode=_enum_from_value(
                AggregationMode,
                metadata_raw["aggregation_mode"],
                field="aggregation_metadata.aggregation_mode",
            ),
            signal_count=int(metadata_raw["signal_count"]),
            conflict_count=int(metadata_raw.get("conflict_count", 0)),
        ),
    )


def aggregated_to_json(result: AggregatedSignalResult, *, omit_nulls: bool = True) -> str:
    """Serialize an aggregated signal result to JSON."""
    return json.dumps(aggregated_to_dict(result, omit_nulls=omit_nulls), sort_keys=True)


def aggregated_from_json(text: str) -> AggregatedSignalResult:
    """Deserialize an aggregated signal result from JSON."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TradingSignalSerializationError(
            "Malformed JSON payload.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(payload, dict):
        raise TradingSignalSerializationError(
            "JSON root must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return aggregated_from_dict(payload)


__all__ = [
    "TRADING_SIGNAL_SCHEMA_VERSION",
    "ERROR_BUNDLE_DUPLICATE_ID",
    "ERROR_BUNDLE_LIMIT_EXCEEDED",
    "ERROR_DIRECTION_FAMILY_MISMATCH",
    "ERROR_EXPIRED",
    "ERROR_SCHEMA_BAND_MISMATCH",
    "ERROR_SCHEMA_EMPTY_REASONS",
    "ERROR_SCHEMA_INVALID_ENUM",
    "ERROR_SCHEMA_INVALID_EXPIRY",
    "ERROR_SCHEMA_INVALID_ID",
    "ERROR_SCHEMA_INVALID_SCORE",
    "ERROR_SCHEMA_MISSING_FIELD",
    "ERROR_SCHEMA_NAIVE_TIMESTAMP",
    "ERROR_SEMANTIC_FAMILY_CONFLICT",
    "ERROR_SEMANTIC_FORBIDDEN_FIELD",
    "ERROR_SEMANTIC_SNAPSHOT_MISMATCH",
    "ERROR_SEMANTIC_STALE_CONTEXT",
    "ERROR_SEMANTIC_UNDERLYING_MISMATCH",
    "ERROR_SERIALIZATION_MALFORMED",
    "ERROR_SERIALIZATION_UNSUPPORTED_VERSION",
    "AggregatedSignalResult",
    "AggregationMetadata",
    "AggregationMode",
    "ConfidenceBand",
    "ConfidenceComponent",
    "ConditionOperator",
    "EntryCondition",
    "EntryLogic",
    "EntryTriggerType",
    "ExitCondition",
    "ExitLogic",
    "ExitTriggerType",
    "MarginIntensityHint",
    "RiskLevelHint",
    "RiskProfileHint",
    "SessionScope",
    "SessionWindow",
    "SignalAction",
    "SignalBundle",
    "SignalConfidence",
    "SignalDirection",
    "SignalExpirationPolicy",
    "SignalFactor",
    "SignalMarketContext",
    "SignalRiskMetadata",
    "SignalStrategyMetadata",
    "SignalStrength",
    "SignalTimeValidity",
    "SignalType",
    "SignalValidationContext",
    "SignalValidationRecord",
    "SignalValidationResult",
    "StopLossHint",
    "StopLossHintType",
    "StrategyExecutionMode",
    "StrategyFamily",
    "StructureHint",
    "TargetHint",
    "TargetHintType",
    "TradingSignal",
    "TradingSignalExpiredError",
    "TradingSignalSerializationError",
    "TradingSignalValidationError",
    "ValidationPolicy",
    "ValueUnit",
    "aggregated_from_dict",
    "aggregated_from_json",
    "aggregated_to_dict",
    "aggregated_to_json",
    "apply_default_valid_until",
    "are_directions_opposed",
    "assert_signal_fresh",
    "assert_valid_trading_signal",
    "bundle_from_dict",
    "bundle_from_json",
    "bundle_to_dict",
    "bundle_to_json",
    "confidence_band_for_score",
    "from_dict",
    "from_json",
    "infer_signal_strength",
    "infer_signal_type",
    "is_signal_expired",
    "market_context_from_snapshot",
    "remaining_validity_seconds",
    "signal_fingerprint",
    "to_dict",
    "to_json",
    "validate_aggregated_result",
    "validate_signal_bundle",
    "validate_trading_signal",
    "validate_trading_signal_schema",
    "validate_trading_signal_semantics",
]
