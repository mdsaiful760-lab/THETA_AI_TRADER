"""REST gateway for Kite Connect."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Mapping, Sequence

from broker.base_broker import (
    BrokerId,
    CancelOrderRequest,
    Exchange,
    HistoricalRequest,
    InstrumentRequest,
    ModifyOrderRequest,
    OrderQueryRequest,
    PlaceOrderRequest,
    QuoteRequest,
)
from broker.zerodha._kite_copy import copy_mapping, copy_mapping_batch, copy_sequence
from broker.zerodha._kite_errors import map_kite_exception
from broker.zerodha._kite_mappers import (
    to_kite_order_type,
    to_kite_product,
    to_kite_transaction_type,
    to_kite_variety,
)
from broker.zerodha._kite_policy import KiteBrokerPolicy

_logger = logging.getLogger("broker.zerodha.kite_rest")

KiteConnectFactory = Callable[[str], object]


class RateLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, rate_per_second: float, max_wait_seconds: float) -> None:
        self._rate = rate_per_second
        self._max_wait = max_wait_seconds
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        """Block until a request token is available."""
        if self._rate <= 0:
            return
        interval = 1.0 / self._rate
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                sleep_for = self._next_allowed - now
                if sleep_for > self._max_wait:
                    from broker.base_broker import BrokerRateLimitError

                    raise BrokerRateLimitError(
                        "rate limit wait exceeded",
                        broker_id=BrokerId.KITE,
                    )
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_allowed = now + interval


class KiteRestGateway:
    """Thread-safe wrapper around KiteConnect REST calls."""

    def __init__(
        self,
        *,
        api_key: str,
        access_token: str,
        policy: KiteBrokerPolicy,
        kite_factory: KiteConnectFactory | None = None,
    ) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._policy = policy
        self._lock = threading.RLock()
        self._rate_limiter = RateLimiter(
            policy.rate_limit_rps,
            policy.rate_limit_wait_seconds,
        )
        if kite_factory is None:
            from kiteconnect import KiteConnect

            self._kite = KiteConnect(api_key=api_key)
        else:
            self._kite = kite_factory(api_key)
        self._set_access_token(access_token)

    @property
    def kite(self) -> object:
        """Return the underlying KiteConnect instance."""
        return self._kite

    def set_access_token(self, access_token: str) -> None:
        """Update the REST access token."""
        with self._lock:
            self._access_token = access_token
            self._set_access_token(access_token)

    def profile(self) -> Mapping[str, object]:
        """Fetch profile as immutable mapping."""
        payload = self._call_read("profile", lambda: self._kite.profile())
        if not isinstance(payload, Mapping):
            raise TypeError("profile response must be a mapping")
        return copy_mapping(payload)

    def instruments(self, request: InstrumentRequest) -> tuple[Mapping[str, object], ...]:
        """Fetch instrument master."""
        exchange = request.exchange.value.upper()
        rows = self._call_read("instruments", lambda: self._kite.instruments(exchange))
        if not isinstance(rows, list):
            return ()
        return copy_sequence(rows)

    def quote(self, request: QuoteRequest) -> Mapping[str, Mapping[str, object]]:
        """Fetch full quotes in batches."""
        return self._batched_quote_call("quote", request.instrument_keys)

    def ltp(self, request: QuoteRequest) -> Mapping[str, Mapping[str, object]]:
        """Fetch LTP in batches."""
        return self._batched_quote_call("ltp", request.instrument_keys)

    def ohlc(self, request: QuoteRequest) -> Mapping[str, Mapping[str, object]]:
        """Fetch OHLC in batches."""
        return self._batched_quote_call("ohlc", request.instrument_keys)

    def historical_data(
        self,
        request: HistoricalRequest,
        instrument_token: int,
    ) -> tuple[Mapping[str, object], ...]:
        """Fetch historical candles."""
        rows = self._call_read(
            "historical_data",
            lambda: self._kite.historical_data(
                instrument_token,
                request.from_ts,
                request.to_ts,
                request.interval,
                continuous=request.continuous,
            ),
        )
        if not isinstance(rows, list):
            return ()
        return copy_sequence(rows)

    def place_order(self, request: PlaceOrderRequest) -> Mapping[str, object]:
        """Place an order via Kite REST."""
        exchange, tradingsymbol = _split_key(request.instrument_key)
        return self._call_write(
            "place_order",
            lambda: self._kite.place_order(
                variety=to_kite_variety(request.variety),
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type=to_kite_transaction_type(request.side),
                quantity=request.quantity,
                product=to_kite_product(request.product),
                order_type=to_kite_order_type(request.order_type),
                price=request.price,
                trigger_price=request.trigger_price,
                validity=request.validity,
                tag=request.tag,
            ),
        )

    def modify_order(self, request: ModifyOrderRequest) -> Mapping[str, object]:
        """Modify an order via Kite REST."""
        variety = (
            to_kite_variety(request.variety)
            if request.variety is not None
            else "regular"
        )
        return self._call_write(
            "modify_order",
            lambda: self._kite.modify_order(
                variety=variety,
                order_id=request.order_id,
                quantity=request.quantity,
                price=request.price,
                trigger_price=request.trigger_price,
            ),
        )

    def cancel_order(self, request: CancelOrderRequest) -> str:
        """Cancel an order via Kite REST."""
        variety = (
            to_kite_variety(request.variety)
            if request.variety is not None
            else "regular"
        )
        result = self._execute(
            "cancel_order",
            lambda: self._kite.cancel_order(
                variety=variety,
                order_id=request.order_id,
            ),
            retry=False,
        )
        return str(result)

    def orders(self, request: OrderQueryRequest) -> tuple[Mapping[str, object], ...]:
        """Fetch orders."""
        rows = self._call_read("orders", lambda: self._kite.orders())
        copied = copy_sequence(rows)
        if request.order_id is None:
            return copied
        return tuple(row for row in copied if str(row.get("order_id")) == request.order_id)

    def positions(self) -> tuple[Mapping[str, object], ...]:
        """Fetch net positions."""
        payload = self._call_read("positions", lambda: self._kite.positions())
        net_rows = payload.get("net", [])
        if not isinstance(net_rows, list):
            return ()
        return copy_sequence(net_rows)

    def holdings(self) -> tuple[Mapping[str, object], ...]:
        """Fetch holdings."""
        rows = self._call_read("holdings", lambda: self._kite.holdings())
        return copy_sequence(rows)

    def margins(self) -> Mapping[str, object]:
        """Fetch margins."""
        return self._call_read("margins", lambda: self._kite.margins())

    def order_margins(self, orders: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
        """Preview order margins."""
        return self._call_read(
            "order_margins",
            lambda: self._kite.order_margins(list(orders)),
        )

    def _batched_quote_call(
        self,
        method_name: str,
        keys: tuple[str, ...],
    ) -> Mapping[str, Mapping[str, object]]:
        batch_size = self._policy.quote_batch_size
        combined: dict[str, Mapping[str, object]] = {}
        for start in range(0, len(keys), batch_size):
            batch = keys[start : start + batch_size]
            payload = self._call_read(
                method_name,
                lambda batch=batch: getattr(self._kite, method_name)(list(batch)),
            )
            combined.update(copy_mapping_batch(payload))
        return copy_mapping_batch(combined)

    def _call_read(self, method_name: str, func: Callable[[], object]) -> object:
        return self._execute(method_name, func, retry=True)

    def _call_write(self, method_name: str, func: Callable[[], object]) -> object:
        result = self._execute(method_name, func, retry=False)
        assert isinstance(result, Mapping)
        return copy_mapping(result)

    def _execute(
        self,
        method_name: str,
        func: Callable[[], object],
        *,
        retry: bool,
    ) -> object:
        attempts = self._policy.retry_max_attempts if retry else 1
        delay_ms = self._policy.retry_initial_delay_ms
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with self._lock:
                    self._rate_limiter.acquire()
                    start = time.perf_counter()
                    result = func()
                duration_ms = (time.perf_counter() - start) * 1000.0
                _logger.debug(
                    "kite_broker.rest.request",
                    extra={"method": method_name, "duration_ms": duration_ms},
                )
                return result
            except Exception as exc:  # noqa: BLE001 - SDK boundary
                mapped = map_kite_exception(exc)
                last_error = mapped
                if not retry or not mapped.recoverable or attempt + 1 >= attempts:
                    raise mapped from exc
                sleep_seconds = min(
                    delay_ms / 1000.0,
                    self._policy.retry_max_delay_ms / 1000.0,
                )
                if self._policy.retry_jitter:
                    sleep_seconds *= random.uniform(0.5, 1.5)
                time.sleep(sleep_seconds)
                delay_ms = min(delay_ms * 2, self._policy.retry_max_delay_ms)
        assert last_error is not None
        raise last_error

    def _set_access_token(self, access_token: str) -> None:
        set_token = getattr(self._kite, "set_access_token", None)
        if callable(set_token):
            set_token(access_token)


def _split_key(instrument_key: str) -> tuple[str, str]:
    exchange, symbol = instrument_key.split(":", 1)
    return exchange.upper(), symbol
