"""DTO mapping helpers for Kite broker responses."""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType
from typing import Mapping

from broker.base_broker import (
    AccountProfile,
    Exchange,
    FundsSnapshot,
    HoldingRecord,
    MarginSnapshot,
    OrderRecord,
    OrderSide,
    OrderStatus,
    OrderType,
    OrderVariety,
    PlaceOrderResult,
    PositionRecord,
    ProductType,
)
from broker.zerodha._kite_copy import copy_mapping


def split_instrument_key(instrument_key: str) -> tuple[str, str]:
    """Split an instrument key into exchange and tradingsymbol."""
    if ":" not in instrument_key:
        raise ValueError(f"invalid instrument key: {instrument_key!r}")
    exchange, symbol = instrument_key.split(":", 1)
    return exchange, symbol


def map_exchange(value: str) -> Exchange:
    """Map a Kite exchange string to Exchange enum."""
    try:
        return Exchange(value.lower())
    except ValueError:
        return Exchange.UNKNOWN


def map_product(value: str) -> ProductType:
    """Map a Kite product string to ProductType enum."""
    try:
        return ProductType(value.lower())
    except ValueError:
        return ProductType.MIS


def map_order_side(value: str) -> OrderSide:
    """Map Kite transaction type to OrderSide."""
    normalized = value.upper()
    return OrderSide.BUY if normalized == "BUY" else OrderSide.SELL


def map_order_type(value: str) -> OrderType:
    """Map Kite order type to OrderType."""
    mapping = {
        "MARKET": OrderType.MARKET,
        "LIMIT": OrderType.LIMIT,
        "SL": OrderType.SL,
        "SL-M": OrderType.SL_M,
    }
    return mapping.get(value.upper(), OrderType.MARKET)


def map_order_status(value: str) -> OrderStatus:
    """Map Kite order status to OrderStatus."""
    mapping = {
        "OPEN": OrderStatus.OPEN,
        "COMPLETE": OrderStatus.COMPLETE,
        "CANCELLED": OrderStatus.CANCELLED,
        "REJECTED": OrderStatus.REJECTED,
        "TRIGGER PENDING": OrderStatus.PENDING,
    }
    return mapping.get(value.upper(), OrderStatus.UNKNOWN)


def map_order_variety(value: str | None) -> OrderVariety:
    """Map Kite variety to OrderVariety."""
    if value is None:
        return OrderVariety.REGULAR
    try:
        return OrderVariety(value.lower())
    except ValueError:
        return OrderVariety.REGULAR


def to_kite_transaction_type(side: OrderSide) -> str:
    """Map OrderSide to Kite transaction type."""
    return "BUY" if side is OrderSide.BUY else "SELL"


def to_kite_order_type(order_type: OrderType) -> str:
    """Map OrderType to Kite order type."""
    mapping = {
        OrderType.MARKET: "MARKET",
        OrderType.LIMIT: "LIMIT",
        OrderType.SL: "SL",
        OrderType.SL_M: "SL-M",
    }
    return mapping[order_type]


def to_kite_product(product: ProductType) -> str:
    """Map ProductType to Kite product string."""
    return product.value.upper()


def to_kite_variety(variety: OrderVariety) -> str:
    """Map OrderVariety to Kite variety string."""
    return variety.value


def map_place_order_result(response: Mapping[str, object]) -> PlaceOrderResult:
    """Map a Kite place_order response to PlaceOrderResult."""
    order_id = str(response.get("order_id", ""))
    return PlaceOrderResult(
        order_id=order_id,
        status=OrderStatus.OPEN,
        message="order placed",
        broker_order_id=order_id,
        raw=copy_mapping(response),
    )


def map_order_record(record: Mapping[str, object]) -> OrderRecord:
    """Map a Kite order dict to OrderRecord."""
    exchange = str(record.get("exchange", "UNKNOWN"))
    symbol = str(record.get("tradingsymbol", ""))
    return OrderRecord(
        order_id=str(record.get("order_id", "")),
        instrument_key=f"{exchange}:{symbol}",
        side=map_order_side(str(record.get("transaction_type", "BUY"))),
        order_type=map_order_type(str(record.get("order_type", "MARKET"))),
        product=map_product(str(record.get("product", "MIS"))),
        quantity=int(record.get("quantity", 0)),
        status=map_order_status(str(record.get("status", "UNKNOWN"))),
        price=_optional_float(record.get("price")),
        trigger_price=_optional_float(record.get("trigger_price")),
        variety=map_order_variety(str(record.get("variety")) if record.get("variety") else None),
        message=str(record.get("status_message")) if record.get("status_message") else None,
        broker_order_id=str(record.get("order_id", "")),
        raw=copy_mapping(record),
    )


def map_position_record(record: Mapping[str, object]) -> PositionRecord:
    """Map a Kite position dict to PositionRecord."""
    exchange = str(record.get("exchange", "UNKNOWN"))
    symbol = str(record.get("tradingsymbol", ""))
    return PositionRecord(
        instrument_key=f"{exchange}:{symbol}",
        product=map_product(str(record.get("product", "MIS"))),
        quantity=int(record.get("quantity", 0)),
        average_price=float(record.get("average_price", 0.0)),
        exchange=map_exchange(exchange),
        last_price=_optional_float(record.get("last_price")),
        pnl=_optional_float(record.get("pnl")),
        broker_position_id=str(record.get("instrument_token"))
        if record.get("instrument_token") is not None
        else None,
        raw=copy_mapping(record),
    )


def map_holding_record(record: Mapping[str, object]) -> HoldingRecord:
    """Map a Kite holding dict to HoldingRecord."""
    exchange = str(record.get("exchange", "UNKNOWN"))
    symbol = str(record.get("tradingsymbol", ""))
    return HoldingRecord(
        instrument_key=f"{exchange}:{symbol}",
        quantity=int(record.get("quantity", 0)),
        average_price=float(record.get("average_price", 0.0)),
        exchange=map_exchange(exchange),
        collateral_quantity=_optional_int(record.get("collateral_quantity")),
        collateral_type=str(record.get("collateral_type"))
        if record.get("collateral_type") is not None
        else None,
        raw=copy_mapping(record),
    )


def map_margin_snapshot(response: Mapping[str, object]) -> MarginSnapshot:
    """Map Kite margins response to MarginSnapshot."""
    equity = response.get("equity", {})
    commodity = response.get("commodity", {})
    assert isinstance(equity, Mapping)
    available = equity.get("available", {})
    utilised = equity.get("utilised", {})
    assert isinstance(available, Mapping)
    assert isinstance(utilised, Mapping)
    commodity_available = commodity.get("available", {}) if isinstance(commodity, Mapping) else {}
    commodity_live = (
        commodity_available.get("live_balance", 0.0)
        if isinstance(commodity_available, Mapping)
        else 0.0
    )
    return MarginSnapshot(
        available=float(available.get("live_balance", 0.0)),
        used=float(utilised.get("debits", 0.0)),
        total=float(equity.get("net", 0.0)),
        as_of=datetime.now(timezone.utc),
        span=_optional_float(available.get("span")),
        exposure=_optional_float(available.get("exposure")),
        commodity_available=float(commodity_live),
    )


def map_account_profile(response: Mapping[str, object]) -> AccountProfile:
    """Map Kite profile response to AccountProfile."""
    exchanges = tuple(
        map_exchange(str(item))
        for item in response.get("exchanges", [])
        if isinstance(response.get("exchanges"), list)
    )
    products = tuple(
        map_product(str(item))
        for item in response.get("products", [])
        if isinstance(response.get("products"), list)
    )
    return AccountProfile(
        user_id=str(response.get("user_id", "")),
        user_name=str(response.get("user_name", "")),
        broker=str(response.get("broker", "ZERODHA")),
        exchanges=exchanges,
        products=products,
        email=str(response.get("email")) if response.get("email") else None,
    )


def map_funds_snapshot(response: Mapping[str, object]) -> FundsSnapshot:
    """Map Kite margins response to FundsSnapshot."""
    equity = response.get("equity", {})
    commodity = response.get("commodity", {})
    assert isinstance(equity, Mapping)
    assert isinstance(commodity, Mapping)
    equity_available = equity.get("available", {})
    commodity_available = commodity.get("available", {})
    assert isinstance(equity_available, Mapping)
    assert isinstance(commodity_available, Mapping)
    return FundsSnapshot(
        equity_available=float(equity_available.get("live_balance", 0.0)),
        commodity_available=float(commodity_available.get("live_balance", 0.0)),
        as_of=datetime.now(timezone.utc),
    )


def cache_instrument_tokens(
    instruments: tuple[Mapping[str, object], ...],
) -> dict[str, int]:
    """Build instrument_key → token cache from instrument master rows."""
    cache: dict[str, int] = {}
    for row in instruments:
        exchange = row.get("exchange")
        symbol = row.get("tradingsymbol")
        token = row.get("instrument_token")
        if exchange is None or symbol is None or token is None:
            continue
        cache[f"{exchange}:{symbol}"] = int(token)
    return cache


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)
