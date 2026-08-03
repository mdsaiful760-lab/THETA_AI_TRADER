"""Unit tests for strategy.strategy_evaluation_engine."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from types import MappingProxyType

import pytest

from core.engine_context import EngineContext
from core.enums import EngineStatus
from strategy.base_strategy import (
    BaseStrategy,
    StrategyContext,
    StrategyContextError,
    StrategyPluginConfig,
    StrategySignalError,
)
from strategy.registry import StrategyRegistry
from strategy.strategy_evaluation_engine import (
    ERROR_CONTEXT_INVALID,
    ERROR_EMPTY_ENABLED_SET,
    ERROR_NO_ACTIONABLE,
    ERROR_PARTIAL_FAILURE,
    ERROR_REGISTRY_FINGERPRINT_MISMATCH,
    ERROR_REGISTRY_PLUGIN_MISSING,
    ERROR_SERIALIZATION_MALFORMED,
    ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
    EvaluationFailureMode,
    EvaluationOutcomeClass,
    EvaluationParallelismMode,
    EvaluationRunContext,
    EvaluationScoringPolicy,
    EvaluationStatus,
    StrategyEvaluationConfigurationError,
    StrategyEvaluationContextError,
    StrategyEvaluationEngine,
    StrategyEvaluationEngineConfig,
    StrategyEvaluationRegistryError,
    StrategyEvaluationValidationError,
    bundle_from_json,
    bundle_to_dict,
    bundle_to_json,
    classify_outcome,
    evaluation_fingerprint,
    rank_reports,
    report_to_dict,
)
from strategy.signals import (
    SignalAction,
    SignalConfidence,
    SignalDirection,
    StrategyExecutionMode,
    StrategyFamily,
    TradingSignal,
    confidence_band_for_score,
    market_context_from_snapshot,
)
from tests.test_base_strategy import (
    AbstainOnlyStrategy,
    EchoEvaluateStrategy,
    build_evaluate_signal,
    fixed_as_of,
    minimal_valid_snapshot,
    valid_metadata,
    valid_plugin_config,
)

IST = fixed_as_of().tzinfo


class FixedClock:
    """Deterministic clock."""

    def __init__(self, start: datetime | None = None) -> None:
        from datetime import timezone

        self._current = start or datetime(2026, 8, 3, 10, 15, 0, tzinfo=IST or timezone.utc)
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._current


def make_strategy(
    *,
    strategy_id: str = "short_strangle",
    display_name: str = "Short Strangle",
    family: StrategyFamily = StrategyFamily.SHORT_STRANGLE,
    priority: int = 650,
    enabled: bool = True,
    supported_underlyings: tuple[str, ...] = (),
    requires_volatility: bool = False,
    min_contracts: int = 1,
) -> BaseStrategy:
    """Build registry-ready strategy plugin."""

    class _Plugin(EchoEvaluateStrategy):
        pass

    metadata = valid_metadata(
        strategy_id=strategy_id,
        display_name=display_name,
        strategy_family=family,
        supported_underlyings=supported_underlyings,
        requires_volatility_snapshot=requires_volatility,
        min_contracts_required=min_contracts,
    )
    return _Plugin(StrategyPluginConfig(metadata=metadata, priority=priority, enabled=enabled))


class FailingStrategy(BaseStrategy):
    """Strategy that raises on run."""

    def _execute(self, context: StrategyContext) -> TradingSignal:
        raise RuntimeError("boom")


class ContextErrorStrategy(BaseStrategy):
    """Strategy that raises StrategyContextError."""

    def _execute(self, context: StrategyContext) -> TradingSignal:
        raise StrategyContextError("bad context", field="snapshot")


class SignalErrorStrategy(BaseStrategy):
    """Strategy that raises StrategySignalError."""

    def _execute(self, context: StrategyContext) -> TradingSignal:
        raise StrategySignalError("bad signal", field="signal_id")


class BadSignalStrategy(BaseStrategy):
    """Strategy returning invalid signal id mismatch."""

    def _execute(self, context: StrategyContext) -> TradingSignal:
        score = 50.0
        return TradingSignal(
            signal_id="bad",
            strategy_id="wrong_id",
            strategy_version=self.metadata.version,
            strategy_family=self.metadata.strategy_family,
            action=SignalAction.EVALUATE,
            direction=SignalDirection.NEUTRAL,
            confidence=SignalConfidence(
                score=score,
                band=confidence_band_for_score(score),
            ),
            market=market_context_from_snapshot(context.snapshot),
            as_of=context.as_of,
            reasons=("bad",),
        )


class UnexpectedExceptionStrategy(BaseStrategy):
    """Strategy whose run() raises an unexpected exception."""

    def _execute(self, context: StrategyContext) -> TradingSignal:
        raise RuntimeError("never reached")

    def run(self, context: StrategyContext) -> TradingSignal:
        raise ValueError("unexpected plugin failure")


class NullSignalStrategy(BaseStrategy):
    """Strategy whose run() bypasses validation and returns None."""

    def _execute(self, context: StrategyContext) -> TradingSignal:
        return build_evaluate_signal(self, context)

    def run(self, context: StrategyContext) -> TradingSignal | None:
        return None


class BypassValidationStrategy(BaseStrategy):
    """Strategy whose run() returns a signal without engine-side validation."""

    def _execute(self, context: StrategyContext) -> TradingSignal:
        return build_evaluate_signal(self, context)

    def run(self, context: StrategyContext) -> TradingSignal:
        signal = build_evaluate_signal(self, context)
        return replace(signal, strategy_id="wrong_strategy_id")


class BypassEmptyReasonsStrategy(BaseStrategy):
    """Strategy whose run() returns a schema-invalid signal with matching strategy_id."""

    def _execute(self, context: StrategyContext) -> TradingSignal:
        return build_evaluate_signal(self, context)

    def run(self, context: StrategyContext) -> TradingSignal:
        signal = build_evaluate_signal(self, context)
        return replace(signal, reasons=())


def setup_registry(*strategies: BaseStrategy, clock: FixedClock | None = None) -> tuple[StrategyRegistry, object]:
    """Register strategies and return registry + frozen snapshot."""
    reg = StrategyRegistry(clock=clock or FixedClock())
    for strategy in strategies:
        reg.register(strategy)
    return reg, reg.freeze()


def make_run_context(
    registry_snapshot: object,
    *,
    snapshot: object | None = None,
    correlation_id: str = "corr-eval-001",
) -> EvaluationRunContext:
    """Build valid evaluation run context."""
    snap = snapshot or minimal_valid_snapshot()
    return EvaluationRunContext(
        correlation_id=correlation_id,
        as_of=fixed_as_of(),
        snapshot=snap,
        registry_snapshot=registry_snapshot,
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def registry_and_snapshot(clock: FixedClock) -> tuple[StrategyRegistry, object]:
    return setup_registry(make_strategy(), clock=clock)


@pytest.fixture
def engine(registry_and_snapshot: tuple[StrategyRegistry, object], clock: FixedClock) -> StrategyEvaluationEngine:
    registry, _ = registry_and_snapshot
    return StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), registry, clock=clock)


class TestConfiguration:
    def test_missing_registry_raises(self) -> None:
        with pytest.raises(StrategyEvaluationConfigurationError):
            StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), None)

    def test_invalid_max_parallelism(self) -> None:
        with pytest.raises(StrategyEvaluationConfigurationError):
            StrategyEvaluationEngineConfig(max_parallelism=0)

    def test_invalid_scoring_weights(self) -> None:
        with pytest.raises(StrategyEvaluationConfigurationError):
            EvaluationScoringPolicy(
                weight_suitability=0.0,
                weight_confidence=0.0,
                weight_pop=0.0,
                weight_priority=0.0,
            )


class TestContextValidation:
    def test_empty_correlation_id(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, snap = registry_and_snapshot
        ctx = make_run_context(snap, correlation_id="  ")
        with pytest.raises(StrategyEvaluationContextError):
            engine.validate_run_context(ctx)

    def test_naive_as_of(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, snap = registry_and_snapshot
        ctx = replace(make_run_context(snap), as_of=datetime(2026, 8, 3, 10, 0, 0))
        with pytest.raises(StrategyEvaluationContextError):
            engine.validate_run_context(ctx)

    def test_invalid_snapshot_live_rejected(
        self,
        registry_and_snapshot: tuple,
        clock: FixedClock,
    ) -> None:
        from market_data.market_snapshot import SnapshotValidationStatus

        registry, reg_snap = registry_and_snapshot
        eng = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), registry, clock=clock)
        snap = minimal_valid_snapshot()
        bad_quality = replace(snap.quality, validation_status=SnapshotValidationStatus.INVALID)
        snap = replace(snap, quality=bad_quality)
        ctx = make_run_context(reg_snap, snapshot=snap)
        with pytest.raises(StrategyEvaluationContextError):
            eng.validate_run_context(ctx)


class TestEvaluationHappyPath:
    def test_single_strategy_success(
        self,
        engine: StrategyEvaluationEngine,
        registry_and_snapshot: tuple,
    ) -> None:
        _, reg_snap = registry_and_snapshot
        bundle = engine.evaluate_bundle(make_run_context(reg_snap))
        assert len(bundle.reports) == 1
        report = bundle.reports[0]
        assert report.evaluation_status is EvaluationStatus.SUCCESS
        assert report.outcome_class is EvaluationOutcomeClass.ACTIONABLE
        assert report.signal is not None
        assert report.suitability_score > 0
        assert report.expected_pop > 0
        assert report.reasons
        assert report.factors

    def test_ranked_reports_permutation(
        self,
        engine: StrategyEvaluationEngine,
        clock: FixedClock,
    ) -> None:
        reg, snap = setup_registry(
            make_strategy(strategy_id="alpha", priority=700),
            make_strategy(strategy_id="beta", family=StrategyFamily.IRON_CONDOR, priority=600),
            clock=clock,
        )
        eng = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        bundle = eng.evaluate_bundle(make_run_context(snap))
        assert sorted(r.strategy_id for r in bundle.reports) == sorted(
            r.strategy_id for r in bundle.ranked_reports
        )
        assert bundle.summary.total_actionable == 2

    def test_engine_result_success(
        self,
        engine: StrategyEvaluationEngine,
        registry_and_snapshot: tuple,
    ) -> None:
        _, reg_snap = registry_and_snapshot
        ctx = make_run_context(reg_snap)
        result = engine.evaluate(
            EngineContext(correlation_id="corr-001", as_of=fixed_as_of(), payload=ctx)
        )
        assert result.status is EngineStatus.SUCCESS
        assert result.payload is not None
        assert result.payload.summary.total_enabled == 1

    def test_evaluate_direct_run_context(
        self,
        engine: StrategyEvaluationEngine,
        registry_and_snapshot: tuple,
    ) -> None:
        _, reg_snap = registry_and_snapshot
        result = engine.evaluate(make_run_context(reg_snap))
        assert result.status is EngineStatus.SUCCESS


class TestAbstainAndFailure:
    def test_abstain_strategy(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(AbstainOnlyStrategy(valid_plugin_config(metadata=valid_metadata(strategy_id="abstain"))))
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        report = bundle.reports[0]
        assert report.evaluation_status is EvaluationStatus.ABSTAIN
        assert report.outcome_class is EvaluationOutcomeClass.NO_TRADE
        assert report.suitability_score <= 25.0

    def test_failing_strategy_continue(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy(strategy_id="good"))
        reg.register(FailingStrategy(valid_plugin_config(metadata=valid_metadata(strategy_id="bad"))))
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert bundle.summary.total_failed == 1
        assert bundle.summary.total_success == 1
        result = engine.evaluate(make_run_context(snap))
        assert result.status is EngineStatus.PARTIAL

    def test_fail_fast(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(
            FailingStrategy(
                valid_plugin_config(
                    metadata=valid_metadata(strategy_id="bad_first"),
                    priority=700,
                )
            )
        )
        reg.register(make_strategy(strategy_id="second", priority=500))
        snap = reg.freeze()
        config = StrategyEvaluationEngineConfig(failure_mode=EvaluationFailureMode.FAIL_FAST)
        engine = StrategyEvaluationEngine(config, reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert len(bundle.reports) == 1
        assert bundle.reports[0].strategy_id == "bad_first"


class TestSkipPolicy:
    def test_unsupported_underlying_skipped(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy(strategy_id="nifty_only", supported_underlyings=("BANKNIFTY",)))
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert bundle.reports[0].evaluation_status is EvaluationStatus.SKIPPED

    def test_missing_volatility_skipped(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy(strategy_id="vol_req", requires_volatility=True))
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert bundle.reports[0].evaluation_status is EvaluationStatus.SKIPPED

    def test_insufficient_contracts_skipped(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy(strategy_id="needs_many", min_contracts=10))
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert bundle.reports[0].evaluation_status is EvaluationStatus.SKIPPED


class TestRegistryIntegration:
    def test_empty_enabled_set(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy(), enabled=False)
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert bundle.reports == ()
        assert any(w.code == ERROR_EMPTY_ENABLED_SET for w in bundle.warnings)

    def test_strict_registry_mismatch(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy())
        snap = reg.snapshot()
        reg.register(make_strategy(strategy_id="extra", family=StrategyFamily.IRON_CONDOR))
        config = StrategyEvaluationEngineConfig(strict_registry_match=True)
        engine = StrategyEvaluationEngine(config, reg, clock=clock)
        with pytest.raises(StrategyEvaluationRegistryError) as exc_info:
            engine.evaluate_bundle(make_run_context(snap))
        assert exc_info.value.code == ERROR_REGISTRY_FINGERPRINT_MISMATCH

    def test_missing_plugin_in_registry(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy())
        snap = reg.snapshot()
        reg.unregister("short_strangle")
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert bundle.reports[0].evaluation_status is EvaluationStatus.FAILED
        assert bundle.reports[0].errors[0].code == ERROR_REGISTRY_PLUGIN_MISSING

    def test_bad_signal_validation(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(BadSignalStrategy(valid_plugin_config(metadata=valid_metadata(strategy_id="bad_signal"))))
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert bundle.reports[0].evaluation_status is EvaluationStatus.FAILED


class TestScoringAndRanking:
    def test_classify_outcome(self) -> None:
        assert classify_outcome(None, evaluation_status=EvaluationStatus.FAILED) is EvaluationOutcomeClass.ERROR
        from strategy.strategy_evaluation_engine import EvaluationStatus as ES

        ctx = StrategyContext(
            correlation_id="c",
            as_of=fixed_as_of(),
            snapshot=minimal_valid_snapshot(),
        )
        plugin = make_strategy()
        signal = plugin.run(ctx)
        assert classify_outcome(signal, evaluation_status=ES.SUCCESS) is EvaluationOutcomeClass.ACTIONABLE

    def test_wait_action_monitor_outcome(self, clock: FixedClock) -> None:
        class WaitStrategy(BaseStrategy):
            def _execute(self, context: StrategyContext) -> TradingSignal:
                score = 55.0
                return TradingSignal(
                    signal_id="wait-signal",
                    strategy_id=self.metadata.strategy_id,
                    strategy_version=self.metadata.version,
                    strategy_family=self.metadata.strategy_family,
                    action=SignalAction.WAIT,
                    direction=SignalDirection.NEUTRAL,
                    confidence=SignalConfidence(
                        score=score,
                        band=confidence_band_for_score(score),
                    ),
                    market=market_context_from_snapshot(context.snapshot),
                    as_of=context.as_of,
                    reasons=("wait for setup",),
                )

        reg = StrategyRegistry(clock=clock)
        reg.register(WaitStrategy(valid_plugin_config(metadata=valid_metadata(strategy_id="waiter"))))
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        report = engine.evaluate_bundle(make_run_context(snap)).reports[0]
        assert report.outcome_class is EvaluationOutcomeClass.MONITOR
        assert report.ranking_score <= 60.0

    def test_scorer_with_risk_metadata(self, clock: FixedClock) -> None:
        from strategy.signals import MarginIntensityHint, RiskLevelHint, RiskProfileHint, SignalRiskMetadata, TargetHint, TargetHintType

        class RichSignalStrategy(EchoEvaluateStrategy):
            def _execute(self, context: StrategyContext) -> TradingSignal:
                signal = build_evaluate_signal(self, context)
                return replace(
                    signal,
                    risk=SignalRiskMetadata(
                        profile=RiskProfileHint.DEFINED,
                        max_loss_category="HIGH",
                        margin_intensity=MarginIntensityHint.HIGH,
                        tail_risk=RiskLevelHint.HIGH,
                    ),
                    target=TargetHint(
                        hint_type=TargetHintType.PREMIUM_DECAY_PERCENT,
                        reference="net_credit",
                        value=50.0,
                    ),
                )

        reg = StrategyRegistry(clock=clock)
        reg.register(
            RichSignalStrategy(
                valid_plugin_config(metadata=valid_metadata(strategy_id="rich", strategy_family=StrategyFamily.IRON_CONDOR))
            )
        )
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        report = engine.evaluate_bundle(make_run_context(snap)).reports[0]
        assert report.expected_risk.category.value in {"high", "elevated", "undefined"}
        assert report.capital_estimate.category.value == "large"

    def test_undefined_risk_profile(self, clock: FixedClock) -> None:
        from strategy.signals import RiskProfileHint, SignalRiskMetadata

        class UndefinedRiskStrategy(EchoEvaluateStrategy):
            def _execute(self, context: StrategyContext) -> TradingSignal:
                signal = build_evaluate_signal(self, context)
                return replace(
                    signal,
                    risk=SignalRiskMetadata(profile=RiskProfileHint.UNDEFINED),
                )

        reg = StrategyRegistry(clock=clock)
        reg.register(
            UndefinedRiskStrategy(
                valid_plugin_config(metadata=valid_metadata(strategy_id="undef_risk"))
            )
        )
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        report = engine.evaluate_bundle(make_run_context(snap)).reports[0]
        assert report.expected_risk.category.value == "undefined"

    def test_rank_reports_order(self, engine: StrategyEvaluationEngine, clock: FixedClock) -> None:
        reg, snap = setup_registry(
            make_strategy(strategy_id="alpha", priority=700),
            make_strategy(strategy_id="beta", family=StrategyFamily.IRON_CONDOR, priority=600),
            clock=clock,
        )
        eng = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        bundle = eng.evaluate_bundle(make_run_context(snap))
        ranked_ids = [r.strategy_id for r in bundle.ranked_reports]
        assert ranked_ids[0] in {"alpha", "beta"}

    def test_fingerprint_stable(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        ctx = make_run_context(reg_snap)
        b1 = engine.evaluate_bundle(ctx)
        b2 = engine.evaluate_bundle(ctx)
        assert b1.bundle_fingerprint == b2.bundle_fingerprint


class TestParallelMode:
    def test_parallel_evaluation(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        for index in range(6):
            reg.register(
                make_strategy(
                    strategy_id=f"plugin_{index:02d}",
                    display_name=f"Plugin {index}",
                    priority=500 + index,
                )
            )
        snap = reg.freeze()
        config = StrategyEvaluationEngineConfig(
            parallelism_mode=EvaluationParallelismMode.PARALLEL,
            max_parallelism=4,
        )
        engine = StrategyEvaluationEngine(config, reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert len(bundle.reports) == 6
        assert bundle.summary.total_success == 6

    def test_parallel_fail_fast(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(
            FailingStrategy(
                valid_plugin_config(
                    metadata=valid_metadata(strategy_id="bad_first"),
                    priority=700,
                )
            )
        )
        reg.register(make_strategy(strategy_id="second", priority=500))
        snap = reg.freeze()
        config = StrategyEvaluationEngineConfig(
            parallelism_mode=EvaluationParallelismMode.PARALLEL,
            failure_mode=EvaluationFailureMode.FAIL_FAST,
        )
        engine = StrategyEvaluationEngine(config, reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert len(bundle.reports) >= 1


class TestBundleValidation:
    def test_assert_valid_bundle(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        bundle = engine.evaluate_bundle(make_run_context(reg_snap))
        engine.assert_valid_bundle(bundle)

    def test_invalid_bundle_fingerprint(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        bundle = engine.evaluate_bundle(make_run_context(reg_snap))
        bad = replace(bundle, bundle_fingerprint="deadbeef")
        result = engine.validate_bundle(bad)
        assert not result.is_valid

    def test_assert_valid_raises(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        bundle = engine.evaluate_bundle(make_run_context(reg_snap))
        bad = replace(bundle, bundle_fingerprint="invalid")
        with pytest.raises(StrategyEvaluationValidationError):
            engine.assert_valid_bundle(bad)

    def test_duplicate_strategy_id_invalid(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        bundle = engine.evaluate_bundle(make_run_context(reg_snap))
        dup = replace(bundle, reports=(bundle.reports[0], bundle.reports[0]))
        result = engine.validate_bundle(dup)
        assert not result.is_valid

    def test_success_report_missing_signal_invalid(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        bundle = engine.evaluate_bundle(make_run_context(reg_snap))
        bad_report = replace(bundle.reports[0], signal=None)
        bad = replace(bundle, reports=(bad_report,))
        result = engine.validate_bundle(bad)
        assert not result.is_valid

    def test_failed_report_missing_errors_invalid(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        bundle = engine.evaluate_bundle(make_run_context(reg_snap))
        bad_report = replace(
            bundle.reports[0],
            evaluation_status=EvaluationStatus.FAILED,
            errors=(),
        )
        bad = replace(bundle, reports=(bad_report,))
        result = engine.validate_bundle(bad)
        assert not result.is_valid

    def test_out_of_bounds_suitability_invalid(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        bundle = engine.evaluate_bundle(make_run_context(reg_snap))
        bad_report = replace(bundle.reports[0], suitability_score=150.0)
        bad = replace(bundle, reports=(bad_report,))
        result = engine.validate_bundle(bad)
        assert not result.is_valid


class TestSerialization:
    def test_report_to_dict(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        report = engine.evaluate_bundle(make_run_context(reg_snap)).reports[0]
        payload = report_to_dict(report)
        assert payload["strategy_id"] == "short_strangle"
        assert "signal" in payload

    def test_bundle_json(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        bundle = engine.evaluate_bundle(make_run_context(reg_snap))
        data = bundle_to_dict(bundle)
        assert data["schema_version"] == "1.0.0"
        assert bundle_to_json(bundle)

    def test_bundle_from_json_unsupported_version(self) -> None:
        with pytest.raises(StrategyEvaluationValidationError) as exc_info:
            bundle_from_json('{"schema_version":"9.9.9"}')
        assert exc_info.value.code == ERROR_SERIALIZATION_UNSUPPORTED_VERSION

    def test_bundle_from_json_malformed(self) -> None:
        with pytest.raises(StrategyEvaluationValidationError) as exc_info:
            bundle_from_json("{bad")
        assert exc_info.value.code == ERROR_SERIALIZATION_MALFORMED


class TestWarnings:
    def test_no_actionable_warning(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(AbstainOnlyStrategy(valid_plugin_config(metadata=valid_metadata(strategy_id="abstain"))))
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert any(w.code == ERROR_NO_ACTIONABLE for w in bundle.warnings)

    def test_partial_failure_warning(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy(strategy_id="good"))
        reg.register(FailingStrategy(valid_plugin_config(metadata=valid_metadata(strategy_id="bad"))))
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert any(w.code == ERROR_PARTIAL_FAILURE for w in bundle.warnings)


class TestThreadSafety:
    def test_concurrent_evaluate(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy())
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        errors: list[Exception] = []

        def worker(index: int) -> None:
            try:
                ctx = make_run_context(snap, correlation_id=f"corr-{index:03d}")
                engine.evaluate_bundle(ctx)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker, index) for index in range(8)]
            for future in as_completed(futures):
                future.result()
        assert not errors


class TestPerformanceSmoke:
    def test_evaluate_32_plugins(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        for index in range(32):
            reg.register(
                make_strategy(
                    strategy_id=f"plugin_{index:02d}",
                    display_name=f"Plugin {index}",
                    priority=500 + index,
                )
            )
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        start = time.perf_counter()
        engine.evaluate_bundle(make_run_context(snap))
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0


class TestEngineProperties:
    def test_engine_name_and_version(self, engine: StrategyEvaluationEngine) -> None:
        assert engine.engine_name == "strategy_evaluation_engine"
        assert engine.engine_version == "1.0.0"

    def test_invalid_evaluate_context_type(self, engine: StrategyEvaluationEngine) -> None:
        with pytest.raises(StrategyEvaluationValidationError):
            engine.evaluate("not-a-context")  # type: ignore[arg-type]

    def test_engine_context_wrong_payload(self, engine: StrategyEvaluationEngine) -> None:
        ctx = EngineContext(correlation_id="c", as_of=fixed_as_of(), payload={"bad": True})
        with pytest.raises(StrategyEvaluationValidationError):
            engine.evaluate(ctx)

    def test_all_failed_status(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(FailingStrategy(valid_plugin_config(metadata=valid_metadata(strategy_id="bad"))))
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        result = engine.evaluate(make_run_context(snap))
        assert result.status is EngineStatus.FAILED


class TestExceptionPaths:
    def test_context_error_strategy(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(ContextErrorStrategy(valid_plugin_config(metadata=valid_metadata(strategy_id="ctx_bad"))))
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        report = engine.evaluate_bundle(make_run_context(snap)).reports[0]
        assert report.evaluation_status is EvaluationStatus.FAILED

    def test_signal_error_strategy(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(SignalErrorStrategy(valid_plugin_config(metadata=valid_metadata(strategy_id="sig_bad"))))
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        report = engine.evaluate_bundle(make_run_context(snap)).reports[0]
        assert report.evaluation_status is EvaluationStatus.FAILED

    def test_evaluation_fingerprint_function(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        reports = engine.evaluate_bundle(make_run_context(reg_snap)).reports
        fp = evaluation_fingerprint(reports)
        assert len(fp) == 64

    def test_rank_reports_helper(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        reports = engine.evaluate_bundle(make_run_context(reg_snap)).reports
        ranked = rank_reports(reports)
        assert ranked == reports


class TestHelperFunctions:
    def test_apply_outcome_helpers(self) -> None:
        from strategy.strategy_evaluation_engine import _apply_outcome_tier_cap

        assert _apply_outcome_tier_cap(80.0, EvaluationOutcomeClass.NO_TRADE) == 20.0
        assert _apply_outcome_tier_cap(80.0, EvaluationOutcomeClass.MONITOR) == 60.0
        assert _apply_outcome_tier_cap(80.0, EvaluationOutcomeClass.ERROR) == 0.0

    def test_vol_adjustment_with_vix(self) -> None:
        from market_data.market_snapshot import VolatilitySnapshot
        from strategy.strategy_evaluation_engine import _vol_adjustment

        snap = minimal_valid_snapshot()
        snap = replace(
            snap,
            volatility=VolatilitySnapshot(
                symbol="INDIA VIX",
                exchange="NSE",
                quote_key="NSE:INDIA VIX",
                last_price=22.0,
                quote_timestamp=fixed_as_of(),
            ),
        )
        assert _vol_adjustment(snap) == 0.05

    def test_metadata_drift_warning(self, clock: FixedClock) -> None:
        from strategy.strategy_evaluation_engine import WARN_METADATA_DRIFT

        reg = StrategyRegistry(clock=clock)
        strategy = make_strategy()
        reg.register(strategy)
        snap = reg.snapshot()
        object.__setattr__(
            strategy,
            "_plugin_config",
            replace(strategy.plugin_config, metadata=valid_metadata(strategy_id="short_strangle", version="9.9.9")),
        )
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        report = engine.evaluate_bundle(make_run_context(snap)).reports[0]
        assert any(w.code == WARN_METADATA_DRIFT for w in report.warnings)

    def test_invalid_snapshot_analysis_allowed(self, clock: FixedClock) -> None:
        from market_data.market_snapshot import SnapshotValidationStatus

        reg = StrategyRegistry(clock=clock)
        snap = minimal_valid_snapshot()
        bad_quality = replace(snap.quality, validation_status=SnapshotValidationStatus.INVALID)
        snap = replace(snap, quality=bad_quality)
        config = StrategyEvaluationEngineConfig(
            reject_invalid_snapshot_live=False,
            allow_invalid_snapshot_analysis=True,
        )
        engine = StrategyEvaluationEngine(config, reg, clock=clock)
        ctx = EvaluationRunContext(
            correlation_id="corr-analysis",
            as_of=fixed_as_of(),
            snapshot=snap,
            registry_snapshot=reg.snapshot(),
            execution_mode=StrategyExecutionMode.ANALYSIS,
        )
        engine.validate_run_context(ctx)

    def test_evaluate_rejected_on_context_error(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy())
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        ctx = replace(make_run_context(snap), snapshot=None)  # type: ignore[arg-type]
        result = engine.evaluate(ctx)
        assert result.status is EngineStatus.REJECTED
        assert result.payload is None

    def test_permutation_mismatch_validation(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        bundle = engine.evaluate_bundle(make_run_context(reg_snap))
        bad = replace(bundle, ranked_reports=())
        result = engine.validate_bundle(bad)
        assert not result.is_valid

    def test_scoring_helper_branches(self) -> None:
        from strategy.strategy_evaluation_engine import (
            EvaluationScorer,
            EvaluationScoringPolicy,
            _action_pop_adjustment,
            _family_fit_score,
            _liquidity_hint_score,
        )
        from strategy.signals import SignalDirection

        snap = minimal_valid_snapshot()
        ctx = StrategyContext(correlation_id="c", as_of=fixed_as_of(), snapshot=snap)
        plugin = make_strategy(family=StrategyFamily.BULL_PUT_SPREAD)
        signal = replace(
            plugin.run(ctx),
            direction=SignalDirection.BULLISH,
            strategy_family=StrategyFamily.BULL_PUT_SPREAD,
        )
        assert _action_pop_adjustment(SignalAction.WAIT) == -0.10
        assert _family_fit_score(
            replace(signal, strategy_family=StrategyFamily.NO_STRATEGY),
            snap,
        ) == 0.0
        assert _liquidity_hint_score(snap) >= 0.0
        scorer = EvaluationScorer()
        reg, _ = setup_registry(make_strategy(family=StrategyFamily.SHORT_STRANGLE))
        record_obj = reg.get_record("short_strangle")
        result = scorer.score(
            signal=signal,
            snapshot=snap,
            record=record_obj,
            policy=EvaluationScoringPolicy(),
            outcome_class=EvaluationOutcomeClass.ACTIONABLE,
            execution_mode=StrategyExecutionMode.LIVE,
        )
        assert result.suitability_score >= 0.0

    def test_validate_context_none_registry_snapshot(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        ctx = replace(make_run_context(reg_snap), registry_snapshot=None)  # type: ignore[arg-type]
        with pytest.raises(StrategyEvaluationContextError):
            engine.validate_run_context(ctx)

    def test_validate_context_none_snapshot(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        ctx = replace(make_run_context(reg_snap), snapshot=None)  # type: ignore[arg-type]
        with pytest.raises(StrategyEvaluationContextError):
            engine.validate_run_context(ctx)


class TestCoverageGaps:
    """Targeted tests for remaining uncovered branches."""

    def test_engine_properties(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        reg, _ = registry_and_snapshot
        assert isinstance(engine.eval_config, StrategyEvaluationEngineConfig)
        assert engine.registry is reg

    def test_default_clock_uses_utc_now(self) -> None:
        reg = StrategyRegistry()
        reg.register(make_strategy())
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg)
        bundle = engine.evaluate_bundle(make_run_context(snap))
        assert bundle.evaluated_at.tzinfo is not None

    def test_empty_enabled_evaluate_success_status(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy(), enabled=False)
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        result = engine.evaluate(make_run_context(snap))
        assert result.status is EngineStatus.SUCCESS

    def test_parallel_empty_records(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        ctx = make_run_context(reg_snap)
        assert engine._evaluate_parallel((), ctx) == ()

    def test_unexpected_plugin_exception(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(
            UnexpectedExceptionStrategy(
                valid_plugin_config(metadata=valid_metadata(strategy_id="unexpected"))
            )
        )
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        report = engine.evaluate_bundle(make_run_context(snap)).reports[0]
        assert report.evaluation_status is EvaluationStatus.FAILED
        assert "unexpected" in report.errors[0].message.lower()

    def test_null_signal_from_plugin(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(
            NullSignalStrategy(
                valid_plugin_config(metadata=valid_metadata(strategy_id="null_signal"))
            )
        )
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        report = engine.evaluate_bundle(make_run_context(snap)).reports[0]
        assert report.evaluation_status is EvaluationStatus.FAILED
        assert "none signal" in report.errors[0].message.lower()

    def test_bypass_validation_signal_mismatch(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(
            BypassValidationStrategy(
                valid_plugin_config(metadata=valid_metadata(strategy_id="bypass_bad"))
            )
        )
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        report = engine.evaluate_bundle(make_run_context(snap)).reports[0]
        assert report.evaluation_status is EvaluationStatus.FAILED
        assert "mismatch" in report.errors[0].message.lower()

    def test_bypass_validation_empty_reasons(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(
            BypassEmptyReasonsStrategy(
                valid_plugin_config(metadata=valid_metadata(strategy_id="bypass_empty"))
            )
        )
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        report = engine.evaluate_bundle(make_run_context(snap)).reports[0]
        assert report.evaluation_status is EvaluationStatus.FAILED
        assert "reasons" in report.errors[0].message.lower()

    def test_classify_outcome_skipped_and_no_trade(self) -> None:
        ctx = StrategyContext(
            correlation_id="c",
            as_of=fixed_as_of(),
            snapshot=minimal_valid_snapshot(),
        )
        signal = make_strategy().run(ctx)
        assert classify_outcome(None, evaluation_status=EvaluationStatus.SUCCESS) is EvaluationOutcomeClass.ERROR
        assert classify_outcome(signal, evaluation_status=EvaluationStatus.SKIPPED) is EvaluationOutcomeClass.ERROR
        no_trade_signal = replace(signal, action=SignalAction.NO_TRADE)
        assert (
            classify_outcome(no_trade_signal, evaluation_status=EvaluationStatus.SUCCESS)
            is EvaluationOutcomeClass.NO_TRADE
        )

    def test_context_naive_provenance(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        snap = minimal_valid_snapshot()
        bad_prov = replace(snap.provenance, as_of=datetime(2026, 8, 3, 10, 0, 0))
        snap = replace(snap, provenance=bad_prov)
        ctx = make_run_context(reg_snap, snapshot=snap)
        with pytest.raises(StrategyEvaluationContextError):
            engine.validate_run_context(ctx)

    def test_context_underlying_mismatch(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        snap = minimal_valid_snapshot()
        chain = replace(
            snap.option_chain,
            metadata=replace(snap.option_chain.metadata, underlying="BANKNIFTY"),
        )
        snap = replace(snap, option_chain=chain)
        ctx = make_run_context(reg_snap, snapshot=snap)
        with pytest.raises(StrategyEvaluationContextError):
            engine.validate_run_context(ctx)

    def test_context_invalid_snapshot_analysis_rejected(self, clock: FixedClock) -> None:
        from market_data.market_snapshot import SnapshotValidationStatus

        reg = StrategyRegistry(clock=clock)
        snap = minimal_valid_snapshot()
        bad_quality = replace(snap.quality, validation_status=SnapshotValidationStatus.INVALID)
        snap = replace(snap, quality=bad_quality)
        config = StrategyEvaluationEngineConfig(allow_invalid_snapshot_analysis=False)
        engine = StrategyEvaluationEngine(config, reg, clock=clock)
        ctx = EvaluationRunContext(
            correlation_id="corr-invalid",
            as_of=fixed_as_of(),
            snapshot=snap,
            registry_snapshot=reg.snapshot(),
            execution_mode=StrategyExecutionMode.ANALYSIS,
        )
        with pytest.raises(StrategyEvaluationContextError):
            engine.validate_run_context(ctx)

    def test_scorer_invalid_snapshot_live_cap(self) -> None:
        from market_data.market_snapshot import SnapshotValidationStatus
        from strategy.strategy_evaluation_engine import EvaluationScorer, EvaluationScoringPolicy

        valid_snap = minimal_valid_snapshot()
        ctx = StrategyContext(correlation_id="c", as_of=fixed_as_of(), snapshot=valid_snap)
        plugin = make_strategy()
        signal = plugin.run(ctx)
        invalid_snap = replace(
            valid_snap,
            quality=replace(valid_snap.quality, validation_status=SnapshotValidationStatus.INVALID),
        )
        reg, _ = setup_registry(make_strategy())
        record = reg.get_record("short_strangle")
        result = EvaluationScorer().score(
            signal=signal,
            snapshot=invalid_snap,
            record=record,
            policy=EvaluationScoringPolicy(),
            outcome_class=EvaluationOutcomeClass.ACTIONABLE,
            execution_mode=StrategyExecutionMode.LIVE,
        )
        assert result.suitability_score <= 10.0
        assert any("invalid for live" in w.message.lower() for w in result.warnings)

    def test_scorer_partial_and_stale_snapshot(self) -> None:
        from market_data.market_snapshot import SnapshotFreshnessStatus, SnapshotValidationStatus
        from strategy.strategy_evaluation_engine import EvaluationScorer, EvaluationScoringPolicy, WARN_STALE

        valid_snap = minimal_valid_snapshot()
        ctx = StrategyContext(correlation_id="c", as_of=fixed_as_of(), snapshot=valid_snap)
        signal = make_strategy().run(ctx)
        snap = replace(
            valid_snap,
            quality=replace(valid_snap.quality, validation_status=SnapshotValidationStatus.PARTIAL),
            freshness=replace(
                valid_snap.freshness,
                is_usable_for_live_decisions=False,
                status=SnapshotFreshnessStatus.STALE,
            ),
        )
        reg, _ = setup_registry(make_strategy())
        record = reg.get_record("short_strangle")
        result = EvaluationScorer().score(
            signal=signal,
            snapshot=snap,
            record=record,
            policy=EvaluationScoringPolicy(),
            outcome_class=EvaluationOutcomeClass.ACTIONABLE,
            execution_mode=StrategyExecutionMode.LIVE,
        )
        assert any(w.code == WARN_STALE for w in result.warnings)

    def test_scoring_helper_family_and_liquidity_branches(self) -> None:
        from market_data.market_snapshot import (
            OptionChainSnapshot,
            SnapshotValidationStatus,
            VolatilitySnapshot,
        )
        from strategy.strategy_evaluation_engine import (
            _family_fit_score,
            _freshness_multiplier,
            _liquidity_hint_score,
            _snapshot_quality_multiplier,
            _vol_adjustment,
        )
        from strategy.signals import SignalDirection
        from tests.test_base_strategy import make_contract

        snap = minimal_valid_snapshot()
        ctx = StrategyContext(correlation_id="c", as_of=fixed_as_of(), snapshot=snap)
        base_signal = make_strategy().run(ctx)

        partial_snap = replace(
            snap,
            quality=replace(snap.quality, validation_status=SnapshotValidationStatus.PARTIAL),
        )
        assert _snapshot_quality_multiplier(partial_snap) == 0.70

        invalid_snap = replace(
            snap,
            quality=replace(snap.quality, validation_status=SnapshotValidationStatus.INVALID),
        )
        assert _snapshot_quality_multiplier(invalid_snap) == 0.0

        stale_snap = replace(
            snap,
            freshness=replace(snap.freshness, is_usable_for_live_decisions=False),
        )
        assert _freshness_multiplier(stale_snap) == 0.5

        empty_chain = replace(
            snap,
            option_chain=OptionChainSnapshot(
                metadata=snap.option_chain.metadata,
                contracts=(),
            ),
        )
        assert _liquidity_hint_score(empty_chain) == 0.0

        no_spread_snap = replace(
            snap,
            option_chain=OptionChainSnapshot(
                metadata=snap.option_chain.metadata,
                contracts=(make_contract(bid=0.0, ask=0.0),),
            ),
        )
        assert _liquidity_hint_score(no_spread_snap) == 50.0

        bearish = replace(
            base_signal,
            strategy_family=StrategyFamily.BEAR_CALL_SPREAD,
            direction=SignalDirection.BEARISH,
        )
        assert _family_fit_score(bearish, snap) == 70.0
        bearish_wrong = replace(bearish, direction=SignalDirection.BULLISH)
        assert _family_fit_score(bearish_wrong, snap) == 40.0

        long_vol = replace(
            base_signal,
            strategy_family=StrategyFamily.LONG_VOLATILITY,
            direction=SignalDirection.LONG_VOL,
        )
        assert _family_fit_score(long_vol, snap) == 65.0
        long_vol_wrong = replace(long_vol, direction=SignalDirection.NEUTRAL)
        assert _family_fit_score(long_vol_wrong, snap) == 45.0

        low_vix_snap = replace(
            snap,
            volatility=VolatilitySnapshot(
                symbol="INDIA VIX",
                exchange="NSE",
                quote_key="NSE:INDIA VIX",
                last_price=10.0,
                quote_timestamp=fixed_as_of(),
            ),
        )
        assert _vol_adjustment(low_vix_snap) == -0.03

        mid_vix_snap = replace(
            snap,
            volatility=VolatilitySnapshot(
                symbol="INDIA VIX",
                exchange="NSE",
                quote_key="NSE:INDIA VIX",
                last_price=15.0,
                quote_timestamp=fixed_as_of(),
            ),
        )
        assert _vol_adjustment(mid_vix_snap) == 0.0
        assert _vol_adjustment(snap) == 0.0

        vol_snap = replace(
            snap,
            volatility=VolatilitySnapshot(
                symbol="INDIA VIX",
                exchange="NSE",
                quote_key="NSE:INDIA VIX",
                last_price=18.0,
                quote_timestamp=fixed_as_of(),
            ),
        )
        assert _family_fit_score(long_vol, vol_snap) == 70.0

    def test_risk_estimates_low_and_elevated(self, clock: FixedClock) -> None:
        from strategy.signals import RiskLevelHint, RiskProfileHint, SignalRiskMetadata

        class LowLossStrategy(EchoEvaluateStrategy):
            def _execute(self, context: StrategyContext) -> TradingSignal:
                signal = build_evaluate_signal(self, context)
                return replace(
                    signal,
                    risk=SignalRiskMetadata(
                        profile=RiskProfileHint.DEFINED,
                        max_loss_category="LOW",
                        gamma_risk=RiskLevelHint.HIGH,
                    ),
                )

        class ElevatedGammaStrategy(EchoEvaluateStrategy):
            def _execute(self, context: StrategyContext) -> TradingSignal:
                signal = build_evaluate_signal(self, context)
                return replace(
                    signal,
                    risk=SignalRiskMetadata(
                        profile=RiskProfileHint.DEFINED,
                        gamma_risk=RiskLevelHint.HIGH,
                    ),
                )

        reg = StrategyRegistry(clock=clock)
        reg.register(
            LowLossStrategy(
                valid_plugin_config(
                    metadata=valid_metadata(
                        strategy_id="low_loss",
                        strategy_family=StrategyFamily.IRON_CONDOR,
                    )
                )
            )
        )
        snap = reg.freeze()
        engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
        report = engine.evaluate_bundle(make_run_context(snap)).reports[0]
        assert report.expected_risk.category.value == "low"

        reg3 = StrategyRegistry(clock=clock)
        reg3.register(
            ElevatedGammaStrategy(
                valid_plugin_config(
                    metadata=valid_metadata(
                        strategy_id="elevated_gamma",
                        strategy_family=StrategyFamily.IRON_CONDOR,
                    )
                )
            )
        )
        snap3 = reg3.freeze()
        report3 = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg3, clock=clock).evaluate_bundle(
            make_run_context(snap3)
        ).reports[0]
        assert report3.expected_risk.category.value == "elevated"

        class LongVolStrategy(EchoEvaluateStrategy):
            def _execute(self, context: StrategyContext) -> TradingSignal:
                return build_evaluate_signal(self, context)

        reg2 = StrategyRegistry(clock=clock)
        reg2.register(
            LongVolStrategy(
                valid_plugin_config(
                    metadata=valid_metadata(
                        strategy_id="long_vol",
                        strategy_family=StrategyFamily.LONG_VOLATILITY,
                    )
                )
            )
        )
        snap2 = reg2.freeze()
        report2 = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg2, clock=clock).evaluate_bundle(
            make_run_context(snap2)
        ).reports[0]
        assert report2.expected_risk.normalized_score == 50.0

    def test_capital_estimates_low_and_moderate(self, clock: FixedClock) -> None:
        from strategy.signals import MarginIntensityHint, RiskProfileHint, SignalRiskMetadata

        class MarginLowStrategy(EchoEvaluateStrategy):
            def _execute(self, context: StrategyContext) -> TradingSignal:
                signal = build_evaluate_signal(self, context)
                return replace(
                    signal,
                    risk=SignalRiskMetadata(
                        profile=RiskProfileHint.DEFINED,
                        margin_intensity=MarginIntensityHint.LOW,
                    ),
                )

        class MarginModerateStrategy(EchoEvaluateStrategy):
            def _execute(self, context: StrategyContext) -> TradingSignal:
                signal = build_evaluate_signal(self, context)
                return replace(
                    signal,
                    risk=SignalRiskMetadata(
                        profile=RiskProfileHint.DEFINED,
                        margin_intensity=MarginIntensityHint.MODERATE,
                    ),
                )

        for strategy_id, strategy_cls, expected_category in (
            ("margin_low", MarginLowStrategy, "small"),
            ("margin_mod", MarginModerateStrategy, "moderate"),
        ):
            reg = StrategyRegistry(clock=clock)
            reg.register(
                strategy_cls(
                    valid_plugin_config(metadata=valid_metadata(strategy_id=strategy_id))
                )
            )
            snap = reg.freeze()
            engine = StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), reg, clock=clock)
            report = engine.evaluate_bundle(make_run_context(snap)).reports[0]
            assert report.capital_estimate.category.value == expected_category

    def test_non_deterministic_fingerprint(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        reports = engine.evaluate_bundle(make_run_context(reg_snap)).reports
        fp_det = evaluation_fingerprint(reports, deterministic=True)
        fp_non = evaluation_fingerprint(reports, deterministic=False)
        assert fp_det != fp_non or len(reports) == 0

    def test_report_to_dict_keep_nulls(self, engine: StrategyEvaluationEngine, registry_and_snapshot: tuple) -> None:
        _, reg_snap = registry_and_snapshot
        report = engine.evaluate_bundle(make_run_context(reg_snap)).reports[0]
        payload = report_to_dict(report, omit_nulls=False)
        assert payload["strategy_id"] == "short_strangle"

    def test_bundle_from_json_invalid_root(self) -> None:
        with pytest.raises(StrategyEvaluationValidationError) as exc_info:
            bundle_from_json('["not-an-object"]')
        assert exc_info.value.code == ERROR_SERIALIZATION_MALFORMED

    def test_bundle_from_json_audit_only(self) -> None:
        with pytest.raises(StrategyEvaluationValidationError) as exc_info:
            bundle_from_json('{"schema_version":"1.0.0","bundle_id":"audit"}')
        assert exc_info.value.code == ERROR_SERIALIZATION_MALFORMED
        assert "audit-only" in str(exc_info.value).lower()
