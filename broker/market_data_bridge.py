"""WebSocket-to-market-data-pipeline tick forwarding bridge for THETA AI TRADER.

Wires :class:`broker.kite_websocket.KiteWebSocketClient`'s live tick stream
into :class:`broker.market_data_streaming.MarketDataStreamingEngine`'s
ingestion contract. Pure glue: this module never assembles snapshots or
owns the WebSocket connection or the streaming engine's lifecycle, and it
never invents a pricing model. It converts each raw broker tick into a
normalized ``TickEvent`` (using already-registered instrument descriptors),
and — for option ticks — delegates to the existing, unmodified
:class:`option_greeks_engine.OptionGreeksEngine` to attach Delta/Gamma/
Theta/Vega/IV before the tick is forwarded (Rule GRK-001 in
``market_data_streaming``: that module never computes Greeks itself).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from broker.kite_websocket import KiteWebSocketClient, SubscriptionInstrument
from broker.market_data_streaming import (
    GreeksAttachment,
    InstrumentDescriptor,
    InstrumentRole,
    MarketDataStreamingEngine,
    MarketDataStreamingError,
    TickEvent,
    resolve_instrument_role,
)
from option_greeks_engine import OptionGreeksEngine

MARKET_DATA_BRIDGE_VERSION: Final[str] = "1.0.0"

_OPTION_ROLES: Final[frozenset[InstrumentRole]] = frozenset(
    {InstrumentRole.OPTION_CE, InstrumentRole.OPTION_PE}
)
_GREEKS_SOURCE: Final[str] = "option_greeks_engine"

_LOGGER = logging.getLogger("broker.market_data_bridge")


def _utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class BridgeStatistics:
    """Tick-forwarding counters for observability."""

    ticks_received: int
    ticks_forwarded: int
    ticks_dropped_unmapped: int
    ticks_dropped_error: int
    ticks_with_greeks: int
    ticks_greeks_unavailable: int
    last_forwarded_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None


def _to_subscription_instrument(descriptor: InstrumentDescriptor) -> SubscriptionInstrument:
    """Project a market-data ``InstrumentDescriptor`` onto a WebSocket ``SubscriptionInstrument``.

    Pure field mapping — carries only the fields the WebSocket subscription
    layer needs; option/Greeks metadata stays with the streaming engine.
    """
    return SubscriptionInstrument(
        instrument_token=descriptor.instrument_token,
        underlying=descriptor.underlying,
        quote_key=descriptor.quote_key,
        exchange=descriptor.exchange,
        tradingsymbol=descriptor.tradingsymbol,
        instrument_kind=descriptor.instrument_kind,
    )


def _extract_best_level(raw: Mapping[str, Any], side: str) -> tuple[float | None, int | None]:
    """Extract best price/quantity from a Kite Connect v3 tick's depth block.

    Args:
        raw: Raw tick payload.
        side: ``"buy"`` or ``"sell"``.

    Returns:
        ``(price, quantity)``; ``(None, None)`` when the depth block is
        absent or malformed.
    """
    try:
        depth = raw.get("depth")
        levels = depth.get(side) if isinstance(depth, Mapping) else None
        if not isinstance(levels, list) or not levels:
            return None, None
        best = levels[0]
        if not isinstance(best, Mapping):
            return None, None
        price = best.get("price")
        quantity = best.get("quantity")
        price_f = float(price) if isinstance(price, (int, float)) and price > 0 else None
        quantity_i = int(quantity) if isinstance(quantity, (int, float)) else None
        return price_f, quantity_i
    except (TypeError, ValueError, AttributeError):
        return None, None


def build_tick_event(
    raw: Mapping[str, Any],
    descriptor: InstrumentDescriptor,
    *,
    received_at: datetime,
    sequence: int | None = None,
    greeks: GreeksAttachment | None = None,
) -> TickEvent:
    """Build a normalized ``TickEvent`` from one raw broker tick and its descriptor.

    Pure mapping — reads only already-present broker fields (Kite Connect
    v3 tick shape: ``last_price``, ``volume``, ``oi``, ``ohlc``, ``depth``,
    ``average_price``, ``exchange_timestamp``) and already-registered
    descriptor metadata; never computes prices or OI. ``greeks`` (when
    provided by the caller) is attached as-is — this function does not
    compute Greeks/IV itself; see
    :meth:`WebSocketMarketDataBridge._compute_greeks`.

    Args:
        raw: One raw tick dict from ``KiteWebSocketClient``'s tick callback.
        descriptor: Previously registered instrument descriptor for this token.
        received_at: Local receive timestamp (must be timezone-aware).
        sequence: Optional monotonic per-token sequence number.
        greeks: Optional pre-computed Greeks/IV attachment for option ticks.

    Returns:
        Normalized tick event ready for
        ``MarketDataStreamingEngine.ingest_tick``.
    """
    ohlc = raw.get("ohlc") if isinstance(raw.get("ohlc"), Mapping) else {}
    bid, bid_quantity = _extract_best_level(raw, "buy")
    ask, ask_quantity = _extract_best_level(raw, "sell")

    exchange_ts = raw.get("exchange_timestamp")
    if not isinstance(exchange_ts, datetime):
        raw_timestamp = raw.get("timestamp")
        exchange_ts = raw_timestamp if isinstance(raw_timestamp, datetime) else None

    last_price = raw.get("last_price")
    volume = raw.get("volume")
    open_interest = raw.get("oi")
    average_price = raw.get("average_price")

    def _num(value: object) -> float | None:
        return float(value) if isinstance(value, (int, float)) else None

    return TickEvent(
        instrument_token=descriptor.instrument_token,
        underlying=descriptor.underlying,
        quote_key=descriptor.quote_key,
        exchange=descriptor.exchange,
        tradingsymbol=descriptor.tradingsymbol,
        instrument_kind=descriptor.instrument_kind,
        last_price=_num(last_price) or 0.0,
        volume=int(volume) if isinstance(volume, (int, float)) else 0,
        received_at=received_at,
        bid=bid,
        ask=ask,
        bid_quantity=bid_quantity,
        ask_quantity=ask_quantity,
        open_interest=int(open_interest) if isinstance(open_interest, (int, float)) else None,
        open=_num(ohlc.get("open")),
        high=_num(ohlc.get("high")),
        low=_num(ohlc.get("low")),
        close=_num(ohlc.get("close")),
        average_price=_num(average_price),
        exchange_timestamp=exchange_ts,
        sequence=sequence,
        greeks=greeks,
    )


class WebSocketMarketDataBridge:
    """Forwards live ``KiteWebSocketClient`` ticks into a ``MarketDataStreamingEngine``.

    Pure orchestration glue between three already-implemented, independent
    components — never opens a WebSocket connection, never assembles
    snapshots, and never invents a pricing model itself. Delegates all
    option pricing/Greeks/IV math to the existing, unmodified
    :class:`option_greeks_engine.OptionGreeksEngine`. Thread-safe: the tick
    callback runs on the WebSocket client's own callback thread and never
    raises back into it.
    """

    def __init__(
        self,
        *,
        ws_client: KiteWebSocketClient,
        streaming_engine: MarketDataStreamingEngine,
        greeks_engine: OptionGreeksEngine | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Construct the bridge.

        Args:
            ws_client: Live (or fake, for tests) WebSocket client.
            streaming_engine: Target market-data streaming engine.
            greeks_engine: Existing Greeks/IV engine used to enrich option
                ticks; defaults to a fresh :class:`OptionGreeksEngine`
                (same defaults the rest of the codebase uses).
            clock: Injectable clock for deterministic tests.
        """
        self._ws_client = ws_client
        self._streaming_engine = streaming_engine
        self._greeks_engine = greeks_engine or OptionGreeksEngine()
        self._clock = clock or _utc_now
        self._lock = threading.RLock()
        self._descriptors: dict[int, InstrumentDescriptor] = {}
        self._sequence_by_token: dict[int, int] = {}
        self._spot_price_by_underlying: dict[str, float] = {}
        self._ticks_received = 0
        self._ticks_forwarded = 0
        self._ticks_dropped_unmapped = 0
        self._ticks_dropped_error = 0
        self._ticks_with_greeks = 0
        self._ticks_greeks_unavailable = 0
        self._last_forwarded_at: datetime | None = None
        self._last_error_code: str | None = None
        self._last_error_message: str | None = None
        self._started = False

    @property
    def is_started(self) -> bool:
        """Return whether this bridge is currently registered as the tick handler."""
        with self._lock:
            return self._started

    def register_instruments(self, descriptors: Sequence[InstrumentDescriptor]) -> None:
        """Register instruments on both the WebSocket client and the streaming engine.

        Replaces the desired instrument set on both sides (matching each
        engine's own ``set_instruments()`` / ``register_instruments()``
        replace semantics) and refreshes this bridge's token -> descriptor
        enrichment map used to build ``TickEvent`` objects.

        Args:
            descriptors: Externally resolved instrument descriptors —
                the single source of truth for both the subscription side
                and the assembly side.
        """
        resolved = tuple(descriptors)
        with self._lock:
            self._descriptors = {d.instrument_token: d for d in resolved}
        self._ws_client.set_instruments(tuple(_to_subscription_instrument(d) for d in resolved))
        self._streaming_engine.register_instruments(resolved)

    def start(self) -> None:
        """Register this bridge as the WebSocket client's tick handler.

        Idempotent. Does not connect the WebSocket or start the streaming
        engine — those remain independently owned lifecycles; call
        ``ws_client.connect()`` / ``streaming_engine.start()`` separately.
        """
        self._ws_client.set_tick_handler(self._on_tick)
        with self._lock:
            self._started = True

    def stop(self) -> None:
        """Detach this bridge from the WebSocket client's tick handler.

        Idempotent. Does not disconnect the WebSocket or stop the
        streaming engine.
        """
        self._ws_client.set_tick_handler(None)
        with self._lock:
            self._started = False

    def _on_tick(self, raw: Mapping[str, Any]) -> None:
        """Convert and forward one raw tick. Never raises back into the caller."""
        received_at = self._clock()
        with self._lock:
            self._ticks_received += 1

        token_raw = raw.get("instrument_token")
        try:
            token = int(token_raw) if token_raw is not None else -1
        except (TypeError, ValueError):
            token = -1

        with self._lock:
            descriptor = self._descriptors.get(token)
        if descriptor is None:
            with self._lock:
                self._ticks_dropped_unmapped += 1
            return

        with self._lock:
            sequence = self._sequence_by_token.get(token, 0) + 1
            self._sequence_by_token[token] = sequence

        self._update_spot_cache(descriptor, raw)
        greeks = self._compute_greeks(descriptor, raw, received_at=received_at)
        with self._lock:
            if greeks is not None:
                self._ticks_with_greeks += 1
            elif (descriptor.instrument_role or resolve_instrument_role(descriptor.instrument_kind)) in _OPTION_ROLES:
                self._ticks_greeks_unavailable += 1

        try:
            tick_event = build_tick_event(
                raw, descriptor, received_at=received_at, sequence=sequence, greeks=greeks
            )
            self._streaming_engine.ingest_tick(tick_event)
        except MarketDataStreamingError as exc:
            with self._lock:
                self._ticks_dropped_error += 1
                self._last_error_code = exc.code
                self._last_error_message = str(exc)
            _LOGGER.warning("market_data_bridge.ingest_failed token=%s: %s", token, exc)
            return
        except Exception as exc:  # noqa: BLE001 - tick callback must never crash
            with self._lock:
                self._ticks_dropped_error += 1
                self._last_error_code = "MDS_BRIDGE.UNEXPECTED"
                self._last_error_message = str(exc)
            _LOGGER.exception("market_data_bridge.unexpected_failure token=%s", token)
            return

        with self._lock:
            self._ticks_forwarded += 1
            self._last_forwarded_at = received_at

    def _update_spot_cache(
        self, descriptor: InstrumentDescriptor, raw: Mapping[str, Any]
    ) -> None:
        """Cache the latest SPOT-role last_price per underlying.

        Greeks computation needs a current spot price for the underlying;
        this is the only source used (never fabricated, never carried over
        from a different underlying).
        """
        role = descriptor.instrument_role or resolve_instrument_role(descriptor.instrument_kind)
        if role is not InstrumentRole.SPOT:
            return
        last_price = raw.get("last_price")
        if isinstance(last_price, (int, float)) and last_price > 0:
            with self._lock:
                self._spot_price_by_underlying[descriptor.underlying] = float(last_price)

    def _compute_greeks(
        self,
        descriptor: InstrumentDescriptor,
        raw: Mapping[str, Any],
        *,
        received_at: datetime,
    ) -> GreeksAttachment | None:
        """Delegate option Greeks/IV computation to the existing ``OptionGreeksEngine``.

        Read-only soft-computation — never raises, never fabricates.
        Returns ``None`` when the tick is not an option, the descriptor is
        missing option metadata, the underlying's latest spot price is not
        yet known, or the engine cannot solve IV/Greeks for the given
        inputs (e.g. no two-sided market yet, or already expired).

        Args:
            descriptor: Registered instrument descriptor for this tick.
            raw: Raw tick payload.
            received_at: Local receive timestamp, used as the engine's
                "as of" clock for time-to-expiry.

        Returns:
            A :class:`GreeksAttachment` on success, else ``None``.
        """
        role = descriptor.instrument_role or resolve_instrument_role(descriptor.instrument_kind)
        if role not in _OPTION_ROLES:
            return None
        if descriptor.strike is None or descriptor.option_type is None or descriptor.expiry is None:
            return None

        with self._lock:
            spot_price = self._spot_price_by_underlying.get(descriptor.underlying)
        if spot_price is None:
            return None

        bid, _ = _extract_best_level(raw, "buy")
        ask, _ = _extract_best_level(raw, "sell")
        last_price = raw.get("last_price")

        contract = {
            "tradingsymbol": descriptor.tradingsymbol,
            "expiry": descriptor.expiry,
            "strike": descriptor.strike,
            "option_type": descriptor.option_type,
            "bid": bid,
            "ask": ask,
            "ltp": last_price if isinstance(last_price, (int, float)) else None,
        }

        try:
            result = self._greeks_engine.enrich_contract(
                contract, spot_price=spot_price, current_time=received_at
            )
        except Exception:  # noqa: BLE001 - Greeks enrichment must not break tick forwarding
            _LOGGER.exception(
                "market_data_bridge.greeks_engine_failed token=%s",
                descriptor.instrument_token,
            )
            return None

        if not isinstance(result, Mapping) or not result.get("valid"):
            return None
        enriched = result.get("contract")
        if not isinstance(enriched, Mapping):
            return None

        return GreeksAttachment(
            delta=enriched.get("delta"),
            gamma=enriched.get("gamma"),
            theta=enriched.get("theta"),
            vega=enriched.get("vega"),
            iv=enriched.get("iv_decimal"),
            computed_at=received_at,
            source=_GREEKS_SOURCE,
        )

    def get_statistics(self) -> BridgeStatistics:
        """Return tick-forwarding counters."""
        with self._lock:
            return BridgeStatistics(
                ticks_received=self._ticks_received,
                ticks_forwarded=self._ticks_forwarded,
                ticks_dropped_unmapped=self._ticks_dropped_unmapped,
                ticks_dropped_error=self._ticks_dropped_error,
                ticks_with_greeks=self._ticks_with_greeks,
                ticks_greeks_unavailable=self._ticks_greeks_unavailable,
                last_forwarded_at=self._last_forwarded_at,
                last_error_code=self._last_error_code,
                last_error_message=self._last_error_message,
            )

    def reset_statistics(self) -> None:
        """Zero all counters without touching registered instruments or handler state."""
        with self._lock:
            self._ticks_received = 0
            self._ticks_forwarded = 0
            self._ticks_dropped_unmapped = 0
            self._ticks_dropped_error = 0
            self._ticks_with_greeks = 0
            self._ticks_greeks_unavailable = 0
            self._last_forwarded_at = None
            self._last_error_code = None
            self._last_error_message = None


__all__ = [
    "MARKET_DATA_BRIDGE_VERSION",
    "BridgeStatistics",
    "build_tick_event",
    "WebSocketMarketDataBridge",
]
