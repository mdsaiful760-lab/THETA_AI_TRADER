"""Unit tests for system.system_orchestrator."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from types import MappingProxyType, SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from core.engine_result import EngineResult, EngineStatus
from core.event_bus import EventBus, EventEnvelope
from decision.trade_decision_engine import DecisionStatus
from execution.execution_engine import ExecutionPlanStatus
from portfolio.portfolio_manager import (
    PortfolioExposure,
    PortfolioMetrics,
    PortfolioPositionSummary,
    PortfolioSnapshot,
)
from risk.risk_engine import RiskVerdict
from strategy.signals import StrategyExecutionMode, StrategyFamily
from strategy.strategy_evaluation_engine import StrategyEvaluationBundle
from system.system_orchestrator import (
    ERROR_CONFIG_INVALID,
    ERROR_CONTEXT_CORRELATION_MISMATCH,
    ERROR_CONTEXT_NAIVE_TIMESTAMP,
    ERROR_CYCLE_OVERLAP,
    ERROR_SERIALIZATION_MALFORMED,
    ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
    ERROR_STATE_INVALID,
    CycleStatus,
    CycleTrigger,
    EngineId,
    EngineRegistry,
    HealthStatus,
    OrchestratorEventType,
    OrchestratorState,
    OrchestratorStateError,
    OrchestratorValidationError,
    PostFillCycleContext,
    PreTradeCycleStageId,
    ShutdownStatus,
    StartupStatus,
    SystemOrchestrator,
    SystemOrchestratorConfig,
    TradingCycleContext,
    compute_cycle_fingerprint,
    config_fingerprint,
    default_orchestrator_config,
    deserialize_system_health_report,
    deserialize_trading_cycle_result,
    map_portfolio_snapshot_for_risk,
    serialize_system_health_report,
    serialize_trading_cycle_result,
    validate_orchestrator_config,
    validate_trading_cycle_result,
)
from tests.test_base_strategy import minimal_valid_snapshot
from tests.test_portfolio_manager import fixed_as_of, make_position_snapshot

IST = ZoneInfo("Asia/Kolkata")


def orchestrator_clock() -> datetime:
    """Fixed clock for deterministic orchestrator tests."""
    return datetime(2026, 8, 4, 4, 30, tzinfo=IST)


def fast_config(**overrides: object) -> SystemOrchestratorConfig:
    """Build fast deterministic orchestrator configuration."""
    base = default_orchestrator_config()
    defaults: dict[str, object] = {
        "execution_mode": StrategyExecutionMode.BACKTEST,
        "account_id": "acct-1",
        "deterministic_fingerprint": True,
        "publish_system_events": True,
        "block_pre_trade_in_degraded": False,
    }
    defaults.update(overrides)
    return replace(base, **defaults)


class StubEngine:
    """Minimal engine stub with optional validate_configuration."""

    def __init__(self, *, fail_validate: bool = False) -> None:
        self.fail_validate = fail_validate

    def validate_configuration(self) -> None:
        if self.fail_validate:
            raise RuntimeError("validation failed")


def make_market_snapshot(*, snapshot_id: str = "msnap-1") -> object:
    """Build minimal market snapshot fixture."""
    return minimal_valid_snapshot(snapshot_id=snapshot_id)


def make_golden_portfolio_snapshot() -> PortfolioSnapshot:
    """Build deterministic portfolio snapshot for risk mapping golden test."""
    position = PortfolioPositionSummary(
        position_id="pos-golden-1",
        instrument_key="NFO:NIFTY24AUG25000CE",
        underlying="NIFTY",
        expiry="2026-08-28",
        strategy_id="short-strangle-v1",
        strategy_family=StrategyFamily.SHORT_STRANGLE,
        side="short",
        quantity=75,
        notional_exposure=125_000.0,
        unrealized_pnl=150.0,
        realized_pnl_session=25.0,
        opened_at=fixed_as_of(),
    )
    metrics = PortfolioMetrics(
        total_realized_pnl_session=25.0,
        total_unrealized_pnl=150.0,
        total_daily_pnl=175.0,
        equity_hint=1_000_000.0,
        cash_available_hint=500_000.0,
        capital_deployed=125_000.0,
        capital_utilization_pct=12.5,
        margin_used_hint=50_000.0,
        margin_available_hint=450_000.0,
        margin_utilization_pct=10.0,
        portfolio_delta=-10.5,
        portfolio_gamma=0.02,
        portfolio_theta=-120.0,
        portfolio_vega=45.0,
        open_position_count=1,
        peak_equity_hint=1_010_000.0,
        metrics_fingerprint="metrics-golden",
    )
    exposure = PortfolioExposure(
        gross_notional=125_000.0,
        net_notional=125_000.0,
        gross_notional_by_underlying=MappingProxyType({"NIFTY": 125_000.0}),
        net_notional_by_underlying=MappingProxyType({"NIFTY": 125_000.0}),
        exposure_by_strategy_id=MappingProxyType({"short-strangle-v1": 125_000.0}),
        exposure_by_strategy_family=MappingProxyType({"short_strangle": 125_000.0}),
        exposure_by_expiry=MappingProxyType({"2026-08-28": 125_000.0}),
        largest_underlying_weight_pct=100.0,
        largest_strategy_weight_pct=100.0,
        open_position_count=1,
        open_position_count_by_underlying=MappingProxyType({"NIFTY": 1}),
        exposure_fingerprint="exposure-golden",
    )
    return PortfolioSnapshot(
        snapshot_id="pf-golden-1",
        correlation_id="corr-golden",
        as_of=fixed_as_of(),
        account_id="acct-1",
        metrics=metrics,
        exposure=exposure,
        positions=(position,),
        by_strategy=MappingProxyType({}),
        by_underlying=MappingProxyType({}),
        by_expiry=MappingProxyType({}),
        snapshot_fingerprint="pf-fp-golden",
    )


def make_bundle() -> StrategyEvaluationBundle:
    """Build minimal strategy evaluation bundle mock."""
    bundle = MagicMock(spec=StrategyEvaluationBundle)
    bundle.bundle_id = "bundle-1"
    return bundle


def make_trade_decision(*, status: DecisionStatus = DecisionStatus.SELECTED) -> MagicMock:
    """Build trade decision mock."""
    decision = MagicMock()
    decision.decision_id = "dec-1"
    decision.decision_status = status
    return decision


def make_risk_decision(*, verdict: RiskVerdict = RiskVerdict.APPROVED) -> MagicMock:
    """Build risk decision mock."""
    decision = MagicMock()
    decision.verdict = verdict
    return decision


def make_execution_plan(*, status: ExecutionPlanStatus = ExecutionPlanStatus.READY) -> MagicMock:
    """Build execution plan mock."""
    plan = MagicMock()
    plan.plan_id = "plan-1"
    plan.status = status
    return plan


def make_order_submission_result() -> MagicMock:
    """Build order submission result mock."""
    result = MagicMock()
    result.submission_id = "sub-1"
    return result


def make_registry_snapshot() -> MagicMock:
    """Build registry snapshot mock."""
    snap = MagicMock()
    snap.fingerprint = "reg-fp-1"
    return snap


class MockStrategyEngine(StubEngine):
    """Recording strategy evaluation stub."""

    def __init__(self, bundle: StrategyEvaluationBundle | None = None) -> None:
        super().__init__()
        self.bundle = bundle or make_bundle()
        self.calls: list[object] = []

    def run(self, context: object) -> EngineResult:
        self.calls.append(context)
        return EngineResult(
            status=EngineStatus.SUCCESS,
            metadata=MagicMock(),
            payload=self.bundle,
        )


class MockDecisionEngine(StubEngine):
    """Recording trade decision stub."""

    def __init__(self, decision: MagicMock | None = None) -> None:
        super().__init__()
        self.decision = decision or make_trade_decision()
        self.calls: list[object] = []

    def run(self, context: object) -> EngineResult:
        self.calls.append(context)
        return EngineResult(
            status=EngineStatus.SUCCESS,
            metadata=MagicMock(),
            payload=self.decision,
        )


class MockRiskEngine(StubEngine):
    """Recording risk engine stub."""

    def __init__(self, decision: MagicMock | None = None) -> None:
        super().__init__()
        self.decision = decision or make_risk_decision()
        self.calls: list[object] = []

    def review(self, context: object) -> MagicMock:
        self.calls.append(context)
        return self.decision


class MockExecutionEngine(StubEngine):
    """Recording execution engine stub."""

    def __init__(self, plan: MagicMock | None = None) -> None:
        super().__init__()
        self.plan = plan or make_execution_plan()
        self.calls: list[object] = []

    def plan_from_run_context(self, context: object) -> MagicMock:
        self.calls.append(context)
        return self.plan


class MockOrderManager(StubEngine):
    """Recording order manager stub."""

    def __init__(self, submission: MagicMock | None = None) -> None:
        super().__init__()
        self.submission = submission or make_order_submission_result()
        self.calls: list[tuple[object, ...]] = []

    def submit_plan(self, plan: object, broker: object, context: object) -> MagicMock:
        self.calls.append((plan, broker, context))
        return self.submission


class MockPortfolioManager(StubEngine):
    """Portfolio manager stub returning golden snapshot."""

    def __init__(self, snapshot: PortfolioSnapshot | None = None) -> None:
        super().__init__()
        self.snapshot = snapshot or make_golden_portfolio_snapshot()

    def get_snapshot(self) -> PortfolioSnapshot:
        return self.snapshot

    def ingest_position_snapshot(self, position_snapshot: object, context: object) -> MagicMock:
        result = MagicMock()
        result.update_id = "pf-upd-1"
        result.status = SimpleNamespace(value="applied")
        from portfolio.portfolio_manager import PortfolioUpdateStatus

        result.status = PortfolioUpdateStatus.APPLIED
        result.snapshot = self.snapshot
        return result


class MockPositionManager(StubEngine):
    """Position manager stub."""

    def apply_order_tracker(self, tracker: object, context: object) -> MagicMock:
        from portfolio.position_manager import PositionUpdateStatus

        result = MagicMock()
        result.update_id = "pos-upd-1"
        result.status = PositionUpdateStatus.APPLIED
        result.snapshot = make_position_snapshot()
        return result


class MockApmeEngine(StubEngine):
    """APME engine stub."""

    def evaluate(self, portfolio_snapshot: object, context: object, **kwargs: object) -> MagicMock:
        report = MagicMock()
        report.report_id = "apme-rpt-1"
        return report


class MockMarketDataEngine(StubEngine):
    """Market data engine stub."""

    def __init__(self, snapshot: object | None = None) -> None:
        super().__init__()
        self.snapshot = snapshot or make_market_snapshot()

    def publish_snapshot(self, **kwargs: object) -> MagicMock:
        event = MagicMock()
        event.snapshot = self.snapshot
        return event


def build_orchestrator(
    *,
    config: SystemOrchestratorConfig | None = None,
    strategy: MockStrategyEngine | None = None,
    decision: MockDecisionEngine | None = None,
    risk: MockRiskEngine | None = None,
    execution: MockExecutionEngine | None = None,
    order_manager: MockOrderManager | None = None,
    portfolio_manager: MockPortfolioManager | None = None,
    position_manager: MockPositionManager | None = None,
    apme: MockApmeEngine | None = None,
    market_data: MockMarketDataEngine | None = None,
    broker_client: object | None = object(),
    auto_start: bool = True,
) -> SystemOrchestrator:
    """Build orchestrator with mock engine registry."""
    registry = EngineRegistry(
        market_data=market_data or MockMarketDataEngine(),
        strategy_evaluation=strategy or MockStrategyEngine(),
        trade_decision=decision or MockDecisionEngine(),
        risk=risk or MockRiskEngine(),
        execution=execution or MockExecutionEngine(),
        order_manager=order_manager or MockOrderManager(),
        portfolio_manager=portfolio_manager or MockPortfolioManager(),
        position_manager=position_manager or MockPositionManager(),
        apme=apme or MockApmeEngine(),
    )
    orchestrator = SystemOrchestrator(
        config or fast_config(),
        broker_client=broker_client,
        engine_registry=registry,
        clock=orchestrator_clock,
    )
    if auto_start:
        orchestrator.start()
    return orchestrator


def make_trading_context(**overrides: object) -> TradingCycleContext:
    """Build trading cycle context."""
    defaults: dict[str, object] = {
        "correlation_id": "corr-1",
        "reference_time": fixed_as_of(),
        "execution_mode": StrategyExecutionMode.BACKTEST,
        "account_id": "acct-1",
        "trigger": CycleTrigger.MANUAL,
        "market_snapshot": make_market_snapshot(),
        "registry_snapshot": make_registry_snapshot(),
    }
    defaults.update(overrides)
    return TradingCycleContext(**defaults)  # type: ignore[arg-type]


def make_post_fill_context(**overrides: object) -> PostFillCycleContext:
    """Build post-fill cycle context."""
    tracker = MagicMock()
    tracker.submission_id = "sub-1"
    defaults: dict[str, object] = {
        "correlation_id": "corr-pf-1",
        "reference_time": fixed_as_of(),
        "execution_mode": StrategyExecutionMode.BACKTEST,
        "account_id": "acct-1",
        "order_tracker": tracker,
        "equity_hint": 1_000_000.0,
        "cash_available_hint": 500_000.0,
    }
    defaults.update(overrides)
    return PostFillCycleContext(**defaults)  # type: ignore[arg-type]


class TestConfigAndMapping:
    def test_default_config_valid(self) -> None:
        assert validate_orchestrator_config(default_orchestrator_config()).is_valid

    def test_invalid_cycle_timeout(self) -> None:
        config = replace(default_orchestrator_config(), cycle_timeout_seconds=0)
        result = validate_orchestrator_config(config)
        assert not result.is_valid
        assert result.errors[0].code == ERROR_CONFIG_INVALID

    def test_config_fingerprint_stable(self) -> None:
        config = fast_config()
        assert config_fingerprint(config) == config_fingerprint(config)

    def test_map_portfolio_snapshot_for_risk_golden(self) -> None:
        pm = make_golden_portfolio_snapshot()
        mapped = map_portfolio_snapshot_for_risk(
            pm,
            account_equity=1_050_000.0,
            account_cash=525_000.0,
        )
        assert mapped.snapshot_id == "pf-golden-1"
        assert mapped.equity == 1_050_000.0
        assert mapped.cash_available == 525_000.0
        assert mapped.portfolio_fingerprint == "pf-fp-golden"
        assert len(mapped.open_positions) == 1
        assert mapped.open_positions[0].underlying == "NIFTY"
        assert mapped.exposure_summary.gross_notional == 125_000.0


class TestLifecycle:
    def test_startup_success_with_all_critical_engines(self) -> None:
        orchestrator = build_orchestrator(auto_start=False)
        result = orchestrator.start()
        assert result.status is StartupStatus.SUCCESS
        assert orchestrator.get_state() is OrchestratorState.RUNNING

    def test_startup_failed_missing_critical_engine(self) -> None:
        registry = EngineRegistry(
            market_data=MockMarketDataEngine(),
            risk=MockRiskEngine(),
            order_manager=MockOrderManager(),
            position_manager=MockPositionManager(),
            portfolio_manager=None,
        )
        orchestrator = SystemOrchestrator(fast_config(), engine_registry=registry, clock=orchestrator_clock)
        result = orchestrator.start()
        assert result.status is StartupStatus.FAILED
        assert orchestrator.get_state() is OrchestratorState.FAILED

    def test_startup_partial_non_critical_failure(self) -> None:
        registry = EngineRegistry(
            market_data=MockMarketDataEngine(),
            strategy_evaluation=StubEngine(fail_validate=True),
            trade_decision=MockDecisionEngine(),
            risk=MockRiskEngine(),
            execution=MockExecutionEngine(),
            order_manager=MockOrderManager(),
            position_manager=MockPositionManager(),
            portfolio_manager=MockPortfolioManager(),
        )
        orchestrator = SystemOrchestrator(fast_config(), engine_registry=registry, clock=orchestrator_clock)
        result = orchestrator.start()
        assert result.status is StartupStatus.PARTIAL
        assert orchestrator.get_state() is OrchestratorState.DEGRADED

    def test_stop_from_running(self) -> None:
        orchestrator = build_orchestrator()
        result = orchestrator.stop()
        assert result.status is ShutdownStatus.SUCCESS
        assert orchestrator.get_state() is OrchestratorState.STOPPED

    def test_stop_invalid_state_raises(self) -> None:
        orchestrator = build_orchestrator(auto_start=False)
        with pytest.raises(OrchestratorStateError) as exc:
            orchestrator.stop()
        assert exc.value.code == ERROR_STATE_INVALID

    def test_restart_after_stop(self) -> None:
        orchestrator = build_orchestrator()
        orchestrator.stop()
        result = orchestrator.start()
        assert result.status is StartupStatus.SUCCESS
        assert orchestrator.get_state() is OrchestratorState.RUNNING


class TestPreTradeCycle:
    def test_happy_path_completed(self) -> None:
        orchestrator = build_orchestrator()
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.COMPLETED
        assert result.market_snapshot_id == "msnap-1"
        assert result.strategy_result_id == "bundle-1"
        assert result.decision_result_id == "dec-1"
        assert result.risk_verdict == RiskVerdict.APPROVED.value
        assert result.execution_plan_id == "plan-1"
        assert result.order_submission_id == "sub-1"
        assert result.cycle_fingerprint
        assert validate_trading_cycle_result(result).is_valid

    def test_abstain_short_circuit(self) -> None:
        decision = MockDecisionEngine(make_trade_decision(status=DecisionStatus.ABSTAIN))
        orchestrator = build_orchestrator(decision=decision)
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.COMPLETED
        assert result.risk_verdict is None
        assert result.execution_plan_id is None
        stage_ids = [s.stage_id for s in result.stages]
        assert PreTradeCycleStageId.RISK_REVIEW.value in stage_ids

    def test_risk_rejected_short_circuit(self) -> None:
        risk = MockRiskEngine(make_risk_decision(verdict=RiskVerdict.REJECTED))
        orchestrator = build_orchestrator(risk=risk)
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.PARTIAL
        assert result.risk_verdict == RiskVerdict.REJECTED.value
        assert result.execution_plan_id is None
        assert result.order_submission_id is None

    def test_execution_no_plan_short_circuit(self) -> None:
        execution = MockExecutionEngine(make_execution_plan(status=ExecutionPlanStatus.NO_PLAN))
        orchestrator = build_orchestrator(execution=execution)
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.COMPLETED
        assert result.execution_plan_id == "plan-1"
        assert result.order_submission_id is None

    def test_naive_timestamp_rejected(self) -> None:
        orchestrator = build_orchestrator()
        naive = datetime(2026, 8, 4, 4, 30)
        result = orchestrator.run_trading_cycle(make_trading_context(reference_time=naive))
        assert result.status is CycleStatus.REJECTED
        assert result.primary_error_code == ERROR_CONTEXT_NAIVE_TIMESTAMP

    def test_empty_correlation_rejected(self) -> None:
        orchestrator = build_orchestrator(config=fast_config(strict_correlation=True))
        result = orchestrator.run_trading_cycle(make_trading_context(correlation_id="  "))
        assert result.status is CycleStatus.REJECTED
        assert result.primary_error_code == ERROR_CONTEXT_CORRELATION_MISMATCH

    def test_pre_trade_disabled_skipped(self) -> None:
        orchestrator = build_orchestrator(config=fast_config(enable_pre_trade_cycle=False))
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.SKIPPED

    def test_cycle_not_allowed_before_start(self) -> None:
        orchestrator = build_orchestrator(auto_start=False)
        with pytest.raises(OrchestratorStateError) as exc:
            orchestrator.run_trading_cycle(make_trading_context())
        assert exc.value.code == ERROR_STATE_INVALID

    def test_overlap_rejection(self) -> None:
        orchestrator = build_orchestrator(config=fast_config(serial_cycle_execution=True))
        lock = orchestrator._cycle_lock  # test double access
        lock.acquire()
        try:
            result = orchestrator.run_trading_cycle(make_trading_context())
            assert result.status is CycleStatus.REJECTED
            assert result.primary_error_code == ERROR_CYCLE_OVERLAP
        finally:
            lock.release()

    def test_deterministic_fingerprint_backtest(self) -> None:
        orchestrator = build_orchestrator()
        ctx = make_trading_context()
        first = orchestrator.run_trading_cycle(ctx)
        second = orchestrator.run_trading_cycle(ctx)
        assert first.cycle_fingerprint == second.cycle_fingerprint


class TestPostFillCycle:
    def test_happy_path(self) -> None:
        orchestrator = build_orchestrator()
        result = orchestrator.run_post_fill_cycle(make_post_fill_context())
        assert result.status is CycleStatus.COMPLETED
        assert result.position_update_id == "pos-upd-1"
        assert result.portfolio_update_id == "pf-upd-1"
        assert result.apme_report_id == "apme-rpt-1"
        assert result.cycle_fingerprint

    def test_post_fill_disabled_skipped(self) -> None:
        orchestrator = build_orchestrator(config=fast_config(enable_post_fill_cycle=False))
        result = orchestrator.run_post_fill_cycle(make_post_fill_context())
        assert result.status is CycleStatus.SKIPPED

    def test_apme_unavailable_partial(self) -> None:
        registry = EngineRegistry(
            market_data=MockMarketDataEngine(),
            strategy_evaluation=MockStrategyEngine(),
            trade_decision=MockDecisionEngine(),
            risk=MockRiskEngine(),
            execution=MockExecutionEngine(),
            order_manager=MockOrderManager(),
            portfolio_manager=MockPortfolioManager(),
            position_manager=MockPositionManager(),
            apme=None,
        )
        orchestrator = SystemOrchestrator(
            fast_config(),
            engine_registry=registry,
            clock=orchestrator_clock,
        )
        orchestrator.start()
        result = orchestrator.run_post_fill_cycle(make_post_fill_context())
        assert result.status is CycleStatus.PARTIAL
        assert result.apme_report_id is None


class TestHealthAndEvents:
    def test_get_health_running(self) -> None:
        orchestrator = build_orchestrator()
        report = orchestrator.get_health()
        assert report.orchestrator_state is OrchestratorState.RUNNING
        assert report.overall_status is HealthStatus.HEALTHY
        assert report.event_bus_metrics.active_subscriptions >= 1

    def test_get_health_thread_safe_during_cycle(self) -> None:
        orchestrator = build_orchestrator()
        reports: list[object] = []

        def worker() -> None:
            for _ in range(20):
                reports.append(orchestrator.get_health())

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker) for _ in range(4)]
            orchestrator.run_trading_cycle(make_trading_context())
            for future in futures:
                future.result()
        assert len(reports) == 80

    def test_event_subscription_wiring(self) -> None:
        orchestrator = build_orchestrator(auto_start=False)
        orchestrator.start()
        assert len(orchestrator._subscription_handles) >= 4

    def test_market_snapshot_event_handler(self) -> None:
        orchestrator = build_orchestrator(config=fast_config(enable_event_driven_cycles=False))
        snapshot = make_market_snapshot()
        envelope = EventEnvelope(
            event_id="evt-1",
            topic="market.snapshot.published",
            payload=snapshot,
            correlation_id="corr-event",
            causation_id=None,
            producer="test",
            producer_version="1.0.0",
            occurred_at=fixed_as_of(),
            published_at=fixed_as_of(),
        )
        orchestrator.on_market_snapshot_event(envelope)
        assert orchestrator._last_market_snapshot_at == fixed_as_of()


class TestSerialization:
    def test_trading_cycle_result_round_trip(self) -> None:
        orchestrator = build_orchestrator()
        result = orchestrator.run_trading_cycle(make_trading_context())
        payload = serialize_trading_cycle_result(result)
        restored = deserialize_trading_cycle_result(payload)
        assert restored.cycle_id == result.cycle_id
        assert restored.status == result.status
        assert restored.cycle_fingerprint == result.cycle_fingerprint

    def test_health_report_round_trip(self) -> None:
        orchestrator = build_orchestrator()
        report = orchestrator.get_health()
        payload = serialize_system_health_report(report)
        restored = deserialize_system_health_report(payload)
        assert restored.report_id == report.report_id
        assert restored.overall_status == report.overall_status

    def test_deserialize_malformed_json(self) -> None:
        with pytest.raises(OrchestratorValidationError) as exc:
            deserialize_trading_cycle_result("{bad")
        assert exc.value.code == ERROR_SERIALIZATION_MALFORMED

    def test_deserialize_unsupported_version(self) -> None:
        payload = json.dumps({"schema_version": "9.9.9", "cycle_id": "x"})
        with pytest.raises(OrchestratorValidationError) as exc:
            deserialize_trading_cycle_result(payload)
        assert exc.value.code == ERROR_SERIALIZATION_UNSUPPORTED_VERSION


class TestErrorIsolation:
    def test_strategy_engine_failure_does_not_crash_orchestrator(self) -> None:
        class FailingStrategy(StubEngine):
            def run(self, context: object) -> EngineResult:
                raise RuntimeError("strategy boom")

        orchestrator = build_orchestrator(strategy=FailingStrategy())  # type: ignore[arg-type]
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.FAILED
        assert orchestrator.get_state() in (OrchestratorState.RUNNING, OrchestratorState.DEGRADED)

    def test_get_latest_cycle_result(self) -> None:
        orchestrator = build_orchestrator()
        assert orchestrator.get_latest_cycle_result() is None
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert orchestrator.get_latest_cycle_result() is result


class TestAdditionalCoverage:
    def test_live_stale_snapshot_rejected(self) -> None:
        config = fast_config(
            execution_mode=StrategyExecutionMode.LIVE,
            stale_snapshot_max_age_seconds=1,
        )
        old_snapshot = minimal_valid_snapshot(snapshot_id="stale-1")
        orchestrator = build_orchestrator(
            config=config,
            market_data=MockMarketDataEngine(old_snapshot),
        )
        ref = datetime(2026, 8, 4, 10, 30, tzinfo=IST)
        result = orchestrator.run_trading_cycle(
            make_trading_context(
                reference_time=ref,
                market_snapshot=None,
                execution_mode=StrategyExecutionMode.LIVE,
            )
        )
        assert result.status is CycleStatus.FAILED
        assert result.primary_error_code == "ORCHESTRATOR.SNAPSHOT.STALE"

    def test_market_data_publish_path(self) -> None:
        orchestrator = build_orchestrator()
        result = orchestrator.run_trading_cycle(
            make_trading_context(market_snapshot=None)
        )
        assert result.status is CycleStatus.COMPLETED
        assert result.market_snapshot_id == "msnap-1"

    def test_portfolio_snapshot_unavailable(self) -> None:
        class EmptyPortfolio(MockPortfolioManager):
            def get_snapshot(self) -> None:
                return None

        orchestrator = build_orchestrator(portfolio_manager=EmptyPortfolio())
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.FAILED

    def test_risk_engine_missing(self) -> None:
        orchestrator = build_orchestrator()
        from system.system_orchestrator import CycleExecutor

        orchestrator._registry = replace(orchestrator._registry, risk=None)
        orchestrator._cycle_executor = CycleExecutor(
            orchestrator._config,
            orchestrator._registry,
            broker_client=orchestrator._broker_client,
            clock=orchestrator_clock,
            record_engine_failure=orchestrator._record_engine_failure,
            invoke_engine=orchestrator._invoke_engine,
        )
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.FAILED

    def test_execution_engine_missing(self) -> None:
        registry = EngineRegistry(
            market_data=MockMarketDataEngine(),
            strategy_evaluation=MockStrategyEngine(),
            trade_decision=MockDecisionEngine(),
            risk=MockRiskEngine(),
            execution=None,
            order_manager=MockOrderManager(),
            portfolio_manager=MockPortfolioManager(),
            position_manager=MockPositionManager(),
        )
        orchestrator = SystemOrchestrator(
            fast_config(),
            engine_registry=registry,
            broker_client=object(),
            clock=orchestrator_clock,
        )
        orchestrator.start()
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.FAILED

    def test_order_submission_missing_broker(self) -> None:
        orchestrator = build_orchestrator(broker_client=None)
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.FAILED

    def test_post_fill_naive_timestamp(self) -> None:
        orchestrator = build_orchestrator()
        naive = datetime(2026, 8, 4, 4, 30)
        result = orchestrator.run_post_fill_cycle(make_post_fill_context(reference_time=naive))
        assert result.status is CycleStatus.REJECTED

    def test_post_fill_empty_correlation(self) -> None:
        orchestrator = build_orchestrator(config=fast_config(strict_correlation=True))
        result = orchestrator.run_post_fill_cycle(make_post_fill_context(correlation_id=""))
        assert result.status is CycleStatus.REJECTED

    def test_post_fill_position_rejected(self) -> None:
        class RejectPosition(MockPositionManager):
            def apply_order_tracker(self, tracker: object, context: object) -> MagicMock:
                from portfolio.position_manager import PositionUpdateStatus

                result = MagicMock()
                result.status = PositionUpdateStatus.REJECTED
                return result

        orchestrator = build_orchestrator(position_manager=RejectPosition())
        result = orchestrator.run_post_fill_cycle(make_post_fill_context())
        assert result.status is CycleStatus.FAILED

    def test_post_fill_portfolio_rejected(self) -> None:
        class RejectPortfolio(MockPortfolioManager):
            def ingest_position_snapshot(self, position_snapshot: object, context: object) -> MagicMock:
                from portfolio.portfolio_manager import PortfolioUpdateStatus

                result = MagicMock()
                result.status = PortfolioUpdateStatus.REJECTED
                return result

        orchestrator = build_orchestrator(portfolio_manager=RejectPortfolio())
        result = orchestrator.run_post_fill_cycle(make_post_fill_context())
        assert result.status is CycleStatus.FAILED

    def test_post_fill_apme_failure(self) -> None:
        class FailingApme(StubEngine):
            def evaluate(self, *args: object, **kwargs: object) -> MagicMock:
                raise RuntimeError("apme failed")

        orchestrator = build_orchestrator(apme=FailingApme())  # type: ignore[arg-type]
        result = orchestrator.run_post_fill_cycle(make_post_fill_context())
        assert result.status is CycleStatus.FAILED

    def test_block_pre_trade_in_degraded(self) -> None:
        registry = EngineRegistry(
            market_data=MockMarketDataEngine(),
            strategy_evaluation=StubEngine(fail_validate=True),
            trade_decision=MockDecisionEngine(),
            risk=MockRiskEngine(),
            execution=MockExecutionEngine(),
            order_manager=MockOrderManager(),
            portfolio_manager=MockPortfolioManager(),
            position_manager=MockPositionManager(),
        )
        orchestrator = SystemOrchestrator(
            fast_config(block_pre_trade_in_degraded=True),
            engine_registry=registry,
            clock=orchestrator_clock,
        )
        orchestrator.start()
        assert orchestrator.get_state() is OrchestratorState.DEGRADED
        with pytest.raises(OrchestratorStateError):
            orchestrator.run_trading_cycle(make_trading_context())

    def test_fail_fast_on_engine_error(self) -> None:
        class FailingStrategy(StubEngine):
            def run(self, context: object) -> EngineResult:
                raise RuntimeError("boom")

        orchestrator = build_orchestrator(
            config=fast_config(fail_fast_on_engine_error=True),
            strategy=FailingStrategy(),  # type: ignore[arg-type]
        )
        with pytest.raises(RuntimeError):
            orchestrator.run_trading_cycle(make_trading_context())

    def test_validate_trading_cycle_result_invalid(self) -> None:
        orchestrator = build_orchestrator()
        result = orchestrator.run_trading_cycle(make_trading_context())
        invalid = replace(result, cycle_id="")
        validation = validate_trading_cycle_result(invalid)
        assert not validation.is_valid

    def test_validate_shutdown_drain_negative(self) -> None:
        config = replace(default_orchestrator_config(), shutdown_drain_timeout_seconds=-1)
        result = validate_orchestrator_config(config)
        assert not result.is_valid

    def test_validate_stale_snapshot_negative(self) -> None:
        config = replace(default_orchestrator_config(), stale_snapshot_max_age_seconds=-1)
        result = validate_orchestrator_config(config)
        assert not result.is_valid

    def test_dispatch_apme_escalation_event(self) -> None:
        orchestrator = build_orchestrator()
        envelope = EventEnvelope(
            event_id="evt-2",
            topic="apme.risk.escalated",
            payload={"level": "critical"},
            correlation_id="corr-esc",
            causation_id=None,
            producer="apme",
            producer_version="1.0.0",
            occurred_at=fixed_as_of(),
            published_at=fixed_as_of(),
        )
        orchestrator._dispatch_event(envelope)

    def test_engine_consecutive_failures_degraded(self) -> None:
        orchestrator = build_orchestrator()

        class FailingRisk(StubEngine):
            def review(self, context: object) -> MagicMock:
                raise RuntimeError("risk fail")

        orchestrator._registry = replace(orchestrator._registry, risk=FailingRisk())
        orchestrator._cycle_executor = __import__(
            "system.system_orchestrator", fromlist=["CycleExecutor"]
        ).CycleExecutor(
            orchestrator._config,
            orchestrator._registry,
            broker_client=orchestrator._broker_client,
            clock=orchestrator_clock,
            record_engine_failure=orchestrator._record_engine_failure,
            invoke_engine=orchestrator._invoke_engine,
        )
        for _ in range(3):
            orchestrator.run_trading_cycle(make_trading_context())
        assert orchestrator.get_state() is OrchestratorState.DEGRADED

    def test_strategy_engine_missing(self) -> None:
        registry = EngineRegistry(
            market_data=MockMarketDataEngine(),
            strategy_evaluation=None,
            trade_decision=MockDecisionEngine(),
            risk=MockRiskEngine(),
            execution=MockExecutionEngine(),
            order_manager=MockOrderManager(),
            portfolio_manager=MockPortfolioManager(),
            position_manager=MockPositionManager(),
        )
        orchestrator = SystemOrchestrator(fast_config(), engine_registry=registry, clock=orchestrator_clock)
        orchestrator.start()
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.FAILED

    def test_registry_snapshot_missing(self) -> None:
        orchestrator = build_orchestrator()
        result = orchestrator.run_trading_cycle(
            make_trading_context(registry_snapshot=None)
        )
        assert result.status is CycleStatus.FAILED

    def test_publish_system_events_disabled(self) -> None:
        orchestrator = build_orchestrator(config=fast_config(publish_system_events=False))
        orchestrator._publish_system_event(
            OrchestratorEventType.CYCLE_STARTED,
            "pipeline.cycle.started",
            "corr-x",
        )
        assert orchestrator._publish_count == 0

    def test_get_health_degraded_and_stale(self) -> None:
        orchestrator = build_orchestrator()
        orchestrator._last_market_snapshot_at = datetime(2020, 1, 1, tzinfo=IST)
        with orchestrator._lock:
            orchestrator._state = OrchestratorState.DEGRADED
        report = orchestrator.get_health()
        assert report.overall_status is HealthStatus.DEGRADED
        assert report.stale_snapshot is True

    def test_market_data_engine_missing_on_refresh(self) -> None:
        orchestrator = build_orchestrator()
        from system.system_orchestrator import CycleExecutor

        orchestrator._registry = replace(orchestrator._registry, market_data=None)
        orchestrator._cycle_executor = CycleExecutor(
            orchestrator._config,
            orchestrator._registry,
            broker_client=orchestrator._broker_client,
            clock=orchestrator_clock,
            record_engine_failure=orchestrator._record_engine_failure,
            invoke_engine=orchestrator._invoke_engine,
        )
        result = orchestrator.run_trading_cycle(make_trading_context(market_snapshot=None))
        assert result.status is CycleStatus.FAILED

    def test_market_data_publish_failure(self) -> None:
        class BadMarketData(StubEngine):
            def publish_snapshot(self, **kwargs: object) -> MagicMock:
                raise RuntimeError("publish failed")

        orchestrator = build_orchestrator(market_data=BadMarketData())  # type: ignore[arg-type]
        result = orchestrator.run_trading_cycle(make_trading_context(market_snapshot=None))
        assert result.status is CycleStatus.FAILED

    def test_trade_decision_engine_missing(self) -> None:
        orchestrator = build_orchestrator()
        from system.system_orchestrator import CycleExecutor

        orchestrator._registry = replace(orchestrator._registry, trade_decision=None)
        orchestrator._cycle_executor = CycleExecutor(
            orchestrator._config,
            orchestrator._registry,
            broker_client=orchestrator._broker_client,
            clock=orchestrator_clock,
            record_engine_failure=orchestrator._record_engine_failure,
            invoke_engine=orchestrator._invoke_engine,
        )
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.FAILED

    def test_shutdown_forced_with_inflight_cycle(self) -> None:
        orchestrator = build_orchestrator(config=fast_config(shutdown_drain_timeout_seconds=0))
        with orchestrator._lock:
            orchestrator._in_flight_cycles = 1
        result = orchestrator.stop()
        assert result.status is ShutdownStatus.FORCED

    def test_start_invalid_state_raises(self) -> None:
        orchestrator = build_orchestrator()
        with pytest.raises(OrchestratorStateError):
            orchestrator.start()

    def test_health_unhealthy_engine_issue(self) -> None:
        orchestrator = build_orchestrator()
        orchestrator._engine_health[EngineId.RISK] = __import__(
            "system.system_orchestrator", fromlist=["EngineHealthStatus"]
        ).EngineHealthStatus(
            engine_id=EngineId.RISK,
            status=HealthStatus.UNHEALTHY,
            last_success_at=None,
            last_failure_at=fixed_as_of(),
            consecutive_failures=3,
            message="risk down",
        )
        report = orchestrator.get_health()
        assert any(i.engine_id is EngineId.RISK for i in report.issues)

    def test_validate_trading_cycle_result_multiple_errors(self) -> None:
        naive = datetime(2026, 8, 4, 4, 30)
        result = MagicMock()
        result.cycle_id = ""
        result.correlation_id = ""
        result.submitted_at = naive
        validation = validate_trading_cycle_result(result)
        assert len(validation.errors) >= 2

    def test_event_driven_market_snapshot_triggers_cycle(self) -> None:
        orchestrator = build_orchestrator(config=fast_config(enable_event_driven_cycles=True))
        snapshot = make_market_snapshot()
        envelope = EventEnvelope(
            event_id="evt-3",
            topic="market.snapshot.published",
            payload=snapshot,
            correlation_id="corr-event-2",
            causation_id=None,
            producer="market_data",
            producer_version="1.0.0",
            occurred_at=fixed_as_of(),
            published_at=fixed_as_of(),
        )
        orchestrator.on_market_snapshot_event(envelope)
        assert orchestrator.get_latest_cycle_result() is not None

    def test_no_market_snapshot_after_publish(self) -> None:
        class NullSnapshotMarket(StubEngine):
            def publish_snapshot(self, **kwargs: object) -> MagicMock:
                event = MagicMock()
                event.snapshot = None
                return event

        orchestrator = build_orchestrator(market_data=NullSnapshotMarket())  # type: ignore[arg-type]
        result = orchestrator.run_trading_cycle(make_trading_context(market_snapshot=None))
        assert result.status is CycleStatus.FAILED

    def test_market_data_missing_publish_method(self) -> None:
        orchestrator = build_orchestrator(market_data=StubEngine())  # type: ignore[arg-type]
        result = orchestrator.run_trading_cycle(make_trading_context(market_snapshot=None))
        assert result.status is CycleStatus.FAILED

    def test_strategy_evaluate_fallback(self) -> None:
        class EvaluateOnlyStrategy(StubEngine):
            def evaluate(self, context: object) -> EngineResult:
                return EngineResult(
                    status=EngineStatus.SUCCESS,
                    metadata=MagicMock(),
                    payload=make_bundle(),
                )

        orchestrator = build_orchestrator(strategy=EvaluateOnlyStrategy())  # type: ignore[arg-type]
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.COMPLETED

    def test_decision_decide_fallback(self) -> None:
        class DecideOnlyEngine(StubEngine):
            def decide(self, context: object) -> MagicMock:
                return make_trade_decision()

        orchestrator = build_orchestrator(decision=DecideOnlyEngine())  # type: ignore[arg-type]
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.COMPLETED

    def test_execution_plan_method_fallback(self) -> None:
        class PlanOnlyExecution(StubEngine):
            def plan(self, context: object) -> MagicMock:
                return make_execution_plan()

        orchestrator = build_orchestrator(execution=PlanOnlyExecution())  # type: ignore[arg-type]
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.COMPLETED

    def test_order_submission_failure(self) -> None:
        class FailingOrderManager(StubEngine):
            def submit_plan(self, plan: object, broker: object, context: object) -> MagicMock:
                raise RuntimeError("submit failed")

        orchestrator = build_orchestrator(order_manager=FailingOrderManager())  # type: ignore[arg-type]
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.FAILED

    def test_post_fill_position_manager_missing(self) -> None:
        orchestrator = build_orchestrator()
        from system.system_orchestrator import CycleExecutor

        orchestrator._registry = replace(orchestrator._registry, position_manager=None)
        orchestrator._cycle_executor = CycleExecutor(
            orchestrator._config,
            orchestrator._registry,
            broker_client=orchestrator._broker_client,
            clock=orchestrator_clock,
            record_engine_failure=orchestrator._record_engine_failure,
            invoke_engine=orchestrator._invoke_engine,
        )
        result = orchestrator.run_post_fill_cycle(make_post_fill_context())
        assert result.status is CycleStatus.FAILED

    def test_post_fill_portfolio_manager_missing(self) -> None:
        orchestrator = build_orchestrator()
        from system.system_orchestrator import CycleExecutor

        orchestrator._registry = replace(orchestrator._registry, portfolio_manager=None)
        orchestrator._cycle_executor = CycleExecutor(
            orchestrator._config,
            orchestrator._registry,
            broker_client=orchestrator._broker_client,
            clock=orchestrator_clock,
            record_engine_failure=orchestrator._record_engine_failure,
            invoke_engine=orchestrator._invoke_engine,
        )
        result = orchestrator.run_post_fill_cycle(make_post_fill_context())
        assert result.status is CycleStatus.FAILED

    def test_isoformat_utc_rejects_naive(self) -> None:
        from system.system_orchestrator import _isoformat_utc

        with pytest.raises(OrchestratorValidationError):
            _isoformat_utc(datetime(2026, 8, 4, 4, 30))

    def test_cycle_executor_direct_error_isolation(self) -> None:
        from system.system_orchestrator import CycleExecutor

        registry = EngineRegistry(
            market_data=MockMarketDataEngine(),
            strategy_evaluation=MockStrategyEngine(),
            trade_decision=MockDecisionEngine(),
            risk=MockRiskEngine(),
            execution=MockExecutionEngine(),
            order_manager=MockOrderManager(),
            portfolio_manager=MockPortfolioManager(),
        )
        executor = CycleExecutor(
            fast_config(),
            registry,
            broker_client=object(),
            clock=orchestrator_clock,
        )

        def boom() -> None:
            raise RuntimeError("isolated")

        assert executor._call_engine(EngineId.RISK, boom, "cycle-1") is None

    def test_portfolio_manager_missing_pre_trade(self) -> None:
        orchestrator = build_orchestrator()
        from system.system_orchestrator import CycleExecutor

        orchestrator._registry = replace(orchestrator._registry, portfolio_manager=None)
        orchestrator._cycle_executor = CycleExecutor(
            orchestrator._config,
            orchestrator._registry,
            broker_client=orchestrator._broker_client,
            clock=orchestrator_clock,
            record_engine_failure=orchestrator._record_engine_failure,
            invoke_engine=orchestrator._invoke_engine,
        )
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.FAILED

    def test_trade_decision_unavailable_payload(self) -> None:
        class EmptyDecision(MockDecisionEngine):
            def run(self, context: object) -> EngineResult:
                return EngineResult(
                    status=EngineStatus.SUCCESS,
                    metadata=MagicMock(),
                    payload=SimpleNamespace(),
                )

        orchestrator = build_orchestrator(decision=EmptyDecision())
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.FAILED

    def test_execution_planning_failure(self) -> None:
        class FailingExecution(StubEngine):
            def plan_from_run_context(self, context: object) -> MagicMock:
                raise RuntimeError("plan failed")

        orchestrator = build_orchestrator(execution=FailingExecution())  # type: ignore[arg-type]
        result = orchestrator.run_trading_cycle(make_trading_context())
        assert result.status is CycleStatus.FAILED

    def test_utc_now_helper(self) -> None:
        from system.system_orchestrator import _utc_now

        now = _utc_now()
        assert now.tzinfo is not None


class TestComputeFingerprint:
    def test_compute_cycle_fingerprint_changes_with_status(self) -> None:
        config = fast_config()
        ctx = make_trading_context()
        completed = MagicMock()
        completed.status = CycleStatus.COMPLETED
        completed.risk_verdict = "approved"
        completed.execution_plan_id = "p1"
        completed.submitted_at = fixed_as_of()
        failed = MagicMock()
        failed.status = CycleStatus.FAILED
        failed.risk_verdict = None
        failed.execution_plan_id = None
        failed.submitted_at = fixed_as_of()
        fp1 = compute_cycle_fingerprint(ctx, completed, config)
        fp2 = compute_cycle_fingerprint(ctx, failed, config)
        assert fp1 != fp2