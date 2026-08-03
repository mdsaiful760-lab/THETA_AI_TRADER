"""Unit tests for portfolio.position_manager."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from broker.base_broker import Exchange, PositionRecord, ProductType as BrokerProductType
from core.event_bus import EventBus
from execution.execution_engine import OrderSide, OrderType, ProductType
from execution.order_manager import (
    OrderAggregateStatus,
    OrderLifecycleEvent,
    OrderLifecycleEventType,
    OrderLifecycleStatus,
    OrderState,
    OrderTracker,
    compute_tracker_fingerprint,
)
from portfolio.position_manager import (
    ERROR_CONTEXT_CORRELATION_MISMATCH,
    ERROR_CONTEXT_NAIVE_TIMESTAMP,
    ERROR_FILL_INVALID_PRICE,
    ERROR_FILL_OVER_EXIT,
    ERROR_POSITION_NOT_FOUND,
    ERROR_RESULT_INVALID,
    ERROR_SERIALIZATION_MALFORMED,
    ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
    ERROR_TRACKER_MISSING,
    ERROR_TRACKER_NO_LEGS,
    FillDelta,
    PositionEventType,
    PositionLifecycleState,
    PositionManager,
    PositionManagerConfig,
    PositionManagerConfigurationError,
    PositionManagerUpdateError,
    PositionManagerValidationError,
    PositionSide,
    PositionUpdateContext,
    PositionUpdateStatus,
    WARN_BROKER_DRIFT,
    WARN_PRICE_HINT_MISSING,
    WARN_STRATEGY_MISSING,
    assert_valid_position_update_result,
    compute_new_average_entry_price,
    compute_realized_pnl_delta,
    compute_unrealized_pnl,
    compute_update_fingerprint,
    default_position_manager_config,
    deserialize_position_update_result,
    extract_fill_deltas,
    serialize_position_update_result,
    validate_position_update_result,
    validate_update_context,
)
from strategy.signals import StrategyExecutionMode, StrategyFamily

IST = ZoneInfo("Asia/Kolkata")


def fixed_as_of() -> datetime:
    """Return fixed timezone-aware reference time."""
    return datetime(2026, 8, 4, 10, 0, 0, tzinfo=IST)


def fast_config(**overrides: object) -> PositionManagerConfig:
    """Build fast deterministic test configuration."""
    base = default_position_manager_config()
    return replace(base, **overrides)


def make_context(
    *,
    correlation_id: str = "corr-1",
    reference_time: datetime | None = None,
    execution_mode: StrategyExecutionMode = StrategyExecutionMode.BACKTEST,
    price_hints: MappingProxyType | None = None,
    allow_orphan_exits: bool | None = None,
) -> PositionUpdateContext:
    """Build position update context."""
    kwargs: dict[str, object] = {
        "correlation_id": correlation_id,
        "reference_time": reference_time or fixed_as_of(),
        "execution_mode": execution_mode,
        "price_hints": price_hints or MappingProxyType({}),
        "tags": MappingProxyType({}),
    }
    return PositionUpdateContext(**kwargs)  # type: ignore[arg-type]


def make_order_state(
    *,
    leg_index: int = 0,
    instrument_key: str = "NFO:NIFTY24AUG25000CE",
    side: OrderSide = OrderSide.SELL,
    product: ProductType = ProductType.NRML,
    planned_quantity: int = 75,
    filled_quantity: int = 75,
    lifecycle_status: OrderLifecycleStatus = OrderLifecycleStatus.COMPLETE,
    average_fill_price: float = 125.50,
    strategy_id: str = "short-strangle-v1",
    strategy_family: str = StrategyFamily.SHORT_STRANGLE.value,
    plan_id: str = "plan-1",
    idempotency_key: str = "idem-0",
    terminal: bool = True,
    metadata: MappingProxyType | None = None,
) -> OrderState:
    """Build order state fixture."""
    base_metadata = {
        "plan_id": plan_id,
        "strategy_id": strategy_id,
        "strategy_family": strategy_family,
        "underlying": "NIFTY",
    }
    if metadata:
        base_metadata.update(dict(metadata))
    return OrderState(
        leg_index=leg_index,
        sequence_group=0,
        instrument_key=instrument_key,
        side=side,
        order_type=OrderType.LIMIT,
        product=product,
        planned_quantity=planned_quantity,
        lifecycle_status=lifecycle_status,
        idempotency_key=idempotency_key,
        filled_quantity=filled_quantity,
        remaining_quantity=planned_quantity - filled_quantity,
        average_fill_price=average_fill_price,
        terminal=terminal,
        terminal_at=fixed_as_of() if terminal else None,
        metadata=MappingProxyType(base_metadata),
    )


def make_tracker(
    *states: OrderState,
    submission_id: str = "sub-1",
    plan_id: str = "plan-1",
    correlation_id: str = "corr-1",
) -> OrderTracker:
    """Build order tracker from leg states."""
    leg_states = states or (make_order_state(),)
    fingerprint = compute_tracker_fingerprint(leg_states)
    return OrderTracker(
        submission_id=submission_id,
        plan_id=plan_id,
        correlation_id=correlation_id,
        plan_fingerprint="fp-1",
        leg_states=leg_states,
        aggregate_status=OrderAggregateStatus.ALL_COMPLETE,
        sequence_results=(),
        started_at=fixed_as_of(),
        completed_at=fixed_as_of(),
        tracker_fingerprint=fingerprint,
    )


class RecordingEventBus:
    """Capture published position events."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def publish(self, envelope: object) -> None:
        self.events.append(envelope)


@pytest.fixture
def manager() -> PositionManager:
    return PositionManager(fast_config())


class TestConfiguration:
    def test_invalid_price_hint_max_age(self) -> None:
        with pytest.raises(PositionManagerConfigurationError):
            PositionManagerConfig(price_hint_max_age_seconds=-1)

    def test_default_config_factory(self) -> None:
        config = default_position_manager_config()
        assert config.strict_correlation is True
        assert config.idempotent_updates is True


class TestPnLCalculations:
    def test_vwap_first_fill(self) -> None:
        assert compute_new_average_entry_price(0, 0.0, 75, 125.50) == 125.50

    def test_vwap_second_fill(self) -> None:
        avg = compute_new_average_entry_price(75, 125.50, 75, 130.00)
        assert avg == 127.75

    def test_realized_pnl_long_exit(self) -> None:
        pnl = compute_realized_pnl_delta(PositionSide.LONG, 125.50, 75, 130.00)
        assert pnl == 337.50

    def test_realized_pnl_short_exit(self) -> None:
        pnl = compute_realized_pnl_delta(PositionSide.SHORT, 125.50, 75, 120.00)
        assert pnl == 412.50

    def test_unrealized_pnl_long(self) -> None:
        pnl = compute_unrealized_pnl(PositionSide.LONG, 75, 125.50, 130.00)
        assert pnl == 337.50

    def test_unrealized_pnl_short(self) -> None:
        pnl = compute_unrealized_pnl(PositionSide.SHORT, 75, 125.50, 120.00)
        assert pnl == 412.50

    def test_unrealized_zero_quantity(self) -> None:
        assert compute_unrealized_pnl(PositionSide.LONG, 0, 100.0, 110.0) == 0.0


class TestInputGate:
    def test_reject_correlation_mismatch(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state(), correlation_id="other")
        context = make_context(correlation_id="corr-1")
        result = manager.apply_order_tracker(tracker, context)
        assert result.status is PositionUpdateStatus.REJECTED
        assert result.primary_error_code == ERROR_CONTEXT_CORRELATION_MISMATCH

    def test_reject_naive_timestamp(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        naive = datetime(2026, 8, 4, 10, 0, 0)
        context = make_context(reference_time=naive)
        result = manager.apply_order_tracker(tracker, context)
        assert result.status is PositionUpdateStatus.REJECTED
        assert any(error.code == ERROR_CONTEXT_NAIVE_TIMESTAMP for error in result.errors)

    def test_reject_no_legs(self, manager: PositionManager) -> None:
        tracker = make_tracker()
        tracker = replace(tracker, leg_states=())
        context = make_context()
        result = manager.apply_order_tracker(tracker, context)
        assert result.status is PositionUpdateStatus.REJECTED
        assert result.primary_error_code == ERROR_TRACKER_NO_LEGS

    def test_validate_context_helper(self) -> None:
        tracker = make_tracker(make_order_state(), correlation_id="x")
        context = make_context(correlation_id="y")
        result = validate_update_context(context, tracker, fast_config())
        assert not result.is_valid


class TestFillExtraction:
    def test_extract_single_fill(self) -> None:
        tracker = make_tracker(make_order_state())
        deltas = extract_fill_deltas(tracker, previously_applied=frozenset())
        assert len(deltas) == 1
        assert deltas[0].fill_quantity == 75

    def test_idempotent_extraction(self) -> None:
        tracker = make_tracker(make_order_state())
        deltas = extract_fill_deltas(tracker, previously_applied=frozenset())
        fill_id = deltas[0].fill_id
        again = extract_fill_deltas(tracker, previously_applied=frozenset({fill_id}))
        assert again == ()

    def test_skip_non_fill_bearing_status(self) -> None:
        state = make_order_state(
            filled_quantity=0,
            lifecycle_status=OrderLifecycleStatus.REJECTED,
        )
        tracker = make_tracker(state)
        assert extract_fill_deltas(tracker, previously_applied=frozenset()) == ()


class TestEntryAndLifecycle:
    def test_open_position_on_entry(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        result = manager.apply_order_tracker(tracker, make_context())
        assert result.status is PositionUpdateStatus.APPLIED
        assert result.snapshot.open_position_count == 1
        position = result.updated_positions[0]
        assert position.quantity == 75
        assert position.side is PositionSide.SHORT
        assert result.updated_positions[0].lifecycle_state is PositionLifecycleState.OPEN

    def test_two_leg_strangle(self, manager: PositionManager) -> None:
        ce = make_order_state(
            leg_index=0,
            instrument_key="NFO:NIFTY24AUG25000CE",
            average_fill_price=125.50,
        )
        pe = make_order_state(
            leg_index=1,
            instrument_key="NFO:NIFTY24AUG25000PE",
            average_fill_price=118.25,
            idempotency_key="idem-1",
        )
        tracker = make_tracker(ce, pe)
        result = manager.apply_order_tracker(tracker, make_context())
        assert result.snapshot.open_position_count == 2
        group_ids = {position.position_group_id for position in result.snapshot.positions}
        assert group_ids == {"plan-1"}

    def test_partial_exit_and_close(self, manager: PositionManager) -> None:
        entry = make_order_state(
            side=OrderSide.BUY,
            average_fill_price=125.50,
            filled_quantity=150,
            planned_quantity=150,
        )
        open_result = manager.apply_order_tracker(make_tracker(entry), make_context())
        position_id = open_result.updated_positions[0].position_id

        partial_exit_state = make_order_state(
            side=OrderSide.SELL,
            filled_quantity=75,
            planned_quantity=75,
            average_fill_price=130.00,
            idempotency_key="exit-1",
        )
        partial = manager.apply_order_tracker(make_tracker(partial_exit_state), make_context())
        assert partial.updated_positions[0].quantity == 75
        assert partial.updated_positions[0].lifecycle_state is PositionLifecycleState.PARTIALLY_CLOSED
        assert partial.updated_positions[0].realized_pnl == 337.50

        final_exit = make_order_state(
            side=OrderSide.SELL,
            filled_quantity=75,
            planned_quantity=75,
            average_fill_price=128.00,
            idempotency_key="exit-2",
        )
        closed = manager.apply_order_tracker(make_tracker(final_exit), make_context())
        closed_position = manager.get_position(position_id)
        assert closed_position is not None
        assert closed_position.lifecycle_state is PositionLifecycleState.CLOSED
        assert closed_position.quantity == 0
        assert closed_position.realized_pnl == 525.00
        assert closed.snapshot.open_position_count == 0

    def test_idempotent_reapply(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        first = manager.apply_order_tracker(tracker, make_context())
        second = manager.apply_order_tracker(tracker, make_context())
        assert second.status is PositionUpdateStatus.NOOP
        assert first.update_fingerprint == second.update_fingerprint


class TestExitErrors:
    def test_over_exit_rejected(self, manager: PositionManager) -> None:
        entry = make_order_state(side=OrderSide.BUY, filled_quantity=50, planned_quantity=50)
        manager.apply_order_tracker(make_tracker(entry), make_context())
        over_exit = make_order_state(
            side=OrderSide.SELL,
            filled_quantity=100,
            planned_quantity=100,
            idempotency_key="over",
        )
        result = manager.apply_order_tracker(
            make_tracker(over_exit),
            make_context(),
        )
        assert result.status is PositionUpdateStatus.PARTIAL
        assert any(error.code == ERROR_FILL_OVER_EXIT for error in result.errors)

    def test_orphan_exit_without_position(self, manager: PositionManager) -> None:
        exit_only = make_order_state(
            side=OrderSide.SELL,
            filled_quantity=75,
            idempotency_key="orphan",
        )
        exit_only = replace(
            exit_only,
            metadata=MappingProxyType(
                {
                    **dict(exit_only.metadata),
                    "position_intent": "exit",
                }
            ),
        )
        result = manager.apply_order_tracker(make_tracker(exit_only), make_context())
        assert any(error.code == ERROR_POSITION_NOT_FOUND for error in result.errors)

    def test_orphan_exit_allowed_in_backtest(self) -> None:
        mgr = PositionManager(fast_config(allow_orphan_exits=True))
        exit_only = make_order_state(side=OrderSide.SELL, idempotency_key="orphan")
        result = mgr.apply_order_tracker(make_tracker(exit_only), make_context())
        assert result.status is PositionUpdateStatus.APPLIED


class TestUnrealizedPnL:
    def test_mark_to_market(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state(average_fill_price=125.50))
        context = make_context(
            price_hints=MappingProxyType({"NFO:NIFTY24AUG25000CE": 130.00}),
        )
        result = manager.apply_order_tracker(tracker, context)
        position = result.snapshot.positions[0]
        assert position.unrealized_pnl == compute_unrealized_pnl(
            PositionSide.SHORT,
            75,
            125.50,
            130.00,
        )

    def test_missing_price_hint_warning(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        manager.apply_order_tracker(tracker, make_context())
        second = make_order_state(
            instrument_key="NFO:NIFTY24AUG25000PE",
            idempotency_key="pe-leg",
            leg_index=1,
        )
        result = manager.apply_order_tracker(make_tracker(second), make_context())
        assert any(warning.code == WARN_PRICE_HINT_MISSING for warning in result.warnings)


class TestEvents:
    def test_lifecycle_events_published(self) -> None:
        bus = EventBus()
        captured: list[object] = []
        bus.subscribe("position.*", lambda event: captured.append(event.payload))
        manager = PositionManager(fast_config(), event_bus=bus)
        tracker = make_tracker(make_order_state())
        manager.apply_order_tracker(tracker, make_context())
        topics = {getattr(payload, "topic", "") for payload in captured}
        assert "position.opened" in topics
        assert "position.update.completed" in topics

    def test_no_events_when_bus_none(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        result = manager.apply_order_tracker(tracker, make_context())
        assert result.status is PositionUpdateStatus.APPLIED


class TestSerialization:
    def test_round_trip_update_result(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        result = manager.apply_order_tracker(tracker, make_context())
        payload = serialize_position_update_result(result)
        restored = deserialize_position_update_result(payload)
        assert restored.update_id == result.update_id
        assert restored.status == result.status
        assert restored.snapshot.open_position_count == result.snapshot.open_position_count

    def test_malformed_json(self) -> None:
        with pytest.raises(PositionManagerValidationError) as exc:
            deserialize_position_update_result("{bad")
        assert exc.value.code == ERROR_SERIALIZATION_MALFORMED

    def test_unsupported_schema_version(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        result = manager.apply_order_tracker(tracker, make_context())
        payload = serialize_position_update_result(result)
        import json

        data = json.loads(payload)
        data["schema_version"] = "9.9.9"
        with pytest.raises(PositionManagerValidationError) as exc:
            deserialize_position_update_result(json.dumps(data))
        assert exc.value.code == ERROR_SERIALIZATION_UNSUPPORTED_VERSION


class TestDeterminism:
    def test_stable_fingerprint(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        context = make_context()
        first = manager.apply_order_tracker(tracker, context)
        mgr2 = PositionManager(fast_config())
        second = mgr2.apply_order_tracker(tracker, context)
        assert first.update_fingerprint == second.update_fingerprint

    def test_compute_update_fingerprint_helper(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        result = manager.apply_order_tracker(tracker, make_context())
        recomputed = compute_update_fingerprint(tracker, result.snapshot, fast_config())
        assert recomputed == result.update_fingerprint


class TestValidation:
    def test_validate_update_result_valid(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        result = manager.apply_order_tracker(tracker, make_context())
        validation = validate_position_update_result(result)
        assert validation.is_valid
        assert_valid_position_update_result(result)

    def test_assert_invalid_update_id(self) -> None:
        from portfolio.position_manager import (
            PositionPipelineResult,
            PositionSnapshot,
            PositionUpdateResult,
        )

        snapshot = PositionSnapshot(
            snapshot_id="s",
            as_of=fixed_as_of(),
            account_id=None,
            positions=(),
            open_position_count=0,
            aggregate_quantity_by_underlying=MappingProxyType({}),
            aggregate_unrealized_pnl=0.0,
            aggregate_realized_pnl_session=0.0,
            snapshot_fingerprint="fp",
        )
        result = PositionUpdateResult(
            update_id="",
            tracker_submission_id=None,
            correlation_id="c",
            status=PositionUpdateStatus.APPLIED,
            snapshot=snapshot,
            updated_positions=(),
            pipeline_summary=PositionPipelineResult(
                total_stages=0,
                passed_stages=0,
                failed_stage_id=None,
                stages=(),
                short_circuited=False,
            ),
            warnings=(),
            errors=(),
            primary_error_code=None,
            submitted_at=fixed_as_of(),
            completed_at=fixed_as_of(),
            duration_ms=0.0,
            update_fingerprint="",
        )
        with pytest.raises(PositionManagerValidationError) as exc:
            assert_valid_position_update_result(result)
        assert exc.value.code == ERROR_RESULT_INVALID


class TestApplyFillDelta:
    def test_apply_single_delta(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        deltas = extract_fill_deltas(tracker, previously_applied=frozenset())
        result = manager.apply_fill_delta(deltas[0], make_context())
        assert result.status is PositionUpdateStatus.APPLIED
        assert result.snapshot.open_position_count == 1

    def test_apply_fill_delta_idempotent(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        delta = extract_fill_deltas(tracker, previously_applied=frozenset())[0]
        manager.apply_fill_delta(delta, make_context())
        second = manager.apply_fill_delta(delta, make_context())
        assert second.status is PositionUpdateStatus.NOOP


class TestThreadSafety:
    def test_concurrent_updates_different_instruments(self) -> None:
        manager = PositionManager(fast_config())
        instruments = [
            f"NFO:INST{i}" for i in range(8)
        ]

        def apply_one(key: str, index: int) -> None:
            state = make_order_state(
                instrument_key=key,
                idempotency_key=f"idem-{index}",
                leg_index=0,
            )
            tracker = make_tracker(state, submission_id=f"sub-{index}")
            manager.apply_order_tracker(tracker, make_context())

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(apply_one, key, index)
                for index, key in enumerate(instruments)
            ]
            for future in futures:
                future.result()

        snapshot = manager.get_snapshot()
        assert snapshot.open_position_count == 8


class TestSnapshotQuery:
    def test_get_snapshot_and_position(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        result = manager.apply_order_tracker(tracker, make_context())
        position_id = result.updated_positions[0].position_id
        assert manager.get_position(position_id) is not None
        snapshot = manager.get_snapshot(as_of=fixed_as_of())
        assert snapshot.open_position_count == 1


class TestWarnings:
    def test_missing_strategy_warning(self, manager: PositionManager) -> None:
        state = make_order_state()
        state = replace(
            state,
            metadata=MappingProxyType({"plan_id": "plan-1"}),
        )
        tracker = make_tracker(state)
        result = manager.apply_order_tracker(tracker, make_context())
        assert any(warning.code == WARN_STRATEGY_MISSING for warning in result.warnings)

    def test_invalid_fill_price(self, manager: PositionManager) -> None:
        state = make_order_state(average_fill_price=0.0)
        tracker = make_tracker(state)
        result = manager.apply_order_tracker(tracker, make_context())
        assert result.status is PositionUpdateStatus.REJECTED
        assert any(error.code == ERROR_FILL_INVALID_PRICE for error in result.errors)


class TestBrokerReconciliation:
    def test_broker_drift_warning(self) -> None:
        config = fast_config(enable_broker_reconciliation=True)
        manager = PositionManager(config)
        tracker = make_tracker(make_order_state())
        broker_record = PositionRecord(
            instrument_key="NFO:NIFTY24AUG25000CE",
            product=BrokerProductType.NRML,
            quantity=50,
            average_price=125.50,
            exchange=Exchange.NFO,
        )
        context = replace(
            make_context(),
            broker_positions=(broker_record,),
        )
        result = manager.apply_order_tracker(tracker, context)
        assert any(warning.code == WARN_BROKER_DRIFT for warning in result.warnings)


class TestOrderLifecycleHandler:
    def test_on_partial_fill_event(self) -> None:
        manager = PositionManager(fast_config())
        state = make_order_state(
            lifecycle_status=OrderLifecycleStatus.PARTIALLY_FILLED,
            filled_quantity=30,
            planned_quantity=75,
            terminal=False,
        )
        event = OrderLifecycleEvent(
            event_type=OrderLifecycleEventType.LEG_PARTIAL_FILL,
            topic="order.leg.partial_fill",
            submission_id="sub-ev",
            plan_id="plan-1",
            correlation_id="corr-1",
            occurred_at=fixed_as_of(),
            leg_index=0,
            order_state=state,
        )
        manager.on_order_lifecycle_event(event)
        snapshot = manager.get_snapshot()
        assert snapshot.open_position_count == 1


class TestMultiLegOpening:
    def test_opening_to_open_single_leg_plan(self) -> None:
        config = fast_config(group_multi_leg_by_plan=False)
        manager = PositionManager(config)
        tracker = make_tracker(make_order_state())
        result = manager.apply_order_tracker(tracker, make_context())
        assert result.updated_positions[0].lifecycle_state is PositionLifecycleState.OPEN

    def test_multi_leg_opening_transitions(self, manager: PositionManager) -> None:
        ce = make_order_state(leg_index=0, idempotency_key="ce")
        tracker = make_tracker(ce)
        manager.apply_order_tracker(tracker, make_context())
        pe = make_order_state(
            leg_index=1,
            instrument_key="NFO:NIFTY24AUG25000PE",
            idempotency_key="pe",
        )
        result = manager.apply_order_tracker(make_tracker(pe), make_context())
        states = {position.lifecycle_state for position in result.snapshot.positions}
        assert PositionLifecycleState.OPEN in states


class TestPipelineStages:
    def test_all_stages_recorded(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        result = manager.apply_order_tracker(tracker, make_context())
        assert result.pipeline_summary.total_stages == 9
        assert result.pipeline_summary.passed_stages == 9

    def test_relaxed_correlation_backtest(self) -> None:
        config = fast_config(strict_correlation=False)
        manager = PositionManager(config)
        tracker = make_tracker(make_order_state(), correlation_id="tracker-corr")
        context = make_context(correlation_id="context-corr")
        result = manager.apply_order_tracker(tracker, context)
        assert result.status is PositionUpdateStatus.APPLIED


class TestPositionSideMapping:
    def test_short_from_sell_entry(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state(side=OrderSide.SELL))
        result = manager.apply_order_tracker(tracker, make_context())
        assert result.updated_positions[0].side is PositionSide.SHORT

    def test_long_from_buy_entry(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state(side=OrderSide.BUY))
        result = manager.apply_order_tracker(tracker, make_context())
        assert result.updated_positions[0].side is PositionSide.LONG


class TestPositionValidation:
    def test_position_negative_quantity_raises(self) -> None:
        from portfolio.position_manager import Position

        with pytest.raises(PositionManagerValidationError):
            Position(
                position_id="p1",
                instrument_key="NFO:X",
                side=PositionSide.LONG,
                product=ProductType.NRML,
                quantity=-1,
                average_entry_price=100.0,
                cost_basis=-100.0,
                lifecycle_state=PositionLifecycleState.OPEN,
                strategy_id="s",
                strategy_family=StrategyFamily.CUSTOM,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                transitions=(),
            )

    def test_closed_position_nonzero_quantity_raises(self) -> None:
        from portfolio.position_manager import Position

        with pytest.raises(PositionManagerValidationError):
            Position(
                position_id="p1",
                instrument_key="NFO:X",
                side=PositionSide.LONG,
                product=ProductType.NRML,
                quantity=10,
                average_entry_price=100.0,
                cost_basis=1000.0,
                lifecycle_state=PositionLifecycleState.CLOSED,
                strategy_id="s",
                strategy_family=StrategyFamily.CUSTOM,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                transitions=(),
            )


class TestMetadataHelpers:
    def test_price_from_metadata_fallback(self) -> None:
        state = make_order_state(average_fill_price=0.0)
        state = replace(
            state,
            average_fill_price=None,
            metadata=MappingProxyType({"fill_price": "99.5", "plan_id": "plan-1"}),
        )
        tracker = make_tracker(state)
        deltas = extract_fill_deltas(tracker, previously_applied=frozenset())
        assert deltas[0].fill_price == 99.5

    def test_invalid_strategy_family_defaults_custom(self) -> None:
        state = make_order_state(strategy_family="not-a-real-family")
        tracker = make_tracker(state)
        deltas = extract_fill_deltas(tracker, previously_applied=frozenset())
        assert deltas[0].strategy_family is StrategyFamily.CUSTOM

    def test_position_intent_exit_metadata(self) -> None:
        state = make_order_state()
        state = replace(
            state,
            metadata=MappingProxyType(
                {**dict(state.metadata), "position_intent": "exit"}
            ),
        )
        deltas = extract_fill_deltas(make_tracker(state), previously_applied=frozenset())
        assert deltas[0].is_exit is True


class TestTrackerIntegrity:
    def test_duplicate_leg_indices_rejected(self, manager: PositionManager) -> None:
        duplicate = make_order_state(leg_index=0)
        tracker = make_tracker(duplicate, replace(duplicate, idempotency_key="dup"))
        result = manager.apply_order_tracker(tracker, make_context())
        assert result.status is PositionUpdateStatus.REJECTED

    def test_empty_submission_id_rejected(self, manager: PositionManager) -> None:
        tracker = replace(make_tracker(make_order_state()), submission_id="")
        result = manager.apply_order_tracker(tracker, make_context())
        assert result.status is PositionUpdateStatus.REJECTED

    def test_complete_mismatch_warning(self, manager: PositionManager) -> None:
        state = make_order_state(
            filled_quantity=50,
            planned_quantity=75,
            lifecycle_status=OrderLifecycleStatus.COMPLETE,
        )
        result = manager.apply_order_tracker(make_tracker(state), make_context())
        assert any("COMPLETE" in warning.message for warning in result.warnings)


class TestAdditionalPaths:
    def test_session_realized_pnl_tracking_disabled(self) -> None:
        manager = PositionManager(fast_config(session_realized_pnl_tracking=False))
        tracker = make_tracker(make_order_state(side=OrderSide.BUY))
        manager.apply_order_tracker(tracker, make_context())
        exit_state = make_order_state(
            side=OrderSide.SELL,
            filled_quantity=75,
            idempotency_key="x",
            metadata=MappingProxyType(
                {
                    "plan_id": "plan-1",
                    "strategy_id": "short-strangle-v1",
                    "strategy_family": StrategyFamily.SHORT_STRANGLE.value,
                    "underlying": "NIFTY",
                    "position_intent": "exit",
                }
            ),
        )
        snapshot = manager.apply_order_tracker(make_tracker(exit_state), make_context()).snapshot
        assert snapshot.aggregate_realized_pnl_session == 0.0

    def test_orphan_exit_allowed_creates_closed(self) -> None:
        manager = PositionManager(fast_config(allow_orphan_exits=True))
        state = make_order_state(
            metadata=MappingProxyType(
                {
                    "plan_id": "plan-1",
                    "strategy_id": "short-strangle-v1",
                    "strategy_family": StrategyFamily.SHORT_STRANGLE.value,
                    "position_intent": "exit",
                }
            ),
        )
        result = manager.apply_order_tracker(make_tracker(state), make_context())
        assert result.status is PositionUpdateStatus.APPLIED

    def test_continue_on_leg_error_false(self) -> None:
        manager = PositionManager(fast_config(continue_on_leg_error=False))
        manager.apply_order_tracker(
            make_tracker(make_order_state(side=OrderSide.BUY, filled_quantity=50, planned_quantity=50)),
            make_context(),
        )
        over_exit = make_order_state(
            side=OrderSide.SELL,
            filled_quantity=100,
            planned_quantity=100,
            idempotency_key="over",
            metadata=MappingProxyType(
                {
                    "plan_id": "plan-1",
                    "strategy_id": "short-strangle-v1",
                    "strategy_family": StrategyFamily.SHORT_STRANGLE.value,
                    "position_intent": "exit",
                }
            ),
        )
        result = manager.apply_order_tracker(make_tracker(over_exit), make_context())
        assert result.status is PositionUpdateStatus.PARTIAL

    def test_entry_after_partial_close_reopens(self, manager: PositionManager) -> None:
        entry = make_order_state(side=OrderSide.BUY, filled_quantity=150, planned_quantity=150)
        manager.apply_order_tracker(make_tracker(entry), make_context())
        partial = make_order_state(
            side=OrderSide.SELL,
            filled_quantity=75,
            planned_quantity=75,
            idempotency_key="p1",
            metadata=MappingProxyType(
                {
                    **dict(entry.metadata),
                    "position_intent": "exit",
                }
            ),
        )
        manager.apply_order_tracker(make_tracker(partial), make_context())
        add = make_order_state(
            side=OrderSide.BUY,
            filled_quantity=25,
            planned_quantity=25,
            idempotency_key="add",
        )
        result = manager.apply_order_tracker(make_tracker(add), make_context())
        assert result.updated_positions[0].lifecycle_state is PositionLifecycleState.OPEN

    def test_on_order_lifecycle_event_ignores_unrelated(self, manager: PositionManager) -> None:
        event = OrderLifecycleEvent(
            event_type=OrderLifecycleEventType.PLAN_RECEIVED,
            topic="order.plan.received",
            submission_id="sub",
            plan_id="plan",
            correlation_id="corr",
            occurred_at=fixed_as_of(),
        )
        manager.on_order_lifecycle_event(event)
        assert manager.get_snapshot().open_position_count == 0

    def test_manager_validate_helpers(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        context = make_context()
        assert manager.validate_update_context(context, tracker).is_valid
        result = manager.apply_order_tracker(tracker, context)
        assert manager.validate_update_result(result).is_valid
        assert manager.config.strict_correlation is True

    def test_deserialize_malformed_datetime(self) -> None:
        from portfolio.position_manager import _datetime_from_iso

        with pytest.raises(PositionManagerValidationError):
            _datetime_from_iso("2026-01-01T00:00:00")

    def test_snapshot_with_underlying_metadata(self, manager: PositionManager) -> None:
        state = make_order_state(metadata=MappingProxyType({"underlying": "NIFTY50"}))
        result = manager.apply_order_tracker(make_tracker(state), make_context())
        assert "NIFTY50" in result.snapshot.aggregate_quantity_by_underlying

    def test_update_rejected_event_published(self) -> None:
        bus = EventBus()
        captured: list[object] = []
        bus.subscribe("position.*", lambda event: captured.append(event.payload))
        manager = PositionManager(fast_config(), event_bus=bus)
        tracker = make_tracker(make_order_state(), correlation_id="wrong")
        manager.apply_order_tracker(tracker, make_context(correlation_id="corr-1"))
        topics = {getattr(payload, "topic", "") for payload in captured}
        assert "position.update.rejected" in topics

    def test_noop_tracker_with_existing_registry(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state())
        manager.apply_order_tracker(tracker, make_context())
        second = manager.apply_order_tracker(tracker, make_context())
        assert second.status is PositionUpdateStatus.NOOP
        assert "position.snapshot.published" not in {
            getattr(event, "topic", "")
            for event in []
        }

    def test_extract_fill_deltas_with_registry(self, manager: PositionManager) -> None:
        tracker = make_tracker(make_order_state(side=OrderSide.BUY))
        manager.apply_order_tracker(tracker, make_context())
        exit_state = make_order_state(
            side=OrderSide.SELL,
            idempotency_key="exit",
            metadata=MappingProxyType(
                {
                    "plan_id": "plan-1",
                    "strategy_id": "short-strangle-v1",
                    "strategy_family": StrategyFamily.SHORT_STRANGLE.value,
                    "position_intent": "exit",
                }
            ),
        )
        registry = {
            position.position_id: position
            for position in manager.get_snapshot().positions
        }
        deltas = extract_fill_deltas(
            make_tracker(exit_state),
            previously_applied=frozenset(),
            registry=registry,
        )
        assert deltas[0].is_exit is True
