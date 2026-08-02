"""Zerodha Kite Connect implementation of BaseBrokerClient."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Mapping

from broker.base_broker import (
    ERROR_AUTH_EXPIRED,
    ERROR_CONFIG_INVALID,
    ERROR_ORDER_NOT_FOUND,
    ERROR_REQUEST_INVALID,
    AccountProfile,
    BaseBrokerClient,
    BrokerAuthenticationError,
    BrokerCapabilities,
    BrokerClientMetadata,
    BrokerConfigurationError,
    BrokerId,
    BrokerOrderError,
    BrokerRequestError,
    BrokerSession,
    CancelOrderRequest,
    ConnectionInfo,
    ConnectionState,
    FundsSnapshot,
    HistoricalRequest,
    HoldingRecord,
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
    validate_place_order_request,
    validate_quote_request,
)
from broker.zerodha._kite_errors import map_kite_exception
from broker.zerodha._kite_mappers import (
    cache_instrument_tokens,
    map_account_profile,
    map_funds_snapshot,
    map_holding_record,
    map_margin_snapshot,
    map_order_record,
    map_place_order_result,
    map_position_record,
)
from broker.zerodha._kite_policy import KITE_BROKER_VERSION, KiteBrokerPolicy
from broker.zerodha._kite_rest import KiteRestGateway
from broker.zerodha._kite_ws import KiteWebSocketGateway

_logger = logging.getLogger("broker.zerodha.kite_broker")

KiteConnectFactory = Callable[[str], object]
KiteTickerFactory = Callable[[str, str], object]


class KiteBrokerClient(BaseBrokerClient):
    """Production BaseBrokerClient implementation for Zerodha Kite Connect."""

    def __init__(
        self,
        session: BrokerSession,
        policy: KiteBrokerPolicy | None = None,
        *,
        kite_connect_factory: KiteConnectFactory | None = None,
        kite_ticker_factory: KiteTickerFactory | None = None,
    ) -> None:
        """Initialize the Kite broker client.

        Args:
            session: Authenticated Kite session injected by external auth.
            policy: Optional transport policy.
            kite_connect_factory: Optional factory for tests.
            kite_ticker_factory: Optional ticker factory for tests.
        """
        super().__init__(session)
        self._policy = policy or KiteBrokerPolicy()
        self._api_key, self._access_token = _validate_kite_credentials(session)
        self._lock = threading.RLock()
        self._session_state = SessionState.UNAUTHENTICATED
        self._connection_state = ConnectionState.DISCONNECTED
        self._connected_since: datetime | None = None
        self._instrument_token_cache: dict[str, int] = {}
        self._idempotency_cache: dict[str, PlaceOrderResult] = {}
        self._rest = KiteRestGateway(
            api_key=self._api_key,
            access_token=self._access_token,
            policy=self._policy,
            kite_factory=kite_connect_factory,
        )
        self._ws = KiteWebSocketGateway(
            api_key=self._api_key,
            access_token=self._access_token,
            policy=self._policy,
            ticker_factory=kite_ticker_factory,
        )

    @property
    def broker_id(self) -> BrokerId:
        """Return Kite broker identifier."""
        return BrokerId.KITE

    @property
    def client_version(self) -> str:
        """Return implementation version."""
        return KITE_BROKER_VERSION

    @property
    def capabilities(self) -> BrokerCapabilities:
        """Return supported API capabilities."""
        return BrokerCapabilities(
            websocket_ticks=True,
            historical_candles=True,
            order_placement=True,
            order_modification=True,
            margin_preview=self._policy.enable_margin_preview,
            holdings=self._policy.enable_holdings,
            funds_breakdown=self._policy.enable_funds_breakdown,
            ohlc_batch=self._policy.enable_ohlc_batch,
            amo_orders=True,
            cover_orders=False,
        )

    def metadata(self) -> BrokerClientMetadata:
        """Return broker metadata snapshot."""
        return BrokerClientMetadata(
            broker_id=self.broker_id,
            client_version=self.client_version,
            capabilities=self.capabilities,
            rate_limit_hint_rps=self._policy.rate_limit_rps,
        )

    def connect(self) -> None:
        """Establish REST auth probe and WebSocket transport."""
        _logger.info("kite_broker.connect.start")
        try:
            _ = self._rest.profile()
            with self._lock:
                self._session_state = SessionState.AUTHENTICATED
                self._connection_state = ConnectionState.CONNECTING
            self._ws.connect()
            with self._lock:
                self._connection_state = ConnectionState.CONNECTED
                self._connected_since = datetime.now(timezone.utc)
        except Exception as exc:  # noqa: BLE001 - mapped below
            mapped = map_kite_exception(exc, broker_id=self.broker_id)
            if isinstance(mapped, BrokerAuthenticationError):
                with self._lock:
                    self._session_state = SessionState.EXPIRED
                _logger.warning("kite_broker.session.expired")
            _logger.error("kite_broker.connect.failed", extra={"code": mapped.code})
            raise mapped from exc

    def disconnect(self) -> None:
        """Shut down WebSocket and mark client disconnected."""
        self._ws.close()
        with self._lock:
            self._connection_state = ConnectionState.DISCONNECTED
        _logger.info("kite_broker.disconnect")

    def is_connected(self) -> bool:
        """Return whether transport is connected."""
        with self._lock:
            return self._connection_state is ConnectionState.CONNECTED

    def get_connection_info(self) -> ConnectionInfo:
        """Return connection snapshot."""
        ws_info = self._ws.get_connection_info()
        with self._lock:
            return ConnectionInfo(
                state=self._connection_state,
                since=self._connected_since,
                websocket_state=ws_info.websocket_state,
                last_error_code=ws_info.last_error_code,
                last_error_message=ws_info.last_error_message,
            )

    def is_authenticated(self) -> bool:
        """Return whether the session is authenticated."""
        with self._lock:
            return self._session_state is SessionState.AUTHENTICATED

    def get_session_state(self) -> SessionState:
        """Return explicit session state."""
        with self._lock:
            return self._session_state

    def session_expires_at(self) -> datetime | None:
        """Return optional session expiry timestamp."""
        return self.session.expires_at

    def fetch_instruments(
        self,
        request: InstrumentRequest,
    ) -> tuple[Mapping[str, object], ...]:
        """Fetch Kite instrument master rows."""
        self._require_connected()
        self._require_authenticated()
        self._check_expiry_hint()
        rows = self._rest.instruments(request)
        with self._lock:
            self._instrument_token_cache.update(cache_instrument_tokens(rows))
        return rows

    def fetch_quotes(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        """Fetch full quote payloads."""
        self._require_connected()
        self._require_authenticated()
        self._check_expiry_hint()
        validate_quote_request(request)
        return self._rest.quote(request)

    def fetch_ltp(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        """Fetch LTP payloads."""
        self._require_connected()
        self._require_authenticated()
        self._check_expiry_hint()
        validate_quote_request(request)
        return self._rest.ltp(request)

    def _fetch_ohlc_impl(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        self._require_connected()
        self._require_authenticated()
        validate_quote_request(request)
        return self._rest.ohlc(request)

    def fetch_historical(
        self,
        request: HistoricalRequest,
    ) -> tuple[Mapping[str, object], ...]:
        """Fetch historical candles."""
        self._require_connected()
        self._require_authenticated()
        token = self._resolve_instrument_token(request.instrument_key)
        return self._rest.historical_data(request, token)

    def subscribe(self, instrument_tokens: tuple[int, ...]) -> None:
        """Subscribe to live ticks."""
        self._require_connected()
        self._require_authenticated()
        self._ws.subscribe(instrument_tokens)

    def unsubscribe(self, instrument_tokens: tuple[int, ...]) -> None:
        """Unsubscribe from live ticks."""
        self._require_connected()
        self._ws.unsubscribe(instrument_tokens)

    def get_subscribed_tokens(self) -> frozenset[int]:
        """Return active WebSocket subscriptions."""
        return self._ws.get_subscribed_tokens()

    def set_tick_handler(self, handler) -> None:
        """Register tick handler."""
        self._ws.set_tick_handler(handler)

    def set_error_handler(self, handler) -> None:
        """Register transport error handler."""
        self._ws.set_error_handler(handler)

    def set_connection_handler(self, handler) -> None:
        """Register connection handler."""
        self._ws.set_connection_handler(handler)

    def place_order(self, request: PlaceOrderRequest) -> PlaceOrderResult:
        """Place an order via Kite REST."""
        self._require_connected()
        self._require_authenticated()
        validate_place_order_request(request)
        if request.idempotency_key:
            cached = self._idempotency_cache.get(request.idempotency_key)
            if cached is not None:
                return cached
        response = self._rest.place_order(request)
        result = map_place_order_result(response)
        if request.idempotency_key:
            self._idempotency_cache[request.idempotency_key] = result
        _logger.info("kite_broker.order.placed", extra={"order_id": result.order_id})
        return result

    def modify_order(self, request: ModifyOrderRequest) -> OrderRecord:
        """Modify an open order."""
        self._require_connected()
        self._require_authenticated()
        self._rest.modify_order(request)
        records = self.fetch_orders(OrderQueryRequest(order_id=request.order_id))
        if not records:
            raise BrokerOrderError(
                "order not found after modify",
                code=ERROR_ORDER_NOT_FOUND,
                broker_id=self.broker_id,
            )
        return records[0]

    def cancel_order(self, request: CancelOrderRequest) -> OrderRecord:
        """Cancel an open order."""
        self._require_connected()
        self._require_authenticated()
        order_id = self._rest.cancel_order(request)
        records = self.fetch_orders(OrderQueryRequest(order_id=str(order_id)))
        if records:
            return records[0]
        return OrderRecord(
            order_id=str(order_id),
            instrument_key="UNKNOWN:UNKNOWN",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product=ProductType.MIS,
            quantity=0,
            status=OrderStatus.CANCELLED,
        )

    def fetch_orders(
        self,
        request: OrderQueryRequest,
    ) -> tuple[OrderRecord, ...]:
        """Fetch orders as broker-neutral records."""
        self._require_connected()
        self._require_authenticated()
        rows = self._rest.orders(request)
        return tuple(map_order_record(row) for row in rows)

    def fetch_positions(self) -> tuple[PositionRecord, ...]:
        """Fetch open positions."""
        self._require_connected()
        self._require_authenticated()
        rows = self._rest.positions()
        return tuple(map_position_record(row) for row in rows)

    def _fetch_holdings_impl(self) -> tuple[HoldingRecord, ...]:
        self._require_connected()
        self._require_authenticated()
        rows = self._rest.holdings()
        return tuple(map_holding_record(row) for row in rows)

    def fetch_margins(self) -> MarginSnapshot:
        """Fetch account margin snapshot."""
        self._require_connected()
        self._require_authenticated()
        return map_margin_snapshot(self._rest.margins())

    def _preview_margin_impl(self, request: MarginPreviewRequest) -> MarginSnapshot:
        self._require_connected()
        self._require_authenticated()
        basket = [_build_kite_margin_order(order) for order in request.orders]
        response = self._rest.order_margins(basket)
        return map_margin_snapshot({"equity": response, "commodity": {}})

    def fetch_profile(self) -> AccountProfile:
        """Fetch account profile."""
        self._require_connected()
        self._require_authenticated()
        return map_account_profile(self._rest.profile())

    def _fetch_funds_impl(self) -> FundsSnapshot:
        self._require_connected()
        self._require_authenticated()
        return map_funds_snapshot(self._rest.margins())

    def update_session(self, session: BrokerSession) -> None:
        """Replace session and refresh REST token."""
        super().update_session(session)
        self._api_key, self._access_token = _validate_kite_credentials(session)
        self._rest.set_access_token(self._access_token)

    def _resolve_instrument_token(self, instrument_key: str) -> int:
        with self._lock:
            token = self._instrument_token_cache.get(instrument_key)
        if token is None:
            raise BrokerRequestError(
                f"instrument token not cached for {instrument_key}",
                code=ERROR_REQUEST_INVALID,
                broker_id=self.broker_id,
            )
        return token

    def _check_expiry_hint(self) -> None:
        expires_at = self.session_expires_at()
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            with self._lock:
                self._session_state = SessionState.EXPIRED
            raise BrokerAuthenticationError(
                "broker session has expired",
                code=ERROR_AUTH_EXPIRED,
                broker_id=self.broker_id,
            )


def _validate_kite_credentials(session: BrokerSession) -> tuple[str, str]:
    if session.broker_id is not BrokerId.KITE:
        raise BrokerConfigurationError(
            "session broker_id must be KITE",
            code=ERROR_CONFIG_INVALID,
            broker_id=session.broker_id,
        )
    api_key = session.credentials.get("api_key")
    access_token = session.credentials.get("access_token")
    if not isinstance(api_key, str) or not api_key.strip():
        raise BrokerConfigurationError(
            "api_key credential is required",
            code=ERROR_CONFIG_INVALID,
            broker_id=BrokerId.KITE,
        )
    if not isinstance(access_token, str) or not access_token.strip():
        raise BrokerConfigurationError(
            "access_token credential is required",
            code=ERROR_CONFIG_INVALID,
            broker_id=BrokerId.KITE,
        )
    return api_key.strip(), access_token.strip()


def _build_kite_margin_order(request: PlaceOrderRequest) -> dict[str, object]:
    exchange, tradingsymbol = request.instrument_key.split(":", 1)
    from broker.zerodha._kite_mappers import (
        to_kite_order_type,
        to_kite_product,
        to_kite_transaction_type,
        to_kite_variety,
    )

    return {
        "exchange": exchange.upper(),
        "tradingsymbol": tradingsymbol,
        "transaction_type": to_kite_transaction_type(request.side),
        "variety": to_kite_variety(request.variety),
        "product": to_kite_product(request.product),
        "order_type": to_kite_order_type(request.order_type),
        "quantity": request.quantity,
        "price": request.price or 0.0,
        "trigger_price": request.trigger_price or 0.0,
    }


__all__ = ["KITE_BROKER_VERSION", "KiteBrokerClient", "KiteBrokerPolicy"]
