"""Unit tests for risk.risk_engine."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from core.engine_context import EngineContext
from core.enums import EngineStatus
from decision.trade_decision_engine import (
    DecisionMode,
    DecisionOutcomeClass,
    DecisionStatus,
    TradeDecisionEngine,
    default_trade_decision_engine_config,
)
from risk.risk_engine import (
    ERROR_CAPITAL_BUDGET_EXCEEDED,
    ERROR_CAPITAL_INSUFFICIENT,
    ERROR_CONSECUTIVE_LOSSES,
    ERROR_CONTEXT_CORRELATION_MISMATCH,
    ERROR_CONTEXT_NAIVE_TIMESTAMP,
    ERROR_DAILY_LOSS_LIMIT,
    ERROR_DRAWDOWN_LIMIT,
    ERROR_EXPIRY_DAY_LIMIT,
    ERROR_KILL_SWITCH_ACTIVE,
    ERROR_MARGIN_INSUFFICIENT,
    ERROR_PORTFOLIO_MAX_POSITIONS,
    ERROR_SIGNAL_EXPIRED,
    ERROR_SIZING_EXCEEDS_BUDGET,
    ERROR_SIZING_HINT_REQUIRED,
    ERROR_STRATEGY_BLOCKED,
    ERROR_STRATEGY_UNDEFINED_RISK,
    ERROR_UNDERLYING_BLOCKED,
    ERROR_WINDOW_NEAR_CLOSE,
    ERROR_WINDOW_OUTSIDE_SESSION,
    PortfolioExposureSummary,
    PortfolioPosition,
    PortfolioSnapshot,
    PositionSizingHint,
    RiskEngine,
    RiskEngineConfig,
    RiskEngineConfigurationError,
    RiskEngineContextError,
    RiskProfileTier,
    RiskRunContext,
    RiskStageId,
    RiskTimeWindow,
    RiskTradingWindowPolicy,
    RiskVerdict,
    SkipReasonCode,
    UserRiskProfile,
    compute_drawdown_pct,
    compute_daily_loss_pct,
    compute_heuristic_margin_demand,
    default_risk_engine_config,
    default_user_risk_profile,
    portfolio_fingerprint,
    risk_fingerprint,
    risk_from_json,
    risk_to_json,
)
from strategy.signals import (
    MarginIntensityHint,
    StrategyExecutionMode,
    StrategyFamily,
    is_signal_expired,
)
from strategy.strategy_evaluation_engine import CapitalEstimateCategory
from tests.test_base_strategy import fixed_as_of
from tests.test_strategy_evaluation_engine import FixedClock, make_strategy, setup_registry
from tests.test_trade_decision_engine import evaluate_bundle, make_decision_context

IST = ZoneInfo("Asia/Kolkata")


def make_portfolio_snapshot(
    *,
    equity: float = 1_000_000.0,
    cash_available: float = 500_000.0,
    correlation_id: str = "corr-eval-001",
    open_positions: tuple[PortfolioPosition, ...] = (),
    daily_realized_pnl: float = 0.0,
    daily_unrealized_pnl: float = 0.0,
    peak_equity: float | None = None,
    consecutive_losses: int = 0,
    margin_available_hint: float | None = 500_000.0,
) -> PortfolioSnapshot:
    """Build valid portfolio snapshot with sensible defaults."""
    gross = sum(position.notional_exposure for position in open_positions)
    gross_by_underlying: dict[str, float] = {}
    net_by_underlying: dict[str, float] = {}
    count_by_underlying: dict[str, int] = {}
    exposure_by_family: dict[str, float] = {}
    for position in open_positions:
        gross_by_underlying[position.underlying] = (
            gross_by_underlying.get(position.underlying, 0.0) + position.notional_exposure
        )
        net_by_underlying[position.underlying] = (
            net_by_underlying.get(position.underlying, 0.0) + position.notional_exposure
        )
        count_by_underlying[position.underlying] = count_by_underlying.get(position.underlying, 0) + 1
        if position.strategy_family is not None:
            key = position.strategy_family.value
            exposure_by_family[key] = exposure_by_family.get(key, 0.0) + position.notional_exposure
    exposure = PortfolioExposureSummary(
        gross_notional=gross,
        net_notional_by_underlying=MappingProxyType(net_by_underlying),
        gross_notional_by_underlying=MappingProxyType(gross_by_underlying),
        exposure_by_family=MappingProxyType(exposure_by_family),
        open_position_count=len(open_positions),
        open_position_count_by_underlying=MappingProxyType(count_by_underlying),
    )
    snapshot = PortfolioSnapshot(
        snapshot_id="port-test-001",
        correlation_id=correlation_id,
        as_of=fixed_as_of(),
        account_id="acct-test-001",
        equity=equity,
        cash_available=cash_available,
        daily_realized_pnl=daily_realized_pnl,
        daily_unrealized_pnl=daily_unrealized_pnl,
        peak_equity=peak_equity if peak_equity is not None else equity,
        consecutive_losses=consecutive_losses,
        open_positions=open_positions,
        exposure_summary=exposure,
        portfolio_fingerprint="",
        margin_available_hint=margin_available_hint,
    )
    return replace(snapshot, portfolio_fingerprint=portfolio_fingerprint(snapshot))


def make_user_risk_profile(**overrides: object) -> object:
    """Build MODERATE tier user risk profile."""
    profile = default_user_risk_profile()
    if overrides:
        return replace(profile, **overrides)
    return profile


def make_position_sizing_hint(
    *,
    proposed_risk_amount: float = 8_000.0,
    proposed_risk_pct: float = 0.8,
    proposed_notional: float = 100_000.0,
    proposed_margin_hint: float = 50_000.0,
) -> PositionSizingHint:
    """Build valid sizing hint within MODERATE budget."""
    return PositionSizingHint(
        hint_id="hint-test-001",
        proposed_risk_amount=proposed_risk_amount,
        proposed_risk_pct=proposed_risk_pct,
        proposed_notional=proposed_notional,
        proposed_margin_hint=proposed_margin_hint,
        sizing_method="test_helper_v1",
    )


def build_selected_decision(clock: FixedClock) -> object:
    """Build SELECTED trade decision via upstream engines."""
    reg, snap = setup_registry(make_strategy(), clock=clock)
    bundle = evaluate_bundle(reg, snap, clock=clock)
    decision_engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
    return decision_engine.decide(make_decision_context(bundle, correlation_id="corr-eval-001"))


def make_risk_run_context(
    decision: object,
    *,
    portfolio: PortfolioSnapshot | None = None,
    profile: object | None = None,
    sizing_hint: PositionSizingHint | None = None,
    force_skip: bool = False,
    tags: dict[str, str] | None = None,
    available_capital: float | None = None,
    available_margin_hint: float | None = None,
    reference_time: datetime | None = None,
    execution_mode: StrategyExecutionMode | None = None,
) -> RiskRunContext:
    """Build valid risk run context from trade decision."""
    return RiskRunContext(
        correlation_id=decision.correlation_id,
        as_of=fixed_as_of(),
        trade_decision=decision,
        portfolio=portfolio or make_portfolio_snapshot(correlation_id=decision.correlation_id),
        user_risk_profile=profile or make_user_risk_profile(),
        position_sizing_hint=sizing_hint,
        execution_mode=execution_mode,
        reference_time=reference_time or fixed_as_of(),
        force_skip=force_skip,
        available_capital=available_capital,
        available_margin_hint=available_margin_hint,
        tags=MappingProxyType(tags or {}),
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def risk_engine(clock: FixedClock) -> RiskEngine:
    return RiskEngine(default_risk_engine_config(), clock=clock)


class TestConfiguration:
    def test_invalid_confidence_multiplier(self) -> None:
        with pytest.raises(RiskEngineConfigurationError):
            RiskEngineConfig(medium_confidence_multiplier=1.5)

    def test_kill_switch_factory(self) -> None:
        config = RiskEngineConfig.with_kill_switch(reason="halt")
        assert config.kill_switch_active is True
        assert config.kill_switch_reason == "halt"


class TestHelpers:
    def test_compute_daily_loss_pct(self) -> None:
        assert compute_daily_loss_pct(daily_pnl=-30_000.0, equity=1_000_000.0) == pytest.approx(3.0)

    def test_compute_drawdown_pct(self) -> None:
        assert compute_drawdown_pct(equity=900_000.0, peak_equity=1_000_000.0) == pytest.approx(10.0)

    def test_compute_heuristic_margin_demand(self) -> None:
        demand = compute_heuristic_margin_demand(
            equity=1_000_000.0,
            margin_intensity=MarginIntensityHint.LOW,
            capital_category=CapitalEstimateCategory.SMALL,
            proposed_margin_hint=None,
            margin_policy=default_risk_engine_config().margin_policy,
        )
        assert demand > 0


class TestContextValidation:
    def test_naive_as_of(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(decision)
        bad = replace(ctx, as_of=datetime(2026, 8, 3, 10, 0, 0))
        with pytest.raises(RiskEngineContextError) as exc:
            risk_engine.validate_run_context(bad)
        assert exc.value.code == ERROR_CONTEXT_NAIVE_TIMESTAMP

    def test_correlation_mismatch(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(decision)
        bad = replace(ctx, correlation_id="other-id")
        with pytest.raises(RiskEngineContextError) as exc:
            risk_engine.validate_run_context(bad)
        assert exc.value.code == ERROR_CONTEXT_CORRELATION_MISMATCH


class TestSkipPaths:
    def test_force_skip(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        result = risk_engine.review(make_risk_run_context(decision, force_skip=True))
        assert result.verdict is RiskVerdict.SKIPPED
        assert result.skip_reason_code is SkipReasonCode.ORCHESTRATOR_SKIP

    def test_abstain_skips(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(enabled=False), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        decision = engine.decide(make_decision_context(bundle))
        result = risk_engine.review(make_risk_run_context(decision))
        assert result.verdict is RiskVerdict.SKIPPED
        assert result.skip_reason_code is SkipReasonCode.DECISION_ABSTAIN

    def test_analysis_mode_skip(self, clock: FixedClock) -> None:
        config = RiskEngineConfig(skip_review_in_analysis=True)
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            execution_mode=StrategyExecutionMode.ANALYSIS,
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.SKIPPED
        assert result.skip_reason_code is SkipReasonCode.ANALYSIS_MODE_SKIP


class TestKillSwitch:
    def test_kill_switch_rejects(self, clock: FixedClock) -> None:
        config = RiskEngineConfig.with_kill_switch(reason="Operator halt")
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            sizing_hint=make_position_sizing_hint(),
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_KILL_SWITCH_ACTIVE
        assert result.pipeline_summary.failed_stage_id is RiskStageId.KILL_SWITCH


class TestApprovalHappyPath:
    def test_all_stages_pass(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        assert decision.decision_status is DecisionStatus.SELECTED
        assert decision.outcome_class is DecisionOutcomeClass.TRADE_CANDIDATE
        ctx = make_risk_run_context(
            decision,
            sizing_hint=make_position_sizing_hint(),
        )
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.APPROVED
        assert result.approved_risk_budget is not None
        assert result.approved_risk_pct is not None
        assert result.pipeline_summary.total_stages == 17
        assert result.pipeline_summary.failed_stage_id is None
        assert result.risk_fingerprint == risk_fingerprint(result)

    def test_engine_result_success(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(decision, sizing_hint=make_position_sizing_hint())
        engine_ctx = EngineContext(
            correlation_id=ctx.correlation_id,
            as_of=ctx.as_of,
            payload=ctx,
        )
        engine_result = risk_engine.evaluate(engine_ctx)
        assert engine_result.status is EngineStatus.SUCCESS
        assert engine_result.payload.verdict is RiskVerdict.APPROVED


class TestRejections:
    def test_insufficient_capital(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            sizing_hint=make_position_sizing_hint(proposed_risk_amount=50_000.0, proposed_risk_pct=5.0),
            available_capital=10_000.0,
        )
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_CAPITAL_INSUFFICIENT

    def test_budget_exceeded_via_sizing(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            sizing_hint=make_position_sizing_hint(proposed_risk_amount=25_000.0, proposed_risk_pct=2.5),
        )
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code in {
            ERROR_CAPITAL_BUDGET_EXCEEDED,
            ERROR_SIZING_EXCEEDS_BUDGET,
        }

    def test_missing_sizing_hint_live(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(decision, sizing_hint=None)
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_SIZING_HINT_REQUIRED

    def test_daily_loss_limit(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        portfolio = make_portfolio_snapshot(
            daily_realized_pnl=-25_000.0,
            daily_unrealized_pnl=-10_000.0,
        )
        ctx = make_risk_run_context(decision, portfolio=portfolio, sizing_hint=make_position_sizing_hint())
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_DAILY_LOSS_LIMIT

    def test_drawdown_limit(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        portfolio = make_portfolio_snapshot(equity=850_000.0, peak_equity=1_000_000.0)
        ctx = make_risk_run_context(decision, portfolio=portfolio, sizing_hint=make_position_sizing_hint())
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_DRAWDOWN_LIMIT

    def test_consecutive_losses(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        portfolio = make_portfolio_snapshot(consecutive_losses=5)
        ctx = make_risk_run_context(decision, portfolio=portfolio, sizing_hint=make_position_sizing_hint())
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_CONSECUTIVE_LOSSES

    def test_max_positions(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        positions = tuple(
            PortfolioPosition(
                position_id=f"pos-{index}",
                underlying="NIFTY",
                notional_exposure=50_000.0,
                unrealized_pnl=0.0,
                opened_at=fixed_as_of(),
            )
            for index in range(3)
        )
        portfolio = make_portfolio_snapshot(open_positions=positions)
        ctx = make_risk_run_context(decision, portfolio=portfolio, sizing_hint=make_position_sizing_hint())
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_PORTFOLIO_MAX_POSITIONS

    def test_blocked_strategy(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        profile = make_user_risk_profile(blocked_strategy_ids=frozenset({decision.selected_strategy_id}))
        ctx = make_risk_run_context(
            decision,
            profile=profile,
            sizing_hint=make_position_sizing_hint(),
        )
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_STRATEGY_BLOCKED

    def test_blocked_underlying(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        underlying = decision.selected_signal.market.underlying.upper()
        profile = make_user_risk_profile(blocked_underlyings=frozenset({underlying}))
        ctx = make_risk_run_context(
            decision,
            profile=profile,
            sizing_hint=make_position_sizing_hint(),
        )
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_UNDERLYING_BLOCKED

    def test_margin_insufficient(self, clock: FixedClock) -> None:
        config = default_risk_engine_config()
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            sizing_hint=make_position_sizing_hint(proposed_margin_hint=2_000_000.0),
            available_margin_hint=1_000.0,
            portfolio=make_portfolio_snapshot(margin_available_hint=1_000.0),
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_MARGIN_INSUFFICIENT

    def test_signal_expired(self, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        expired_signal = replace(
            decision.selected_signal,
            valid_until=fixed_as_of() - timedelta(minutes=5),
        )
        expired_decision = replace(decision, selected_signal=expired_signal)
        engine = RiskEngine(default_risk_engine_config(), clock=clock)
        ctx = make_risk_run_context(expired_decision, sizing_hint=make_position_sizing_hint())
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_SIGNAL_EXPIRED
        assert is_signal_expired(expired_signal, reference_time=fixed_as_of())


class TestTradingWindow:
    def test_outside_session(self, clock: FixedClock) -> None:
        early = FixedClock(datetime(2026, 8, 3, 8, 0, 0, tzinfo=IST))
        engine = RiskEngine(default_risk_engine_config(), clock=early)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            sizing_hint=make_position_sizing_hint(),
            reference_time=early(),
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_WINDOW_OUTSIDE_SESSION

    def test_near_close_cutoff(self, clock: FixedClock) -> None:
        late = FixedClock(datetime(2026, 8, 3, 15, 10, 0, tzinfo=IST))
        engine = RiskEngine(default_risk_engine_config(), clock=late)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            sizing_hint=make_position_sizing_hint(),
            reference_time=late(),
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_WINDOW_NEAR_CLOSE


class TestExpiryDay:
    def test_expiry_day_limit(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            sizing_hint=make_position_sizing_hint(proposed_risk_amount=8_000.0, proposed_risk_pct=0.9),
            tags={"expiry_day": "true"},
        )
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code in {
            ERROR_EXPIRY_DAY_LIMIT,
            ERROR_CAPITAL_BUDGET_EXCEEDED,
        }


class TestSerialization:
    def test_json_round_trip(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        result = risk_engine.review(
            make_risk_run_context(decision, sizing_hint=make_position_sizing_hint())
        )
        payload = risk_to_json(result)
        restored = risk_from_json(payload)
        assert restored.verdict is result.verdict
        assert restored.risk_fingerprint == result.risk_fingerprint
        assert restored.trading_signal.signal_id == result.trading_signal.signal_id


class TestFingerprintStability:
    def test_identical_inputs_same_fingerprint(self, clock: FixedClock) -> None:
        frozen = FixedClock(fixed_as_of())
        engine = RiskEngine(default_risk_engine_config(), clock=frozen)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(decision, sizing_hint=make_position_sizing_hint())
        first = engine.review(ctx)
        second = engine.review(ctx)
        assert first.risk_fingerprint == second.risk_fingerprint


class TestThreadSafety:
    def test_concurrent_reviews(self, clock: FixedClock) -> None:
        engine = RiskEngine(default_risk_engine_config(), clock=clock)
        decisions = [build_selected_decision(clock) for _ in range(4)]
        contexts = [
            make_risk_run_context(decision, sizing_hint=make_position_sizing_hint())
            for decision in decisions
        ]

        def _run(ctx: RiskRunContext) -> RiskVerdict:
            return engine.review(ctx).verdict

        with ThreadPoolExecutor(max_workers=4) as pool:
            verdicts = list(pool.map(_run, contexts))
        assert all(verdict is RiskVerdict.APPROVED for verdict in verdicts)


class TestIntegrationPipeline:
    def test_decision_to_risk_gate(self, clock: FixedClock) -> None:
        reg, snap = setup_registry(make_strategy(), clock=clock)
        bundle = evaluate_bundle(reg, snap, clock=clock)
        decision_engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
        decision = decision_engine.decide(make_decision_context(bundle))
        risk_engine = RiskEngine(default_risk_engine_config(), clock=clock)
        if decision.decision_status is DecisionStatus.SELECTED:
            result = risk_engine.review(
                make_risk_run_context(decision, sizing_hint=make_position_sizing_hint())
            )
            assert result.verdict in {RiskVerdict.APPROVED, RiskVerdict.REJECTED}
        else:
            result = risk_engine.review(make_risk_run_context(decision))
            assert result.verdict is RiskVerdict.SKIPPED


class TestExtendedCoverage:
    def test_capital_policy_invalid(self) -> None:
        from risk.risk_engine import CapitalPolicy

        with pytest.raises(RiskEngineConfigurationError):
            CapitalPolicy(near_limit_threshold=1.5)

    def test_custom_profile_tier_raises(self) -> None:
        with pytest.raises(RiskEngineConfigurationError):
            default_user_risk_profile(tier=RiskProfileTier.CUSTOM)

    def test_zero_equity_helpers(self) -> None:
        from risk.risk_engine import PERCENT_MAX

        assert compute_daily_loss_pct(daily_pnl=-1.0, equity=0.0) == PERCENT_MAX
        assert compute_drawdown_pct(equity=0.0, peak_equity=0.0) == PERCENT_MAX

    def test_portfolio_validation_errors(self) -> None:
        from risk.risk_engine import validate_portfolio_snapshot

        bad = make_portfolio_snapshot()
        bad = replace(bad, equity=float("nan"))
        with pytest.raises(RiskEngineContextError):
            validate_portfolio_snapshot(bad)

    def test_profile_validation_errors(self) -> None:
        from risk.risk_engine import validate_user_risk_profile

        profile = make_user_risk_profile(max_risk_per_trade_pct=-1.0)
        with pytest.raises(RiskEngineContextError):
            validate_user_risk_profile(profile)

    def test_sizing_hint_validation_error(self) -> None:
        from risk.risk_engine import validate_position_sizing_hint

        with pytest.raises(RiskEngineContextError):
            validate_position_sizing_hint(make_position_sizing_hint(proposed_risk_amount=-1.0))

    def test_allow_correlation_mismatch_tag(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(decision, tags={"allow_correlation_mismatch": "true"})
        bad = replace(ctx, correlation_id="other-id")
        risk_engine.validate_run_context(bad)

    def test_not_trade_candidate_skip(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        bad = replace(decision, outcome_class=DecisionOutcomeClass.MONITOR_ONLY)
        result = risk_engine.review(make_risk_run_context(bad))
        assert result.skip_reason_code is SkipReasonCode.NOT_TRADE_CANDIDATE

    def test_window_closed_skip(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        bad = replace(decision, decision_status=DecisionStatus.WINDOW_CLOSED)
        result = risk_engine.review(make_risk_run_context(bad))
        assert result.skip_reason_code is SkipReasonCode.WINDOW_CLOSED_DECISION

    def test_manual_invalid_skip(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        bad = replace(decision, decision_status=DecisionStatus.MANUAL_INVALID)
        result = risk_engine.review(make_risk_run_context(bad))
        assert result.skip_reason_code is SkipReasonCode.MANUAL_INVALID_DECISION

    def test_strict_portfolio_fingerprint(self, clock: FixedClock) -> None:
        config = RiskEngineConfig(strict_portfolio_fingerprint=True)
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        portfolio = make_portfolio_snapshot()
        bad_portfolio = replace(portfolio, portfolio_fingerprint="bad-fingerprint")
        ctx = make_risk_run_context(
            decision,
            portfolio=bad_portfolio,
            sizing_hint=make_position_sizing_hint(),
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_very_large_capital_reject(self, clock: FixedClock) -> None:
        from risk.risk_engine import CapitalPolicy

        config = RiskEngineConfig(
            capital_policy=replace(CapitalPolicy(), strict_large_capital_reject=True)
        )
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(decision, sizing_hint=make_position_sizing_hint())
        result = engine.review(ctx)
        assert result.verdict in {RiskVerdict.APPROVED, RiskVerdict.REJECTED}

    def test_exposure_family_limit(self, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        family = decision.selected_signal.strategy_family.value
        exposure = PortfolioExposureSummary(
            gross_notional=1_800_000.0,
            net_notional_by_underlying=MappingProxyType({"NIFTY": 1_800_000.0}),
            gross_notional_by_underlying=MappingProxyType({"NIFTY": 1_800_000.0}),
            exposure_by_family=MappingProxyType({family: 1_800_000.0}),
            open_position_count=1,
            open_position_count_by_underlying=MappingProxyType({"NIFTY": 1}),
        )
        portfolio = replace(
            make_portfolio_snapshot(),
            exposure_summary=exposure,
        )
        portfolio = replace(portfolio, portfolio_fingerprint=portfolio_fingerprint(portfolio))
        engine = RiskEngine(default_risk_engine_config(), clock=clock)
        ctx = make_risk_run_context(
            decision,
            portfolio=portfolio,
            sizing_hint=make_position_sizing_hint(proposed_notional=500_000.0),
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_duplicate_strategy_warning(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        position = PortfolioPosition(
            position_id="pos-dup",
            underlying="NIFTY",
            strategy_id=decision.selected_strategy_id,
            notional_exposure=50_000.0,
            unrealized_pnl=0.0,
            opened_at=fixed_as_of(),
        )
        portfolio = make_portfolio_snapshot(open_positions=(position,))
        ctx = make_risk_run_context(
            decision,
            portfolio=portfolio,
            sizing_hint=make_position_sizing_hint(),
        )
        result = risk_engine.review(ctx)
        assert any(w.code == "RISK.PORTFOLIO.DUPLICATE_STRATEGY" for w in result.warnings)

    def test_profile_floor_violation(self, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        profile = make_user_risk_profile(max_risk_per_trade_pct=0.1)
        engine = RiskEngine(default_risk_engine_config(), clock=clock)
        ctx = make_risk_run_context(
            decision,
            profile=profile,
            sizing_hint=make_position_sizing_hint(proposed_risk_pct=0.08, proposed_risk_amount=800.0),
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_invalid_sizing_hint_values(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            sizing_hint=make_position_sizing_hint(proposed_risk_amount=-100.0, proposed_risk_pct=1.0),
        )
        with pytest.raises(RiskEngineContextError):
            risk_engine.validate_run_context(ctx)

    def test_allowed_families_restriction(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        profile = make_user_risk_profile(allowed_families=frozenset({StrategyFamily.IRON_CONDOR}))
        ctx = make_risk_run_context(
            decision,
            profile=profile,
            sizing_hint=make_position_sizing_hint(),
        )
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_allowed_underlyings_restriction(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        profile = make_user_risk_profile(allowed_underlyings=frozenset({"BANKNIFTY"}))
        ctx = make_risk_run_context(
            decision,
            profile=profile,
            sizing_hint=make_position_sizing_hint(),
        )
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_blackout_window_reject(self, clock: FixedClock) -> None:
        from datetime import time as dt_time

        policy = RiskTradingWindowPolicy(
            blackout_windows=(
                RiskTimeWindow(
                    window_id="test-blackout",
                    start_time=dt_time(10, 0),
                    end_time=dt_time(11, 0),
                ),
            )
        )
        config = RiskEngineConfig(trading_window_policy=policy)
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(decision, sizing_hint=make_position_sizing_hint())
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_analysis_without_required_hint(self, clock: FixedClock) -> None:
        config = RiskEngineConfig(require_sizing_hint_in_live=False)
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            sizing_hint=None,
            execution_mode=StrategyExecutionMode.ANALYSIS,
        )
        result = engine.review(ctx)
        assert result.primary_rejection_code != ERROR_SIZING_HINT_REQUIRED
        if result.verdict is RiskVerdict.APPROVED:
            assert any(w.code == "RISK.SIZING.HEURISTIC_FALLBACK" for w in result.warnings)

    def test_confidence_multiplier(self, clock: FixedClock) -> None:
        config = RiskEngineConfig(apply_confidence_risk_multiplier=True)
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            sizing_hint=make_position_sizing_hint(proposed_risk_amount=5_000.0, proposed_risk_pct=0.5),
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.APPROVED

    def test_serialization_bad_version(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        from risk.risk_engine import RiskEngineValidationError, risk_from_dict, risk_to_dict

        decision = build_selected_decision(clock)
        result = risk_engine.review(
            make_risk_run_context(decision, sizing_hint=make_position_sizing_hint())
        )
        data = risk_to_dict(result)
        data["schema_version"] = "9.9.9"
        with pytest.raises(RiskEngineValidationError):
            risk_from_dict(data)

    def test_serialization_malformed_json(self) -> None:
        from risk.risk_engine import RiskEngineValidationError

        with pytest.raises(RiskEngineValidationError):
            risk_from_json("{not-json")

    def test_evaluate_direct_run_context(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(decision, sizing_hint=make_position_sizing_hint())
        result = risk_engine.evaluate(ctx)
        assert result.status is EngineStatus.SUCCESS

    def test_evaluate_invalid_context_type(self, risk_engine: RiskEngine) -> None:
        with pytest.raises(RiskEngineContextError):
            risk_engine.evaluate("bad")  # type: ignore[arg-type]

    def test_validate_context_wrong_payload(self, risk_engine: RiskEngine) -> None:
        from core.engine_context import EngineContext

        ctx = EngineContext(correlation_id="x", as_of=fixed_as_of(), payload="bad")
        with pytest.raises(RiskEngineContextError):
            risk_engine.validate_context(ctx)

    def test_conservative_profile_defaults(self) -> None:
        profile = default_user_risk_profile(tier=RiskProfileTier.CONSERVATIVE)
        assert profile.max_risk_per_trade_pct == 0.5

    def test_aggressive_profile_defaults(self) -> None:
        profile = default_user_risk_profile(tier=RiskProfileTier.AGGRESSIVE)
        assert profile.max_open_positions == 5

    def test_backtest_daily_loss_disabled(self, clock: FixedClock) -> None:
        engine = RiskEngine(default_risk_engine_config(), clock=clock)
        decision = build_selected_decision(clock)
        portfolio = make_portfolio_snapshot(
            daily_realized_pnl=-100_000.0,
            daily_unrealized_pnl=0.0,
        )
        ctx = make_risk_run_context(
            decision,
            portfolio=portfolio,
            sizing_hint=make_position_sizing_hint(),
            execution_mode=StrategyExecutionMode.BACKTEST,
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.APPROVED

    def test_reject_unknown_margin(self, clock: FixedClock) -> None:
        config = RiskEngineConfig(reject_unknown_margin=True)
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        signal = replace(
            decision.selected_signal,
            risk=replace(
                decision.selected_signal.risk,
                margin_intensity=MarginIntensityHint.UNKNOWN,
            )
            if decision.selected_signal.risk
            else None,
        )
        bad_decision = replace(decision, selected_signal=signal)
        ctx = make_risk_run_context(
            bad_decision,
            sizing_hint=make_position_sizing_hint(),
            available_margin_hint=0.0,
            portfolio=make_portfolio_snapshot(margin_available_hint=0.0),
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_strict_decision_integrity_reject(self, clock: FixedClock) -> None:
        config = RiskEngineConfig(strict_decision_integrity=True)
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        from strategy.signals import SignalAction

        bad_signal = replace(decision.selected_signal, action=SignalAction.ABSTAIN)
        bad_decision = replace(decision, selected_signal=bad_signal)
        ctx = make_risk_run_context(bad_decision, sizing_hint=make_position_sizing_hint())
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_concentration_limit(self, clock: FixedClock) -> None:
        from risk.risk_engine import PortfolioLimitPolicy

        config = RiskEngineConfig(
            portfolio_limit_policy=replace(
                PortfolioLimitPolicy(),
                max_single_underlying_concentration_pct=30.0,
            )
        )
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        exposure = PortfolioExposureSummary(
            gross_notional=200_000.0,
            net_notional_by_underlying=MappingProxyType({"NIFTY": 200_000.0}),
            gross_notional_by_underlying=MappingProxyType({"NIFTY": 200_000.0}),
            exposure_by_family=MappingProxyType({decision.selected_signal.strategy_family.value: 200_000.0}),
            open_position_count=1,
            open_position_count_by_underlying=MappingProxyType({"NIFTY": 1}),
        )
        portfolio = replace(make_portfolio_snapshot(), exposure_summary=exposure)
        portfolio = replace(portfolio, portfolio_fingerprint=portfolio_fingerprint(portfolio))
        ctx = make_risk_run_context(
            decision,
            portfolio=portfolio,
            sizing_hint=make_position_sizing_hint(proposed_notional=500_000.0),
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_missing_context_fields(self) -> None:
        from risk.risk_engine import validate_run_context

        with pytest.raises(RiskEngineContextError):
            validate_run_context(
                RiskRunContext(
                    correlation_id="",
                    as_of=fixed_as_of(),
                    trade_decision=None,  # type: ignore[arg-type]
                    portfolio=make_portfolio_snapshot(),
                    user_risk_profile=make_user_risk_profile(),
                )
            )

    def test_invalid_analysis_multiplier(self) -> None:
        with pytest.raises(RiskEngineConfigurationError):
            RiskEngineConfig(analysis_mode_limit_multiplier=0.0)

    def test_evaluate_risk_engine_error(self, clock: FixedClock) -> None:
        engine = RiskEngine(default_risk_engine_config(), clock=clock)
        ctx = make_risk_run_context(build_selected_decision(clock))
        bad = replace(ctx, correlation_id="")
        result = engine.evaluate(bad)
        assert result.status is EngineStatus.REJECTED

    def test_assert_valid_risk_decision_failure(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        from risk.risk_engine import RiskEngineValidationError

        decision = build_selected_decision(clock)
        result = risk_engine.review(
            make_risk_run_context(decision, sizing_hint=make_position_sizing_hint())
        )
        bad = replace(result, risk_fingerprint="invalid")
        with pytest.raises(RiskEngineValidationError):
            risk_engine.assert_valid_risk_decision(bad)

    def test_near_limit_warnings(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        portfolio = make_portfolio_snapshot(
            daily_realized_pnl=-24_000.0,
            daily_unrealized_pnl=0.0,
            peak_equity=1_000_000.0,
            equity=920_000.0,
        )
        ctx = make_risk_run_context(
            decision,
            portfolio=portfolio,
            sizing_hint=make_position_sizing_hint(proposed_risk_amount=7_500.0, proposed_risk_pct=0.75),
        )
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.APPROVED
        assert result.warnings

    def test_validate_run_context_extended(self) -> None:
        from risk.risk_engine import validate_run_context

        decision = build_selected_decision(FixedClock())
        with pytest.raises(RiskEngineContextError):
            validate_run_context(
                RiskRunContext(
                    correlation_id="x",
                    as_of=fixed_as_of(),
                    trade_decision=decision,
                    portfolio=None,  # type: ignore[arg-type]
                    user_risk_profile=make_user_risk_profile(),
                )
            )
        with pytest.raises(RiskEngineContextError):
            validate_run_context(
                RiskRunContext(
                    correlation_id="x",
                    as_of=fixed_as_of(),
                    trade_decision=decision,
                    portfolio=make_portfolio_snapshot(correlation_id="x"),
                    user_risk_profile=None,  # type: ignore[arg-type]
                )
            )
        ctx = make_risk_run_context(decision)
        with pytest.raises(RiskEngineContextError):
            validate_run_context(replace(ctx, reference_time=datetime(2026, 8, 3, 10, 0, 0)))

    def test_portfolio_negative_positions(self) -> None:
        from risk.risk_engine import validate_portfolio_snapshot

        snapshot = make_portfolio_snapshot()
        bad_exposure = replace(snapshot.exposure_summary, open_position_count=-1)
        bad = replace(snapshot, exposure_summary=bad_exposure)
        with pytest.raises(RiskEngineContextError):
            validate_portfolio_snapshot(bad)

    def test_pipeline_direct_stages(self, clock: FixedClock) -> None:
        from risk.risk_engine import RiskValidationPipeline

        pipeline = RiskValidationPipeline()
        decision = build_selected_decision(clock)
        bad = replace(
            decision,
            decision_status=DecisionStatus.SELECTED,
            outcome_class=DecisionOutcomeClass.MONITOR_ONLY,
        )
        ctx = make_risk_run_context(bad, sizing_hint=make_position_sizing_hint())
        apply_result = pipeline.apply(ctx, config=default_risk_engine_config())
        assert apply_result.pipeline.failed_stage_id is RiskStageId.DECISION_ELIGIBILITY

    def test_sizing_exceeds_pct_budget(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            sizing_hint=make_position_sizing_hint(proposed_risk_amount=5_000.0, proposed_risk_pct=2.5),
        )
        result = risk_engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_expiry_window_cutoff(self, clock: FixedClock) -> None:
        late = FixedClock(datetime(2026, 8, 3, 14, 45, 0, tzinfo=IST))
        engine = RiskEngine(default_risk_engine_config(), clock=late)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            sizing_hint=make_position_sizing_hint(),
            reference_time=late(),
            tags={"expiry_day": "true"},
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_evaluate_unhandled_exception(self, clock: FixedClock, monkeypatch: pytest.MonkeyPatch) -> None:
        engine = RiskEngine(default_risk_engine_config(), clock=clock)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(decision, sizing_hint=make_position_sizing_hint())

        def _boom(_: object) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(engine, "review", _boom)
        result = engine.evaluate(ctx)
        assert result.status is EngineStatus.FAILED

    def test_decision_not_selected_skip(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        bad = replace(decision, decision_status=DecisionStatus.REJECTED)
        result = risk_engine.review(make_risk_run_context(bad))
        assert result.skip_reason_code is SkipReasonCode.DECISION_NOT_SELECTED

    def test_profile_limit_ordering_warning(self, clock: FixedClock) -> None:
        profile = UserRiskProfile(
            profile_id="ordering-test",
            profile_tier=RiskProfileTier.CUSTOM,
            max_risk_per_trade_pct=2.0,
            max_daily_loss_pct=1.0,
            max_drawdown_pct=10.0,
            max_open_positions=3,
            max_consecutive_losses=3,
        )
        engine = RiskEngine(default_risk_engine_config(), clock=clock)
        decision = build_selected_decision(clock)
        ctx = make_risk_run_context(
            decision,
            profile=profile,
            sizing_hint=make_position_sizing_hint(proposed_risk_amount=5_000.0, proposed_risk_pct=0.5),
        )
        result = engine.review(ctx)
        assert any(w.code == "RISK.PROFILE.LIMIT_ORDERING" for w in result.warnings)

    def test_signal_mismatch_warning_non_strict(self, clock: FixedClock) -> None:
        from strategy.signals import SignalAction

        config = RiskEngineConfig(strict_decision_integrity=False)
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        bad_signal = replace(decision.selected_signal, action=SignalAction.ABSTAIN)
        bad_decision = replace(decision, selected_signal=bad_signal)
        ctx = make_risk_run_context(bad_decision, sizing_hint=make_position_sizing_hint())
        result = engine.review(ctx)
        assert any(w.code == "RISK.DECISION.SIGNAL_ACTION_MISMATCH" for w in result.warnings)

    def test_margin_unknown_passed_warning(self, clock: FixedClock) -> None:
        from strategy.signals import RiskProfileHint, SignalRiskMetadata

        config = RiskEngineConfig(reject_unknown_margin=False)
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        risk_meta = SignalRiskMetadata(
            profile=RiskProfileHint.DEFINED,
            margin_intensity=MarginIntensityHint.UNKNOWN,
        )
        bad_signal = replace(decision.selected_signal, risk=risk_meta)
        bad_decision = replace(decision, selected_signal=bad_signal)
        ctx = make_risk_run_context(
            bad_decision,
            sizing_hint=make_position_sizing_hint(),
            available_margin_hint=0.0,
            portfolio=make_portfolio_snapshot(margin_available_hint=0.0),
        )
        result = engine.review(ctx)
        assert any(w.code == "RISK.MARGIN.UNKNOWN_PASSED" for w in result.warnings)

    def test_undefined_risk_rejected(self, clock: FixedClock) -> None:
        from strategy.signals import RiskProfileHint, SignalRiskMetadata

        decision = build_selected_decision(clock)
        signal = decision.selected_signal
        risk_meta = SignalRiskMetadata(profile=RiskProfileHint.UNDEFINED)
        bad_signal = replace(signal, risk=risk_meta)
        bad_decision = replace(decision, selected_signal=bad_signal)
        engine = RiskEngine(default_risk_engine_config(), clock=clock)
        ctx = make_risk_run_context(
            bad_decision,
            profile=make_user_risk_profile(allow_undefined_risk=False),
            sizing_hint=make_position_sizing_hint(),
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED
        assert result.primary_rejection_code == ERROR_STRATEGY_UNDEFINED_RISK

    def test_missing_underlying_analysis_allowed(self, clock: FixedClock) -> None:
        config = RiskEngineConfig(allow_invalid_signal_in_analysis=True)
        engine = RiskEngine(config, clock=clock)
        decision = build_selected_decision(clock)
        bad_market = replace(decision.selected_signal.market, underlying="")
        bad_signal = replace(decision.selected_signal, market=bad_market)
        bad_decision = replace(decision, selected_signal=bad_signal)
        ctx = make_risk_run_context(
            bad_decision,
            sizing_hint=make_position_sizing_hint(),
            execution_mode=StrategyExecutionMode.ANALYSIS,
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.APPROVED

    def test_validate_context_empty_correlation(self) -> None:
        from risk.risk_engine import validate_run_context

        decision = build_selected_decision(FixedClock())
        ctx = make_risk_run_context(decision)
        with pytest.raises(RiskEngineContextError):
            validate_run_context(replace(ctx, correlation_id="  "))

    def test_validate_profile_open_positions(self) -> None:
        from risk.risk_engine import validate_user_risk_profile

        profile = make_user_risk_profile(max_open_positions=0)
        with pytest.raises(RiskEngineContextError):
            validate_user_risk_profile(profile)

    def test_pipeline_not_selected_stage(self, clock: FixedClock) -> None:
        from risk.risk_engine import RiskValidationPipeline

        pipeline = RiskValidationPipeline()
        decision = build_selected_decision(clock)
        bad = replace(decision, decision_status=DecisionStatus.ABSTAIN)
        ctx = make_risk_run_context(bad, sizing_hint=make_position_sizing_hint())
        apply_result = pipeline.apply(ctx, config=default_risk_engine_config())
        assert apply_result.pipeline.stages[0].rejection_code == "RISK.DECISION.NOT_SELECTED"

    def test_generic_skip_reason_message(self, risk_engine: RiskEngine, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        bad = replace(decision, decision_status=DecisionStatus.NO_CANDIDATES)
        result = risk_engine.review(make_risk_run_context(bad))
        assert result.skip_reason_code is SkipReasonCode.DECISION_ABSTAIN
        assert any("RISK.SKIP" in reason.code for reason in result.reasons)

    def test_no_strategy_family_blocked(self, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        bad_signal = replace(decision.selected_signal, strategy_family=StrategyFamily.NO_STRATEGY)
        bad_decision = replace(decision, selected_signal=bad_signal)
        engine = RiskEngine(default_risk_engine_config(), clock=clock)
        ctx = make_risk_run_context(bad_decision, sizing_hint=make_position_sizing_hint())
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_blocked_family_rejected(self, clock: FixedClock) -> None:
        decision = build_selected_decision(clock)
        profile = make_user_risk_profile(blocked_families=frozenset({StrategyFamily.SHORT_STRANGLE}))
        engine = RiskEngine(default_risk_engine_config(), clock=clock)
        ctx = make_risk_run_context(
            decision,
            profile=profile,
            sizing_hint=make_position_sizing_hint(),
        )
        result = engine.review(ctx)
        assert result.verdict is RiskVerdict.REJECTED

    def test_portfolio_naive_as_of(self) -> None:
        from risk.risk_engine import validate_portfolio_snapshot

        snapshot = make_portfolio_snapshot()
        bad = replace(snapshot, as_of=datetime(2026, 8, 3, 10, 0, 0))
        with pytest.raises(RiskEngineContextError):
            validate_portfolio_snapshot(bad)
