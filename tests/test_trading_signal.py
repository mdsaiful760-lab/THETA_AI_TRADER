"""Unit tests for strategy.signals."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from market_data.market_snapshot import OptionType, build_market_snapshot
from market_data.market_snapshot import UnderlyingSnapshot
from strategy.signals import (
    ERROR_BUNDLE_DUPLICATE_ID,
    ERROR_BUNDLE_LIMIT_EXCEEDED,
    ERROR_EXPIRED,
    ERROR_SCHEMA_BAND_MISMATCH,
    ERROR_SCHEMA_EMPTY_REASONS,
    ERROR_SCHEMA_INVALID_EXPIRY,
    ERROR_SCHEMA_INVALID_ID,
    ERROR_SCHEMA_NAIVE_TIMESTAMP,
    ERROR_SEMANTIC_FAMILY_CONFLICT,
    ERROR_SEMANTIC_FORBIDDEN_FIELD,
    ERROR_SEMANTIC_SNAPSHOT_MISMATCH,
    ERROR_SEMANTIC_STALE_CONTEXT,
    ERROR_SEMANTIC_UNDERLYING_MISMATCH,
    ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
    TRADING_SIGNAL_SCHEMA_VERSION,
    AggregatedSignalResult,
    AggregationMetadata,
    AggregationMode,
    ConfidenceBand,
    ConfidenceComponent,
    ConditionOperator,
    EntryCondition,
    EntryLogic,
    EntryTriggerType,
    ExitCondition,
    ExitLogic,
    ExitTriggerType,
    RiskLevelHint,
    RiskProfileHint,
    SignalAction,
    SignalBundle,
    SignalConfidence,
    SignalDirection,
    SignalExpirationPolicy,
    SignalFactor,
    SignalMarketContext,
    SignalRiskMetadata,
    SignalStrength,
    SignalTimeValidity,
    SignalType,
    SignalValidationContext,
    StopLossHint,
    StopLossHintType,
    StrategyExecutionMode,
    StrategyFamily,
    StructureHint,
    TargetHint,
    TargetHintType,
    TradingSignal,
    TradingSignalExpiredError,
    TradingSignalSerializationError,
    TradingSignalValidationError,
    ValidationPolicy,
    aggregated_from_dict,
    aggregated_from_json,
    aggregated_to_dict,
    aggregated_to_json,
    apply_default_valid_until,
    are_directions_opposed,
    assert_signal_fresh,
    assert_valid_trading_signal,
    bundle_from_dict,
    bundle_from_json,
    bundle_to_dict,
    bundle_to_json,
    confidence_band_for_score,
    from_dict,
    from_json,
    infer_signal_strength,
    infer_signal_type,
    is_signal_expired,
    market_context_from_snapshot,
    remaining_validity_seconds,
    signal_fingerprint,
    to_dict,
    to_json,
    validate_aggregated_result,
    validate_signal_bundle,
    validate_trading_signal,
    validate_trading_signal_schema,
    validate_trading_signal_semantics,
)

IST = ZoneInfo("Asia/Kolkata")


def fixed_as_of() -> datetime:
    return datetime(2026, 8, 3, 10, 15, 0, tzinfo=IST)


def fixed_valid_until() -> datetime:
    return datetime(2026, 8, 3, 10, 17, 0, tzinfo=IST)


def make_underlying() -> UnderlyingSnapshot:
    return UnderlyingSnapshot(
        symbol="NIFTY",
        exchange="NSE",
        quote_key="NSE:NIFTY 50",
        last_price=24296.75,
        quote_timestamp=fixed_as_of(),
    )


def make_contract(option_type: OptionType = OptionType.CE) -> object:
    from market_data.market_snapshot import OptionContractSnapshot

    suffix = option_type.value
    return OptionContractSnapshot(
        underlying="NIFTY",
        exchange="NFO",
        tradingsymbol=f"NIFTY2680724300{suffix}",
        expiry="2026-08-07",
        strike=24300.0,
        option_type=option_type,
        lot_size=75,
        ltp=110.0,
        bid=109.65,
        ask=109.9,
        volume=1000,
        open_interest=10000,
        quote_timestamp=fixed_as_of(),
    )


def minimal_snapshot():
    return build_market_snapshot(
        underlying=make_underlying(),
        contracts=(make_contract(OptionType.CE), make_contract(OptionType.PE)),
        underlying_symbol="NIFTY",
        exchange="NFO",
        expiry="2026-08-07",
        atm_strike=24300.0,
        strike_step=50.0,
        strike_window_strikes=1,
        minimum_strike=24300.0,
        maximum_strike=24300.0,
        lot_size=75,
        as_of=fixed_as_of(),
        captured_at=fixed_as_of(),
        snapshot_id="test-snapshot-001",
        reference_time=fixed_as_of(),
    )


def valid_market(**overrides: object) -> SignalMarketContext:
    base = market_context_from_snapshot(minimal_snapshot())
    if not overrides:
        return base
    return replace(base, **overrides)  # type: ignore[arg-type]


def valid_confidence(score: float = 72.5) -> SignalConfidence:
    return SignalConfidence(
        score=score,
        band=confidence_band_for_score(score),
        method="test_method",
        components=(
            ConfidenceComponent(
                name="iv_rank",
                weight=0.5,
                score=80.0,
                contribution=40.0,
                description="IV rank factor",
            ),
        ),
    )


def valid_signal(**overrides: object) -> TradingSignal:
    base = TradingSignal(
        signal_id="550e8400-e29b-41d4-a716-446655440000",
        as_of=fixed_as_of(),
        valid_until=fixed_valid_until(),
        action=SignalAction.EVALUATE,
        direction=SignalDirection.NEUTRAL,
        signal_type=SignalType.ENTRY,
        strength=SignalStrength.STRONG,
        strategy_id="short_strangle",
        strategy_version="1.0.0",
        strategy_family=StrategyFamily.SHORT_STRANGLE,
        confidence=valid_confidence(),
        market=valid_market(),
        reasons=("IV rank elevated in range-bound regime",),
        structure_hint=StructureHint(
            structure_type="STRANGLE",
            leg_count=2,
            strike_selection_policy="DELTA_TARGET",
            target_delta=0.16,
        ),
        risk=SignalRiskMetadata(
            profile=RiskProfileHint.UNDEFINED,
            max_loss_category="HIGH",
            gamma_risk=RiskLevelHint.MODERATE,
        ),
        factors=(
            SignalFactor(
                name="spread_quality",
                weight=0.5,
                score=70.0,
                description="Bid-ask spread quality",
            ),
        ),
    )
    if not overrides:
        return base
    return replace(base, **overrides)  # type: ignore[arg-type]


class TestConstantsAndHelpers:
    def test_schema_version(self) -> None:
        assert TRADING_SIGNAL_SCHEMA_VERSION == "1.0.0"

    @pytest.mark.parametrize(
        ("score", "band"),
        [
            (0.0, ConfidenceBand.LOW),
            (39.9, ConfidenceBand.LOW),
            (40.0, ConfidenceBand.MEDIUM),
            (79.9, ConfidenceBand.HIGH),
            (80.0, ConfidenceBand.VERY_HIGH),
        ],
    )
    def test_confidence_band_for_score(self, score: float, band: ConfidenceBand) -> None:
        assert confidence_band_for_score(score) is band

    def test_infer_signal_type_abstain(self) -> None:
        assert infer_signal_type(SignalAction.ABSTAIN) is SignalType.ABSTAIN

    def test_infer_signal_type_entry(self) -> None:
        assert infer_signal_type(SignalAction.EVALUATE, has_entry=True) is SignalType.ENTRY

    def test_infer_signal_strength_abstain(self) -> None:
        confidence = valid_confidence(score=0.0)
        assert infer_signal_strength(confidence, SignalAction.ABSTAIN) is SignalStrength.NONE

    def test_are_directions_opposed(self) -> None:
        assert are_directions_opposed(SignalDirection.BULLISH, SignalDirection.BEARISH)
        assert not are_directions_opposed(SignalDirection.NEUTRAL, SignalDirection.BULLISH)


class TestImmutability:
    def test_trading_signal_frozen(self) -> None:
        signal = valid_signal()
        with pytest.raises(Exception):
            signal.action = SignalAction.WAIT  # type: ignore[misc]

    def test_signal_confidence_frozen(self) -> None:
        confidence = valid_confidence()
        with pytest.raises(Exception):
            confidence.score = 1.0  # type: ignore[misc]

    def test_signal_bundle_frozen(self) -> None:
        bundle = SignalBundle(signals=(valid_signal(),))
        with pytest.raises(Exception):
            bundle.signals = ()  # type: ignore[misc]


class TestSchemaValidation:
    def test_valid_signal_passes(self) -> None:
        result = validate_trading_signal_schema(valid_signal())
        assert result.is_valid
        assert not result.errors

    def test_empty_signal_id_rejected(self) -> None:
        result = validate_trading_signal_schema(valid_signal(signal_id="  "))
        assert not result.is_valid
        assert any(error.code == ERROR_SCHEMA_INVALID_ID for error in result.errors)

    def test_naive_as_of_rejected(self) -> None:
        result = validate_trading_signal_schema(
            valid_signal(as_of=datetime(2026, 8, 3, 10, 15, 0))
        )
        assert not result.is_valid
        assert any(error.code == ERROR_SCHEMA_NAIVE_TIMESTAMP for error in result.errors)

    def test_empty_reasons_rejected(self) -> None:
        result = validate_trading_signal_schema(valid_signal(reasons=()))
        assert not result.is_valid
        assert any(error.code == ERROR_SCHEMA_EMPTY_REASONS for error in result.errors)

    def test_band_mismatch_rejected(self) -> None:
        confidence = replace(valid_confidence(), band=ConfidenceBand.LOW)
        result = validate_trading_signal_schema(valid_signal(confidence=confidence))
        assert not result.is_valid
        assert any(error.code == ERROR_SCHEMA_BAND_MISMATCH for error in result.errors)

    def test_invalid_expiry_rejected(self) -> None:
        result = validate_trading_signal_schema(
            valid_signal(valid_until=fixed_as_of() - timedelta(seconds=1))
        )
        assert not result.is_valid
        assert any(error.code == ERROR_SCHEMA_INVALID_EXPIRY for error in result.errors)

    def test_evaluate_no_strategy_rejected(self) -> None:
        result = validate_trading_signal_schema(
            valid_signal(strategy_family=StrategyFamily.NO_STRATEGY)
        )
        assert not result.is_valid
        assert any(error.code == ERROR_SEMANTIC_FAMILY_CONFLICT for error in result.errors)

    def test_forbidden_metadata_key_rejected(self) -> None:
        result = validate_trading_signal_schema(
            valid_signal(metadata=MappingProxyType({"tradingsymbol": "BAD"}))
        )
        assert not result.is_valid
        assert any(error.code == ERROR_SEMANTIC_FORBIDDEN_FIELD for error in result.errors)


class TestSemanticValidation:
    def test_snapshot_mismatch(self) -> None:
        signal = valid_signal()
        context = SignalValidationContext(snapshot_id="other-snapshot")
        result = validate_trading_signal_semantics(signal, context=context)
        assert not result.is_valid
        assert any(error.code == ERROR_SEMANTIC_SNAPSHOT_MISMATCH for error in result.errors)

    def test_underlying_mismatch(self) -> None:
        signal = valid_signal()
        context = SignalValidationContext(underlying="BANKNIFTY")
        result = validate_trading_signal_semantics(signal, context=context)
        assert not result.is_valid
        assert any(error.code == ERROR_SEMANTIC_UNDERLYING_MISMATCH for error in result.errors)

    def test_expired_signal_semantic_error(self) -> None:
        signal = valid_signal()
        context = SignalValidationContext(reference_time=fixed_valid_until() + timedelta(seconds=1))
        result = validate_trading_signal_semantics(signal, context=context)
        assert not result.is_valid
        assert any(error.code == ERROR_EXPIRED for error in result.errors)

    def test_stale_context_warning(self) -> None:
        signal = valid_signal(market=valid_market(freshness_status="STALE"))
        context = SignalValidationContext(execution_mode=StrategyExecutionMode.LIVE.value)
        result = validate_trading_signal_semantics(signal, context=context)
        assert result.is_valid
        assert any(warning.code == ERROR_SEMANTIC_STALE_CONTEXT for warning in result.warnings)

    def test_stale_context_strict_error(self) -> None:
        signal = valid_signal(market=valid_market(freshness_status="STALE"))
        context = SignalValidationContext(
            execution_mode=StrategyExecutionMode.LIVE.value,
            strict=True,
        )
        result = validate_trading_signal(signal, context=context)
        assert not result.is_valid


class TestExpirationHelpers:
    def test_is_signal_expired_false_when_no_valid_until(self) -> None:
        signal = valid_signal(valid_until=None)
        assert not is_signal_expired(signal, reference_time=fixed_valid_until())

    def test_is_signal_expired_true_after_valid_until(self) -> None:
        signal = valid_signal()
        assert is_signal_expired(signal, reference_time=fixed_valid_until() + timedelta(seconds=1))

    def test_remaining_validity_seconds(self) -> None:
        signal = valid_signal()
        remaining = remaining_validity_seconds(signal, reference_time=fixed_as_of())
        assert remaining == pytest.approx(120.0)

    def test_assert_signal_fresh_raises(self) -> None:
        signal = valid_signal()
        with pytest.raises(TradingSignalExpiredError) as exc_info:
            assert_signal_fresh(signal, reference_time=fixed_valid_until() + timedelta(seconds=5))
        assert exc_info.value.code == ERROR_EXPIRED

    def test_apply_default_valid_until_live(self) -> None:
        signal = valid_signal(valid_until=None)
        updated = apply_default_valid_until(
            signal,
            execution_mode=StrategyExecutionMode.LIVE,
        )
        assert updated.valid_until == fixed_as_of() + timedelta(seconds=120)


class TestSerialization:
    def test_to_dict_includes_schema_version(self) -> None:
        payload = to_dict(valid_signal())
        assert payload["schema_version"] == TRADING_SIGNAL_SCHEMA_VERSION

    def test_dict_round_trip_equality(self) -> None:
        signal = valid_signal()
        restored = from_dict(to_dict(signal))
        assert restored == signal

    def test_json_round_trip_equality(self) -> None:
        signal = valid_signal()
        restored = from_json(to_json(signal))
        assert restored == signal

    def test_unsupported_schema_version_rejected(self) -> None:
        payload = to_dict(valid_signal())
        payload["schema_version"] = "2.0.0"
        with pytest.raises(TradingSignalSerializationError) as exc_info:
            from_dict(payload)
        assert exc_info.value.code == ERROR_SERIALIZATION_UNSUPPORTED_VERSION

    def test_malformed_json_rejected(self) -> None:
        with pytest.raises(TradingSignalSerializationError):
            from_json("{bad-json")

    def test_trading_signal_properties(self) -> None:
        signal = valid_signal()
        assert signal.underlying == "NIFTY"
        assert signal.snapshot_id == "test-snapshot-001"
        assert signal.expiry == "2026-08-07"


class TestFingerprint:
    def test_fingerprint_stable(self) -> None:
        signal = valid_signal()
        assert signal_fingerprint(signal) == signal_fingerprint(signal)

    def test_fingerprint_excludes_signal_id_by_default(self) -> None:
        first = valid_signal(signal_id="id-1")
        second = valid_signal(signal_id="id-2")
        assert signal_fingerprint(first) == signal_fingerprint(second)

    def test_fingerprint_changes_with_signal_id_when_included(self) -> None:
        first = valid_signal(signal_id="id-1")
        second = valid_signal(signal_id="id-2")
        assert signal_fingerprint(first, include_signal_id=True) != signal_fingerprint(
            second,
            include_signal_id=True,
        )


class TestBundleValidation:
    def test_valid_bundle_passes(self) -> None:
        bundle = SignalBundle(signals=(valid_signal(), valid_signal(signal_id="signal-2")))
        result = validate_signal_bundle(bundle)
        assert result.is_valid

    def test_duplicate_signal_id_rejected(self) -> None:
        bundle = SignalBundle(signals=(valid_signal(), valid_signal()))
        result = validate_signal_bundle(bundle)
        assert not result.is_valid
        assert any(error.code == ERROR_BUNDLE_DUPLICATE_ID for error in result.errors)

    def test_bundle_limit_exceeded(self) -> None:
        signals = tuple(valid_signal(signal_id=f"signal-{index}") for index in range(3))
        bundle = SignalBundle(signals=signals)
        result = validate_signal_bundle(bundle, policy=ValidationPolicy(max_bundle_size=2))
        assert not result.is_valid
        assert any(error.code == ERROR_BUNDLE_LIMIT_EXCEEDED for error in result.errors)

    def test_bundle_json_round_trip(self) -> None:
        bundle = SignalBundle(signals=(valid_signal(), valid_signal(signal_id="signal-2")))
        restored = bundle_from_json(bundle_to_json(bundle))
        assert restored == bundle

    def test_bundle_dict_round_trip(self) -> None:
        bundle = SignalBundle(signals=(valid_signal(),))
        restored = bundle_from_dict(bundle_to_dict(bundle))
        assert restored == bundle


class TestAggregatedResult:
    def test_validate_aggregated_result(self) -> None:
        primary = valid_signal()
        result = AggregatedSignalResult(
            primary_signal=primary,
            secondary_signals=(valid_signal(signal_id="secondary-1"),),
            abstain_signals=(valid_signal(signal_id="abstain-1", action=SignalAction.ABSTAIN),),
            aggregate_confidence=valid_confidence(65.0),
            aggregation_metadata=AggregationMetadata(
                aggregation_mode=AggregationMode.PRIMARY_SECONDARY,
                signal_count=3,
            ),
        )
        validation = validate_aggregated_result(result)
        assert validation.is_valid

    def test_aggregated_json_round_trip(self) -> None:
        result = AggregatedSignalResult(
            primary_signal=valid_signal(),
            aggregate_confidence=valid_confidence(),
            aggregation_metadata=AggregationMetadata(
                aggregation_mode=AggregationMode.SINGLE_WINNER,
                signal_count=1,
            ),
        )
        restored = aggregated_from_json(aggregated_to_json(result))
        assert restored.primary_signal == result.primary_signal
        assert restored.aggregation_metadata == result.aggregation_metadata

    def test_aggregated_dict_round_trip(self) -> None:
        result = AggregatedSignalResult(
            primary_signal=valid_signal(),
            aggregate_confidence=valid_confidence(),
            aggregation_metadata=AggregationMetadata(
                aggregation_mode=AggregationMode.NO_TRADE_DEFAULT,
                signal_count=1,
            ),
        )
        restored = aggregated_from_dict(aggregated_to_dict(result))
        assert restored == result


class TestAssertValidTradingSignal:
    def test_assert_valid_returns_signal(self) -> None:
        signal = valid_signal()
        assert assert_valid_trading_signal(signal) is signal

    def test_assert_valid_raises(self) -> None:
        with pytest.raises(TradingSignalValidationError):
            assert_valid_trading_signal(valid_signal(reasons=()))


class TestMarketContextFromSnapshot:
    def test_market_context_from_snapshot(self) -> None:
        snapshot = minimal_snapshot()
        context = market_context_from_snapshot(snapshot)
        assert context.snapshot_id == "test-snapshot-001"
        assert context.underlying == "NIFTY"
        assert context.spot_at_signal == pytest.approx(24296.75)


class TestEntryExitHints:
    def test_entry_logic_validation_reference_pattern(self) -> None:
        entry = EntryLogic(
            trigger_type=EntryTriggerType.IMMEDIATE,
            conditions=(
                EntryCondition(
                    condition_id="spot_above_vwap",
                    operator=ConditionOperator.GT,
                    reference="atm_strike",
                    value=24300.0,
                    met=True,
                    description="Spot above VWAP",
                ),
            ),
            notes="Immediate entry",
        )
        signal = valid_signal(entry=entry, signal_type=SignalType.ENTRY)
        result = validate_trading_signal_schema(signal)
        assert result.is_valid

    def test_exit_logic_fraction_validation(self) -> None:
        exit_logic = ExitLogic(
            trigger_type=ExitTriggerType.PROFIT_TARGET,
            conditions=(),
            exit_fraction=1.5,
        )
        result = validate_trading_signal_schema(valid_signal(exit=exit_logic, signal_type=SignalType.EXIT))
        assert not result.is_valid

    def test_stop_loss_and_target_hints(self) -> None:
        signal = valid_signal(
            stop_loss=StopLossHint(
                hint_type=StopLossHintType.UNDERLYING_LEVEL,
                reference="short_strike",
                value=100.0,
            ),
            target=TargetHint(
                hint_type=TargetHintType.PREMIUM_DECAY_PERCENT,
                reference="net_credit",
                value=50.0,
                basis="net_credit",
            ),
        )
        result = validate_trading_signal_schema(signal)
        assert result.is_valid


class TestThreadSafety:
    def test_concurrent_validation_and_serialization(self) -> None:
        signal = valid_signal()
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                result = validate_trading_signal(signal)
                payload = to_json(from_json(to_json(signal)))
                fingerprint = signal_fingerprint(signal)
                assert result.is_valid
                assert fingerprint
                assert payload
            except BaseException as exc:  # pragma: no cover
                with lock:
                    errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker) for _ in range(24)]
            for future in as_completed(futures):
                future.result()

        assert not errors


class TestPerformanceSmoke:
    def test_validation_performance_smoke(self) -> None:
        signal = valid_signal()
        start = time.perf_counter()
        for _ in range(200):
            validate_trading_signal(signal)
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0

    def test_serialization_performance_smoke(self) -> None:
        signal = valid_signal()
        start = time.perf_counter()
        for _ in range(100):
            from_json(to_json(signal))
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0

    def test_fingerprint_performance_smoke(self) -> None:
        signal = valid_signal()
        start = time.perf_counter()
        for _ in range(200):
            signal_fingerprint(signal)
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0


class TestExtendedCoverage:
    def test_infer_signal_type_branches(self) -> None:
        assert infer_signal_type(SignalAction.NO_TRADE) is SignalType.ABSTAIN
        assert infer_signal_type(SignalAction.WAIT) is SignalType.MONITOR
        assert infer_signal_type(SignalAction.EVALUATE, has_exit=True) is SignalType.EXIT
        assert infer_signal_type(SignalAction.EVALUATE) is SignalType.SETUP

    def test_resolved_properties(self) -> None:
        signal = valid_signal(signal_type=None, strength=None, entry=None)
        assert signal.resolved_signal_type is SignalType.SETUP
        assert signal.resolved_strength is SignalStrength.STRONG

    def test_strategy_nested_mismatch(self) -> None:
        from strategy.signals import SignalStrategyMetadata

        signal = valid_signal(
            strategy=SignalStrategyMetadata(
                strategy_id="other",
                strategy_version="9.9.9",
                strategy_family=StrategyFamily.IRON_CONDOR,
            )
        )
        result = validate_trading_signal_schema(signal)
        assert not result.is_valid
        assert len(result.errors) >= 3

    def test_time_validity_validation_errors(self) -> None:
        validity = SignalTimeValidity(
            valid_from=datetime(2026, 8, 3, 10, 0, 0),
            valid_until=fixed_valid_until(),
        )
        result = validate_trading_signal_schema(valid_signal(time_validity=validity))
        assert not result.is_valid

    def test_time_validity_mismatch_with_top_level(self) -> None:
        validity = SignalTimeValidity(valid_until=fixed_as_of())
        result = validate_trading_signal_schema(
            valid_signal(time_validity=validity, valid_until=fixed_valid_until())
        )
        assert not result.is_valid

    def test_invalid_entry_reference(self) -> None:
        entry = EntryLogic(
            trigger_type=EntryTriggerType.IMMEDIATE,
            conditions=(
                EntryCondition(
                    condition_id="bad",
                    operator=ConditionOperator.EQ,
                    reference="INVALID",
                    value=1.0,
                    met=True,
                    description="bad ref",
                ),
            ),
        )
        result = validate_trading_signal_schema(valid_signal(entry=entry))
        assert not result.is_valid

    def test_stop_loss_none_with_value_warning(self) -> None:
        stop = StopLossHint(
            hint_type=StopLossHintType.NONE,
            reference="none",
            value=1.0,
        )
        result = validate_trading_signal_schema(valid_signal(stop_loss=stop))
        assert result.is_valid
        assert result.warnings

    def test_strict_direction_mismatch_error(self) -> None:
        signal = valid_signal(
            strategy_family=StrategyFamily.BULL_PUT_SPREAD,
            direction=SignalDirection.BEARISH,
        )
        context = SignalValidationContext(strict_direction_check=True, strict=True)
        result = validate_trading_signal(signal, context=context)
        assert not result.is_valid

    def test_aggregated_duplicate_primary_in_secondary(self) -> None:
        primary = valid_signal()
        result = AggregatedSignalResult(
            primary_signal=primary,
            secondary_signals=(primary,),
            aggregate_confidence=valid_confidence(),
            aggregation_metadata=AggregationMetadata(
                aggregation_mode=AggregationMode.PRIMARY_SECONDARY,
                signal_count=2,
            ),
        )
        validation = validate_aggregated_result(result)
        assert not validation.is_valid

    def test_aggregated_band_mismatch(self) -> None:
        bad_confidence = replace(valid_confidence(), band=ConfidenceBand.LOW)
        result = AggregatedSignalResult(
            aggregate_confidence=bad_confidence,
            aggregation_metadata=AggregationMetadata(
                aggregation_mode=AggregationMode.NO_TRADE_DEFAULT,
                signal_count=0,
            ),
        )
        validation = validate_aggregated_result(result)
        assert not validation.is_valid

    def test_from_dict_missing_required_field(self) -> None:
        payload = to_dict(valid_signal())
        del payload["signal_id"]
        with pytest.raises(TradingSignalSerializationError):
            from_dict(payload)

    def test_apply_default_valid_until_analysis(self) -> None:
        signal = valid_signal(valid_until=None)
        updated = apply_default_valid_until(signal, execution_mode=StrategyExecutionMode.ANALYSIS)
        assert updated.valid_until == fixed_as_of() + timedelta(seconds=86400)

    def test_full_optional_fields_round_trip(self) -> None:
        from strategy.signals import (
            ExitCondition,
            SessionScope,
            SessionWindow,
            SignalStrategyMetadata,
            ValueUnit,
        )

        signal = valid_signal(
            strategy=SignalStrategyMetadata(
                strategy_id="short_strangle",
                strategy_version="1.0.0",
                strategy_family=StrategyFamily.SHORT_STRANGLE,
                display_name="Short Strangle",
                plugin_priority=700,
                execution_mode=StrategyExecutionMode.LIVE,
            ),
            entry=EntryLogic(
                trigger_type=EntryTriggerType.TIME_WINDOW,
                conditions=(
                    EntryCondition(
                        condition_id="entry.window",
                        operator=ConditionOperator.BETWEEN,
                        reference="spot",
                        value=(24200.0, 24400.0),
                        met=None,
                        description="Spot window",
                    ),
                ),
                preferred_session_window=SessionWindow(
                    start_time=fixed_as_of().time(),
                    end_time=fixed_valid_until().time(),
                    timezone="Asia/Kolkata",
                    label="morning",
                ),
            ),
            exit=ExitLogic(
                trigger_type=ExitTriggerType.TIME_DECAY,
                conditions=(
                    ExitCondition(
                        condition_id="exit.theta",
                        operator=ConditionOperator.GTE,
                        reference="theta_decay",
                        value=0.5,
                        met=True,
                        description="Theta milestone",
                    ),
                ),
                exit_fraction=0.5,
                roll_to_expiry="2026-08-14",
            ),
            time_validity=SignalTimeValidity(
                valid_from=fixed_as_of(),
                valid_until=fixed_valid_until(),
                session_scope=SessionScope.REGULAR,
            ),
            stop_loss=StopLossHint(
                hint_type=StopLossHintType.PREMIUM_MULTIPLE,
                reference="premium_multiple",
                value=2.0,
                value_unit=ValueUnit.MULTIPLE,
                basis="net_credit",
            ),
            target=TargetHint(
                hint_type=TargetHintType.RISK_REWARD_RATIO,
                reference="rr",
                value=3.0,
                value_unit=ValueUnit.MULTIPLE,
                basis="net_credit",
            ),
            structure_hint=StructureHint(
                structure_type="IRON_CONDOR",
                leg_count=4,
                option_types=(OptionType.CE, OptionType.PE),
            ),
            metadata=MappingProxyType({"regime": "range_bound"}),
        )
        restored = from_json(to_json(signal))
        assert restored == signal

    def test_bundle_from_dict_invalid_signals_type(self) -> None:
        with pytest.raises(TradingSignalSerializationError):
            bundle_from_dict({"schema_version": TRADING_SIGNAL_SCHEMA_VERSION, "signals": "bad"})

    def test_action_type_abstain_mismatch(self) -> None:
        result = validate_trading_signal_schema(
            valid_signal(action=SignalAction.WAIT, signal_type=SignalType.ABSTAIN)
        )
        assert not result.is_valid

    def test_strength_none_with_evaluate_warning(self) -> None:
        result = validate_trading_signal_schema(
            valid_signal(strength=SignalStrength.NONE, action=SignalAction.EVALUATE)
        )
        assert result.is_valid
        assert result.warnings

    def test_invalid_strategy_id_and_version(self) -> None:
        result = validate_trading_signal_schema(
            valid_signal(strategy_id="BAD", strategy_version="not-semver")
        )
        assert not result.is_valid

    def test_invalid_spot_at_signal(self) -> None:
        result = validate_trading_signal_schema(
            valid_signal(market=valid_market(spot_at_signal=float("nan")))
        )
        assert not result.is_valid

    def test_direction_family_warning_non_strict(self) -> None:
        signal = valid_signal(
            strategy_family=StrategyFamily.BULL_PUT_SPREAD,
            direction=SignalDirection.BEARISH,
        )
        context = SignalValidationContext(strict_direction_check=True)
        result = validate_trading_signal_semantics(signal, context=context)
        assert result.is_valid
        assert result.warnings

    def test_aggregated_metadata_count_warning(self) -> None:
        result = AggregatedSignalResult(
            aggregate_confidence=valid_confidence(),
            aggregation_metadata=AggregationMetadata(
                aggregation_mode=AggregationMode.NO_TRADE_DEFAULT,
                signal_count=99,
            ),
        )
        validation = validate_aggregated_result(result)
        assert validation.warnings

    def test_infer_signal_strength_branches(self) -> None:
        assert infer_signal_strength(valid_confidence(30.0), SignalAction.EVALUATE) is SignalStrength.WEAK
        assert infer_signal_strength(valid_confidence(50.0), SignalAction.EVALUATE) is SignalStrength.MODERATE
        assert infer_signal_strength(valid_confidence(90.0), SignalAction.EVALUATE) is SignalStrength.EXCEPTIONAL
