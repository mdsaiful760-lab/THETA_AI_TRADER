"""Unit tests for broker.base_broker."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Mapping

import pytest

from broker.base_broker import (
    BROKER_CLIENT_VERSION,
    ERROR_AUTH_EXPIRED,
    ERROR_AUTH_INVALID,
    ERROR_AUTH_REVOKED,
    ERROR_CAPABILITY_UNSUPPORTED,
    ERROR_CONFIG_INVALID,
    ERROR_CONFIG_MISSING_SESSION,
    ERROR_CONNECTION_DISCONNECTED,
    ERROR_ORDER_NOT_FOUND,
    ERROR_ORDER_REJECTED,
    ERROR_REQUEST_INVALID,
    AccountProfile,
    BaseBrokerClient,
    BrokerAuthenticationError,
    BrokerCapabilityError,
    BrokerClient,
    BrokerClientError,
    BrokerClientMetadata,
    BrokerConfigurationError,
    BrokerConnectionError,
    BrokerId,
    BrokerOrderError,
    BrokerRateLimitError,
    BrokerRequestError,
    BrokerSession,
    BrokerTimeoutError,
    BrokerCapabilities,
    CancelOrderRequest,
    ConnectionInfo,
    ConnectionState,
    Exchange,
    FundsSnapshot,
    HistoricalRequest,
    InstrumentRequest,
    MarginPreviewRequest,
    MarginSnapshot,
    ModifyOrderRequest,
    OrderQueryRequest,
    OrderRecord,
    OrderSide,
    OrderStatus,
    OrderType,
    PlaceOrderRequest,
    PlaceOrderResult,
    PositionRecord,
    ProductType,
    QuoteRequest,
    SessionState,
    WebSocketState,
    validate_broker_session,
    validate_place_order_request,
    validate_quote_request,
)

UTC = timezone.utc


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def make_session(
    *,
    broker_id: BrokerId = BrokerId.MOCK,
    session_id: str = "session-1",
    expires_at: datetime | None = None,
) -> BrokerSession:
    """Build a valid broker session for tests."""
    return BrokerSession(
        broker_id=broker_id,
        session_id=session_id,
        authenticated_at=utc_now(),
        credentials=MappingProxyType({"token": "opaque"}),
        expires_at=expires_at,
    )


class FakeBrokerClient(BaseBrokerClient):
    """In-memory broker client for contract and integration tests."""

    def __init__(
        self,
        session: BrokerSession,
        *,
        capabilities: BrokerCapabilities | None = None,
    ) -> None:
        super().__init__(session)
        self._capabilities = capabilities or BrokerCapabilities(
            margin_preview=True,
            holdings=True,
            funds_breakdown=True,
            ohlc_batch=True,
        )
        self._lock = threading.RLock()
        self._connected = False
        self._session_state = SessionState.UNAUTHENTICATED
        self._connection_state = ConnectionState.DISCONNECTED
        self._websocket_state = WebSocketState.CLOSED
        self._connected_since: datetime | None = None
        self._subscribed: set[int] = set()
        self._tick_handler = None
        self._error_handler = None
        self._connection_handler = None
        self._orders: dict[str, OrderRecord] = {}
        self._next_order_id = 1
        self._instruments: dict[Exchange, tuple[Mapping[str, object], ...]] = {
            Exchange.NFO: (
                MappingProxyType(
                    {"instrument_token": 1, "tradingsymbol": "NIFTY24AUG25000CE"}
                ),
            )
        }
        self._quotes: dict[str, Mapping[str, object]] = {
            "NSE:NIFTY 50": MappingProxyType({"last_price": 25000.0}),
        }
        self._ltp: dict[str, Mapping[str, object]] = {
            "NSE:NIFTY 50": MappingProxyType({"last_price": 25000.0}),
        }
        self._historical: tuple[Mapping[str, object], ...] = (
            MappingProxyType({"open": 1.0, "close": 2.0}),
        )
        self._positions: tuple[PositionRecord, ...] = ()
        self._holdings: tuple = ()
        self._margins = MarginSnapshot(
            available=100000.0,
            used=10000.0,
            total=110000.0,
            as_of=utc_now(),
        )
        self._profile = AccountProfile(
            user_id="user-1",
            user_name="Test User",
            broker="mock",
            exchanges=(Exchange.NSE, Exchange.NFO),
            products=(ProductType.MIS, ProductType.NRML),
        )
        self._funds = FundsSnapshot(
            equity_available=90000.0,
            commodity_available=0.0,
            as_of=utc_now(),
        )

    @property
    def broker_id(self) -> BrokerId:
        return BrokerId.MOCK

    @property
    def client_version(self) -> str:
        return "1.0.0-test"

    @property
    def capabilities(self) -> BrokerCapabilities:
        return self._capabilities

    def metadata(self) -> BrokerClientMetadata:
        return BrokerClientMetadata(
            broker_id=self.broker_id,
            client_version=self.client_version,
            capabilities=self.capabilities,
        )

    def connect(self) -> None:
        with self._lock:
            self._connected = True
            self._session_state = SessionState.AUTHENTICATED
            self._connection_state = ConnectionState.CONNECTED
            self._websocket_state = WebSocketState.OPEN
            self._connected_since = utc_now()
            info = self.get_connection_info()
        if self._connection_handler is not None:
            self._connection_handler(info)

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
            self._connection_state = ConnectionState.DISCONNECTED
            self._websocket_state = WebSocketState.CLOSED
            self._subscribed.clear()
            info = self.get_connection_info()
        if self._connection_handler is not None:
            self._connection_handler(info)

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def get_connection_info(self) -> ConnectionInfo:
        with self._lock:
            return ConnectionInfo(
                state=self._connection_state,
                since=self._connected_since,
                websocket_state=self._websocket_state,
            )

    def is_authenticated(self) -> bool:
        with self._lock:
            return self._session_state is SessionState.AUTHENTICATED

    def get_session_state(self) -> SessionState:
        with self._lock:
            return self._session_state

    def session_expires_at(self) -> datetime | None:
        return self.session.expires_at

    def fetch_instruments(
        self,
        request: InstrumentRequest,
    ) -> tuple[Mapping[str, object], ...]:
        self._require_connected()
        self._require_authenticated()
        return self._instruments.get(request.exchange, ())

    def fetch_quotes(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        self._require_connected()
        self._require_authenticated()
        validate_quote_request(request)
        return {
            key: self._quotes.get(key, MappingProxyType({}))
            for key in request.instrument_keys
        }

    def fetch_ltp(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        self._require_connected()
        self._require_authenticated()
        validate_quote_request(request)
        return {
            key: self._ltp.get(key, MappingProxyType({}))
            for key in request.instrument_keys
        }

    def _fetch_ohlc_impl(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        self._require_connected()
        self._require_authenticated()
        validate_quote_request(request)
        return {
            key: MappingProxyType({"open": 1.0, "close": 2.0})
            for key in request.instrument_keys
        }

    def fetch_historical(
        self,
        request: HistoricalRequest,
    ) -> tuple[Mapping[str, object], ...]:
        self._require_connected()
        self._require_authenticated()
        return self._historical

    def subscribe(self, instrument_tokens: tuple[int, ...]) -> None:
        self._require_connected()
        self._require_authenticated()
        with self._lock:
            self._subscribed.update(instrument_tokens)

    def unsubscribe(self, instrument_tokens: tuple[int, ...]) -> None:
        self._require_connected()
        with self._lock:
            self._subscribed.difference_update(instrument_tokens)

    def get_subscribed_tokens(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._subscribed)

    def set_tick_handler(self, handler) -> None:
        with self._lock:
            self._tick_handler = handler

    def set_error_handler(self, handler) -> None:
        with self._lock:
            self._error_handler = handler

    def set_connection_handler(self, handler) -> None:
        with self._lock:
            self._connection_handler = handler

    def deliver_tick(self, tick: Mapping[str, object]) -> None:
        """Test helper to simulate a WebSocket tick."""
        with self._lock:
            handler = self._tick_handler
        if handler is not None:
            handler(tick)

    def place_order(self, request: PlaceOrderRequest) -> PlaceOrderResult:
        self._require_connected()
        self._require_authenticated()
        validate_place_order_request(request)
        order_id = str(self._next_order_id)
        self._next_order_id += 1
        record = OrderRecord(
            order_id=order_id,
            instrument_key=request.instrument_key,
            side=request.side,
            order_type=request.order_type,
            product=request.product,
            quantity=request.quantity,
            status=OrderStatus.OPEN,
            price=request.price,
            trigger_price=request.trigger_price,
            variety=request.variety,
        )
        self._orders[order_id] = record
        return PlaceOrderResult(
            order_id=order_id,
            status=OrderStatus.OPEN,
            message="accepted",
            broker_order_id=f"broker-{order_id}",
        )

    def modify_order(self, request: ModifyOrderRequest) -> OrderRecord:
        self._require_connected()
        self._require_authenticated()
        record = self._orders.get(request.order_id)
        if record is None:
            raise BrokerOrderError(
                "order not found",
                code=ERROR_ORDER_NOT_FOUND,
                broker_id=self.broker_id,
            )
        updated = OrderRecord(
            order_id=record.order_id,
            instrument_key=record.instrument_key,
            side=record.side,
            order_type=record.order_type,
            product=record.product,
            quantity=request.quantity or record.quantity,
            status=record.status,
            price=request.price if request.price is not None else record.price,
            trigger_price=(
                request.trigger_price
                if request.trigger_price is not None
                else record.trigger_price
            ),
            variety=request.variety or record.variety,
        )
        self._orders[request.order_id] = updated
        return updated

    def cancel_order(self, request: CancelOrderRequest) -> OrderRecord:
        self._require_connected()
        self._require_authenticated()
        record = self._orders.get(request.order_id)
        if record is None:
            raise BrokerOrderError(
                "order not found",
                code=ERROR_ORDER_NOT_FOUND,
                broker_id=self.broker_id,
            )
        cancelled = OrderRecord(
            order_id=record.order_id,
            instrument_key=record.instrument_key,
            side=record.side,
            order_type=record.order_type,
            product=record.product,
            quantity=record.quantity,
            status=OrderStatus.CANCELLED,
            price=record.price,
            trigger_price=record.trigger_price,
            variety=request.variety or record.variety,
        )
        self._orders[request.order_id] = cancelled
        return cancelled

    def fetch_orders(
        self,
        request: OrderQueryRequest,
    ) -> tuple[OrderRecord, ...]:
        self._require_connected()
        self._require_authenticated()
        if request.order_id is None:
            return tuple(self._orders.values())
        record = self._orders.get(request.order_id)
        return (record,) if record is not None else ()

    def fetch_positions(self) -> tuple[PositionRecord, ...]:
        self._require_connected()
        self._require_authenticated()
        return self._positions

    def _fetch_holdings_impl(self) -> tuple:
        self._require_connected()
        self._require_authenticated()
        return self._holdings

    def fetch_margins(self) -> MarginSnapshot:
        self._require_connected()
        self._require_authenticated()
        return self._margins

    def _preview_margin_impl(self, request: MarginPreviewRequest) -> MarginSnapshot:
        self._require_connected()
        self._require_authenticated()
        return self._margins

    def fetch_profile(self) -> AccountProfile:
        self._require_connected()
        self._require_authenticated()
        return self._profile

    def _fetch_funds_impl(self) -> FundsSnapshot:
        self._require_connected()
        self._require_authenticated()
        return self._funds


class MinimalBrokerClient(BaseBrokerClient):
    """Minimal concrete client using base optional defaults."""

    @property
    def broker_id(self) -> BrokerId:
        return BrokerId.MOCK

    @property
    def client_version(self) -> str:
        return "0.0.1"

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
        return None

    def disconnect(self) -> None:
        return None

    def is_connected(self) -> bool:
        return False

    def get_connection_info(self) -> ConnectionInfo:
        return ConnectionInfo(
            state=ConnectionState.DISCONNECTED,
            since=None,
            websocket_state=WebSocketState.CLOSED,
        )

    def is_authenticated(self) -> bool:
        return False

    def get_session_state(self) -> SessionState:
        return SessionState.UNAUTHENTICATED

    def session_expires_at(self) -> datetime | None:
        return None

    def fetch_instruments(
        self,
        request: InstrumentRequest,
    ) -> tuple[Mapping[str, object], ...]:
        return ()

    def fetch_quotes(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        return {}

    def fetch_ltp(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        return {}

    def fetch_historical(
        self,
        request: HistoricalRequest,
    ) -> tuple[Mapping[str, object], ...]:
        return ()

    def subscribe(self, instrument_tokens: tuple[int, ...]) -> None:
        return None

    def unsubscribe(self, instrument_tokens: tuple[int, ...]) -> None:
        return None

    def get_subscribed_tokens(self) -> frozenset[int]:
        return frozenset()

    def set_tick_handler(self, handler) -> None:
        return None

    def set_error_handler(self, handler) -> None:
        return None

    def set_connection_handler(self, handler) -> None:
        return None

    def place_order(self, request: PlaceOrderRequest) -> PlaceOrderResult:
        raise BrokerOrderError("not implemented", broker_id=self.broker_id)

    def modify_order(self, request: ModifyOrderRequest) -> OrderRecord:
        raise BrokerOrderError("not implemented", broker_id=self.broker_id)

    def cancel_order(self, request: CancelOrderRequest) -> OrderRecord:
        raise BrokerOrderError("not implemented", broker_id=self.broker_id)

    def fetch_orders(
        self,
        request: OrderQueryRequest,
    ) -> tuple[OrderRecord, ...]:
        return ()

    def fetch_positions(self) -> tuple[PositionRecord, ...]:
        return ()

    def fetch_margins(self) -> MarginSnapshot:
        return MarginSnapshot(available=0.0, used=0.0, total=0.0, as_of=utc_now())

    def fetch_profile(self) -> AccountProfile:
        return AccountProfile(
            user_id="x",
            user_name="x",
            broker="mock",
            exchanges=(),
            products=(),
        )


def test_broker_client_alias() -> None:
    """BrokerClient is an alias for BaseBrokerClient."""
    assert BrokerClient is BaseBrokerClient


def test_version_constant() -> None:
    """Module version constant is defined."""
    assert BROKER_CLIENT_VERSION == "1.0.0"


class TestEnums:
    """Enumeration tests."""

    @pytest.mark.parametrize(
        ("enum_cls", "member", "value"),
        [
            (BrokerId, BrokerId.KITE, "kite"),
            (ConnectionState, ConnectionState.CONNECTED, "connected"),
            (WebSocketState, WebSocketState.OPEN, "open"),
            (SessionState, SessionState.AUTHENTICATED, "authenticated"),
            (OrderSide, OrderSide.BUY, "buy"),
            (OrderType, OrderType.LIMIT, "limit"),
            (ProductType, ProductType.MIS, "mis"),
            (OrderStatus, OrderStatus.OPEN, "open"),
        ],
    )
    def test_str_enum_values(self, enum_cls, member, value) -> None:
        assert isinstance(member, str)
        assert member.value == value
        assert enum_cls(value) is member


class TestSessionValidation:
    """Broker session validation tests."""

    def test_missing_session_raises(self) -> None:
        with pytest.raises(BrokerConfigurationError) as exc_info:
            validate_broker_session(None)
        assert exc_info.value.code == ERROR_CONFIG_MISSING_SESSION

    def test_empty_session_id_raises(self) -> None:
        session = make_session(session_id="  ")
        with pytest.raises(BrokerConfigurationError) as exc_info:
            validate_broker_session(session)
        assert exc_info.value.code == ERROR_CONFIG_INVALID

    def test_naive_authenticated_at_raises(self) -> None:
        session = BrokerSession(
            broker_id=BrokerId.MOCK,
            session_id="s1",
            authenticated_at=datetime(2026, 8, 3, 10, 0, 0),
            credentials=MappingProxyType({}),
        )
        with pytest.raises(BrokerConfigurationError):
            validate_broker_session(session)

    def test_naive_expires_at_raises(self) -> None:
        session = BrokerSession(
            broker_id=BrokerId.MOCK,
            session_id="s1",
            authenticated_at=utc_now(),
            credentials=MappingProxyType({}),
            expires_at=datetime(2026, 8, 3, 10, 0, 0),
        )
        with pytest.raises(BrokerConfigurationError) as exc_info:
            validate_broker_session(session)
        assert exc_info.value.code == ERROR_CONFIG_INVALID

    def test_construction_requires_valid_session(self) -> None:
        with pytest.raises(BrokerConfigurationError):
            FakeBrokerClient(None)  # type: ignore[arg-type]


class TestRequestValidation:
    """Request validation helper tests."""

    def test_empty_quote_request_raises(self) -> None:
        with pytest.raises(BrokerRequestError) as exc_info:
            validate_quote_request(QuoteRequest(instrument_keys=()))
        assert exc_info.value.code == ERROR_REQUEST_INVALID

    def test_invalid_place_order_quantity(self) -> None:
        request = PlaceOrderRequest(
            instrument_key="NSE:ABC",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product=ProductType.MIS,
            quantity=0,
        )
        with pytest.raises(BrokerRequestError):
            validate_place_order_request(request)

    def test_limit_order_requires_price(self) -> None:
        request = PlaceOrderRequest(
            instrument_key="NSE:ABC",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            product=ProductType.MIS,
            quantity=1,
        )
        with pytest.raises(BrokerRequestError):
            validate_place_order_request(request)


    def test_empty_instrument_key_raises(self) -> None:
        request = PlaceOrderRequest(
            instrument_key="  ",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product=ProductType.MIS,
            quantity=1,
        )
        with pytest.raises(BrokerRequestError):
            validate_place_order_request(request)


class TestErrorHierarchy:
    """Exception hierarchy tests."""

    def test_broker_client_error_attributes(self) -> None:
        error = BrokerClientError(
            "failed",
            code=ERROR_REQUEST_INVALID,
            recoverable=True,
            broker_id=BrokerId.MOCK,
        )
        assert error.message == "failed"
        assert error.recoverable is True
        assert error.broker_id is BrokerId.MOCK

    def test_connection_error_defaults(self) -> None:
        error = BrokerConnectionError("down")
        assert error.code == ERROR_CONNECTION_DISCONNECTED
        assert error.recoverable is True

    def test_authentication_error_not_recoverable(self) -> None:
        error = BrokerAuthenticationError("expired", code=ERROR_AUTH_EXPIRED)
        assert error.recoverable is False

    def test_rate_limit_error_recoverable(self) -> None:
        error = BrokerRateLimitError("slow down")
        assert error.recoverable is True

    def test_timeout_error_recoverable(self) -> None:
        error = BrokerTimeoutError("timed out")
        assert error.recoverable is True

    def test_order_error_code(self) -> None:
        error = BrokerOrderError("rejected", code=ERROR_ORDER_REJECTED)
        assert error.code == ERROR_ORDER_REJECTED

    def test_capability_error_code(self) -> None:
        error = BrokerCapabilityError("unsupported")
        assert error.code == ERROR_CAPABILITY_UNSUPPORTED


class TestDTOImmutability:
    """Frozen dataclass tests."""

    def test_broker_session_is_frozen(self) -> None:
        session = make_session()
        with pytest.raises(FrozenInstanceError):
            session.session_id = "changed"  # type: ignore[misc]

    def test_place_order_request_is_frozen(self) -> None:
        request = PlaceOrderRequest(
            instrument_key="NSE:ABC",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product=ProductType.MIS,
            quantity=1,
        )
        with pytest.raises(FrozenInstanceError):
            request.quantity = 2  # type: ignore[misc]


class TestAbstractContract:
    """Abstract base class contract tests."""

    def test_base_broker_client_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            BaseBrokerClient(make_session())  # type: ignore[abstract]

    def test_optional_methods_raise_capability_error(self) -> None:
        client = MinimalBrokerClient(make_session())
        with pytest.raises(BrokerCapabilityError):
            client.fetch_ohlc(QuoteRequest(instrument_keys=("NSE:ABC",)))
        with pytest.raises(BrokerCapabilityError):
            client.fetch_holdings()
        with pytest.raises(BrokerCapabilityError):
            client.preview_margin(MarginPreviewRequest())
        with pytest.raises(BrokerCapabilityError):
            client.fetch_funds()


class TestFakeBrokerClientConnection:
    """Fake broker connection lifecycle tests."""

    def test_connect_disconnect_idempotent(self) -> None:
        client = FakeBrokerClient(make_session())
        assert client.is_connected() is False
        client.connect()
        assert client.is_connected() is True
        assert client.get_session_state() is SessionState.AUTHENTICATED
        client.disconnect()
        assert client.is_connected() is False
        client.disconnect()

    def test_mutating_call_when_disconnected_raises(self) -> None:
        client = FakeBrokerClient(make_session())
        with pytest.raises(BrokerConnectionError):
            client.fetch_quotes(QuoteRequest(instrument_keys=("NSE:NIFTY 50",)))

    def test_update_session(self) -> None:
        client = FakeBrokerClient(make_session())
        new_session = make_session(session_id="session-2")
        client.update_session(new_session)
        assert client.session.session_id == "session-2"


class TestFakeBrokerClientMarketData:
    """Fake broker market data tests."""

    def test_fetch_instruments_quotes_ltp_historical(self) -> None:
        client = FakeBrokerClient(make_session())
        client.connect()
        instruments = client.fetch_instruments(InstrumentRequest(exchange=Exchange.NFO))
        assert len(instruments) == 1
        quotes = client.fetch_quotes(
            QuoteRequest(instrument_keys=("NSE:NIFTY 50",))
        )
        assert "NSE:NIFTY 50" in quotes
        ltp = client.fetch_ltp(QuoteRequest(instrument_keys=("NSE:NIFTY 50",)))
        assert ltp["NSE:NIFTY 50"]["last_price"] == 25000.0
        candles = client.fetch_historical(
            HistoricalRequest(
                instrument_key="NSE:NIFTY 50",
                interval="minute",
                from_ts=utc_now() - timedelta(days=1),
                to_ts=utc_now(),
            )
        )
        assert len(candles) == 1

    def test_fetch_ohlc_when_supported(self) -> None:
        client = FakeBrokerClient(make_session())
        client.connect()
        result = client.fetch_ohlc(QuoteRequest(instrument_keys=("NSE:NIFTY 50",)))
        assert "NSE:NIFTY 50" in result


class TestFakeBrokerClientWebSocket:
    """Fake broker WebSocket tests."""

    def test_subscribe_unsubscribe_and_tick_handler(self) -> None:
        client = FakeBrokerClient(make_session())
        client.connect()
        received: list[Mapping[str, object]] = []
        client.set_tick_handler(lambda tick: received.append(tick))
        client.subscribe((101, 102))
        assert client.get_subscribed_tokens() == frozenset({101, 102})
        client.deliver_tick(MappingProxyType({"instrument_token": 101, "last_price": 1.0}))
        assert len(received) == 1
        client.unsubscribe((101,))
        assert client.get_subscribed_tokens() == frozenset({102})

    def test_connection_handler_invoked(self) -> None:
        client = FakeBrokerClient(make_session())
        states: list[ConnectionState] = []
        client.set_connection_handler(lambda info: states.append(info.state))
        client.connect()
        client.disconnect()
        assert states == [ConnectionState.CONNECTED, ConnectionState.DISCONNECTED]


class TestFakeBrokerClientOrders:
    """Fake broker order tests."""

    def test_place_modify_cancel_round_trip(self) -> None:
        client = FakeBrokerClient(make_session())
        client.connect()
        placed = client.place_order(
            PlaceOrderRequest(
                instrument_key="NFO:NIFTY24AUG25000CE",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                product=ProductType.MIS,
                quantity=50,
                price=100.0,
            )
        )
        assert placed.status is OrderStatus.OPEN
        modified = client.modify_order(
            ModifyOrderRequest(order_id=placed.order_id, quantity=25)
        )
        assert modified.quantity == 25
        cancelled = client.cancel_order(
            CancelOrderRequest(order_id=placed.order_id)
        )
        assert cancelled.status is OrderStatus.CANCELLED
        orders = client.fetch_orders(OrderQueryRequest(order_id=placed.order_id))
        assert orders[0].status is OrderStatus.CANCELLED

    def test_cancel_unknown_order_raises(self) -> None:
        client = FakeBrokerClient(make_session())
        client.connect()
        with pytest.raises(BrokerOrderError) as exc_info:
            client.cancel_order(CancelOrderRequest(order_id="missing"))
        assert exc_info.value.code == ERROR_ORDER_NOT_FOUND


class TestFakeBrokerClientAccount:
    """Fake broker account API tests."""

    def test_positions_margin_profile_funds_holdings_preview(self) -> None:
        client = FakeBrokerClient(make_session())
        client.connect()
        assert client.fetch_positions() == ()
        margins = client.fetch_margins()
        assert margins.available == 100000.0
        profile = client.fetch_profile()
        assert profile.user_id == "user-1"
        funds = client.fetch_funds()
        assert funds.equity_available == 90000.0
        assert client.fetch_holdings() == ()
        preview = client.preview_margin(MarginPreviewRequest())
        assert preview.total == margins.total


class TestAuthGuards:
    """Authentication guard tests."""

    def test_expired_session_raises_on_require_authenticated(self) -> None:
        client = FakeBrokerClient(make_session())
        client.connect()
        client._session_state = SessionState.EXPIRED  # noqa: SLF001
        with pytest.raises(BrokerAuthenticationError) as exc_info:
            client._require_authenticated()  # noqa: SLF001
        assert exc_info.value.code == ERROR_AUTH_EXPIRED

    def test_revoked_session_raises(self) -> None:
        client = FakeBrokerClient(make_session())
        client._session_state = SessionState.REVOKED  # noqa: SLF001
        with pytest.raises(BrokerAuthenticationError) as exc_info:
            client._require_authenticated()  # noqa: SLF001
        assert exc_info.value.code == ERROR_AUTH_REVOKED

    def test_invalid_session_raises(self) -> None:
        client = FakeBrokerClient(make_session())
        client._session_state = SessionState.UNAUTHENTICATED  # noqa: SLF001
        with pytest.raises(BrokerAuthenticationError) as exc_info:
            client._require_authenticated()  # noqa: SLF001
        assert exc_info.value.code == ERROR_AUTH_INVALID


class TestThreadSafety:
    """Thread-safety smoke tests."""

    def test_concurrent_subscribe_and_ticks(self) -> None:
        client = FakeBrokerClient(make_session())
        client.connect()
        received: list[int] = []
        client.set_tick_handler(
            lambda tick: received.append(int(tick["instrument_token"]))
        )

        def worker(token: int) -> None:
            client.subscribe((token,))
            client.deliver_tick(
                MappingProxyType({"instrument_token": token, "last_price": 1.0})
            )

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(worker, i) for i in range(100)]
            for future in as_completed(futures):
                future.result()

        assert len(received) == 100


class TestMetadata:
    """Metadata tests."""

    def test_metadata_snapshot(self) -> None:
        client = FakeBrokerClient(make_session())
        metadata = client.metadata()
        assert metadata.broker_id is BrokerId.MOCK
        assert metadata.client_version == "1.0.0-test"
        assert metadata.capabilities.websocket_ticks is True
