"""Tests for the deterministic strategy scoring framework."""

from __future__ import annotations

import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone

import pytest

from strategy.strategy_scoring_framework import (
    BALANCED,
    ConfidenceBand,
    FactorCategory,
    FactorInput,
    FactorInputBundle,
    RawValueKind,
    ScoreRequest,
    ScoringFrameworkConfig,
    ScoringHealth,
    StrategyScoringConfigurationError,
    StrategyScoringFramework,
    StrategyScoringSerializationError,
    StrategyScoringValidationError,
    WeightProfile,
)

NOW = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)


def fixed_clock() -> datetime:
    """Return the fixed test audit timestamp."""
    return NOW


def factor(
    category: FactorCategory,
    value: object = 80.0,
    kind: RawValueKind = RawValueKind.SCORE_0_100,
    **kwargs: object,
) -> FactorInput:
    """Build a valid factor with a stable identity."""
    return FactorInput(
        category=category,
        factor_id=kwargs.pop("factor_id", category.value.lower()),  # type: ignore[arg-type]
        raw_value=value,  # type: ignore[arg-type]
        raw_value_kind=kind,
        provenance=kwargs.pop("provenance", "test:plugin"),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def full_factors(value: float = 80.0) -> tuple[FactorInput, ...]:
    """Build all seven normative category inputs."""
    return tuple(factor(category, value) for category in FactorCategory)


def framework(**kwargs: object) -> StrategyScoringFramework:
    """Build a deterministic framework."""
    return StrategyScoringFramework(ScoringFrameworkConfig(**kwargs), clock=fixed_clock)


def request(factors: tuple[FactorInput, ...] | None = None, **kwargs: object) -> ScoreRequest:
    """Build a valid balanced request."""
    return ScoreRequest("iron_condor", factors or full_factors(), **kwargs)  # type: ignore[arg-type]


def test_tst_ssf_001_default_balanced_score_all_categories() -> None:
    score = framework().score(request())
    assert score.overall_score == 80.0
    assert score.sealed_at == NOW
    assert len(score.factor_scores) == 7
    assert score.confidence.band is ConfidenceBand.VERY_HIGH


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (RawValueKind.SCORE_0_100, 50, 50.0),
        (RawValueKind.UNIT_INTERVAL, 0.5, 50.0),
        (RawValueKind.LABEL, "RANGE_BOUND", 70.0),
        (RawValueKind.RATIO, 1.0, 50.0),
        (RawValueKind.BOOLEAN, False, 100.0),
    ],
)
def test_tst_ssf_002_raw_kind_conversions(
    kind: RawValueKind, value: object, expected: float
) -> None:
    category = {
        RawValueKind.LABEL: FactorCategory.MARKET_REGIME,
        RawValueKind.RATIO: FactorCategory.RISK_REWARD,
        RawValueKind.BOOLEAN: FactorCategory.EVENT_RISK,
    }.get(kind, FactorCategory.VOLATILITY)
    values = list(full_factors())
    values[list(FactorCategory).index(category)] = factor(category, value, kind)
    score = framework().score(request(tuple(values)))
    selected = next(item for item in score.factor_scores if item.category is category)
    assert selected.normalized_score == expected


@pytest.mark.parametrize(("value", "expected"), [(-1.0, 0.0), (0.0, 50.0), (1.0, 100.0)])
def test_tst_ssf_003_signed_unit(value: float, expected: float) -> None:
    values = list(full_factors())
    values[1] = factor(FactorCategory.TREND_ALIGNMENT, value, RawValueKind.SIGNED_UNIT)
    score = framework().score(request(tuple(values)))
    assert score.factor_scores[1].normalized_score == expected


def test_tst_ssf_004_label_success_and_unknown_rejection() -> None:
    values = list(full_factors())
    values[0] = factor(FactorCategory.MARKET_REGIME, "TRENDING_UP", RawValueKind.LABEL)
    assert framework().score(request(tuple(values))).factor_scores[0].normalized_score == 85.0
    values[0] = factor(FactorCategory.MARKET_REGIME, "OTHER", RawValueKind.LABEL)
    with pytest.raises(StrategyScoringValidationError, match="invalid score request") as raised:
        framework().score(request(tuple(values)))
    assert raised.value.code == "SSF.NORM.002"


def test_tst_ssf_005_ratio_interpolation_and_endpoint_policy() -> None:
    values = list(full_factors())
    values[5] = factor(FactorCategory.RISK_REWARD, 2.5, RawValueKind.RATIO)
    assert framework().score(request(tuple(values))).factor_scores[5].normalized_score == 82.5
    values[5] = factor(FactorCategory.RISK_REWARD, 6.0, RawValueKind.RATIO)
    with pytest.raises(StrategyScoringValidationError) as raised:
        framework().score(request(tuple(values)))
    assert raised.value.code == "SSF.NORM.001"
    profile = replace(BALANCED, allow_endpoint_clamp=True)
    scorer = framework(profiles={profile.name: profile})
    assert scorer.score(request(tuple(values))).factor_scores[5].normalized_score == 100.0


def test_tst_ssf_006_required_omission_rejected() -> None:
    with pytest.raises(StrategyScoringValidationError) as raised:
        framework().score(request(full_factors()[:-1]))
    assert raised.value.result is not None
    assert raised.value.code == "SSF.VAL.003"


def test_tst_ssf_007_optional_omission_redistributes_weight() -> None:
    profile = replace(BALANCED, optional_categories=frozenset({FactorCategory.GREEKS}))
    scorer = framework(profiles={profile.name: profile})
    score = scorer.score(request(tuple(item for item in full_factors() if item.category is not FactorCategory.GREEKS)))
    assert score.overall_score == 80.0
    assert all(item.category is not FactorCategory.GREEKS for item in score.factor_scores)
    assert score.explanation.summary.endswith("Weights were redistributed for optional omissions.")


def test_tst_ssf_008_invalid_profile_total_and_unknown_profile() -> None:
    bad = dict(BALANCED.weights)
    bad[FactorCategory.EVENT_RISK] = 11.0
    with pytest.raises(StrategyScoringConfigurationError):
        WeightProfile("BAD", "1", bad)
    result = framework().validate(request(profile_name="MISSING"))
    assert not result.is_valid and result.errors[0].code == "SSF.CFG.003"


def test_tst_ssf_009_reliability_weighted_aggregation() -> None:
    values = list(full_factors())
    values.extend((
        factor(FactorCategory.VOLATILITY, 0.0, reliability=0.25, factor_id="low"),
        factor(FactorCategory.VOLATILITY, 100.0, reliability=0.75, factor_id="high"),
    ))
    values[2] = factor(FactorCategory.VOLATILITY, 0.0, reliability=0.01, factor_id="base")
    score = framework().score(request(tuple(values)))
    contributions = [item for item in score.factor_scores if item.category is FactorCategory.VOLATILITY]
    assert len(contributions) == 3
    assert score.overall_score == 79.1386


def test_tst_ssf_010_event_risk_boolean_mapping() -> None:
    values = list(full_factors())
    values[-1] = factor(FactorCategory.EVENT_RISK, True, RawValueKind.BOOLEAN)
    adverse = framework().score(request(tuple(values)))
    values[-1] = factor(FactorCategory.EVENT_RISK, False, RawValueKind.BOOLEAN)
    safe = framework().score(request(tuple(values)))
    assert adverse.factor_scores[-1].normalized_score == 0.0
    assert safe.factor_scores[-1].normalized_score == 100.0
    assert safe.overall_score > adverse.overall_score


@pytest.mark.parametrize(
    ("value", "band"),
    [(44.9999, ConfidenceBand.LOW), (45.0, ConfidenceBand.MEDIUM), (70.0, ConfidenceBand.HIGH), (85.0, ConfidenceBand.VERY_HIGH)],
)
def test_tst_ssf_011_confidence_band_boundaries(value: float, band: ConfidenceBand) -> None:
    assert StrategyScoringFramework._confidence_band(value) is band


def test_tst_ssf_012_agreement_declines_for_divergent_categories() -> None:
    uniform = framework().score(request())
    values = tuple(factor(category, 0.0 if index % 2 else 100.0) for index, category in enumerate(FactorCategory))
    divergent = framework().score(request(values))
    assert divergent.confidence.agreement < uniform.confidence.agreement


def test_tst_ssf_013_explanation_order_strengths_concerns() -> None:
    values = list(full_factors())
    values[0] = factor(FactorCategory.MARKET_REGIME, 90.0, factor_id="z")
    values[3] = factor(FactorCategory.LIQUIDITY, 20.0)
    score = framework().score(request(tuple(values)))
    assert "MARKET_REGIME" in score.explanation.factor_narratives[0]
    assert score.explanation.strengths
    assert any("LIQUIDITY" in concern for concern in score.explanation.concerns)
    assert "Strategy iron_condor scored" in score.explanation.summary


def test_tst_ssf_014_models_are_frozen() -> None:
    score = framework().score(request())
    with pytest.raises(FrozenInstanceError):
        score.overall_score = 1.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        score.confidence.penalties += ("X",)  # type: ignore[misc]


def test_tst_ssf_015_canonical_round_trip_and_fingerprint() -> None:
    scorer = framework()
    score = scorer.score(request())
    payload = scorer.serialize(score)
    assert payload == scorer.serialize(score)
    restored = scorer.deserialize(payload)
    assert restored == score
    tampered = json.loads(payload)
    tampered["factor_scores"][0]["raw_value"] = 1
    with pytest.raises(StrategyScoringSerializationError) as raised:
        scorer.deserialize(json.dumps(tampered))
    assert raised.value.code == "SSF.SER.003"


def test_tst_ssf_016_malformed_and_unknown_schema_rejected() -> None:
    scorer = framework()
    with pytest.raises(StrategyScoringSerializationError) as malformed:
        scorer.deserialize("{")
    assert malformed.value.code == "SSF.SER.001"
    payload = json.loads(scorer.serialize(scorer.score(request())))
    payload["schema_version"] = "2.0"
    with pytest.raises(StrategyScoringSerializationError) as schema:
        scorer.deserialize(json.dumps(payload))
    assert schema.value.code == "SSF.SER.002"


@pytest.mark.parametrize(
    ("value", "kind", "code"),
    [(float("nan"), RawValueKind.SCORE_0_100, "SSF.NORM.001"), (101.0, RawValueKind.SCORE_0_100, "SSF.NORM.001"), (True, RawValueKind.SCORE_0_100, "SSF.NORM.001")],
)
def test_tst_ssf_017_invalid_numeric_values(value: object, kind: RawValueKind, code: str) -> None:
    values = list(full_factors())
    values[0] = factor(FactorCategory.MARKET_REGIME, value, kind)
    with pytest.raises(StrategyScoringValidationError) as raised:
        framework().score(request(tuple(values)))
    assert raised.value.code == code


def test_tst_ssf_018_concurrent_cache_and_statistics() -> None:
    scorer = framework(enable_cache=True, cache_capacity=2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        scores = list(pool.map(lambda _: scorer.score(request()), range(24)))
    assert len({item.overall_score for item in scores}) == 1
    assert scorer.statistics().cache_hits >= 1
    assert scorer.statistics().sealed >= 1


def test_tst_ssf_019_event_sink_failure_is_isolated() -> None:
    class FailingSink:
        def publish(self, topic: str, payload: object) -> None:
            raise RuntimeError("sink failed")

    scorer = StrategyScoringFramework(ScoringFrameworkConfig(event_sink=FailingSink()), clock=fixed_clock)
    assert scorer.score(request()).overall_score == 80.0
    assert scorer.health().health is ScoringHealth.DEGRADED


def test_tst_ssf_020_forbidden_dependencies_absent() -> None:
    source = inspect.getsource(__import__("strategy.strategy_scoring_framework", fromlist=["*"]))
    forbidden = ("broker.", "kiteconnect", "place_order", "def ema", "def rsi", "dotenv")
    assert not any(item in source.lower() for item in forbidden)


def test_validation_quality_reliability_and_health_disabled() -> None:
    values = list(full_factors())
    values[0] = factor(FactorCategory.MARKET_REGIME, 80, reliability=0.0)
    result = framework().validate(request(tuple(values)))
    assert not result.is_valid and result.errors[0].code == "SSF.VAL.008"
    disabled = framework(enable_statistics=False)
    assert disabled.health().health is ScoringHealth.DISABLED
    assert disabled.statistics().requests == 0


def test_validation_and_configuration_error_branches() -> None:
    with pytest.raises(StrategyScoringConfigurationError):
        ScoringFrameworkConfig(schema_version="2.0")
    with pytest.raises(StrategyScoringConfigurationError):
        ScoringFrameworkConfig(default_profile="missing")
    with pytest.raises(StrategyScoringConfigurationError):
        ScoringFrameworkConfig(rounding_decimals=9)
    with pytest.raises(StrategyScoringConfigurationError):
        ScoringFrameworkConfig(cache_capacity=4097)
    with pytest.raises(StrategyScoringConfigurationError):
        WeightProfile("BAD", "1", {FactorCategory.MARKET_REGIME: 100.0})
    with pytest.raises(StrategyScoringConfigurationError):
        replace(BALANCED, ratio_curves={FactorCategory.RISK_REWARD: ((1.0, 0.0),)})
    scorer = framework()
    assert scorer.validate(ScoreRequest("", full_factors())).errors[0].code == "SSF.VAL.001"
    assert scorer.validate(ScoreRequest("iron_condor", ())).errors[0].code == "SSF.VAL.002"
    assert scorer.validate(request(metadata={"x" * 129: "v"})).errors[0].code == "SSF.VAL.001"
    duplicate = full_factors() + (factor(FactorCategory.MARKET_REGIME, factor_id="market_regime"),)
    assert any(item.code == "SSF.VAL.004" for item in scorer.validate(request(duplicate)).errors)


def test_normalization_error_paths_and_quality_flags() -> None:
    scorer = framework()
    values = list(full_factors())
    values[1] = factor(FactorCategory.TREND_ALIGNMENT, "wrong", RawValueKind.SIGNED_UNIT)
    assert scorer.validate(request(tuple(values))).errors[0].code == "SSF.NORM.001"
    values[1] = factor(FactorCategory.TREND_ALIGNMENT, 0.0, RawValueKind.SIGNED_UNIT)
    values[0] = factor(FactorCategory.MARKET_REGIME, True, RawValueKind.LABEL)
    assert scorer.validate(request(tuple(values))).errors[0].code == "SSF.VAL.006"
    values[0] = factor(FactorCategory.MARKET_REGIME, 80.0, provenance="")
    assert scorer.validate(request(tuple(values))).errors[0].code == "SSF.VAL.007"
    values[0] = factor(FactorCategory.MARKET_REGIME, 80.0, quality_flags=("stale", "custom"))
    score = scorer.score(request(tuple(values)))
    assert score.confidence.data_quality == 80.0
    assert score.confidence.penalties == ("QUALITY_CUSTOM", "QUALITY_STALE")


def test_provider_bundle_default_reliability_and_serialization_errors() -> None:
    profile = replace(BALANCED, allow_default_reliability=False)
    scorer = framework(profiles={profile.name: profile})
    assert scorer.validate(request(profile_name=profile.name)).errors[0].code == "SSF.VAL.008"
    default_scorer = framework()
    score = default_scorer.score(
        ScoreRequest("iron_condor", FactorInputBundle(full_factors()))
    )
    assert default_scorer.explain(score) == score.explanation
    payload = default_scorer.serialize(score)
    document = json.loads(payload)
    document["profile_revision"] = "not-the-profile"
    with pytest.raises(StrategyScoringSerializationError) as unknown:
        default_scorer.deserialize(json.dumps(document))
    assert unknown.value.code == "SSF.CFG.003"
    document = json.loads(payload)
    document["overall_score"] = "101.0"
    with pytest.raises(StrategyScoringSerializationError) as range_error:
        default_scorer.deserialize(json.dumps(document))
    assert range_error.value.code == "SSF.SER.001"


def test_clock_and_cache_eviction_paths() -> None:
    scorer = StrategyScoringFramework(ScoringFrameworkConfig(enable_cache=True, cache_capacity=0), clock=fixed_clock)
    scorer.score(request())
    assert scorer.statistics().sealed == 1
    naive = StrategyScoringFramework(
        ScoringFrameworkConfig(), clock=lambda: datetime(2026, 8, 5, 10, 0)
    )
    with pytest.raises(Exception, match="clock must return aware"):
        naive.score(request())

