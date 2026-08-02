"""Unit tests for broker.zerodha.kite_broker with mocked Kite SDK."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from types import MappingProxyType, SimpleNamespace

import pytest

from broker.base_broker import (
    ERROR_AUTH_EXPIRED,
    ERROR_AUTH_INVALID,
    ERROR_CONFIG_INVALID,
    ERROR_CONNECTION_FAILED,
    ERROR_INTERNAL_UNHANDLED,
    ERROR_ORDER_NOT_FOUND,
    ERROR_ORDER_REJECTED,
    ERROR_RATE_LIMIT_EXCEEDED,
    ERROR_REQUEST_BATCH_TOO_LARGE,
    ERROR_REQUEST_INVALID,
    BrokerAuthenticationError,
    BrokerCapabilityError,
    BrokerClientError,
    BrokerConfigurationError,
    BrokerConnectionError,
    BrokerId,
    BrokerOrderError,
    BrokerRateLimitError,
    BrokerRequestError,
    BrokerSession,
    CancelOrderRequest,
    ConnectionState,
    Exchange,
    HistoricalRequest,
    InstrumentRequest,
    MarginPreviewRequest,
    ModifyOrderRequest,
    OrderQueryRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    OrderVariety,
    PlaceOrderRequest,
    ProductType,
    QuoteRequest,
    SessionState,
    WebSocketState,
)
from broker.zerodha._kite_copy import copy_to_immutable
from broker.zerodha._kite_errors import map_kite_exception, _sanitize_message
from broker.zerodha._kite_mappers import (
    cache_instrument_tokens,
    map_exchange,
    map_order_variety,
    map_product,
    split_instrument_key,
)
from broker.zerodha._kite_policy import KiteBrokerPolicy, KiteWebSocketTickMode
from broker.zerodha._kite_rest import KiteRestGateway, RateLimiter
from broker.zerodha._kite_ws import KiteWebSocketGateway
from broker.zerodha.kite_broker import KiteBrokerClient


UTC = timezone.utc


class TokenException(Exception):
    """Mock Kite token exception."""


TokenException.__module__ = "kiteconnect.exceptions"


class NetworkException(Exception):
    """Mock Kite network exception."""


NetworkException.__module__ = "kiteconnect.exceptions"


class OrderException(Exception):
    """Mock Kite order exception."""


OrderException.__module__ = "kiteconnect.exceptions"


class PermissionException(Exception):
    """Mock Kite permission exception."""


PermissionException.__module__ = "kiteconnect.exceptions"


class DataException(Exception):
    """Mock Kite data exception."""


DataException.__module__ = "kiteconnect.exceptions"


class InputException(Exception):
    """Mock Kite input exception."""


InputException.__module__ = "kiteconnect.exceptions"


def utc_now() -> datetime:
    return datetime.now(UTC)


def make_session(**overrides: object) -> BrokerSession:
    credentials = MappingProxyType(
        {
            "api_key": "test-api-key",
            "access_token": "test-access-token",
        }
    )
    defaults: dict[str, object] = {
        "broker_id": BrokerId.KITE,
        "session_id": "session-1",
        "authenticated_at": utc_now(),
        "credentials": credentials,
        "expires_at": None,
    }
    defaults.update(overrides)
    return BrokerSession(
        broker_id=defaults["broker_id"],  # type: ignore[arg-type]
        session_id=defaults["session_id"],  # type: ignore[arg-type]
        authenticated_at=defaults["authenticated_at"],  # type: ignore[arg-type]
        credentials=defaults["credentials"],  # type: ignore[arg-type]
        expires_at=defaults["expires_at"],  # type: ignore[arg-type]
    )


class MockKiteConnect:
    """Mock KiteConnect REST client."""

    MODE_FULL = "full"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.access_token: str | None = None

    def set_access_token(self, access_token: str) -> None:
        self.access_token = access_token

    def profile(self) -> dict[str, object]:
        return {
            "user_id": "AB1234",
            "user_name": "Test User",
            "broker": "ZERODHA",
            "exchanges": ["NSE", "NFO"],
            "products": ["MIS", "NRML"],
            "email": "user@example.com",
        }

    def instruments(self, exchange: str) -> list[dict[str, object]]:
        return [
            {
                "instrument_token": 256265,
                "exchange": exchange,
                "tradingsymbol": "NIFTY 50",
                "name": "NIFTY 50",
                "instrument_type": "EQ",
            }
        ]

    def quote(self, keys: list[str]) -> dict[str, dict[str, object]]:
        return {key: {"last_price": 25000.0} for key in keys}

    def ltp(self, keys: list[str]) -> dict[str, dict[str, object]]:
        return {key: {"last_price": 25000.0} for key in keys}

    def ohlc(self, keys: list[str]) -> dict[str, dict[str, object]]:
        return {key: {"open": 1.0, "close": 2.0} for key in keys}

    def historical_data(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        *,
        continuous: bool = False,
    ) -> list[dict[str, object]]:
        return [{"date": from_date, "open": 1.0, "close": 2.0, "volume": 10}]

    def place_order(self, **kwargs) -> dict[str, str]:
        return {"order_id": "ORD-1"}

    def modify_order(self, **kwargs) -> dict[str, str]:
        return {"order_id": kwargs["order_id"]}

    def cancel_order(self, **kwargs) -> str:
        return kwargs["order_id"]

    def orders(self) -> list[dict[str, object]]:
        return [
            {
                "order_id": "ORD-1",
                "exchange": "NFO",
                "tradingsymbol": "NIFTY24AUG25000CE",
                "transaction_type": "BUY",
                "order_type": "LIMIT",
                "product": "MIS",
                "quantity": 50,
                "status": "OPEN",
                "price": 100.0,
                "variety": "regular",
            }
        ]

    def positions(self) -> dict[str, list[dict[str, object]]]:
        return {
            "net": [
                {
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY24AUG25000CE",
                    "product": "MIS",
                    "quantity": 50,
                    "average_price": 100.0,
                    "last_price": 105.0,
                    "pnl": 250.0,
                    "instrument_token": 123,
                }
            ]
        }

    def holdings(self) -> list[dict[str, object]]:
        return [
            {
                "exchange": "NSE",
                "tradingsymbol": "INFY",
                "quantity": 10,
                "average_price": 1500.0,
            }
        ]

    def margins(self) -> dict[str, object]:
        return {
            "equity": {
                "available": {"live_balance": 100000.0, "span": 1.0, "exposure": 2.0},
                "utilised": {"debits": 10000.0},
                "net": 110000.0,
            },
            "commodity": {"available": {"live_balance": 5000.0}},
        }

    def order_margins(self, orders: list[dict[str, object]]) -> dict[str, object]:
        return {
            "available": {"live_balance": 90000.0},
            "utilised": {"debits": 20000.0},
            "net": 110000.0,
        }


class MockKiteTicker:
    """Mock KiteTicker WebSocket client."""

    MODE_FULL = "full"
    MODE_QUOTE = "quote"

    def __init__(self, api_key: str, access_token: str) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.on_ticks = None
        self.on_connect = None
        self.on_close = None
        self.on_error = None
        self.on_reconnect = None
        self.on_noreconnect = None

    def connect(self, threaded: bool = True) -> None:
        if self.on_connect is not None:
            self.on_connect()

    def close(self) -> None:
        if self.on_close is not None:
            self.on_close()

    def subscribe(self, tokens: list[int]) -> None:
        return None

    def unsubscribe(self, tokens: list[int]) -> None:
        return None

    def set_mode(self, mode: str, tokens: list[int]) -> None:
        return None

    def simulate_tick(self, tick: dict[str, object]) -> None:
        if self.on_ticks is not None:
            self.on_ticks([tick])


@pytest.fixture
def connected_client() -> KiteBrokerClient:
    client = KiteBrokerClient(
        make_session(),
        KiteBrokerPolicy(enable_ohlc_batch=True, enable_funds_breakdown=True),
        kite_connect_factory=lambda api_key: MockKiteConnect(api_key),
        kite_ticker_factory=lambda api_key, token: MockKiteTicker(api_key, token),
    )
    client.connect()
    return client


def test_invalid_broker_id_raises() -> None:
    session = BrokerSession(
        broker_id=BrokerId.MOCK,
        session_id="s1",
        authenticated_at=utc_now(),
        credentials=MappingProxyType({"api_key": "k", "access_token": "t"}),
    )
    with pytest.raises(BrokerConfigurationError) as exc_info:
        KiteBrokerClient(session)
    assert exc_info.value.code == ERROR_CONFIG_INVALID


def test_missing_credentials_raise() -> None:
    session = BrokerSession(
        broker_id=BrokerId.KITE,
        session_id="s1",
        authenticated_at=utc_now(),
        credentials=MappingProxyType({"api_key": "k"}),
    )
    with pytest.raises(BrokerConfigurationError):
        KiteBrokerClient(session)


def test_connect_and_disconnect(connected_client: KiteBrokerClient) -> None:
    assert connected_client.is_connected() is True
    assert connected_client.is_authenticated() is True
    connected_client.disconnect()
    assert connected_client.is_connected() is False


def test_connect_auth_failure() -> None:
    class FailingKite(MockKiteConnect):
        def profile(self) -> dict[str, object]:
            raise TokenException("invalid token")

    client = KiteBrokerClient(
        make_session(),
        kite_connect_factory=lambda api_key: FailingKite(api_key),
        kite_ticker_factory=lambda api_key, token: MockKiteTicker(api_key, token),
    )
    with pytest.raises(BrokerAuthenticationError) as exc_info:
        client.connect()
    assert exc_info.value.code == ERROR_AUTH_EXPIRED
    assert client.get_session_state() is SessionState.EXPIRED


def test_fetch_instruments_quotes_ltp(connected_client: KiteBrokerClient) -> None:
    instruments = connected_client.fetch_instruments(
        InstrumentRequest(exchange=Exchange.NSE)
    )
    assert len(instruments) == 1
    quotes = connected_client.fetch_quotes(
        QuoteRequest(instrument_keys=("NSE:NIFTY 50",))
    )
    assert quotes["NSE:NIFTY 50"]["last_price"] == 25000.0
    ltp = connected_client.fetch_ltp(QuoteRequest(instrument_keys=("NSE:NIFTY 50",)))
    assert ltp["NSE:NIFTY 50"]["last_price"] == 25000.0


def test_fetch_ohlc(connected_client: KiteBrokerClient) -> None:
    result = connected_client.fetch_ohlc(
        QuoteRequest(instrument_keys=("NSE:NIFTY 50",))
    )
    assert result["NSE:NIFTY 50"]["close"] == 2.0


def test_fetch_historical_requires_cached_token(connected_client: KiteBrokerClient) -> None:
    connected_client.fetch_instruments(InstrumentRequest(exchange=Exchange.NSE))
    candles = connected_client.fetch_historical(
        HistoricalRequest(
            instrument_key="NSE:NIFTY 50",
            interval="minute",
            from_ts=utc_now() - timedelta(days=1),
            to_ts=utc_now(),
        )
    )
    assert len(candles) == 1


def test_fetch_historical_missing_token_raises(connected_client: KiteBrokerClient) -> None:
    with pytest.raises(BrokerRequestError) as exc_info:
        connected_client.fetch_historical(
            HistoricalRequest(
                instrument_key="NSE:UNKNOWN",
                interval="minute",
                from_ts=utc_now() - timedelta(days=1),
                to_ts=utc_now(),
            )
        )
    assert exc_info.value.code == ERROR_REQUEST_INVALID


def test_websocket_subscribe_and_tick(connected_client: KiteBrokerClient) -> None:
    received: list[dict[str, object]] = []
    connected_client.set_tick_handler(lambda tick: received.append(dict(tick)))
    connected_client.subscribe((256265,))
    assert connected_client.get_subscribed_tokens() == frozenset({256265})
    ticker = connected_client._ws._ticker  # noqa: SLF001
    ticker.simulate_tick({"instrument_token": 256265, "last_price": 25001.0})
    assert received[0]["last_price"] == 25001.0
    connected_client.unsubscribe((256265,))
    assert connected_client.get_subscribed_tokens() == frozenset()


def test_place_modify_cancel_orders(connected_client: KiteBrokerClient) -> None:
    placed = connected_client.place_order(
        PlaceOrderRequest(
            instrument_key="NFO:NIFTY24AUG25000CE",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            product=ProductType.MIS,
            quantity=50,
            price=100.0,
            idempotency_key="idem-1",
        )
    )
    assert placed.order_id == "ORD-1"
    placed_again = connected_client.place_order(
        PlaceOrderRequest(
            instrument_key="NFO:NIFTY24AUG25000CE",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            product=ProductType.MIS,
            quantity=50,
            price=100.0,
            idempotency_key="idem-1",
        )
    )
    assert placed_again.order_id == "ORD-1"
    modified = connected_client.modify_order(
        ModifyOrderRequest(order_id="ORD-1", quantity=25)
    )
    assert modified.quantity == 50
    cancelled = connected_client.cancel_order(CancelOrderRequest(order_id="ORD-1"))
    assert cancelled.order_id == "ORD-1"


def test_fetch_positions_holdings_margins_profile_funds(
    connected_client: KiteBrokerClient,
) -> None:
    positions = connected_client.fetch_positions()
    assert positions[0].quantity == 50
    holdings = connected_client.fetch_holdings()
    assert holdings[0].instrument_key == "NSE:INFY"
    margins = connected_client.fetch_margins()
    assert margins.available == 100000.0
    profile = connected_client.fetch_profile()
    assert profile.user_id == "AB1234"
    funds = connected_client.fetch_funds()
    assert funds.equity_available == 100000.0


def test_preview_margin_when_enabled(connected_client: KiteBrokerClient) -> None:
    client = KiteBrokerClient(
        make_session(),
        KiteBrokerPolicy(enable_margin_preview=True),
        kite_connect_factory=lambda api_key: MockKiteConnect(api_key),
        kite_ticker_factory=lambda api_key, token: MockKiteTicker(api_key, token),
    )
    client.connect()
    preview = client.preview_margin(
        MarginPreviewRequest(
            orders=(
                PlaceOrderRequest(
                    instrument_key="NFO:NIFTY24AUG25000CE",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    product=ProductType.MIS,
                    quantity=50,
                    price=100.0,
                ),
            )
        )
    )
    assert preview.available == 90000.0


def test_optional_capabilities_disabled_by_default() -> None:
    client = KiteBrokerClient(
        make_session(),
        kite_connect_factory=lambda api_key: MockKiteConnect(api_key),
        kite_ticker_factory=lambda api_key, token: MockKiteTicker(api_key, token),
    )
    with pytest.raises(BrokerCapabilityError):
        client.preview_margin(MarginPreviewRequest())


def test_disconnected_fetch_raises() -> None:
    client = KiteBrokerClient(
        make_session(),
        kite_connect_factory=lambda api_key: MockKiteConnect(api_key),
        kite_ticker_factory=lambda api_key, token: MockKiteTicker(api_key, token),
    )
    with pytest.raises(BrokerConnectionError):
        client.fetch_quotes(QuoteRequest(instrument_keys=("NSE:NIFTY 50",)))


def test_expired_session_hint_raises() -> None:
    session = make_session(expires_at=utc_now() - timedelta(minutes=1))
    client = KiteBrokerClient(
        session,
        kite_connect_factory=lambda api_key: MockKiteConnect(api_key),
        kite_ticker_factory=lambda api_key, token: MockKiteTicker(api_key, token),
    )
    client.connect()
    with pytest.raises(BrokerAuthenticationError) as exc_info:
        client.fetch_quotes(QuoteRequest(instrument_keys=("NSE:NIFTY 50",)))
    assert exc_info.value.code == ERROR_AUTH_EXPIRED


def test_update_session(connected_client: KiteBrokerClient) -> None:
    new_session = make_session(session_id="session-2")
    connected_client.update_session(new_session)
    assert connected_client.session.session_id == "session-2"


def test_error_mapping() -> None:
    mapped = map_kite_exception(OrderException("rejected"))
    assert mapped.code == ERROR_ORDER_REJECTED


def test_immutable_copy() -> None:
    payload = {"a": {"b": 1}, "c": [1, 2]}
    copied = copy_to_immutable(payload)
    with pytest.raises(TypeError):
        copied["a"] = {}  # type: ignore[index]


def test_metadata(connected_client: KiteBrokerClient) -> None:
    metadata = connected_client.metadata()
    assert metadata.broker_id is BrokerId.KITE


def test_thread_safe_subscribe(connected_client: KiteBrokerClient) -> None:
    def worker(token: int) -> None:
        connected_client.subscribe((token,))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(connected_client.get_subscribed_tokens()) == 20


def test_fetch_orders_filter(connected_client: KiteBrokerClient) -> None:
    orders = connected_client.fetch_orders(OrderQueryRequest(order_id="ORD-1"))
    assert orders[0].status is OrderStatus.OPEN


def test_place_order_failure() -> None:
    class RejectingKite(MockKiteConnect):
        def place_order(self, **kwargs) -> dict[str, str]:
            raise OrderException("margin insufficient")

    client = KiteBrokerClient(
        make_session(),
        kite_connect_factory=lambda api_key: RejectingKite(api_key),
        kite_ticker_factory=lambda api_key, token: MockKiteTicker(api_key, token),
    )
    client.connect()
    with pytest.raises(BrokerOrderError):
        client.place_order(
            PlaceOrderRequest(
                instrument_key="NFO:NIFTY24AUG25000CE",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                product=ProductType.MIS,
                quantity=50,
                price=100.0,
            )
        )


def test_policy_ws_tick_mode_enum() -> None:
    policy = KiteBrokerPolicy(ws_tick_mode=KiteWebSocketTickMode.QUOTE)
    assert policy.ws_tick_mode is KiteWebSocketTickMode.QUOTE


def test_error_mapping_all_kite_exceptions() -> None:
    assert map_kite_exception(PermissionException("denied")).code == ERROR_AUTH_INVALID
    assert map_kite_exception(NetworkException("offline")).code == ERROR_CONNECTION_FAILED
    assert map_kite_exception(DataException("bad data")).code == ERROR_REQUEST_INVALID
    assert map_kite_exception(InputException("bad input")).code == ERROR_REQUEST_INVALID
    assert map_kite_exception(Exception("unknown")).code == ERROR_INTERNAL_UNHANDLED
    existing = BrokerClientError("already mapped", code=ERROR_INTERNAL_UNHANDLED)
    assert map_kite_exception(existing) is existing


def test_sanitize_message_strips_secrets() -> None:
    assert _sanitize_message("failed api_key=secret") == "kite request failed"
    assert _sanitize_message("plain error") == "plain error"


def test_immutable_copy_tuple_branch() -> None:
    copied = copy_to_immutable((1, {"a": 2}))
    assert copied == (1, MappingProxyType({"a": 2}))


def test_mapper_edge_cases() -> None:
    with pytest.raises(ValueError):
        split_instrument_key("INVALID")
    assert map_exchange("UNKNOWN_EX") is Exchange.UNKNOWN
    assert map_product("UNKNOWN_PRODUCT") is ProductType.MIS
    assert map_order_variety("unknown") is OrderVariety.REGULAR
    assert map_order_variety(None) is OrderVariety.REGULAR
    assert cache_instrument_tokens(({"exchange": "NSE"},)) == {}


def test_empty_api_key_raises() -> None:
    session = BrokerSession(
        broker_id=BrokerId.KITE,
        session_id="s1",
        authenticated_at=utc_now(),
        credentials=MappingProxyType({"api_key": "  ", "access_token": "token"}),
    )
    with pytest.raises(BrokerConfigurationError):
        KiteBrokerClient(session)


def test_get_connection_info_and_handlers(connected_client: KiteBrokerClient) -> None:
    connection_events: list[ConnectionState] = []
    errors: list[BrokerClientError] = []

    connected_client.set_connection_handler(
        lambda info: connection_events.append(info.state)
    )
    connected_client.set_error_handler(errors.append)

    info = connected_client.get_connection_info()
    assert info.websocket_state is WebSocketState.OPEN

    ticker = connected_client._ws._ticker  # noqa: SLF001
    ticker.on_close()
    assert ConnectionState.DISCONNECTED in connection_events

    ticker.on_reconnect()
    assert ConnectionState.RECONNECTING in connection_events

    ticker.on_error(None, 1006, "closed")
    assert errors

    ticker.on_noreconnect()
    assert len(errors) == 2


def test_tick_handler_failure_is_isolated(connected_client: KiteBrokerClient) -> None:
    def failing_handler(_tick: object) -> None:
        raise RuntimeError("handler failed")

    connected_client.set_tick_handler(failing_handler)
    connected_client.subscribe((256265,))
    ticker = connected_client._ws._ticker  # noqa: SLF001
    ticker.simulate_tick({"instrument_token": 256265, "last_price": 1.0})


def test_subscription_limit_raises(connected_client: KiteBrokerClient) -> None:
    connected_client._policy = KiteBrokerPolicy(max_subscribed_tokens=1)  # noqa: SLF001
    connected_client._ws._policy = connected_client._policy  # noqa: SLF001
    connected_client.subscribe((1,))
    with pytest.raises(BrokerRequestError) as exc_info:
        connected_client.subscribe((2,))
    assert exc_info.value.code == ERROR_REQUEST_BATCH_TOO_LARGE


def test_modify_order_not_found_raises(connected_client: KiteBrokerClient) -> None:
    class EmptyOrdersKite(MockKiteConnect):
        def orders(self) -> list[dict[str, object]]:
            return []

    connected_client._rest._kite = EmptyOrdersKite("key")  # noqa: SLF001
    with pytest.raises(BrokerOrderError) as exc_info:
        connected_client.modify_order(ModifyOrderRequest(order_id="ORD-404", quantity=1))
    assert exc_info.value.code == ERROR_ORDER_NOT_FOUND


def test_cancel_order_fallback_record(connected_client: KiteBrokerClient) -> None:
    class EmptyOrdersKite(MockKiteConnect):
        def orders(self) -> list[dict[str, object]]:
            return []

        def cancel_order(self, **kwargs) -> str:
            return "ORD-GONE"

    connected_client._rest._kite = EmptyOrdersKite("key")  # noqa: SLF001
    cancelled = connected_client.cancel_order(CancelOrderRequest(order_id="ORD-GONE"))
    assert cancelled.order_id == "ORD-GONE"
    assert cancelled.status is OrderStatus.CANCELLED


def test_rest_retry_on_transient_network_error() -> None:
    attempts = {"count": 0}

    class FlakyKite(MockKiteConnect):
        def profile(self) -> dict[str, object]:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise NetworkException("temporary")
            return super().profile()

    gateway = KiteRestGateway(
        api_key="key",
        access_token="token",
        policy=KiteBrokerPolicy(retry_max_attempts=3, retry_initial_delay_ms=1),
        kite_factory=lambda api_key: FlakyKite(api_key),
    )
    profile = gateway.profile()
    assert profile["user_id"] == "AB1234"
    assert attempts["count"] == 2


def test_rate_limiter_raises_when_wait_exceeded() -> None:
    limiter = RateLimiter(rate_per_second=1000.0, max_wait_seconds=0.0)
    limiter.acquire()
    with pytest.raises(BrokerRateLimitError):
        limiter.acquire()


def test_rate_limiter_disabled_when_rate_zero() -> None:
    limiter = RateLimiter(rate_per_second=0.0, max_wait_seconds=1.0)
    limiter.acquire()
    limiter.acquire()


def test_rest_non_list_responses_return_empty() -> None:
    class BadShapeKite(MockKiteConnect):
        def instruments(self, exchange: str) -> object:
            return "not-a-list"

        def historical_data(self, *args, **kwargs) -> object:
            return "not-a-list"

        def positions(self) -> dict[str, object]:
            return {"net": "not-a-list"}

    gateway = KiteRestGateway(
        api_key="key",
        access_token="token",
        policy=KiteBrokerPolicy(),
        kite_factory=lambda api_key: BadShapeKite(api_key),
    )
    assert gateway.instruments(InstrumentRequest(exchange=Exchange.NSE)) == ()
    assert (
        gateway.historical_data(
            HistoricalRequest(
                instrument_key="NSE:NIFTY 50",
                interval="minute",
                from_ts=utc_now() - timedelta(days=1),
                to_ts=utc_now(),
            ),
            256265,
        )
        == ()
    )
    assert gateway.positions() == ()


def test_ws_gateway_direct_callbacks() -> None:
    ws = KiteWebSocketGateway(
        api_key="key",
        access_token="token",
        policy=KiteBrokerPolicy(),
        ticker_factory=lambda api_key, token: MockKiteTicker(api_key, token),
    )
    connection_events: list[ConnectionState] = []
    ws.set_connection_handler(lambda info: connection_events.append(info.state))
    ws.connect()
    ws._on_connect()  # noqa: SLF001
    assert ws.get_connection_info().websocket_state is WebSocketState.OPEN
    ws._on_ticks([])  # noqa: SLF001 - no handler registered
