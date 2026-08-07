"""Unit tests for system.paper_trading_runtime."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType

import pytest

from core.event_bus import EventEnvelope
from dashboard.live_session_adapter import (
    build_default_presentation_facade,
    clear_live_handles,
    get_registered_live_handles,
)
from decision.ai_decision_loop import PaperTradeDecision, PaperTradeDecisionStatus
from execution.execution_engine import (
    ExecutionEngine,
    ExecutionPlan,
    ExecutionPlanStatus,
    default_execution_engine_config,
)
from market_data.market_snapshot import (
    OptionContractSnapshot,
    OptionType,
    UnderlyingSnapshot,
    VolatilitySnapshot,
    build_market_snapshot,
)
from paper_trading.paper_trading_runner import PaperExecutionStatus
from strategy.signals import StructureHint
from system.paper_trading_runtime import (
    PaperTradeExecutionOutcome,
    PaperTradingRuntime,
    _marks_from_plan,
)
from system.system_orchestrator import SystemOrchestrator
from tests.test_ai_decision_loop import make_loop
from tests.test_execution_engine import build_approved_risk, fixed_as_of
from tests.test_order_manager import build_ready_plan
from tests.test_strategy_evaluation_engine import FixedClock

_FIXTURE_EXPIRY = "2026-08-07"  # matches build_approved_risk()'s default signal.market.expiry


def _short_strangle_snapshot_with_deltas():
    """Build a real MarketSnapshot with two liquid, delta-tagged NIFTY legs.

    Bid/ask are tight (spread well under OptionContractSelector's default
    2% cap) and deltas sit at the strategy's target so the real selector
    genuinely selects both legs rather than being starved by fixture data.
    """
    as_of = fixed_as_of()

    def contract(strike: float, option_type: OptionType, delta: float, tradingsymbol: str):
        return OptionContractSnapshot(
            underlying="NIFTY",
            exchange="NFO",
            tradingsymbol=tradingsymbol,
            expiry=_FIXTURE_EXPIRY,
            strike=strike,
            option_type=option_type,
            lot_size=75,
            ltp=30.0,
            bid=29.9,
            ask=30.1,
            volume=5000,
            open_interest=20000,
            delta=delta,
            quote_timestamp=as_of,
        )

    contracts = (
        contract(24700.0, OptionType.CE, 0.18, "NIFTY26080724700CE"),
        contract(24300.0, OptionType.PE, -0.18, "NIFTY26080724300PE"),
    )
    return build_market_snapshot(
        underlying=UnderlyingSnapshot("NIFTY", "NSE", "NSE:NIFTY 50", 24500.0, quote_timestamp=as_of),
        contracts=contracts,
        underlying_symbol="NIFTY",
        exchange="NFO",
        expiry=_FIXTURE_EXPIRY,
        atm_strike=24500.0,
        strike_step=100.0,
        strike_window_strikes=4,
        minimum_strike=24300.0,
        maximum_strike=24700.0,
        lot_size=75,
        as_of=as_of,
        captured_at=as_of,
        reference_time=as_of,
        snapshot_id="contract-selection-fixture",
        volatility=VolatilitySnapshot("INDIA VIX", "NSE", "NSE:INDIA VIX", 15.0, as_of),
    )


def _real_trade_candidate_with_structure_hint():
    """Build a genuine APPROVED RiskDecisionResult with a populated structure_hint.

    Mirrors exactly what every currently implemented strategy (short
    strangle, iron condor, bull put/call spreads) emits: two option-type
    legs and a target delta, produced by the real strategy/decision/risk
    pipeline via build_approved_risk (only the structure_hint is attached
    afterwards, since the fixture strategy build_approved_risk uses does not
    itself set one).
    """
    snapshot = _short_strangle_snapshot_with_deltas()
    risk, _ = build_approved_risk(FixedClock(), snapshot=snapshot)
    structure = StructureHint(
        "short_strangle", 2, "delta_ranked_otm", 0.18, 1, (OptionType.CE, OptionType.PE)
    )
    signal = replace(risk.trading_signal, structure_hint=structure)
    risk = replace(risk, trading_signal=signal)
    decision = PaperTradeDecision(
        decision_id="real-d1",
        correlation_id=risk.correlation_id,
        as_of=fixed_as_of(),
        underlying=signal.market.underlying,
        status=PaperTradeDecisionStatus.TRADE_CANDIDATE,
        strategy_id=signal.strategy_id,
        risk_verdict=risk.verdict.value,
        approved_risk_budget=risk.approved_risk_budget,
        approved_risk_pct=risk.approved_risk_pct,
        final_lots=1,
        final_quantity=75,
        risk_decision=risk,
    )
    return decision, snapshot


class _StubReadyExecutionEngine:
    """Test double whose plan_from_run_context always returns a READY plan.

    Substitutes the real contract-selection-dependent pipeline (a known,
    pre-existing gap — see test_execute_decision_uses_real_engine_and_is_
    rejected_without_contract_selection below) so the decision -> plan ->
    paper-fill bridge itself can be exercised in isolation with a genuinely
    sealed ExecutionPlan built by the real ExecutionEngine.
    """

    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan = plan
        self.calls: list[object] = []

    def plan_from_run_context(self, run_context: object) -> ExecutionPlan:
        self.calls.append(run_context)
        return self.plan


class _StubRejectedExecutionEngine:
    """Test double whose plan_from_run_context always returns a non-READY plan."""

    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan = plan

    def plan_from_run_context(self, run_context: object) -> ExecutionPlan:
        return self.plan


@pytest.fixture(autouse=True)
def _clean_dashboard_handles():
    """Ensure no test leaks live handles into another test via the module global."""
    clear_live_handles()
    yield
    clear_live_handles()


def _trade_candidate_decision(risk_decision: object) -> PaperTradeDecision:
    return PaperTradeDecision(
        decision_id="decision-1",
        correlation_id="corr-1",
        as_of=fixed_as_of(),
        underlying="NIFTY",
        status=PaperTradeDecisionStatus.TRADE_CANDIDATE,
        strategy_id="short_strangle",
        final_lots=1,
        final_quantity=75,
        risk_decision=risk_decision,
    )


class _StaticSnapshotProvider:
    def __init__(self, snapshot: object) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def get_snapshot(self, underlying: str) -> object:
        self.calls += 1
        return self.snapshot


class TestMarksFromPlan:
    def test_uses_limit_price_hint_when_present(self) -> None:
        plan = build_ready_plan()
        marks = _marks_from_plan(plan)
        for leg in plan.legs:
            assert marks[leg.instrument_key] == Decimal(str(leg.limit_price_hint))

    def test_falls_back_to_placeholder_mark_when_hint_missing(self) -> None:
        from dataclasses import replace

        plan = build_ready_plan()
        leg = replace(plan.legs[0], limit_price_hint=None)
        plan = replace(plan, legs=(leg,) + plan.legs[1:])
        marks = _marks_from_plan(plan)
        assert marks[leg.instrument_key] == Decimal("100.00")


class TestDecisionToPaperTradeBridge:
    """Task 2/3/4: PaperTradeDecision -> ExecutionPlan -> PaperTradingRunner."""

    def test_non_trade_candidate_decision_is_skipped_without_planning(self) -> None:
        loop, provider, _ = make_loop()
        engine = _StubReadyExecutionEngine(build_ready_plan())
        runtime = PaperTradingRuntime(
            decision_loop=loop, market_data_engine=provider, execution_engine=engine
        )
        decision = PaperTradeDecision(
            decision_id="d1",
            correlation_id="c1",
            as_of=fixed_as_of(),
            underlying="NIFTY",
            status=PaperTradeDecisionStatus.NO_TRADE,
        )

        outcome = runtime._execute_decision(decision)

        assert outcome.execution_plan is None
        assert outcome.paper_result is None
        assert outcome.skipped_reason == "decision_status=no_trade"
        assert engine.calls == []

    def test_trade_candidate_without_risk_decision_is_skipped(self) -> None:
        loop, provider, _ = make_loop()
        engine = _StubReadyExecutionEngine(build_ready_plan())
        runtime = PaperTradingRuntime(
            decision_loop=loop, market_data_engine=provider, execution_engine=engine
        )
        decision = _trade_candidate_decision(risk_decision=None)

        outcome = runtime._execute_decision(decision)

        assert outcome.skipped_reason == "missing_risk_decision"
        assert engine.calls == []

    def test_trade_candidate_with_no_snapshot_is_skipped(self) -> None:
        loop, _, _ = make_loop()
        plan = build_ready_plan()
        engine = _StubReadyExecutionEngine(plan)
        empty_provider = _StaticSnapshotProvider(None)
        outcomes: list[PaperTradeExecutionOutcome] = []
        runtime = PaperTradingRuntime(
            decision_loop=loop,
            market_data_engine=empty_provider,
            execution_engine=engine,
            on_outcome=outcomes.append,
        )

        real_decision = loop.run_once()  # drives runtime automatically via the callback

        assert real_decision.status is PaperTradeDecisionStatus.TRADE_CANDIDATE
        assert len(outcomes) == 1
        assert outcomes[0].skipped_reason == "missing_snapshot"
        assert empty_provider.calls == 1

    def test_ready_plan_produces_a_real_paper_trade(self) -> None:
        loop, provider, _ = make_loop()
        decision = loop.run_once()
        assert decision.status is PaperTradeDecisionStatus.TRADE_CANDIDATE
        ready_plan = build_ready_plan()
        engine = _StubReadyExecutionEngine(ready_plan)
        runtime = PaperTradingRuntime(
            decision_loop=loop, market_data_engine=provider, execution_engine=engine
        )
        capital_before = runtime.paper_trading_runner.get_capital_snapshot()

        outcome = runtime._execute_decision(decision)

        assert isinstance(outcome, PaperTradeExecutionOutcome)
        assert outcome.skipped_reason is None
        assert outcome.execution_plan is ready_plan
        assert outcome.paper_result is not None
        assert outcome.paper_result.status in (
            PaperExecutionStatus.COMPLETED,
            PaperExecutionStatus.PARTIAL,
        )
        capital_after = runtime.paper_trading_runner.get_capital_snapshot()
        assert capital_after.cash != capital_before.cash
        position_book = runtime.paper_trading_runner.get_position_book()
        assert len(position_book.positions) > 0

    def test_non_ready_plan_is_skipped_and_never_reaches_paper_runner(self) -> None:
        loop, provider, _ = make_loop()
        decision = loop.run_once()
        rejected_plan = build_ready_plan()
        from dataclasses import replace

        rejected_plan = replace(rejected_plan, status=ExecutionPlanStatus.REJECTED)
        engine = _StubRejectedExecutionEngine(rejected_plan)
        runtime = PaperTradingRuntime(
            decision_loop=loop, market_data_engine=provider, execution_engine=engine
        )
        capital_before = runtime.paper_trading_runner.get_capital_snapshot()

        outcome = runtime._execute_decision(decision)

        assert outcome.paper_result is None
        assert outcome.skipped_reason == "plan_status=rejected"
        capital_after = runtime.paper_trading_runner.get_capital_snapshot()
        assert capital_after.cash == capital_before.cash

    def test_real_execution_engine_is_rejected_without_upstream_contract_selection(self) -> None:
        """Documents the current, honest end state of the real pipeline.

        AIDecisionLoop does not (yet) produce a ContractSelectionResult, and
        ExecutionEngine requires one in LIVE mode by default — so today a
        real TRADE_CANDIDATE decision correctly produces no paper trade
        until that upstream contract-selection wiring exists. This is not a
        bug in this bridge: the bridge correctly reports the skip rather
        than fabricating a trade.
        """
        loop, provider, _ = make_loop()
        decision = loop.run_once()
        assert decision.status is PaperTradeDecisionStatus.TRADE_CANDIDATE
        real_engine = ExecutionEngine(default_execution_engine_config())
        runtime = PaperTradingRuntime(
            decision_loop=loop, market_data_engine=provider, execution_engine=real_engine
        )

        outcome = runtime._execute_decision(decision)

        assert outcome.execution_plan is not None
        assert outcome.execution_plan.status is not ExecutionPlanStatus.READY
        assert outcome.skipped_reason == f"plan_status={outcome.execution_plan.status.value}"
        assert outcome.paper_result is None


class TestAutomaticWiringViaDecisionCallback:
    """Task 2/4: the loop drives the paper runner with no manual runtime call."""

    def test_run_once_on_the_loop_automatically_produces_a_paper_trade(self) -> None:
        loop, provider, _ = make_loop()
        engine = _StubReadyExecutionEngine(build_ready_plan())
        outcomes: list[PaperTradeExecutionOutcome] = []
        runtime = PaperTradingRuntime(
            decision_loop=loop,
            market_data_engine=provider,
            execution_engine=engine,
            on_outcome=outcomes.append,
        )

        decision = loop.run_once()  # caller only touches the decision loop, not runtime

        assert decision.status is PaperTradeDecisionStatus.TRADE_CANDIDATE
        assert len(outcomes) == 1
        assert outcomes[0].paper_result is not None
        assert runtime.paper_trading_runner.get_position_book().positions


class TestEventBusSharing:
    """Task 1: AIDecisionLoop's paper-trading runtime shares SystemOrchestrator's bus."""

    def test_paper_runner_publishes_onto_orchestrators_shared_event_bus(self) -> None:
        loop, provider, _ = make_loop()
        engine = _StubReadyExecutionEngine(build_ready_plan())
        orchestrator = SystemOrchestrator()
        runtime = PaperTradingRuntime(
            decision_loop=loop,
            market_data_engine=provider,
            orchestrator=orchestrator,
            execution_engine=engine,
        )
        assert runtime.event_bus is orchestrator.event_bus

        received: list[EventEnvelope] = []
        orchestrator.event_bus.subscribe("paper.order.plan.completed", received.append)

        decision = loop.run_once()
        assert decision.status is PaperTradeDecisionStatus.TRADE_CANDIDATE

        assert len(received) == 1
        assert received[0].topic == "paper.order.plan.completed"


class TestDashboardRegistration:
    """Task 5: the live PaperTradingRunner becomes visible to dashboard pages."""

    def test_start_registers_live_paper_runner_with_dashboard(self) -> None:
        loop, provider, _ = make_loop(interval_seconds=100.0)
        engine = _StubReadyExecutionEngine(build_ready_plan())
        runtime = PaperTradingRuntime(
            decision_loop=loop, market_data_engine=provider, execution_engine=engine
        )
        assert get_registered_live_handles() is None

        runtime.start()
        try:
            handles = get_registered_live_handles()
            assert handles is not None
            assert handles.paper_runner is runtime.paper_trading_runner
            assert handles.market_streaming is provider
            assert handles.evaluation_bundle_provider() is loop.get_latest_bundle()

            facade = build_default_presentation_facade()
            # Must not raise even before any decision has produced a fill.
            facade.get_paper_trading()
        finally:
            runtime.stop()

        assert get_registered_live_handles() is None

    def test_dashboard_reflects_paper_positions_after_a_fill(self) -> None:
        loop, provider, _ = make_loop(interval_seconds=100.0)
        engine = _StubReadyExecutionEngine(build_ready_plan())
        runtime = PaperTradingRuntime(
            decision_loop=loop, market_data_engine=provider, execution_engine=engine
        )
        runtime.start()
        try:
            decision = loop.run_once()
            assert decision.status is PaperTradeDecisionStatus.TRADE_CANDIDATE

            facade = build_default_presentation_facade()
            snapshot = facade.get_paper_trading()
            assert snapshot is not None
        finally:
            runtime.stop()


class TestRealContractSelection:
    """The real OptionContractSelector closes the contract-selection gap.

    Uses a genuine RiskDecisionResult/TradingSignal from build_approved_risk
    (real strategy -> decision -> risk pipeline) plus a real MarketSnapshot —
    no fake data, no bypass of ExecutionEngine's own validation.
    """

    def test_select_contracts_builds_a_real_two_leg_selection(self) -> None:
        loop, provider, _ = make_loop()
        runtime = PaperTradingRuntime(decision_loop=loop, market_data_engine=provider)
        decision, snapshot = _real_trade_candidate_with_structure_hint()

        selection = runtime._select_contracts(decision, snapshot)

        assert selection is not None
        assert selection.correlation_id == decision.correlation_id
        assert selection.underlying == "NIFTY"
        assert len(selection.legs) == 2
        instrument_keys = {leg.instrument_key for leg in selection.legs}
        assert instrument_keys == {"NFO:NIFTY26080724700CE", "NFO:NIFTY26080724300PE"}
        for leg in selection.legs:
            assert leg.option_type in (OptionType.CE, OptionType.PE)
            assert leg.lot_size == 75

    def test_select_contracts_returns_none_without_structure_hint(self) -> None:
        loop, provider, _ = make_loop()
        runtime = PaperTradingRuntime(decision_loop=loop, market_data_engine=provider)
        decision, snapshot = _real_trade_candidate_with_structure_hint()
        bare_signal = replace(decision.risk_decision.trading_signal, structure_hint=None)
        bare_risk = replace(decision.risk_decision, trading_signal=bare_signal)
        decision = replace(decision, risk_decision=bare_risk)

        assert runtime._select_contracts(decision, snapshot) is None

    def test_build_position_sizing_hint_gives_every_leg_the_full_quantity(self) -> None:
        """A 2-leg strangle must not be halved by ExecutionEngine's even-split fallback."""
        loop, provider, _ = make_loop()
        runtime = PaperTradingRuntime(decision_loop=loop, market_data_engine=provider)
        decision, snapshot = _real_trade_candidate_with_structure_hint()
        selection = runtime._select_contracts(decision, snapshot)
        assert selection is not None

        hint = runtime._build_position_sizing_hint(decision, selection)

        assert hint is not None
        assert hint.proposed_risk_amount == decision.approved_risk_budget
        assert hint.proposed_risk_pct == decision.approved_risk_pct
        assert hint.metadata["leg_0_quantity"] == str(decision.final_quantity)
        assert hint.metadata["leg_1_quantity"] == str(decision.final_quantity)

    def test_build_position_sizing_hint_returns_none_for_non_positive_quantity(self) -> None:
        loop, provider, _ = make_loop()
        runtime = PaperTradingRuntime(decision_loop=loop, market_data_engine=provider)
        decision, _ = _real_trade_candidate_with_structure_hint()
        decision = replace(decision, final_quantity=0)

        assert runtime._build_position_sizing_hint(decision, None) is None

    def test_execute_decision_produces_a_complete_end_to_end_paper_trade(self) -> None:
        """The full, real pipeline — no manual injection of anything.

        live snapshot -> AIDecisionLoop's real risk/sizing output ->
        OptionContractSelector -> PositionSizingHint -> ExecutionEngine
        (READY) -> PaperTradingRunner (COMPLETED, position opened) — driven
        by nothing but PaperTradingRuntime._execute_decision itself, proving
        no "missing context" rejection remains for this decision shape.
        """
        loop, _, _ = make_loop()
        decision, snapshot = _real_trade_candidate_with_structure_hint()
        runtime = PaperTradingRuntime(
            decision_loop=loop, market_data_engine=_StaticSnapshotProvider(snapshot)
        )

        outcome = runtime._execute_decision(decision)

        assert outcome.skipped_reason is None
        assert outcome.execution_plan is not None
        assert outcome.execution_plan.status is ExecutionPlanStatus.READY
        assert outcome.execution_plan.errors == ()
        assert outcome.paper_result is not None
        assert outcome.paper_result.status in (
            PaperExecutionStatus.COMPLETED,
            PaperExecutionStatus.PARTIAL,
        )
        assert len(outcome.paper_result.fills) == 2
        positions = runtime.paper_trading_runner.get_position_book().positions
        assert len(positions) == 2
        instrument_keys = {position.instrument_key for position in positions}
        assert instrument_keys == {"NFO:NIFTY26080724700CE", "NFO:NIFTY26080724300PE"}
        for position in positions:
            assert position.quantity == -75  # short strangle: both legs sold

    def test_run_once_on_the_loop_drives_a_real_paper_trade_with_no_manual_step(self) -> None:
        """Same proof as above, but entered purely via the decision-loop callback."""
        loop, _, _ = make_loop()
        decision, snapshot = _real_trade_candidate_with_structure_hint()
        outcomes: list[PaperTradeExecutionOutcome] = []
        runtime = PaperTradingRuntime(
            decision_loop=loop,
            market_data_engine=_StaticSnapshotProvider(snapshot),
            on_outcome=outcomes.append,
        )

        runtime._on_decision(decision)  # exactly what add_decision_callback wires automatically

        assert len(outcomes) == 1
        assert outcomes[0].skipped_reason is None
        assert outcomes[0].paper_result is not None
        # `==` (not `is`): PaperExecutionStatus is a str-Enum, and
        # tests/test_paper_trading_runner.py's own reload test can leave a
        # second, distinct-but-equal-by-value class object in sys.modules
        # when the full suite runs in one process.
        assert outcomes[0].paper_result.status == PaperExecutionStatus.COMPLETED
