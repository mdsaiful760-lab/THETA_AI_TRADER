"""Unit tests for paper_trading.paper_trading_runner."""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import MappingProxyType

import pytest

from core.event_bus import EventBus
from execution.execution_engine import ExecutionPlanStatus, LegSequence, LegSequenceMode, OrderSide
from execution.order_manager import OrderAggregateStatus, OrderLifecycleStatus
from paper_trading.paper_trading_runner import (
    ERROR_CAPITAL_INSUFFICIENT,
    ERROR_CORR_MISMATCH,
    ERROR_EXECUTION_DUPLICATE_ID,
    ERROR_PLAN_EMPTY_LEGS,
    ERROR_PLAN_EXPIRED,
    ERROR_PLAN_NOT_READY,
    ERROR_PRICE_INVALID_FILL,
    ERROR_PRICE_MARK_MISSING,
    ERROR_PRICE_NON_FINITE,
    ERROR_PRICE_NON_POSITIVE,
    ERROR_QTY_NON_POSITIVE,
    ERROR_SERIALIZATION_UNSUPPORTED,
    ERROR_SLIPPAGE_EXCEEDED,
    PaperBrokerageMode,
    PaperExecutionStatus,
    PaperFillModel,
    PaperLatencyMode,
    PaperSlippageMode,
    PaperSimulationContext,
    PaperTradingConfigurationError,
    PaperTradingRunner,
    PaperTradingRunnerConfig,
    PaperTradingSerializationError,
    compute_execution_fingerprint,
    default_paper_trading_runner_config,
    deserialize_paper_execution_result,
    serialize_paper_execution_result,
    to_jsonable,
)
from tests.test_execution_engine import fixed_as_of
from tests.test_order_manager import build_ready_plan, replan, single_leg_plan


def marks_from_plan(plan, override: dict[str, Decimal] | None = None) -> dict[str, Decimal]:
    """Build mark dict from plan legs using limit hints or fixed values."""
    result: dict[str, Decimal] = {}
    for leg in plan.legs:
        if override and leg.instrument_key in override:
            result[leg.instrument_key] = override[leg.instrument_key]
        elif leg.limit_price_hint is not None:
            result[leg.instrument_key] = Decimal(str(leg.limit_price_hint))
        else:
            result[leg.instrument_key] = Decimal("100.00")
    return result


def make_context(
    plan,
    *,
    marks: dict[str, Decimal] | None = None,
    execution_id: str | None = None,
    force_reject: bool = False,
) -> PaperSimulationContext:
    return PaperSimulationContext(
        correlation_id=plan.correlation_id,
        reference_time=fixed_as_of(),
        marks=MappingProxyType(marks or marks_from_plan(plan)),
        execution_id=execution_id,
        tags=MappingProxyType({"runner_kind": "paper"}),
        force_reject=force_reject,
    )


def fast_config(**kwargs: object) -> PaperTradingRunnerConfig:
    defaults = {
        "initial_cash": Decimal("1000000.00"),
        "latency_mode": PaperLatencyMode.NONE,
    }
    defaults.update(kwargs)
    return PaperTradingRunnerConfig(**defaults)


@pytest.fixture
def ready_plan():
    return build_ready_plan()


@pytest.fixture
def runner() -> PaperTradingRunner:
    return PaperTradingRunner(fast_config())


class TestConfiguration:
    def test_default_config_factory(self) -> None:
        config = default_paper_trading_runner_config()
        assert config.strict_mark_presence is True
        assert config.reject_insufficient_capital is True

    def test_cfg_paper_001_negative_cash(self) -> None:
        with pytest.raises(PaperTradingConfigurationError) as exc:
            PaperTradingRunnerConfig(initial_cash=Decimal("-1"))
        assert exc.value.code == "CFG-PAPER-001"

    def test_cfg_paper_002_negative_slippage(self) -> None:
        with pytest.raises(PaperTradingConfigurationError) as exc:
            PaperTradingRunnerConfig(slippage_bps=Decimal("-1"))
        assert exc.value.code == "CFG-PAPER-002"

    def test_cfg_paper_003_invalid_fraction(self) -> None:
        with pytest.raises(PaperTradingConfigurationError) as exc:
            PaperTradingRunnerConfig(partial_fill_fraction=Decimal("0"))
        assert exc.value.code == "CFG-PAPER-003"

    def test_cfg_paper_004_negative_latency(self) -> None:
        with pytest.raises(PaperTradingConfigurationError) as exc:
            PaperTradingRunnerConfig(latency_base_ms=-1)
        assert exc.value.code == "CFG-PAPER-004"

    def test_cfg_paper_005_invalid_quantum(self) -> None:
        with pytest.raises(PaperTradingConfigurationError) as exc:
            PaperTradingRunnerConfig(price_quantum=Decimal("0"))
        assert exc.value.code == "CFG-PAPER-005"

    def test_cfg_paper_006_dedupe_retention(self) -> None:
        with pytest.raises(PaperTradingConfigurationError) as exc:
            PaperTradingRunnerConfig(dedupe_retention=0)
        assert exc.value.code == "CFG-PAPER-006"

    def test_cfg_paper_007_schema_version(self) -> None:
        with pytest.raises(PaperTradingConfigurationError) as exc:
            PaperTradingRunnerConfig(schema_version="0.9.0")
        assert exc.value.code == "CFG-PAPER-007"

    def test_cfg_paper_008_full_fill_fraction(self) -> None:
        with pytest.raises(PaperTradingConfigurationError) as exc:
            PaperTradingRunnerConfig(
                fill_model=PaperFillModel.FULL_AT_MARK,
                partial_fill_fraction=Decimal("0.5"),
            )
        assert exc.value.code == "CFG-PAPER-008"


class TestPlanGates:
    def test_t01_ready_plan_completed(self, runner: PaperTradingRunner, ready_plan) -> None:
        result = runner.simulate_plan(ready_plan, make_context(ready_plan))
        assert result.status == PaperExecutionStatus.COMPLETED
        assert len(result.fills) == len(ready_plan.legs)
        assert result.order_tracker.aggregate_status is OrderAggregateStatus.ALL_COMPLETE
        validation = runner.validate_result(result)
        assert validation.is_valid

    def test_t02_non_ready_plan_rejected(self, runner: PaperTradingRunner, ready_plan) -> None:
        for status in (
            ExecutionPlanStatus.SKIPPED,
            ExecutionPlanStatus.NO_PLAN,
            ExecutionPlanStatus.REJECTED,
        ):
            plan = replan(ready_plan, status=status)
            result = runner.simulate_plan(plan, make_context(plan))
            assert result.status == PaperExecutionStatus.REJECTED
            assert any(error.code == ERROR_PLAN_NOT_READY for error in result.errors)
            assert not result.fills

    def test_t03_expired_plan(self, runner: PaperTradingRunner, ready_plan) -> None:
        plan = replan(ready_plan, valid_until=fixed_as_of() - timedelta(seconds=1))
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.status == PaperExecutionStatus.EXPIRED
        assert any(error.code == ERROR_PLAN_EXPIRED for error in result.errors)

    def test_t24_correlation_mismatch(self, runner: PaperTradingRunner, ready_plan) -> None:
        ctx = replace(make_context(ready_plan), correlation_id="wrong-corr")
        result = runner.simulate_plan(ready_plan, ctx)
        assert result.status == PaperExecutionStatus.REJECTED
        assert any(error.code == ERROR_CORR_MISMATCH for error in result.errors)

    def test_empty_legs_rejected(self, runner: PaperTradingRunner, ready_plan) -> None:
        plan = replan(ready_plan, legs=(), sequences=())
        result = runner.simulate_plan(plan, make_context(plan))
        assert any(error.code == ERROR_PLAN_EMPTY_LEGS for error in result.errors)

    def test_force_reject(self, runner: PaperTradingRunner, ready_plan) -> None:
        ctx = replace(make_context(ready_plan), force_reject=True)
        result = runner.simulate_plan(ready_plan, ctx)
        assert result.status == PaperExecutionStatus.REJECTED


class TestDuplicateExecutionId:
    def test_t04_duplicate_execution_id(self, runner: PaperTradingRunner, ready_plan) -> None:
        ctx = make_context(ready_plan, execution_id="paper-exec-dup-test-001")
        first = runner.simulate_plan(ready_plan, ctx)
        assert first.status == PaperExecutionStatus.COMPLETED
        cash_before = runner.get_capital_snapshot().cash
        second = runner.simulate_plan(ready_plan, ctx)
        assert second.status == PaperExecutionStatus.REJECTED
        assert any(error.code == ERROR_EXECUTION_DUPLICATE_ID for error in second.errors)
        assert runner.get_capital_snapshot().cash == cash_before


class TestPriceAndQuantityValidation:
    def test_t05_missing_mark(self, runner: PaperTradingRunner, ready_plan) -> None:
        marks = marks_from_plan(ready_plan)
        del marks[ready_plan.legs[0].instrument_key]
        result = runner.simulate_plan(ready_plan, make_context(ready_plan, marks=marks))
        assert result.status == PaperExecutionStatus.REJECTED
        assert any(error.code == ERROR_PRICE_MARK_MISSING for error in result.errors)

    def test_t06_non_finite_mark(self, runner: PaperTradingRunner, ready_plan) -> None:
        marks = marks_from_plan(ready_plan)
        marks[ready_plan.legs[0].instrument_key] = Decimal("NaN")
        result = runner.simulate_plan(ready_plan, make_context(ready_plan, marks=marks))
        assert result.status == PaperExecutionStatus.REJECTED
        assert any(error.code == ERROR_PRICE_NON_FINITE for error in result.errors)

    def test_t06_non_positive_mark(self, runner: PaperTradingRunner, ready_plan) -> None:
        marks = marks_from_plan(ready_plan)
        marks[ready_plan.legs[0].instrument_key] = Decimal("0")
        result = runner.simulate_plan(ready_plan, make_context(ready_plan, marks=marks))
        assert result.status == PaperExecutionStatus.REJECTED
        assert any(error.code == ERROR_PRICE_NON_POSITIVE for error in result.errors)

    def test_t07_non_positive_quantity(self, runner: PaperTradingRunner, ready_plan) -> None:
        leg = replace(ready_plan.legs[0], quantity=0)
        plan = replan(ready_plan, legs=(leg, ready_plan.legs[1]))
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.status == PaperExecutionStatus.REJECTED
        assert any(error.code == ERROR_QTY_NON_POSITIVE for error in result.errors)


class TestSlippageAndBrokerage:
    def test_t08_buy_slippage_increases_sell_decreases(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(
                slippage_mode=PaperSlippageMode.BPS_ADVERSE,
                slippage_bps=Decimal("100"),
                honor_plan_slippage_policy=False,
            )
        )
        plan = single_leg_plan(ready_plan)
        leg = plan.legs[0]
        mark = Decimal("100.00")
        marks = {leg.instrument_key: mark}

        buy_plan = replan(plan, legs=(replace(leg, side=OrderSide.BUY),))
        buy_result = runner.simulate_plan(buy_plan, make_context(buy_plan, marks=marks))
        assert buy_result.fills[0].fill_price > buy_result.fills[0].raw_reference_price

        runner.reset_ledger()
        sell_plan = replan(plan, legs=(replace(leg, side=OrderSide.SELL),))
        sell_result = runner.simulate_plan(sell_plan, make_context(sell_plan, marks=marks))
        assert sell_result.fills[0].fill_price < sell_result.fills[0].raw_reference_price

    def test_t09_flat_brokerage_deducted(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(brokerage_mode=PaperBrokerageMode.FLAT_PER_LEG, brokerage_flat=Decimal("25"))
        )
        plan = single_leg_plan(ready_plan)
        cash_before = runner.get_capital_snapshot().cash
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.fills[0].brokerage == Decimal("25.00")
        premium = result.fills[0].cash_delta + result.fills[0].brokerage
        assert result.fills[0].cash_delta == premium - Decimal("25.00")

    def test_t10_brokerage_bps_scales_notional(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(
                brokerage_mode=PaperBrokerageMode.BPS_OF_NOTIONAL,
                brokerage_bps=Decimal("100"),
                brokerage_flat=Decimal("0"),
            )
        )
        plan = single_leg_plan(ready_plan)
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.fills[0].brokerage > Decimal("0")


class TestLatency:
    def test_t11_latency_offsets_filled_at(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(
                latency_mode=PaperLatencyMode.PER_LEG_INCREMENT_MS,
                latency_base_ms=100,
                latency_step_ms=50,
            )
        )
        plan = single_leg_plan(ready_plan)
        ctx = make_context(plan)
        result = runner.simulate_plan(plan, ctx)
        expected = ctx.reference_time + timedelta(milliseconds=100)
        assert result.fills[0].filled_at == expected


class TestCapital:
    def test_t12_insufficient_capital_no_mutation(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(initial_cash=Decimal("1.00"), reject_insufficient_capital=True)
        )
        plan = single_leg_plan(ready_plan)
        leg = replace(plan.legs[0], side=OrderSide.BUY)
        plan = replan(plan, legs=(leg,))
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.status == PaperExecutionStatus.INSUFFICIENT_CAPITAL
        assert any(error.code == ERROR_CAPITAL_INSUFFICIENT for error in result.errors)
        assert runner.get_capital_snapshot().cash == Decimal("1.00")
        assert runner.get_position_book().positions == ()


class TestPositionsAndPnL:
    def test_t13_position_lifecycle_and_realized_pnl(self, ready_plan) -> None:
        runner = PaperTradingRunner(fast_config(brokerage_mode=PaperBrokerageMode.NONE))
        plan = single_leg_plan(ready_plan)
        leg = plan.legs[0]
        open_mark = Decimal("100.00")
        result = runner.simulate_plan(
            plan,
            make_context(plan, marks={leg.instrument_key: open_mark}),
        )
        assert result.position_book.positions
        pos = result.position_book.positions[0]
        assert pos.quantity != 0

        close_side = OrderSide.SELL if leg.side is OrderSide.BUY else OrderSide.BUY
        close_leg = replace(leg, side=close_side, leg_index=1, idempotency_key=f"{leg.idempotency_key}-close")
        close_plan = replan(
            plan,
            legs=(close_leg,),
            sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (1,)),),
        )
        close_mark = Decimal("110.00")
        close_result = runner.simulate_plan(
            close_plan,
            make_context(close_plan, marks={leg.instrument_key: close_mark}, execution_id="paper-exec-close-001"),
        )
        assert close_result.capital_snapshot.cumulative_realized_pnl != Decimal("0")

    def test_t14_mark_to_market_unrealized(self, runner: PaperTradingRunner, ready_plan) -> None:
        plan = single_leg_plan(ready_plan)
        leg = plan.legs[0]
        mark = Decimal("100.00")
        runner.simulate_plan(plan, make_context(plan, marks={leg.instrument_key: mark}))
        new_mark = Decimal("120.00")
        view = runner.mark_to_market({leg.instrument_key: new_mark}, fixed_as_of())
        assert view.total_unrealized_pnl != Decimal("0")


class TestOrderTracker:
    def test_t15_order_tracker_shape(self, runner: PaperTradingRunner, ready_plan) -> None:
        result = runner.simulate_plan(ready_plan, make_context(ready_plan))
        tracker = result.order_tracker
        assert tracker.plan_id == ready_plan.plan_id
        assert tracker.submission_id == result.execution_id
        assert len(tracker.leg_states) == len(ready_plan.legs)
        for state in tracker.leg_states:
            assert state.lifecycle_status is OrderLifecycleStatus.COMPLETE
            assert state.filled_quantity > 0
            assert state.terminal is True


class TestDeterminism:
    def test_t16_fingerprint_identical_on_replay(self, ready_plan) -> None:
        config = fast_config()
        marks = marks_from_plan(ready_plan)
        ctx = make_context(ready_plan, marks=marks)
        runner_a = PaperTradingRunner(config)
        runner_b = PaperTradingRunner(config)
        result_a = runner_a.simulate_plan(ready_plan, ctx)
        result_b = runner_b.simulate_plan(ready_plan, ctx)
        assert result_a.execution_fingerprint == result_b.execution_fingerprint
        assert result_a.execution_id == result_b.execution_id

    def test_determinism_50_runs(self, ready_plan) -> None:
        config = fast_config()
        ctx = make_context(ready_plan)
        fingerprints = set()
        for _ in range(50):
            runner = PaperTradingRunner(config)
            result = runner.simulate_plan(ready_plan, ctx)
            fingerprints.add(result.execution_fingerprint)
        assert len(fingerprints) == 1


class TestSerialization:
    def test_t17_serialize_round_trip_fields(self, runner: PaperTradingRunner, ready_plan) -> None:
        result = runner.simulate_plan(ready_plan, make_context(ready_plan))
        payload = serialize_paper_execution_result(result)
        assert payload["schema_version"] == "1.0.0"
        assert payload["execution_id"] == result.execution_id
        assert payload["status"] == result.status.value
        json.dumps(payload)
        with pytest.raises(PaperTradingSerializationError):
            deserialize_paper_execution_result(payload)
        with pytest.raises(PaperTradingSerializationError):
            deserialize_paper_execution_result({"schema_version": "0.9.0"})
        config_payload = to_jsonable(fast_config())
        assert config_payload["fill_model"] == PaperFillModel.FULL_AT_LIMIT_OR_MARK.value


class TestThreadSafety:
    def test_t18_parallel_distinct_execution_ids(self, ready_plan) -> None:
        runner = PaperTradingRunner(fast_config())
        marks = marks_from_plan(ready_plan)
        errors: list[Exception] = []

        def worker(index: int) -> None:
            try:
                plan = single_leg_plan(ready_plan)
                ctx = make_context(
                    plan,
                    marks=marks,
                    execution_id=f"paper-exec-thread-{index}",
                )
                result = runner.simulate_plan(plan, ctx)
                assert result.status == PaperExecutionStatus.COMPLETED
            except Exception as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(worker, range(16)))
        assert not errors
        assert runner.get_capital_snapshot().cash.is_finite()


class TestSequenceAbort:
    def test_t19_sequence_abort_on_leg_failure(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(
                slippage_mode=PaperSlippageMode.BPS_ADVERSE,
                slippage_bps=Decimal("5000"),
                honor_plan_slippage_policy=True,
            )
        )
        leg0, leg1 = ready_plan.legs[0], ready_plan.legs[1]
        marks = {
            leg0.instrument_key: Decimal("100.00"),
            leg1.instrument_key: Decimal("100.00"),
        }
        tight_plan = replan(
            ready_plan,
            slippage_policy=replace(ready_plan.slippage_policy, max_slippage_bps=1.0),
            sequences=(
                LegSequence(
                    0,
                    LegSequenceMode.SEQUENTIAL,
                    (leg0.leg_index, leg1.leg_index),
                    inter_leg_delay_ms=100,
                    abort_on_leg_failure=True,
                ),
            ),
        )
        result = runner.simulate_plan(tight_plan, make_context(tight_plan, marks=marks))
        assert result.status in {PaperExecutionStatus.PARTIAL, PaperExecutionStatus.FAILED}
        assert any(error.code == ERROR_SLIPPAGE_EXCEEDED for error in result.errors)


class TestEvents:
    def test_t22_events_published_with_bus(self, ready_plan) -> None:
        bus = EventBus()
        received: list[str] = []

        def handler(event) -> None:
            received.append(event.topic)

        bus.subscribe("paper.order.plan.started", handler)
        bus.subscribe("paper.order.plan.completed", handler)
        bus.subscribe("paper.capital.updated", handler)
        runner = PaperTradingRunner(fast_config(), event_bus=bus)
        runner.simulate_plan(ready_plan, make_context(ready_plan))
        assert "paper.order.plan.started" in received
        assert "paper.order.plan.completed" in received
        assert "paper.capital.updated" in received

    def test_t23_events_noop_without_bus(self, runner: PaperTradingRunner, ready_plan) -> None:
        result = runner.simulate_plan(ready_plan, make_context(ready_plan))
        assert result.status == PaperExecutionStatus.COMPLETED


class TestPartialFill:
    def test_t25_partial_fill_model(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(
                fill_model=PaperFillModel.DETERMINISTIC_PARTIAL,
                partial_fill_fraction=Decimal("0.5"),
            )
        )
        leg = replace(ready_plan.legs[0], quantity=10, leg_index=0)
        plan = replan(ready_plan, legs=(leg,), sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (0,)),))
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.status == PaperExecutionStatus.PARTIAL
        assert result.fills[0].quantity == 5
        state = result.order_tracker.leg_states[0]
        assert state.lifecycle_status is OrderLifecycleStatus.PARTIALLY_FILLED


class TestMultiLeg:
    def test_t26_four_leg_capital_and_book(self, ready_plan) -> None:
        runner = PaperTradingRunner(fast_config())
        result = runner.simulate_plan(ready_plan, make_context(ready_plan))
        assert len(result.fills) == len(ready_plan.legs)
        assert result.capital_snapshot.cumulative_brokerage > Decimal("0")


class TestResetLedger:
    def test_t27_reset_ledger(self, runner: PaperTradingRunner, ready_plan) -> None:
        runner.simulate_plan(ready_plan, make_context(ready_plan))
        assert runner.get_position_book().positions
        snap = runner.reset_ledger(initial_cash=Decimal("500000.00"))
        assert snap.cash == Decimal("500000.00")
        assert runner.get_position_book().positions == ()


class TestSlippageCeiling:
    def test_t28_honor_plan_max_slippage_bps(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(
                slippage_bps=Decimal("500"),
                honor_plan_slippage_policy=True,
            )
        )
        plan = replan(
            single_leg_plan(ready_plan),
            slippage_policy=replace(ready_plan.slippage_policy, max_slippage_bps=1.0),
        )
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.status in {
            PaperExecutionStatus.FAILED,
            PaperExecutionStatus.PARTIAL,
            PaperExecutionStatus.REJECTED,
        }
        if result.fills:
            assert any(error.code == ERROR_SLIPPAGE_EXCEEDED for error in result.errors)


class TestForbiddenImports:
    def test_t21_no_broker_imports(self) -> None:
        import paper_trading.paper_trading_runner as module

        source = inspect.getsource(module)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "broker" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                assert node.module is None or "broker" not in node.module
                assert node.module is None or "risk_engine" not in node.module
                assert node.module is None or "strategy_evaluation" not in node.module

    def test_module_imports_cleanly(self) -> None:
        importlib.reload(importlib.import_module("paper_trading.paper_trading_runner"))


class TestExecutionIdGeneration:
    def test_auto_generated_execution_id(self, runner: PaperTradingRunner, ready_plan) -> None:
        ctx = make_context(ready_plan, execution_id=None)
        result = runner.simulate_plan(ready_plan, ctx)
        assert result.execution_id.startswith("paper-exec-")
        assert len(result.execution_id) == len("paper-exec-") + 24


class TestGetters:
    def test_getters_return_snapshots(self, runner: PaperTradingRunner, ready_plan) -> None:
        runner.simulate_plan(ready_plan, make_context(ready_plan))
        capital = runner.get_capital_snapshot()
        positions = runner.get_position_book()
        portfolio = runner.get_portfolio_view()
        assert capital.cash.is_finite()
        assert portfolio.capital.cash == capital.cash
        assert portfolio.positions.as_of == positions.as_of


class TestInvalidFillPrice:
    def test_extreme_slippage_invalid_fill(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(
                slippage_mode=PaperSlippageMode.ABSOLUTE,
                slippage_absolute=Decimal("1000"),
                honor_plan_slippage_policy=False,
            )
        )
        plan = single_leg_plan(ready_plan)
        leg = plan.legs[0]
        if leg.side is OrderSide.SELL:
            plan = replan(plan, legs=(replace(leg, side=OrderSide.SELL),))
        marks = {plan.legs[0].instrument_key: Decimal("1.00")}
        result = runner.simulate_plan(plan, make_context(plan, marks=marks))
        if not result.fills:
            assert any(
                error.code in {ERROR_PRICE_INVALID_FILL, ERROR_SLIPPAGE_EXCEEDED}
                for error in result.errors
            )


class TestComputeExecutionFingerprint:
    def test_compute_execution_fingerprint_export(self, runner: PaperTradingRunner, ready_plan) -> None:
        result = runner.simulate_plan(ready_plan, make_context(ready_plan))
        fp = compute_execution_fingerprint(
            plan_fingerprint=ready_plan.plan_fingerprint,
            execution_id=result.execution_id,
            fills=result.fills,
            capital_snapshot=result.capital_snapshot,
            position_book=result.position_book,
            portfolio_view=result.portfolio_view,
            order_tracker=result.order_tracker,
            config=fast_config(),
        )
        assert fp == result.execution_fingerprint


class TestNearExpiryWarning:
    def test_near_expiry_warning(self, runner: PaperTradingRunner, ready_plan) -> None:
        plan = replan(ready_plan, valid_until=fixed_as_of() + timedelta(seconds=10))
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.status == PaperExecutionStatus.COMPLETED
        assert any(warning.code == "PAPER.PLAN.NEAR_EXPIRY" for warning in result.warnings)


class TestSequenceDelayLatency:
    def test_sequence_delay_latency_mode(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(latency_mode=PaperLatencyMode.SEQUENCE_DELAY)
        )
        result = runner.simulate_plan(ready_plan, make_context(ready_plan))
        if len(result.fills) >= 2:
            assert result.fills[1].filled_at >= result.fills[0].filled_at


class TestFlatPlusBpsBrokerage:
    def test_flat_plus_bps_brokerage(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(
                brokerage_mode=PaperBrokerageMode.FLAT_PLUS_BPS,
                brokerage_flat=Decimal("10"),
                brokerage_bps=Decimal("50"),
            )
        )
        plan = single_leg_plan(ready_plan)
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.fills[0].brokerage >= Decimal("10.00")


class TestMaxOfPlanAndConfigSlippage:
    def test_max_of_plan_and_config_slippage_mode(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(
                slippage_mode=PaperSlippageMode.MAX_OF_PLAN_AND_CONFIG,
                slippage_bps=Decimal("5"),
                honor_plan_slippage_policy=False,
            )
        )
        leg = replace(ready_plan.legs[0], max_slippage_bps=100.0)
        plan = replan(single_leg_plan(ready_plan), legs=(leg,))
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.status == PaperExecutionStatus.COMPLETED


class TestNoneSlippage:
    def test_none_slippage_mode(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(slippage_mode=PaperSlippageMode.NONE)
        )
        plan = single_leg_plan(ready_plan)
        mark = Decimal(str(plan.legs[0].limit_price_hint or "100"))
        marks = {plan.legs[0].instrument_key: mark}
        result = runner.simulate_plan(plan, make_context(plan, marks=marks))
        assert result.fills[0].slippage_applied == Decimal("0")


class TestInvalidExecutionId:
    def test_invalid_execution_id_format(self, runner: PaperTradingRunner, ready_plan) -> None:
        ctx = make_context(ready_plan, execution_id="bad id!")
        result = runner.simulate_plan(ready_plan, ctx)
        assert result.status == PaperExecutionStatus.REJECTED


class TestContextInvalid:
    def test_naive_timestamp_rejected(self, runner: PaperTradingRunner, ready_plan) -> None:
        from datetime import datetime

        ctx = replace(
            make_context(ready_plan),
            reference_time=datetime(2026, 8, 3, 10, 15, 0),
        )
        result = runner.simulate_plan(ready_plan, ctx)
        assert result.status == PaperExecutionStatus.REJECTED


class TestValidateResultInvalidSchema:
    def test_validate_result_bad_schema(self, runner: PaperTradingRunner, ready_plan) -> None:
        result = runner.simulate_plan(ready_plan, make_context(ready_plan))
        bad = replace(result, schema_version="9.9.9")
        validation = runner.validate_result(bad)
        assert not validation.is_valid
        assert any(error.code == ERROR_SERIALIZATION_UNSUPPORTED for error in validation.errors)


class TestDedupeRetention:
    def test_dedupe_retention_evicts_oldest(self, ready_plan) -> None:
        runner = PaperTradingRunner(fast_config(dedupe_retention=2))
        plan = single_leg_plan(ready_plan)
        for index in range(3):
            ctx = make_context(plan, execution_id=f"paper-exec-retention-{index}")
            runner.simulate_plan(plan, ctx)
        ctx_first = make_context(plan, execution_id="paper-exec-retention-0")
        retry = runner.simulate_plan(plan, ctx_first)
        assert retry.status == PaperExecutionStatus.COMPLETED


class TestAdditionalCoverage:
    def test_paper_event_type_topics(self) -> None:
        from paper_trading.paper_trading_runner import PaperEventType

        for event_type in PaperEventType:
            assert event_type.topic.startswith("paper.")

    def test_mark_to_market_invalid_mark_raises(self, runner: PaperTradingRunner) -> None:
        from paper_trading.paper_trading_runner import PaperTradingValidationError

        with pytest.raises(PaperTradingValidationError):
            runner.mark_to_market({"NIFTY": Decimal("-1")}, fixed_as_of())

    def test_to_jsonable_unsupported_type(self) -> None:
        from paper_trading.paper_trading_runner import PaperTradingSerializationError

        with pytest.raises(PaperTradingSerializationError):
            to_jsonable({"not": "supported"})

    def test_reset_ledger_invalid_cash(self, runner: PaperTradingRunner) -> None:
        from paper_trading.paper_trading_runner import PaperTradingValidationError

        with pytest.raises(PaperTradingValidationError):
            runner.reset_ledger(initial_cash=Decimal("-100"))

    def test_market_order_blocked(self, ready_plan) -> None:
        from execution.execution_engine import OrderType

        runner = PaperTradingRunner(fast_config(allow_market_orders=False))
        leg = replace(ready_plan.legs[0], order_type=OrderType.MARKET)
        plan = replan(single_leg_plan(ready_plan), legs=(leg,))
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.status == PaperExecutionStatus.REJECTED

    def test_full_at_mark_fill_model(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(fill_model=PaperFillModel.FULL_AT_MARK, slippage_mode=PaperSlippageMode.NONE)
        )
        plan = single_leg_plan(ready_plan)
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.status == PaperExecutionStatus.COMPLETED

    def test_absolute_slippage_mode(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(
                slippage_mode=PaperSlippageMode.ABSOLUTE,
                slippage_absolute=Decimal("1.00"),
                honor_plan_slippage_policy=False,
            )
        )
        plan = single_leg_plan(ready_plan)
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.fills[0].slippage_applied >= Decimal("0")

    def test_aborted_without_fills_failure(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(
                slippage_bps=Decimal("5000"),
                honor_plan_slippage_policy=True,
            )
        )
        plan = replan(
            single_leg_plan(ready_plan),
            slippage_policy=replace(ready_plan.slippage_policy, max_slippage_bps=0.1),
        )
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.status == PaperExecutionStatus.FAILED
        assert not result.fills

    def test_invalid_limit_hint_rejected(self, ready_plan) -> None:
        runner = PaperTradingRunner(fast_config())
        leg = replace(ready_plan.legs[0], limit_price_hint=-1.0)
        plan = replan(single_leg_plan(ready_plan), legs=(leg,))
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.status == PaperExecutionStatus.REJECTED

    def test_non_int_quantity_rejected(self, ready_plan) -> None:
        runner = PaperTradingRunner(fast_config())
        leg = replace(ready_plan.legs[0], quantity=1.5)  # type: ignore[arg-type]
        plan = replan(single_leg_plan(ready_plan), legs=(leg,))
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.status == PaperExecutionStatus.REJECTED

    def test_publish_events_disabled(self, ready_plan) -> None:
        bus = EventBus()
        received: list[str] = []
        bus.subscribe("paper.order.plan.started", lambda e: received.append(e.topic))
        runner = PaperTradingRunner(fast_config(publish_events=False), event_bus=bus)
        runner.simulate_plan(ready_plan, make_context(ready_plan))
        assert received == []

    def test_strict_mark_false_runtime_missing_mark(self, ready_plan) -> None:
        runner = PaperTradingRunner(fast_config(strict_mark_presence=False))
        marks = marks_from_plan(ready_plan)
        del marks[ready_plan.legs[0].instrument_key]
        result = runner.simulate_plan(ready_plan, make_context(ready_plan, marks=marks))
        assert result.status in {PaperExecutionStatus.PARTIAL, PaperExecutionStatus.FAILED}

    def test_extra_leg_in_sequence_index(self, ready_plan) -> None:
        runner = PaperTradingRunner(fast_config())
        bad_sequence = LegSequence(0, LegSequenceMode.SEQUENTIAL, (99,), abort_on_leg_failure=True)
        plan = replan(ready_plan, sequences=(*ready_plan.sequences, bad_sequence))
        result = runner.simulate_plan(plan, make_context(plan))
        assert result.errors

    def test_fixed_ms_latency(self, ready_plan) -> None:
        runner = PaperTradingRunner(
            fast_config(latency_mode=PaperLatencyMode.FIXED_MS, latency_base_ms=250)
        )
        plan = single_leg_plan(ready_plan)
        ctx = make_context(plan)
        result = runner.simulate_plan(plan, ctx)
        assert result.fills[0].filled_at == ctx.reference_time + timedelta(milliseconds=250)

    def test_position_flip_on_over_close(self, ready_plan) -> None:
        runner = PaperTradingRunner(fast_config(brokerage_mode=PaperBrokerageMode.NONE))
        plan = single_leg_plan(ready_plan)
        leg = plan.legs[0]
        runner.simulate_plan(plan, make_context(plan))
        close_qty = leg.quantity * 2
        close_leg = replace(
            leg,
            quantity=close_qty,
            side=OrderSide.BUY if leg.side is OrderSide.SELL else OrderSide.SELL,
            leg_index=1,
            idempotency_key=f"{leg.idempotency_key}-flip",
        )
        close_plan = replan(
            plan,
            legs=(close_leg,),
            sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (1,)),),
        )
        result = runner.simulate_plan(
            close_plan,
            make_context(close_plan, execution_id="paper-exec-flip-001"),
        )
        assert result.status == PaperExecutionStatus.COMPLETED
        pos = result.position_book.positions[0]
        assert pos.quantity != 0

    def test_empty_correlation_rejected(self, runner: PaperTradingRunner, ready_plan) -> None:
        ctx = replace(make_context(ready_plan), correlation_id="")
        result = runner.simulate_plan(ready_plan, ctx)
        assert result.status == PaperExecutionStatus.REJECTED

    def test_validate_result_fingerprint_mismatch(self, runner: PaperTradingRunner, ready_plan) -> None:
        result = runner.simulate_plan(ready_plan, make_context(ready_plan))
        tampered = replace(result, execution_fingerprint="deadbeef")
        validation = runner.validate_result(tampered)
        assert not validation.is_valid
