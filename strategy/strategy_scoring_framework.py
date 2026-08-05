"""Deterministic universal strategy-suitability scoring boundary.

The framework consumes only caller-extracted factor facts.  It intentionally
does not fetch data, calculate indicators, select strategies, or execute orders.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, Union

STRATEGY_SCORING_FRAMEWORK_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"
SCORE_MIN = 0.0
SCORE_MAX = 100.0
DEFAULT_ROUNDING_DECIMALS = 4
PRODUCER_NAME = "strategy.strategy_scoring_framework"
TOPIC_SCORE_SEALED = "strategy_score.sealed.v1"
TOPIC_SCORE_REJECTED = "strategy_score.rejected.v1"
TOPIC_SCORE_HEALTH = "strategy_score.health.v1"
_EMPTY_STR_MAPPING: Mapping[str, str] = MappingProxyType({})
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")


class FactorCategory(str, Enum):
    """Normative evidence categories."""

    MARKET_REGIME = "MARKET_REGIME"
    TREND_ALIGNMENT = "TREND_ALIGNMENT"
    VOLATILITY = "VOLATILITY"
    LIQUIDITY = "LIQUIDITY"
    GREEKS = "GREEKS"
    RISK_REWARD = "RISK_REWARD"
    EVENT_RISK = "EVENT_RISK"


class RawValueKind(str, Enum):
    """Declared raw-value normalization kinds."""

    SCORE_0_100 = "SCORE_0_100"
    UNIT_INTERVAL = "UNIT_INTERVAL"
    SIGNED_UNIT = "SIGNED_UNIT"
    LABEL = "LABEL"
    RATIO = "RATIO"
    BOOLEAN = "BOOLEAN"


class ValidationSeverity(str, Enum):
    """Validation issue severity."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class ConfidenceBand(str, Enum):
    """Evidence-confidence bands."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class ScoringHealth(str, Enum):
    """Operational health classifications."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    DISABLED = "DISABLED"


class StrategyScoringError(Exception):
    """Base error with a stable framework error code."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class StrategyScoringConfigurationError(StrategyScoringError):
    """Invalid immutable scoring configuration."""


class StrategyScoringValidationError(StrategyScoringError):
    """Invalid score request with its structured validation result."""

    def __init__(
        self,
        message: str,
        code: str,
        result: ValidationResult | None = None,
    ) -> None:
        super().__init__(message, code)
        self.result = result


class StrategyScoringSerializationError(StrategyScoringError):
    """Invalid serialized strategy-score payload."""


class StrategyScoringInvariantError(StrategyScoringError):
    """Internal invariant failure that must fail closed."""


def _freeze_str(mapping: Mapping[str, str] | None) -> Mapping[str, str]:
    return MappingProxyType({str(key): str(value) for key, value in dict(mapping or {}).items()})


def _freeze_float(mapping: Mapping[Any, float]) -> Mapping[Any, float]:
    return MappingProxyType({key: float(value) for key, value in dict(mapping).items()})


@dataclass(frozen=True)
class FactorInput:
    """One extracted and auditable strategy-suitability fact."""

    category: FactorCategory
    factor_id: str
    raw_value: Union[str, float, int, bool]
    raw_value_kind: RawValueKind
    provenance: str
    reliability: float | None = None
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class FactorInputBundle:
    """Immutable collection of extracted factor inputs."""

    factors: tuple[FactorInput, ...]


@dataclass(frozen=True)
class ScoreRequest:
    """A request to calculate one strategy suitability score."""

    strategy_id: str
    factors: tuple[FactorInput, ...] | FactorInputBundle
    profile_name: str | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_STR_MAPPING)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_str(self.metadata))


@dataclass(frozen=True)
class WeightProfile:
    """Revisioned immutable score weights and normalization mappings."""

    name: str
    revision: str
    weights: Mapping[FactorCategory, float]
    label_maps: Mapping[FactorCategory, Mapping[str, float]] = field(default_factory=dict)
    boolean_maps: Mapping[FactorCategory, Mapping[bool, float]] = field(default_factory=dict)
    ratio_curves: Mapping[FactorCategory, tuple[tuple[float, float], ...]] = field(
        default_factory=dict
    )
    optional_categories: frozenset[FactorCategory] = frozenset()
    allow_endpoint_clamp: bool = False
    allow_default_reliability: bool = True
    max_penalty: float = 0.0

    def __post_init__(self) -> None:
        weights = _freeze_float(self.weights)
        if set(weights) != set(FactorCategory):
            raise StrategyScoringConfigurationError(
                "profiles require every category weight", "SSF.CFG.002"
            )
        if any(value < 0.0 or not math.isfinite(value) for value in weights.values()):
            raise StrategyScoringConfigurationError("invalid profile weight", "SSF.CFG.002")
        if sum(Decimal(str(value)) for value in weights.values()) != _HUNDRED:
            raise StrategyScoringConfigurationError(
                "profile weights must total 100", "SSF.CFG.002"
            )
        label_maps = MappingProxyType(
            {
                FactorCategory(category): MappingProxyType(
                    {str(key): float(value) for key, value in values.items()}
                )
                for category, values in self.label_maps.items()
            }
        )
        boolean_maps = MappingProxyType(
            {
                FactorCategory(category): MappingProxyType(
                    {bool(key): float(value) for key, value in values.items()}
                )
                for category, values in self.boolean_maps.items()
            }
        )
        curves = MappingProxyType(
            {
                FactorCategory(category): tuple((float(x), float(y)) for x, y in values)
                for category, values in self.ratio_curves.items()
            }
        )
        for curve in curves.values():
            if len(curve) < 2 or any(
                right[0] <= left[0] or not 0.0 <= right[1] <= 100.0
                for left, right in zip(curve, curve[1:])
            ) or not 0.0 <= curve[0][1] <= 100.0:
                raise StrategyScoringConfigurationError("invalid ratio curve", "SSF.CFG.002")
        if not self.name or not self.revision or not 0.0 <= self.max_penalty <= 100.0:
            raise StrategyScoringConfigurationError("invalid profile", "SSF.CFG.002")
        object.__setattr__(self, "weights", weights)
        object.__setattr__(self, "label_maps", label_maps)
        object.__setattr__(self, "boolean_maps", boolean_maps)
        object.__setattr__(self, "ratio_curves", curves)
        object.__setattr__(self, "optional_categories", frozenset(self.optional_categories))


def _profile(name: str, values: tuple[float, ...]) -> WeightProfile:
    """Build one documented built-in profile."""
    return WeightProfile(
        name=name,
        revision="1",
        weights=dict(zip(FactorCategory, values)),
        label_maps={
            FactorCategory.MARKET_REGIME: {
                "TRENDING_UP": 85.0,
                "TRENDING_DOWN": 25.0,
                "RANGE_BOUND": 70.0,
                "HIGH_VOL": 45.0,
                "LOW_VOL": 60.0,
                "UNKNOWN": 0.0,
            }
        },
        # True means an adverse event is present; False means no known event.
        boolean_maps={FactorCategory.EVENT_RISK: {True: 0.0, False: 100.0}},
        ratio_curves={FactorCategory.RISK_REWARD: ((0.0, 0.0), (1.0, 50.0), (2.0, 75.0), (3.0, 90.0), (5.0, 100.0))},
    )


BALANCED = _profile("BALANCED", (15, 15, 15, 15, 10, 20, 10))
PREMIUM_SELLING = _profile("PREMIUM_SELLING", (15, 10, 20, 20, 10, 15, 10))
DEFINED_RISK = _profile("DEFINED_RISK", (15, 15, 15, 10, 10, 25, 10))
DIRECTIONAL = _profile("DIRECTIONAL", (10, 25, 10, 15, 10, 20, 10))
EVENT_CAUTION = _profile("EVENT_CAUTION", (10, 10, 15, 10, 10, 15, 30))


@dataclass(frozen=True)
class ScoringFrameworkConfig:
    """Immutable composition-root configuration."""

    schema_version: str = SCHEMA_VERSION
    default_profile: str = "BALANCED"
    rounding_decimals: int = DEFAULT_ROUNDING_DECIMALS
    minimum_category_coverage: float = 0.80
    allow_optional_factor_omission: bool = True
    enable_statistics: bool = True
    enable_cache: bool = False
    cache_capacity: int = 256
    event_sink: ScoringEventSink | None = None
    profiles: Mapping[str, WeightProfile] = field(default_factory=dict)

    def __post_init__(self) -> None:
        profiles = dict(self.profiles) or {
            item.name: item
            for item in (BALANCED, PREMIUM_SELLING, DEFINED_RISK, DIRECTIONAL, EVENT_CAUTION)
        }
        if self.schema_version != SCHEMA_VERSION:
            raise StrategyScoringConfigurationError("unsupported schema", "SSF.CFG.001")
        if self.default_profile not in profiles:
            raise StrategyScoringConfigurationError("unknown default profile", "SSF.CFG.003")
        if not 0 <= self.rounding_decimals <= 8 or not 0 < self.minimum_category_coverage <= 1:
            raise StrategyScoringConfigurationError("invalid configuration threshold", "SSF.CFG.001")
        if not 0 <= self.cache_capacity <= 4096:
            raise StrategyScoringConfigurationError("invalid cache capacity", "SSF.CFG.001")
        object.__setattr__(self, "profiles", MappingProxyType(profiles))


def default_scoring_framework_config() -> ScoringFrameworkConfig:
    """Return the documented balanced default configuration."""
    return ScoringFrameworkConfig()


@dataclass(frozen=True)
class ValidationIssue:
    """One stable request-validation issue."""

    code: str
    severity: ValidationSeverity
    message: str
    factor_id: str | None = None
    category: FactorCategory | None = None


@dataclass(frozen=True)
class NormalizedFactorInput:
    """Validated factor with its Decimal-derived sealed score."""

    category: FactorCategory
    factor_id: str
    raw_value: Union[str, float, int, bool]
    raw_value_kind: RawValueKind
    normalized_score: float
    provenance: str
    reliability: float
    quality_flags: tuple[str, ...] = ()
    validation_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationResult:
    """Structured outcome of score-request validation."""

    is_valid: bool
    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]
    normalized_inputs: tuple[NormalizedFactorInput, ...]


@dataclass(frozen=True)
class FactorScore:
    """One normalized factor's contribution to the sealed score."""

    category: FactorCategory
    factor_id: str
    raw_value: Union[str, float, int, bool]
    raw_value_kind: RawValueKind
    normalized_score: float
    weight: float
    contribution: float
    provenance: str
    validation_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConfidenceReport:
    """Evidence quality associated with a sealed score."""

    score: float
    band: ConfidenceBand
    coverage: float
    agreement: float
    data_quality: float
    penalties: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class StrategyExplanation:
    """Stable, template-driven explanation of sealed evidence."""

    summary: str
    strengths: tuple[str, ...]
    concerns: tuple[str, ...]
    factor_narratives: tuple[str, ...]
    methodology_version: str


@dataclass(frozen=True)
class StrategyScore:
    """Immutable sealed universal strategy suitability artifact."""

    strategy_id: str
    profile_name: str
    profile_revision: str
    overall_score: float
    factor_scores: tuple[FactorScore, ...]
    confidence: ConfidenceReport
    explanation: StrategyExplanation
    schema_version: str
    sealed_at: datetime
    input_fingerprint: str


@dataclass(frozen=True)
class ScoringStatistics:
    """Immutable process-local scoring counters."""

    requests: int = 0
    sealed: int = 0
    rejected: int = 0
    cache_hits: int = 0
    band_counts: Mapping[str, int] = field(default_factory=dict)
    category_omissions: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "band_counts", MappingProxyType(dict(self.band_counts)))
        object.__setattr__(self, "category_omissions", MappingProxyType(dict(self.category_omissions)))


@dataclass(frozen=True)
class ScoringHealthReport:
    """Immutable operational health projection."""

    health: ScoringHealth
    event_sink_failures: int
    statistics: ScoringStatistics


class FactorProvider(Protocol):
    """Supplies extracted factors; it is never invoked implicitly."""

    def provide(self, strategy_id: str) -> FactorInputBundle:
        """Return factors for a strategy."""


class StrategyScorer(Protocol):
    """Seals a validated universal strategy score."""

    def score(self, request: ScoreRequest) -> StrategyScore:
        """Seal the supplied request."""


class ScoringEventSink(Protocol):
    """Observational sink for sealed framework events."""

    def publish(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Publish a public immutable artifact."""


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid numeric value") from exc
    if not result.is_finite():
        raise ValueError("non-finite numeric value")
    return result


def _seal(value: Decimal, decimals: int) -> float:
    value = value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_EVEN)
    if not _ZERO <= value <= _HUNDRED:
        raise StrategyScoringInvariantError("score outside sealed range", "SSF.INT.001")
    return float(value)


def _format(value: float, decimals: int = DEFAULT_ROUNDING_DECIMALS) -> str:
    return format(Decimal(str(value)), f".{decimals}f")


class StrategyScoringFramework:
    """Stateless scoring facade with lock-protected optional observability."""

    def __init__(
        self,
        config: ScoringFrameworkConfig,
        *,
        clock: Callable[[], datetime] | None = None,
        event_sink: ScoringEventSink | None = None,
    ) -> None:
        self._config = config
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._event_sink = event_sink if event_sink is not None else config.event_sink
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, StrategyScore] = OrderedDict()
        self._counters: dict[str, Any] = {
            "requests": 0, "sealed": 0, "rejected": 0, "cache_hits": 0,
            "bands": {}, "omissions": {}, "sink_failures": 0,
        }

    def _profile(self, request: ScoreRequest) -> WeightProfile | None:
        return self._config.profiles.get(request.profile_name or self._config.default_profile)

    def _issue(
        self, code: str, message: str, *, factor: FactorInput | None = None,
        severity: ValidationSeverity = ValidationSeverity.ERROR,
    ) -> ValidationIssue:
        return ValidationIssue(
            code, severity, message, factor.factor_id if factor else None,
            factor.category if factor else None,
        )

    def _normalize(self, factor: FactorInput, profile: WeightProfile) -> NormalizedFactorInput:
        if not isinstance(factor.category, FactorCategory) or not isinstance(
            factor.raw_value_kind, RawValueKind
        ):
            raise StrategyScoringValidationError("invalid raw kind", "SSF.VAL.006")
        if not factor.factor_id or len(factor.factor_id) > 128:
            raise StrategyScoringValidationError("invalid factor id", "SSF.VAL.004")
        if not factor.provenance or len(factor.provenance) > 256:
            raise StrategyScoringValidationError("invalid provenance", "SSF.VAL.007")
        reliability = factor.reliability
        if reliability is None:
            if not profile.allow_default_reliability:
                raise StrategyScoringValidationError("reliability required", "SSF.VAL.008")
            reliability = 1.0
        try:
            reliable = _decimal(reliability)
        except ValueError as exc:
            raise StrategyScoringValidationError("invalid reliability", "SSF.VAL.008") from exc
        if not _ZERO < reliable <= Decimal("1"):
            raise StrategyScoringValidationError("invalid reliability", "SSF.VAL.008")
        kind = factor.raw_value_kind
        notes: tuple[str, ...] = ()
        try:
            if kind is RawValueKind.LABEL:
                if not isinstance(factor.raw_value, str):
                    raise StrategyScoringValidationError("label requires string", "SSF.VAL.006")
                mapping = profile.label_maps.get(factor.category, {})
                if factor.raw_value not in mapping:
                    raise StrategyScoringValidationError("unknown label", "SSF.NORM.002")
                score = _decimal(mapping[factor.raw_value])
                notes = ("label_map",)
            elif kind is RawValueKind.BOOLEAN:
                if not isinstance(factor.raw_value, bool):
                    raise StrategyScoringValidationError("boolean required", "SSF.VAL.006")
                mapping = profile.boolean_maps.get(factor.category)
                if mapping is None or factor.raw_value not in mapping:
                    raise StrategyScoringValidationError("unsupported boolean category", "SSF.VAL.006")
                score = _decimal(mapping[factor.raw_value])
                notes = ("boolean_map",)
            else:
                raw = _decimal(factor.raw_value)
                if kind is RawValueKind.SCORE_0_100:
                    if not _ZERO <= raw <= _HUNDRED:
                        raise ValueError
                    score, notes = raw, ("score_0_100",)
                elif kind is RawValueKind.UNIT_INTERVAL:
                    if not _ZERO <= raw <= Decimal("1"):
                        raise ValueError
                    score, notes = raw * _HUNDRED, ("unit_interval",)
                elif kind is RawValueKind.SIGNED_UNIT:
                    if factor.category is not FactorCategory.TREND_ALIGNMENT:
                        raise StrategyScoringValidationError("signed unit is trend-only", "SSF.VAL.006")
                    if not Decimal("-1") <= raw <= Decimal("1"):
                        raise ValueError
                    score, notes = (raw + Decimal("1")) * Decimal("50"), ("signed_unit",)
                elif kind is RawValueKind.RATIO:
                    if raw < _ZERO:
                        raise ValueError
                    curve = profile.ratio_curves.get(factor.category)
                    if curve is None:
                        raise StrategyScoringValidationError("missing ratio curve", "SSF.NORM.003")
                    score, notes = self._interpolate(raw, curve, profile.allow_endpoint_clamp), ("ratio_curve",)
                else:
                    raise StrategyScoringValidationError("unsupported raw kind", "SSF.VAL.006")
        except ValueError as exc:
            raise StrategyScoringValidationError("value outside declared domain", "SSF.NORM.001") from exc
        return NormalizedFactorInput(
            factor.category, factor.factor_id, factor.raw_value, kind, _seal(score, self._config.rounding_decimals),
            factor.provenance, float(reliable), tuple(sorted(set(factor.quality_flags))), notes,
        )

    @staticmethod
    def _interpolate(
        raw: Decimal, curve: tuple[tuple[float, float], ...], allow_clamp: bool
    ) -> Decimal:
        points = tuple((_decimal(x), _decimal(y)) for x, y in curve)
        if raw <= points[0][0]:
            return points[0][1]
        if raw > points[-1][0]:
            if not allow_clamp:
                raise ValueError("ratio endpoint")
            return points[-1][1]
        for left, right in zip(points, points[1:]):
            if raw <= right[0]:
                return left[1] + (raw - left[0]) * (right[1] - left[1]) / (right[0] - left[0])
        raise StrategyScoringInvariantError("unreachable curve", "SSF.INT.001")

    def validate(self, request: ScoreRequest) -> ValidationResult:
        """Validate and normalize a request without mutating framework state."""
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        normalized: list[NormalizedFactorInput] = []
        if not isinstance(request, ScoreRequest) or not request.strategy_id or len(request.strategy_id) > 128:
            errors.append(self._issue("SSF.VAL.001", "invalid strategy identifier"))
            return ValidationResult(False, tuple(errors), (), ())
        profile = self._profile(request)
        if profile is None:
            errors.append(self._issue("SSF.CFG.003", "unknown profile"))
            return ValidationResult(False, tuple(errors), (), ())
        factors = request.factors.factors if isinstance(request.factors, FactorInputBundle) else request.factors
        if not factors:
            errors.append(self._issue("SSF.VAL.002", "no factor inputs"))
        if len(request.metadata) > 32 or any(len(key) > 128 or len(value) > 1024 for key, value in request.metadata.items()):
            errors.append(self._issue("SSF.VAL.001", "invalid metadata"))
        seen: set[tuple[FactorCategory, str]] = set()
        for factor in factors:
            if not isinstance(factor, FactorInput):
                errors.append(self._issue("SSF.VAL.006", "invalid factor input"))
                continue
            key = (factor.category, factor.factor_id)
            if key in seen:
                errors.append(self._issue("SSF.VAL.004", "duplicate factor id", factor=factor))
                continue
            seen.add(key)
            try:
                normalized.append(self._normalize(factor, profile))
            except StrategyScoringValidationError as exc:
                errors.append(self._issue(exc.code, exc.message, factor=factor))
        available = {item.category for item in normalized}
        for category in FactorCategory:
            if category not in available:
                if category in profile.optional_categories and self._config.allow_optional_factor_omission:
                    warnings.append(self._issue(
                        "SSF.VAL.003", "optional category omitted", severity=ValidationSeverity.WARNING
                    ))
                else:
                    errors.append(self._issue("SSF.VAL.003", "missing required category"))
        coverage = sum(
            (_decimal(profile.weights[category]) for category in available), _ZERO
        ) / _HUNDRED
        if coverage < _decimal(self._config.minimum_category_coverage):
            errors.append(self._issue("SSF.VAL.003", "minimum category coverage not met"))
        ordered = tuple(sorted(normalized, key=lambda item: (list(FactorCategory).index(item.category), item.factor_id)))
        return ValidationResult(not errors, tuple(errors), tuple(warnings), ordered)

    def _weights(
        self, normalized: tuple[NormalizedFactorInput, ...], profile: WeightProfile
    ) -> tuple[dict[FactorCategory, Decimal], tuple[FactorCategory, ...]]:
        available = {item.category for item in normalized}
        omitted = tuple(category for category in profile.optional_categories if category not in available)
        weights = {category: _decimal(profile.weights[category]) for category in FactorCategory}
        if omitted and self._config.allow_optional_factor_omission:
            released = sum((weights[category] for category in omitted), _ZERO)
            recipients = [
                category for category in FactorCategory
                if category in available and category is not FactorCategory.EVENT_RISK
            ]
            base = sum((weights[category] for category in recipients), _ZERO)
            if not recipients or base == _ZERO:
                raise StrategyScoringInvariantError("no usable category weight", "SSF.AGG.001")
            for category in omitted:
                weights[category] = _ZERO
            for category in recipients:
                weights[category] += released * weights[category] / base
        if sum(weights.values(), _ZERO) != _HUNDRED:
            raise StrategyScoringInvariantError("weights do not reconcile", "SSF.INT.001")
        return weights, omitted

    def _confidence(
        self, category_scores: Mapping[FactorCategory, Decimal], weights: Mapping[FactorCategory, Decimal],
        normalized: tuple[NormalizedFactorInput, ...], overall: Decimal,
    ) -> ConfidenceReport:
        coverage = sum((weights[category] for category in category_scores), _ZERO)
        mad = sum(
            (abs(score - overall) * weights[category] / _HUNDRED for category, score in category_scores.items()),
            _ZERO,
        )
        agreement = max(_ZERO, _HUNDRED - mad)
        penalties: list[str] = []
        quality = _HUNDRED
        deductions = {"stale": Decimal("15"), "imprecise": Decimal("10"), "degraded": Decimal("20"), "warning": Decimal("5")}
        for item in normalized:
            for flag in item.quality_flags:
                deduction = deductions.get(flag.lower(), Decimal("5"))
                quality -= deduction
                penalties.append(f"QUALITY_{flag.upper()}")
        quality = max(_ZERO, quality)
        confidence = max(_ZERO, min(_HUNDRED, Decimal("0.45") * coverage + Decimal("0.30") * agreement + Decimal("0.25") * quality))
        sealed = _seal(confidence, self._config.rounding_decimals)
        band = self._confidence_band(sealed)
        return ConfidenceReport(
            sealed, band, _seal(coverage, self._config.rounding_decimals),
            _seal(agreement, self._config.rounding_decimals), _seal(quality, self._config.rounding_decimals),
            tuple(sorted(set(penalties))),
            (f"coverage={_format(_seal(coverage, self._config.rounding_decimals))}",
             f"agreement={_format(_seal(agreement, self._config.rounding_decimals))}",
             f"data_quality={_format(_seal(quality, self._config.rounding_decimals))}"),
        )

    @staticmethod
    def _confidence_band(value: float) -> ConfidenceBand:
        if value < 45.0:
            return ConfidenceBand.LOW
        if value < 70.0:
            return ConfidenceBand.MEDIUM
        if value < 85.0:
            return ConfidenceBand.HIGH
        return ConfidenceBand.VERY_HIGH

    def _explanation(
        self, strategy_id: str, profile: WeightProfile, overall: float, factors: tuple[FactorScore, ...],
        confidence: ConfidenceReport, omitted: tuple[FactorCategory, ...],
    ) -> StrategyExplanation:
        strengths = tuple(
            f"{item.category.value} is a strength because its normalized score is {_format(item.normalized_score)}."
            for item in factors if item.normalized_score >= 70.0
        )
        concerns = tuple(
            f"{item.category.value} is a concern because its normalized score is {_format(item.normalized_score)}."
            for item in factors if item.normalized_score < 45.0 or item.validation_notes
            and any(note.startswith("quality") for note in item.validation_notes)
        )
        narratives = tuple(
            f"Factor {item.factor_id} in {item.category.value} normalized to {_format(item.normalized_score)}; "
            f"effective category weight is {_format(item.weight)} and contribution is {_format(item.contribution)}."
            for item in factors
        )
        omission = " Weights were redistributed for optional omissions." if omitted else ""
        return StrategyExplanation(
            f"Strategy {strategy_id} scored {_format(overall)}/100 under {profile.name}; confidence is "
            f"{confidence.band.value} ({_format(confidence.score)}/100) because coverage={_format(confidence.coverage)}, "
            f"agreement={_format(confidence.agreement)}, data_quality={_format(confidence.data_quality)}.{omission}",
            strengths, concerns, narratives, STRATEGY_SCORING_FRAMEWORK_VERSION,
        )

    def _fingerprint(self, strategy_id: str, profile: WeightProfile, factors: tuple[FactorScore, ...]) -> str:
        payload = {
            "schema_version": SCHEMA_VERSION, "strategy_id": strategy_id, "profile_name": profile.name,
            "profile_revision": profile.revision,
            "factors": [
                {"category": item.category.value, "factor_id": item.factor_id, "raw_value": item.raw_value,
                 "raw_value_kind": item.raw_value_kind.value, "provenance": item.provenance}
                for item in factors
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()

    def score(self, request: ScoreRequest) -> StrategyScore:
        """Validate, aggregate, explain, and seal a strategy score."""
        with self._lock:
            if self._config.enable_statistics:
                self._counters["requests"] += 1
        result = self.validate(request)
        profile = self._profile(request)
        assert profile is not None or not result.is_valid
        if not result.is_valid:
            code = result.errors[0].code
            self._record_rejection(code, request)
            raise StrategyScoringValidationError("invalid score request", code, result)
        assert profile is not None
        key = self._cache_key(request, profile)
        if self._config.enable_cache:
            with self._lock:
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    self._counters["cache_hits"] += 1
                    return cached
        weights, omitted = self._weights(result.normalized_inputs, profile)
        grouped: dict[FactorCategory, list[NormalizedFactorInput]] = {category: [] for category in FactorCategory}
        for item in result.normalized_inputs:
            grouped[item.category].append(item)
        category_scores: dict[FactorCategory, Decimal] = {}
        for category in FactorCategory:
            items = grouped[category]
            if items:
                denominator = sum((_decimal(item.reliability) for item in items), _ZERO)
                category_scores[category] = sum(
                    (_decimal(item.normalized_score) * _decimal(item.reliability) for item in items), _ZERO
                ) / denominator
        overall_decimal = sum(
            (category_scores[category] * weights[category] / _HUNDRED for category in category_scores), _ZERO
        )
        overall_decimal = max(_ZERO, min(_HUNDRED, overall_decimal - min(_decimal(profile.max_penalty), overall_decimal)))
        overall = _seal(overall_decimal, self._config.rounding_decimals)
        factor_scores = tuple(
            FactorScore(
                item.category, item.factor_id, item.raw_value, item.raw_value_kind, item.normalized_score,
                _seal(weights[item.category], self._config.rounding_decimals),
                _seal(_decimal(item.normalized_score) * weights[item.category] / _HUNDRED, self._config.rounding_decimals),
                item.provenance, item.validation_notes,
            )
            for item in result.normalized_inputs
        )
        confidence = self._confidence(category_scores, weights, result.normalized_inputs, Decimal(str(overall)))
        explanation = self._explanation(request.strategy_id, profile, overall, factor_scores, confidence, omitted)
        now = self._clock()
        if now.tzinfo is None:
            raise StrategyScoringInvariantError("clock must return aware datetime", "SSF.INT.001")
        score = StrategyScore(
            request.strategy_id, profile.name, profile.revision, overall, factor_scores, confidence, explanation,
            SCHEMA_VERSION, now.astimezone(timezone.utc), self._fingerprint(request.strategy_id, profile, factor_scores),
        )
        self._record_success(score, key, omitted)
        self._publish(TOPIC_SCORE_SEALED, {"score": self.serialize(score)})
        return score

    def _cache_key(self, request: ScoreRequest, profile: WeightProfile) -> str:
        factors = request.factors.factors if isinstance(request.factors, FactorInputBundle) else request.factors
        source = repr((SCHEMA_VERSION, profile.name, profile.revision, self._config.rounding_decimals, request.strategy_id, factors))
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def _record_rejection(self, code: str, request: ScoreRequest) -> None:
        with self._lock:
            if self._config.enable_statistics:
                self._counters["rejected"] += 1
        self._publish(TOPIC_SCORE_REJECTED, {
            "code": code, "profile": request.profile_name or self._config.default_profile,
            "strategy_id_hash": hashlib.sha256(request.strategy_id.encode("utf-8")).hexdigest(),
        })

    def _record_success(self, score: StrategyScore, key: str, omitted: tuple[FactorCategory, ...]) -> None:
        with self._lock:
            if self._config.enable_cache:
                self._cache[key] = score
                self._cache.move_to_end(key)
                while len(self._cache) > self._config.cache_capacity:
                    self._cache.popitem(last=False)
            if self._config.enable_statistics:
                self._counters["sealed"] += 1
                bands = self._counters["bands"]
                bands[score.confidence.band.value] = bands.get(score.confidence.band.value, 0) + 1
                for category in omitted:
                    omissions = self._counters["omissions"]
                    omissions[category.value] = omissions.get(category.value, 0) + 1

    def _publish(self, topic: str, payload: Mapping[str, Any]) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink.publish(topic, payload)
        except Exception:
            with self._lock:
                self._counters["sink_failures"] += 1

    def explain(self, score: StrategyScore) -> StrategyExplanation:
        """Return the explanation sealed with ``score``."""
        return score.explanation

    def serialize(self, score: StrategyScore) -> str:
        """Serialize a sealed score to compact, canonical JSON."""
        return json.dumps(self._score_dict(score), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def _score_dict(self, score: StrategyScore) -> dict[str, Any]:
        return {
            "schema_version": score.schema_version, "strategy_id": score.strategy_id,
            "profile_name": score.profile_name, "profile_revision": score.profile_revision,
            "overall_score": _format(score.overall_score, self._config.rounding_decimals),
            "factor_scores": [
                {"category": item.category.value, "factor_id": item.factor_id, "raw_value": item.raw_value,
                 "raw_value_kind": item.raw_value_kind.value, "normalized_score": _format(item.normalized_score, self._config.rounding_decimals),
                 "weight": _format(item.weight, self._config.rounding_decimals), "contribution": _format(item.contribution, self._config.rounding_decimals),
                 "provenance": item.provenance, "validation_notes": list(item.validation_notes)}
                for item in score.factor_scores
            ],
            "confidence": {"score": _format(score.confidence.score, self._config.rounding_decimals), "band": score.confidence.band.value,
                           "coverage": _format(score.confidence.coverage, self._config.rounding_decimals), "agreement": _format(score.confidence.agreement, self._config.rounding_decimals),
                           "data_quality": _format(score.confidence.data_quality, self._config.rounding_decimals), "penalties": list(score.confidence.penalties), "reasons": list(score.confidence.reasons)},
            "explanation": {"summary": score.explanation.summary, "strengths": list(score.explanation.strengths), "concerns": list(score.explanation.concerns),
                            "factor_narratives": list(score.explanation.factor_narratives), "methodology_version": score.explanation.methodology_version},
            "sealed_at": score.sealed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "input_fingerprint": score.input_fingerprint,
        }

    def deserialize(self, payload: str) -> StrategyScore:
        """Deserialize, range-check, and fingerprint-verify canonical JSON."""
        try:
            if len(payload) > 1_000_000:
                raise ValueError("oversized")
            document = json.loads(payload)
            if document["schema_version"].split(".", 1)[0] != SCHEMA_VERSION.split(".", 1)[0]:
                raise StrategyScoringSerializationError("unsupported schema", "SSF.SER.002")
            profile = self._config.profiles.get(document["profile_name"])
            if profile is None or profile.revision != document["profile_revision"]:
                raise StrategyScoringSerializationError("unknown profile", "SSF.CFG.003")
            factors = tuple(
                FactorScore(FactorCategory(item["category"]), str(item["factor_id"]), item["raw_value"], RawValueKind(item["raw_value_kind"]),
                            self._deserialize_score(item["normalized_score"]), self._deserialize_score(item["weight"]),
                            self._deserialize_score(item["contribution"]), str(item["provenance"]), tuple(item["validation_notes"]))
                for item in document["factor_scores"]
            )
            confidence_data = document["confidence"]
            confidence = ConfidenceReport(
                self._deserialize_score(confidence_data["score"]), ConfidenceBand(confidence_data["band"]),
                self._deserialize_score(confidence_data["coverage"]), self._deserialize_score(confidence_data["agreement"]),
                self._deserialize_score(confidence_data["data_quality"]), tuple(confidence_data["penalties"]), tuple(confidence_data["reasons"]),
            )
            explanation_data = document["explanation"]
            explanation = StrategyExplanation(explanation_data["summary"], tuple(explanation_data["strengths"]), tuple(explanation_data["concerns"]),
                                              tuple(explanation_data["factor_narratives"]), explanation_data["methodology_version"])
            score = StrategyScore(
                str(document["strategy_id"]), profile.name, profile.revision, self._deserialize_score(document["overall_score"]), factors,
                confidence, explanation, SCHEMA_VERSION,
                datetime.fromisoformat(document["sealed_at"].replace("Z", "+00:00")).astimezone(timezone.utc),
                str(document["input_fingerprint"]),
            )
            if self._fingerprint(score.strategy_id, profile, factors) != score.input_fingerprint:
                raise StrategyScoringSerializationError("fingerprint mismatch", "SSF.SER.003")
            return score
        except StrategyScoringSerializationError:
            raise
        except StrategyScoringError as exc:
            raise StrategyScoringSerializationError(
                "malformed strategy score", "SSF.SER.001"
            ) from exc
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StrategyScoringSerializationError("malformed strategy score", "SSF.SER.001") from exc

    def _deserialize_score(self, value: Any) -> float:
        return _seal(_decimal(value), self._config.rounding_decimals)

    def statistics(self) -> ScoringStatistics:
        """Return an immutable snapshot of process-local counters."""
        with self._lock:
            if not self._config.enable_statistics:
                return ScoringStatistics()
            return ScoringStatistics(
                self._counters["requests"], self._counters["sealed"], self._counters["rejected"],
                self._counters["cache_hits"], dict(self._counters["bands"]), dict(self._counters["omissions"]),
            )

    def health(self) -> ScoringHealthReport:
        """Return operational health without affecting score calculations."""
        stats = self.statistics()
        if not self._config.enable_statistics:
            state = ScoringHealth.DISABLED
        elif self._counters["sink_failures"] or stats.rejected > stats.sealed:
            state = ScoringHealth.DEGRADED
        else:
            state = ScoringHealth.HEALTHY
        report = ScoringHealthReport(state, self._counters["sink_failures"], stats)
        self._publish(TOPIC_SCORE_HEALTH, {"health": report.health.value})
        return report


__all__ = [
    "STRATEGY_SCORING_FRAMEWORK_VERSION", "SCHEMA_VERSION", "SCORE_MIN", "SCORE_MAX",
    "DEFAULT_ROUNDING_DECIMALS", "PRODUCER_NAME", "TOPIC_SCORE_SEALED", "TOPIC_SCORE_REJECTED",
    "TOPIC_SCORE_HEALTH", "FactorCategory", "RawValueKind", "ValidationSeverity", "ConfidenceBand",
    "ScoringHealth", "StrategyScoringError", "StrategyScoringConfigurationError",
    "StrategyScoringValidationError", "StrategyScoringSerializationError", "StrategyScoringInvariantError",
    "FactorInput", "FactorInputBundle", "ScoreRequest", "WeightProfile", "ScoringFrameworkConfig",
    "FactorScore", "ConfidenceReport", "StrategyExplanation", "StrategyScore", "ValidationIssue",
    "ValidationResult", "NormalizedFactorInput", "ScoringStatistics", "ScoringHealthReport",
    "FactorProvider", "StrategyScorer", "ScoringEventSink", "StrategyScoringFramework",
    "default_scoring_framework_config", "BALANCED", "PREMIUM_SELLING", "DEFINED_RISK",
    "DIRECTIONAL", "EVENT_CAUTION",
]
