"""Validated, transport-neutral broker order execution service.

This module deliberately owns no trading decision.  It validates and tracks
authorised broker operations through an injected, narrow transport boundary.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Protocol

BROKER_ORDER_MANAGER_VERSION = "1.0.0"
BROKER_ORDER_MANAGER_SCHEMA_VERSION = 1
PRODUCER_NAME = "broker.broker_order_manager"
SUPPORTED_EXCHANGES = frozenset({"NSE", "BSE", "NFO", "BFO"})
SUPPORTED_PRODUCTS = frozenset({"MIS", "NRML", "CNC"})
TOPIC_ORDER_PLACED = "broker.order.placed"
TOPIC_ORDER_MODIFIED = "broker.order.modified"
TOPIC_ORDER_CANCELLED = "broker.order.cancelled"
TOPIC_ORDER_STATUS_UPDATED = "broker.order.status_updated"
TOPIC_ORDER_EXECUTION_OBSERVED = "broker.order.execution_observed"
TOPIC_ORDER_REJECTED = "broker.order.rejected"
TOPIC_ORDER_VALIDATION_FAILED = "broker.order.validation_failed"
TOPIC_ORDER_TRANSPORT_FAILED = "broker.order.transport_failed"
TOPIC_HEALTH_UPDATED = "broker.health.updated"


class OrderSide(str, Enum):
    """Broker order side."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Broker order type."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL_M"


class ProductType(str, Enum):
    """Broker product type."""
    MIS = "MIS"
    NRML = "NRML"
    CNC = "CNC"


class Validity(str, Enum):
    """Broker order validity."""
    DAY = "DAY"
    IOC = "IOC"


class Exchange(str, Enum):
    """Supported broker exchange."""
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"
    BFO = "BFO"


class BrokerOrderStatus(str, Enum):
    """Normalized order lifecycle status."""
    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    TRIGGER_PENDING = "TRIGGER_PENDING"


class BrokerOrderManagerError(Exception):
    """Base error with a stable public code."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class BrokerOrderManagerConfigurationError(BrokerOrderManagerError):
    """Invalid manager configuration."""


class BrokerOrderManagerValidationError(BrokerOrderManagerError):
    """Invalid public request."""


class BrokerOrderManagerStateError(BrokerOrderManagerError):
    """Invalid lifecycle operation."""


class BrokerOrderManagerTransportError(BrokerOrderManagerError):
    """Transport boundary failure."""


class BrokerOrderManagerSerializationError(BrokerOrderManagerError):
    """Invalid serialized public model."""


class BrokerOrderManagerReconciliationError(BrokerOrderManagerError):
    """Irreconcilable broker state."""


class RetryableTransportError(BrokerOrderManagerTransportError):
    """A transport error eligible for bounded retry."""


class AmbiguousTransportError(BrokerOrderManagerTransportError):
    """A placement may have reached the broker."""


class BrokerRejectedError(BrokerOrderManagerTransportError):
    """A terminal broker business rejection."""


@dataclass(frozen=True)
class BrokerOrderManagerConfig:
    """Immutable operational policy for :class:`BrokerOrderManager`."""

    max_attempts: int = 3
    initial_retry_delay_seconds: float = 0.25
    max_retry_delay_seconds: float = 2.0
    retry_backoff_multiplier: float = 2.0
    operation_timeout_seconds: float = 8.0
    health_window_size: int = 100
    max_batch_cancel_size: int = 50
    require_client_order_id: bool = True
    serialization_version: int = 1
    reconcile_after_ambiguous_place: bool = True

    def __post_init__(self) -> None:
        finite = (self.initial_retry_delay_seconds, self.max_retry_delay_seconds,
                  self.retry_backoff_multiplier, self.operation_timeout_seconds)
        if not 1 <= self.max_attempts <= 5:
            raise BrokerOrderManagerConfigurationError("max_attempts must be 1..5", code="BOM.CONFIG.INVALID")
        if any(not math.isfinite(v) or v < 0 for v in finite[:2]) or self.max_retry_delay_seconds < self.initial_retry_delay_seconds:
            raise BrokerOrderManagerConfigurationError("invalid retry delays", code="BOM.CONFIG.INVALID")
        if not math.isfinite(self.retry_backoff_multiplier) or self.retry_backoff_multiplier < 1:
            raise BrokerOrderManagerConfigurationError("invalid retry multiplier", code="BOM.CONFIG.INVALID")
        if not math.isfinite(self.operation_timeout_seconds) or not 0 < self.operation_timeout_seconds <= 60:
            raise BrokerOrderManagerConfigurationError("invalid operation timeout", code="BOM.CONFIG.INVALID")
        if not 10 <= self.health_window_size <= 10000 or not 1 <= self.max_batch_cancel_size <= 100:
            raise BrokerOrderManagerConfigurationError("invalid limits", code="BOM.CONFIG.INVALID")
        if self.serialization_version != BROKER_ORDER_MANAGER_SCHEMA_VERSION:
            raise BrokerOrderManagerConfigurationError("unsupported serialization version", code="BOM.CONFIG.INVALID")


def default_broker_order_manager_config(profile: str) -> BrokerOrderManagerConfig:
    """Return deterministic defaults for ``unit_test``, ``paper``, or ``live``."""
    normalized = profile.strip().lower()
    if normalized in {"unit_test", "test"}:
        return BrokerOrderManagerConfig(max_attempts=1, initial_retry_delay_seconds=0, max_retry_delay_seconds=0, operation_timeout_seconds=1)
    if normalized == "paper":
        return BrokerOrderManagerConfig(max_attempts=2, initial_retry_delay_seconds=.05, max_retry_delay_seconds=.2, operation_timeout_seconds=2)
    if normalized in {"live", "live_standard"}:
        return BrokerOrderManagerConfig()
    if normalized in {"live_conservative", "conservative"}:
        return BrokerOrderManagerConfig(max_attempts=2, max_retry_delay_seconds=.5, operation_timeout_seconds=5)
    raise BrokerOrderManagerConfigurationError("unknown profile", code="BOM.CONFIG.INVALID")


@dataclass(frozen=True)
class PlaceOrderRequest:
    """Immutable broker placement request."""
    client_order_id: str
    instrument_token: int
    trading_symbol: str
    exchange: Exchange
    side: OrderSide
    order_type: OrderType
    product: ProductType
    validity: Validity
    quantity: int
    tick_size: Decimal
    price: Decimal | None = None
    trigger_price: Decimal | None = None
    variety: str = "regular"
    tag: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class ModifyOrderRequest:
    """Immutable broker modification request."""
    quantity: int | None = None
    price: Decimal | None = None
    trigger_price: Decimal | None = None
    validity: Validity | None = None
    variety: str = "regular"


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise BrokerOrderManagerSerializationError("naive datetime", code="BOM.SERIALIZATION.VERSION")
    return value.isoformat()


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("datetime must be a string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("naive datetime")
    return result


def _envelope(schema: str, payload: Mapping[str, object]) -> dict[str, object]:
    return {"schema": schema, "version": BROKER_ORDER_MANAGER_SCHEMA_VERSION, "payload": dict(payload)}


def _payload(data: Mapping[str, object], schema: str) -> Mapping[str, object]:
    if data.get("schema") != schema or data.get("version") != BROKER_ORDER_MANAGER_SCHEMA_VERSION:
        raise BrokerOrderManagerSerializationError("unsupported serialization envelope", code="BOM.SERIALIZATION.VERSION")
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        raise BrokerOrderManagerSerializationError("invalid serialization payload", code="BOM.SERIALIZATION.VERSION")
    return payload


@dataclass(frozen=True)
class BrokerExecution:
    """Immutable broker execution reported for one order."""
    trade_id: str
    broker_order_id: str
    quantity: int
    price: Decimal
    executed_at: datetime
    exchange: Exchange

    def to_dict(self) -> dict[str, object]:
        return _envelope("theta.broker_execution", {"trade_id": self.trade_id, "broker_order_id": self.broker_order_id, "quantity": self.quantity, "price": str(self.price), "executed_at": _iso(self.executed_at), "exchange": self.exchange.value})
    def to_json(self) -> str:
        """Serialize this execution to versioned JSON."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> BrokerExecution:
        """Deserialize a versioned execution envelope."""
        try:
            p = _payload(data, "theta.broker_execution")
            return cls(str(p["trade_id"]), str(p["broker_order_id"]), int(p["quantity"]), Decimal(str(p["price"])), _datetime(p["executed_at"]), Exchange(str(p["exchange"])))
        except (KeyError, ValueError, InvalidOperation, TypeError) as exc:
            raise BrokerOrderManagerSerializationError("invalid execution", code="BOM.SERIALIZATION.VERSION") from exc
    @classmethod
    def from_json(cls, data: str) -> BrokerExecution:
        """Deserialize an execution JSON envelope."""
        try: return cls.from_dict(json.loads(data))
        except json.JSONDecodeError as exc: raise BrokerOrderManagerSerializationError("invalid JSON", code="BOM.SERIALIZATION.VERSION") from exc


@dataclass(frozen=True)
class BrokerOrder:
    """Immutable normalized broker order snapshot."""
    broker_order_id: str; client_order_id: str; instrument_token: int; trading_symbol: str
    exchange: Exchange; side: OrderSide; order_type: OrderType; product: ProductType; validity: Validity
    quantity: int; filled_quantity: int; remaining_quantity: int; average_price: Decimal | None
    price: Decimal | None; trigger_price: Decimal | None; status: BrokerOrderStatus
    status_message: str | None; executions: tuple[BrokerExecution, ...]; created_at: datetime; updated_at: datetime; version: int

    def to_dict(self) -> dict[str, object]:
        p: dict[str, object] = {k: getattr(self, k) for k in ("broker_order_id", "client_order_id", "instrument_token", "trading_symbol", "quantity", "filled_quantity", "remaining_quantity", "status_message", "version")}
        for key in ("exchange", "side", "order_type", "product", "validity", "status"): p[key] = getattr(self, key).value
        for key in ("average_price", "price", "trigger_price"): p[key] = None if getattr(self, key) is None else str(getattr(self, key))
        p["executions"] = [item.to_dict() for item in self.executions]; p["created_at"] = _iso(self.created_at); p["updated_at"] = _iso(self.updated_at)
        return _envelope("theta.broker_order", p)
    def to_json(self) -> str:
        """Serialize this order to versioned JSON."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> BrokerOrder:
        """Deserialize a versioned order envelope."""
        try:
            p = _payload(data, "theta.broker_order"); decimal = lambda key: None if p.get(key) is None else Decimal(str(p[key]))
            return cls(str(p["broker_order_id"]), str(p["client_order_id"]), int(p["instrument_token"]), str(p["trading_symbol"]), Exchange(str(p["exchange"])), OrderSide(str(p["side"])), OrderType(str(p["order_type"])), ProductType(str(p["product"])), Validity(str(p["validity"])), int(p["quantity"]), int(p["filled_quantity"]), int(p["remaining_quantity"]), decimal("average_price"), decimal("price"), decimal("trigger_price"), BrokerOrderStatus(str(p["status"])), None if p.get("status_message") is None else str(p["status_message"]), tuple(BrokerExecution.from_dict(x) for x in p["executions"]), _datetime(p["created_at"]), _datetime(p["updated_at"]), int(p["version"]))
        except (KeyError, ValueError, InvalidOperation, TypeError) as exc: raise BrokerOrderManagerSerializationError("invalid order", code="BOM.SERIALIZATION.VERSION") from exc
    @classmethod
    def from_json(cls, data: str) -> BrokerOrder:
        """Deserialize an order JSON envelope."""
        try: return cls.from_dict(json.loads(data))
        except json.JSONDecodeError as exc: raise BrokerOrderManagerSerializationError("invalid JSON", code="BOM.SERIALIZATION.VERSION") from exc


@dataclass(frozen=True)
class BrokerOrderResult:
    """Immutable result returned by a broker-order operation."""
    operation: str; success: bool; order: BrokerOrder | None; attempts: int; broker_request_id: str | None; error_code: str | None; error_message: str | None; completed_at: datetime
    def to_dict(self) -> dict[str, object]:
        return _envelope("theta.broker_order_result", {"operation": self.operation, "success": self.success, "order": None if self.order is None else self.order.to_dict(), "attempts": self.attempts, "broker_request_id": self.broker_request_id, "error_code": self.error_code, "error_message": self.error_message, "completed_at": _iso(self.completed_at)})
    def to_json(self) -> str:
        """Serialize this result to versioned JSON."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> BrokerOrderResult:
        """Deserialize a versioned result envelope."""
        try:
            p = _payload(data, "theta.broker_order_result"); order = p.get("order")
            return cls(str(p["operation"]), bool(p["success"]), None if order is None else BrokerOrder.from_dict(order), int(p["attempts"]), p.get("broker_request_id"), p.get("error_code"), p.get("error_message"), _datetime(p["completed_at"]))
        except (KeyError, ValueError, TypeError) as exc: raise BrokerOrderManagerSerializationError("invalid result", code="BOM.SERIALIZATION.VERSION") from exc
    @classmethod
    def from_json(cls, data: str) -> BrokerOrderResult:
        """Deserialize a result JSON envelope."""
        try: return cls.from_dict(json.loads(data))
        except json.JSONDecodeError as exc: raise BrokerOrderManagerSerializationError("invalid JSON", code="BOM.SERIALIZATION.VERSION") from exc


@dataclass(frozen=True)
class BrokerHealth:
    """Immutable operational health snapshot."""
    connected: bool; last_success_at: datetime | None; last_failure_at: datetime | None; rolling_failure_rate: Decimal; average_latency_ms: Decimal; average_execution_time_ms: Decimal; consecutive_failures: int; checked_at: datetime


@dataclass(frozen=True)
class BrokerStatistics:
    """Immutable lifetime statistics snapshot."""
    placement_attempts: int; placement_successes: int; modification_attempts: int; cancellation_attempts: int; status_fetches: int; executions_observed: int; validation_failures: int; retry_attempts: int; transport_failures: int; rejections: int; generated_at: datetime


class BrokerOrderTransport(Protocol):
    """Broker transport boundary used by :class:`BrokerOrderManager`."""
    def place_order(self, request: PlaceOrderRequest) -> Mapping[str, object]: ...
    def modify_order(self, broker_order_id: str, request: ModifyOrderRequest) -> Mapping[str, object]: ...
    def cancel_order(self, broker_order_id: str, variety: str) -> Mapping[str, object]: ...
    def fetch_order(self, broker_order_id: str) -> Mapping[str, object]: ...
    def fetch_trades(self, broker_order_id: str) -> Sequence[Mapping[str, object]]: ...
    def find_by_client_order_id(self, client_order_id: str) -> Mapping[str, object] | None: ...


def to_kite_order_type(value: OrderType) -> str:
    """Map a local order type to its broker wire value."""
    return "SL-M" if value is OrderType.SL_M else value.value
def to_kite_product(value: ProductType) -> str:
    """Map a local product to its broker wire value."""
    return value.value
def to_kite_exchange(value: Exchange) -> str:
    """Map a local exchange to its broker wire value."""
    return value.value
def normalize_broker_status(raw: str) -> BrokerOrderStatus:
    """Normalize a broker status without guessing unknown lifecycle states."""
    normalized = " ".join(raw.upper().split())
    values = {"PUT ORDER REQ RECEIVED": BrokerOrderStatus.PENDING, "VALIDATION PENDING": BrokerOrderStatus.PENDING, "OPEN PENDING": BrokerOrderStatus.PENDING, "OPEN": BrokerOrderStatus.OPEN, "TRIGGER PENDING": BrokerOrderStatus.TRIGGER_PENDING, "COMPLETE": BrokerOrderStatus.COMPLETE, "CANCELLED": BrokerOrderStatus.CANCELLED, "REJECTED": BrokerOrderStatus.REJECTED}
    if normalized not in values: raise BrokerOrderManagerValidationError("unknown broker status", code="BOM.NORMALIZATION.UNKNOWN_STATUS")
    return values[normalized]
def is_tick_aligned(price: Decimal, tick: Decimal) -> bool:
    """Return whether a Decimal price is exactly aligned to a Decimal tick."""
    return tick.is_finite() and tick > 0 and price.is_finite() and price % tick == 0
def canonicalize_place_request(request: PlaceOrderRequest) -> tuple[object, ...]:
    """Return broker-behavioural fields used for idempotency matching."""
    return (request.instrument_token, request.trading_symbol, request.exchange.value, request.side.value, request.order_type.value, request.product.value, request.validity.value, request.quantity, str(request.price), str(request.trigger_price), request.variety)


class BrokerOrderManager:
    """Thread-safe façade for validated broker order operations."""
    def __init__(self, config: BrokerOrderManagerConfig, *, transport: BrokerOrderTransport, event_bus: object | None = None, clock: Callable[[], datetime] | None = None, sleeper: Callable[[float], None] | None = None, id_factory: Callable[[], str] | None = None, monotonic: Callable[[], float] | None = None) -> None:
        self._config, self._transport, self._event_bus = config, transport, event_bus
        self._clock, self._sleeper, self._id_factory, self._monotonic = clock or (lambda: datetime.now(timezone.utc)), sleeper or time.sleep, id_factory or (lambda: str(uuid.uuid4())), monotonic or time.monotonic
        self._lock = threading.RLock(); self._orders: dict[str, BrokerOrder] = {}; self._ledger: dict[str, tuple[tuple[object, ...], BrokerOrderResult]] = {}; self._inflight: dict[str, threading.Event] = {}
        self._counts = dict.fromkeys(("placement_attempts", "placement_successes", "modification_attempts", "cancellation_attempts", "status_fetches", "executions_observed", "validation_failures", "retry_attempts", "transport_failures", "rejections"), 0)
        self._outcomes: deque[bool] = deque(maxlen=config.health_window_size); self._latencies: list[Decimal] = []; self._execution_times: list[Decimal] = []; self._last_success: datetime | None = None; self._last_failure: datetime | None = None; self._consecutive_failures = 0

    def _failure(self, operation: str, code: str, message: str, attempts: int = 0, order: BrokerOrder | None = None) -> BrokerOrderResult:
        return BrokerOrderResult(operation, False, order, attempts, None, code, message[:512], self._clock())
    def _publish(self, topic: str, payload: object) -> None:
        if self._event_bus is None: return
        try:
            publisher = getattr(self._event_bus, "publish")
            publisher(topic, payload)
        except Exception: pass
    def _validation_failure(self, operation: str, error: BrokerOrderManagerError) -> BrokerOrderResult:
        with self._lock: self._counts["validation_failures"] += 1
        result = self._failure(operation, error.code, error.message); self._publish(TOPIC_ORDER_VALIDATION_FAILED, result); return result
    def _record_transport(self, success: bool, started: float) -> None:
        elapsed = Decimal(str((self._monotonic() - started) * 1000))
        with self._lock:
            self._latencies.append(elapsed); self._outcomes.append(not success)
            if success: self._last_success = self._clock(); self._consecutive_failures = 0
            else: self._last_failure = self._clock(); self._consecutive_failures += 1; self._counts["transport_failures"] += 1
    def _validate_place(self, r: PlaceOrderRequest) -> None:
        if not isinstance(r, PlaceOrderRequest): raise BrokerOrderManagerValidationError("invalid request", code="BOM.VALIDATION.REQUEST")
        if not isinstance(r.instrument_token, int) or isinstance(r.instrument_token, bool) or r.instrument_token <= 0: raise BrokerOrderManagerValidationError("invalid instrument", code="BOM.VALIDATION.INSTRUMENT")
        if not isinstance(r.trading_symbol, str) or not r.trading_symbol.strip() or not r.trading_symbol.isprintable(): raise BrokerOrderManagerValidationError("invalid symbol", code="BOM.VALIDATION.INSTRUMENT")
        if not isinstance(r.quantity, int) or isinstance(r.quantity, bool) or r.quantity <= 0: raise BrokerOrderManagerValidationError("invalid quantity", code="BOM.VALIDATION.QUANTITY")
        if not isinstance(r.tick_size, Decimal) or not r.tick_size.is_finite() or r.tick_size <= 0: raise BrokerOrderManagerValidationError("invalid tick", code="BOM.VALIDATION.TICK_SIZE")
        if not isinstance(r.exchange, Exchange): raise BrokerOrderManagerValidationError("invalid exchange", code="BOM.VALIDATION.EXCHANGE")
        if not isinstance(r.product, ProductType) or (r.exchange in (Exchange.NSE, Exchange.BSE) and r.product not in (ProductType.MIS, ProductType.CNC)) or (r.exchange in (Exchange.NFO, Exchange.BFO) and r.product not in (ProductType.MIS, ProductType.NRML)): raise BrokerOrderManagerValidationError("invalid product", code="BOM.VALIDATION.PRODUCT")
        if not all(isinstance(x, t) for x, t in ((r.side, OrderSide), (r.order_type, OrderType), (r.validity, Validity))): raise BrokerOrderManagerValidationError("invalid enum", code="BOM.VALIDATION.REQUEST")
        if self._config.require_client_order_id and (not isinstance(r.client_order_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", r.client_order_id)): raise BrokerOrderManagerValidationError("invalid client order id", code="BOM.VALIDATION.CLIENT_ORDER_ID")
        for value, code in ((r.price, "BOM.VALIDATION.PRICE"), (r.trigger_price, "BOM.VALIDATION.TRIGGER_PRICE")):
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite() or value <= 0 or not is_tick_aligned(value, r.tick_size)): raise BrokerOrderManagerValidationError("invalid price", code=code)
        fields = (r.price is not None, r.trigger_price is not None)
        expected = {OrderType.MARKET: (False, False), OrderType.LIMIT: (True, False), OrderType.SL: (True, True), OrderType.SL_M: (False, True)}
        if fields != expected[r.order_type]: raise BrokerOrderManagerValidationError("invalid order type fields", code="BOM.VALIDATION.ORDER_TYPE")
        if r.order_type is OrderType.SL and ((r.side is OrderSide.BUY and r.price < r.trigger_price) or (r.side is OrderSide.SELL and r.price > r.trigger_price)): raise BrokerOrderManagerValidationError("invalid stop direction", code="BOM.VALIDATION.TRIGGER_PRICE")
    def _normalize(self, raw: Mapping[str, object], request: PlaceOrderRequest | None = None, previous: BrokerOrder | None = None) -> BrokerOrder:
        try:
            now = self._clock(); order_id = str(raw["order_id"])
            if not order_id: raise ValueError("empty order id")
            base = previous
            quantity = int(raw.get("quantity", base.quantity if base else request.quantity))
            filled = int(raw.get("filled_quantity", base.filled_quantity if base else 0))
            if quantity <= 0 or filled < 0 or filled > quantity: raise ValueError("invalid quantities")
            status = normalize_broker_status(str(raw.get("status", base.status.value if base else "PUT ORDER REQ RECEIVED")))
            if status is BrokerOrderStatus.COMPLETE and filled != quantity: raise ValueError("complete has pending quantity")
            def money(key: str, fallback: Decimal | None) -> Decimal | None:
                value = raw.get(key, fallback)
                if value in (None, "", "0", 0): return None
                if isinstance(value, float): raise ValueError("float money")
                return Decimal(str(value))
            created = _datetime(raw["order_timestamp"]) if "order_timestamp" in raw else (base.created_at if base else now)
            updated = _datetime(raw["exchange_timestamp"]) if "exchange_timestamp" in raw else now
            source = request
            return BrokerOrder(order_id, str(raw.get("client_order_id", base.client_order_id if base else source.client_order_id)), int(raw.get("instrument_token", base.instrument_token if base else source.instrument_token)), str(raw.get("tradingsymbol", raw.get("trading_symbol", base.trading_symbol if base else source.trading_symbol))), Exchange(str(raw.get("exchange", base.exchange.value if base else source.exchange.value))), OrderSide(str(raw.get("transaction_type", raw.get("side", base.side.value if base else source.side.value)))), OrderType(str(raw.get("order_type", base.order_type.value if base else source.order_type.value)).replace("-", "_")), ProductType(str(raw.get("product", base.product.value if base else source.product.value))), Validity(str(raw.get("validity", base.validity.value if base else source.validity.value))), quantity, filled, quantity - filled, money("average_price", base.average_price if base else None), money("price", base.price if base else source.price), money("trigger_price", base.trigger_price if base else source.trigger_price), status, None if raw.get("status_message") is None else str(raw["status_message"])[:512], base.executions if base else (), created, updated, (base.version + 1) if base else 1)
        except (KeyError, ValueError, TypeError, InvalidOperation, BrokerOrderManagerError) as exc:
            if isinstance(exc, BrokerOrderManagerError): raise
            raise BrokerOrderManagerValidationError("malformed broker response", code="BOM.NORMALIZATION.RESPONSE") from exc
    def _seal(self, result: BrokerOrderResult, topic: str) -> BrokerOrderResult:
        with self._lock:
            if result.order: self._orders[result.order.broker_order_id] = result.order
        self._publish(topic, result); return result
    def _backoff(self, attempt: int) -> float:
        return min(self._config.max_retry_delay_seconds, self._config.initial_retry_delay_seconds * self._config.retry_backoff_multiplier ** (attempt - 1))

    def place(self, request: PlaceOrderRequest) -> BrokerOrderResult:
        """Validate, place, retry, and atomically seal one logical order."""
        try: self._validate_place(request)
        except BrokerOrderManagerError as exc: return self._validation_failure("place", exc)
        fingerprint = canonicalize_place_request(request)
        with self._lock:
            existing = self._ledger.get(request.client_order_id)
            if existing: return existing[1] if existing[0] == fingerprint else self._failure("place", "BOM.IDEMPOTENCY.CONFLICT", "client order id reused with different request")
            waiter = self._inflight.get(request.client_order_id)
            if waiter is None: waiter = threading.Event(); self._inflight[request.client_order_id] = waiter; owner = True
            else: owner = False
        if not owner:
            waiter.wait()
            with self._lock: return self._ledger[request.client_order_id][1]
        started = self._monotonic(); result: BrokerOrderResult
        try:
            for attempt in range(1, self._config.max_attempts + 1):
                with self._lock: self._counts["placement_attempts"] += 1
                attempt_started = self._monotonic()
                try:
                    raw = self._transport.place_order(request); self._record_transport(True, attempt_started)
                    order = self._normalize(raw, request); result = self._seal(BrokerOrderResult("place", True, order, attempt, str(raw.get("request_id")) if raw.get("request_id") else None, None, None, self._clock()), TOPIC_ORDER_PLACED)
                    with self._lock: self._counts["placement_successes"] += 1
                    break
                except BrokerRejectedError as exc:
                    with self._lock: self._counts["rejections"] += 1
                    result = self._failure("place", exc.code, exc.message, attempt); self._publish(TOPIC_ORDER_REJECTED, result); break
                except AmbiguousTransportError as exc:
                    self._record_transport(False, attempt_started)
                    try: found = self._transport.find_by_client_order_id(request.client_order_id)
                    except Exception: found = None
                    if found is not None:
                        order = self._normalize(found, request); result = self._seal(BrokerOrderResult("place", True, order, attempt, None, None, None, self._clock()), TOPIC_ORDER_PLACED)
                        with self._lock: self._counts["placement_successes"] += 1
                        break
                    if attempt == self._config.max_attempts or not self._config.reconcile_after_ambiguous_place: result = self._failure("place", "BOM.TRANSPORT.AMBIGUOUS", exc.message, attempt); self._publish(TOPIC_ORDER_TRANSPORT_FAILED, result); break
                    with self._lock: self._counts["retry_attempts"] += 1
                except RetryableTransportError as exc:
                    self._record_transport(False, attempt_started)
                    if attempt == self._config.max_attempts: result = self._failure("place", exc.code, exc.message, attempt); self._publish(TOPIC_ORDER_TRANSPORT_FAILED, result); break
                    with self._lock: self._counts["retry_attempts"] += 1
                except BrokerOrderManagerError as exc:
                    result = self._failure("place", exc.code, exc.message, attempt); break
                except Exception:
                    self._record_transport(False, attempt_started); result = self._failure("place", "BOM.INTERNAL.INVARIANT", "unexpected transport failure", attempt); break
                self._sleeper(self._backoff(attempt))
        finally:
            with self._lock: self._execution_times.append(Decimal(str((self._monotonic() - started) * 1000)))
        with self._lock: self._ledger[request.client_order_id] = (fingerprint, result); self._inflight.pop(request.client_order_id).set()
        return result

    def _validate_modify(self, order_id: str, request: ModifyOrderRequest) -> None:
        if not isinstance(order_id, str) or not order_id.strip():
            raise BrokerOrderManagerValidationError("empty broker order id", code="BOM.VALIDATION.REQUEST")
        if not isinstance(request, ModifyOrderRequest) or all(x is None for x in (request.quantity, request.price, request.trigger_price, request.validity)):
            raise BrokerOrderManagerValidationError("empty modification", code="BOM.VALIDATION.REQUEST")
        if request.quantity is not None and (not isinstance(request.quantity, int) or isinstance(request.quantity, bool) or request.quantity <= 0):
            raise BrokerOrderManagerValidationError("invalid quantity", code="BOM.VALIDATION.QUANTITY")
        for value in (request.price, request.trigger_price):
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite() or value <= 0):
                raise BrokerOrderManagerValidationError("invalid price", code="BOM.VALIDATION.PRICE")
        if request.validity is not None and not isinstance(request.validity, Validity):
            raise BrokerOrderManagerValidationError("invalid validity", code="BOM.VALIDATION.REQUEST")

    def modify(self, broker_order_id: str, request: ModifyOrderRequest) -> BrokerOrderResult:
        """Modify an active order after local validation."""
        try:
            self._validate_modify(broker_order_id, request)
        except BrokerOrderManagerError as exc:
            return self._validation_failure("modify", exc)
        with self._lock:
            current = self._orders.get(broker_order_id)
        if current and current.status in (BrokerOrderStatus.COMPLETE, BrokerOrderStatus.CANCELLED, BrokerOrderStatus.REJECTED):
            return self._failure("modify", "BOM.STATE.NOT_MODIFIABLE", "terminal order cannot be modified", order=current)
        if current:
            tick = Decimal("0.01")  # Raw status does not include tick; preserve existing price precision.
            for value in (request.price, request.trigger_price):
                if value is not None and current.price is not None:
                    tick = Decimal(1).scaleb(current.price.as_tuple().exponent)
                    if not is_tick_aligned(value, tick):
                        return self._validation_failure("modify", BrokerOrderManagerValidationError("unaligned price", code="BOM.VALIDATION.TICK_SIZE"))
            if current.order_type is OrderType.MARKET and request.price is not None:
                return self._validation_failure("modify", BrokerOrderManagerValidationError("market price forbidden", code="BOM.VALIDATION.ORDER_TYPE"))
        return self._mutate("modify", broker_order_id, lambda: self._transport.modify_order(broker_order_id, request), current, TOPIC_ORDER_MODIFIED)

    def _mutate(self, operation: str, order_id: str, call: Callable[[], Mapping[str, object]], current: BrokerOrder | None, topic: str) -> BrokerOrderResult:
        """Run a bounded retrying non-placement operation."""
        counter = "modification_attempts" if operation == "modify" else "cancellation_attempts"
        for attempt in range(1, self._config.max_attempts + 1):
            with self._lock:
                self._counts[counter] += 1
            started = self._monotonic()
            try:
                raw = call()
                self._record_transport(True, started)
                if "order_id" not in raw:
                    raw = self._transport.fetch_order(order_id)
                order = self._normalize(raw, previous=current)
                return self._seal(BrokerOrderResult(operation, True, order, attempt, str(raw.get("request_id")) if raw.get("request_id") else None, None, None, self._clock()), topic)
            except BrokerRejectedError as exc:
                with self._lock: self._counts["rejections"] += 1
                result = self._failure(operation, exc.code, exc.message, attempt, current); self._publish(TOPIC_ORDER_REJECTED, result); return result
            except (RetryableTransportError, AmbiguousTransportError) as exc:
                self._record_transport(False, started)
                if attempt == self._config.max_attempts:
                    return self._failure(operation, exc.code, exc.message, attempt, current)
                with self._lock: self._counts["retry_attempts"] += 1
                self._sleeper(self._backoff(attempt))
            except BrokerOrderManagerError as exc:
                return self._failure(operation, exc.code, exc.message, attempt, current)
            except Exception:
                self._record_transport(False, started)
                return self._failure(operation, "BOM.INTERNAL.INVARIANT", "unexpected transport failure", attempt, current)
        return self._failure(operation, "BOM.INTERNAL.INVARIANT", "retry invariant", self._config.max_attempts, current)

    def cancel(self, broker_order_id: str, *, variety: str = "regular") -> BrokerOrderResult:
        """Cancel an order, preserving terminal-state idempotency semantics."""
        if not isinstance(broker_order_id, str) or not broker_order_id.strip():
            return self._validation_failure("cancel", BrokerOrderManagerValidationError("empty broker order id", code="BOM.VALIDATION.REQUEST"))
        with self._lock:
            current = self._orders.get(broker_order_id)
        if current:
            if current.status is BrokerOrderStatus.CANCELLED:
                return BrokerOrderResult("cancel", True, current, 0, None, None, None, self._clock())
            if current.status is BrokerOrderStatus.COMPLETE:
                return self._failure("cancel", "BOM.STATE.ALREADY_COMPLETE", "order already complete", order=current)
            if current.status is BrokerOrderStatus.REJECTED:
                return self._failure("cancel", "BOM.STATE.ALREADY_REJECTED", "order already rejected", order=current)
        return self._mutate("cancel", broker_order_id, lambda: self._transport.cancel_order(broker_order_id, variety), current, TOPIC_ORDER_CANCELLED)

    def cancel_many(self, broker_order_ids: Sequence[str]) -> tuple[BrokerOrderResult, ...]:
        """Cancel a batch while preserving input ordering and duplicate outcomes."""
        if len(broker_order_ids) > self._config.max_batch_cancel_size:
            return tuple(self._validation_failure("cancel_many", BrokerOrderManagerValidationError("batch too large", code="BOM.BATCH.TOO_LARGE")) for _ in broker_order_ids)
        sealed: dict[str, BrokerOrderResult] = {}
        results: list[BrokerOrderResult] = []
        for order_id in broker_order_ids:
            if order_id not in sealed:
                sealed[order_id] = self.cancel(order_id)
            results.append(sealed[order_id])
        return tuple(results)

    def get_status(self, broker_order_id: str, *, refresh: bool = True) -> BrokerOrderResult:
        """Return a locally sealed snapshot or refresh it from the transport."""
        if not isinstance(broker_order_id, str) or not broker_order_id.strip():
            return self._validation_failure("status", BrokerOrderManagerValidationError("empty broker order id", code="BOM.VALIDATION.REQUEST"))
        with self._lock:
            current = self._orders.get(broker_order_id)
        if not refresh:
            return BrokerOrderResult("status", current is not None, current, 0, None, None if current else "BOM.ORDER.NOT_FOUND", None if current else "order not found", self._clock())
        started = self._monotonic()
        try:
            raw = self._transport.fetch_order(broker_order_id); self._record_transport(True, started)
            order = self._normalize(raw, previous=current)
            if current and order.filled_quantity < current.filled_quantity:
                return self._failure("status", "BOM.RECONCILIATION.FILL_REGRESSION", "reported fill regressed", 1, current)
            if current and order.updated_at < current.updated_at:
                order = current
            with self._lock: self._counts["status_fetches"] += 1; self._orders[broker_order_id] = order
            result = BrokerOrderResult("status", True, order, 1, None, None, None, self._clock()); self._publish(TOPIC_ORDER_STATUS_UPDATED, order); return result
        except BrokerOrderManagerError as exc:
            return self._failure("status", exc.code, exc.message, 1, current)
        except Exception:
            self._record_transport(False, started); return self._failure("status", "BOM.TRANSPORT.NETWORK", "status fetch failed", 1, current)

    def track_executions(self, broker_order_id: str) -> BrokerOrderResult:
        """Fetch, deduplicate, and reconcile reported executions."""
        status = self.get_status(broker_order_id, refresh=True)
        if not status.success or status.order is None:
            return replace(status, operation="track_executions")
        try:
            rows = self._transport.fetch_trades(broker_order_id)
            executions: dict[str, BrokerExecution] = {item.trade_id: item for item in status.order.executions}
            for row in rows:
                trade_id = str(row["trade_id"])
                if trade_id in executions: continue
                quantity = int(row["quantity"]); price = Decimal(str(row.get("average_price", row.get("price"))))
                if quantity <= 0 or price <= 0: raise ValueError("invalid execution")
                executed_at = _datetime(row["fill_timestamp"]) if "fill_timestamp" in row else self._clock()
                executions[trade_id] = BrokerExecution(trade_id, broker_order_id, quantity, price, executed_at, Exchange(str(row.get("exchange", status.order.exchange.value))))
            ordered = tuple(sorted(executions.values(), key=lambda item: (item.executed_at, item.trade_id)))
            filled = sum(item.quantity for item in ordered)
            if filled < status.order.filled_quantity:
                raise BrokerOrderManagerReconciliationError("reported fill regressed", code="BOM.RECONCILIATION.FILL_REGRESSION")
            if filled > status.order.quantity:
                raise BrokerOrderManagerReconciliationError("execution quantity exceeds order", code="BOM.RECONCILIATION.FILL_REGRESSION")
            average = None if not filled else sum((item.price * item.quantity for item in ordered), Decimal(0)) / Decimal(filled)
            order = replace(status.order, executions=ordered, filled_quantity=filled, remaining_quantity=status.order.quantity - filled, average_price=average, version=status.order.version + 1, updated_at=self._clock())
            if order.status is BrokerOrderStatus.COMPLETE and order.remaining_quantity:
                raise BrokerOrderManagerReconciliationError("complete order has remaining quantity", code="BOM.RECONCILIATION.FILL_REGRESSION")
            with self._lock:
                self._orders[broker_order_id] = order; self._counts["executions_observed"] += len(ordered) - len(status.order.executions)
            result = BrokerOrderResult("track_executions", True, order, 1, None, None, None, self._clock())
            for execution in ordered: self._publish(TOPIC_ORDER_EXECUTION_OBSERVED, execution)
            return result
        except BrokerOrderManagerError as exc:
            return self._failure("track_executions", exc.code, exc.message, 1, status.order)
        except Exception:
            return self._failure("track_executions", "BOM.NORMALIZATION.RESPONSE", "invalid trade response", 1, status.order)

    def get_health(self) -> BrokerHealth:
        """Return an immutable health snapshot without performing I/O."""
        with self._lock:
            rate = Decimal(sum(self._outcomes)) / Decimal(len(self._outcomes)) if self._outcomes else Decimal(0)
            latency = sum(self._latencies, Decimal(0)) / Decimal(len(self._latencies)) if self._latencies else Decimal(0)
            execution = sum(self._execution_times, Decimal(0)) / Decimal(len(self._execution_times)) if self._execution_times else Decimal(0)
            result = BrokerHealth(bool(self._outcomes) and not self._outcomes[-1] and self._consecutive_failures == 0, self._last_success, self._last_failure, rate, latency, execution, self._consecutive_failures, self._clock())
        self._publish(TOPIC_HEALTH_UPDATED, result)
        return result

    def get_statistics(self) -> BrokerStatistics:
        """Return an immutable lifetime counter snapshot."""
        with self._lock:
            return BrokerStatistics(**self._counts, generated_at=self._clock())
