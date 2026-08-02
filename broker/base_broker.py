"""Broker-agnostic abstract client interface for THETA AI TRADER.

Concrete broker implementations (e.g. Kite) live in sibling modules and satisfy
:class:`BaseBrokerClient`. Platform consumers depend on this contract only.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

BROKER_CLIENT_VERSION: str = "1.0.0"
DEFAULT_REST_TIMEOUT_SECONDS: float = 2.0
DEFAULT_WS_RECONNECT_HINT_SECONDS: float = 5.0

ERROR_CONFIG_INVALID: str = "BROKER_CLIENT.CONFIG.INVALID"
ERROR_CONFIG_MISSING_SESSION: str = "BROKER_CLIENT.CONFIG.MISSING_SESSION"
ERROR_CONNECTION_FAILED: str = "BROKER_CLIENT.CONNECTION.FAILED"
ERROR_CONNECTION_DISCONNECTED: str = "BROKER_CLIENT.CONNECTION.DISCONNECTED"
ERROR_CONNECTION_WEBSOCKET_FAILED: str = "BROKER_CLIENT.CONNECTION.WEBSOCKET_FAILED"
ERROR_CONNECTION_WEBSOCKET_CLOSED: str = "BROKER_CLIENT.CONNECTION.WEBSOCKET_CLOSED"
ERROR_AUTH_EXPIRED: str = "BROKER_CLIENT.AUTH.EXPIRED"
ERROR_AUTH_INVALID: str = "BROKER_CLIENT.AUTH.INVALID"
ERROR_AUTH_REVOKED: str = "BROKER_CLIENT.AUTH.REVOKED"
ERROR_REQUEST_INVALID: str = "BROKER_CLIENT.REQUEST.INVALID"
ERROR_REQUEST_BATCH_TOO_LARGE: str = "BROKER_CLIENT.REQUEST.BATCH_TOO_LARGE"
ERROR_REQUEST_TIMEOUT: str = "BROKER_CLIENT.REQUEST.TIMEOUT"
ERROR_RATE_LIMIT_EXCEEDED: str = "BROKER_CLIENT.RATE_LIMIT.EXCEEDED"
ERROR_ORDER_REJECTED: str = "BROKER_CLIENT.ORDER.REJECTED"
ERROR_ORDER_NOT_FOUND: str = "BROKER_CLIENT.ORDER.NOT_FOUND"
ERROR_CAPABILITY_UNSUPPORTED: str = "BROKER_CLIENT.CAPABILITY.UNSUPPORTED"
ERROR_INTERNAL_UNHANDLED: str = "BROKER_CLIENT.INTERNAL.UNHANDLED"

_logger = logging.getLogger("broker.base_broker")


class BrokerId(str, Enum):
    """Identifies a concrete broker implementation."""

    KITE = "kite"
    MOCK = "mock"
    UNKNOWN = "unknown"


class ConnectionState(str, Enum):
    """Unified REST/WebSocket transport health."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"


class WebSocketState(str, Enum):
    """WebSocket stream state."""

    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    ERROR = "error"


class SessionState(str, Enum):
    """Authentication session validity."""

    UNAUTHENTICATED = "unauthenticated"
    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"
    REVOKED = "revoked"


class OrderSide(str, Enum):
    """Order direction."""

    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    """Order type."""

    MARKET = "market"
    LIMIT = "limit"
    SL = "sl"
    SL_M = "sl_m"


class ProductType(str, Enum):
    """Product classification."""

    CNC = "cnc"
    NRML = "nrml"
    MIS = "mis"
    MTF = "mtf"


class OrderStatus(str, Enum):
    """Normalized order lifecycle status."""

    PENDING = "pending"
    OPEN = "open"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class OrderVariety(str, Enum):
    """Order variety."""

    REGULAR = "regular"
    AMO = "amo"
    CO = "co"
    ICEBERG = "iceberg"


class Exchange(str, Enum):
    """Exchange identifier."""

    NSE = "nse"
    BSE = "bse"
    NFO = "nfo"
    BFO = "bfo"
    MCX = "mcx"
    CDS = "cds"
    UNKNOWN = "unknown"


class InstrumentKind(str, Enum):
    """Instrument classification hint."""

    EQ = "eq"
    INDEX = "index"
    FUT = "fut"
    CE = "ce"
    PE = "pe"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BrokerSession:
    """Injected authenticated session metadata.

    Attributes:
        broker_id: Target broker implementation identifier.
        session_id: Opaque correlation id for logs (not the access token).
        authenticated_at: Timezone-aware session issuance timestamp.
        credentials: Opaque immutable credential mapping for implementations.
        expires_at: Optional timezone-aware expiry timestamp.
    """

    broker_id: BrokerId
    session_id: str
    authenticated_at: datetime
    credentials: Mapping[str, object]
    expires_at: datetime | None = None


@dataclass(frozen=True)
class BrokerCapabilities:
    """Supported API surface for a concrete broker client."""

    websocket_ticks: bool = True
    historical_candles: bool = True
    order_placement: bool = True
    order_modification: bool = True
    margin_preview: bool = False
    holdings: bool = False
    funds_breakdown: bool = False
    ohlc_batch: bool = False
    amo_orders: bool = False
    cover_orders: bool = False


@dataclass(frozen=True)
class ConnectionInfo:
    """Detailed connection snapshot."""

    state: ConnectionState
    since: datetime | None
    websocket_state: WebSocketState
    last_error_code: str | None = None
    last_error_message: str | None = None


@dataclass(frozen=True)
class BrokerClientMetadata:
    """Broker client metadata snapshot."""

    broker_id: BrokerId
    client_version: str
    capabilities: BrokerCapabilities
    rate_limit_hint_rps: float = 3.0


@dataclass(frozen=True)
class InstrumentRequest:
    """Instrument master download request."""

    exchange: Exchange
    filters: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class QuoteRequest:
    """Batch quote/LTP/OHLC request."""

    instrument_keys: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalRequest:
    """Historical candle download request."""

    instrument_key: str
    interval: str
    from_ts: datetime
    to_ts: datetime
    continuous: bool = False


@dataclass(frozen=True)
class PlaceOrderRequest:
    """Broker-neutral order placement request."""

    instrument_key: str
    side: OrderSide
    order_type: OrderType
    product: ProductType
    quantity: int
    price: float | None = None
    trigger_price: float | None = None
    variety: OrderVariety = OrderVariety.REGULAR
    validity: str | None = None
    tag: str | None = None
    idempotency_key: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class ModifyOrderRequest:
    """Broker-neutral order modification request."""

    order_id: str
    quantity: int | None = None
    price: float | None = None
    trigger_price: float | None = None
    variety: OrderVariety | None = None


@dataclass(frozen=True)
class CancelOrderRequest:
    """Broker-neutral order cancellation request."""

    order_id: str
    variety: OrderVariety | None = None


@dataclass(frozen=True)
class OrderQueryRequest:
    """Order list/query request."""

    order_id: str | None = None


@dataclass(frozen=True)
class MarginPreviewRequest:
    """Hypothetical margin preview request."""

    orders: tuple[PlaceOrderRequest, ...] = ()
    basket_id: str | None = None


@dataclass(frozen=True)
class PlaceOrderResult:
    """Result of a successful order submission."""

    order_id: str
    status: OrderStatus
    message: str
    broker_order_id: str | None = None
    raw: Mapping[str, object] | None = None


@dataclass(frozen=True)
class OrderRecord:
    """Normalized broker order record."""

    order_id: str
    instrument_key: str
    side: OrderSide
    order_type: OrderType
    product: ProductType
    quantity: int
    status: OrderStatus
    price: float | None = None
    trigger_price: float | None = None
    variety: OrderVariety = OrderVariety.REGULAR
    message: str | None = None
    broker_order_id: str | None = None
    raw: Mapping[str, object] | None = None


@dataclass(frozen=True)
class PositionRecord:
    """Normalized open position record."""

    instrument_key: str
    product: ProductType
    quantity: int
    average_price: float
    exchange: Exchange
    last_price: float | None = None
    pnl: float | None = None
    broker_position_id: str | None = None
    raw: Mapping[str, object] | None = None


@dataclass(frozen=True)
class HoldingRecord:
    """Normalized delivery holding record."""

    instrument_key: str
    quantity: int
    average_price: float
    exchange: Exchange
    collateral_quantity: int | None = None
    collateral_type: str | None = None
    raw: Mapping[str, object] | None = None


@dataclass(frozen=True)
class MarginSnapshot:
    """Account margin snapshot."""

    available: float
    used: float
    total: float
    as_of: datetime
    span: float | None = None
    exposure: float | None = None
    commodity_available: float | None = None


@dataclass(frozen=True)
class AccountProfile:
    """Broker account profile."""

    user_id: str
    user_name: str
    broker: str
    exchanges: tuple[Exchange, ...]
    products: tuple[ProductType, ...]
    email: str | None = None


@dataclass(frozen=True)
class FundsSnapshot:
    """Account funds breakdown."""

    equity_available: float
    commodity_available: float
    as_of: datetime


class BrokerClientError(Exception):
    """Root of all broker client exceptions.

    Attributes:
        code: Stable machine-readable error code.
        message: Human-readable error description.
        recoverable: Whether the caller may retry the operation.
        broker_id: Originating broker identifier.
        cause: Optional wrapped exception.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        recoverable: bool = False,
        broker_id: BrokerId = BrokerId.UNKNOWN,
        cause: BaseException | None = None,
    ) -> None:
        """Initialize a broker client error.

        Args:
            message: Human-readable error description.
            code: Stable machine-readable error code.
            recoverable: Whether the caller may retry the operation.
            broker_id: Originating broker identifier.
            cause: Optional wrapped exception.
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable
        self.broker_id = broker_id
        self.cause = cause


class BrokerConfigurationError(BrokerClientError):
    """Raised when static broker client configuration is invalid."""


class BrokerConnectionError(BrokerClientError):
    """Raised for connect, disconnect, or WebSocket transport failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str = ERROR_CONNECTION_DISCONNECTED,
        recoverable: bool = True,
        broker_id: BrokerId = BrokerId.UNKNOWN,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            recoverable=recoverable,
            broker_id=broker_id,
            cause=cause,
        )


class BrokerAuthenticationError(BrokerClientError):
    """Raised when the session is invalid or expired."""

    def __init__(
        self,
        message: str,
        *,
        code: str = ERROR_AUTH_INVALID,
        recoverable: bool = False,
        broker_id: BrokerId = BrokerId.UNKNOWN,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            recoverable=recoverable,
            broker_id=broker_id,
            cause=cause,
        )


class BrokerRateLimitError(BrokerClientError):
    """Raised when a broker rate limit is exceeded."""

    def __init__(
        self,
        message: str,
        *,
        code: str = ERROR_RATE_LIMIT_EXCEEDED,
        broker_id: BrokerId = BrokerId.UNKNOWN,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            recoverable=True,
            broker_id=broker_id,
            cause=cause,
        )


class BrokerTimeoutError(BrokerClientError):
    """Raised when a broker operation times out."""

    def __init__(
        self,
        message: str,
        *,
        code: str = ERROR_REQUEST_TIMEOUT,
        broker_id: BrokerId = BrokerId.UNKNOWN,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            recoverable=True,
            broker_id=broker_id,
            cause=cause,
        )


class BrokerRequestError(BrokerClientError):
    """Raised when request parameters are invalid."""

    def __init__(
        self,
        message: str,
        *,
        code: str = ERROR_REQUEST_INVALID,
        broker_id: BrokerId = BrokerId.UNKNOWN,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            recoverable=False,
            broker_id=broker_id,
            cause=cause,
        )


class BrokerOrderError(BrokerClientError):
    """Raised when a broker rejects an order operation."""

    def __init__(
        self,
        message: str,
        *,
        code: str = ERROR_ORDER_REJECTED,
        broker_id: BrokerId = BrokerId.UNKNOWN,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            recoverable=False,
            broker_id=broker_id,
            cause=cause,
        )


class BrokerCapabilityError(BrokerClientError):
    """Raised when the broker implementation does not support an API."""

    def __init__(
        self,
        message: str,
        *,
        code: str = ERROR_CAPABILITY_UNSUPPORTED,
        broker_id: BrokerId = BrokerId.UNKNOWN,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            recoverable=False,
            broker_id=broker_id,
            cause=cause,
        )


@runtime_checkable
class TickHandler(Protocol):
    """Callback invoked for each WebSocket tick."""

    def __call__(self, tick: Mapping[str, object]) -> None:
        """Handle one tick payload."""


@runtime_checkable
class BrokerErrorHandler(Protocol):
    """Callback invoked for transport-level broker errors."""

    def __call__(self, error: BrokerClientError) -> None:
        """Handle a structured broker error."""


@runtime_checkable
class ConnectionHandler(Protocol):
    """Callback invoked on connection state transitions."""

    def __call__(self, info: ConnectionInfo) -> None:
        """Handle a connection state update."""


def _utc_now() -> datetime:
    """Return the current UTC timestamp with timezone information."""
    return datetime.now(timezone.utc)


def _is_timezone_aware(value: datetime) -> bool:
    """Return whether a datetime carries timezone information."""
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def validate_broker_session(session: BrokerSession | None) -> BrokerSession:
    """Validate a broker session before client construction.

    Args:
        session: Session object supplied by external auth infrastructure.

    Returns:
        The validated session.

    Raises:
        BrokerConfigurationError: When the session is missing or invalid.
    """
    if session is None:
        raise BrokerConfigurationError(
            "broker session is required",
            code=ERROR_CONFIG_MISSING_SESSION,
        )
    if not session.session_id or not session.session_id.strip():
        raise BrokerConfigurationError(
            "session_id must be non-empty",
            code=ERROR_CONFIG_INVALID,
            broker_id=session.broker_id,
        )
    if not _is_timezone_aware(session.authenticated_at):
        raise BrokerConfigurationError(
            "authenticated_at must be timezone-aware",
            code=ERROR_CONFIG_INVALID,
            broker_id=session.broker_id,
        )
    if session.expires_at is not None and not _is_timezone_aware(session.expires_at):
        raise BrokerConfigurationError(
            "expires_at must be timezone-aware when provided",
            code=ERROR_CONFIG_INVALID,
            broker_id=session.broker_id,
        )
    return session


def validate_quote_request(request: QuoteRequest) -> None:
    """Validate a quote/LTP/OHLC request.

    Args:
        request: Quote request to validate.

    Raises:
        BrokerRequestError: When instrument keys are empty.
    """
    if not request.instrument_keys:
        raise BrokerRequestError(
            "instrument_keys must not be empty",
            code=ERROR_REQUEST_INVALID,
        )


def validate_place_order_request(request: PlaceOrderRequest) -> None:
    """Validate an order placement request.

    Args:
        request: Order request to validate.

    Raises:
        BrokerRequestError: When required fields are invalid.
    """
    if not request.instrument_key or not request.instrument_key.strip():
        raise BrokerRequestError(
            "instrument_key must be non-empty",
            code=ERROR_REQUEST_INVALID,
        )
    if request.quantity <= 0:
        raise BrokerRequestError(
            "quantity must be positive",
            code=ERROR_REQUEST_INVALID,
        )
    if request.order_type is OrderType.LIMIT and request.price is None:
        raise BrokerRequestError(
            "price is required for LIMIT orders",
            code=ERROR_REQUEST_INVALID,
        )


class BaseBrokerClient(abc.ABC):
    """Abstract broker transport client for THETA AI TRADER."""

    def __init__(self, session: BrokerSession) -> None:
        """Initialize the broker client with an injected session.

        Args:
            session: Authenticated session supplied by external auth layer.

        Raises:
            BrokerConfigurationError: When the session is invalid.
        """
        self._session = validate_broker_session(session)

    @property
    def session(self) -> BrokerSession:
        """Return the active broker session."""
        return self._session

    @property
    @abc.abstractmethod
    def broker_id(self) -> BrokerId:
        """Return the stable broker identifier."""

    @property
    @abc.abstractmethod
    def client_version(self) -> str:
        """Return the implementation semantic version."""

    @property
    @abc.abstractmethod
    def capabilities(self) -> BrokerCapabilities:
        """Return supported API capabilities."""

    @abc.abstractmethod
    def metadata(self) -> BrokerClientMetadata:
        """Return a metadata snapshot for this client."""

    @abc.abstractmethod
    def connect(self) -> None:
        """Establish REST and WebSocket transport using the injected session."""

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Cleanly shut down transport connections."""

    @abc.abstractmethod
    def is_connected(self) -> bool:
        """Return whether the client is connected."""

    @abc.abstractmethod
    def get_connection_info(self) -> ConnectionInfo:
        """Return a detailed connection snapshot."""

    @abc.abstractmethod
    def is_authenticated(self) -> bool:
        """Return whether the session is valid for authenticated API calls."""

    @abc.abstractmethod
    def get_session_state(self) -> SessionState:
        """Return explicit authentication session state."""

    @abc.abstractmethod
    def session_expires_at(self) -> datetime | None:
        """Return optional timezone-aware session expiry."""

    @abc.abstractmethod
    def fetch_instruments(
        self,
        request: InstrumentRequest,
    ) -> tuple[Mapping[str, object], ...]:
        """Download the instrument master for an exchange."""

    @abc.abstractmethod
    def fetch_quotes(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        """Fetch full quote payloads for instrument keys."""

    @abc.abstractmethod
    def fetch_ltp(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        """Fetch LTP payloads for instrument keys."""

    def fetch_ohlc(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        """Fetch OHLC payloads for instrument keys.

        Raises:
            BrokerCapabilityError: When OHLC batch is unsupported.
        """
        if not self.capabilities.ohlc_batch:
            raise BrokerCapabilityError(
                "OHLC batch API is not supported",
                broker_id=self.broker_id,
            )
        return self._fetch_ohlc_impl(request)

    def _fetch_ohlc_impl(
        self,
        request: QuoteRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        """Implementation hook for OHLC batch support."""
        raise BrokerCapabilityError(
            "OHLC batch API is not implemented",
            broker_id=self.broker_id,
        )

    @abc.abstractmethod
    def fetch_historical(
        self,
        request: HistoricalRequest,
    ) -> tuple[Mapping[str, object], ...]:
        """Fetch historical candles for an instrument."""

    @abc.abstractmethod
    def subscribe(self, instrument_tokens: tuple[int, ...]) -> None:
        """Subscribe to live ticks for instrument tokens."""

    @abc.abstractmethod
    def unsubscribe(self, instrument_tokens: tuple[int, ...]) -> None:
        """Unsubscribe from live ticks for instrument tokens."""

    @abc.abstractmethod
    def get_subscribed_tokens(self) -> frozenset[int]:
        """Return the current WebSocket subscription snapshot."""

    @abc.abstractmethod
    def set_tick_handler(self, handler: TickHandler | None) -> None:
        """Register the tick callback handler."""

    @abc.abstractmethod
    def set_error_handler(self, handler: BrokerErrorHandler | None) -> None:
        """Register the transport error callback handler."""

    @abc.abstractmethod
    def set_connection_handler(self, handler: ConnectionHandler | None) -> None:
        """Register the connection state callback handler."""

    @abc.abstractmethod
    def place_order(self, request: PlaceOrderRequest) -> PlaceOrderResult:
        """Submit a new order."""

    @abc.abstractmethod
    def modify_order(self, request: ModifyOrderRequest) -> OrderRecord:
        """Modify an open order."""

    @abc.abstractmethod
    def cancel_order(self, request: CancelOrderRequest) -> OrderRecord:
        """Cancel an open or pending order."""

    @abc.abstractmethod
    def fetch_orders(
        self,
        request: OrderQueryRequest,
    ) -> tuple[OrderRecord, ...]:
        """List or query orders."""

    @abc.abstractmethod
    def fetch_positions(self) -> tuple[PositionRecord, ...]:
        """Fetch open positions."""

    def fetch_holdings(self) -> tuple[HoldingRecord, ...]:
        """Fetch delivery holdings.

        Raises:
            BrokerCapabilityError: When holdings are unsupported.
        """
        if not self.capabilities.holdings:
            raise BrokerCapabilityError(
                "holdings API is not supported",
                broker_id=self.broker_id,
            )
        return self._fetch_holdings_impl()

    def _fetch_holdings_impl(self) -> tuple[HoldingRecord, ...]:
        """Implementation hook for holdings support."""
        raise BrokerCapabilityError(
            "holdings API is not implemented",
            broker_id=self.broker_id,
        )

    @abc.abstractmethod
    def fetch_margins(self) -> MarginSnapshot:
        """Fetch account margin summary."""

    def preview_margin(self, request: MarginPreviewRequest) -> MarginSnapshot:
        """Preview margin impact for hypothetical orders.

        Raises:
            BrokerCapabilityError: When margin preview is unsupported.
        """
        if not self.capabilities.margin_preview:
            raise BrokerCapabilityError(
                "margin preview API is not supported",
                broker_id=self.broker_id,
            )
        return self._preview_margin_impl(request)

    def _preview_margin_impl(self, request: MarginPreviewRequest) -> MarginSnapshot:
        """Implementation hook for margin preview support."""
        raise BrokerCapabilityError(
            "margin preview API is not implemented",
            broker_id=self.broker_id,
        )

    @abc.abstractmethod
    def fetch_profile(self) -> AccountProfile:
        """Fetch account profile metadata."""

    def fetch_funds(self) -> FundsSnapshot:
        """Fetch segment-wise funds breakdown.

        Raises:
            BrokerCapabilityError: When funds breakdown is unsupported.
        """
        if not self.capabilities.funds_breakdown:
            raise BrokerCapabilityError(
                "funds breakdown API is not supported",
                broker_id=self.broker_id,
            )
        return self._fetch_funds_impl()

    def _fetch_funds_impl(self) -> FundsSnapshot:
        """Implementation hook for funds breakdown support."""
        raise BrokerCapabilityError(
            "funds breakdown API is not implemented",
            broker_id=self.broker_id,
        )

    def update_session(self, session: BrokerSession) -> None:
        """Replace the active session after external refresh.

        Args:
            session: New authenticated session.

        Raises:
            BrokerConfigurationError: When the session is invalid.
        """
        self._session = validate_broker_session(session)
        _logger.info(
            "broker.session.updated",
            extra={"broker_id": self.broker_id.value, "session_id": session.session_id},
        )

    def _require_connected(self) -> None:
        """Ensure the client is connected before a mutating operation.

        Raises:
            BrokerConnectionError: When the client is disconnected.
        """
        if not self.is_connected():
            raise BrokerConnectionError(
                "broker client is not connected",
                code=ERROR_CONNECTION_DISCONNECTED,
                broker_id=self.broker_id,
            )

    def _require_authenticated(self) -> None:
        """Ensure the session is authenticated before REST calls.

        Raises:
            BrokerAuthenticationError: When the session is not authenticated.
        """
        state = self.get_session_state()
        if state is SessionState.EXPIRED:
            raise BrokerAuthenticationError(
                "broker session has expired",
                code=ERROR_AUTH_EXPIRED,
                broker_id=self.broker_id,
            )
        if state is SessionState.REVOKED:
            raise BrokerAuthenticationError(
                "broker session has been revoked",
                code=ERROR_AUTH_REVOKED,
                broker_id=self.broker_id,
            )
        if not self.is_authenticated():
            raise BrokerAuthenticationError(
                "broker session is not authenticated",
                code=ERROR_AUTH_INVALID,
                broker_id=self.broker_id,
            )


BrokerClient = BaseBrokerClient

__all__ = [
    "BROKER_CLIENT_VERSION",
    "DEFAULT_REST_TIMEOUT_SECONDS",
    "DEFAULT_WS_RECONNECT_HINT_SECONDS",
    "AccountProfile",
    "BaseBrokerClient",
    "BrokerAuthenticationError",
    "BrokerCapabilities",
    "BrokerCapabilityError",
    "BrokerClient",
    "BrokerClientError",
    "BrokerClientMetadata",
    "BrokerConfigurationError",
    "BrokerConnectionError",
    "BrokerErrorHandler",
    "BrokerId",
    "BrokerOrderError",
    "BrokerRateLimitError",
    "BrokerRequestError",
    "BrokerSession",
    "BrokerTimeoutError",
    "CancelOrderRequest",
    "ConnectionHandler",
    "ConnectionInfo",
    "ConnectionState",
    "Exchange",
    "FundsSnapshot",
    "HistoricalRequest",
    "HoldingRecord",
    "InstrumentKind",
    "InstrumentRequest",
    "MarginPreviewRequest",
    "MarginSnapshot",
    "ModifyOrderRequest",
    "OrderQueryRequest",
    "OrderRecord",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "OrderVariety",
    "PlaceOrderRequest",
    "PlaceOrderResult",
    "PositionRecord",
    "ProductType",
    "QuoteRequest",
    "SessionState",
    "TickHandler",
    "WebSocketState",
    "validate_broker_session",
    "validate_place_order_request",
    "validate_quote_request",
]
