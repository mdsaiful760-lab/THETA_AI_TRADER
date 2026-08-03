"""Unit tests for execution.order_manager."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from broker.base_broker import (
    ERROR_ORDER_REJECTED,
    ERROR_RATE_LIMIT_EXCEEDED,
    ERROR_REQUEST_TIMEOUT,
    BaseBrokerClient,
    BrokerClientError,
    BrokerOrderError,
    BrokerRateLimitError,
    BrokerSession,
    BrokerTimeoutError,
    OrderQueryRequest,
    OrderRecord,
    OrderSide as BrokerOrderSide,
    OrderStatus,
    OrderType as BrokerOrderType,
    OrderVariety,
    PlaceOrderRequest,
    PlaceOrderResult,
    ProductType as BrokerProductType,
    validate_place_order_request,
)
from core.event_bus import EventBus
from execution.execution_engine import (
    ExecutionEngine,
    ExecutionPlan,
    ExecutionPlanStatus,
    LegSequence,
    LegSequenceMode,
    OrderSide,
    OrderType,
    PlannedOrderLeg,
    ProductType,
    RetryPolicy,
    default_execution_engine_config,
    generate_idempotency_key,
    plan_fingerprint,
)
from execution.execution_engine import ContractResolutionSource
from execution.order_manager import (
    ERROR_BROKER_NOT_CONNECTED,
    ERROR_CONTEXT_CORRELATION_MISMATCH,
    ERROR_CONTEXT_NAIVE_TIMESTAMP,
    ERROR_PLAN_EXPIRED,
    ERROR_PLAN_NOT_READY,
    ERROR_PLAN_NO_LEGS,
    ERROR_RESULT_FINGERPRINT_MISMATCH,
    ERROR_SERIALIZATION_MALFORMED,
    ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
    OrderAggregateStatus,
    OrderLifecycleEventType,
    OrderLifecycleStatus,
    OrderManager,
    OrderManagerConfig,
    OrderManagerConfigurationError,
    OrderManagerSubmissionError,
    OrderManagerValidationError,
    OrderSubmissionContext,
    OrderSubmissionStatus,
    OrderState,
    OrderTracker,
    assert_valid_order_submission_result,
    compute_submission_fingerprint,
    default_order_manager_config,
    derive_aggregate_status,
    deserialize_order_submission_result,
    is_retryable,
    map_broker_error_code,
    map_leg_to_place_order_request,
    regenerate_idempotency_key,
    serialize_order_submission_result,
    validate_order_submission_result,
    validate_submission_context,
)
from strategy.signals import StrategyExecutionMode
from tests.test_base_broker import FakeBrokerClient, make_session
from tests.test_execution_engine import (
    build_approved_risk,
    fixed_as_of,
    make_contract_selection,
    make_execution_run_context,
    make_execution_sizing_hint,
)
from tests.test_strategy_evaluation_engine import FixedClock

IST = ZoneInfo("Asia/Kolkata")


def fast_config(**overrides: object) -> OrderManagerConfig:
    """Build fast deterministic test configuration."""
    defaults = {
        "enable_status_polling": False,
        "honor_sequence_delays": False,
        "poll_interval_ms": 1,
        "max_poll_attempts": 3,
    }
    defaults.update(overrides)
    base = default_order_manager_config()
    return replace(base, **defaults)


class MockBrokerClient(FakeBrokerClient):
    """Scriptable broker client extending FakeBrokerClient for order tests."""

    def __init__(
        self,
        session: BrokerSession,
        *,
        place_results: list[PlaceOrderResult | Exception] | None = None,
        fetch_records: dict[str, list[OrderRecord]] | None = None,
        connected: bool = True,
        authenticated: bool = True,
        order_placement: bool = True,
    ) -> None:
        from broker.base_broker import BrokerCapabilities

        super().__init__(
            session,
            capabilities=BrokerCapabilities(order_placement=order_placement),
        )
        self._connected = connected
        self._authenticated = authenticated
        self._place_results = list(place_results or [])
        self._fetch_records = fetch_records or {}
        self.calls: list[tuple[str, object]] = []

    def connect(self) -> None:
        self._connected = True
        self._authenticated = True
        super().connect()

    def disconnect(self) -> None:
        self._connected = False
        super().disconnect()

    def is_connected(self) -> bool:
        return self._connected

    def is_authenticated(self) -> bool:
        return self._authenticated

    def place_order(self, request: PlaceOrderRequest) -> PlaceOrderResult:
        self.calls.append(("place_order", request))
        validate_place_order_request(request)
        if self._place_results:
            outcome = self._place_results.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            result = outcome
        else:
            result = super().place_order(request)
            result = replace(result, status=OrderStatus.COMPLETE)
        record = OrderRecord(
            order_id=result.order_id,
            instrument_key=request.instrument_key,
            side=request.side,
            order_type=request.order_type,
            product=request.product,
            quantity=request.quantity,
            status=result.status,
            price=request.price,
            trigger_price=request.trigger_price,
            variety=request.variety,
            broker_order_id=result.broker_order_id,
        )
        self._orders[result.order_id] = record
        if result.broker_order_id:
            self._orders[result.broker_order_id] = record
        return result

    def cancel_order(self, request):
        self.calls.append(("cancel_order", request))
        if request.order_id not in self._orders:
            for record in self._orders.values():
                if record.broker_order_id == request.order_id:
                    request = replace(request, order_id=record.order_id)
                    break
        return super().cancel_order(request)

    def fetch_orders(self, request: OrderQueryRequest):
        self.calls.append(("fetch_orders", request))
        if request.order_id and request.order_id in self._fetch_records:
            records = self._fetch_records[request.order_id]
            if records:
                return (records.pop(0),)
            return ()
        if request.order_id and request.order_id not in self._orders:
            for record in self._orders.values():
                if record.broker_order_id == request.order_id:
                    request = OrderQueryRequest(order_id=record.order_id)
                    break
        return super().fetch_orders(request)


        return super().fetch_orders(request)


def make_submission_context(
    plan: ExecutionPlan,
    *,
    reference_time: datetime | None = None,
    execution_mode: StrategyExecutionMode | None = None,
    submission_id: str | None = None,
) -> OrderSubmissionContext:
    """Build submission context aligned with plan."""
    return OrderSubmissionContext(
        correlation_id=plan.correlation_id,
        reference_time=reference_time or fixed_as_of(),
        execution_mode=execution_mode or StrategyExecutionMode.BACKTEST,
        tags=MappingProxyType({}),
        submission_id=submission_id,
    )


def build_ready_plan(clock: FixedClock | None = None) -> ExecutionPlan:
    """Build READY execution plan via execution engine."""
    clock = clock or FixedClock()
    engine = ExecutionEngine(default_execution_engine_config(), clock=clock)
    risk, snap = build_approved_risk(clock)
    selection = make_contract_selection(correlation_id=risk.correlation_id)
    ctx = make_execution_run_context(
        risk,
        snap,
        sizing_hint=make_execution_sizing_hint(proposed_units_hint=2.0),
        contract_selection=selection,
    )
    plan = engine.plan_from_run_context(ctx)
    assert plan.status is ExecutionPlanStatus.READY
    return plan


def replan(plan: ExecutionPlan, **changes: object) -> ExecutionPlan:
    """Return plan copy with recomputed fingerprint after field changes."""
    updated = replace(plan, **changes)
    return replace(updated, plan_fingerprint=plan_fingerprint(updated))


def single_leg_plan(plan: ExecutionPlan) -> ExecutionPlan:
    """Reduce plan to one leg with valid fingerprint and sequence metadata."""
    return replan(
        plan,
        legs=(plan.legs[0],),
        sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (0,)),),
    )


@pytest.fixture
def ready_plan() -> ExecutionPlan:
    return build_ready_plan()


@pytest.fixture
def manager() -> OrderManager:
    return OrderManager(fast_config())


class TestConfiguration:
    def test_invalid_poll_interval(self) -> None:
        with pytest.raises(OrderManagerConfigurationError):
            OrderManagerConfig(poll_interval_ms=0)

    def test_default_config_factory(self) -> None:
        config = default_order_manager_config()
        assert config.strict_correlation is True
        assert config.poll_interval_ms == 500


class TestMapping:
    def test_map_limit_leg(self, ready_plan: ExecutionPlan) -> None:
        leg = ready_plan.legs[0]
        request = map_leg_to_place_order_request(leg, ready_plan)
        assert request.price == leg.limit_price_hint
        assert request.idempotency_key == leg.idempotency_key
        assert request.correlation_id == ready_plan.correlation_id

    def test_regenerate_idempotency_key(self, ready_plan: ExecutionPlan) -> None:
        leg = ready_plan.legs[0]
        key = regenerate_idempotency_key(leg, ready_plan, 2)
        assert key == f"{leg.idempotency_key}-retry-2"


class TestPlanGate:
    def test_reject_non_ready_plan(self, manager: OrderManager) -> None:
        plan = replace(build_ready_plan(), status=ExecutionPlanStatus.SKIPPED, legs=())
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = manager.submit_plan(
            plan,
            broker,
            make_submission_context(plan, execution_mode=StrategyExecutionMode.LIVE),
        )
        assert result.status is OrderSubmissionStatus.REJECTED
        assert result.primary_error_code == ERROR_PLAN_NOT_READY
        assert broker.calls == []

    def test_reject_expired_plan(self, manager: OrderManager) -> None:
        plan = build_ready_plan()
        expired = replan(plan, valid_until=fixed_as_of() - timedelta(seconds=1))
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = manager.submit_plan(
            expired,
            broker,
            make_submission_context(expired, execution_mode=StrategyExecutionMode.LIVE),
        )
        assert result.status is OrderSubmissionStatus.REJECTED
        assert result.primary_error_code == ERROR_PLAN_EXPIRED
        assert broker.calls == []

    def test_reject_empty_legs(self, manager: OrderManager) -> None:
        plan = replan(build_ready_plan(), legs=(), sequences=())
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = manager.submit_plan(
            plan,
            broker,
            make_submission_context(plan, execution_mode=StrategyExecutionMode.LIVE),
        )
        assert result.status is OrderSubmissionStatus.REJECTED
        assert result.primary_error_code == ERROR_PLAN_NO_LEGS


class TestSubmission:
    def test_submit_single_leg_complete(self, ready_plan: ExecutionPlan) -> None:
        plan = single_leg_plan(ready_plan)
        manager = OrderManager(fast_config())
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = manager.submit_plan(
            plan,
            broker,
            make_submission_context(plan),
        )
        assert result.status is OrderSubmissionStatus.COMPLETED
        assert len(broker.calls) == 1
        assert result.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.COMPLETE

    def test_submit_multi_leg_simultaneous(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = manager.submit_plan(
            ready_plan,
            broker,
            make_submission_context(ready_plan),
        )
        assert result.status is OrderSubmissionStatus.COMPLETED
        place_calls = [call for call in broker.calls if call[0] == "place_order"]
        assert len(place_calls) == len(ready_plan.legs)

    def test_broker_not_connected(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())
        broker = MockBrokerClient(make_session(), connected=False)
        result = manager.submit_plan(
            ready_plan,
            broker,
            make_submission_context(ready_plan, execution_mode=StrategyExecutionMode.LIVE),
        )
        assert result.status is OrderSubmissionStatus.REJECTED
        assert result.primary_error_code == ERROR_BROKER_NOT_CONNECTED


class TestRetry:
    def test_retry_transient_timeout(self, ready_plan: ExecutionPlan) -> None:
        single = replan(
            ready_plan,
            legs=(ready_plan.legs[0],),
            sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (0,)),),
            retry_policy=RetryPolicy(max_attempts=3, initial_backoff_ms=0),
        )
        broker = MockBrokerClient(
            make_session(),
            place_results=[
                BrokerTimeoutError("timeout", code=ERROR_REQUEST_TIMEOUT),
                PlaceOrderResult(
                    order_id="1",
                    status=OrderStatus.COMPLETE,
                    message="ok",
                    broker_order_id="broker-1",
                ),
            ],
        )
        broker.connect()
        manager = OrderManager(fast_config())
        result = manager.submit_plan(single, broker, make_submission_context(single))
        assert result.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.COMPLETE
        assert result.tracker.leg_states[0].attempt_count == 2
        assert any(item.code.endswith("RETRY_SUCCEEDED") for item in result.warnings)

    def test_retry_exhausted(self, ready_plan: ExecutionPlan) -> None:
        single = replan(
            ready_plan,
            legs=(ready_plan.legs[0],),
            sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (0,)),),
            retry_policy=RetryPolicy(max_attempts=2, initial_backoff_ms=0),
        )
        broker = MockBrokerClient(
            make_session(),
            place_results=[
                BrokerTimeoutError("timeout", code=ERROR_REQUEST_TIMEOUT),
                BrokerTimeoutError("timeout", code=ERROR_REQUEST_TIMEOUT),
            ],
        )
        broker.connect()
        manager = OrderManager(fast_config())
        result = manager.submit_plan(single, broker, make_submission_context(single))
        assert result.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.FAILED

    def test_no_retry_on_rejection(self, ready_plan: ExecutionPlan) -> None:
        single = replan(
            ready_plan,
            legs=(ready_plan.legs[0],),
            sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (0,)),),
        )
        broker = MockBrokerClient(
            make_session(),
            place_results=[
                BrokerOrderError("rejected", code=ERROR_ORDER_REJECTED),
            ],
        )
        broker.connect()
        manager = OrderManager(fast_config())
        result = manager.submit_plan(single, broker, make_submission_context(single))
        assert result.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.REJECTED
        assert len([c for c in broker.calls if c[0] == "place_order"]) == 1


class TestSequential:
    def test_sequential_abort_on_failure(self, ready_plan: ExecutionPlan) -> None:
        plan = replan(
            ready_plan,
            sequences=(
                LegSequence(
                    sequence_group=0,
                    mode=LegSequenceMode.SEQUENTIAL,
                    leg_indices=(0, 1),
                    abort_on_leg_failure=True,
                ),
            ),
            retry_policy=RetryPolicy(max_attempts=1),
        )
        broker = MockBrokerClient(
            make_session(),
            place_results=[
                PlaceOrderResult("1", OrderStatus.COMPLETE, "ok", broker_order_id="b1"),
                BrokerOrderError("reject", code=ERROR_ORDER_REJECTED),
            ],
        )
        broker.connect()
        manager = OrderManager(fast_config())
        result = manager.submit_plan(plan, broker, make_submission_context(plan))
        states = {state.leg_index: state for state in result.tracker.leg_states}
        assert states[0].lifecycle_status is OrderLifecycleStatus.COMPLETE
        assert states[1].lifecycle_status is OrderLifecycleStatus.REJECTED


class TestPartialFill:
    def test_partial_fill_with_polling(self, ready_plan: ExecutionPlan) -> None:
        single = replan(
            ready_plan,
            legs=(replace(ready_plan.legs[0], quantity=100),),
            sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (0,)),),
        )
        leg = single.legs[0]
        broker = MockBrokerClient(
            make_session(),
            place_results=[
                PlaceOrderResult("1", OrderStatus.OPEN, "open", broker_order_id="broker-1"),
            ],
        )
        partial_record = OrderRecord(
            order_id="1",
            instrument_key=leg.instrument_key,
            side=BrokerOrderSide(leg.side.value),
            order_type=BrokerOrderType(leg.order_type.value),
            product=BrokerProductType(leg.product.value),
            quantity=leg.quantity,
            status=OrderStatus.OPEN,
            raw=MappingProxyType({"filled_quantity": leg.quantity // 2}),
        )
        complete_record = replace(
            partial_record,
            status=OrderStatus.COMPLETE,
            raw=MappingProxyType({"filled_quantity": leg.quantity}),
        )
        broker._fetch_records["broker-1"] = [partial_record, complete_record]
        broker.connect()
        config = fast_config(enable_status_polling=True, max_poll_attempts=5, poll_interval_ms=1)
        manager = OrderManager(config)
        result = manager.submit_plan(single, broker, make_submission_context(single))
        assert result.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.COMPLETE
        assert result.tracker.leg_states[0].filled_quantity == leg.quantity


class TestCancellation:
    def test_cancel_open_leg(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())
        broker = MockBrokerClient(make_session())
        broker.connect()
        submit = manager.submit_plan(
            single_leg_plan(ready_plan),
            broker,
            make_submission_context(ready_plan),
        )
        open_state = replace(
            submit.tracker.leg_states[0],
            lifecycle_status=OrderLifecycleStatus.OPEN,
            terminal=False,
            terminal_at=None,
        )
        tracker = replace(submit.tracker, leg_states=(open_state,))
        cancel = manager.cancel_plan(tracker, broker)
        assert cancel.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.CANCELLED


class TestEvents:
    def test_events_published_on_submit(self, ready_plan: ExecutionPlan) -> None:
        bus = EventBus()
        events: list[str] = []
        bus.subscribe("order.*", lambda envelope: events.append(envelope.topic))
        manager = OrderManager(fast_config(), event_bus=bus)
        broker = MockBrokerClient(make_session())
        broker.connect()
        manager.submit_plan(
            single_leg_plan(ready_plan),
            broker,
            make_submission_context(ready_plan),
        )
        assert "order.plan.received" in events
        assert "order.plan.completed" in events

    def test_no_events_when_bus_none(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config(), event_bus=None)
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = manager.submit_plan(
            single_leg_plan(ready_plan),
            broker,
            make_submission_context(ready_plan),
        )
        assert result.status is OrderSubmissionStatus.COMPLETED


class TestDeterminism:
    def test_submission_fingerprint_stable(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())
        broker_a = MockBrokerClient(make_session())
        broker_b = MockBrokerClient(make_session())
        broker_a.connect()
        broker_b.connect()
        ctx = make_submission_context(ready_plan, submission_id="sub-fixed-001")
        single = single_leg_plan(ready_plan)
        result_a = manager.submit_plan(single, broker_a, ctx)
        result_b = manager.submit_plan(single, broker_b, ctx)
        assert result_a.submission_fingerprint == result_b.submission_fingerprint
        assert result_a.submission_id == result_b.submission_id


class TestThreadSafety:
    def test_concurrent_different_plans(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())

        def submit_one(submission_id: str) -> str:
            broker = MockBrokerClient(make_session())
            broker.connect()
            single = replace(
                ready_plan,
                plan_id=f"{ready_plan.plan_id}-{submission_id}",
                legs=(ready_plan.legs[0],),
                sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (0,)),),
            )
            ctx = make_submission_context(single, submission_id=submission_id)
            result = manager.submit_plan(single, broker, ctx)
            return result.submission_id

        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(submit_one, [f"run-{index}" for index in range(4)]))
        assert len(set(ids)) == 4

    def test_tracker_immutable_after_return(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = manager.submit_plan(
            single_leg_plan(ready_plan),
            broker,
            make_submission_context(ready_plan),
        )
        with pytest.raises(AttributeError):
            result.tracker.plan_id = "mutated"  # type: ignore[misc]


class TestSerialization:
    def test_submission_result_round_trip(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = manager.submit_plan(
            single_leg_plan(ready_plan),
            broker,
            make_submission_context(ready_plan),
        )
        payload = serialize_order_submission_result(result)
        restored = deserialize_order_submission_result(payload)
        assert restored.submission_id == result.submission_id
        assert restored.status == result.status
        assert restored.tracker.aggregate_status == result.tracker.aggregate_status

    def test_unsupported_schema_version_raises(self) -> None:
        with pytest.raises(OrderManagerValidationError) as exc:
            deserialize_order_submission_result('{"schema_version":"9.9.9"}')
        assert exc.value.code == ERROR_SERIALIZATION_UNSUPPORTED_VERSION

    def test_malformed_json_raises(self) -> None:
        with pytest.raises(OrderManagerValidationError) as exc:
            deserialize_order_submission_result("{bad")
        assert exc.value.code == ERROR_SERIALIZATION_MALFORMED


class TestValidationHelpers:
    def test_naive_timestamp(self, ready_plan: ExecutionPlan) -> None:
        ctx = OrderSubmissionContext(
            correlation_id=ready_plan.correlation_id,
            reference_time=datetime(2026, 8, 3, 10, 0, 0),
            execution_mode=StrategyExecutionMode.LIVE,
        )
        result = validate_submission_context(ctx, ready_plan, default_order_manager_config())
        assert not result.is_valid
        assert any(item.code == ERROR_CONTEXT_NAIVE_TIMESTAMP for item in result.errors)

    def test_correlation_mismatch(self, ready_plan: ExecutionPlan) -> None:
        ctx = OrderSubmissionContext(
            correlation_id="other",
            reference_time=fixed_as_of(),
            execution_mode=StrategyExecutionMode.LIVE,
        )
        result = validate_submission_context(ctx, ready_plan, default_order_manager_config())
        assert any(item.code == ERROR_CONTEXT_CORRELATION_MISMATCH for item in result.errors)

    def test_derive_aggregate_status(self) -> None:
        planned = OrderState(
            leg_index=0,
            sequence_group=0,
            instrument_key="NFO:TEST",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product=ProductType.NRML,
            planned_quantity=1,
            lifecycle_status=OrderLifecycleStatus.COMPLETE,
            idempotency_key="k",
            filled_quantity=1,
            remaining_quantity=0,
            terminal=True,
            terminal_at=fixed_as_of(),
        )
        assert derive_aggregate_status((planned,)) is OrderAggregateStatus.ALL_COMPLETE

    def test_is_retryable(self) -> None:
        error = BrokerRateLimitError("rate", code=ERROR_RATE_LIMIT_EXCEEDED)
        policy = RetryPolicy()
        assert is_retryable(error, policy)
        assert map_broker_error_code(ERROR_REQUEST_TIMEOUT) == "BROKER.TRANSIENT.TIMEOUT"

    def test_get_tracker(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())
        broker = MockBrokerClient(make_session())
        broker.connect()
        ctx = make_submission_context(ready_plan, submission_id="tracker-test")
        single = single_leg_plan(ready_plan)
        result = manager.submit_plan(single, broker, ctx)
        assert manager.get_tracker("tracker-test") == result.tracker
        assert manager.get_tracker("missing") is None

    def test_assert_valid_result(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = manager.submit_plan(
            single_leg_plan(ready_plan),
            broker,
            make_submission_context(ready_plan),
        )
        single = single_leg_plan(ready_plan)
        assert_valid_order_submission_result(result)
        fingerprint = compute_submission_fingerprint(single, result.tracker, fast_config())
        assert fingerprint == result.submission_fingerprint

    def test_analysis_dry_run(self, ready_plan: ExecutionPlan) -> None:
        config = fast_config(allow_analysis_dry_run=True, require_broker_connected=False)
        manager = OrderManager(config)
        ctx = make_submission_context(single_leg_plan(ready_plan), execution_mode=StrategyExecutionMode.ANALYSIS)
        result = manager.submit_plan(
            single_leg_plan(ready_plan),
            MockBrokerClient(make_session(), connected=False),
            ctx,
        )
        assert result.tracker.leg_states[0].broker_order_id == "dry-run-0"

    def test_fake_broker_integration(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config(enable_status_polling=True, poll_interval_ms=1, max_poll_attempts=2))
        broker = FakeBrokerClient(make_session())
        broker.connect()
        single = single_leg_plan(ready_plan)
        result = manager.submit_plan(
            single,
            broker,
            make_submission_context(single, execution_mode=StrategyExecutionMode.LIVE),
        )
        assert result.status in {OrderSubmissionStatus.SUBMITTED, OrderSubmissionStatus.COMPLETED}


class TestExtendedCoverage:
    def test_order_state_validation_errors(self) -> None:
        base = OrderState(
            leg_index=0,
            sequence_group=0,
            instrument_key="NFO:TEST",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product=ProductType.NRML,
            planned_quantity=10,
            lifecycle_status=OrderLifecycleStatus.PLANNED,
            idempotency_key="key",
            filled_quantity=0,
            remaining_quantity=10,
        )
        with pytest.raises(OrderManagerValidationError):
            replace(base, remaining_quantity=5)
        with pytest.raises(OrderManagerValidationError):
            replace(base, filled_quantity=20)
        with pytest.raises(OrderManagerValidationError):
            replace(base, terminal=True)

    def test_config_max_poll_invalid(self) -> None:
        with pytest.raises(OrderManagerConfigurationError):
            OrderManagerConfig(max_poll_attempts=0)

    def test_validate_leg_mapping_errors(self) -> None:
        from execution.order_manager import validate_leg_mapping

        leg = PlannedOrderLeg(
            leg_index=0,
            sequence_group=0,
            instrument_key="",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product=ProductType.NRML,
            quantity=0,
            idempotency_key="",
            resolution_source=ContractResolutionSource.CONTRACT_SELECTION,
        )
        result = validate_leg_mapping(leg)
        assert len(result.errors) >= 3

    def test_derive_submission_status_variants(self) -> None:
        from execution.order_manager import derive_submission_status

        planned = OrderState(
            leg_index=0,
            sequence_group=0,
            instrument_key="NFO:TEST",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product=ProductType.NRML,
            planned_quantity=1,
            lifecycle_status=OrderLifecycleStatus.PLANNED,
            idempotency_key="k",
            filled_quantity=0,
            remaining_quantity=1,
        )
        tracker = OrderTracker(
            submission_id="s",
            plan_id="p",
            correlation_id="c",
            plan_fingerprint="fp",
            leg_states=(planned,),
            aggregate_status=OrderAggregateStatus.PENDING,
            sequence_results=(),
            started_at=fixed_as_of(),
            completed_at=fixed_as_of(),
            tracker_fingerprint="tf",
        )
        assert derive_submission_status(tracker, pre_rejected=True) is OrderSubmissionStatus.REJECTED
        assert derive_submission_status(tracker) is OrderSubmissionStatus.SUBMITTED

    def test_broker_auth_error_on_submit(self, ready_plan: ExecutionPlan) -> None:
        from broker.base_broker import BrokerAuthenticationError

        single = single_leg_plan(ready_plan)
        broker = MockBrokerClient(
            make_session(),
            place_results=[BrokerAuthenticationError("auth failed")],
        )
        broker.connect()
        manager = OrderManager(fast_config())
        result = manager.submit_plan(single, broker, make_submission_context(single))
        assert result.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.REJECTED

    def test_broker_capability_error_raises(self, ready_plan: ExecutionPlan) -> None:
        from broker.base_broker import BrokerCapabilityError

        single = single_leg_plan(ready_plan)
        broker = MockBrokerClient(
            make_session(),
            place_results=[BrokerCapabilityError("unsupported")],
        )
        broker.connect()
        manager = OrderManager(fast_config())
        with pytest.raises(OrderManagerSubmissionError):
            manager.submit_plan(single, broker, make_submission_context(single))

    def test_sequence_skip_on_abort(self, ready_plan: ExecutionPlan) -> None:
        plan = replan(
            ready_plan,
            sequences=(
                LegSequence(
                    0,
                    LegSequenceMode.SEQUENTIAL,
                    (0, 1),
                    abort_on_leg_failure=True,
                ),
            ),
            retry_policy=RetryPolicy(max_attempts=1),
        )
        broker = MockBrokerClient(
            make_session(),
            place_results=[
                BrokerOrderError("fail", code=ERROR_ORDER_REJECTED),
            ],
        )
        broker.connect()
        result = OrderManager(fast_config()).submit_plan(
            plan,
            broker,
            make_submission_context(plan),
        )
        states = {item.leg_index: item for item in result.tracker.leg_states}
        assert states[1].lifecycle_status is OrderLifecycleStatus.SKIPPED

    def test_cancel_failure_path(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())
        broker = MockBrokerClient(make_session())
        broker.connect()
        submit = manager.submit_plan(
            single_leg_plan(ready_plan),
            broker,
            make_submission_context(ready_plan),
        )
        open_state = replace(
            submit.tracker.leg_states[0],
            lifecycle_status=OrderLifecycleStatus.OPEN,
            terminal=False,
            terminal_at=None,
        )
        tracker = replace(submit.tracker, leg_states=(open_state,))

        def fail_cancel(request):
            raise BrokerClientError("cancel failed", code="ORDER_MANAGER.LEG.CANCEL_FAILED", recoverable=False)

        broker.cancel_order = fail_cancel  # type: ignore[method-assign]
        cancel = manager.cancel_plan(tracker, broker)
        assert cancel.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.FAILED

    def test_stale_limit_hint_warning(self, ready_plan: ExecutionPlan) -> None:
        leg = replace(ready_plan.legs[0], metadata=MappingProxyType({"limit_hint_stale": "true"}))
        plan = replan(ready_plan, legs=(leg,), sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (0,)),))
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = OrderManager(fast_config()).submit_plan(
            plan,
            broker,
            make_submission_context(plan),
        )
        assert any("STALE" in warning.code for warning in result.warnings)

    def test_near_expiry_warning_on_valid_plan(self, ready_plan: ExecutionPlan) -> None:
        ref = fixed_as_of()
        plan = replan(ready_plan, valid_until=ref + timedelta(seconds=10))
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = OrderManager(fast_config()).submit_plan(
            plan,
            broker,
            make_submission_context(plan, reference_time=ref),
        )
        assert any("NEAR_EXPIRY" in warning.code for warning in result.warnings)

    def test_order_state_serialization_round_trip(self, ready_plan: ExecutionPlan) -> None:
        from execution.order_manager import _order_state_from_dict, _order_state_to_dict

        manager = OrderManager(fast_config())
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = manager.submit_plan(
            single_leg_plan(ready_plan),
            broker,
            make_submission_context(ready_plan),
        )
        payload = _order_state_to_dict(result.tracker.leg_states[0])
        restored = _order_state_from_dict(payload)
        assert restored.leg_index == result.tracker.leg_states[0].leg_index

    def test_config_fingerprint_and_helpers(self) -> None:
        from execution.order_manager import config_fingerprint, map_broker_error_code

        config = default_order_manager_config()
        assert config_fingerprint(config)
        assert map_broker_error_code(ERROR_RATE_LIMIT_EXCEEDED) == "BROKER.TRANSIENT.RATE_LIMIT"

    def test_invalid_leg_indices_in_plan(self, ready_plan: ExecutionPlan) -> None:
        bad_leg = replace(ready_plan.legs[0], leg_index=5)
        plan = replan(ready_plan, legs=(bad_leg,), sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (5,)),))
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = OrderManager(fast_config()).submit_plan(
            plan,
            broker,
            make_submission_context(plan),
        )
        assert result.status is OrderSubmissionStatus.REJECTED
        assert result.primary_error_code == "ORDER_MANAGER.PLAN.INVALID_LEGS"

    def test_aggregate_status_branches(self) -> None:
        from execution.order_manager import derive_aggregate_status

        def make_state(status: OrderLifecycleStatus, *, terminal: bool = True) -> OrderState:
            return OrderState(
                leg_index=0,
                sequence_group=0,
                instrument_key="NFO:T",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                product=ProductType.NRML,
                planned_quantity=1,
                lifecycle_status=status,
                idempotency_key="k",
                filled_quantity=1 if status is OrderLifecycleStatus.COMPLETE else 0,
                remaining_quantity=0 if status is OrderLifecycleStatus.COMPLETE else 1,
                terminal=terminal,
                terminal_at=fixed_as_of() if terminal else None,
            )

        assert derive_aggregate_status((make_state(OrderLifecycleStatus.CANCELLED),)) is OrderAggregateStatus.ALL_CANCELLED
        assert derive_aggregate_status((make_state(OrderLifecycleStatus.FAILED),)) is OrderAggregateStatus.ALL_FAILED
        assert derive_aggregate_status(()) is OrderAggregateStatus.PENDING

    def test_leg_submission_timeout(self, ready_plan: ExecutionPlan) -> None:
        single = replan(
            ready_plan,
            legs=(ready_plan.legs[0],),
            sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (0,)),),
            timeout_policy=replace(ready_plan.timeout_policy, leg_submission_timeout_ms=0),
            retry_policy=RetryPolicy(max_attempts=5, initial_backoff_ms=1000),
        )
        broker = MockBrokerClient(
            make_session(),
            place_results=[BrokerTimeoutError("timeout", code=ERROR_REQUEST_TIMEOUT)] * 5,
        )
        broker.connect()
        result = OrderManager(fast_config()).submit_plan(
            single,
            broker,
            make_submission_context(single),
        )
        assert result.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.TIMEOUT

    def test_non_retryable_request_invalid(self, ready_plan: ExecutionPlan) -> None:
        from broker.base_broker import BrokerRequestError, ERROR_REQUEST_INVALID

        single = single_leg_plan(ready_plan)
        broker = MockBrokerClient(
            make_session(),
            place_results=[BrokerRequestError("bad", code=ERROR_REQUEST_INVALID)],
        )
        broker.connect()
        result = OrderManager(fast_config()).submit_plan(
            single,
            broker,
            make_submission_context(single),
        )
        assert result.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.REJECTED

    def test_cancel_terminal_leg_noop(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())
        broker = MockBrokerClient(make_session())
        broker.connect()
        submit = manager.submit_plan(
            single_leg_plan(ready_plan),
            broker,
            make_submission_context(ready_plan),
        )
        cancel = manager.cancel_plan(submit.tracker, broker)
        assert cancel.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.COMPLETE

    def test_invalid_sequence_reference(self, ready_plan: ExecutionPlan) -> None:
        plan = replan(
            ready_plan,
            sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (99,)),),
        )
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = OrderManager(fast_config()).submit_plan(
            plan,
            broker,
            make_submission_context(plan),
        )
        assert result.status is OrderSubmissionStatus.REJECTED

        broker = MockBrokerClient(make_session(), order_placement=False)
        broker.connect()
        result = OrderManager(fast_config()).submit_plan(
            single_leg_plan(ready_plan),
            broker,
            make_submission_context(ready_plan, execution_mode=StrategyExecutionMode.LIVE),
        )
        assert result.primary_error_code == "ORDER_MANAGER.BROKER.PLACEMENT_UNSUPPORTED"

    def test_poll_timeout_warning(self, ready_plan: ExecutionPlan) -> None:
        single = single_leg_plan(ready_plan)
        broker = MockBrokerClient(
            make_session(),
            place_results=[
                PlaceOrderResult("1", OrderStatus.OPEN, "open", broker_order_id="broker-1"),
            ],
        )
        broker.connect()
        config = fast_config(enable_status_polling=True, max_poll_attempts=1, poll_interval_ms=1)
        result = OrderManager(config).submit_plan(
            single,
            broker,
            make_submission_context(single, execution_mode=StrategyExecutionMode.LIVE),
        )
        assert any("POLL.TIMEOUT" in item.code for item in result.warnings)

    def test_idempotency_regenerate_on_retry(self, ready_plan: ExecutionPlan) -> None:
        single = replan(
            ready_plan,
            legs=(ready_plan.legs[0],),
            sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (0,)),),
            retry_policy=RetryPolicy(
                max_attempts=3,
                initial_backoff_ms=0,
                idempotency_regenerate_on_retry=True,
            ),
        )
        broker = MockBrokerClient(
            make_session(),
            place_results=[
                BrokerTimeoutError("timeout", code=ERROR_REQUEST_TIMEOUT),
                PlaceOrderResult("2", OrderStatus.COMPLETE, "ok", broker_order_id="broker-2"),
            ],
        )
        broker.connect()
        result = OrderManager(fast_config()).submit_plan(
            single,
            broker,
            make_submission_context(single),
        )
        place_calls = [call for call in broker.calls if call[0] == "place_order"]
        keys = [call[1].idempotency_key for call in place_calls]
        assert len(keys) == 2
        assert keys[0] != keys[1]

    def test_derive_submission_partial_and_aggregate(self) -> None:
        from execution.order_manager import derive_aggregate_status, derive_submission_status

        complete = OrderState(
            leg_index=0,
            sequence_group=0,
            instrument_key="NFO:A",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product=ProductType.NRML,
            planned_quantity=1,
            lifecycle_status=OrderLifecycleStatus.COMPLETE,
            idempotency_key="k0",
            filled_quantity=1,
            remaining_quantity=0,
            terminal=True,
            terminal_at=fixed_as_of(),
        )
        failed = replace(
            complete,
            leg_index=1,
            lifecycle_status=OrderLifecycleStatus.FAILED,
            filled_quantity=0,
            remaining_quantity=1,
            idempotency_key="k1",
            last_error_code="ERR",
        )
        tracker = OrderTracker(
            submission_id="s",
            plan_id="p",
            correlation_id="c",
            plan_fingerprint="fp",
            leg_states=(complete, failed),
            aggregate_status=derive_aggregate_status((complete, failed)),
            sequence_results=(),
            started_at=fixed_as_of(),
            completed_at=fixed_as_of(),
            tracker_fingerprint="tf",
        )
        assert derive_submission_status(tracker) is OrderSubmissionStatus.PARTIAL
        assert tracker.aggregate_status is OrderAggregateStatus.MIXED_TERMINAL

        in_flight = replace(complete, terminal=False, terminal_at=None, lifecycle_status=OrderLifecycleStatus.OPEN)
        assert derive_aggregate_status((in_flight,)) is OrderAggregateStatus.IN_FLIGHT

    def test_poll_rejected_and_cancelled_status(self, ready_plan: ExecutionPlan) -> None:
        single = single_leg_plan(ready_plan)
        broker = MockBrokerClient(
            make_session(),
            place_results=[
                PlaceOrderResult("1", OrderStatus.OPEN, "open", broker_order_id="broker-1"),
            ],
        )
        leg = single.legs[0]
        rejected = OrderRecord(
            order_id="1",
            instrument_key=leg.instrument_key,
            side=BrokerOrderSide(leg.side.value),
            order_type=BrokerOrderType(leg.order_type.value),
            product=BrokerProductType(leg.product.value),
            quantity=leg.quantity,
            status=OrderStatus.REJECTED,
        )
        broker._fetch_records["broker-1"] = [rejected]
        broker.connect()
        config = fast_config(enable_status_polling=True, max_poll_attempts=2, poll_interval_ms=1)
        result = OrderManager(config).submit_plan(
            single,
            broker,
            make_submission_context(single, execution_mode=StrategyExecutionMode.LIVE),
        )
        assert result.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.REJECTED

    def test_sl_leg_mapping_failure(self, ready_plan: ExecutionPlan) -> None:
        from execution.order_manager import ERROR_MAP_MISSING_TRIGGER_PRICE, validate_leg_mapping

        sl_leg = replace(
            ready_plan.legs[0],
            order_type=OrderType.SL,
            limit_price_hint=None,
            trigger_price_hint=None,
        )
        result = validate_leg_mapping(sl_leg)
        assert any(error.code == ERROR_MAP_MISSING_TRIGGER_PRICE for error in result.errors)

    def test_broker_status_helpers(self) -> None:
        from execution.order_manager import _broker_status_to_lifecycle, _extract_filled_quantity

        assert (
            _broker_status_to_lifecycle(
                OrderStatus.COMPLETE,
                planned_quantity=10,
                filled_quantity=5,
            )
            is OrderLifecycleStatus.PARTIALLY_FILLED
        )
        result = PlaceOrderResult(
            "1",
            OrderStatus.COMPLETE,
            "ok",
            raw=MappingProxyType({"filled_quantity": "bad"}),
        )
        assert _extract_filled_quantity(result, 10) == 10

    def test_backtest_finalize_publishes_complete(self, ready_plan: ExecutionPlan) -> None:
        bus = EventBus()
        topics: list[str] = []
        bus.subscribe("order.leg.complete", lambda envelope: topics.append(envelope.topic))
        single = single_leg_plan(ready_plan)
        broker = MockBrokerClient(
            make_session(),
            place_results=[
                PlaceOrderResult("1", OrderStatus.OPEN, "open", broker_order_id="broker-1"),
            ],
        )
        broker.connect()
        config = fast_config(enable_status_polling=False)
        OrderManager(config, event_bus=bus).submit_plan(
            single,
            broker,
            make_submission_context(single, execution_mode=StrategyExecutionMode.BACKTEST),
        )
        assert "order.leg.complete" in topics

    def test_derive_submission_cancelled_and_failed(self) -> None:
        from execution.order_manager import derive_submission_status

        def state(status: OrderLifecycleStatus) -> OrderState:
            return OrderState(
                leg_index=0,
                sequence_group=0,
                instrument_key="NFO:T",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                product=ProductType.NRML,
                planned_quantity=1,
                lifecycle_status=status,
                idempotency_key="k",
                filled_quantity=0,
                remaining_quantity=1,
                terminal=True,
                terminal_at=fixed_as_of(),
            )

        for lifecycle, expected in (
            (OrderLifecycleStatus.CANCELLED, OrderSubmissionStatus.CANCELLED),
            (OrderLifecycleStatus.FAILED, OrderSubmissionStatus.FAILED),
        ):
            leg = state(lifecycle)
            tracker = OrderTracker(
                submission_id="s",
                plan_id="p",
                correlation_id="c",
                plan_fingerprint="fp",
                leg_states=(leg,),
                aggregate_status=derive_aggregate_status((leg,)),
                sequence_results=(),
                started_at=fixed_as_of(),
                completed_at=fixed_as_of(),
                tracker_fingerprint="tf",
            )
            assert derive_submission_status(tracker) is expected

    def test_poll_open_event_and_backtest_partial_terminal(self, ready_plan: ExecutionPlan) -> None:
        single = replan(
            ready_plan,
            legs=(replace(ready_plan.legs[0], quantity=10),),
            sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (0,)),),
        )
        leg = single.legs[0]
        broker = MockBrokerClient(
            make_session(),
            place_results=[
                PlaceOrderResult("1", OrderStatus.OPEN, "open", broker_order_id="broker-1"),
            ],
        )
        partial = OrderRecord(
            order_id="1",
            instrument_key=leg.instrument_key,
            side=BrokerOrderSide(leg.side.value),
            order_type=BrokerOrderType(leg.order_type.value),
            product=BrokerProductType(leg.product.value),
            quantity=leg.quantity,
            status=OrderStatus.OPEN,
            raw=MappingProxyType({"filled_quantity": 4}),
        )
        broker._fetch_records["broker-1"] = [partial]
        broker.connect()
        config = fast_config(enable_status_polling=True, max_poll_attempts=1, poll_interval_ms=1)
        result = OrderManager(config).submit_plan(
            single,
            broker,
            make_submission_context(single, execution_mode=StrategyExecutionMode.BACKTEST),
        )
        assert result.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.COMPLETE

    def test_cancel_pending_when_not_immediately_cancelled(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())
        broker = MockBrokerClient(make_session())
        broker.connect()
        submit = manager.submit_plan(
            single_leg_plan(ready_plan),
            broker,
            make_submission_context(ready_plan),
        )
        open_state = replace(
            submit.tracker.leg_states[0],
            lifecycle_status=OrderLifecycleStatus.OPEN,
            terminal=False,
            terminal_at=None,
        )
        tracker = replace(submit.tracker, leg_states=(open_state,))

        def pending_cancel(request):
            record = broker._orders[request.order_id]
            return replace(record, status=OrderStatus.OPEN)

        broker.cancel_order = pending_cancel  # type: ignore[method-assign]
        cancel = manager.cancel_plan(tracker, broker)
        assert cancel.tracker.leg_states[0].lifecycle_status is OrderLifecycleStatus.CANCEL_PENDING

    def test_poll_lifecycle_events(self, ready_plan: ExecutionPlan) -> None:
        bus = EventBus()
        topics: list[str] = []
        bus.subscribe("order.leg.*", lambda envelope: topics.append(envelope.topic))
        single = single_leg_plan(ready_plan)
        leg = single.legs[0]
        broker = MockBrokerClient(
            make_session(),
            place_results=[
                PlaceOrderResult("1", OrderStatus.PENDING, "pending", broker_order_id="broker-1"),
            ],
        )
        open_record = OrderRecord(
            order_id="1",
            instrument_key=leg.instrument_key,
            side=BrokerOrderSide(leg.side.value),
            order_type=BrokerOrderType(leg.order_type.value),
            product=BrokerProductType(leg.product.value),
            quantity=leg.quantity,
            status=OrderStatus.OPEN,
        )
        cancelled_record = replace(open_record, status=OrderStatus.CANCELLED)
        broker._fetch_records["broker-1"] = [open_record, cancelled_record]
        broker.connect()
        config = fast_config(enable_status_polling=True, max_poll_attempts=3, poll_interval_ms=1)
        OrderManager(config, event_bus=bus).submit_plan(
            single,
            broker,
            make_submission_context(single, execution_mode=StrategyExecutionMode.LIVE),
        )
        assert "order.leg.open" in topics
        assert "order.leg.cancelled" in topics

    def test_filled_quantity_out_of_range(self) -> None:
        base = OrderState(
            leg_index=0,
            sequence_group=0,
            instrument_key="NFO:TEST",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product=ProductType.NRML,
            planned_quantity=10,
            lifecycle_status=OrderLifecycleStatus.PLANNED,
            idempotency_key="key",
            filled_quantity=0,
            remaining_quantity=10,
        )
        with pytest.raises(OrderManagerValidationError):
            replace(base, filled_quantity=-1)

    def test_duplicate_leg_indices_rejected(self, ready_plan: ExecutionPlan) -> None:
        duplicate = replace(ready_plan.legs[0], leg_index=0)
        plan = replan(
            ready_plan,
            legs=(duplicate, duplicate),
            sequences=(LegSequence(0, LegSequenceMode.SIMULTANEOUS, (0,)),),
        )
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = OrderManager(fast_config()).submit_plan(
            plan,
            broker,
            make_submission_context(plan),
        )
        assert result.status is OrderSubmissionStatus.REJECTED

    def test_manager_validate_wrappers(self, ready_plan: ExecutionPlan) -> None:
        manager = OrderManager(fast_config())
        ctx = make_submission_context(ready_plan)
        assert manager.validate_submission_context(ctx, ready_plan).is_valid
        broker = MockBrokerClient(make_session())
        broker.connect()
        result = manager.submit_plan(single_leg_plan(ready_plan), broker, ctx)
        assert manager.validate_submission_result(result).is_valid

