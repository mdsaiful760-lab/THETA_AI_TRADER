"""Unit tests for market_data.market_data_engine."""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType
from typing import Callable
from zoneinfo import ZoneInfo

import pytest

from broker.base_broker import (
    AccountProfile,
    BaseBrokerClient,
    BrokerCapabilities,
    BrokerClientError,
    BrokerClientMetadata,
    BrokerConnectionError,
    BrokerId,
    BrokerSession,
    ConnectionInfo as BrokerConnectionInfo,
    ConnectionState,
    Exchange,
    HistoricalRequest,
    InstrumentRequest,
    QuoteRequest,
    SessionState,
    WebSocketState,
)
from core.event_bus import EventBus, EventBusPolicy
from core.event_topics import EventTopics
from market_data.market_data_adapter import AdapterPermission, MarketDataAdapter
from market_data.market_data_engine import (
    ERROR_CONNECTION_DISCONNECTED,
    ERROR_PUBLISH_ADAPTER_BLOCK,
    ERROR_PUBLISH_SKIPPED_COVERAGE,
    ConnectionStatus,
    HeartbeatPolicy,
    HistoricalCandleRequest,
    MarketDataEngine,
    MarketDataEngineConfig,
    MarketDataEngineConfigurationError,
    MarketDataEngineConnectionError,
    PublishMode,
    PublishOutcome,
    ReconnectPolicy,
    SubscriptionState,
    TickBuffer,
    UniverseConfig,
    EngineRunState,
)
from market_data.market_snapshot import SnapshotSource

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def fixed_as_of() -> datetime:
    return datetime(2026, 8, 3, 10, 15, 0, tzinfo=IST)


def make_session() -> BrokerSession:
    return BrokerSession(
        broker_id=BrokerId.MOCK,
        session_id="session-1",
        authenticated_at=utc_now(),
        credentials=MappingProxyType({"token": "mock"}),
    )


def kite_instrument_factory(
    *,
    symbol: str,
    strike: float,
    option_type: str,
    expiry: date | str = date(2026, 8, 7),
    underlying: str = "NIFTY",
    exchange: str = "NFO",
    lot_size: int = 75,
    instrument_token: int = 100001,
) -> dict[str, object]:
    return {
        "instrument_token": instrument_token,
        "exchange_token": 200001,
        "tradingsymbol": symbol,
        "name": underlying,
        "last_price": 0.0,
        "expiry": expiry,
        "strike": strike,
        "tick_size": 0.05,
        "lot_size": lot_size,
        "instrument_type": option_type,
        "segment": f"{exchange}-OPT",
        "exchange": exchange,
    }


def kite_quote_factory(*, ltp: float = 100.0, instrument_token: int = 100001) -> dict[str, object]:
    return {
        "instrument_token": instrument_token,
        "timestamp": fixed_as_of(),
        "last_price": ltp,
        "volume": 1000,
        "oi": 2000,
        "depth": {
            "buy": [{"quantity": 750, "price": ltp - 0.5, "orders": 2}],
            "sell": [{"quantity": 750, "price": ltp + 0.5, "orders": 2}],
        },
    }


def build_nfo_instruments(*, strikes_each_side: int = 1) -> list[dict[str, object]]:
    instruments: list[dict[str, object]] = []
    token = 1000
    atm = 24300.0
    for offset in range(-strikes_each_side, strikes_each_side + 1):
        strike = atm + offset * 50.0
        for option_type in ("CE", "PE"):
            symbol = f"NIFTY26807{int(strike)}{option_type}"
            token += 1
            instruments.append(
                kite_instrument_factory(
                    symbol=symbol,
                    strike=strike,
                    option_type=option_type,
                    instrument_token=token,
                )
            )
    return instruments


def build_nse_instruments() -> list[dict[str, object]]:
    return [
        {
            "instrument_token": 256265,
            "exchange": "NSE",
            "tradingsymbol": "NIFTY 50",
            "name": "NIFTY 50",
            "instrument_type": "EQ",
            "segment": "INDICES",
        },
        {
            "instrument_token": 264969,
            "exchange": "NSE",
            "tradingsymbol": "INDIA VIX",
            "name": "INDIA VIX",
            "instrument_type": "EQ",
            "segment": "INDICES",
        },
    ]


class CapturingMetrics:
    """Capture metric hook calls for assertions."""

    def __init__(self) -> None:
        self.gauges: dict[str, float] = {}
        self.counters: dict[str, float] = {}
        self.histograms: list[tuple[str, float]] = []

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: MappingProxyType | None = None,
    ) -> None:
        self.gauges[name] = value

    def increment_counter(
        self,
        name: str,
        *,
        value: float = 1.0,
        labels: MappingProxyType | None = None,
    ) -> None:
        self.counters[name] = self.counters.get(name, 0.0) + value

    def observe_histogram(
        self,
        name: str,
        value: float,
        *,
        labels: MappingProxyType | None = None,
    ) -> None:
        self.histograms.append((name, value))


class AlwaysOpenSafety:
    """Safety collaborator that always reports market open."""

    def is_market_open(self, current_time: datetime | None = None) -> bool:
        return True


class FakeBrokerClient(BaseBrokerClient):
    """Deterministic broker transport fake for engine tests."""

    def __init__(
        self,
        session: BrokerSession,
        *,
        connect_fail: bool = False,
        spot_price: float = 24300.0,
    ) -> None:
        super().__init__(session)
        self._connect_fail = connect_fail
        self._spot_price = spot_price
        self._connected = False
        self._lock = threading.RLock()
        self._subscribed: set[int] = set()
        self._tick_handler: Callable[[MappingProxyType], None] | None = None
        self._error_handler: Callable[[BrokerClientError], None] | None = None
        self._connection_handler: Callable[[BrokerConnectionInfo], None] | None = None
        self._nfo = build_nfo_instruments(strikes_each_side=1)
        self._nse = build_nse_instruments()
        self._connect_calls = 0
        self._disconnect_calls = 0

    @property
    def broker_id(self) -> BrokerId:
        return BrokerId.MOCK

    @property
    def client_version(self) -> str:
        return "1.0.0-test"

    @property
    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities()

    def metadata(self) -> BrokerClientMetadata:
        return BrokerClientMetadata(
            broker_id=self.broker_id,
            client_version=self.client_version,
            capabilities=self.capabilities,
        )

    def connect(self) -> None:
        self._connect_calls += 1
        if self._connect_fail:
            raise BrokerConnectionError("connect failed")
        self._connected = True

    def disconnect(self) -> None:
        self._disconnect_calls += 1
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_connection_info(self) -> BrokerConnectionInfo:
        return BrokerConnectionInfo(
            state=ConnectionState.CONNECTED if self._connected else ConnectionState.DISCONNECTED,
            since=utc_now(),
            websocket_state=WebSocketState.OPEN if self._connected else WebSocketState.CLOSED,
        )

    def is_authenticated(self) -> bool:
        return self._connected

    def get_session_state(self) -> SessionState:
        return SessionState.AUTHENTICATED if self._connected else SessionState.UNAUTHENTICATED

    def session_expires_at(self) -> datetime | None:
        return None

    def fetch_instruments(
        self,
        request: InstrumentRequest,
    ) -> tuple[MappingProxyType, ...]:
        rows = self._nfo if request.exchange is Exchange.NFO else self._nse
        return tuple(MappingProxyType(dict(row)) for row in rows)

    def fetch_quotes(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        quotes: dict[str, Mapping[str, object]] = {}
        for key in request.instrument_keys:
            if key == "NSE:NIFTY 50":
                quotes[key] = MappingProxyType(
                    {
                        "last_price": self._spot_price,
                        "timestamp": fixed_as_of(),
                        "ohlc": {
                            "open": self._spot_price - 100.0,
                            "high": self._spot_price + 50.0,
                            "low": self._spot_price - 150.0,
                            "close": self._spot_price - 90.0,
                        },
                    }
                )
            elif key == "NSE:INDIA VIX":
                quotes[key] = MappingProxyType(
                    {
                        "last_price": 12.5,
                        "timestamp": fixed_as_of(),
                    }
                )
            else:
                token = self._token_for_key(key)
                quotes[key] = MappingProxyType(
                    kite_quote_factory(ltp=100.0, instrument_token=token or 100001)
                )
        return quotes

    def _token_for_key(self, quote_key: str) -> int | None:
        for row in self._nfo:
            if f"{row['exchange']}:{row['tradingsymbol']}" == quote_key:
                return int(row["instrument_token"])
        for row in self._nse:
            if f"{row['exchange']}:{row['tradingsymbol']}" == quote_key:
                return int(row["instrument_token"])
        return None

    def fetch_ltp(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        return self.fetch_quotes(request)

    def fetch_historical(
        self,
        request: HistoricalRequest,
    ) -> tuple[MappingProxyType, ...]:
        return (MappingProxyType({"open": 1.0, "close": 2.0, "volume": 10}),)

    def subscribe(self, instrument_tokens: tuple[int, ...]) -> None:
        with self._lock:
            self._subscribed.update(instrument_tokens)

    def unsubscribe(self, instrument_tokens: tuple[int, ...]) -> None:
        with self._lock:
            self._subscribed.difference_update(instrument_tokens)

    def get_subscribed_tokens(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._subscribed)

    def set_tick_handler(self, handler) -> None:
        self._tick_handler = handler

    def set_error_handler(self, handler) -> None:
        self._error_handler = handler

    def set_connection_handler(self, handler) -> None:
        self._connection_handler = handler

    def simulate_tick(self, tick: dict[str, object]) -> None:
        if self._tick_handler is not None:
            self._tick_handler(MappingProxyType(dict(tick)))

    def simulate_error(self, error: BrokerClientError) -> None:
        if self._error_handler is not None:
            self._error_handler(error)

    def place_order(self, request):
        raise NotImplementedError

    def modify_order(self, request):
        raise NotImplementedError

    def cancel_order(self, request):
        raise NotImplementedError

    def fetch_orders(self, request):
        return ()

    def fetch_positions(self):
        return ()

    def fetch_margins(self):
        raise NotImplementedError

    def fetch_profile(self) -> AccountProfile:
        return AccountProfile(
            user_id="user-1",
            user_name="Test User",
            broker="mock",
            exchanges=(Exchange.NSE, Exchange.NFO),
            products=(),
        )

    def update_session(self, session: BrokerSession) -> None:
        super().update_session(session)


def make_engine_config(**overrides: object) -> MarketDataEngineConfig:
    universe = UniverseConfig(
        underlying="NIFTY",
        strikes_each_side=1,
        include_vix=True,
    )
    defaults: dict[str, object] = {
        "universe": universe,
        "publish_interval_seconds": 0.05,
        "publish_mode": PublishMode.ANALYSIS,
        "minimum_publish_coverage_ratio": 0.5,
        "heartbeat_policy": HeartbeatPolicy(
            max_silence_seconds=0.2,
            check_interval_seconds=0.05,
        ),
        "reconnect_policy": ReconnectPolicy(
            max_attempts=2,
            initial_delay_seconds=0.01,
            max_delay_seconds=0.05,
        ),
    }
    defaults.update(overrides)
    return MarketDataEngineConfig(
        universe=defaults["universe"],  # type: ignore[arg-type]
        publish_interval_seconds=defaults["publish_interval_seconds"],  # type: ignore[arg-type]
        publish_mode=defaults["publish_mode"],  # type: ignore[arg-type]
        minimum_publish_coverage_ratio=defaults["minimum_publish_coverage_ratio"],  # type: ignore[arg-type]
        heartbeat_policy=defaults["heartbeat_policy"],  # type: ignore[arg-type]
        reconnect_policy=defaults["reconnect_policy"],  # type: ignore[arg-type]
    )


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus(EventBusPolicy())


@pytest.fixture
def adapter() -> MarketDataAdapter:
    return MarketDataAdapter()


@pytest.fixture
def broker() -> FakeBrokerClient:
    return FakeBrokerClient(make_session())


@pytest.fixture
def engine(
    broker: FakeBrokerClient,
    adapter: MarketDataAdapter,
    event_bus: EventBus,
) -> MarketDataEngine:
    metrics = CapturingMetrics()
    engine = MarketDataEngine(
        make_engine_config(),
        broker,
        adapter,
        event_bus,
        safety=AlwaysOpenSafety(),
        metrics=metrics,
        time_fn=fixed_as_of,
    )
    engine._metrics = metrics  # noqa: SLF001 - test hook
    return engine


def test_invalid_config_raises() -> None:
    with pytest.raises(MarketDataEngineConfigurationError):
        MarketDataEngine(
            MarketDataEngineConfig(universe=UniverseConfig(underlying="")),
            FakeBrokerClient(make_session()),
            MarketDataAdapter(),
            EventBus(EventBusPolicy()),
        )


def test_start_stop_idempotent(engine: MarketDataEngine, broker: FakeBrokerClient) -> None:
    engine.start()
    assert engine.health_check().connection_status is ConnectionStatus.CONNECTED
    engine.start()
    engine.stop()
    engine.stop()
    assert broker._disconnect_calls == 1


def test_connect_failure_leaves_stopped(broker: FakeBrokerClient, event_bus: EventBus) -> None:
    broker._connect_fail = True
    engine = MarketDataEngine(
        make_engine_config(),
        broker,
        MarketDataAdapter(),
        event_bus,
    )
    with pytest.raises(MarketDataEngineConnectionError):
        engine.start()
    assert engine.health_check().connection_status is ConnectionStatus.DISCONNECTED


def test_subscribe_on_start(engine: MarketDataEngine, broker: FakeBrokerClient) -> None:
    engine.start()
    assert len(broker.get_subscribed_tokens()) > 0
    engine.stop()


def test_publish_snapshot_with_ticks(
    engine: MarketDataEngine,
    broker: FakeBrokerClient,
) -> None:
    engine.start()
    for token in broker.get_subscribed_tokens():
        broker.simulate_tick(
            {
                "instrument_token": token,
                "last_price": 100.0,
                "timestamp": fixed_as_of(),
            }
        )
    event = engine.publish_snapshot(correlation_id="corr-1", as_of=fixed_as_of())
    assert event.outcome is PublishOutcome.PUBLISHED
    assert event.snapshot is not None
    engine.stop()


def test_event_bus_receives_published_event(
    engine: MarketDataEngine,
    broker: FakeBrokerClient,
    event_bus: EventBus,
) -> None:
    received: list[object] = []
    event_bus.subscribe(
        EventTopics.MARKET_SNAPSHOT_PUBLISHED,
        lambda envelope: received.append(envelope.payload),
    )
    engine.start()
    for token in broker.get_subscribed_tokens():
        broker.simulate_tick({"instrument_token": token, "last_price": 100.0})
    engine.publish_snapshot(correlation_id="corr-bus")
    assert received
    engine.stop()


def test_publish_skipped_when_disconnected(engine: MarketDataEngine) -> None:
    event = engine.publish_snapshot(correlation_id="corr-skip", as_of=fixed_as_of())
    assert event.outcome is PublishOutcome.SKIPPED
    assert event.reason_code == ERROR_CONNECTION_DISCONNECTED


def test_publish_force_when_disconnected(
    engine: MarketDataEngine,
    broker: FakeBrokerClient,
) -> None:
    engine.start()
    for token in broker.get_subscribed_tokens():
        broker.simulate_tick({"instrument_token": token, "last_price": 100.0})
    engine.stop()
    event = engine.publish_snapshot(
        correlation_id="corr-force",
        as_of=fixed_as_of(),
        force=True,
    )
    assert event.outcome in {PublishOutcome.PUBLISHED, PublishOutcome.FAILED}


def test_live_mode_skips_low_coverage(
    broker: FakeBrokerClient,
    adapter: MarketDataAdapter,
    event_bus: EventBus,
) -> None:
    engine = MarketDataEngine(
        make_engine_config(publish_mode=PublishMode.LIVE, minimum_publish_coverage_ratio=0.99),
        broker,
        adapter,
        event_bus,
        safety=AlwaysOpenSafety(),
        time_fn=fixed_as_of,
    )
    engine.start()
    event = engine.publish_snapshot(correlation_id="corr-live")
    assert event.outcome is PublishOutcome.SKIPPED
    assert event.reason_code == ERROR_PUBLISH_SKIPPED_COVERAGE
    engine.stop()


def test_subscriber_exception_isolated(
    engine: MarketDataEngine,
    broker: FakeBrokerClient,
) -> None:
    def failing_subscriber(_event: object) -> None:
        raise RuntimeError("subscriber failed")

    engine.add_subscriber(failing_subscriber)
    engine.start()
    for token in broker.get_subscribed_tokens():
        broker.simulate_tick({"instrument_token": token, "last_price": 100.0})
    event = engine.publish_snapshot(correlation_id="corr-sub")
    assert event.outcome is PublishOutcome.PUBLISHED
    engine.stop()


def test_heartbeat_triggers_reconnect(
    broker: FakeBrokerClient,
    adapter: MarketDataAdapter,
    event_bus: EventBus,
) -> None:
    monotonic = {"value": 0.0}

    def fake_monotonic() -> float:
        return monotonic["value"]

    engine = MarketDataEngine(
        make_engine_config(
            heartbeat_policy=HeartbeatPolicy(
                max_silence_seconds=0.1,
                check_interval_seconds=0.02,
            ),
            reconnect_policy=ReconnectPolicy(
                max_attempts=1,
                initial_delay_seconds=0.01,
            ),
        ),
        broker,
        adapter,
        event_bus,
        safety=AlwaysOpenSafety(),
        monotonic_clock=fake_monotonic,
        time_fn=fixed_as_of,
    )
    engine.start()
    monotonic["value"] = 1.0
    time.sleep(0.15)
    info = engine.get_connection_info()
    assert info.status in {
        ConnectionStatus.DEGRADED,
        ConnectionStatus.RECONNECTING,
        ConnectionStatus.CONNECTED,
    }
    engine.stop()


def test_broker_error_triggers_degraded(engine: MarketDataEngine, broker: FakeBrokerClient) -> None:
    engine.start()
    broker.simulate_error(BrokerConnectionError("ws error"))
    assert engine.get_connection_info().status in {
        ConnectionStatus.DEGRADED,
        ConnectionStatus.RECONNECTING,
    }
    engine.stop()


def test_tick_buffer_thread_safe() -> None:
    buffer = TickBuffer(max_entries=256)

    def worker(token: int) -> None:
        for _ in range(20):
            buffer.upsert(
                {
                    "instrument_token": token,
                    "last_price": float(token),
                    "timestamp": fixed_as_of(),
                }
            )

    threads = [threading.Thread(target=worker, args=(index + 1,)) for index in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert buffer.stats().instrument_count == 10


def test_refresh_instruments(engine: MarketDataEngine, broker: FakeBrokerClient) -> None:
    engine.start()
    count = engine.refresh_instruments()
    assert count == len(broker._nfo)
    engine.stop()


def test_fetch_historical_candles(engine: MarketDataEngine, broker: FakeBrokerClient) -> None:
    engine.start()
    result = engine.fetch_historical_candles(
        HistoricalCandleRequest(
            instrument_key="NSE:NIFTY 50",
            interval="minute",
            from_ts=fixed_as_of() - timedelta(days=1),
            to_ts=fixed_as_of(),
        )
    )
    assert len(result.candles) == 1
    engine.stop()


def test_subscription_records_mark_active(
    engine: MarketDataEngine,
    broker: FakeBrokerClient,
) -> None:
    engine.start()
    token = next(iter(broker.get_subscribed_tokens()))
    broker.simulate_tick({"instrument_token": token, "last_price": 100.0})
    records = engine.get_subscriptions()
    assert any(record.state is SubscriptionState.ACTIVE for record in records)
    engine.stop()


def test_health_check_running(engine: MarketDataEngine, broker: FakeBrokerClient) -> None:
    engine.start()
    for token in broker.get_subscribed_tokens():
        broker.simulate_tick({"instrument_token": token, "last_price": 100.0})
    engine.publish_snapshot(correlation_id="corr-health", as_of=fixed_as_of())
    health = engine.health_check()
    assert health.connection_status is ConnectionStatus.CONNECTED
    assert health.last_successful_publish_at is not None
    engine.stop()


def test_metrics_updated_on_publish(
    engine: MarketDataEngine,
    broker: FakeBrokerClient,
) -> None:
    metrics: CapturingMetrics = engine._metrics  # noqa: SLF001
    engine.start()
    for token in broker.get_subscribed_tokens():
        broker.simulate_tick({"instrument_token": token, "last_price": 100.0})
    engine.publish_snapshot(correlation_id="corr-metrics", as_of=fixed_as_of())
    assert metrics.counters.get("market_data_publish_total", 0.0) >= 1.0
    assert metrics.histograms
    engine.stop()


def test_subscription_limit_on_start() -> None:
    config = MarketDataEngineConfig(
        universe=UniverseConfig(underlying="NIFTY", strikes_each_side=100),
        max_subscriptions=1,
    )
    engine = MarketDataEngine(
        config,
        FakeBrokerClient(make_session()),
        MarketDataAdapter(),
        EventBus(EventBusPolicy()),
    )
    with pytest.raises(MarketDataEngineConfigurationError):
        engine.start()


def test_adapter_block_returns_failed(
    broker: FakeBrokerClient,
    event_bus: EventBus,
) -> None:
    class BlockingAdapter(MarketDataAdapter):
        def build_market_snapshot_from_kite(self, **kwargs):
            from market_data.market_data_adapter import AdapterBuildResult

            return AdapterBuildResult(
                permission=AdapterPermission.BLOCK,
                adapter_allowed=False,
                reason="blocked",
                snapshot=None,
                validation_errors=(),
                rejections=(),
                instrument_count=0,
                matched_instruments=0,
                normalized_count=0,
                rejected_count=0,
                broker_order_allowed=False,
            )

    engine = MarketDataEngine(
        make_engine_config(),
        broker,
        BlockingAdapter(),
        event_bus,
        safety=AlwaysOpenSafety(),
        time_fn=fixed_as_of,
    )
    engine.start()
    for token in broker.get_subscribed_tokens():
        broker.simulate_tick({"instrument_token": token, "last_price": 100.0})
    event = engine.publish_snapshot(correlation_id="corr-block", as_of=fixed_as_of(), force=True)
    assert event.outcome is PublishOutcome.FAILED
    assert event.reason_code == ERROR_PUBLISH_ADAPTER_BLOCK
    engine.stop()


def test_config_invalid_publish_interval() -> None:
    with pytest.raises(MarketDataEngineConfigurationError):
        MarketDataEngine(
            MarketDataEngineConfig(
                universe=UniverseConfig(underlying="NIFTY"),
                publish_interval_seconds=0.0,
            ),
            FakeBrokerClient(make_session()),
            MarketDataAdapter(),
            EventBus(EventBusPolicy()),
        )


def test_engine_properties(engine: MarketDataEngine) -> None:
    assert engine.engine_name == "market_data"
    assert engine.engine_version == "1.0.0"


def test_remove_subscriber(engine: MarketDataEngine) -> None:
    events: list[object] = []

    def handler(event: object) -> None:
        events.append(event)

    engine.add_subscriber(handler)
    engine.remove_subscriber(handler)
    assert handler not in engine._publisher._subscribers  # noqa: SLF001


def test_buffer_overflow_and_prune() -> None:
    buffer = TickBuffer(max_entries=2)
    buffer.upsert({"instrument_token": 1, "last_price": 1.0})
    buffer.upsert({"instrument_token": 2, "last_price": 2.0})
    buffer.upsert({"instrument_token": 3, "last_price": 3.0})
    assert buffer.stats().instrument_count == 2
    removed = buffer.prune(frozenset({1}))
    assert removed == 1
    assert buffer.get(2) is None
    assert buffer.get(1) is not None


def test_buffer_ignores_invalid_ticks() -> None:
    buffer = TickBuffer(max_entries=8)
    buffer.upsert({"last_price": 1.0})
    buffer.upsert({"instrument_token": 1, "last_price": 0.0})
    assert buffer.stats().instrument_count == 0


def test_get_buffer_stats_via_engine(engine: MarketDataEngine, broker: FakeBrokerClient) -> None:
    engine.start()
    broker.simulate_tick({"instrument_token": 1001, "last_price": 100.0})
    stats = engine.get_buffer_stats()
    assert stats.instrument_count >= 1
    engine.stop()


def test_scheduled_publish_worker(
    broker: FakeBrokerClient,
    adapter: MarketDataAdapter,
    event_bus: EventBus,
) -> None:
    events: list[object] = []
    engine = MarketDataEngine(
        make_engine_config(publish_interval_seconds=0.02),
        broker,
        adapter,
        event_bus,
        safety=AlwaysOpenSafety(),
        time_fn=fixed_as_of,
    )
    engine.add_subscriber(lambda event: events.append(event))
    engine.start()
    for token in broker.get_subscribed_tokens():
        broker.simulate_tick({"instrument_token": token, "last_price": 100.0})
    time.sleep(0.15)
    engine.stop()
    assert events


def test_reconnect_exhausted_stops_engine(
    broker: FakeBrokerClient,
    adapter: MarketDataAdapter,
    event_bus: EventBus,
) -> None:
    engine = MarketDataEngine(
        make_engine_config(
            reconnect_policy=ReconnectPolicy(max_attempts=1, initial_delay_seconds=0.01),
        ),
        broker,
        adapter,
        event_bus,
        safety=AlwaysOpenSafety(),
        time_fn=fixed_as_of,
    )
    engine.start()

    def failing_connect() -> None:
        raise BrokerConnectionError("down")

    broker.connect = failing_connect  # type: ignore[method-assign]
    broker._connected = False
    broker.simulate_error(BrokerConnectionError("ws down"))
    time.sleep(0.15)
    assert engine.get_connection_info().status is ConnectionStatus.DISCONNECTED
    engine.stop()


def test_live_degraded_skips_without_rest(
    broker: FakeBrokerClient,
    adapter: MarketDataAdapter,
    event_bus: EventBus,
) -> None:
    engine = MarketDataEngine(
        make_engine_config(publish_mode=PublishMode.LIVE),
        broker,
        adapter,
        event_bus,
        safety=AlwaysOpenSafety(),
        time_fn=fixed_as_of,
    )
    engine.start()
    broker.fetch_quotes = lambda request: {}  # type: ignore[method-assign]
    with engine._lock:  # noqa: SLF001
        engine._connection_status = ConnectionStatus.DEGRADED
    event = engine.publish_snapshot(correlation_id="degraded")
    assert event.outcome is PublishOutcome.SKIPPED
    engine.stop()


def test_market_closed_skips_live_publish(
    broker: FakeBrokerClient,
    adapter: MarketDataAdapter,
    event_bus: EventBus,
) -> None:
    class ClosedMarketSafety:
        def is_market_open(self, current_time: datetime | None = None) -> bool:
            return False

    engine = MarketDataEngine(
        make_engine_config(publish_mode=PublishMode.LIVE, minimum_publish_coverage_ratio=0.01),
        broker,
        adapter,
        event_bus,
        safety=ClosedMarketSafety(),
        time_fn=fixed_as_of,
    )
    engine.start()
    for token in broker.get_subscribed_tokens():
        broker.simulate_tick({"instrument_token": token, "last_price": 100.0})
    with engine._lock:  # noqa: SLF001
        engine._connection_status = ConnectionStatus.CONNECTED
    event = engine.publish_snapshot(correlation_id="closed")
    assert event.outcome is PublishOutcome.SKIPPED
    engine.stop()


def test_no_valid_expiry_raises_on_start(
    broker: FakeBrokerClient,
    event_bus: EventBus,
) -> None:
    class EmptyExpiryAdapter(MarketDataAdapter):
        def get_nearest_expiry(self, *args, **kwargs):
            return None

    engine = MarketDataEngine(
        make_engine_config(),
        broker,
        EmptyExpiryAdapter(),
        event_bus,
    )
    with pytest.raises(MarketDataEngineConnectionError):
        engine.start()


def test_assembly_failure_returns_failed_event(
    engine: MarketDataEngine,
    broker: FakeBrokerClient,
) -> None:
    engine.start()
    broker.fetch_quotes = lambda request: (_ for _ in ()).throw(BrokerConnectionError("rest down"))  # type: ignore[method-assign]
    event = engine.publish_snapshot(correlation_id="asm-fail", force=True)
    assert event.outcome is PublishOutcome.FAILED
    assert event.reason_code == "MARKET_DATA_ENGINE.PUBLISH.ASSEMBLY_FAILED"
    engine.stop()


def test_on_broker_connection_records_activity(engine: MarketDataEngine, broker: FakeBrokerClient) -> None:
    engine.start()
    before = engine._last_activity_monotonic  # noqa: SLF001
    broker._connection_handler(broker.get_connection_info())  # type: ignore[misc]
    assert engine._last_activity_monotonic >= before  # noqa: SLF001
    engine.stop()


def test_tick_without_token_is_ignored(engine: MarketDataEngine, broker: FakeBrokerClient) -> None:
    engine.start()
    broker.simulate_tick({"last_price": 100.0})
    engine.stop()


def test_subscribe_failure_raises(
    broker: FakeBrokerClient,
    event_bus: EventBus,
) -> None:
    def fail_subscribe(tokens: tuple[int, ...]) -> None:
        raise BrokerConnectionError("subscribe failed")

    broker.subscribe = fail_subscribe  # type: ignore[method-assign]
    engine = MarketDataEngine(
        make_engine_config(),
        broker,
        MarketDataAdapter(),
        event_bus,
    )
    with pytest.raises(MarketDataEngineConnectionError):
        engine.start()


def test_config_invalid_coverage_ratio() -> None:
    with pytest.raises(MarketDataEngineConfigurationError):
        MarketDataEngine(
            MarketDataEngineConfig(
                universe=UniverseConfig(underlying="NIFTY"),
                minimum_publish_coverage_ratio=1.5,
            ),
            FakeBrokerClient(make_session()),
            MarketDataAdapter(),
            EventBus(EventBusPolicy()),
        )


def test_connect_timeout_when_spot_invalid(
    broker: FakeBrokerClient,
    event_bus: EventBus,
) -> None:
    broker._spot_price = 0.0
    engine = MarketDataEngine(
        MarketDataEngineConfig(
            universe=UniverseConfig(underlying="NIFTY"),
            connect_timeout_seconds=0.05,
        ),
        broker,
        MarketDataAdapter(),
        event_bus,
    )
    with pytest.raises(MarketDataEngineConnectionError):
        engine.start()


def test_event_bus_failure_is_isolated(
    broker: FakeBrokerClient,
    adapter: MarketDataAdapter,
) -> None:
    class FailingBus(EventBus):
        def publish(self, *args, **kwargs):
            raise RuntimeError("bus down")

    engine = MarketDataEngine(
        make_engine_config(),
        broker,
        adapter,
        FailingBus(EventBusPolicy()),
        safety=AlwaysOpenSafety(),
        time_fn=fixed_as_of,
    )
    engine.start()
    for token in broker.get_subscribed_tokens():
        broker.simulate_tick({"instrument_token": token, "last_price": 100.0})
    event = engine.publish_snapshot(correlation_id="bus-fail")
    assert event.outcome is PublishOutcome.PUBLISHED
    engine.stop()


def test_stop_tolerates_broker_errors(engine: MarketDataEngine, broker: FakeBrokerClient) -> None:
    engine.start()
    broker.unsubscribe = lambda tokens: (_ for _ in ()).throw(BrokerConnectionError("unsub"))  # type: ignore[method-assign]
    broker.disconnect = lambda: (_ for _ in ()).throw(BrokerConnectionError("disc"))  # type: ignore[method-assign]
    engine.stop()


def test_start_while_starting_is_noop(
    broker: FakeBrokerClient,
    adapter: MarketDataAdapter,
    event_bus: EventBus,
) -> None:
    engine = MarketDataEngine(make_engine_config(), broker, adapter, event_bus)
    with engine._lock:  # noqa: SLF001
        engine._state = EngineRunState.STARTING
    engine.start()
    assert engine.health_check().connection_status is ConnectionStatus.DISCONNECTED


def test_config_invalid_max_buffer_entries() -> None:
    with pytest.raises(MarketDataEngineConfigurationError):
        MarketDataEngine(
            MarketDataEngineConfig(
                universe=UniverseConfig(underlying="NIFTY"),
                max_buffer_entries=0,
            ),
            FakeBrokerClient(make_session()),
            MarketDataAdapter(),
            EventBus(EventBusPolicy()),
        )


def test_quote_key_for_token_missing() -> None:
    buffer = TickBuffer(max_entries=4)
    assert buffer.quote_key_for_token(999) is None
