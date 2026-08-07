"""Unit tests for broker.market_data_bridge."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from broker.market_data_bridge import (
    MARKET_DATA_BRIDGE_VERSION,
    BridgeStatistics,
    WebSocketMarketDataBridge,
    build_tick_event,
)
from broker.market_data_streaming import (
    MarketDataStreamingEngine,
    MarketDataStreamingError,
    TickEvent,
)
from tests.test_kite_websocket import FakeKiteTicker, make_client
from tests.test_market_data_streaming import (
    make_config,
    make_engine,
    option_descriptor,
    spot_descriptor,
)

FIXED_NOW = datetime(2026, 8, 7, 5, 0, tzinfo=timezone.utc)


def fixed_clock() -> datetime:
    """Deterministic bridge clock."""
    return FIXED_NOW


def raw_index_tick(
    token: int = 1001,
    *,
    last_price: float = 24512.5,
    volume: int | None = 0,
    ohlc: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build a Kite-Connect-v3-shaped raw index/spot tick."""
    return {
        "instrument_token": token,
        "last_price": last_price,
        "volume": volume,
        "ohlc": ohlc if ohlc is not None else {"open": 24500.0, "high": 24550.0, "low": 24480.0, "close": 24490.0},
    }


def raw_option_tick(
    token: int,
    *,
    last_price: float = 120.0,
    volume: int = 500,
    oi: int = 10000,
    bid: float = 119.5,
    ask: float = 120.5,
    exchange_timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Build a Kite-Connect-v3-shaped raw option tick with depth."""
    tick: dict[str, Any] = {
        "instrument_token": token,
        "last_price": last_price,
        "volume": volume,
        "oi": oi,
        "average_price": last_price,
        "depth": {
            "buy": [{"price": bid, "quantity": 75}],
            "sell": [{"price": ask, "quantity": 75}],
        },
    }
    if exchange_timestamp is not None:
        tick["exchange_timestamp"] = exchange_timestamp
    return tick


def make_bridge(
    *,
    underlyings: tuple[str, ...] = ("NIFTY",),
    ticker: FakeKiteTicker | None = None,
    streaming_engine=None,
    clock=fixed_clock,
) -> tuple[WebSocketMarketDataBridge, Any, Any]:
    """Build a bridge wired to a real KiteWebSocketClient and streaming engine."""
    ws_client, fake = make_client(underlyings=underlyings, clock=clock, ticker=ticker)
    engine = streaming_engine or make_engine(underlyings, clock=clock)
    bridge = WebSocketMarketDataBridge(
        ws_client=ws_client, streaming_engine=engine, clock=clock
    )
    return bridge, ws_client, fake


class TestBuildTickEvent:
    """build_tick_event(): pure raw-tick + descriptor -> TickEvent mapping."""

    def test_maps_core_fields(self) -> None:
        descriptor = spot_descriptor(1001, "NIFTY")
        event = build_tick_event(raw_index_tick(1001), descriptor, received_at=FIXED_NOW)
        assert isinstance(event, TickEvent)
        assert event.instrument_token == 1001
        assert event.underlying == "NIFTY"
        assert event.quote_key == descriptor.quote_key
        assert event.exchange == descriptor.exchange
        assert event.tradingsymbol == descriptor.tradingsymbol
        assert event.last_price == 24512.5
        assert event.open == 24500.0
        assert event.high == 24550.0
        assert event.low == 24480.0
        assert event.close == 24490.0
        assert event.received_at == FIXED_NOW

    def test_bid_ask_from_depth(self) -> None:
        descriptor = option_descriptor(1011, "NIFTY", 24500.0, "CE")
        event = build_tick_event(
            raw_option_tick(1011, bid=119.5, ask=120.5), descriptor, received_at=FIXED_NOW
        )
        assert event.bid == 119.5
        assert event.ask == 120.5
        assert event.bid_quantity == 75
        assert event.ask_quantity == 75
        assert event.open_interest == 10000
        assert event.average_price == 120.0

    def test_missing_depth_yields_none_bid_ask(self) -> None:
        descriptor = spot_descriptor(1001, "NIFTY")
        event = build_tick_event(raw_index_tick(1001), descriptor, received_at=FIXED_NOW)
        assert event.bid is None
        assert event.ask is None
        assert event.bid_quantity is None
        assert event.ask_quantity is None

    def test_missing_ohlc_yields_none_ohlc_fields(self) -> None:
        descriptor = option_descriptor(1011, "NIFTY", 24500.0, "CE")
        event = build_tick_event(
            raw_option_tick(1011), descriptor, received_at=FIXED_NOW
        )
        assert event.open is None
        assert event.high is None
        assert event.low is None
        assert event.close is None

    def test_exchange_timestamp_passthrough_when_datetime(self) -> None:
        ts = FIXED_NOW - timedelta(seconds=1)
        descriptor = option_descriptor(1011, "NIFTY", 24500.0, "CE")
        event = build_tick_event(
            raw_option_tick(1011, exchange_timestamp=ts), descriptor, received_at=FIXED_NOW
        )
        assert event.exchange_timestamp == ts

    def test_exchange_timestamp_falls_back_to_timestamp_field(self) -> None:
        ts = FIXED_NOW - timedelta(seconds=2)
        descriptor = spot_descriptor(1001, "NIFTY")
        raw = raw_index_tick(1001)
        raw["timestamp"] = ts
        event = build_tick_event(raw, descriptor, received_at=FIXED_NOW)
        assert event.exchange_timestamp == ts

    def test_exchange_timestamp_none_when_not_a_datetime(self) -> None:
        descriptor = spot_descriptor(1001, "NIFTY")
        raw = raw_index_tick(1001)
        raw["exchange_timestamp"] = "not-a-datetime"
        event = build_tick_event(raw, descriptor, received_at=FIXED_NOW)
        assert event.exchange_timestamp is None

    def test_missing_last_price_defaults_to_zero_not_fabricated(self) -> None:
        descriptor = spot_descriptor(1001, "NIFTY")
        event = build_tick_event({"instrument_token": 1001}, descriptor, received_at=FIXED_NOW)
        assert event.last_price == 0.0
        assert event.volume == 0
        assert event.open_interest is None

    def test_underlying_quote_key_exchange_from_descriptor_not_raw(self) -> None:
        descriptor = spot_descriptor(1001, "BANKNIFTY")
        raw = raw_index_tick(1001)
        raw["underlying"] = "SOMETHING_ELSE"
        event = build_tick_event(raw, descriptor, received_at=FIXED_NOW)
        assert event.underlying == "BANKNIFTY"
        assert event.quote_key == descriptor.quote_key

    def test_sequence_passthrough(self) -> None:
        descriptor = spot_descriptor(1001, "NIFTY")
        event = build_tick_event(
            raw_index_tick(1001), descriptor, received_at=FIXED_NOW, sequence=7
        )
        assert event.sequence == 7


class TestRegisterInstruments:
    """register_instruments(): forwards to both engines and builds the enrichment map."""

    def test_forwards_to_websocket_client(self) -> None:
        bridge, ws_client, _ = make_bridge()
        descriptors = [spot_descriptor(1001, "NIFTY")]
        bridge.register_instruments(descriptors)
        subscriptions = ws_client.get_subscriptions()
        # Not yet applied (not connected) but desired set should be populated.
        assert any(i.instrument_token == 1001 for i in ws_client._subscriptions.desired())

    def test_forwards_to_streaming_engine(self) -> None:
        bridge, _, _ = make_bridge()
        engine = bridge._streaming_engine
        descriptors = [spot_descriptor(1001, "NIFTY")]
        bridge.register_instruments(descriptors)
        assert engine._quote_book.get_descriptor(1001) is not None

    def test_builds_internal_descriptor_map(self) -> None:
        bridge, _, _ = make_bridge()
        descriptor = spot_descriptor(1001, "NIFTY")
        bridge.register_instruments([descriptor])
        assert bridge._descriptors[1001] is descriptor

    def test_replaces_previous_registration(self) -> None:
        bridge, _, _ = make_bridge()
        bridge.register_instruments([spot_descriptor(1001, "NIFTY")])
        bridge.register_instruments([spot_descriptor(2001, "NIFTY")])
        assert 1001 not in bridge._descriptors
        assert 2001 in bridge._descriptors


class TestStartStop:
    """start()/stop(): tick handler wiring."""

    def test_start_registers_tick_handler(self) -> None:
        bridge, ws_client, _ = make_bridge()
        bridge.start()
        assert ws_client._tick_handler == bridge._on_tick
        assert bridge.is_started is True

    def test_stop_clears_tick_handler(self) -> None:
        bridge, ws_client, _ = make_bridge()
        bridge.start()
        bridge.stop()
        assert ws_client._tick_handler is None
        assert bridge.is_started is False

    def test_stop_safe_when_never_started(self) -> None:
        bridge, _, _ = make_bridge()
        bridge.stop()
        assert bridge.is_started is False


class TestTickForwardingEndToEnd:
    """Full wiring: real KiteWebSocketClient + real MarketDataStreamingEngine."""

    def test_end_to_end_snapshot_assembly(self) -> None:
        bridge, ws_client, fake = make_bridge()
        engine = bridge._streaming_engine
        engine.start()
        descriptors = [
            spot_descriptor(1001, "NIFTY"),
            option_descriptor(1011, "NIFTY", 24500.0, "CE"),
            option_descriptor(1012, "NIFTY", 24500.0, "PE"),
        ]
        bridge.register_instruments(descriptors)
        bridge.start()

        ws_client.connect()
        ws_client.apply_subscriptions()

        fake.emit_ticks(
            [
                raw_index_tick(1001),
                raw_option_tick(1011, last_price=120.0),
                raw_option_tick(1012, last_price=110.0),
            ]
        )

        snapshot = engine.get_snapshot("NIFTY")
        assert snapshot is not None
        assert snapshot.underlying.last_price == 24512.5
        assert len(snapshot.option_chain.contracts) == 2

        stats = bridge.get_statistics()
        assert stats.ticks_received == 3
        assert stats.ticks_forwarded == 3
        assert stats.ticks_dropped_unmapped == 0
        assert stats.ticks_dropped_error == 0
        assert stats.last_forwarded_at == FIXED_NOW

    def test_repeated_ticks_increment_sequence_and_stats(self) -> None:
        bridge, ws_client, fake = make_bridge()
        engine = bridge._streaming_engine
        engine.start()
        bridge.register_instruments([spot_descriptor(1001, "NIFTY")])
        bridge.start()
        ws_client.connect()

        fake.emit_ticks([raw_index_tick(1001, last_price=24500.0)])
        fake.emit_ticks([raw_index_tick(1001, last_price=24510.0)])

        assert bridge.get_statistics().ticks_forwarded == 2
        assert bridge._sequence_by_token[1001] == 2


class TestUnmappedToken:
    """Ticks for tokens with no registered descriptor are dropped, not fabricated."""

    def test_unmapped_token_dropped_and_counted(self) -> None:
        bridge, ws_client, fake = make_bridge()
        engine = bridge._streaming_engine
        engine.start()
        bridge.register_instruments([spot_descriptor(1001, "NIFTY")])
        bridge.start()
        ws_client.connect()

        fake.emit_ticks([raw_index_tick(9999, last_price=1.0)])

        stats = bridge.get_statistics()
        assert stats.ticks_received == 1
        assert stats.ticks_forwarded == 0
        assert stats.ticks_dropped_unmapped == 1
        assert engine.get_snapshot("NIFTY") is None

    def test_no_instrument_token_in_raw_tick_is_dropped(self) -> None:
        bridge, ws_client, fake = make_bridge()
        bridge.register_instruments([spot_descriptor(1001, "NIFTY")])
        bridge.start()
        ws_client.connect()
        fake.emit_ticks([{"last_price": 1.0}])
        assert bridge.get_statistics().ticks_dropped_unmapped == 1


class TestErrorHandling:
    """Ingestion failures are recorded, never raised back into the WebSocket callback."""

    def test_engine_not_running_error_is_recorded(self) -> None:
        # Streaming engine deliberately never started -> ingest_tick raises
        # MarketDataStreamingStateError, which the bridge must catch.
        not_started_engine = MarketDataStreamingEngine(
            make_config(("NIFTY",)), clock=fixed_clock
        )
        bridge, ws_client, fake = make_bridge(streaming_engine=not_started_engine)
        bridge.register_instruments([spot_descriptor(1001, "NIFTY")])
        bridge.start()
        ws_client.connect()

        fake.emit_ticks([raw_index_tick(1001)])  # must not raise

        stats = bridge.get_statistics()
        assert stats.ticks_dropped_error == 1
        assert stats.ticks_forwarded == 0
        assert stats.last_error_code is not None

    def test_unexpected_exception_from_engine_is_recorded(self) -> None:
        class _RaisingEngine:
            def register_instruments(self, descriptors):
                pass

            def ingest_tick(self, tick):
                raise RuntimeError("boom")

        ws_client, fake = make_client(underlyings=("NIFTY",), clock=fixed_clock)
        bridge = WebSocketMarketDataBridge(
            ws_client=ws_client, streaming_engine=_RaisingEngine(), clock=fixed_clock
        )
        bridge.register_instruments([spot_descriptor(1001, "NIFTY")])
        bridge.start()
        ws_client.connect()

        fake.emit_ticks([raw_index_tick(1001)])  # must not raise

        stats = bridge.get_statistics()
        assert stats.ticks_dropped_error == 1
        assert stats.last_error_code == "MDS_BRIDGE.UNEXPECTED"
        assert "boom" in stats.last_error_message

    def test_malformed_instrument_token_is_dropped_not_raised(self) -> None:
        bridge, ws_client, fake = make_bridge()
        bridge.register_instruments([spot_descriptor(1001, "NIFTY")])
        bridge.start()
        ws_client.connect()
        fake.emit_ticks([{"instrument_token": "not-an-int", "last_price": 1.0}])
        assert bridge.get_statistics().ticks_dropped_unmapped == 1


class TestStatistics:
    """get_statistics()/reset_statistics()."""

    def test_initial_statistics_are_zero(self) -> None:
        bridge, _, _ = make_bridge()
        stats = bridge.get_statistics()
        assert isinstance(stats, BridgeStatistics)
        assert stats.ticks_received == 0
        assert stats.ticks_forwarded == 0
        assert stats.last_forwarded_at is None

    def test_reset_statistics(self) -> None:
        bridge, ws_client, fake = make_bridge()
        bridge._streaming_engine.start()
        bridge.register_instruments([spot_descriptor(1001, "NIFTY")])
        bridge.start()
        ws_client.connect()
        fake.emit_ticks([raw_index_tick(1001)])
        assert bridge.get_statistics().ticks_forwarded == 1
        bridge.reset_statistics()
        stats = bridge.get_statistics()
        assert stats.ticks_forwarded == 0
        assert stats.ticks_received == 0


class TestThreadSafety:
    """Concurrent tick delivery and statistics reads stay consistent."""

    def test_concurrent_ticks_and_stat_reads(self) -> None:
        bridge, ws_client, fake = make_bridge()
        bridge._streaming_engine.start()
        bridge.register_instruments(
            [spot_descriptor(1001, "NIFTY"), option_descriptor(1011, "NIFTY", 24500.0, "CE")]
        )
        bridge.start()
        ws_client.connect()

        barrier = threading.Barrier(4)
        errors: list[BaseException] = []

        def emitter(token: int) -> None:
            try:
                barrier.wait()
                for _ in range(25):
                    fake.emit_ticks([raw_index_tick(token, last_price=24500.0)])
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def reader() -> None:
            try:
                barrier.wait()
                for _ in range(50):
                    bridge.get_statistics()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(emitter, 1001),
                pool.submit(emitter, 1011),
                pool.submit(reader),
                pool.submit(reader),
            ]
            for future in futures:
                future.result()

        assert not errors
        assert bridge.get_statistics().ticks_received == 50


class TestModuleMetadata:
    """Module-level version constant."""

    def test_version_defined(self) -> None:
        assert MARKET_DATA_BRIDGE_VERSION == "1.0.0"
