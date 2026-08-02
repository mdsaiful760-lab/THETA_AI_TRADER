"""WebSocket gateway for KiteTicker."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Mapping

from broker.base_broker import (
    BrokerErrorHandler,
    ConnectionHandler,
    ConnectionInfo,
    ConnectionState,
    TickHandler,
    WebSocketState,
)
from broker.zerodha._kite_copy import copy_mapping
from broker.zerodha._kite_errors import map_kite_exception
from broker.zerodha._kite_policy import KiteBrokerPolicy, KiteWebSocketTickMode

_logger = logging.getLogger("broker.zerodha.kite_ws")

KiteTickerFactory = Callable[[str, str], object]


class KiteWebSocketGateway:
    """Thread-safe wrapper around KiteTicker."""

    def __init__(
        self,
        *,
        api_key: str,
        access_token: str,
        policy: KiteBrokerPolicy,
        ticker_factory: KiteTickerFactory | None = None,
    ) -> None:
        self._policy = policy
        self._lock = threading.RLock()
        self._subscribed: set[int] = set()
        self._tick_handler: TickHandler | None = None
        self._error_handler: BrokerErrorHandler | None = None
        self._connection_handler: ConnectionHandler | None = None
        self._websocket_state = WebSocketState.CLOSED
        self._connection_state = ConnectionState.DISCONNECTED
        self._connected_since = None
        if ticker_factory is None:
            from kiteconnect import KiteTicker

            self._ticker = KiteTicker(api_key, access_token)
        else:
            self._ticker = ticker_factory(api_key, access_token)
        self._register_callbacks()

    def connect(self) -> None:
        """Open the WebSocket connection."""
        with self._lock:
            self._websocket_state = WebSocketState.OPENING
        self._ticker.connect(threaded=True)

    def close(self) -> None:
        """Close the WebSocket connection."""
        with self._lock:
            self._websocket_state = WebSocketState.CLOSING
        close = getattr(self._ticker, "close", None)
        if callable(close):
            close()
        with self._lock:
            self._websocket_state = WebSocketState.CLOSED
            self._connection_state = ConnectionState.DISCONNECTED
            self._subscribed.clear()

    def subscribe(self, instrument_tokens: tuple[int, ...]) -> None:
        """Subscribe to instrument tokens."""
        if len(self._subscribed) + len(instrument_tokens) > self._policy.max_subscribed_tokens:
            from broker.base_broker import BrokerRequestError, ERROR_REQUEST_BATCH_TOO_LARGE

            raise BrokerRequestError(
                "subscription limit exceeded",
                code=ERROR_REQUEST_BATCH_TOO_LARGE,
            )
        self._ticker.subscribe(list(instrument_tokens))
        mode = (
            self._ticker.MODE_FULL
            if self._policy.ws_tick_mode is KiteWebSocketTickMode.FULL
            else self._ticker.MODE_QUOTE
        )
        self._ticker.set_mode(mode, list(instrument_tokens))
        with self._lock:
            self._subscribed.update(instrument_tokens)
        _logger.info(
            "kite_broker.ws.subscribe",
            extra={"count": len(instrument_tokens)},
        )

    def unsubscribe(self, instrument_tokens: tuple[int, ...]) -> None:
        """Unsubscribe from instrument tokens."""
        self._ticker.unsubscribe(list(instrument_tokens))
        with self._lock:
            self._subscribed.difference_update(instrument_tokens)
        _logger.info(
            "kite_broker.ws.unsubscribe",
            extra={"count": len(instrument_tokens)},
        )

    def get_subscribed_tokens(self) -> frozenset[int]:
        """Return subscribed token snapshot."""
        with self._lock:
            return frozenset(self._subscribed)

    def set_tick_handler(self, handler: TickHandler | None) -> None:
        """Register tick handler."""
        with self._lock:
            self._tick_handler = handler

    def set_error_handler(self, handler: BrokerErrorHandler | None) -> None:
        """Register error handler."""
        with self._lock:
            self._error_handler = handler

    def set_connection_handler(self, handler: ConnectionHandler | None) -> None:
        """Register connection handler."""
        with self._lock:
            self._connection_handler = handler

    def get_connection_info(self) -> ConnectionInfo:
        """Return WebSocket connection snapshot."""
        with self._lock:
            return ConnectionInfo(
                state=self._connection_state,
                since=self._connected_since,
                websocket_state=self._websocket_state,
            )

    def _register_callbacks(self) -> None:
        self._ticker.on_ticks = self._on_ticks
        self._ticker.on_connect = self._on_connect
        self._ticker.on_close = self._on_close
        self._ticker.on_error = self._on_error
        self._ticker.on_reconnect = self._on_reconnect
        self._ticker.on_noreconnect = self._on_noreconnect

    def _on_ticks(self, ticks: list[dict]) -> None:
        with self._lock:
            handler = self._tick_handler
        if handler is None:
            return
        for tick in ticks:
            try:
                handler(copy_mapping(tick))
            except Exception:  # noqa: BLE001 - handler isolation
                _logger.exception("kite_broker.ws.tick_handler_failed")

    def _on_connect(self, *_args, **_kwargs) -> None:
        with self._lock:
            self._websocket_state = WebSocketState.OPEN
            self._connection_state = ConnectionState.CONNECTED
            from datetime import datetime, timezone

            self._connected_since = datetime.now(timezone.utc)
            info = ConnectionInfo(
                state=self._connection_state,
                since=self._connected_since,
                websocket_state=self._websocket_state,
            )
            handler = self._connection_handler
        _logger.info("kite_broker.connect.success")
        if handler is not None:
            handler(info)

    def _on_close(self, *_args, **_kwargs) -> None:
        with self._lock:
            self._websocket_state = WebSocketState.CLOSED
            self._connection_state = ConnectionState.DISCONNECTED
            info = ConnectionInfo(
                state=self._connection_state,
                since=self._connected_since,
                websocket_state=self._websocket_state,
            )
            handler = self._connection_handler
        if handler is not None:
            handler(info)

    def _on_error(self, _ws, code, reason) -> None:
        mapped = map_kite_exception(Exception(f"websocket error {code}: {reason}"))
        with self._lock:
            self._websocket_state = WebSocketState.ERROR
            handler = self._error_handler
        _logger.error("kite_broker.ws.error", extra={"code": code})
        if handler is not None:
            handler(mapped)

    def _on_reconnect(self, *_args, **_kwargs) -> None:
        with self._lock:
            self._connection_state = ConnectionState.RECONNECTING
            self._websocket_state = WebSocketState.OPENING
            info = ConnectionInfo(
                state=self._connection_state,
                since=self._connected_since,
                websocket_state=self._websocket_state,
            )
            handler = self._connection_handler
        if handler is not None:
            handler(info)

    def _on_noreconnect(self, *_args, **_kwargs) -> None:
        with self._lock:
            self._connection_state = ConnectionState.DEGRADED
            self._websocket_state = WebSocketState.ERROR
            info = ConnectionInfo(
                state=self._connection_state,
                since=self._connected_since,
                websocket_state=self._websocket_state,
            )
            handler = self._error_handler
        if handler is not None:
            handler(map_kite_exception(Exception("websocket reconnect exhausted")))
