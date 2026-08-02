"""Broker-boundary normalization layer for THETA AI TRADER.

This module is the exclusive parser for Zerodha Kite Connect v3 payloads.
All downstream components consume immutable types from ``market_data.market_snapshot``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Final, Generic, TypeVar

from market_data.market_snapshot import (
    MARKET_SNAPSHOT_SCHEMA_VERSION,
    MarketSnapshot,
    OptionContractSnapshot,
    OptionType,
    SnapshotBuildError,
    SnapshotSource,
    UnderlyingSnapshot,
    VolatilitySnapshot,
    build_market_snapshot,
)

T = TypeVar("T")

MARKET_DATA_ADAPTER_VERSION: Final[str] = "1.0.0"
SUPPORTED_BROKER: Final[str] = "KITE_CONNECT_V3"
VALID_DERIVATIVE_EXCHANGES: Final[frozenset[str]] = frozenset({"NFO", "BFO"})
VALID_SPOT_EXCHANGES: Final[frozenset[str]] = frozenset({"NSE", "BSE"})
VALID_OPTION_TYPES: Final[frozenset[OptionType]] = frozenset({OptionType.CE, OptionType.PE})

# Request errors
ERROR_REQUEST_UNDERLYING_REQUIRED: Final[str] = (
    "MARKET_DATA_ADAPTER.REQUEST.UNDERLYING_REQUIRED"
)
ERROR_REQUEST_INVALID_EXCHANGE: Final[str] = "MARKET_DATA_ADAPTER.REQUEST.INVALID_EXCHANGE"
ERROR_REQUEST_INSTRUMENTS_REQUIRED: Final[str] = (
    "MARKET_DATA_ADAPTER.REQUEST.INSTRUMENTS_REQUIRED"
)
ERROR_REQUEST_QUOTES_INVALID: Final[str] = "MARKET_DATA_ADAPTER.REQUEST.QUOTES_INVALID"
ERROR_REQUEST_INVALID_AS_OF: Final[str] = "MARKET_DATA_ADAPTER.REQUEST.INVALID_AS_OF"
ERROR_REQUEST_INVALID_OPTION_TYPES: Final[str] = (
    "MARKET_DATA_ADAPTER.REQUEST.INVALID_OPTION_TYPES"
)

# Instrument errors
ERROR_INSTRUMENT_INVALID_OBJECT: Final[str] = (
    "MARKET_DATA_ADAPTER.INSTRUMENT.INVALID_OBJECT"
)
ERROR_INSTRUMENT_MISSING_UNDERLYING: Final[str] = (
    "MARKET_DATA_ADAPTER.INSTRUMENT.MISSING_UNDERLYING"
)
ERROR_INSTRUMENT_INVALID_EXCHANGE: Final[str] = "MARKET_DATA_ADAPTER.INSTRUMENT.INVALID_EXCHANGE"
ERROR_INSTRUMENT_MISSING_TRADINGSYMBOL: Final[str] = (
    "MARKET_DATA_ADAPTER.INSTRUMENT.MISSING_TRADINGSYMBOL"
)
ERROR_INSTRUMENT_INVALID_OPTION_TYPE: Final[str] = (
    "MARKET_DATA_ADAPTER.INSTRUMENT.INVALID_OPTION_TYPE"
)
ERROR_INSTRUMENT_MISSING_EXPIRY: Final[str] = "MARKET_DATA_ADAPTER.INSTRUMENT.MISSING_EXPIRY"
ERROR_INSTRUMENT_INVALID_STRIKE: Final[str] = "MARKET_DATA_ADAPTER.INSTRUMENT.INVALID_STRIKE"
ERROR_INSTRUMENT_INVALID_LOT_SIZE: Final[str] = "MARKET_DATA_ADAPTER.INSTRUMENT.INVALID_LOT_SIZE"
ERROR_INSTRUMENT_MISSING_INSTRUMENT_TOKEN: Final[str] = (
    "MARKET_DATA_ADAPTER.INSTRUMENT.MISSING_INSTRUMENT_TOKEN"
)
ERROR_INSTRUMENT_INVALID_TICK_SIZE: Final[str] = "MARKET_DATA_ADAPTER.INSTRUMENT.INVALID_TICK_SIZE"
ERROR_INSTRUMENT_DUPLICATE_QUOTE_KEY: Final[str] = (
    "MARKET_DATA_ADAPTER.INSTRUMENT.DUPLICATE_QUOTE_KEY"
)

# Quote errors
ERROR_QUOTE_INVALID_OBJECT: Final[str] = "MARKET_DATA_ADAPTER.QUOTE.INVALID_OBJECT"
ERROR_QUOTE_INVALID_LTP: Final[str] = "MARKET_DATA_ADAPTER.QUOTE.INVALID_LTP"
ERROR_QUOTE_INVALID_VOLUME: Final[str] = "MARKET_DATA_ADAPTER.QUOTE.INVALID_VOLUME"
ERROR_QUOTE_INVALID_OPEN_INTEREST: Final[str] = "MARKET_DATA_ADAPTER.QUOTE.INVALID_OPEN_INTEREST"
ERROR_QUOTE_MISSING_BID: Final[str] = "MARKET_DATA_ADAPTER.QUOTE.MISSING_BID"
ERROR_QUOTE_MISSING_ASK: Final[str] = "MARKET_DATA_ADAPTER.QUOTE.MISSING_ASK"
ERROR_QUOTE_INVERTED_MARKET: Final[str] = "MARKET_DATA_ADAPTER.QUOTE.INVERTED_MARKET"
ERROR_QUOTE_NOT_FOUND: Final[str] = "MARKET_DATA_ADAPTER.QUOTE.NOT_FOUND"

# Spot / VIX errors
ERROR_SPOT_INVALID_OBJECT: Final[str] = "MARKET_DATA_ADAPTER.SPOT.INVALID_OBJECT"
ERROR_SPOT_INVALID_PRICE: Final[str] = "MARKET_DATA_ADAPTER.SPOT.INVALID_PRICE"
ERROR_VIX_INVALID_PRICE: Final[str] = "MARKET_DATA_ADAPTER.VIX.INVALID_PRICE"

# Chain errors
ERROR_CHAIN_NO_VALID_CONTRACTS: Final[str] = "MARKET_DATA_ADAPTER.CHAIN.NO_VALID_CONTRACTS"
ERROR_CHAIN_BELOW_MINIMUM: Final[str] = "MARKET_DATA_ADAPTER.CHAIN.BELOW_MINIMUM"

# Warnings
WARNING_TIMESTAMP_UNPARSEABLE: Final[str] = "MARKET_DATA_ADAPTER.TIMESTAMP.UNPARSEABLE"
WARNING_OHLC_INCONSISTENT: Final[str] = "MARKET_DATA_ADAPTER.OHLC.INCONSISTENT"
WARNING_OHLC_MISSING: Final[str] = "MARKET_DATA_ADAPTER.OHLC.MISSING"
WARNING_OI_NEGATIVE_CLAMPED: Final[str] = "MARKET_DATA_ADAPTER.OI.NEGATIVE_CLAMPED"

_EXPIRY_FORMATS: Final[tuple[str, ...]] = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%d/%m/%Y",
)

_DEFAULT_SPOT_SYMBOLS: Final[Mapping[str, tuple[str, str, str]]] = {
    "NIFTY": ("NIFTY 50", "NSE", "NSE:NIFTY 50"),
    "BANKNIFTY": ("BANKNIFTY", "NSE", "NSE:BANKNIFTY"),
    "FINNIFTY": ("FINNIFTY", "NSE", "NSE:FINNIFTY"),
    "SENSEX": ("SENSEX", "BSE", "BSE:SENSEX"),
}


class AdapterPermission(str, Enum):
    """Overall adapter outcome for orchestrators."""

    ALLOW = "ALLOW"
    PARTIAL = "PARTIAL"
    BLOCK = "BLOCK"


class AdapterRejectionReason(str, Enum):
    """Machine-readable rejection categories."""

    INVALID_INSTRUMENT = "INVALID_INSTRUMENT"
    QUOTE_NOT_FOUND = "QUOTE_NOT_FOUND"
    INVALID_QUOTE = "INVALID_QUOTE"
    DUPLICATE_INSTRUMENT = "DUPLICATE_INSTRUMENT"
    FILTERED_OUT = "FILTERED_OUT"
    CONTRACT_BUILD_FAILED = "CONTRACT_BUILD_FAILED"


class BrokerFormat(str, Enum):
    """Source payload type hints for normalization entry points."""

    KITE_INSTRUMENT = "KITE_INSTRUMENT"
    KITE_QUOTE = "KITE_QUOTE"
    KITE_INDEX_QUOTE = "KITE_INDEX_QUOTE"


class AdapterConfigurationError(Exception):
    """Raised when adapter policy configuration is invalid."""


class AdapterInputError(Exception):
    """Raised for non-recoverable invalid request parameters before normalization."""


@dataclass(frozen=True)
class AdapterErrorRecord:
    """Structured adapter error record."""

    code: str
    message: str
    field: str | None = None
    broker_field: str | None = None


@dataclass(frozen=True)
class AdapterWarningRecord:
    """Non-fatal normalization warning."""

    code: str
    message: str
    field: str | None = None
    broker_field: str | None = None


@dataclass(frozen=True)
class AdapterRejectionRecord:
    """One rejected instrument or contract."""

    tradingsymbol: str | None
    reason: AdapterRejectionReason
    errors: tuple[AdapterErrorRecord, ...]


@dataclass(frozen=True)
class NormalizedInstrument:
    """Canonical instrument identity after Kite instrument normalization."""

    underlying: str
    exchange: str
    tradingsymbol: str
    expiry: str
    strike: float
    option_type: OptionType
    lot_size: int
    instrument_token: int
    quote_key: str
    exchange_token: int | None = None
    tick_size: float | None = None


@dataclass(frozen=True)
class NormalizedQuote:
    """Canonical quote fields before contract assembly."""

    ltp: float
    bid: float
    ask: float
    volume: int
    open_interest: int
    quote_timestamp: datetime | None
    last_quantity: int
    average_price: float | None
    buy_quantity: int
    sell_quantity: int
    oi_day_high: int
    oi_day_low: int


@dataclass(frozen=True)
class NormalizedGreeks:
    """Optional Greeks attachment."""

    delta: float | None = None
    iv: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


@dataclass(frozen=True)
class NormalizationResult(Generic[T]):
    """Immutable result of a single normalization step."""

    valid: bool
    value: T | None
    errors: tuple[AdapterErrorRecord, ...]
    warnings: tuple[AdapterWarningRecord, ...]


@dataclass(frozen=True)
class AdapterPolicy:
    """Configurable adapter strictness and chain window."""

    strict: bool = False
    minimum_contracts: int = 1
    strikes_each_side: int = 10

    def __post_init__(self) -> None:
        if self.minimum_contracts <= 0:
            raise AdapterConfigurationError("minimum_contracts must be greater than zero.")
        if self.strikes_each_side < 0:
            raise AdapterConfigurationError("strikes_each_side cannot be negative.")


@dataclass(frozen=True)
class AdapterBuildRequest:
    """Request parameters for full snapshot assembly."""

    underlying: str
    as_of: datetime
    correlation_id: str | None = None
    expiry: str | None = None
    exchange: str | None = "NFO"
    strikes_each_side: int | None = None
    option_types: tuple[OptionType, ...] | None = None
    captured_at: datetime | None = None
    source: SnapshotSource = SnapshotSource.LIVE
    reference_date: date | None = None


@dataclass(frozen=True)
class OptionChainBuildResult:
    """Immutable option chain build outcome."""

    contracts: tuple[OptionContractSnapshot, ...]
    rejections: tuple[AdapterRejectionRecord, ...]
    underlying: str
    expiry: str | None
    exchange: str | None
    instrument_count: int
    matched_instruments: int
    normalized_count: int
    rejected_count: int


@dataclass(frozen=True)
class AdapterBuildResult:
    """Top-level adapter result."""

    permission: AdapterPermission
    adapter_allowed: bool
    reason: str
    snapshot: MarketSnapshot | None
    validation_errors: tuple[AdapterErrorRecord, ...]
    rejections: tuple[AdapterRejectionRecord, ...]
    instrument_count: int
    matched_instruments: int
    normalized_count: int
    rejected_count: int
    broker_order_allowed: bool


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        result = float(value)
        if not math.isfinite(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _normalize_expiry(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    for fmt in _EXPIRY_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _normalize_timestamp(value: Any) -> tuple[datetime | None, tuple[AdapterWarningRecord, ...]]:
    if value is None:
        return None, ()
    warnings: list[AdapterWarningRecord] = []
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None, ()
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            warnings.append(
                AdapterWarningRecord(
                    code=WARNING_TIMESTAMP_UNPARSEABLE,
                    message="Could not parse timestamp string.",
                    field="timestamp",
                    broker_field="timestamp",
                )
            )
            return None, tuple(warnings)
    else:
        warnings.append(
            AdapterWarningRecord(
                code=WARNING_TIMESTAMP_UNPARSEABLE,
                message="Unsupported timestamp type.",
                field="timestamp",
            )
        )
        return None, tuple(warnings)
    if not _is_timezone_aware(parsed):
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed, tuple(warnings)


def _extract_best_bid(quote: Mapping[str, Any]) -> float | None:
    try:
        buy_depth = quote.get("depth", {}).get("buy", [])
        if not isinstance(buy_depth, list) or not buy_depth:
            return None
        prices: list[float] = []
        for level in buy_depth:
            if not isinstance(level, dict):
                continue
            price = _safe_float(level.get("price"))
            if price is not None and price > 0:
                prices.append(price)
        return max(prices) if prices else None
    except (AttributeError, TypeError):
        return None


def _extract_best_ask(quote: Mapping[str, Any]) -> float | None:
    try:
        sell_depth = quote.get("depth", {}).get("sell", [])
        if not isinstance(sell_depth, list) or not sell_depth:
            return None
        prices: list[float] = []
        for level in sell_depth:
            if not isinstance(level, dict):
                continue
            price = _safe_float(level.get("price"))
            if price is not None and price > 0:
                prices.append(price)
        return min(prices) if prices else None
    except (AttributeError, TypeError):
        return None


def _find_greeks(
    greeks_map: Mapping[str, Any] | None,
    *,
    quote_key: str | None,
    tradingsymbol: str,
    instrument_token: int | None,
) -> Mapping[str, Any] | None:
    if greeks_map is None or not isinstance(greeks_map, Mapping):
        return None
    possible_keys: list[Any] = [
        quote_key,
        tradingsymbol,
        instrument_token,
        str(instrument_token) if instrument_token is not None else None,
    ]
    for key in possible_keys:
        if key is not None and key in greeks_map:
            candidate = greeks_map[key]
            if isinstance(candidate, Mapping):
                return candidate
    return None


def _non_negative_int(value: Any, default: int = 0) -> tuple[int, tuple[AdapterWarningRecord, ...]]:
    parsed = _safe_int(value, default=default)
    if parsed is None:
        return default, ()
    if parsed < 0:
        return 0, (
            AdapterWarningRecord(
                code=WARNING_OI_NEGATIVE_CLAMPED,
                message="Negative quantity clamped to zero.",
            ),
        )
    return parsed, ()


class MarketDataAdapter:
    """Normalizes Kite Connect payloads into canonical market snapshot types."""

    def __init__(self, policy: AdapterPolicy | None = None) -> None:
        self._policy = policy or AdapterPolicy()

    @property
    def policy(self) -> AdapterPolicy:
        """Return the immutable adapter policy."""
        return self._policy

    def build_quote_key(self, exchange: Any, tradingsymbol: Any) -> str | None:
        """Construct a Kite quote lookup key."""
        exchange_text = _normalize_text(exchange)
        symbol_text = _normalize_text(tradingsymbol)
        if not exchange_text or not symbol_text:
            return None
        return f"{exchange_text}:{symbol_text}"

    def normalize_instrument(
        self,
        raw: Mapping[str, Any] | Any,
    ) -> NormalizationResult[NormalizedInstrument]:
        """Normalize one Kite instrument master record."""
        if not isinstance(raw, Mapping):
            return NormalizationResult(
                valid=False,
                value=None,
                errors=(
                    AdapterErrorRecord(
                        code=ERROR_INSTRUMENT_INVALID_OBJECT,
                        message="Instrument payload must be a mapping.",
                    ),
                ),
                warnings=(),
            )

        underlying = _normalize_text(raw.get("name"))
        exchange = _normalize_text(raw.get("exchange"))
        tradingsymbol = _normalize_text(raw.get("tradingsymbol"))
        option_type_text = _normalize_text(raw.get("instrument_type"))
        expiry = _normalize_expiry(raw.get("expiry"))
        strike = _safe_float(raw.get("strike"))
        lot_size = _safe_int(raw.get("lot_size"))
        instrument_token = raw.get("instrument_token")
        exchange_token = _safe_int(raw.get("exchange_token"))
        tick_size = _safe_float(raw.get("tick_size"))

        errors: list[AdapterErrorRecord] = []
        if not underlying:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_INSTRUMENT_MISSING_UNDERLYING,
                    message="Instrument missing underlying name.",
                    field="underlying",
                    broker_field="name",
                )
            )
        if exchange not in VALID_DERIVATIVE_EXCHANGES:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_INSTRUMENT_INVALID_EXCHANGE,
                    message="Instrument exchange is not supported.",
                    field="exchange",
                    broker_field="exchange",
                )
            )
        if not tradingsymbol:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_INSTRUMENT_MISSING_TRADINGSYMBOL,
                    message="Instrument missing tradingsymbol.",
                    field="tradingsymbol",
                    broker_field="tradingsymbol",
                )
            )
        try:
            option_type = OptionType(option_type_text)
        except ValueError:
            option_type = None
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_INSTRUMENT_INVALID_OPTION_TYPE,
                    message="Instrument option type must be CE or PE.",
                    field="option_type",
                    broker_field="instrument_type",
                )
            )
        if not expiry:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_INSTRUMENT_MISSING_EXPIRY,
                    message="Instrument missing expiry.",
                    field="expiry",
                    broker_field="expiry",
                )
            )
        if strike is None or strike <= 0:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_INSTRUMENT_INVALID_STRIKE,
                    message="Instrument strike must be finite and greater than zero.",
                    field="strike",
                    broker_field="strike",
                )
            )
        if lot_size is None or lot_size <= 0:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_INSTRUMENT_INVALID_LOT_SIZE,
                    message="Instrument lot_size must be greater than zero.",
                    field="lot_size",
                    broker_field="lot_size",
                )
            )
        if instrument_token is None:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_INSTRUMENT_MISSING_INSTRUMENT_TOKEN,
                    message="Instrument missing instrument_token.",
                    field="instrument_token",
                    broker_field="instrument_token",
                )
            )
        if tick_size is not None and tick_size <= 0:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_INSTRUMENT_INVALID_TICK_SIZE,
                    message="Instrument tick_size must be greater than zero when present.",
                    field="tick_size",
                    broker_field="tick_size",
                )
            )

        quote_key = self.build_quote_key(exchange, tradingsymbol)
        if quote_key is None:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_INSTRUMENT_MISSING_TRADINGSYMBOL,
                    message="Could not derive quote key.",
                    field="quote_key",
                )
            )

        if errors or option_type is None or strike is None or lot_size is None:
            return NormalizationResult(valid=False, value=None, errors=tuple(errors), warnings=())

        assert quote_key is not None
        assert instrument_token is not None
        return NormalizationResult(
            valid=True,
            value=NormalizedInstrument(
                underlying=underlying,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                expiry=expiry or "",
                strike=strike,
                option_type=option_type,
                lot_size=lot_size,
                instrument_token=int(instrument_token),
                quote_key=quote_key,
                exchange_token=exchange_token,
                tick_size=tick_size,
            ),
            errors=(),
            warnings=(),
        )

    def normalize_quote(
        self,
        raw: Mapping[str, Any] | Any,
    ) -> NormalizationResult[NormalizedQuote]:
        """Normalize one Kite derivative quote record."""
        if not isinstance(raw, Mapping):
            return NormalizationResult(
                valid=False,
                value=None,
                errors=(
                    AdapterErrorRecord(
                        code=ERROR_QUOTE_INVALID_OBJECT,
                        message="Quote payload must be a mapping.",
                    ),
                ),
                warnings=(),
            )

        ltp = _safe_float(raw.get("last_price"))
        volume = _safe_int(raw.get("volume"), default=0) or 0
        open_interest = _safe_int(raw.get("oi"), default=0) or 0
        bid = _extract_best_bid(raw)
        ask = _extract_best_ask(raw)
        timestamp_raw = raw.get("timestamp") or raw.get("last_trade_time")
        quote_timestamp, timestamp_warnings = _normalize_timestamp(timestamp_raw)

        errors: list[AdapterErrorRecord] = []
        warnings: list[AdapterWarningRecord] = list(timestamp_warnings)

        if ltp is None or ltp <= 0:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_QUOTE_INVALID_LTP,
                    message="Quote last_price must be finite and greater than zero.",
                    field="ltp",
                    broker_field="last_price",
                )
            )
        if volume < 0:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_QUOTE_INVALID_VOLUME,
                    message="Quote volume must be non-negative.",
                    field="volume",
                    broker_field="volume",
                )
            )
        if open_interest < 0:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_QUOTE_INVALID_OPEN_INTEREST,
                    message="Quote open interest must be non-negative.",
                    field="open_interest",
                    broker_field="oi",
                )
            )

        bid_value = bid if bid is not None and bid > 0 else 0.0
        ask_value = ask if ask is not None and ask > 0 else 0.0

        if bid is None or bid <= 0:
            message = "Quote bid is missing or zero."
            if self._policy.strict:
                errors.append(
                    AdapterErrorRecord(
                        code=ERROR_QUOTE_MISSING_BID,
                        message=message,
                        field="bid",
                    )
                )
            else:
                warnings.append(
                    AdapterWarningRecord(
                        code=ERROR_QUOTE_MISSING_BID,
                        message=message,
                        field="bid",
                    )
                )
        if ask is None or ask <= 0:
            message = "Quote ask is missing or zero."
            if self._policy.strict:
                errors.append(
                    AdapterErrorRecord(
                        code=ERROR_QUOTE_MISSING_ASK,
                        message=message,
                        field="ask",
                    )
                )
            else:
                warnings.append(
                    AdapterWarningRecord(
                        code=ERROR_QUOTE_MISSING_ASK,
                        message=message,
                        field="ask",
                    )
                )
        if bid_value > 0 and ask_value > 0 and ask_value < bid_value:
            inverted = AdapterErrorRecord(
                code=ERROR_QUOTE_INVERTED_MARKET,
                message="Quote ask is below bid.",
                field="ask",
            )
            if self._policy.strict:
                errors.append(inverted)
            else:
                warnings.append(
                    AdapterWarningRecord(
                        code=ERROR_QUOTE_INVERTED_MARKET,
                        message=inverted.message,
                        field="ask",
                    )
                )

        oi_day_high, oi_high_warnings = _non_negative_int(raw.get("oi_day_high"), default=0)
        oi_day_low, oi_low_warnings = _non_negative_int(raw.get("oi_day_low"), default=0)
        warnings.extend(oi_high_warnings)
        warnings.extend(oi_low_warnings)

        if errors or ltp is None:
            return NormalizationResult(valid=False, value=None, errors=tuple(errors), warnings=tuple(warnings))

        return NormalizationResult(
            valid=True,
            value=NormalizedQuote(
                ltp=ltp,
                bid=bid_value,
                ask=ask_value,
                volume=volume,
                open_interest=open_interest,
                quote_timestamp=quote_timestamp,
                last_quantity=_safe_int(raw.get("last_quantity"), default=0) or 0,
                average_price=_safe_float(raw.get("average_price")),
                buy_quantity=_safe_int(raw.get("buy_quantity"), default=0) or 0,
                sell_quantity=_safe_int(raw.get("sell_quantity"), default=0) or 0,
                oi_day_high=oi_day_high,
                oi_day_low=oi_day_low,
            ),
            errors=(),
            warnings=tuple(warnings),
        )

    def normalize_index_quote(
        self,
        raw: Mapping[str, Any] | Any,
        *,
        symbol: str,
        exchange: str,
        quote_key: str,
    ) -> NormalizationResult[UnderlyingSnapshot]:
        """Normalize a Kite index or spot quote into ``UnderlyingSnapshot``."""
        warnings: list[AdapterWarningRecord] = []
        if not isinstance(raw, Mapping):
            return NormalizationResult(
                valid=False,
                value=None,
                errors=(
                    AdapterErrorRecord(
                        code=ERROR_SPOT_INVALID_OBJECT,
                        message="Spot quote payload must be a mapping.",
                    ),
                ),
                warnings=(),
            )

        last_price = _safe_float(raw.get("last_price"))
        if last_price is None or last_price <= 0:
            return NormalizationResult(
                valid=False,
                value=None,
                errors=(
                    AdapterErrorRecord(
                        code=ERROR_SPOT_INVALID_PRICE,
                        message="Spot last_price must be finite and greater than zero.",
                        field="last_price",
                        broker_field="last_price",
                    ),
                ),
                warnings=(),
            )

        ohlc = raw.get("ohlc") if isinstance(raw.get("ohlc"), Mapping) else {}
        open_price = _safe_float(ohlc.get("open"))
        high_price = _safe_float(ohlc.get("high"))
        low_price = _safe_float(ohlc.get("low"))
        previous_close = _safe_float(ohlc.get("close"))

        if not ohlc:
            warnings.append(
                AdapterWarningRecord(
                    code=WARNING_OHLC_MISSING,
                    message="Spot quote missing ohlc block.",
                    field="ohlc",
                )
            )
        if (
            high_price is not None
            and low_price is not None
            and high_price < low_price
        ):
            warnings.append(
                AdapterWarningRecord(
                    code=WARNING_OHLC_INCONSISTENT,
                    message="Spot ohlc high is below low.",
                    field="ohlc",
                )
            )

        change: float | None = None
        change_percent: float | None = None
        if previous_close is not None and previous_close > 0:
            change = last_price - previous_close
            change_percent = (change / previous_close) * 100.0

        quote_timestamp, timestamp_warnings = _normalize_timestamp(raw.get("timestamp"))
        warnings.extend(timestamp_warnings)

        index_volume: int | None = None
        if raw.get("volume") is not None:
            volume_value, volume_warnings = _non_negative_int(raw.get("volume"), default=0)
            warnings.extend(volume_warnings)
            index_volume = volume_value

        return NormalizationResult(
            valid=True,
            value=UnderlyingSnapshot(
                symbol=symbol,
                exchange=exchange,
                quote_key=quote_key,
                last_price=last_price,
                open=open_price,
                high=high_price,
                low=low_price,
                previous_close=previous_close,
                change=change,
                change_percent=change_percent,
                quote_timestamp=quote_timestamp,
                volume=index_volume,
            ),
            errors=(),
            warnings=tuple(warnings),
        )

    def normalize_vix_quote(
        self,
        raw: Mapping[str, Any] | Any,
        *,
        quote_key: str = "NSE:INDIA VIX",
    ) -> NormalizationResult[VolatilitySnapshot]:
        """Normalize a Kite India VIX quote."""
        if raw is None:
            return NormalizationResult(valid=False, value=None, errors=(), warnings=())
        if not isinstance(raw, Mapping):
            return NormalizationResult(
                valid=False,
                value=None,
                errors=(
                    AdapterErrorRecord(
                        code=ERROR_VIX_INVALID_PRICE,
                        message="VIX quote payload must be a mapping.",
                    ),
                ),
                warnings=(),
            )

        last_price = _safe_float(raw.get("last_price"))
        if last_price is None or last_price <= 0:
            return NormalizationResult(
                valid=False,
                value=None,
                errors=(
                    AdapterErrorRecord(
                        code=ERROR_VIX_INVALID_PRICE,
                        message="VIX last_price must be finite and greater than zero.",
                        field="last_price",
                    ),
                ),
                warnings=(),
            )

        quote_timestamp, _ = _normalize_timestamp(raw.get("timestamp"))
        return NormalizationResult(
            valid=True,
            value=VolatilitySnapshot(
                symbol="INDIA VIX",
                exchange="NSE",
                quote_key=quote_key,
                last_price=last_price,
                quote_timestamp=quote_timestamp,
            ),
            errors=(),
            warnings=(),
        )

    def normalize_greeks(self, raw: Mapping[str, Any] | Any | None) -> NormalizedGreeks:
        """Normalize optional Greeks; never raises for bad input."""
        if raw is None or not isinstance(raw, Mapping):
            return NormalizedGreeks()

        def _finite_greek(key: str) -> float | None:
            value = _safe_float(raw.get(key))
            return value if value is not None and math.isfinite(value) else None

        return NormalizedGreeks(
            delta=_finite_greek("delta"),
            iv=_finite_greek("iv"),
            gamma=_finite_greek("gamma"),
            theta=_finite_greek("theta"),
            vega=_finite_greek("vega"),
        )

    def build_contract(
        self,
        instrument: Mapping[str, Any] | NormalizedInstrument,
        quote: Mapping[str, Any] | NormalizedQuote,
        greeks: Mapping[str, Any] | NormalizedGreeks | None = None,
    ) -> NormalizationResult[OptionContractSnapshot]:
        """Merge instrument, quote, and optional Greeks into one contract snapshot."""
        if isinstance(instrument, NormalizedInstrument):
            normalized_instrument = instrument
            instrument_errors: tuple[AdapterErrorRecord, ...] = ()
            instrument_warnings: tuple[AdapterWarningRecord, ...] = ()
        else:
            instrument_result = self.normalize_instrument(instrument)
            if not instrument_result.valid or instrument_result.value is None:
                return NormalizationResult(
                    valid=False,
                    value=None,
                    errors=instrument_result.errors,
                    warnings=instrument_result.warnings,
                )
            normalized_instrument = instrument_result.value
            instrument_errors = instrument_result.errors
            instrument_warnings = instrument_result.warnings

        if isinstance(quote, NormalizedQuote):
            normalized_quote = quote
            quote_errors: tuple[AdapterErrorRecord, ...] = ()
            quote_warnings: tuple[AdapterWarningRecord, ...] = ()
        else:
            quote_result = self.normalize_quote(quote)
            if not quote_result.valid or quote_result.value is None:
                return NormalizationResult(
                    valid=False,
                    value=None,
                    errors=instrument_errors + quote_result.errors,
                    warnings=instrument_warnings + quote_result.warnings,
                )
            normalized_quote = quote_result.value
            quote_errors = quote_result.errors
            quote_warnings = quote_result.warnings

        normalized_greeks = (
            greeks if isinstance(greeks, NormalizedGreeks) else self.normalize_greeks(greeks)
        )

        contract = OptionContractSnapshot(
            underlying=normalized_instrument.underlying,
            exchange=normalized_instrument.exchange,
            tradingsymbol=normalized_instrument.tradingsymbol,
            expiry=normalized_instrument.expiry,
            strike=normalized_instrument.strike,
            option_type=normalized_instrument.option_type,
            lot_size=normalized_instrument.lot_size,
            ltp=normalized_quote.ltp,
            bid=normalized_quote.bid,
            ask=normalized_quote.ask,
            volume=normalized_quote.volume,
            open_interest=normalized_quote.open_interest,
            delta=normalized_greeks.delta,
            iv=normalized_greeks.iv,
            gamma=normalized_greeks.gamma,
            theta=normalized_greeks.theta,
            vega=normalized_greeks.vega,
            instrument_token=normalized_instrument.instrument_token,
            exchange_token=normalized_instrument.exchange_token,
            tick_size=normalized_instrument.tick_size,
            quote_timestamp=normalized_quote.quote_timestamp,
            last_quantity=normalized_quote.last_quantity,
            average_price=normalized_quote.average_price,
            buy_quantity=normalized_quote.buy_quantity,
            sell_quantity=normalized_quote.sell_quantity,
            oi_day_high=normalized_quote.oi_day_high,
            oi_day_low=normalized_quote.oi_day_low,
        )
        return NormalizationResult(
            valid=True,
            value=contract,
            errors=instrument_errors + quote_errors,
            warnings=instrument_warnings + quote_warnings,
        )

    def build_option_chain(
        self,
        instruments: Sequence[Mapping[str, Any]] | Any,
        quotes: Mapping[str, Mapping[str, Any]],
        underlying: str,
        *,
        expiry: str | date | datetime | None = None,
        exchange: str | None = None,
        greeks_map: Mapping[str, Any] | None = None,
        option_types: Sequence[OptionType | str] | None = None,
    ) -> OptionChainBuildResult:
        """Build a filtered, sorted option chain from Kite instruments and quotes."""
        underlying_text = _normalize_text(underlying)
        normalized_expiry = _normalize_expiry(expiry) if expiry is not None else None
        normalized_exchange = _normalize_text(exchange) if exchange is not None else None

        if option_types is None:
            requested_option_types = {OptionType.CE, OptionType.PE}
        else:
            requested_option_types = set()
            for item in option_types:
                if isinstance(item, OptionType):
                    requested_option_types.add(item)
                else:
                    try:
                        requested_option_types.add(OptionType(_normalize_text(item)))
                    except ValueError:
                        continue

        rejections: list[AdapterRejectionRecord] = []
        contracts: list[OptionContractSnapshot] = []
        matched_instruments = 0
        seen_quote_keys: set[str] = set()

        if instruments is None:
            return OptionChainBuildResult(
                contracts=(),
                rejections=(),
                underlying=underlying_text,
                expiry=normalized_expiry,
                exchange=normalized_exchange,
                instrument_count=0,
                matched_instruments=0,
                normalized_count=0,
                rejected_count=0,
            )

        try:
            instrument_list = list(instruments)
        except TypeError:
            return OptionChainBuildResult(
                contracts=(),
                rejections=(),
                underlying=underlying_text,
                expiry=normalized_expiry,
                exchange=normalized_exchange,
                instrument_count=0,
                matched_instruments=0,
                normalized_count=0,
                rejected_count=0,
            )

        for raw_instrument in instrument_list:
            instrument_result = self.normalize_instrument(raw_instrument)
            if not instrument_result.valid or instrument_result.value is None:
                rejections.append(
                    AdapterRejectionRecord(
                        tradingsymbol=(
                            str(raw_instrument.get("tradingsymbol"))
                            if isinstance(raw_instrument, Mapping)
                            else None
                        ),
                        reason=AdapterRejectionReason.INVALID_INSTRUMENT,
                        errors=instrument_result.errors,
                    )
                )
                continue

            instrument = instrument_result.value
            if instrument.underlying != underlying_text:
                continue
            if instrument.option_type not in requested_option_types:
                continue
            if normalized_expiry is not None and instrument.expiry != normalized_expiry:
                continue
            if normalized_exchange is not None and instrument.exchange != normalized_exchange:
                continue

            matched_instruments += 1
            if instrument.quote_key in seen_quote_keys:
                rejections.append(
                    AdapterRejectionRecord(
                        tradingsymbol=instrument.tradingsymbol,
                        reason=AdapterRejectionReason.DUPLICATE_INSTRUMENT,
                        errors=(
                            AdapterErrorRecord(
                                code=ERROR_INSTRUMENT_DUPLICATE_QUOTE_KEY,
                                message="Duplicate quote key encountered.",
                                field="quote_key",
                            ),
                        ),
                    )
                )
                continue
            seen_quote_keys.add(instrument.quote_key)

            quote = quotes.get(instrument.quote_key)
            if quote is None:
                rejections.append(
                    AdapterRejectionRecord(
                        tradingsymbol=instrument.tradingsymbol,
                        reason=AdapterRejectionReason.QUOTE_NOT_FOUND,
                        errors=(
                            AdapterErrorRecord(
                                code=ERROR_QUOTE_NOT_FOUND,
                                message="Quote not found for instrument.",
                                field="quote_key",
                            ),
                        ),
                    )
                )
                continue

            greeks = _find_greeks(
                greeks_map,
                quote_key=instrument.quote_key,
                tradingsymbol=instrument.tradingsymbol,
                instrument_token=instrument.instrument_token,
            )
            build_result = self.build_contract(instrument, quote, greeks)
            if not build_result.valid or build_result.value is None:
                rejections.append(
                    AdapterRejectionRecord(
                        tradingsymbol=instrument.tradingsymbol,
                        reason=AdapterRejectionReason.CONTRACT_BUILD_FAILED,
                        errors=build_result.errors,
                    )
                )
                continue

            contracts.append(build_result.value)

        contracts.sort(
            key=lambda item: (item.expiry, item.strike, item.option_type.value, item.tradingsymbol)
        )
        return OptionChainBuildResult(
            contracts=tuple(contracts),
            rejections=tuple(rejections),
            underlying=underlying_text,
            expiry=normalized_expiry,
            exchange=normalized_exchange,
            instrument_count=len(instrument_list),
            matched_instruments=matched_instruments,
            normalized_count=len(contracts),
            rejected_count=len(rejections),
        )

    def get_available_expiries(
        self,
        instruments: Sequence[Mapping[str, Any]],
        underlying: str,
        *,
        exchange: str | None = None,
        include_expired: bool = False,
        reference_date: date | None = None,
    ) -> tuple[str, ...]:
        """Return sorted ISO expiry dates for an underlying."""
        underlying_text = _normalize_text(underlying)
        exchange_text = _normalize_text(exchange) if exchange is not None else None
        ref = reference_date or date.today()
        expiries: set[str] = set()

        for raw in instruments:
            result = self.normalize_instrument(raw)
            if not result.valid or result.value is None:
                continue
            instrument = result.value
            if instrument.underlying != underlying_text:
                continue
            if exchange_text is not None and instrument.exchange != exchange_text:
                continue
            try:
                expiry_date = date.fromisoformat(instrument.expiry)
            except ValueError:
                continue
            if not include_expired and expiry_date < ref:
                continue
            expiries.add(instrument.expiry)
        return tuple(sorted(expiries))

    def get_nearest_expiry(
        self,
        instruments: Sequence[Mapping[str, Any]],
        underlying: str,
        *,
        exchange: str | None = None,
        reference_date: date | None = None,
    ) -> str | None:
        """Return the nearest non-expired expiry."""
        expiries = self.get_available_expiries(
            instruments,
            underlying,
            exchange=exchange,
            include_expired=False,
            reference_date=reference_date,
        )
        return expiries[0] if expiries else None

    def get_available_strikes(
        self,
        instruments: Sequence[Mapping[str, Any]],
        underlying: str,
        expiry: str | date | datetime,
        *,
        exchange: str | None = None,
    ) -> tuple[float, ...]:
        """Return sorted strikes for an underlying and expiry."""
        underlying_text = _normalize_text(underlying)
        expiry_text = _normalize_expiry(expiry)
        exchange_text = _normalize_text(exchange) if exchange is not None else None
        strikes: set[float] = set()

        for raw in instruments:
            result = self.normalize_instrument(raw)
            if not result.valid or result.value is None:
                continue
            instrument = result.value
            if instrument.underlying != underlying_text:
                continue
            if expiry_text is not None and instrument.expiry != expiry_text:
                continue
            if exchange_text is not None and instrument.exchange != exchange_text:
                continue
            strikes.add(instrument.strike)
        return tuple(sorted(strikes))

    def get_atm_strike(
        self,
        instruments: Sequence[Mapping[str, Any]],
        underlying: str,
        expiry: str | date | datetime,
        spot_price: float,
        *,
        exchange: str | None = None,
    ) -> float | None:
        """Return the strike nearest spot."""
        spot = _safe_float(spot_price)
        if spot is None or spot <= 0:
            return None
        strikes = self.get_available_strikes(
            instruments,
            underlying,
            expiry,
            exchange=exchange,
        )
        if not strikes:
            return None
        return min(strikes, key=lambda strike: (abs(strike - spot), strike))

    def detect_strike_step(self, strikes: Sequence[float]) -> float | None:
        """Return the minimum positive strike increment."""
        try:
            clean = sorted({float(strike) for strike in strikes if float(strike) > 0})
        except (TypeError, ValueError):
            return None
        if len(clean) < 2:
            return None
        differences = [
            clean[index + 1] - clean[index]
            for index in range(len(clean) - 1)
            if clean[index + 1] > clean[index]
        ]
        return min(differences) if differences else None

    def get_nearby_strikes(
        self,
        strikes: Sequence[float],
        spot_price: float,
        strikes_each_side: int | None = None,
    ) -> tuple[float, ...]:
        """Return strikes within a window around spot."""
        spot = _safe_float(spot_price)
        window = (
            strikes_each_side
            if strikes_each_side is not None
            else self._policy.strikes_each_side
        )
        if spot is None or spot <= 0 or window < 0:
            return ()
        try:
            clean = sorted({float(strike) for strike in strikes if float(strike) > 0})
        except (TypeError, ValueError):
            return ()
        if not clean:
            return ()
        atm_index = min(
            range(len(clean)),
            key=lambda index: (abs(clean[index] - spot), clean[index]),
        )
        start = max(0, atm_index - window)
        end = min(len(clean), atm_index + window + 1)
        return tuple(clean[start:end])

    def build_market_snapshot_from_kite(
        self,
        *,
        kite_instruments: Sequence[Mapping[str, Any]],
        kite_quotes: Mapping[str, Mapping[str, Any]],
        kite_spot_quote: Mapping[str, Any],
        request: AdapterBuildRequest,
        kite_vix_quote: Mapping[str, Any] | None = None,
        greeks_map: Mapping[str, Any] | None = None,
        spot_symbol: str | None = None,
        spot_exchange: str | None = None,
        spot_quote_key: str | None = None,
    ) -> AdapterBuildResult:
        """Run the full Kite normalization pipeline and return a ``MarketSnapshot``."""
        validation_errors = self._validate_build_request(request, kite_instruments, kite_quotes)
        if validation_errors:
            return self._blocked_result(
                reason="INVALID_ADAPTER_REQUEST",
                validation_errors=validation_errors,
            )

        underlying_text = _normalize_text(request.underlying)
        spot_defaults = _DEFAULT_SPOT_SYMBOLS.get(
            underlying_text,
            (f"{underlying_text} SPOT", "NSE", f"NSE:{underlying_text}"),
        )
        resolved_spot_symbol = spot_symbol or spot_defaults[0]
        resolved_spot_exchange = spot_exchange or spot_defaults[1]
        resolved_spot_quote_key = spot_quote_key or spot_defaults[2]

        spot_result = self.normalize_index_quote(
            kite_spot_quote,
            symbol=resolved_spot_symbol,
            exchange=resolved_spot_exchange,
            quote_key=resolved_spot_quote_key,
        )
        if not spot_result.valid or spot_result.value is None:
            return self._blocked_result(
                reason="INVALID_SPOT_QUOTE",
                validation_errors=spot_result.errors,
            )

        volatility: VolatilitySnapshot | None = None
        if kite_vix_quote is not None:
            vix_result = self.normalize_vix_quote(kite_vix_quote)
            if vix_result.valid:
                volatility = vix_result.value

        exchange = _normalize_text(request.exchange) if request.exchange else "NFO"
        resolved_expiry = request.expiry or self.get_nearest_expiry(
            kite_instruments,
            underlying_text,
            exchange=exchange,
            reference_date=request.reference_date,
        )
        if resolved_expiry is None:
            return self._blocked_result(
                reason="NO_VALID_EXPIRY",
                validation_errors=(
                    AdapterErrorRecord(
                        code=ERROR_CHAIN_NO_VALID_CONTRACTS,
                        message="No valid expiry found for underlying.",
                    ),
                ),
            )

        strikes_each_side = (
            request.strikes_each_side
            if request.strikes_each_side is not None
            else self._policy.strikes_each_side
        )
        available_strikes = self.get_available_strikes(
            kite_instruments,
            underlying_text,
            resolved_expiry,
            exchange=exchange,
        )
        nearby_strikes = set(
            self.get_nearby_strikes(
                available_strikes,
                spot_result.value.last_price,
                strikes_each_side=strikes_each_side,
            )
        )

        filtered_instruments = [
            raw
            for raw in kite_instruments
            if isinstance(raw, Mapping)
            and _normalize_expiry(raw.get("expiry")) == resolved_expiry
            and _normalize_text(raw.get("name")) == underlying_text
            and (
                request.exchange is None
                or _normalize_text(raw.get("exchange")) == exchange
            )
            and _safe_float(raw.get("strike")) in nearby_strikes
        ]

        option_types = request.option_types or (OptionType.CE, OptionType.PE)
        chain_result = self.build_option_chain(
            filtered_instruments,
            kite_quotes,
            underlying_text,
            expiry=resolved_expiry,
            exchange=exchange,
            greeks_map=greeks_map,
            option_types=option_types,
        )

        if chain_result.normalized_count < self._policy.minimum_contracts:
            return AdapterBuildResult(
                permission=AdapterPermission.BLOCK,
                adapter_allowed=False,
                reason="NO_VALID_OPTION_CONTRACTS",
                snapshot=None,
                validation_errors=(
                    AdapterErrorRecord(
                        code=ERROR_CHAIN_BELOW_MINIMUM,
                        message="Normalized contract count below minimum.",
                    ),
                ),
                rejections=chain_result.rejections,
                instrument_count=chain_result.instrument_count,
                matched_instruments=chain_result.matched_instruments,
                normalized_count=chain_result.normalized_count,
                rejected_count=chain_result.rejected_count,
                broker_order_allowed=False,
            )

        atm_strike = self.get_atm_strike(
            kite_instruments,
            underlying_text,
            resolved_expiry,
            spot_result.value.last_price,
            exchange=exchange,
        )
        strike_step = self.detect_strike_step(available_strikes) or 50.0
        if atm_strike is None:
            atm_strike = chain_result.contracts[0].strike

        contract_strikes = [contract.strike for contract in chain_result.contracts]
        lot_size = chain_result.contracts[0].lot_size
        captured_at = request.captured_at or request.as_of

        try:
            snapshot = build_market_snapshot(
                underlying=spot_result.value,
                contracts=chain_result.contracts,
                underlying_symbol=underlying_text,
                exchange=exchange,
                expiry=resolved_expiry,
                atm_strike=atm_strike,
                strike_step=strike_step,
                strike_window_strikes=strikes_each_side,
                minimum_strike=min(contract_strikes),
                maximum_strike=max(contract_strikes),
                lot_size=lot_size,
                as_of=request.as_of,
                captured_at=captured_at,
                source=request.source,
                adapter_name="market_data_adapter",
                adapter_version=MARKET_DATA_ADAPTER_VERSION,
                correlation_id=request.correlation_id,
                volatility=volatility,
                reference_time=captured_at,
                strict=self._policy.strict,
            )
        except SnapshotBuildError as exc:
            return self._blocked_result(
                reason="SNAPSHOT_BUILD_FAILED",
                validation_errors=(
                    AdapterErrorRecord(
                        code=ERROR_CHAIN_NO_VALID_CONTRACTS,
                        message=str(exc),
                    ),
                ),
                rejections=chain_result.rejections,
                instrument_count=chain_result.instrument_count,
                matched_instruments=chain_result.matched_instruments,
                normalized_count=chain_result.normalized_count,
                rejected_count=chain_result.rejected_count,
            )

        permission = (
            AdapterPermission.ALLOW
            if not chain_result.rejections
            else AdapterPermission.PARTIAL
        )
        return AdapterBuildResult(
            permission=permission,
            adapter_allowed=True,
            reason="OPTION_CHAIN_NORMALIZED",
            snapshot=snapshot,
            validation_errors=(),
            rejections=chain_result.rejections,
            instrument_count=chain_result.instrument_count,
            matched_instruments=chain_result.matched_instruments,
            normalized_count=chain_result.normalized_count,
            rejected_count=chain_result.rejected_count,
            broker_order_allowed=False,
        )

    def _validate_build_request(
        self,
        request: AdapterBuildRequest,
        instruments: Sequence[Mapping[str, Any]] | Any,
        quotes: Mapping[str, Mapping[str, Any]] | Any,
    ) -> tuple[AdapterErrorRecord, ...]:
        errors: list[AdapterErrorRecord] = []
        if not _normalize_text(request.underlying):
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_REQUEST_UNDERLYING_REQUIRED,
                    message="Underlying is required.",
                    field="underlying",
                )
            )
        if request.exchange is not None and _normalize_text(request.exchange) not in VALID_DERIVATIVE_EXCHANGES:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_REQUEST_INVALID_EXCHANGE,
                    message="Exchange is not supported.",
                    field="exchange",
                )
            )
        if not _is_timezone_aware(request.as_of):
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_REQUEST_INVALID_AS_OF,
                    message="as_of must be timezone-aware.",
                    field="as_of",
                )
            )
        if instruments is None:
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_REQUEST_INSTRUMENTS_REQUIRED,
                    message="Instrument collection is required.",
                    field="instruments",
                )
            )
        if not isinstance(quotes, Mapping):
            errors.append(
                AdapterErrorRecord(
                    code=ERROR_REQUEST_QUOTES_INVALID,
                    message="Quotes must be a mapping.",
                    field="quotes",
                )
            )
        if request.option_types is not None:
            invalid = [
                item for item in request.option_types if item not in VALID_OPTION_TYPES
            ]
            if invalid:
                errors.append(
                    AdapterErrorRecord(
                        code=ERROR_REQUEST_INVALID_OPTION_TYPES,
                        message="Invalid option type filter.",
                        field="option_types",
                    )
                )
        return tuple(errors)

    def _blocked_result(
        self,
        *,
        reason: str,
        validation_errors: Sequence[AdapterErrorRecord],
        rejections: Sequence[AdapterRejectionRecord] = (),
        instrument_count: int = 0,
        matched_instruments: int = 0,
        normalized_count: int = 0,
        rejected_count: int = 0,
    ) -> AdapterBuildResult:
        return AdapterBuildResult(
            permission=AdapterPermission.BLOCK,
            adapter_allowed=False,
            reason=reason,
            snapshot=None,
            validation_errors=tuple(validation_errors),
            rejections=tuple(rejections),
            instrument_count=instrument_count,
            matched_instruments=matched_instruments,
            normalized_count=normalized_count,
            rejected_count=rejected_count,
            broker_order_allowed=False,
        )


__all__ = [
    "MARKET_DATA_ADAPTER_VERSION",
    "SUPPORTED_BROKER",
    "VALID_DERIVATIVE_EXCHANGES",
    "VALID_SPOT_EXCHANGES",
    "VALID_OPTION_TYPES",
    "AdapterPermission",
    "AdapterRejectionReason",
    "BrokerFormat",
    "AdapterConfigurationError",
    "AdapterInputError",
    "AdapterErrorRecord",
    "AdapterWarningRecord",
    "AdapterRejectionRecord",
    "NormalizedInstrument",
    "NormalizedQuote",
    "NormalizedGreeks",
    "NormalizationResult",
    "AdapterPolicy",
    "AdapterBuildRequest",
    "OptionChainBuildResult",
    "AdapterBuildResult",
    "MarketDataAdapter",
]
