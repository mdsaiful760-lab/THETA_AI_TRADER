"""Unit tests for execution.execution_engine."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from core.engine_context import EngineContext
from core.enums import EngineStatus
from decision.trade_decision_engine import (
    TradeDecisionEngine,
    default_trade_decision_engine_config,
)
from execution.execution_engine import (
    ERROR_CONFIG_INVALID,
    ERROR_CONTEXT_CORRELATION_MISMATCH,
    ERROR_CONTEXT_NAIVE_TIMESTAMP,
    ERROR_CONTRACT_MISSING,
    ERROR_RESULT_FINGERPRINT_MISMATCH,
    ERROR_RESULT_INVALID,
    ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
    ERROR_SIGNAL_EXPIRED,
    ERROR_SIZING_HINT_REQUIRED,
    ERROR_SLIPPAGE_PRICE_BAND_EXCEEDED,
    ExecutionEngine,
    ExecutionEngineConfig,
    ExecutionEngineConfigurationError,
    ExecutionEngineContextError,
    ExecutionEngineValidationError,
    ExecutionErrorRecord,
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionPlanSummary,
    ExecutionPipelineResult,
    ExecutionPolicy,
    ExecutionRunContext,
    ExecutionSkipReasonCode,
    ExecutionStageId,
    LegSequenceMode,
    OrderSide,
    OrderType,
    PlannedOrderLeg,
    ProductType,
    RetryPolicy,
    SelectedContractLeg,
    ContractSelectionResult,
    ContractResolutionSource,
    SlippagePolicy,
    generate_idempotency_key,
    plan_fingerprint,
    plan_from_dict,
    plan_from_json,
    plan_to_dict,
    plan_to_json,
    resolve_leg_quantity,
    validate_execution_plan,
    validate_planned_order_leg,
    validate_run_context,
    default_execution_engine_config,
)
from market_data.market_snapshot import OptionType
from risk.risk_engine import (
    PositionSizingHint,
    RiskEngine,
    RiskVerdict,
    SkipReasonCode,
    default_risk_engine_config,
)
from strategy.signals import (
    SignalAction,
    StrategyExecutionMode,
    StrategyFamily,
    StructureHint,
    TradingSignal,
)
from tests.test_market_snapshot import full_nifty_snapshot, minimal_valid_snapshot
from tests.test_risk_engine import (
    build_selected_decision,
    make_portfolio_snapshot,
    make_risk_run_context,
    make_user_risk_profile,
)
from tests.test_strategy_evaluation_engine import FixedClock, make_strategy, setup_registry
from tests.test_trade_decision_engine import evaluate_bundle, make_decision_context

IST = ZoneInfo("Asia/Kolkata")


def fixed_as_of() -> datetime:
    """Monday during regular NSE session."""
    return datetime(2026, 8, 3, 10, 15, 0, tzinfo=IST)


def make_execution_sizing_hint(
    *,
    proposed_units_hint: float = 2.0,
    proposed_risk_amount: float = 8_000.0,
    proposed_risk_pct: float = 0.8,
    metadata: dict[str, str] | None = None,
) -> PositionSizingHint:
    """Build sizing hint with units for execution planning."""
    return PositionSizingHint(
        hint_id="hint-exec-001",
        proposed_risk_amount=proposed_risk_amount,
        proposed_risk_pct=proposed_risk_pct,
        proposed_units_hint=proposed_units_hint,
        sizing_method="test_execution_v1",
        metadata=MappingProxyType(metadata or {}),
    )


def make_contract_selection(
    *,
    correlation_id: str = "corr-eval-001",
    leg_count: int = 2,
    underlying: str = "NIFTY",
) -> ContractSelectionResult:
    """Build contract selection for short strangle or iron condor."""
    if leg_count == 2:
        legs = (
            SelectedContractLeg(
                leg_index=0,
                instrument_key="NFO:NIFTY2680724300CE",
                strike=24300.0,
                option_type=OptionType.CE,
                exchange="NFO",
                lot_size=75,
            ),
            SelectedContractLeg(
                leg_index=1,
                instrument_key="NFO:NIFTY2680724300PE",
                strike=24300.0,
                option_type=OptionType.PE,
                exchange="NFO",
                lot_size=75,
            ),
        )
    elif leg_count == 4:
        snap = full_nifty_snapshot()
        targets = (
            (24150.0, OptionType.PE),
            (24100.0, OptionType.PE),
            (24450.0, OptionType.CE),
            (24500.0, OptionType.CE),
        )
        legs_list: list[SelectedContractLeg] = []
        for leg_index, (strike, option_type) in enumerate(targets):
            contract = next(
                (
                    item
                    for item in snap.option_chain.contracts
                    if item.strike == strike and item.option_type is option_type
                ),
                None,
            )
            assert contract is not None, f"Missing contract for strike={strike} {option_type}"
            legs_list.append(
                SelectedContractLeg(
                    leg_index=leg_index,
                    instrument_key=f"{contract.exchange}:{contract.tradingsymbol}",
                    strike=strike,
                    option_type=option_type,
                    exchange=contract.exchange,
                    lot_size=contract.lot_size,
                )
            )
        legs = tuple(legs_list)
    else:
        raise ValueError(f"Unsupported leg_count: {leg_count}")

    payload = f"{correlation_id}|{underlying}|{leg_count}"
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return ContractSelectionResult(
        selection_id=f"sel-{digest}",
        correlation_id=correlation_id,
        underlying=underlying,
        expiry=date(2026, 8, 7),
        legs=legs,
        selection_fingerprint=digest,
    )


def build_approved_risk(
    clock: FixedClock,
    *,
    sizing_hint: PositionSizingHint | None = None,
    snapshot: object | None = None,
) -> tuple[object, object]:
    """Build APPROVED risk decision via upstream engines."""
    snap = snapshot or minimal_valid_snapshot()
    reg, reg_snap = setup_registry(make_strategy(), clock=clock)
    bundle = evaluate_bundle(reg, reg_snap, clock=clock)
    decision_engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
    decision = decision_engine.decide(make_decision_context(bundle))
    risk_engine = RiskEngine(default_risk_engine_config(), clock=clock)
    hint = sizing_hint or make_execution_sizing_hint()
    ctx = make_risk_run_context(decision, sizing_hint=hint)
    result = risk_engine.review(ctx)
    assert result.verdict is RiskVerdict.APPROVED
    return result, snap


def build_skipped_risk(clock: FixedClock) -> object:
    """Build SKIPPED risk decision from abstain path."""
    reg, reg_snap = setup_registry(make_strategy(enabled=False), clock=clock)
    bundle = evaluate_bundle(reg, reg_snap, clock=clock)
    decision_engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
    decision = decision_engine.decide(make_decision_context(bundle))
    risk_engine = RiskEngine(default_risk_engine_config(), clock=clock)
    return risk_engine.review(make_risk_run_context(decision))


def build_rejected_risk(clock: FixedClock) -> object:
    """Build REJECTED risk decision from kill switch."""
    config = default_risk_engine_config()
    config = replace(config, kill_switch_active=True, kill_switch_reason="test halt")
    reg, reg_snap = setup_registry(make_strategy(), clock=clock)
    bundle = evaluate_bundle(reg, reg_snap, clock=clock)
    decision_engine = TradeDecisionEngine(default_trade_decision_engine_config(), clock=clock)
    decision = decision_engine.decide(make_decision_context(bundle))
    risk_engine = RiskEngine(config, clock=clock)
    result = risk_engine.review(
        make_risk_run_context(decision, sizing_hint=make_execution_sizing_hint())
    )
    assert result.verdict is RiskVerdict.REJECTED
    return result


def make_execution_run_context(
    risk_decision: object,
    market_snapshot: object,
    *,
    sizing_hint: PositionSizingHint | None = None,
    contract_selection: ContractSelectionResult | None = None,
    force_skip: bool = False,
    execution_mode: StrategyExecutionMode | None = None,
    reference_time: datetime | None = None,
    tags: dict[str, str] | None = None,
) -> ExecutionRunContext:
    """Build valid execution run context."""
    return ExecutionRunContext(
        correlation_id=risk_decision.correlation_id,
        as_of=fixed_as_of(),
        risk_decision=risk_decision,
        market_snapshot=market_snapshot,
        position_sizing_hint=sizing_hint,
        contract_selection=contract_selection,
        execution_mode=execution_mode,
        reference_time=reference_time or fixed_as_of(),
        force_skip=force_skip,
        tags=MappingProxyType(tags or {}),
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def exec_engine(clock: FixedClock) -> ExecutionEngine:
    return ExecutionEngine(default_execution_engine_config(), clock=clock)


class TestConfiguration:
    def test_invalid_default_quantity_fallback(self) -> None:
        with pytest.raises(ExecutionEngineConfigurationError) as exc:
            ExecutionEngineConfig(default_quantity_fallback=0)
        assert exc.value.code == ERROR_CONFIG_INVALID

    def test_empty_live_allowed_types(self) -> None:
        from execution.execution_engine import OrderTypePolicy

        policy = OrderTypePolicy(live_allowed_types=frozenset())
        with pytest.raises(ExecutionEngineConfigurationError):
            ExecutionEngineConfig(order_type_policy=policy)

    def test_default_config_factory(self) -> None:
        config = default_execution_engine_config()
        assert config.require_contract_selection_in_live is True
        assert config.execution_policy.default_order_type is OrderType.LIMIT


class TestHelpers:
    def test_generate_idempotency_key_stable(self) -> None:
        key_a = generate_idempotency_key("corr-1", "plan-abc", 0)
        key_b = generate_idempotency_key("corr-1", "plan-abc", 0)
        assert key_a == key_b
        assert key_a.startswith("exec-")

    def test_resolve_leg_quantity_split(self) -> None:
        config = ExecutionEngineConfig(split_quantity_equally_across_legs=True)
        hint = make_execution_sizing_hint(proposed_units_hint=5.0)
        mode = StrategyExecutionMode.LIVE
        q0 = resolve_leg_quantity(0, 4, hint, config=config, execution_mode=mode)
        q1 = resolve_leg_quantity(1, 4, hint, config=config, execution_mode=mode)
        assert q0 + q1 + resolve_leg_quantity(2, 4, hint, config=config, execution_mode=mode) + resolve_leg_quantity(3, 4, hint, config=config, execution_mode=mode) == 5

    def test_resolve_leg_quantity_per_leg_metadata(self) -> None:
        config = default_execution_engine_config()
        hint = make_execution_sizing_hint(
            proposed_units_hint=2.0,
            metadata={"leg_1_quantity": "3"},
        )
        assert resolve_leg_quantity(1, 2, hint, config=config, execution_mode=StrategyExecutionMode.LIVE) == 3

    def test_resolve_leg_quantity_fallback_analysis(self) -> None:
        config = default_execution_engine_config()
        qty = resolve_leg_quantity(0, 1, None, config=config, execution_mode=StrategyExecutionMode.ANALYSIS)
        assert qty == config.default_quantity_fallback

    def test_validate_planned_order_leg_limit_requires_price(self) -> None:
        leg = PlannedOrderLeg(
            leg_index=0,
            sequence_group=0,
            instrument_key="NFO:TEST",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product=ProductType.NRML,
            quantity=1,
            idempotency_key="exec-test",
            resolution_source=ContractResolutionSource.CONTRACT_SELECTION,
        )
        result = validate_planned_order_leg(leg)
        assert not result.is_valid


class TestContextValidation:
    def test_naive_as_of(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        ctx = make_execution_run_context(risk, snap)
        bad = replace(ctx, as_of=datetime(2026, 8, 3, 10, 0, 0))
        with pytest.raises(ExecutionEngineContextError) as exc:
            exec_engine.validate_run_context(bad)
        assert exc.value.code == ERROR_CONTEXT_NAIVE_TIMESTAMP

    def test_correlation_mismatch(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        ctx = make_execution_run_context(risk, snap)
        bad = replace(ctx, correlation_id="other-id")
        with pytest.raises(ExecutionEngineContextError) as exc:
            exec_engine.validate_run_context(bad)
        assert exc.value.code == ERROR_CONTEXT_CORRELATION_MISMATCH

    def test_invalid_engine_context_payload(self, exec_engine: ExecutionEngine) -> None:
        ctx = EngineContext(correlation_id="x", as_of=fixed_as_of(), payload="bad")
        result = exec_engine.plan(ctx)
        assert result.status is EngineStatus.REJECTED


class TestSkipPaths:
    def test_risk_skipped(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk = build_skipped_risk(clock)
        snap = minimal_valid_snapshot()
        ctx = make_execution_run_context(risk, snap)
        plan = exec_engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.SKIPPED
        assert plan.skip_reason_code is ExecutionSkipReasonCode.RISK_SKIPPED
        assert plan.legs == ()

    def test_risk_rejected_no_plan(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk = build_rejected_risk(clock)
        snap = minimal_valid_snapshot()
        ctx = make_execution_run_context(risk, snap)
        plan = exec_engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.NO_PLAN
        assert plan.skip_reason_code is ExecutionSkipReasonCode.RISK_REJECTED
        assert plan.legs == ()

    def test_force_skip(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        ctx = make_execution_run_context(risk, snap, force_skip=True)
        plan = exec_engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.SKIPPED
        assert plan.skip_reason_code is ExecutionSkipReasonCode.ORCHESTRATOR_SKIP

    def test_analysis_mode_skip(self, clock: FixedClock) -> None:
        config = ExecutionEngineConfig(skip_planning_in_analysis=True)
        engine = ExecutionEngine(config, clock=clock)
        risk, snap = build_approved_risk(clock)
        ctx = make_execution_run_context(
            risk,
            snap,
            execution_mode=StrategyExecutionMode.ANALYSIS,
        )
        plan = engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.SKIPPED
        assert plan.skip_reason_code is ExecutionSkipReasonCode.ANALYSIS_MODE_SKIP

    def test_engine_result_success_on_skip(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk = build_skipped_risk(clock)
        ctx = make_execution_run_context(risk, minimal_valid_snapshot())
        engine_ctx = EngineContext(
            correlation_id=ctx.correlation_id,
            as_of=ctx.as_of,
            payload=ctx,
        )
        result = exec_engine.plan(engine_ctx)
        assert result.status is EngineStatus.SUCCESS
        assert result.payload.status is ExecutionPlanStatus.SKIPPED


class TestApprovalHappyPath:
    def test_ready_two_leg_plan(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(proposed_units_hint=2.0),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.READY
        assert len(plan.legs) == 2
        assert all(leg.quantity > 0 for leg in plan.legs)
        assert all(leg.order_type is OrderType.LIMIT for leg in plan.legs)
        assert all(leg.limit_price_hint is not None for leg in plan.legs)
        assert plan.plan_fingerprint == plan_fingerprint(plan)
        assert plan.pipeline_summary.total_stages == 12
        assert plan.pipeline_summary.failed_stage_id is None

    def test_iron_condor_four_leg(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, _ = build_approved_risk(clock)
        snap = full_nifty_snapshot()
        selection = make_contract_selection(correlation_id=risk.correlation_id, leg_count=4)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(proposed_units_hint=4.0),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.READY
        assert len(plan.legs) == 4
        assert plan.summary.leg_count == 4
        assert plan.sequences[0].mode is LegSequenceMode.SIMULTANEOUS
        indices = {leg.leg_index for leg in plan.legs}
        assert indices == {0, 1, 2, 3}

    def test_hedged_first_sequencing(self, clock: FixedClock) -> None:
        config = default_execution_engine_config()
        policy = replace(
            config.execution_policy,
            sequencing_mode=LegSequenceMode.HEDGED_FIRST,
        )
        engine = ExecutionEngine(replace(config, execution_policy=policy), clock=clock)
        risk, _ = build_approved_risk(clock)
        snap = full_nifty_snapshot()
        selection = make_contract_selection(correlation_id=risk.correlation_id, leg_count=4)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(proposed_units_hint=4.0),
            contract_selection=selection,
        )
        plan = engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.READY
        assert len(plan.sequences) >= 1

    def test_fingerprint_stability(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan_a = exec_engine.plan_from_run_context(ctx)
        plan_b = exec_engine.plan_from_run_context(ctx)
        assert plan_a.plan_fingerprint == plan_b.plan_fingerprint
        assert plan_a.legs[0].idempotency_key == plan_b.legs[0].idempotency_key


class TestRejections:
    def test_contract_missing_live(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=None,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.REJECTED
        assert plan.primary_rejection_code == ERROR_CONTRACT_MISSING
        assert plan.legs == ()

    def test_sizing_hint_required_live(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(risk, snap, contract_selection=selection, sizing_hint=None)
        plan = exec_engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.REJECTED
        assert plan.primary_rejection_code == ERROR_SIZING_HINT_REQUIRED

    def test_signal_expired(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        ref = fixed_as_of()
        expired_signal = replace(
            risk.trading_signal,
            as_of=ref - timedelta(hours=1),
            valid_until=ref - timedelta(minutes=1),
        )
        risk = replace(risk, trading_signal=expired_signal)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
            reference_time=fixed_as_of(),
        )
        plan = exec_engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.REJECTED
        assert plan.primary_rejection_code == ERROR_SIGNAL_EXPIRED

    def test_abstain_action_on_approved_path(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        bad_signal = replace(risk.trading_signal, action=SignalAction.ABSTAIN)
        risk = replace(risk, trading_signal=bad_signal)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.REJECTED


class TestStructureHeuristic:
    def test_analysis_heuristic_ready(self, clock: FixedClock) -> None:
        config = ExecutionEngineConfig(
            require_contract_selection_in_live=True,
            allow_structure_hint_heuristics=True,
            require_sizing_hint_in_live=False,
        )
        engine = ExecutionEngine(config, clock=clock)
        risk, _ = build_approved_risk(clock)
        snap = full_nifty_snapshot()
        structure = StructureHint(
            structure_type="STRANGLE",
            leg_count=2,
            target_delta=0.16,
            strikes_each_side=2,
            option_types=(OptionType.CE, OptionType.PE),
        )
        signal = replace(risk.trading_signal, structure_hint=structure)
        risk = replace(risk, trading_signal=signal)
        ctx = make_execution_run_context(
            risk,
            snap,
            execution_mode=StrategyExecutionMode.ANALYSIS,
            sizing_hint=make_execution_sizing_hint(proposed_units_hint=2.0),
        )
        plan = engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.READY
        assert len(plan.legs) == 2
        assert any(
            leg.resolution_source is ContractResolutionSource.STRUCTURE_HINT_HEURISTIC
            for leg in plan.legs
        )


class TestSlippageAndPolicy:
    def test_market_downgraded_to_limit(self, clock: FixedClock) -> None:
        config = default_execution_engine_config()
        policy = replace(
            config.execution_policy,
            default_order_type=OrderType.MARKET,
            allow_market_orders_live=False,
        )
        engine = ExecutionEngine(replace(config, execution_policy=policy), clock=clock)
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.READY
        assert all(leg.order_type is OrderType.LIMIT for leg in plan.legs)

    def test_price_band_rejection(self, clock: FixedClock) -> None:
        config = default_execution_engine_config()
        slippage = replace(config.default_slippage_policy, price_band_pct=0.0001)
        engine = ExecutionEngine(replace(config, default_slippage_policy=slippage), clock=clock)
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.REJECTED
        assert plan.primary_rejection_code == ERROR_SLIPPAGE_PRICE_BAND_EXCEEDED


class TestSerialization:
    def test_json_round_trip(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        original = exec_engine.plan_from_run_context(ctx)
        payload = plan_to_json(original)
        restored = plan_from_json(payload)
        assert restored.plan_id == original.plan_id
        assert restored.status == original.status
        assert len(restored.legs) == len(original.legs)
        assert restored.plan_fingerprint == original.plan_fingerprint

    def test_dict_round_trip(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        original = exec_engine.plan_from_run_context(ctx)
        restored = plan_from_dict(plan_to_dict(original))
        assert restored.correlation_id == original.correlation_id

    def test_unsupported_schema_version(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        data = plan_to_dict(plan)
        data["schema_version"] = "9.9.9"
        with pytest.raises(ExecutionEngineValidationError) as exc:
            plan_from_dict(data)
        assert exc.value.code == ERROR_SERIALIZATION_UNSUPPORTED_VERSION


class TestPostPlanValidation:
    def test_fingerprint_mismatch_detected(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        tampered = replace(plan, plan_fingerprint="bad-fingerprint")
        validation = validate_execution_plan(tampered)
        assert not validation.is_valid
        assert any(error.code == ERROR_RESULT_FINGERPRINT_MISMATCH for error in validation.errors)

    def test_valid_ready_plan_passes(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        validation = validate_execution_plan(plan)
        assert validation.is_valid


class TestTimeoutAndRetry:
    def test_valid_until_computed(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        assert plan.valid_until is not None
        assert plan.valid_until > plan.planned_at

    def test_retry_policy_on_ready(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        assert plan.retry_policy.max_attempts >= 1


class TestThreadSafety:
    def test_concurrent_planning(self, clock: FixedClock) -> None:
        engine = ExecutionEngine(default_execution_engine_config(), clock=clock)
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )

        def _plan() -> str:
            plan = engine.plan_from_run_context(ctx)
            return plan.plan_fingerprint

        with ThreadPoolExecutor(max_workers=8) as pool:
            fingerprints = list(pool.map(lambda _: _plan(), range(16)))

        assert len(set(fingerprints)) == 1

    def test_concurrent_fingerprint(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        results: list[str] = []
        lock = threading.Lock()

        def _compute() -> None:
            fp = plan_fingerprint(plan)
            with lock:
                results.append(fp)

        threads = [threading.Thread(target=_compute) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(set(results)) == 1


class TestInlineTags:
    def test_inline_instrument_keys(self, clock: FixedClock) -> None:
        config = ExecutionEngineConfig(
            require_contract_selection_in_live=False,
            require_sizing_hint_in_live=False,
        )
        engine = ExecutionEngine(config, clock=clock)
        risk, snap = build_approved_risk(clock)
        structure = StructureHint(structure_type="STRANGLE", leg_count=2)
        signal = replace(risk.trading_signal, structure_hint=structure)
        risk = replace(risk, trading_signal=signal)
        tags = {
            "leg_0_instrument_key": "NFO:NIFTY2680724300CE",
            "leg_1_instrument_key": "NFO:NIFTY2680724300PE",
        }
        ctx = make_execution_run_context(
            risk,
            snap,
            execution_mode=StrategyExecutionMode.ANALYSIS,
            sizing_hint=make_execution_sizing_hint(proposed_units_hint=2.0),
            tags=tags,
        )
        plan = engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.READY
        assert len(plan.legs) == 2


class TestPipelineStages:
    def test_all_stage_ids_present_on_ready(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        stage_ids = {stage.stage_id for stage in plan.pipeline_summary.stages}
        assert stage_ids == set(ExecutionStageId)


class TestBacktestMode:
    def test_backtest_extended_validity(self, clock: FixedClock) -> None:
        config = ExecutionEngineConfig(backtest_plan_validity_seconds=7200)
        engine = ExecutionEngine(config, clock=clock)
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            execution_mode=StrategyExecutionMode.BACKTEST,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.READY
        assert plan.valid_until is not None
        delta = plan.valid_until - plan.planned_at
        assert delta.total_seconds() >= 7200 - 1


class TestPolicyValidation:
    def test_retry_policy_invalid_attempts(self) -> None:
        with pytest.raises(ExecutionEngineConfigurationError):
            RetryPolicy(max_attempts=0)

    def test_retry_policy_invalid_backoff(self) -> None:
        with pytest.raises(ExecutionEngineConfigurationError):
            RetryPolicy(initial_backoff_ms=-1)

    def test_retry_policy_invalid_multiplier(self) -> None:
        with pytest.raises(ExecutionEngineConfigurationError):
            RetryPolicy(backoff_multiplier=0.5)

    def test_timeout_policy_invalid(self) -> None:
        from execution.execution_engine import TimeoutPolicy

        with pytest.raises(ExecutionEngineConfigurationError):
            TimeoutPolicy(plan_validity_seconds=0)

    def test_slippage_policy_invalid(self) -> None:
        with pytest.raises(ExecutionEngineConfigurationError):
            SlippagePolicy(max_slippage_bps=-1.0)


class TestExtendedContextValidation:
    def test_empty_correlation_id(self, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        ctx = make_execution_run_context(risk, snap)
        bad = replace(ctx, correlation_id="  ")
        with pytest.raises(ExecutionEngineContextError):
            validate_run_context(bad, config=default_execution_engine_config())

    def test_contract_selection_correlation_mismatch(self, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = replace(make_contract_selection(), correlation_id="other")
        ctx = make_execution_run_context(risk, snap, contract_selection=selection)
        with pytest.raises(ExecutionEngineContextError):
            validate_run_context(ctx, config=default_execution_engine_config())

    def test_naive_reference_time(self, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        ctx = make_execution_run_context(risk, snap, reference_time=datetime(2026, 8, 3, 10, 0, 0))
        with pytest.raises(ExecutionEngineContextError):
            validate_run_context(ctx, config=default_execution_engine_config())

    def test_empty_risk_fingerprint(self, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        bad_risk = replace(risk, risk_fingerprint="")
        ctx = make_execution_run_context(bad_risk, snap)
        with pytest.raises(ExecutionEngineContextError):
            validate_run_context(ctx, config=default_execution_engine_config())

    def test_validate_context_type(self, exec_engine: ExecutionEngine) -> None:
        ctx = EngineContext(correlation_id="c", as_of=fixed_as_of(), payload=object())
        with pytest.raises(ExecutionEngineContextError):
            exec_engine.validate_context(ctx)


class TestLegValidation:
    def test_invalid_quantity(self) -> None:
        leg = PlannedOrderLeg(
            leg_index=0,
            sequence_group=0,
            instrument_key="NFO:TEST",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            product=ProductType.NRML,
            quantity=0,
            idempotency_key="exec-1",
            resolution_source=ContractResolutionSource.CONTRACT_SELECTION,
            limit_price_hint=10.0,
        )
        assert not validate_planned_order_leg(leg).is_valid

    def test_duplicate_leg_index_in_plan_validation(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        dup_leg = replace(plan.legs[0], idempotency_key=plan.legs[1].idempotency_key)
        tampered = replace(plan, legs=(dup_leg, plan.legs[1]))
        validation = validate_execution_plan(tampered)
        assert not validation.is_valid

    def test_assert_valid_raises(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        from execution.execution_engine import assert_valid_execution_plan

        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        assert_valid_execution_plan(plan)
        with pytest.raises(ExecutionEngineValidationError):
            assert_valid_execution_plan(replace(plan, plan_fingerprint="bad"))


class TestContractErrors:
    def test_contract_leg_count_mismatch(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id, leg_count=2)
        bad_selection = ContractSelectionResult(
            selection_id=selection.selection_id,
            correlation_id=selection.correlation_id,
            underlying=selection.underlying,
            expiry=selection.expiry,
            legs=selection.legs[:1],
            selection_fingerprint=selection.selection_fingerprint,
        )
        structure = StructureHint(structure_type="STRANGLE", leg_count=2)
        signal = replace(risk.trading_signal, structure_hint=structure)
        risk = replace(risk, trading_signal=signal)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=bad_selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.REJECTED

    def test_resolve_contracts_direct_mismatch(self, clock: FixedClock) -> None:
        from execution.execution_engine import ExecutionPlanningError, resolve_contracts

        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id, leg_count=2)
        structure = StructureHint(structure_type="STRANGLE", leg_count=2)
        signal = replace(risk.trading_signal, structure_hint=structure)
        with pytest.raises(ExecutionPlanningError):
            resolve_contracts(
                signal,
                replace(selection, legs=selection.legs[:1]),
                snap,
                config=default_execution_engine_config(),
                execution_mode=StrategyExecutionMode.LIVE,
                run_context=make_execution_run_context(risk, snap, contract_selection=selection),
                leg_count=1,
            )

    def test_missing_proposed_units_raises(self) -> None:
        from execution.execution_engine import ExecutionPlanningError

        hint = PositionSizingHint(
            hint_id="h1",
            proposed_risk_amount=100.0,
            proposed_risk_pct=1.0,
            sizing_method="x",
            proposed_units_hint=None,
        )
        with pytest.raises(ExecutionPlanningError):
            resolve_leg_quantity(0, 1, hint, config=default_execution_engine_config(), execution_mode=StrategyExecutionMode.LIVE)


class TestStrategyFamilies:
    def test_sequential_sequencing(self, clock: FixedClock) -> None:
        config = default_execution_engine_config()
        policy = replace(config.execution_policy, sequencing_mode=LegSequenceMode.SEQUENTIAL)
        engine = ExecutionEngine(replace(config, execution_policy=policy), clock=clock)
        risk, snap = build_approved_risk(clock)
        structure = StructureHint(structure_type="IRON_CONDOR", leg_count=2)
        signal = replace(
            risk.trading_signal,
            strategy_family=StrategyFamily.IRON_CONDOR,
            structure_hint=structure,
        )
        risk = replace(risk, trading_signal=signal)
        selection = make_contract_selection(correlation_id=risk.correlation_id, leg_count=2)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(proposed_units_hint=2.0),
            contract_selection=selection,
        )
        plan = engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.READY
        assert plan.sequences[0].mode is LegSequenceMode.SEQUENTIAL

    def test_intraday_product_policy(self, clock: FixedClock) -> None:
        from execution.execution_engine import ProductTypePolicy

        config = ExecutionEngineConfig(
            product_type_policy=ProductTypePolicy(
                default_product=ProductType.NRML,
                intraday_only_strategies=frozenset({"short_strangle"}),
            )
        )
        engine = ExecutionEngine(config, clock=clock)
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.READY
        assert all(leg.product is ProductType.MIS for leg in plan.legs)

    def test_evaluate_alias(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        engine_ctx = EngineContext(correlation_id=ctx.correlation_id, as_of=ctx.as_of, payload=ctx)
        assert exec_engine.evaluate(engine_ctx).status is EngineStatus.SUCCESS


class TestSerializationEdgeCases:
    def test_malformed_json(self) -> None:
        with pytest.raises(ExecutionEngineValidationError):
            plan_from_json("{not json")

    def test_json_root_not_object(self) -> None:
        with pytest.raises(ExecutionEngineValidationError):
            plan_from_json("[1, 2, 3]")

    def test_leg_round_trip(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        from execution.execution_engine import leg_from_dict, leg_to_dict

        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        restored = leg_from_dict(leg_to_dict(plan.legs[0]))
        assert restored.instrument_key == plan.legs[0].instrument_key


class TestPlanValidationEdgeCases:
    def test_rejected_without_code_invalid(self, clock: FixedClock) -> None:
        risk, _ = build_approved_risk(clock)
        plan = ExecutionPlan(
            plan_id="p1",
            correlation_id="c1",
            risk_id="r1",
            decision_fingerprint="d1",
            risk_fingerprint="rf1",
            signal_fingerprint="sf1",
            snapshot_id="s1",
            status=ExecutionPlanStatus.REJECTED,
            trading_signal=risk.trading_signal,
            execution_mode=StrategyExecutionMode.LIVE,
            legs=(),
            sequences=(),
            retry_policy=RetryPolicy(),
            timeout_policy=default_execution_engine_config().default_timeout_policy,
            slippage_policy=SlippagePolicy(),
            execution_policy=ExecutionPolicy(),
            summary=ExecutionPlanSummary(
                strategy_id="x",
                strategy_family=StrategyFamily.SHORT_STRANGLE,
                underlying="NIFTY",
                leg_count=0,
                total_quantity=0,
                sequence_mode=LegSequenceMode.SIMULTANEOUS,
                primary_order_type=OrderType.LIMIT,
            ),
            reasons=(),
            factors=(),
            pipeline_summary=ExecutionPipelineResult(0, 0, None, (), False),
            planned_at=fixed_as_of(),
            duration_ms=1.0,
            plan_fingerprint="fp",
            warnings=(),
            errors=(),
        )
        assert not validate_execution_plan(plan).is_valid

    def test_skip_with_legs_invalid(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        ready = exec_engine.plan_from_run_context(ctx)
        bad = replace(
            ready,
            status=ExecutionPlanStatus.SKIPPED,
            skip_reason_code=ExecutionSkipReasonCode.ORCHESTRATOR_SKIP,
        )
        assert not validate_execution_plan(bad).is_valid


class TestAnalysisInvalidSignal:
    def test_allow_invalid_signal_in_analysis(self, clock: FixedClock) -> None:
        config = ExecutionEngineConfig(
            allow_invalid_signal_in_analysis=True,
            require_contract_selection_in_live=False,
            require_sizing_hint_in_live=False,
        )
        engine = ExecutionEngine(config, clock=clock)
        risk, snap = build_approved_risk(clock)
        bad_signal = replace(risk.trading_signal, signal_id="")
        risk = replace(risk, trading_signal=bad_signal)
        structure = StructureHint(structure_type="STRANGLE", leg_count=2, strikes_each_side=0)
        signal = replace(risk.trading_signal, structure_hint=structure)
        risk = replace(risk, trading_signal=signal)
        ctx = make_execution_run_context(
            risk,
            snap,
            execution_mode=StrategyExecutionMode.ANALYSIS,
            sizing_hint=make_execution_sizing_hint(proposed_units_hint=2.0),
        )
        plan = engine.plan_from_run_context(ctx)
        assert plan.status in {ExecutionPlanStatus.READY, ExecutionPlanStatus.REJECTED}


class TestDirectHelpers:
    def test_validate_price_band(self) -> None:
        from execution.execution_engine import validate_price_band

        assert validate_price_band(100.0, 100.0, 0.02) is True
        assert validate_price_band(200.0, 100.0, 0.02) is False

    def test_compute_valid_until_with_signal_expiry(self, clock: FixedClock) -> None:
        from execution.execution_engine import TimeoutPolicy, compute_valid_until

        risk, _ = build_approved_risk(clock)
        signal = replace(risk.trading_signal, valid_until=fixed_as_of() + timedelta(seconds=30))
        expiry = compute_valid_until(fixed_as_of(), signal, TimeoutPolicy(plan_validity_seconds=120))
        assert expiry == signal.valid_until

    def test_leg_side_iron_condor(self) -> None:
        from execution.execution_engine import OrderSide, _resolve_leg_side

        signal = build_approved_risk(FixedClock())[0].trading_signal
        signal = replace(
            signal,
            strategy_family=StrategyFamily.IRON_CONDOR,
            structure_hint=StructureHint(structure_type="IRON_CONDOR", leg_count=4),
        )
        assert _resolve_leg_side(0, signal) is OrderSide.SELL
        assert _resolve_leg_side(1, signal) is OrderSide.BUY

    def test_leg_side_bull_put_spread(self) -> None:
        from execution.execution_engine import OrderSide, _resolve_leg_side

        signal = build_approved_risk(FixedClock())[0].trading_signal
        signal = replace(signal, strategy_family=StrategyFamily.BULL_PUT_SPREAD)
        assert _resolve_leg_side(0, signal) is OrderSide.SELL
        assert _resolve_leg_side(1, signal) is OrderSide.BUY

    def test_build_sequences_hedged_first(self) -> None:
        from execution.execution_engine import build_sequences

        risk, snap = build_approved_risk(FixedClock())
        selection = make_contract_selection(correlation_id=risk.correlation_id, leg_count=4)
        snap = full_nifty_snapshot()
        engine = ExecutionEngine(default_execution_engine_config(), clock=FixedClock())
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(proposed_units_hint=4.0),
            contract_selection=selection,
        )
        plan = engine.plan_from_run_context(ctx)
        config = replace(
            default_execution_engine_config().execution_policy,
            sequencing_mode=LegSequenceMode.HEDGED_FIRST,
        )
        sequences = build_sequences(
            plan.legs,
            replace(risk.trading_signal, strategy_family=StrategyFamily.IRON_CONDOR),
            config=replace(default_execution_engine_config(), execution_policy=config),
        )
        assert len(sequences) >= 1

    def test_overnight_product_policy(self, clock: FixedClock) -> None:
        from execution.execution_engine import ProductTypePolicy

        config = ExecutionEngineConfig(
            product_type_policy=ProductTypePolicy(
                default_product=ProductType.MIS,
                overnight_strategies=frozenset({"short_strangle"}),
            )
        )
        engine = ExecutionEngine(config, clock=clock)
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = engine.plan_from_run_context(ctx)
        assert all(leg.product is ProductType.NRML for leg in plan.legs)

    def test_engine_rejects_invalid_context(self, clock: FixedClock) -> None:
        engine = ExecutionEngine(default_execution_engine_config(), clock=clock)
        risk, snap = build_approved_risk(clock)
        ctx = make_execution_run_context(risk, snap, sizing_hint=make_execution_sizing_hint())
        bad = replace(ctx, correlation_id="mismatch-id")
        engine_ctx = EngineContext(correlation_id=bad.correlation_id, as_of=bad.as_of, payload=bad)
        result = engine.plan(engine_ctx)
        assert result.status is EngineStatus.REJECTED

    def test_validate_planned_order_leg_trigger_required(self) -> None:
        leg = PlannedOrderLeg(
            leg_index=0,
            sequence_group=0,
            instrument_key="NFO:TEST",
            side=OrderSide.BUY,
            order_type=OrderType.SL,
            product=ProductType.NRML,
            quantity=1,
            idempotency_key="exec-1",
            resolution_source=ContractResolutionSource.CONTRACT_SELECTION,
        )
        assert not validate_planned_order_leg(leg).is_valid

    def test_validate_planned_order_leg_invalid_instrument_key(self) -> None:
        leg = PlannedOrderLeg(
            leg_index=0,
            sequence_group=0,
            instrument_key="bad key!",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product=ProductType.NRML,
            quantity=1,
            idempotency_key="exec-1",
            resolution_source=ContractResolutionSource.CONTRACT_SELECTION,
        )
        assert not validate_planned_order_leg(leg).is_valid

    def test_contract_underlying_mismatch(self, clock: FixedClock) -> None:
        from execution.execution_engine import ExecutionPlanningError, resolve_contracts

        risk, snap = build_approved_risk(clock)
        selection = replace(make_contract_selection(), underlying="BANKNIFTY")
        with pytest.raises(ExecutionPlanningError):
            resolve_contracts(
                risk.trading_signal,
                selection,
                snap,
                config=default_execution_engine_config(),
                execution_mode=StrategyExecutionMode.LIVE,
                run_context=make_execution_run_context(risk, snap),
                leg_count=2,
            )

    def test_skip_plan_missing_reason_invalid(self) -> None:
        risk, _ = build_approved_risk(FixedClock())
        bad = ExecutionPlan(
            plan_id="p1",
            correlation_id="c1",
            risk_id="r1",
            decision_fingerprint="d1",
            risk_fingerprint="rf1",
            signal_fingerprint="sf1",
            snapshot_id="s1",
            status=ExecutionPlanStatus.SKIPPED,
            trading_signal=risk.trading_signal,
            execution_mode=StrategyExecutionMode.LIVE,
            legs=(),
            sequences=(),
            retry_policy=RetryPolicy(),
            timeout_policy=default_execution_engine_config().default_timeout_policy,
            slippage_policy=SlippagePolicy(),
            execution_policy=ExecutionPolicy(),
            summary=ExecutionPlanSummary(
                strategy_id="x",
                strategy_family=StrategyFamily.SHORT_STRANGLE,
                underlying="NIFTY",
                leg_count=0,
                total_quantity=0,
                sequence_mode=LegSequenceMode.SIMULTANEOUS,
                primary_order_type=OrderType.LIMIT,
            ),
            reasons=(),
            factors=(),
            pipeline_summary=ExecutionPipelineResult(0, 0, None, (), False),
            planned_at=fixed_as_of(),
            duration_ms=1.0,
            plan_fingerprint="fp",
            warnings=(),
            errors=(),
        )
        assert not validate_execution_plan(bad).is_valid

    def test_valid_until_before_planned_at_invalid(self, exec_engine: ExecutionEngine, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = exec_engine.plan_from_run_context(ctx)
        bad = replace(plan, valid_until=plan.planned_at - timedelta(seconds=60))
        assert not validate_execution_plan(bad).is_valid

    def test_structure_override_sequencing(self, clock: FixedClock) -> None:
        from execution.execution_engine import ExecutionStructureOverride, build_sequences

        override = ExecutionStructureOverride(sequencing_mode=LegSequenceMode.SEQUENTIAL)
        policy = replace(
            default_execution_engine_config().execution_policy,
            structure_type_overrides=MappingProxyType({"STRANGLE": override}),
        )
        config = replace(default_execution_engine_config(), execution_policy=policy)
        risk, snap = build_approved_risk(clock)
        signal = replace(
            risk.trading_signal,
            structure_hint=StructureHint(structure_type="STRANGLE", leg_count=2),
        )
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            replace(risk, trading_signal=signal),
            snap,
            sizing_hint=make_execution_sizing_hint(proposed_units_hint=2.0),
            contract_selection=selection,
        )
        plan = ExecutionEngine(config, clock=clock).plan_from_run_context(ctx)
        sequences = build_sequences(plan.legs, signal, config=config)
        assert sequences[0].mode is LegSequenceMode.SEQUENTIAL

    def test_jade_lizard_and_long_vol_sides(self) -> None:
        from execution.execution_engine import OrderSide, _resolve_leg_side

        base = build_approved_risk(FixedClock())[0].trading_signal
        jade = replace(base, strategy_family=StrategyFamily.JADE_LIZARD)
        assert _resolve_leg_side(2, jade) is OrderSide.BUY
        long_vol = replace(base, strategy_family=StrategyFamily.LONG_VOLATILITY)
        assert _resolve_leg_side(0, long_vol) is OrderSide.BUY

    def test_validate_run_context_none(self) -> None:
        with pytest.raises(ExecutionEngineContextError):
            validate_run_context(None, config=default_execution_engine_config())  # type: ignore[arg-type]

    def test_validate_planned_order_leg_empty_idempotency(self) -> None:
        leg = PlannedOrderLeg(
            leg_index=0,
            sequence_group=0,
            instrument_key="",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product=ProductType.NRML,
            quantity=1,
            idempotency_key="",
            resolution_source=ContractResolutionSource.CONTRACT_SELECTION,
        )
        result = validate_planned_order_leg(leg)
        assert len(result.errors) >= 2

    def test_resolve_leg_quantity_live_requires_hint(self) -> None:
        from execution.execution_engine import ExecutionPlanningError

        with pytest.raises(ExecutionPlanningError):
            resolve_leg_quantity(
                0,
                1,
                None,
                config=default_execution_engine_config(),
                execution_mode=StrategyExecutionMode.LIVE,
            )

    def test_signal_near_expiry_warning(self, clock: FixedClock) -> None:
        risk, snap = build_approved_risk(clock)
        ref = fixed_as_of()
        signal = replace(
            risk.trading_signal,
            as_of=ref - timedelta(minutes=30),
            valid_until=ref + timedelta(seconds=20),
        )
        risk = replace(risk, trading_signal=signal)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
            reference_time=ref,
        )
        plan = ExecutionEngine(default_execution_engine_config(), clock=clock).plan_from_run_context(ctx)
        assert any(w.code == "EXECUTION.SIGNAL.NEAR_EXPIRY" for w in plan.warnings)

    def test_order_type_blocked(self) -> None:
        from execution.execution_engine import ExecutionPlanningError, OrderTypePolicy, _resolve_order_type

        leg = PlannedOrderLeg(
            leg_index=0,
            sequence_group=0,
            instrument_key="NFO:TEST",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            product=ProductType.NRML,
            quantity=1,
            idempotency_key="exec-1",
            resolution_source=ContractResolutionSource.CONTRACT_SELECTION,
        )
        signal = build_approved_risk(FixedClock())[0].trading_signal
        policy = replace(
            default_execution_engine_config().execution_policy,
            default_order_type=OrderType.SL,
            prefer_limit_orders=False,
            allow_market_orders_live=True,
        )
        order_policy = OrderTypePolicy(
            live_allowed_types=frozenset({OrderType.MARKET}),
            analysis_allowed_types=frozenset({OrderType.MARKET}),
            backtest_allowed_types=frozenset({OrderType.MARKET}),
            force_limit_for_short_premium=False,
        )
        with pytest.raises(ExecutionPlanningError):
            _resolve_order_type(
                leg,
                signal,
                policy=policy,
                order_type_policy=order_policy,
                execution_mode=StrategyExecutionMode.LIVE,
                override=None,
            )
    def test_live_product_map(self, clock: FixedClock) -> None:
        from execution.execution_engine import ProductTypePolicy

        config = ExecutionEngineConfig(
            product_type_policy=ProductTypePolicy(
                default_product=ProductType.MIS,
                live_product_map=MappingProxyType({StrategyFamily.SHORT_STRANGLE: ProductType.NRML}),
            )
        )
        engine = ExecutionEngine(config, clock=clock)
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = engine.plan_from_run_context(ctx)
        assert all(leg.product is ProductType.NRML for leg in plan.legs)

    def test_hedge_legs_first_override(self, clock: FixedClock) -> None:
        from execution.execution_engine import ExecutionStructureOverride

        override = ExecutionStructureOverride(hedge_legs_first=True)
        policy = replace(
            default_execution_engine_config().execution_policy,
            structure_type_overrides=MappingProxyType({"STRANGLE": override}),
        )
        config = replace(default_execution_engine_config(), execution_policy=policy)
        engine = ExecutionEngine(config, clock=clock)
        risk, snap = build_approved_risk(clock)
        signal = replace(
            risk.trading_signal,
            structure_hint=StructureHint(structure_type="STRANGLE", leg_count=2),
        )
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            replace(risk, trading_signal=signal),
            snap,
            sizing_hint=make_execution_sizing_hint(proposed_units_hint=2.0),
            contract_selection=selection,
        )
        plan = engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.READY

    def test_strict_output_validation_raises(self, clock: FixedClock, monkeypatch: pytest.MonkeyPatch) -> None:
        from execution.execution_engine import ExecutionErrorRecord, ExecutionValidationResult

        engine = ExecutionEngine(default_execution_engine_config(), clock=clock)
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )

        def _invalid(_plan: ExecutionPlan) -> ExecutionValidationResult:
            return ExecutionValidationResult(
                errors=(
                    ExecutionErrorRecord(
                        code=ERROR_RESULT_FINGERPRINT_MISMATCH,
                        message="forced invalid",
                    ),
                )
            )

        monkeypatch.setattr(engine, "validate_execution_plan", _invalid)
        result = engine.plan(
            EngineContext(correlation_id=ctx.correlation_id, as_of=ctx.as_of, payload=ctx)
        )
        assert result.status is EngineStatus.REJECTED

    def test_non_strict_validation_adds_warnings(self, clock: FixedClock, monkeypatch: pytest.MonkeyPatch) -> None:
        from execution.execution_engine import ExecutionErrorRecord, ExecutionValidationResult

        config = ExecutionEngineConfig(strict_output_validation=False)
        engine = ExecutionEngine(config, clock=clock)
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )

        def _invalid(_plan: ExecutionPlan) -> ExecutionValidationResult:
            return ExecutionValidationResult(
                errors=(ExecutionErrorRecord(code=ERROR_RESULT_INVALID, message="soft invalid"),)
            )

        monkeypatch.setattr(engine, "validate_execution_plan", _invalid)
        result = engine.plan(
            EngineContext(correlation_id=ctx.correlation_id, as_of=ctx.as_of, payload=ctx)
        )
        assert result.status is EngineStatus.SUCCESS
        assert result.warnings

    def test_iron_condor_heuristic_analysis(self, clock: FixedClock) -> None:
        config = ExecutionEngineConfig(
            allow_structure_hint_heuristics=True,
            require_sizing_hint_in_live=False,
            require_contract_selection_in_live=False,
        )
        engine = ExecutionEngine(config, clock=clock)
        risk, _ = build_approved_risk(clock)
        snap = full_nifty_snapshot()
        signal = replace(
            risk.trading_signal,
            strategy_family=StrategyFamily.IRON_CONDOR,
            structure_hint=StructureHint(
                structure_type="IRON_CONDOR",
                leg_count=4,
                strikes_each_side=2,
            ),
        )
        risk = replace(risk, trading_signal=signal)
        ctx = make_execution_run_context(
            risk,
            snap,
            execution_mode=StrategyExecutionMode.ANALYSIS,
            sizing_hint=make_execution_sizing_hint(proposed_units_hint=4.0),
        )
        plan = engine.plan_from_run_context(ctx)
        assert plan.status is ExecutionPlanStatus.READY
        assert len(plan.legs) == 4

    def test_leg_side_unknown_structure(self) -> None:
        from execution.execution_engine import ExecutionPlanningError, _resolve_leg_side

        signal = replace(
            build_approved_risk(FixedClock())[0].trading_signal,
            strategy_family=StrategyFamily.CUSTOM,
            structure_hint=StructureHint(structure_type="exotic", leg_count=3),
        )
        with pytest.raises(ExecutionPlanningError):
            _resolve_leg_side(2, signal)

    def test_build_single_leg_sequence(self) -> None:
        from execution.execution_engine import build_sequences

        leg = PlannedOrderLeg(
            leg_index=0,
            sequence_group=0,
            instrument_key="NFO:TEST",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product=ProductType.NRML,
            quantity=1,
            idempotency_key="exec-1",
            resolution_source=ContractResolutionSource.CONTRACT_SELECTION,
            limit_price_hint=10.0,
        )
        sequences = build_sequences(
            (leg,),
            build_approved_risk(FixedClock())[0].trading_signal,
            config=default_execution_engine_config(),
        )
        assert sequences[0].leg_indices == (0,)

    def test_malformed_plan_dict(self) -> None:
        with pytest.raises(ExecutionEngineValidationError):
            plan_from_dict({"schema_version": "1.0.0"})

    def test_heuristic_strike_resolution_families(self) -> None:
        from execution.execution_engine import _resolve_strike_for_leg

        snap = full_nifty_snapshot()
        base = build_approved_risk(FixedClock())[0].trading_signal
        for family, structure_type, leg_count in (
            (StrategyFamily.BULL_PUT_SPREAD, "vertical", 2),
            (StrategyFamily.BEAR_CALL_SPREAD, "vertical", 2),
            (StrategyFamily.LONG_VOLATILITY, "straddle", 2),
        ):
            signal = replace(
                base,
                strategy_family=family,
                structure_hint=StructureHint(structure_type=structure_type, leg_count=leg_count),
            )
            for leg_index in range(leg_count):
                strike, _ = _resolve_strike_for_leg(leg_index, signal, snap, signal.structure_hint)
                assert strike > 0

    def test_plan_near_expiry_warning(self, clock: FixedClock) -> None:
        config = ExecutionEngineConfig(
            default_timeout_policy=replace(
                default_execution_engine_config().default_timeout_policy,
                plan_validity_seconds=10,
            )
        )
        engine = ExecutionEngine(config, clock=clock)
        risk, snap = build_approved_risk(clock)
        selection = make_contract_selection(correlation_id=risk.correlation_id)
        ctx = make_execution_run_context(
            risk,
            snap,
            sizing_hint=make_execution_sizing_hint(),
            contract_selection=selection,
        )
        plan = engine.plan_from_run_context(ctx)
        assert any(w.code == "EXECUTION.PLAN.NEAR_EXPIRY" for w in plan.warnings)
