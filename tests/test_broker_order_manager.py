"""Deterministic unit tests for the broker order manager."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from broker import broker_order_manager as bom
from broker.broker_order_manager import (
    AmbiguousTransportError,
    BrokerExecution,
    BrokerOrder,
    BrokerOrderManager,
    BrokerOrderManagerConfig,
    BrokerOrderManagerConfigurationError,
    BrokerOrderManagerError,
    BrokerOrderManagerSerializationError,
    BrokerOrderManagerValidationError,
    BrokerOrderResult,
    BrokerOrderStatus,
    BrokerRejectedError,
    Exchange,
    ModifyOrderRequest,
    OrderSide,
    OrderType,
    PlaceOrderRequest,
    ProductType,
    RetryableTransportError,
    Validity,
    canonicalize_place_request,
    default_broker_order_manager_config,
    is_tick_aligned,
    normalize_broker_status,
    to_kite_exchange,
    to_kite_order_type,
    to_kite_product,
)

NOW = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)


class FakeBrokerOrderTransport:
    """Scriptable in-memory transport conforming to the manager protocol."""

    def __init__(self, scripted=()):
        self.scripted = deque(scripted)
        self.calls: list[str] = []
        self.orders: dict[str, dict] = {}
        self.trades: dict[str, list] = {}
        self.lookup = None
        self.find_raises: Exception | None = None
        self.place_gate: threading.Event | None = None
        self.place_started: threading.Event | None = None

    def _next(self, name, default=None):
        self.calls.append(name)
        item = self.scripted.popleft() if self.scripted else default
        if isinstance(item, Exception):
            raise item
        return item

    def place_order(self, request):
        if self.place_started is not None:
            self.place_started.set()
        if self.place_gate is not None:
            self.place_gate.wait(timeout=2)
        item = self._next("place", raw(request))
        if isinstance(item, Mapping) and item.get("order_id") is not None:
            self.orders[str(item["order_id"])] = item
        return item

    def modify_order(self, order_id, request):
        return self._next("modify", self.orders.get(order_id, raw()))

    def cancel_order(self, order_id, variety):
        value = self._next(
            "cancel",
            {**self.orders.get(order_id, raw()), "order_id": order_id, "status": "CANCELLED"},
        )
        self.orders[order_id] = value
        return value

    def fetch_order(self, order_id):
        return self._next("fetch", self.orders[order_id])

    def fetch_trades(self, order_id):
        self.calls.append("trades")
        return self.trades.get(order_id, ())

    def find_by_client_order_id(self, client_order_id):
        self.calls.append("find")
        if self.find_raises is not None:
            raise self.find_raises
        return self.lookup


def raw(request=None, **extra):
    data = {
        "order_id": "OID-1",
        "status": "OPEN",
        "quantity": 10,
        "filled_quantity": 0,
        "average_price": None,
        "price": None,
        "trigger_price": None,
        "order_timestamp": NOW.isoformat(),
        "exchange_timestamp": NOW.isoformat(),
    }
    if request:
        data.update({
            "client_order_id": request.client_order_id,
            "instrument_token": request.instrument_token,
            "tradingsymbol": request.trading_symbol,
            "exchange": request.exchange.value,
            "transaction_type": request.side.value,
            "order_type": request.order_type.value.replace("_", "-"),
            "product": request.product.value,
            "validity": request.validity.value,
            "quantity": request.quantity,
            "price": None if request.price is None else str(request.price),
            "trigger_price": None if request.trigger_price is None else str(request.trigger_price),
        })
    data.update(extra)
    return data


def request(**changes):
    values = dict(
        client_order_id="cid_1",
        instrument_token=123,
        trading_symbol="NIFTY",
        exchange=Exchange.NFO,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product=ProductType.MIS,
        validity=Validity.DAY,
        quantity=10,
        tick_size=Decimal("0.05"),
    )
    values.update(changes)
    return PlaceOrderRequest(**values)


def manager(transport, **config):
    return BrokerOrderManager(
        BrokerOrderManagerConfig(**config),
        transport=transport,
        clock=lambda: NOW,
        sleeper=lambda _: None,
        monotonic=lambda: 1.0,
    )


@pytest.mark.parametrize(
    "kind, price, trigger",
    [
        (OrderType.MARKET, None, None),
        (OrderType.LIMIT, Decimal("100.00"), None),
        (OrderType.SL, Decimal("104.00"), Decimal("103.50")),
        (OrderType.SL_M, None, Decimal("103.50")),
    ],
)
def test_valid_order_types(kind, price, trigger):
    result = manager(FakeBrokerOrderTransport()).place(
        request(order_type=kind, price=price, trigger_price=trigger)
    )
    assert result.success
    assert result.order.status is BrokerOrderStatus.OPEN


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"quantity": 0}, "BOM.VALIDATION.QUANTITY"),
        ({"quantity": True}, "BOM.VALIDATION.QUANTITY"),
        ({"order_type": OrderType.LIMIT, "price": Decimal("1.01")}, "BOM.VALIDATION.PRICE"),
        ({"exchange": Exchange.NSE, "product": ProductType.NRML}, "BOM.VALIDATION.PRODUCT"),
        ({"exchange": Exchange.NFO, "product": ProductType.CNC}, "BOM.VALIDATION.PRODUCT"),
        ({"client_order_id": "bad id"}, "BOM.VALIDATION.CLIENT_ORDER_ID"),
        ({"instrument_token": 0}, "BOM.VALIDATION.INSTRUMENT"),
        ({"trading_symbol": ""}, "BOM.VALIDATION.INSTRUMENT"),
        ({"order_type": OrderType.MARKET, "price": Decimal("1.00")}, "BOM.VALIDATION.ORDER_TYPE"),
        (
            {
                "order_type": OrderType.SL,
                "side": OrderSide.BUY,
                "price": Decimal("100.00"),
                "trigger_price": Decimal("101.00"),
            },
            "BOM.VALIDATION.TRIGGER_PRICE",
        ),
        (
            {
                "order_type": OrderType.SL,
                "side": OrderSide.SELL,
                "price": Decimal("102.00"),
                "trigger_price": Decimal("101.00"),
            },
            "BOM.VALIDATION.TRIGGER_PRICE",
        ),
    ],
)
def test_validation_prevents_transport(changes, code):
    fake = FakeBrokerOrderTransport()
    result = manager(fake).place(request(**changes))
    assert not result.success
    assert result.error_code == code
    assert not fake.calls


def test_rejection_never_retries():
    fake = FakeBrokerOrderTransport([BrokerRejectedError("no", code="BOM.BROKER.REJECTED")])
    assert manager(fake, max_attempts=3).place(request()).error_code == "BOM.BROKER.REJECTED"
    assert fake.calls == ["place"]


def test_network_retries_and_ambiguous_reconciles():
    fake = FakeBrokerOrderTransport([
        RetryableTransportError("retry", code="BOM.TRANSPORT.NETWORK"),
        raw(),
    ])
    result = manager(fake, max_attempts=2).place(request())
    assert result.success
    assert fake.calls == ["place", "place"]

    fake = FakeBrokerOrderTransport([AmbiguousTransportError("timeout", code="BOM.TRANSPORT.TIMEOUT")])
    fake.lookup = raw(request())
    assert manager(fake).place(request()).success
    assert fake.calls == ["place", "find"]


def test_ambiguous_retry_then_success_and_find_exception():
    fake = FakeBrokerOrderTransport([
        AmbiguousTransportError("timeout", code="BOM.TRANSPORT.TIMEOUT"),
        AmbiguousTransportError("timeout", code="BOM.TRANSPORT.TIMEOUT"),
    ])
    fake.lookup = None
    first = manager(fake, max_attempts=2).place(request())
    assert first.error_code == "BOM.TRANSPORT.AMBIGUOUS"
    assert fake.calls.count("find") == 2

    fake2 = FakeBrokerOrderTransport([AmbiguousTransportError("timeout", code="BOM.TRANSPORT.TIMEOUT")])
    fake2.find_raises = RuntimeError("lookup broken")
    assert manager(fake2, max_attempts=1).place(request()).error_code == "BOM.TRANSPORT.AMBIGUOUS"


def test_unresolved_ambiguous_is_not_replayed_when_budget_one():
    fake = FakeBrokerOrderTransport([AmbiguousTransportError("timeout", code="BOM.TRANSPORT.TIMEOUT")])
    result = manager(fake, max_attempts=1).place(request())
    assert result.error_code == "BOM.TRANSPORT.AMBIGUOUS"
    assert fake.calls == ["place", "find"]


def test_place_normalization_error_and_unexpected_exception():
    fake = FakeBrokerOrderTransport([{"status": "OPEN"}])  # missing order_id
    assert manager(fake).place(request()).error_code == "BOM.NORMALIZATION.RESPONSE"

    class Boom(FakeBrokerOrderTransport):
        def place_order(self, request):
            self.calls.append("place")
            raise RuntimeError("boom")

    assert manager(Boom()).place(request()).error_code == "BOM.INTERNAL.INVARIANT"

    class TypedBoom(FakeBrokerOrderTransport):
        def place_order(self, request):
            self.calls.append("place")
            raise BrokerOrderManagerValidationError("typed", code="BOM.VALIDATION.REQUEST")

    assert manager(TypedBoom()).place(request()).error_code == "BOM.VALIDATION.REQUEST"


def test_idempotency_and_conflict():
    service = manager(FakeBrokerOrderTransport())
    first = service.place(request())
    same = service.place(request())
    assert first is same
    assert service.place(request(quantity=11)).error_code == "BOM.IDEMPOTENCY.CONFLICT"
    assert canonicalize_place_request(request())[0] == 123


def test_execution_vwap_deduplication_and_regression():
    fake = FakeBrokerOrderTransport()
    service = manager(fake)
    placed = service.place(request())
    fake.trades["OID-1"] = [
        {"trade_id": "b", "quantity": 3, "average_price": "102", "fill_timestamp": NOW.isoformat(), "exchange": "NFO"},
        {"trade_id": "a", "quantity": 2, "average_price": "100", "fill_timestamp": NOW.isoformat(), "exchange": "NFO"},
        {"trade_id": "a", "quantity": 2, "average_price": "100", "fill_timestamp": NOW.isoformat(), "exchange": "NFO"},
    ]
    result = service.track_executions(placed.order.broker_order_id)
    assert result.order.filled_quantity == 5
    assert result.order.average_price == Decimal("101.2")
    fake.orders["OID-1"] = raw(request(), filled_quantity=1)
    assert service.track_executions("OID-1").error_code == "BOM.RECONCILIATION.FILL_REGRESSION"


def test_track_invalid_trade_and_complete_remaining():
    fake = FakeBrokerOrderTransport()
    service = manager(fake)
    placed = service.place(request())
    fake.trades["OID-1"] = [{"trade_id": "x", "quantity": 0, "average_price": "1"}]
    assert service.track_executions("OID-1").error_code == "BOM.NORMALIZATION.RESPONSE"

    fake.orders["OID-1"] = raw(request(), status="COMPLETE", filled_quantity=10)
    service.get_status("OID-1")
    fake.trades["OID-1"] = [
        {"trade_id": "t1", "quantity": 5, "average_price": "100", "fill_timestamp": NOW.isoformat()}
    ]
    assert service.track_executions("OID-1").error_code == "BOM.RECONCILIATION.FILL_REGRESSION"

    from dataclasses import replace

    class StaticStatus(FakeBrokerOrderTransport):
        def __init__(self, order, trades):
            super().__init__()
            self._order = order
            self.trades = {"OID-1": trades}

        def fetch_order(self, order_id):
            self.calls.append("fetch")
            return self._order

        def fetch_trades(self, order_id):
            self.calls.append("trades")
            return self.trades[order_id]

    inconsistent = replace(
        placed.order,
        status=BrokerOrderStatus.COMPLETE,
        filled_quantity=0,
        remaining_quantity=10,
        executions=(),
    )
    static = StaticStatus(
        raw(request(), status="OPEN", filled_quantity=0),
        [{"trade_id": "partial", "quantity": 1, "average_price": "100", "fill_timestamp": NOW.isoformat()}],
    )
    service2 = manager(static)
    service2._orders["OID-1"] = inconsistent
    # After refresh becomes OPEN filled 0; trades add 1 — not the COMPLETE guard.
    # Plant COMPLETE after a successful status by calling track with patched order:
    service2._orders["OID-1"] = inconsistent
    static._order = raw(request(), status="OPEN", filled_quantity=0)

    def fake_get_status(broker_order_id, *, refresh=True):
        return BrokerOrderResult("status", True, inconsistent, 1, None, None, None, NOW)

    service2.get_status = fake_get_status  # type: ignore[method-assign]
    assert service2.track_executions("OID-1").error_code == "BOM.RECONCILIATION.FILL_REGRESSION"


def test_modify_cancel_batch_status_and_serialization():
    fake = FakeBrokerOrderTransport()
    service = manager(fake)
    placed = service.place(request(order_type=OrderType.LIMIT, price=Decimal("100.00")))
    assert service.modify("OID-1", ModifyOrderRequest(price=Decimal("99.00"))).success
    assert service.cancel("OID-1").success
    assert service.cancel("OID-1").success
    results = service.cancel_many(["OID-1", "OID-1"])
    assert results[0] is results[1]
    encoded = placed.to_json()
    assert BrokerOrderResult.from_json(encoded) == placed
    assert placed.order.to_json()
    corrupted = json.loads(encoded)
    corrupted["version"] = 999
    with pytest.raises(BrokerOrderManagerSerializationError):
        BrokerOrderResult.from_dict(corrupted)


def test_terminal_modify_and_cached_missing_status():
    fake = FakeBrokerOrderTransport()
    service = manager(fake)
    service.place(request())
    fake.orders["OID-1"] = raw(request(), status="COMPLETE", filled_quantity=10)
    service.get_status("OID-1")
    assert service.modify("OID-1", ModifyOrderRequest(quantity=2)).error_code == "BOM.STATE.NOT_MODIFIABLE"
    assert service.get_status("unknown", refresh=False).error_code == "BOM.ORDER.NOT_FOUND"
    assert service.get_status("").error_code == "BOM.VALIDATION.REQUEST"


def test_config_helpers_statuses_wires_health_and_concurrency():
    assert default_broker_order_manager_config("paper").max_attempts == 2
    with pytest.raises(BrokerOrderManagerConfigurationError):
        BrokerOrderManagerConfig(max_attempts=0)
    assert normalize_broker_status(" trigger   pending ") is BrokerOrderStatus.TRIGGER_PENDING
    assert is_tick_aligned(Decimal("1.10"), Decimal(".05"))
    assert to_kite_order_type(OrderType.SL_M) == "SL-M"
    assert to_kite_product(ProductType.MIS) == "MIS"
    assert to_kite_exchange(Exchange.NFO) == "NFO"

    fake = FakeBrokerOrderTransport()
    fake.place_gate = threading.Event()
    fake.place_started = threading.Event()
    service = manager(fake)
    results: list[BrokerOrderResult] = []

    def run():
        results.append(service.place(request()))

    first = threading.Thread(target=run)
    second = threading.Thread(target=run)
    first.start()
    assert fake.place_started.wait(timeout=2)
    second.start()
    time.sleep(0.05)
    fake.place_gate.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert len(results) == 2
    assert fake.calls.count("place") == 1
    assert service.get_health().connected
    assert service.get_statistics().placement_successes == 1


def test_error_paths_events_and_serializers():
    class BrokenBus:
        def publish(self, topic, payload):
            raise RuntimeError("ignore")

    for kwargs in (
        dict(initial_retry_delay_seconds=-1),
        dict(retry_backoff_multiplier=0),
        dict(operation_timeout_seconds=0),
        dict(health_window_size=1),
        dict(serialization_version=2),
        dict(max_retry_delay_seconds=0.1, initial_retry_delay_seconds=1.0),
    ):
        with pytest.raises(BrokerOrderManagerConfigurationError):
            BrokerOrderManagerConfig(**kwargs)
    for profile in ("unit_test", "live", "live_conservative", "test"):
        assert default_broker_order_manager_config(profile)
    with pytest.raises(BrokerOrderManagerConfigurationError):
        default_broker_order_manager_config("nope")
    with pytest.raises(BrokerOrderManagerValidationError):
        normalize_broker_status("unknown")

    service = BrokerOrderManager(
        BrokerOrderManagerConfig(max_attempts=1),
        transport=FakeBrokerOrderTransport(),
        event_bus=BrokenBus(),
        clock=lambda: NOW,
        sleeper=lambda _: None,
        monotonic=lambda: 1,
    )
    assert service.cancel("").error_code == "BOM.VALIDATION.REQUEST"
    assert service.modify("", ModifyOrderRequest()).error_code == "BOM.VALIDATION.REQUEST"
    assert service.cancel_many(["x"] * 51)[0].error_code == "BOM.BATCH.TOO_LARGE"

    trade = BrokerExecution("T", "O", 1, Decimal("1.1"), NOW, Exchange.NFO)
    assert BrokerExecution.from_json(trade.to_json()) == trade
    with pytest.raises(BrokerOrderManagerSerializationError):
        BrokerExecution.from_json("{")
    with pytest.raises(BrokerOrderManagerSerializationError):
        BrokerExecution.from_dict({"schema": "theta.broker_execution", "version": 1, "payload": {}})
    with pytest.raises(BrokerOrderManagerSerializationError):
        BrokerExecution.from_dict({
            "schema": "theta.broker_execution",
            "version": 1,
            "payload": "not-a-map",
        })

    fake = FakeBrokerOrderTransport()
    service = manager(fake)
    placed = service.place(request(order_type=OrderType.LIMIT, price=Decimal("100.00")))
    fake.scripted.append(BrokerRejectedError("no", code="BOM.BROKER.REJECTED"))
    assert service.modify("OID-1", ModifyOrderRequest(quantity=2)).error_code == "BOM.BROKER.REJECTED"

    retry_fake = FakeBrokerOrderTransport([RetryableTransportError("no", code="BOM.TRANSPORT.NETWORK")])
    assert manager(retry_fake, max_attempts=1).cancel("unknown").error_code == "BOM.TRANSPORT.NETWORK"

    retry_twice = FakeBrokerOrderTransport()
    retry_service = manager(retry_twice, max_attempts=2)
    retry_service.place(request())
    retry_twice.scripted.extend([
        RetryableTransportError("no", code="BOM.TRANSPORT.NETWORK"),
        {**raw(request()), "order_id": "OID-1", "status": "CANCELLED"},
    ])
    assert retry_service.cancel("OID-1").success
    assert retry_service.get_statistics().retry_attempts >= 1

    fake.orders["OID-1"] = raw(request(), status="COMPLETE", filled_quantity=10)
    service.get_status("OID-1")
    assert service.cancel("OID-1").error_code == "BOM.STATE.ALREADY_COMPLETE"

    rejected_fake = FakeBrokerOrderTransport()
    rejected_service = manager(rejected_fake)
    rejected_service.place(request())
    rejected_fake.orders["OID-1"] = raw(request(), status="REJECTED")
    rejected_service.get_status("OID-1")
    assert rejected_service.cancel("OID-1").error_code == "BOM.STATE.ALREADY_REJECTED"

    assert BrokerOrder.from_json(placed.order.to_json()) == placed.order
    with pytest.raises(BrokerOrderManagerSerializationError):
        BrokerOrder.from_json("{")
    with pytest.raises(BrokerOrderManagerSerializationError):
        BrokerOrder.from_dict({"schema": "theta.broker_order", "version": 1, "payload": {}})
    with pytest.raises(BrokerOrderManagerSerializationError):
        BrokerOrderResult.from_json("{")
    with pytest.raises(BrokerOrderManagerSerializationError):
        BrokerOrderResult.from_dict({"schema": "theta.broker_order_result", "version": 1, "payload": {}})


def test_remaining_validation_status_and_tracking_failures():
    fake = FakeBrokerOrderTransport()
    service = manager(fake)
    service.place(request(order_type=OrderType.LIMIT, price=Decimal("100.00")))
    assert service.modify("OID-1", ModifyOrderRequest()).error_code == "BOM.VALIDATION.REQUEST"
    assert service.modify("OID-1", ModifyOrderRequest(quantity=0)).error_code == "BOM.VALIDATION.QUANTITY"
    assert service.modify("OID-1", ModifyOrderRequest(price=Decimal("-1"))).error_code == "BOM.VALIDATION.PRICE"
    assert service.modify("OID-1", ModifyOrderRequest(validity="DAY")).error_code == "BOM.VALIDATION.REQUEST"  # type: ignore[arg-type]
    assert service.modify("OID-1", ModifyOrderRequest(price=Decimal("100.001"))).error_code == "BOM.VALIDATION.TICK_SIZE"

    market_service = manager(FakeBrokerOrderTransport())
    market_service.place(request())
    assert market_service.modify(
        "OID-1",
        ModifyOrderRequest(price=Decimal("100.00")),
    ).error_code == "BOM.VALIDATION.ORDER_TYPE"

    fake.orders["OID-1"] = raw(request(), status="NOT A STATUS")
    assert service.get_status("OID-1").error_code == "BOM.NORMALIZATION.UNKNOWN_STATUS"
    fake.orders["OID-1"] = raw(request(), status="OPEN", exchange_timestamp="2020-01-01T00:00:00+00:00")
    assert service.get_status("OID-1").success
    fake.trades["OID-1"] = [{"trade_id": "x", "quantity": 11, "average_price": "1", "exchange": "NFO"}]
    assert service.track_executions("OID-1").error_code == "BOM.RECONCILIATION.FILL_REGRESSION"
    encoded = service.get_status("OID-1", refresh=False).to_json()
    assert BrokerOrderResult.from_json(encoded).operation == "status"


def test_modify_fetch_when_ack_lacks_order_id_and_status_network():
    fake = FakeBrokerOrderTransport()
    service = manager(fake)
    service.place(request(order_type=OrderType.LIMIT, price=Decimal("100.00")))
    fake.scripted.append({"status": "OPEN", "quantity": 10, "filled_quantity": 0})
    fake.orders["OID-1"] = raw(request(order_type=OrderType.LIMIT, price=Decimal("100.00")), price="99.00")
    assert service.modify("OID-1", ModifyOrderRequest(price=Decimal("99.00"))).success
    assert "fetch" in fake.calls

    class ExplodingFetch(FakeBrokerOrderTransport):
        def fetch_order(self, order_id):
            self.calls.append("fetch")
            raise RuntimeError("down")

    exploding = ExplodingFetch()
    exploding.orders["OID-1"] = raw(request())
    service2 = manager(exploding)
    service2.place(request())
    exploding.scripted.clear()
    assert service2.get_status("OID-1").error_code == "BOM.TRANSPORT.NETWORK"


def test_serialization_datetime_helpers_and_boundary_source():
    with pytest.raises(BrokerOrderManagerSerializationError):
        bom._iso(datetime(2026, 8, 5, 10, 0))
    with pytest.raises(ValueError):
        bom._datetime(123)
    with pytest.raises(ValueError):
        bom._datetime("2026-08-05T10:00:00")

    source = Path(bom.__file__).read_text(encoding="utf-8")
    for forbidden in ("kiteconnect", "KiteTicker", "def ema(", "def rsi(", "os.environ", "load_dotenv"):
        assert forbidden not in source
    assert bom.PRODUCER_NAME == "broker.broker_order_manager"
    assert bom.BROKER_ORDER_MANAGER_VERSION == "1.0.0"


def test_mutate_exception_paths_and_fill_regression_on_status():
    fake = FakeBrokerOrderTransport()
    service = manager(fake)
    service.place(request(order_type=OrderType.LIMIT, price=Decimal("100.00")))
    fake.orders["OID-1"] = raw(
        request(order_type=OrderType.LIMIT, price=Decimal("100.00")),
        filled_quantity=5,
        status="OPEN",
    )
    service.get_status("OID-1")
    fake.orders["OID-1"] = raw(
        request(order_type=OrderType.LIMIT, price=Decimal("100.00")),
        filled_quantity=1,
        status="OPEN",
        exchange_timestamp=(NOW.replace(year=2027)).isoformat(),
    )
    assert service.get_status("OID-1").error_code == "BOM.RECONCILIATION.FILL_REGRESSION"

    class OddCancel(FakeBrokerOrderTransport):
        def cancel_order(self, order_id, variety):
            self.calls.append("cancel")
            raise BrokerOrderManagerError("bad", code="BOM.INTERNAL.INVARIANT")

    odd = OddCancel()
    odd.orders["OID-1"] = raw(request())
    service3 = manager(odd)
    service3.place(request())
    assert service3.cancel("OID-1").error_code == "BOM.INTERNAL.INVARIANT"

    class BoomCancel(FakeBrokerOrderTransport):
        def cancel_order(self, order_id, variety):
            self.calls.append("cancel")
            raise RuntimeError("boom")

    boom = BoomCancel()
    boom.orders["OID-1"] = raw(request())
    service4 = manager(boom)
    service4.place(request())
    assert service4.cancel("OID-1").error_code == "BOM.INTERNAL.INVARIANT"
