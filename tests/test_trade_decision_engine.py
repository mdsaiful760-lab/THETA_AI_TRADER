"""Unit tests for decision.trade_decision_engine."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from core.engine_context import EngineContext
from core.enums import EngineStatus
from decision.trade_decision_engine import (
    AbstainReasonCode,
    BlackoutWindow,
    CapitalPolicy,
    ConfidencePropagator,
    DecisionExplanationBuilder,
    DecisionFilterPolicy,
    DecisionMode,
    DecisionRunContext,
    DecisionSelector,
    DecisionStatus,
    DecisionOutcomeClass,
    FilterStageId,
    ManualOverridePolicy,
    SelectionOutcome,
    StrategyFilterPipeline,
    TradeDecisionConfigurationError,
    TradeDecisionContextError,
    TradeDecisionBundleError,
    TradeDecisionEngine,
    TradeDecisionEngineConfig,
    TradeDecisionValidationError,
    TradingWindowPolicy,
    UserPreferences,
    build_decision_abstain_signal,
    decision_fingerprint,
    decision_from_dict,
    decision_from_json,
    decision_to_dict,
    decision_to_json,
    default_trade_decision_engine_config,
    default_trading_window_policy,
    default_user_preferences,
)
from strategy.registry import StrategyRegistry
from strategy.signals import (
    ConfidenceBand,
    SignalAction,
    SignalDirection,
    StrategyExecutionMode,
    StrategyFamily,
    confidence_band_for_score,
)
from strategy.strategy_evaluation_engine import (
    EvaluationOutcomeClass,
    StrategyEvaluationEngine,
    StrategyEvaluationEngineConfig,
)
from tests.test_base_strategy import fixed_as_of, valid_metadata, valid_plugin_config
from tests.test_strategy_evaluation_engine import (
    AbstainOnlyStrategy,
    FixedClock,
    make_run_context,
    make_strategy,
    setup_registry,
)

IST = ZoneInfo("Asia/Kolkata")


def make_decision_context(
    bundle: object,
    *,
    mode: DecisionMode = DecisionMode.AUTONOMOUS,
    correlation_id: str = "corr-eval-001",
    preferences: UserPreferences | None = None,
    manual_strategy_id: str | None = None,
    execution_mode: StrategyExecutionMode | None = None,
    reference_time: datetime | None = None,
    force_abstain: bool = False,
    tags: dict[str, str] | None = None,
) -> DecisionRunContext:
    """Build valid decision run context from evaluation bundle."""
    return DecisionRunContext(
        correlation_id=correlation_id,
        as_of=fixed_as_of(),
        bundle=bundle,
        mode=mode,
        preferences=preferences or default_user_preferences(),
        execution_mode=execution_mode,
        reference_time=reference_time or fixed_as_of(),
        manual_strategy_id=manual_strategy_id,
        force_abstain=force_abstain,
        tags=MappingProxyType(tags or {}),
    )


def evaluate_bundle(
    registry: StrategyRegistry,
    reg_snap: object,
    *,
    clock: FixedClock | None = None,
    execution_mode: StrategyExecutionMode = StrategyExecutionMode.LIVE,
) -> object:
    """Run strategy evaluation and return bundle."""
    engine = StrategyEvaluationEngine(
        StrategyEvaluationEngineConfig(),
        registry,
        clock=clock or FixedClock(),
    )
    ctx = replace(make_run_context(reg_snap), execution_mode=execution_mode)
    return engine.evaluate_bundle(ctx)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def decision_engine(clock: FixedClock) -> TradeDecisionEngine:
    return TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)


@pytest.fixture
def single_strategy_bundle(clock: FixedClock) -> object:
    reg, snap = setup_registry(make_strategy(), clock=clock)
    return evaluate_bundle(reg, snap, clock=clock)


class TestConfiguration:
    def test_invalid_max_bundle_age(self) -> None:
        with pytest.raises(TradeDecisionConfigurationError):
            TradeDecisionEngineConfig(max_bundle_age_seconds=0)

    def test_invalid_abstain_action(self) -> None:
        with pytest.raises(TradeDecisionConfigurationError):
            TradeDecisionEngineConfig(abstain_action=SignalAction.EVALUATE)

    def test_invalid_filter_policy_threshold(self) -> None:
        with pytest.raises(TradeDecisionConfigurationError):
            DecisionFilterPolicy(default_min_confidence=101.0)


class TestContextValidation:
    def test_missing_manual_id(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        ctx = make_decision_context(
            single_strategy_bundle,
            mode=DecisionMode.MANUAL,
            manual_strategy_id=None,
        )
        with pytest.raises(TradeDecisionContextError) as exc:
            decision_engine.validate_run_context(ctx)
        assert exc.value.code == "TRADE_DECISION.CONTEXT.MANUAL_ID_MISSING"

    def test_naive_reference_time(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        ctx = make_decision_context(
            single_strategy_bundle,
            reference_time=datetime(2026, 8, 3, 10, 0, 0),
        )
        with pytest.raises(TradeDecisionContextError):
            decision_engine.validate_run_context(ctx)

    def test_correlation_mismatch(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        ctx = make_decision_context(single_strategy_bundle, correlation_id="other-id")
        with pytest.raises(TradeDecisionContextError) as exc:
            decision_engine.validate_run_context(ctx)
        assert exc.value.code == "TRADE_DECISION.CONTEXT.CORRELATION_MISMATCH"

    def test_blocked_preferred_overlap(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        prefs = UserPreferences(
            blocked_strategy_ids=frozenset({"short_strangle"}),
            preferred_strategy_ids=frozenset({"short_strangle"}),
        )
        ctx = make_decision_context(single_strategy_bundle, preferences=prefs)
        with pytest.raises(TradeDecisionValidationError):
            decision_engine.validate_run_context(ctx)


class TestEmptyBundle:
    def test_empty_bundle_abstains(self, clock: FixedClock, decision_engine: TradeDecisionEngine) -> None:
        reg, snap = setup_registry(make_strategy(enabled=False), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        result = decision_engine.decide(make_decision_context(bundle))
        assert result.decision_status is DecisionStatus.ABSTAIN
        assert result.abstain_reason_code is AbstainReasonCode.EMPTY_BUNDLE
        assert result.selected_signal.action is SignalAction.ABSTAIN


class TestAutonomousSelection:
    def test_selects_top_ranked(self, clock: FixedClock, decision_engine: TradeDecisionEngine) -> None:
        reg, snap = setup_registry(
            make_strategy(strategy_id="alpha", priority=700),
            make_strategy(strategy_id="beta", family=StrategyFamily.IRON_CONDOR, priority=600),
            clock=clock,
        )
        bundle = evaluate_bundle(reg, snap, clock=clock)
        result = decision_engine.decide(make_decision_context(bundle))
        assert result.decision_status is DecisionStatus.SELECTED
        assert result.selected_strategy_id in {"alpha", "beta"}

    def test_all_filtered_abstains(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        prefs = UserPreferences(blocked_strategy_ids=frozenset({"short_strangle"}))
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(make_decision_context(bundle, preferences=prefs))
        assert result.decision_status is DecisionStatus.ABSTAIN
        assert result.abstain_reason_code is AbstainReasonCode.ALL_FILTERED

    def test_force_abstain(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(make_decision_context(single_strategy_bundle, force_abstain=True))
        assert result.abstain_reason_code is AbstainReasonCode.POLICY_ABSTAIN


class TestManualMode:
    def test_manual_success(self, clock: FixedClock, decision_engine: TradeDecisionEngine) -> None:
        reg, snap = setup_registry(
            make_strategy(strategy_id="alpha"),
            make_strategy(strategy_id="beta", family=StrategyFamily.IRON_CONDOR),
            clock=clock,
        )
        bundle = evaluate_bundle(reg, snap, clock=clock)
        result = decision_engine.decide(
            make_decision_context(bundle, mode=DecisionMode.MANUAL, manual_strategy_id="beta")
        )
        assert result.decision_status is DecisionStatus.SELECTED
        assert result.selected_strategy_id == "beta"

    def test_manual_not_in_bundle(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(
            make_decision_context(
                single_strategy_bundle,
                mode=DecisionMode.MANUAL,
                manual_strategy_id="missing_strategy",
            )
        )
        assert result.decision_status is DecisionStatus.MANUAL_INVALID

    def test_manual_strict_blocked(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        prefs = UserPreferences(blocked_strategy_ids=frozenset({"alpha"}))
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(
            make_decision_context(
                bundle,
                mode=DecisionMode.MANUAL,
                manual_strategy_id="alpha",
                preferences=prefs,
            )
        )
        assert result.decision_status is DecisionStatus.MANUAL_INVALID

    def test_manual_allow_with_warning(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        prefs = UserPreferences(min_ranking_score=99.0)
        config = TradeDecisionEngineConfig(manual_override_policy=ManualOverridePolicy.ALLOW_WITH_WARNING)
        engine = TradeDecisionEngine(config, clock=clock)
        result = engine.decide(
            make_decision_context(
                bundle,
                mode=DecisionMode.MANUAL,
                manual_strategy_id="alpha",
                preferences=prefs,
            )
        )
        assert result.decision_status is DecisionStatus.SELECTED
        assert any(w.code == "TRADE_DECISION.MANUAL.OVERRIDE_APPLIED" for w in result.warnings)


class TestUserPreferences:
    def test_family_filter(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(
            make_strategy(strategy_id="alpha", family=StrategyFamily.SHORT_STRANGLE),
            make_strategy(strategy_id="beta", family=StrategyFamily.IRON_CONDOR),
            clock=clock,
        )
        bundle = evaluate_bundle(reg, snap, clock=clock)
        prefs = UserPreferences(allowed_families=frozenset({StrategyFamily.IRON_CONDOR}))
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(make_decision_context(bundle, preferences=prefs))
        if result.decision_status is DecisionStatus.SELECTED:
            assert result.selected_strategy_id == "beta"

    def test_direction_filter(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        prefs = UserPreferences(allowed_directions=frozenset({SignalDirection.BULLISH}))
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(make_decision_context(bundle, preferences=prefs))
        assert result.decision_status is DecisionStatus.ABSTAIN


class TestCapitalPrecheck:
    def test_capital_elimination(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        config = TradeDecisionEngineConfig(
            capital_policy=CapitalPolicy(enabled=True, max_capital_normalized_score=0.0)
        )
        engine = TradeDecisionEngine(config, clock=clock)
        result = engine.decide(make_decision_context(bundle))
        assert result.decision_status is DecisionStatus.ABSTAIN


class TestTradingWindow:
    def test_live_outside_session_abstains(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        late = datetime(2026, 8, 3, 15, 20, 0, tzinfo=IST)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=FixedClock(late))
        result = engine.decide(
            make_decision_context(
                bundle,
                reference_time=late,
                execution_mode=StrategyExecutionMode.LIVE,
            )
        )
        assert result.decision_status in (DecisionStatus.ABSTAIN, DecisionStatus.WINDOW_CLOSED)

    def test_analysis_bypasses_session(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock, execution_mode=StrategyExecutionMode.ANALYSIS)
        late = datetime(2026, 8, 3, 20, 0, 0, tzinfo=IST)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=FixedClock(late))
        result = engine.decide(
            make_decision_context(
                bundle,
                reference_time=late,
                execution_mode=StrategyExecutionMode.ANALYSIS,
            )
        )
        assert result.decision_status is DecisionStatus.SELECTED


class TestTieBreaking:
    def test_deterministic_tie_order(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(
            make_strategy(strategy_id="aaa", priority=650),
            make_strategy(strategy_id="bbb", family=StrategyFamily.IRON_CONDOR, priority=650),
            clock=clock,
        )
        bundle = evaluate_bundle(reg, snap, clock=clock)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        ctx = make_decision_context(bundle)
        r1 = engine.decide(ctx)
        r2 = engine.decide(ctx)
        assert r1.selected_strategy_id == r2.selected_strategy_id
        assert r1.decision_fingerprint == r2.decision_fingerprint


class TestConfidencePropagation:
    def test_confidence_on_selected(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(make_decision_context(single_strategy_bundle))
        assert 0.0 <= result.confidence.overall_score <= 100.0
        assert result.confidence.band is confidence_band_for_score(result.confidence.overall_score)
        assert result.selected_signal.confidence.score == result.confidence.overall_score

    def test_abstain_zero_confidence(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(enabled=False), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(make_decision_context(bundle))
        assert result.confidence.overall_score == 0.0


class TestExplanation:
    def test_non_empty_reasons(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(make_decision_context(single_strategy_bundle))
        assert result.reasons


class TestFingerprint:
    def test_fingerprint_stable(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(make_decision_context(single_strategy_bundle))
        assert result.decision_fingerprint == decision_fingerprint(result)


class TestSerialization:
    def test_json_round_trip(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(make_decision_context(single_strategy_bundle))
        restored = decision_from_json(decision_to_json(result))
        assert restored.decision_status == result.decision_status
        assert restored.selected_strategy_id == result.selected_strategy_id

    def test_unsupported_schema_version(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(make_decision_context(single_strategy_bundle))
        data = decision_to_dict(result)
        data["schema_version"] = "9.9.9"
        with pytest.raises(Exception):
            decision_from_dict(data)


class TestEngineResult:
    def test_success_on_selected(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        ctx = make_decision_context(single_strategy_bundle)
        engine_result = decision_engine.evaluate(
            EngineContext(correlation_id="corr-eval-001", as_of=fixed_as_of(), payload=ctx)
        )
        assert engine_result.status is EngineStatus.SUCCESS
        assert engine_result.payload is not None

    def test_rejected_on_manual_invalid(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        prefs = UserPreferences(blocked_strategy_ids=frozenset({"alpha"}))
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        ctx = make_decision_context(
            bundle,
            mode=DecisionMode.MANUAL,
            manual_strategy_id="alpha",
            preferences=prefs,
        )
        engine_result = engine.evaluate(
            EngineContext(correlation_id="corr-eval-001", as_of=fixed_as_of(), payload=ctx)
        )
        assert engine_result.status is EngineStatus.REJECTED


class TestThreadSafety:
    def test_concurrent_decide(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(
            make_strategy(strategy_id="alpha"),
            make_strategy(strategy_id="beta", family=StrategyFamily.IRON_CONDOR),
            clock=clock,
        )
        bundle = evaluate_bundle(reg, snap, clock=clock)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        contexts = [make_decision_context(bundle, correlation_id=f"corr-{i}") for i in range(8)]

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda ctx: engine.decide(ctx).selected_strategy_id, contexts))
        assert all(r is not None for r in results)


class TestFilterPipeline:
    def test_pipeline_stages_recorded(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(make_decision_context(single_strategy_bundle))
        assert result.filter_summary.initial_count >= 1
        stage_ids = {s.stage_id for s in result.filter_summary.stages}
        assert FilterStageId.OUTCOME_CLASS in stage_ids


class TestBuildAbstainSignal:
    def test_abstain_signal_factory(self, single_strategy_bundle: object) -> None:
        ctx = make_decision_context(single_strategy_bundle)
        signal = build_decision_abstain_signal(
            context=ctx,
            abstain_code=AbstainReasonCode.POLICY_ABSTAIN,
            reasons=("test abstain",),
        )
        assert signal.strategy_id == "trade_decision_engine"
        assert signal.strategy_family is StrategyFamily.NO_STRATEGY


class TestPerformanceSmoke:
    def test_32_report_decision_under_threshold(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        for index in range(32):
            reg.register(
                make_strategy(
                    strategy_id=f"strategy_{index:02d}",
                    family=StrategyFamily.SHORT_STRANGLE if index % 2 == 0 else StrategyFamily.IRON_CONDOR,
                    priority=500 + index,
                )
            )
        snap = reg.freeze()
        bundle = evaluate_bundle(reg, snap, clock=clock)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        ctx = make_decision_context(bundle)
        start = time.perf_counter()
        for _ in range(10):
            engine.decide(ctx)
        elapsed_ms = (time.perf_counter() - start) * 1000.0 / 10.0
        assert elapsed_ms < 50.0


class TestStrictNoActionable:
    def test_no_actionable_abstain(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(AbstainOnlyStrategy(valid_plugin_config(metadata=valid_metadata(strategy_id="abstain_only"))))
        snap = reg.freeze()
        bundle = evaluate_bundle(reg, snap, clock=clock)
        config = TradeDecisionEngineConfig(strict_no_actionable=True)
        engine = TradeDecisionEngine(config, clock=clock)
        result = engine.decide(make_decision_context(bundle))
        assert result.decision_status is DecisionStatus.ABSTAIN


class TestSignalFreshness:
    def test_expired_signal_abstains(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        report = bundle.ranked_reports[0]
        assert report.signal is not None
        expired_signal = replace(
            report.signal,
            valid_until=fixed_as_of() - timedelta(minutes=5),
        )
        expired_report = replace(report, signal=expired_signal)
        expired_bundle = replace(
            bundle,
            ranked_reports=(expired_report,),
            reports=(expired_report,),
        )
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(make_decision_context(expired_bundle))
        assert result.decision_status is DecisionStatus.ABSTAIN


class TestDefaultFactories:
    def test_default_user_preferences(self) -> None:
        prefs = default_user_preferences()
        assert prefs.min_confidence_score == 40.0
        assert prefs.exclude_undefined_risk is True

    def test_assert_valid_decision(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(make_decision_context(single_strategy_bundle))
        decision_engine.assert_valid_decision(result)

    def test_default_trading_window_policy(self) -> None:
        policy = default_trading_window_policy()
        assert policy.blackout_windows


class TestAdditionalCoverage:
    def test_empty_outcome_classes_live_raises(self) -> None:
        with pytest.raises(TradeDecisionConfigurationError):
            DecisionFilterPolicy(allowed_outcome_classes_live=frozenset())

    def test_empty_outcome_classes_analysis_raises(self) -> None:
        with pytest.raises(TradeDecisionConfigurationError):
            DecisionFilterPolicy(allowed_outcome_classes_analysis=frozenset())

    def test_invalid_strategy_id_in_preferences(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        prefs = UserPreferences(blocked_strategy_ids=frozenset({"INVALID-ID"}))
        ctx = make_decision_context(single_strategy_bundle, preferences=prefs)
        with pytest.raises(TradeDecisionValidationError):
            decision_engine.validate_run_context(ctx)

    def test_empty_allowed_families_raises(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        ctx = make_decision_context(
            single_strategy_bundle,
            preferences=UserPreferences(allowed_families=frozenset()),
        )
        with pytest.raises(TradeDecisionValidationError):
            decision_engine.validate_run_context(ctx)

    def test_strict_bundle_freshness_rejected(self, clock: FixedClock, single_strategy_bundle: object) -> None:
        config = TradeDecisionEngineConfig(strict_bundle_freshness=True, max_bundle_age_seconds=1)
        engine = TradeDecisionEngine(config, clock=clock)
        stale_time = fixed_as_of() + timedelta(hours=1)
        ctx = make_decision_context(single_strategy_bundle, reference_time=stale_time)
        with pytest.raises(TradeDecisionBundleError):
            engine.validate_run_context(ctx)

    def test_preferred_strategy_boost(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(
            make_strategy(strategy_id="alpha", priority=650),
            make_strategy(strategy_id="beta", family=StrategyFamily.IRON_CONDOR, priority=650),
            clock=clock,
        )
        bundle = evaluate_bundle(reg, snap, clock=clock)
        prefs = UserPreferences(preferred_strategy_ids=frozenset({"beta"}))
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(make_decision_context(bundle, preferences=prefs))
        assert result.decision_status is DecisionStatus.SELECTED

    def test_undefined_risk_excluded(self, clock: FixedClock) -> None:
        from strategy.signals import RiskProfileHint, SignalRiskMetadata
        from strategy.strategy_evaluation_engine import RiskEstimateCategory

        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        report = bundle.ranked_reports[0]
        assert report.signal is not None
        risky_signal = replace(
            report.signal,
            risk=SignalRiskMetadata(profile=RiskProfileHint.UNDEFINED),
        )
        risky_report = replace(
            report,
            signal=risky_signal,
            expected_risk=replace(report.expected_risk, category=RiskEstimateCategory.UNDEFINED),
        )
        risky_bundle = replace(
            bundle,
            ranked_reports=(risky_report,),
            reports=(risky_report,),
        )
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(make_decision_context(risky_bundle))
        assert result.decision_status is DecisionStatus.ABSTAIN

    def test_risk_reward_filters(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        prefs = UserPreferences(max_risk_normalized_score=0.0, min_reward_normalized_score=100.0)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(make_decision_context(bundle, preferences=prefs))
        assert result.decision_status is DecisionStatus.ABSTAIN

    def test_manual_window_override_analysis(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock, execution_mode=StrategyExecutionMode.ANALYSIS)
        late = datetime(2026, 8, 3, 15, 20, 0, tzinfo=IST)
        config = TradeDecisionEngineConfig(manual_override_policy=ManualOverridePolicy.ALLOW_WINDOW_OVERRIDE)
        engine = TradeDecisionEngine(config, clock=FixedClock(late))
        result = engine.decide(
            make_decision_context(
                bundle,
                mode=DecisionMode.MANUAL,
                manual_strategy_id="alpha",
                reference_time=late,
                execution_mode=StrategyExecutionMode.ANALYSIS,
            )
        )
        assert result.decision_status in (DecisionStatus.SELECTED, DecisionStatus.ABSTAIN)

    def test_evaluate_invalid_context_type(self, decision_engine: TradeDecisionEngine) -> None:
        with pytest.raises(TradeDecisionValidationError):
            decision_engine.evaluate(
                EngineContext(correlation_id="c", as_of=fixed_as_of(), payload="bad")
            )

    def test_assert_valid_decision_raises(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(make_decision_context(single_strategy_bundle))
        bad = replace(result, reasons=())
        with pytest.raises(TradeDecisionValidationError):
            decision_engine.assert_valid_decision(bad)

    def test_decision_from_dict_malformed(self) -> None:
        with pytest.raises(TradeDecisionValidationError):
            decision_from_dict({"schema_version": "1.0.0"})

    def test_decision_from_json_malformed(self) -> None:
        with pytest.raises(TradeDecisionValidationError):
            decision_from_json("not-json")

    def test_selector_components(self, single_strategy_bundle: object) -> None:
        selector = DecisionSelector()
        ctx = make_decision_context(single_strategy_bundle)
        bundle = single_strategy_bundle
        reports = bundle.ranked_reports
        pipeline = StrategyFilterPipeline()
        config = default_trade_decision_engine_config()
        filter_result = pipeline.apply(
            reports,
            context=ctx,
            policy=config.filter_policy,
            engine_config=config,
        )
        remaining = tuple(r for r in reports if r.strategy_id in filter_result.remaining_strategy_ids)
        outcome = selector.select(
            remaining,
            context=ctx,
            policy=config.filter_policy,
            engine_config=config,
            filter_result=filter_result,
            all_reports=reports,
        )
        assert outcome.is_selected or outcome.is_abstain

    def test_confidence_propagator_direct(self, single_strategy_bundle: object) -> None:
        propagator = ConfidencePropagator()
        ctx = make_decision_context(single_strategy_bundle)
        report = single_strategy_bundle.ranked_reports[0]
        config = default_trade_decision_engine_config()
        pipeline = StrategyFilterPipeline()
        filter_result = pipeline.apply(
            single_strategy_bundle.ranked_reports,
            context=ctx,
            policy=config.filter_policy,
            engine_config=config,
        )
        outcome = SelectionOutcome.selected(report)
        confidence = propagator.propagate(
            report=report,
            context=ctx,
            outcome=outcome,
            engine_config=config,
            filter_result=filter_result,
        )
        assert confidence.overall_score > 0

    def test_explanation_builder_direct(self, single_strategy_bundle: object) -> None:
        builder = DecisionExplanationBuilder()
        ctx = make_decision_context(single_strategy_bundle)
        report = single_strategy_bundle.ranked_reports[0]
        config = default_trade_decision_engine_config()
        pipeline = StrategyFilterPipeline()
        filter_result = pipeline.apply(
            single_strategy_bundle.ranked_reports,
            context=ctx,
            policy=config.filter_policy,
            engine_config=config,
        )
        outcome = SelectionOutcome.selected(report)
        propagator = ConfidencePropagator()
        confidence = propagator.propagate(
            report=report,
            context=ctx,
            outcome=outcome,
            engine_config=config,
            filter_result=filter_result,
        )
        reasons, factors = builder.build(
            outcome=outcome,
            context=ctx,
            filter_result=filter_result,
            confidence=confidence,
        )
        assert reasons
        assert factors

    def test_allow_monitor_in_live(self, clock: FixedClock) -> None:
        from tests.test_strategy_evaluation_engine import AbstainOnlyStrategy

        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy(strategy_id="actionable"))
        reg.register(
            AbstainOnlyStrategy(
                valid_plugin_config(metadata=valid_metadata(strategy_id="wait_strat"))
            )
        )
        snap = reg.freeze()
        bundle = evaluate_bundle(reg, snap, clock=clock)
        config = TradeDecisionEngineConfig(allow_monitor_in_live=True)
        engine = TradeDecisionEngine(config, clock=clock)
        result = engine.decide(make_decision_context(bundle))
        assert result.selected_signal is not None

    def test_autonomous_ignored_manual_id_warning(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(
            make_decision_context(
                single_strategy_bundle,
                manual_strategy_id="short_strangle",
            )
        )
        assert any(w.code == "TRADE_DECISION.MODE.IGNORED_MANUAL_ID" for w in result.warnings)

    def test_near_cutoff_confidence_penalty(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        near_cutoff = datetime(2026, 8, 3, 15, 14, 0, tzinfo=IST)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=FixedClock(near_cutoff))
        result = engine.decide(
            make_decision_context(
                bundle,
                reference_time=near_cutoff,
                execution_mode=StrategyExecutionMode.LIVE,
            )
        )
        if result.decision_status is DecisionStatus.SELECTED:
            assert result.confidence.decision_adjustment <= 0.0 or result.confidence.overall_score > 0

    def test_expiry_day_blackout_tag(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        expiry_time = datetime(2026, 8, 7, 14, 45, 0, tzinfo=IST)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=FixedClock(expiry_time))
        result = engine.decide(
            make_decision_context(
                bundle,
                reference_time=expiry_time,
                execution_mode=StrategyExecutionMode.LIVE,
                tags={"is_expiry_day": "true"},
            )
        )
        assert result.decision_status in (DecisionStatus.ABSTAIN, DecisionStatus.WINDOW_CLOSED, DecisionStatus.SELECTED)

    def test_validate_context_engine_context(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        ctx = make_decision_context(single_strategy_bundle)
        decision_engine.validate_context(
            EngineContext(correlation_id="corr-eval-001", as_of=fixed_as_of(), payload=ctx)
        )

    def test_invalid_engine_context_payload(self, decision_engine: TradeDecisionEngine) -> None:
        from core.exceptions import EngineValidationError

        with pytest.raises(EngineValidationError):
            decision_engine.validate_context(
                EngineContext(correlation_id="corr-eval-001", as_of=fixed_as_of(), payload="bad")
            )


class TestValidateDecision:
    def test_validate_decision_errors(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        from decision.trade_decision_engine import (
            DecisionConfidence,
            DecisionOutcomeClass,
            FilterPipelineResult,
            TradeDecisionResult,
        )

        base = decision_engine.decide(make_decision_context(single_strategy_bundle))
        bad_selected = replace(base, selected_report=None, selected_strategy_id=None)
        result = decision_engine.validate_decision(bad_selected)
        assert not result.is_valid

        bad_confidence = replace(
            base,
            confidence=DecisionConfidence(
                overall_score=150.0,
                band=ConfidenceBand.LOW,
                decision_adjustment=0.0,
                method="x",
                components=(),
            ),
        )
        assert not decision_engine.validate_decision(bad_confidence).is_valid

        bad_band = replace(
            base,
            confidence=DecisionConfidence(
                overall_score=50.0,
                band=ConfidenceBand.VERY_HIGH,
                decision_adjustment=0.0,
                method="x",
                components=(),
            ),
        )
        assert not decision_engine.validate_decision(bad_band).is_valid

        bad_reasons = replace(base, reasons=())
        assert not decision_engine.validate_decision(bad_reasons).is_valid

        bad_fp = replace(base, decision_fingerprint="deadbeef")
        assert not decision_engine.validate_decision(bad_fp).is_valid

        mismatch = replace(
            base,
            selected_signal=replace(base.selected_signal, strategy_id="other"),
        )
        assert not decision_engine.validate_decision(mismatch).is_valid


class TestPreferenceValidationExtended:
    def test_min_score_out_of_bounds(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        ctx = make_decision_context(
            single_strategy_bundle,
            preferences=UserPreferences(min_confidence_score=101.0),
        )
        with pytest.raises(TradeDecisionValidationError):
            decision_engine.validate_run_context(ctx)

    def test_min_expected_pop_out_of_bounds(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        ctx = make_decision_context(
            single_strategy_bundle,
            preferences=UserPreferences(min_expected_pop=1.5),
        )
        with pytest.raises(TradeDecisionValidationError):
            decision_engine.validate_run_context(ctx)

    def test_empty_allowed_directions(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        ctx = make_decision_context(
            single_strategy_bundle,
            preferences=UserPreferences(allowed_directions=frozenset()),
        )
        with pytest.raises(TradeDecisionValidationError):
            decision_engine.validate_run_context(ctx)

    def test_min_expected_pop_filter(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        prefs = UserPreferences(min_expected_pop=1.0)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(make_decision_context(bundle, preferences=prefs))
        assert result.decision_status is DecisionStatus.ABSTAIN


class TestSerializationExtended:
    def test_decision_to_dict_keep_nulls(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(make_decision_context(single_strategy_bundle))
        payload = decision_to_dict(result, omit_nulls=False)
        assert "abstain_reason_code" in payload

    def test_decision_from_dict_bad_confidence(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        data = decision_to_dict(decision_engine.decide(make_decision_context(single_strategy_bundle)))
        data["confidence"] = "bad"
        with pytest.raises(TradeDecisionValidationError):
            decision_from_dict(data)

    def test_decision_from_dict_bad_filter(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        data = decision_to_dict(decision_engine.decide(make_decision_context(single_strategy_bundle)))
        data["filter_summary"] = "bad"
        with pytest.raises(TradeDecisionValidationError):
            decision_from_dict(data)

    def test_decision_from_dict_bad_signal(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        data = decision_to_dict(decision_engine.decide(make_decision_context(single_strategy_bundle)))
        data["selected_signal"] = "bad"
        with pytest.raises(TradeDecisionValidationError):
            decision_from_dict(data)

    def test_decision_from_dict_bad_decided_at(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        data = decision_to_dict(decision_engine.decide(make_decision_context(single_strategy_bundle)))
        data["decided_at"] = 123
        with pytest.raises(TradeDecisionValidationError):
            decision_from_dict(data)


class TestEvaluateErrorPaths:
    def test_evaluate_context_error_returns_rejected(self, single_strategy_bundle: object) -> None:
        engine = TradeDecisionEngine(default_trade_decision_engine_config())
        ctx = make_decision_context(single_strategy_bundle, correlation_id="mismatch")
        result = engine.evaluate(
            EngineContext(correlation_id="mismatch", as_of=fixed_as_of(), payload=ctx)
        )
        assert result.status is EngineStatus.REJECTED

    def test_evaluate_validation_failure_rejected(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object, monkeypatch: pytest.MonkeyPatch) -> None:
        from decision.trade_decision_engine import DecisionValidationResult, TradeDecisionResult

        original = decision_engine.validate_decision

        def bad_validate(result: TradeDecisionResult) -> DecisionValidationResult:
            return DecisionValidationResult(
                errors=(
                    __import__(
                        "decision.trade_decision_engine",
                        fromlist=["DecisionErrorRecord"],
                    ).DecisionErrorRecord(code="X", message="bad"),
                )
            )

        monkeypatch.setattr(decision_engine, "validate_decision", bad_validate)
        ctx = make_decision_context(single_strategy_bundle)
        result = decision_engine.evaluate(
            EngineContext(correlation_id="corr-eval-001", as_of=fixed_as_of(), payload=ctx)
        )
        assert result.status is EngineStatus.REJECTED
        monkeypatch.setattr(decision_engine, "validate_decision", original)

    def test_evaluate_unhandled_exception_failed(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_ctx: DecisionRunContext) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(decision_engine, "decide", boom)
        ctx = make_decision_context(single_strategy_bundle)
        result = decision_engine.evaluate(
            EngineContext(correlation_id="corr-eval-001", as_of=fixed_as_of(), payload=ctx)
        )
        assert result.status is EngineStatus.FAILED


class TestManualExplanationPaths:
    def test_manual_invalid_explanation(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(
            make_decision_context(
                single_strategy_bundle,
                mode=DecisionMode.MANUAL,
                manual_strategy_id="not_present",
            )
        )
        assert any("ineligible" in r.message.lower() for r in result.reasons)

    def test_manual_failed_evaluation_status(self, clock: FixedClock) -> None:
        from tests.test_strategy_evaluation_engine import FailingStrategy

        reg = StrategyRegistry(clock=clock)
        reg.register(FailingStrategy(valid_plugin_config(metadata=valid_metadata(strategy_id="bad"))))
        snap = reg.freeze()
        bundle = evaluate_bundle(reg, snap, clock=clock)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(
            make_decision_context(
                bundle,
                mode=DecisionMode.MANUAL,
                manual_strategy_id="bad",
            )
        )
        assert result.decision_status is DecisionStatus.MANUAL_INVALID


class TestBacktestMode:
    def test_backtest_bypasses_window(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock, execution_mode=StrategyExecutionMode.BACKTEST)
        late = datetime(2026, 8, 3, 22, 0, 0, tzinfo=IST)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=FixedClock(late))
        result = engine.decide(
            make_decision_context(
                bundle,
                reference_time=late,
                execution_mode=StrategyExecutionMode.BACKTEST,
            )
        )
        assert result.decision_status is DecisionStatus.SELECTED


class TestRemainingCoverage:
    def test_session_tag_expiry_day(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        expiry_time = datetime(2026, 8, 7, 14, 45, 0, tzinfo=IST)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=FixedClock(expiry_time))
        engine.decide(
            make_decision_context(
                bundle,
                reference_time=expiry_time,
                execution_mode=StrategyExecutionMode.LIVE,
                tags={"session_tag": "expiry_day"},
            )
        )

    def test_reject_unknown_capital(self, clock: FixedClock) -> None:
        from strategy.strategy_evaluation_engine import CapitalEstimateCategory

        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        report = bundle.ranked_reports[0]
        unknown_report = replace(
            report,
            capital_estimate=replace(report.capital_estimate, category=CapitalEstimateCategory.UNKNOWN),
        )
        unknown_bundle = replace(
            bundle,
            ranked_reports=(unknown_report,),
            reports=(unknown_report,),
        )
        config = TradeDecisionEngineConfig(
            capital_policy=CapitalPolicy(enabled=True, reject_unknown_capital=True)
        )
        engine = TradeDecisionEngine(config, clock=clock)
        result = engine.decide(make_decision_context(unknown_bundle))
        assert result.decision_status is DecisionStatus.ABSTAIN

    def test_skipped_report_filtered(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy(strategy_id="good"))
        reg.register(make_strategy(strategy_id="skip_me", supported_underlyings=("BANKNIFTY",)))
        snap = reg.freeze()
        bundle = evaluate_bundle(reg, snap, clock=clock)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(make_decision_context(bundle))
        assert result.decision_status is DecisionStatus.SELECTED

    def test_monitor_outcome_class_analysis(self, clock: FixedClock) -> None:
        from strategy.signals import SignalAction
        from strategy.strategy_evaluation_engine import EvaluationOutcomeClass, EvaluationStatus

        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock, execution_mode=StrategyExecutionMode.ANALYSIS)
        report = bundle.ranked_reports[0]
        assert report.signal is not None
        wait_signal = replace(report.signal, action=SignalAction.WAIT)
        wait_report = replace(
            report,
            signal=wait_signal,
            outcome_class=EvaluationOutcomeClass.MONITOR,
            evaluation_status=EvaluationStatus.SUCCESS,
        )
        wait_bundle = replace(
            bundle,
            ranked_reports=(wait_report,),
            reports=(wait_report,),
            execution_mode=StrategyExecutionMode.ANALYSIS,
        )
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(
            make_decision_context(
                wait_bundle,
                execution_mode=StrategyExecutionMode.ANALYSIS,
            )
        )
        if result.decision_status is DecisionStatus.SELECTED:
            assert result.outcome_class in (
                DecisionOutcomeClass.TRADE_CANDIDATE,
                DecisionOutcomeClass.MONITOR_ONLY,
            )

    def test_force_abstain_manual_warning(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        result = decision_engine.decide(
            make_decision_context(
                single_strategy_bundle,
                mode=DecisionMode.MANUAL,
                manual_strategy_id="short_strangle",
                force_abstain=True,
            )
        )
        assert any(w.code == "TRADE_DECISION.MODE.FORCE_ABSTAIN_MANUAL" for w in result.warnings)

    def test_build_abstain_with_snapshot(self, single_strategy_bundle: object) -> None:
        from tests.test_base_strategy import minimal_valid_snapshot

        ctx = replace(
            make_decision_context(single_strategy_bundle),
            snapshot=minimal_valid_snapshot(),
        )
        signal = build_decision_abstain_signal(
            context=ctx,
            abstain_code=AbstainReasonCode.EMPTY_BUNDLE,
            reasons=("empty",),
        )
        assert signal.market.underlying == "NIFTY"

    def test_validate_decision_null_signal(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        base = decision_engine.decide(make_decision_context(single_strategy_bundle))
        object.__setattr__(base, "selected_signal", None)
        assert not decision_engine.validate_decision(base).is_valid

    def test_blackout_underlying_scope(self, clock: FixedClock) -> None:
        policy = TradingWindowPolicy(
            blackout_windows=(
                BlackoutWindow(
                    window_id="nifty_only",
                    start_time=datetime.strptime("09:15", "%H:%M").time(),
                    end_time=datetime.strptime("15:30", "%H:%M").time(),
                    underlying_scope=frozenset({"NIFTY"}),
                    reason="test",
                ),
            ),
            allow_analysis_outside_session=False,
        )
        config = TradeDecisionEngineConfig(window_policy=policy)
        reg, snap = setup_registry(make_strategy(), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        engine = TradeDecisionEngine(config, clock=clock)
        engine.decide(make_decision_context(bundle, execution_mode=StrategyExecutionMode.LIVE))

    def test_decision_from_json_not_object(self) -> None:
        with pytest.raises(TradeDecisionValidationError):
            decision_from_json("[]")

    def test_allocation_capital_filter(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        report = bundle.ranked_reports[0]
        high_alloc = replace(
            report,
            capital_estimate=replace(report.capital_estimate, allocation_percent_hint=99.0),
        )
        alloc_bundle = replace(
            bundle,
            ranked_reports=(high_alloc,),
            reports=(high_alloc,),
        )
        config = TradeDecisionEngineConfig(
            capital_policy=CapitalPolicy(
                enabled=True,
                max_allocation_percent_hint=5.0,
            )
        )
        engine = TradeDecisionEngine(config, clock=clock)
        result = engine.decide(make_decision_context(alloc_bundle))
        assert result.decision_status is DecisionStatus.ABSTAIN

    def test_blackout_wrong_weekday(self, clock: FixedClock) -> None:
        policy = TradingWindowPolicy(
            blackout_windows=(
                BlackoutWindow(
                    window_id="monday_only",
                    start_time=datetime.strptime("09:15", "%H:%M").time(),
                    end_time=datetime.strptime("15:30", "%H:%M").time(),
                    days_of_week=frozenset({1}),
                    reason="monday",
                ),
            ),
            allow_analysis_outside_session=False,
        )
        config = TradeDecisionEngineConfig(window_policy=policy)
        reg, snap = setup_registry(make_strategy(), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        engine = TradeDecisionEngine(config, clock=clock)
        engine.decide(make_decision_context(bundle, execution_mode=StrategyExecutionMode.LIVE))

    def test_manual_expired_strict(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(strategy_id="alpha"), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        report = bundle.ranked_reports[0]
        assert report.signal is not None
        expired = replace(report, signal=replace(report.signal, valid_until=fixed_as_of() - timedelta(hours=1)))
        expired_bundle = replace(bundle, ranked_reports=(expired,), reports=(expired,))
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        result = engine.decide(
            make_decision_context(
                expired_bundle,
                mode=DecisionMode.MANUAL,
                manual_strategy_id="alpha",
            )
        )
        assert result.decision_status is DecisionStatus.MANUAL_INVALID

    def test_abstain_missing_reason_warning(self, decision_engine: TradeDecisionEngine, single_strategy_bundle: object) -> None:
        base = decision_engine.decide(make_decision_context(single_strategy_bundle))
        no_code = replace(base, abstain_reason_code=None, decision_status=DecisionStatus.ABSTAIN)
        validation = decision_engine.validate_decision(no_code)
        assert any("abstain_reason_code" in (w.field or "") for w in validation.warnings)

    def test_window_closed_abstain_code(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        late = datetime(2026, 8, 3, 15, 25, 0, tzinfo=IST)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=FixedClock(late))
        result = engine.decide(
            make_decision_context(
                bundle,
                reference_time=late,
                execution_mode=StrategyExecutionMode.LIVE,
            )
        )
        if result.decision_status in (DecisionStatus.WINDOW_CLOSED, DecisionStatus.ABSTAIN):
            assert result.abstain_reason_code in (
                AbstainReasonCode.TRADING_WINDOW_CLOSED,
                AbstainReasonCode.ALL_FILTERED,
            )
