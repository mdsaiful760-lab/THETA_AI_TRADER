"""Unit tests for decision.ai_decision_loop."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from decision.ai_decision_loop import (
    AI_DECISION_LOOP_SCHEMA_VERSION,
    AIDecisionLoop,
    AIDecisionLoopConfigurationError,
    AiDecisionLoopConfig,
    DecisionLoopHealthStatus,
    DecisionLoopState,
    PLACEHOLDER,
    PaperTradeDecisionStatus,
    build_dashboard_decision_loop_status,
)
from risk.risk_engine import RiskVerdict
from strategy.base_strategy import StrategyPluginConfig
from strategy.registry import StrategyRegistry
from tests.test_base_strategy import (
    AbstainOnlyStrategy,
    EchoEvaluateStrategy,
    fixed_as_of,
    minimal_valid_snapshot,
    valid_metadata,
)


_UNSET = object()


class FakeSnapshotProvider:
    """Deterministic snapshot source double."""

    def __init__(self, snapshot=_UNSET, *, raise_error: bool = False) -> None:
        self._snapshot = minimal_valid_snapshot() if snapshot is _UNSET else snapshot
        self._raise_error = raise_error
        self.calls = 0

    def get_snapshot(self, underlying: str):
        self.calls += 1
        if self._raise_error:
            raise RuntimeError("snapshot source unavailable")
        return self._snapshot


def make_registry(strategy_cls=EchoEvaluateStrategy) -> StrategyRegistry:
    """Build a registry with one strategy plugin registered."""
    registry = StrategyRegistry()
    registry.register(strategy_cls(StrategyPluginConfig(metadata=valid_metadata())))
    return registry


def make_loop(
    *,
    underlying: str = "NIFTY",
    interval_seconds: float = 1.0,
    strategy_cls=EchoEvaluateStrategy,
    snapshot_provider: FakeSnapshotProvider | None = None,
    default_stop_loss_per_unit: float = 10.0,
    default_margin_per_lot: float = 50000.0,
    **overrides,
) -> tuple[AIDecisionLoop, FakeSnapshotProvider, StrategyRegistry]:
    """Build a decision loop wired with lightweight, deterministic collaborators."""
    provider = snapshot_provider or FakeSnapshotProvider()
    registry = make_registry(strategy_cls)
    config = AiDecisionLoopConfig(
        underlying=underlying,
        interval_seconds=interval_seconds,
        default_stop_loss_per_unit=default_stop_loss_per_unit,
        default_margin_per_lot=default_margin_per_lot,
        runner_kind="test",
    )
    loop = AIDecisionLoop(
        config,
        market_data_engine=provider,
        strategy_registry=registry,
        clock=lambda: fixed_as_of(),
        id_factory=overrides.pop("id_factory", None),
        **overrides,
    )
    return loop, provider, registry


class TestConfig:
    """AiDecisionLoopConfig validation."""

    def test_empty_underlying_raises(self) -> None:
        with pytest.raises(AIDecisionLoopConfigurationError):
            AiDecisionLoopConfig(underlying="  ")

    def test_non_positive_interval_raises(self) -> None:
        with pytest.raises(AIDecisionLoopConfigurationError):
            AiDecisionLoopConfig(interval_seconds=0.0)

    def test_risk_pct_out_of_range_raises(self) -> None:
        with pytest.raises(AIDecisionLoopConfigurationError):
            AiDecisionLoopConfig(default_risk_pct_per_trade=0.0)
        with pytest.raises(AIDecisionLoopConfigurationError):
            AiDecisionLoopConfig(default_risk_pct_per_trade=1.5)

    def test_negative_sizing_defaults_raise(self) -> None:
        with pytest.raises(AIDecisionLoopConfigurationError):
            AiDecisionLoopConfig(default_stop_loss_per_unit=-1.0)
        with pytest.raises(AIDecisionLoopConfigurationError):
            AiDecisionLoopConfig(default_margin_per_lot=-1.0)

    def test_valid_defaults(self) -> None:
        config = AiDecisionLoopConfig()
        assert config.underlying == "NIFTY"
        assert config.interval_seconds == 5.0
        assert AI_DECISION_LOOP_SCHEMA_VERSION == "1.0.0"


class TestFullPipelineHappyPath:
    """End-to-end wiring: snapshot -> regime -> strategy -> risk -> sizing."""

    def test_trade_candidate_end_to_end(self) -> None:
        loop, provider, _ = make_loop()
        decision = loop.run_once()

        assert decision.status is PaperTradeDecisionStatus.TRADE_CANDIDATE
        assert decision.underlying == "NIFTY"
        assert decision.market_regime != PLACEHOLDER
        assert decision.regime_confidence != PLACEHOLDER
        assert decision.decision_status == "selected"
        assert decision.strategy_id == "short_strangle"
        assert decision.strategy_family == "short_strangle"
        assert decision.confidence_score is not None
        assert decision.risk_verdict == "approved"
        assert decision.approved_risk_budget is not None
        assert decision.final_lots > 0
        assert decision.final_quantity == decision.final_lots * 75
        assert decision.trade_decision is not None
        assert decision.risk_decision is not None
        assert provider.calls == 1

    def test_regime_output_fed_into_strategy_evaluation_tags(self) -> None:
        captured_ctx: dict = {}

        class _CapturingEvalEngine:
            def evaluate_bundle(self, run_context):
                captured_ctx["tags"] = dict(run_context.tags)
                from strategy.strategy_evaluation_engine import (
                    StrategyEvaluationEngine,
                    StrategyEvaluationEngineConfig,
                )

                real = StrategyEvaluationEngine(
                    StrategyEvaluationEngineConfig(), make_registry()
                )
                return real.evaluate_bundle(run_context)

        loop, _, _ = make_loop(strategy_evaluation_engine=_CapturingEvalEngine())
        loop.run_once()

        assert "regime" in captured_ctx["tags"]
        assert "regime_confidence" in captured_ctx["tags"]

    def test_deterministic_repeat_produces_same_outcome(self) -> None:
        loop_a, _, _ = make_loop()
        loop_b, _, _ = make_loop()
        decision_a = loop_a.run_once()
        decision_b = loop_b.run_once()
        assert decision_a.status == decision_b.status
        assert decision_a.final_lots == decision_b.final_lots
        assert decision_a.strategy_id == decision_b.strategy_id


class TestNoSnapshot:
    """No live market snapshot available for the configured underlying."""

    def test_no_snapshot_short_circuits(self) -> None:
        provider = FakeSnapshotProvider(snapshot=None)
        loop, _, _ = make_loop(snapshot_provider=provider)
        decision = loop.run_once()
        assert decision.status is PaperTradeDecisionStatus.NO_SNAPSHOT
        assert decision.market_regime == PLACEHOLDER
        assert decision.trade_decision is None
        assert decision.risk_decision is None
        assert decision.reasons


class TestAbstainedDecision:
    """Strategy evaluation yields no SELECTED candidate."""

    def test_abstain_only_strategy_yields_abstained(self) -> None:
        loop, _, _ = make_loop(strategy_cls=AbstainOnlyStrategy)
        decision = loop.run_once()
        assert decision.status is PaperTradeDecisionStatus.ABSTAINED
        assert decision.decision_status != "selected"
        assert decision.strategy_id is None
        assert decision.risk_decision is None
        assert decision.final_lots == 0


class TestRiskBlocked:
    """Risk engine rejects the selected strategy."""

    def test_risk_rejection_short_circuits_before_sizing(self) -> None:
        class _RejectingRiskEngine:
            def __init__(self) -> None:
                self.called = False

            def review(self, run_context):
                self.called = True
                return SimpleNamespace(
                    verdict=RiskVerdict.REJECTED,
                    reasons=(SimpleNamespace(message="Daily loss limit breached."),),
                    approved_risk_budget=None,
                    approved_risk_pct=None,
                )

        class _SpySizingEngine:
            def __init__(self) -> None:
                self.called = False

            def analyze(self, **kwargs):
                self.called = True
                return {"final_lots": 5}

        risk_double = _RejectingRiskEngine()
        sizing_double = _SpySizingEngine()
        loop, _, _ = make_loop(
            risk_engine=risk_double,
            position_sizing_engine=sizing_double,
        )
        decision = loop.run_once()

        assert risk_double.called is True
        assert sizing_double.called is False
        assert decision.status is PaperTradeDecisionStatus.BLOCKED_BY_RISK
        assert decision.risk_verdict == "rejected"
        assert decision.final_lots == 0
        assert "Daily loss limit breached." in decision.reasons


class TestErrorHandling:
    """Unhandled failures never crash the loop."""

    def test_snapshot_provider_exception_yields_error_status(self) -> None:
        provider = FakeSnapshotProvider(raise_error=True)
        loop, _, _ = make_loop(snapshot_provider=provider)
        decision = loop.run_once()
        assert decision.status is PaperTradeDecisionStatus.ERROR
        assert "snapshot source unavailable" in decision.reasons[0]
        health = loop.get_health()
        assert health.cycles_run == 1
        assert health.cycles_failed == 1
        assert health.last_error_code == "AI_LOOP.CYCLE.UNHANDLED"

    def test_regime_engine_failure_degrades_gracefully(self) -> None:
        class _RaisingRegimeEngine:
            def analyze(self, *args, **kwargs):
                raise RuntimeError("regime unavailable")

        loop, _, _ = make_loop(regime_engine=_RaisingRegimeEngine())
        decision = loop.run_once()
        assert decision.status is not PaperTradeDecisionStatus.ERROR
        assert decision.market_regime == PLACEHOLDER
        assert decision.regime_confidence == PLACEHOLDER


class TestLifecycle:
    """start/stop/start_loop/stop_loop/shutdown state transitions."""

    def test_initial_state_is_created(self) -> None:
        loop, _, _ = make_loop()
        assert loop.get_state() is DecisionLoopState.CREATED

    def test_start_and_stop(self) -> None:
        loop, _, _ = make_loop()
        loop.start()
        assert loop.get_state() is DecisionLoopState.RUNNING
        loop.stop()
        assert loop.get_state() is DecisionLoopState.STOPPED

    def test_start_loop_runs_multiple_cycles(self) -> None:
        loop, _, _ = make_loop(interval_seconds=0.02)
        loop.start_loop()
        try:
            deadline = time.time() + 2.0
            while loop.get_health().cycles_run < 3 and time.time() < deadline:
                time.sleep(0.01)
        finally:
            loop.stop_loop()
        assert loop.get_health().cycles_run >= 3
        assert loop.get_state() is DecisionLoopState.RUNNING

    def test_start_loop_idempotent(self) -> None:
        loop, _, _ = make_loop(interval_seconds=0.05)
        loop.start_loop()
        first_thread = loop._loop_thread
        loop.start_loop()
        second_thread = loop._loop_thread
        assert first_thread is second_thread
        loop.stop_loop()

    def test_start_loop_invalid_interval_raises(self) -> None:
        loop, _, _ = make_loop()
        with pytest.raises(AIDecisionLoopConfigurationError):
            loop.start_loop(interval_seconds=0.0)

    def test_stop_loop_safe_when_never_started(self) -> None:
        loop, _, _ = make_loop()
        loop.stop_loop()

    def test_shutdown_stops_loop_and_sets_stopped(self) -> None:
        loop, _, _ = make_loop(interval_seconds=0.05)
        loop.start_loop()
        loop.shutdown()
        assert loop.get_state() is DecisionLoopState.STOPPED
        assert loop._loop_thread is None

    def test_shutdown_safe_when_never_started(self) -> None:
        loop, _, _ = make_loop()
        loop.shutdown()
        assert loop.get_state() is DecisionLoopState.STOPPED


class TestThreadSafety:
    """Concurrent run_once() and reads stay consistent."""

    def test_concurrent_run_once_and_reads(self) -> None:
        loop, _, _ = make_loop()
        barrier = threading.Barrier(4)
        errors: list[BaseException] = []

        def runner() -> None:
            try:
                barrier.wait()
                for _ in range(5):
                    loop.run_once()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def reader() -> None:
            try:
                barrier.wait()
                for _ in range(20):
                    loop.get_latest_decision()
                    loop.get_health()
                    loop.get_dashboard_status()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(runner), pool.submit(runner), pool.submit(reader), pool.submit(reader)]
            for future in futures:
                future.result()
        assert not errors
        assert loop.get_health().cycles_run == 10


class TestHealthMonitoring:
    """get_health() aggregation."""

    def test_health_unknown_before_first_cycle(self) -> None:
        loop, _, _ = make_loop()
        health = loop.get_health()
        assert health.overall_health is DecisionLoopHealthStatus.UNKNOWN
        assert health.cycles_run == 0

    def test_health_healthy_after_successful_running_cycle(self) -> None:
        loop, _, _ = make_loop()
        loop.start()
        loop.run_once()
        health = loop.get_health()
        assert health.overall_health is DecisionLoopHealthStatus.HEALTHY
        assert health.last_decision_status == "trade_candidate"
        assert health.last_cycle_duration_ms is not None

    def test_health_unhealthy_after_failed_cycle(self) -> None:
        provider = FakeSnapshotProvider(raise_error=True)
        loop, _, _ = make_loop(snapshot_provider=provider)
        loop.start()
        loop.run_once()
        health = loop.get_health()
        assert health.overall_health is DecisionLoopHealthStatus.UNHEALTHY
        assert health.issues


class TestDashboardIntegration:
    """Dashboard-shaped status (current strategy, confidence, state)."""

    def test_placeholders_before_any_cycle(self) -> None:
        loop, _, _ = make_loop()
        status = loop.get_dashboard_status()
        assert status.market_regime == PLACEHOLDER
        assert status.active_strategy == PLACEHOLDER
        assert status.confidence_score == PLACEHOLDER
        assert status.strategies == ()
        assert status.state == DecisionLoopState.CREATED.value

    def test_reflects_latest_trade_candidate(self) -> None:
        loop, _, _ = make_loop()
        loop.start()
        loop.run_once()
        status = loop.get_dashboard_status()
        assert status.active_strategy == "short_strangle"
        assert status.confidence_score != PLACEHOLDER
        assert status.market_regime != PLACEHOLDER
        assert status.last_decision_status == "trade_candidate"
        assert status.state == DecisionLoopState.RUNNING.value

    def test_lists_ranked_strategies_from_bundle(self) -> None:
        loop, _, _ = make_loop()
        loop.run_once()
        status = loop.get_dashboard_status()
        assert len(status.strategies) >= 1
        assert status.strategies[0].family == "short_strangle"
        assert status.strategies[0].display_name == "Short Strangle"

    def test_get_strategy_status_alias(self) -> None:
        loop, _, _ = make_loop()
        loop.run_once()
        assert loop.get_strategy_status() == loop.get_dashboard_status()

    def test_build_dashboard_decision_loop_status_pure_function(self) -> None:
        status = build_dashboard_decision_loop_status(
            state=DecisionLoopState.STOPPED,
            latest_decision=None,
            latest_bundle=None,
        )
        assert status.state == "stopped"
        assert status.active_strategy == PLACEHOLDER


class TestCallbacks:
    """add_decision_callback()/remove_decision_callback()."""

    def test_callback_invoked_with_decision(self) -> None:
        received: list = []
        loop, _, _ = make_loop()
        loop.add_decision_callback(received.append)
        decision = loop.run_once()
        assert received == [decision]

    def test_callback_exception_does_not_break_cycle(self) -> None:
        loop, _, _ = make_loop()

        def bad_callback(_decision) -> None:
            raise RuntimeError("subscriber boom")

        loop.add_decision_callback(bad_callback)
        decision = loop.run_once()
        assert decision.status is PaperTradeDecisionStatus.TRADE_CANDIDATE

    def test_remove_callback_stops_future_invocations(self) -> None:
        received: list = []
        loop, _, _ = make_loop()
        loop.add_decision_callback(received.append)
        loop.remove_decision_callback(received.append)
        loop.run_once()
        assert received == []
