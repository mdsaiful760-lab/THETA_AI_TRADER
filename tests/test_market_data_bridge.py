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
    GreeksAttachment,
    MarketDataStreamingEngine,
    MarketDataStreamingError,
    TickEvent,
)
from option_greeks_engine import OptionGreeksEngine
from tests.test_kite_websocket import FakeKiteTicker, make_client
from tests.test_market_data_streaming import (
    future_descriptor,
    make_config,
    make_engine,
    option_descriptor,
    spot_descriptor,
    vix_descriptor,
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
    engine = streaming_engine or make_engine(underlyings, clock=clock, min_complete_pairs=0)
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


class TestBuildTickEventGreeksPassthrough:
    """build_tick_event(): greeks parameter is attached as-is, never computed."""

    def test_attaches_provided_greeks(self) -> None:
        descriptor = option_descriptor(1011, "NIFTY", 24500.0, "CE")
        attachment = GreeksAttachment(delta=0.5, gamma=0.01, theta=-9.0, vega=12.0, iv=0.15)
        event = build_tick_event(
            raw_option_tick(1011), descriptor, received_at=FIXED_NOW, greeks=attachment
        )
        assert event.greeks is attachment

    def test_defaults_to_none(self) -> None:
        descriptor = option_descriptor(1011, "NIFTY", 24500.0, "CE")
        event = build_tick_event(raw_option_tick(1011), descriptor, received_at=FIXED_NOW)
        assert event.greeks is None


class TestGreeksIntegration:
    """Option ticks are enriched with Delta/Gamma/Theta/Vega/IV via the
    existing option_greeks_engine.OptionGreeksEngine before forwarding."""

    def test_option_tick_gets_greeks_when_spot_known(self) -> None:
        bridge, ws_client, fake = make_bridge()
        bridge._streaming_engine.start()
        bridge.register_instruments(
            [
                spot_descriptor(1001, "NIFTY"),
                option_descriptor(1011, "NIFTY", 24500.0, "CE", expiry="2026-08-13"),
            ]
        )
        bridge.start()
        ws_client.connect()

        fake.emit_ticks([raw_index_tick(1001, last_price=24512.5)])
        fake.emit_ticks([raw_option_tick(1011, last_price=120.0, bid=119.5, ask=120.5)])

        snapshot = bridge._streaming_engine.get_snapshot("NIFTY")
        assert snapshot is not None
        contract = snapshot.option_chain.contracts[0]
        assert contract.delta is not None
        assert contract.gamma is not None
        assert contract.theta is not None
        assert contract.vega is not None
        assert contract.iv is not None
        assert 0.0 < contract.iv < 5.0  # decimal fraction, not a percentage

        stats = bridge.get_statistics()
        assert stats.ticks_with_greeks == 1
        assert stats.ticks_greeks_unavailable == 0

    def test_option_tick_no_greeks_when_spot_unknown(self) -> None:
        bridge, ws_client, fake = make_bridge()
        bridge._streaming_engine.start()
        bridge.register_instruments(
            [
                spot_descriptor(1001, "NIFTY"),
                option_descriptor(1011, "NIFTY", 24500.0, "CE", expiry="2026-08-13"),
            ]
        )
        bridge.start()
        ws_client.connect()

        # Option tick arrives before any spot tick has been seen -- no
        # snapshot can assemble yet either way (SPOT quote is required),
        # so assert directly on the bridge's own forwarding outcome.
        fake.emit_ticks([raw_option_tick(1011, last_price=120.0, bid=119.5, ask=120.5)])

        stats = bridge.get_statistics()
        assert stats.ticks_forwarded == 1
        assert stats.ticks_with_greeks == 0
        assert stats.ticks_greeks_unavailable == 1

    def test_spot_tick_itself_has_no_greeks(self) -> None:
        bridge, ws_client, fake = make_bridge()
        bridge.register_instruments([spot_descriptor(1001, "NIFTY")])
        bridge.start()
        ws_client.connect()

        fake.emit_ticks([raw_index_tick(1001)])

        stats = bridge.get_statistics()
        assert stats.ticks_forwarded == 1
        assert stats.ticks_with_greeks == 0
        assert stats.ticks_greeks_unavailable == 0

    def test_vix_tick_has_no_greeks(self) -> None:
        bridge, ws_client, fake = make_bridge()
        bridge._streaming_engine.start()
        bridge.register_instruments([spot_descriptor(1001, "NIFTY"), vix_descriptor(4001, "NIFTY")])
        bridge.start()
        ws_client.connect()

        fake.emit_ticks([raw_index_tick(1001, last_price=24512.5)])
        fake.emit_ticks([raw_index_tick(4001, last_price=13.5)])

        stats = bridge.get_statistics()
        assert stats.ticks_with_greeks == 0
        assert stats.ticks_greeks_unavailable == 0

    def test_future_tick_has_no_greeks(self) -> None:
        bridge, ws_client, fake = make_bridge()
        bridge._streaming_engine.start()
        bridge.register_instruments(
            [spot_descriptor(1001, "NIFTY"), future_descriptor(3001, "NIFTY")]
        )
        bridge.start()
        ws_client.connect()

        fake.emit_ticks([raw_index_tick(1001, last_price=24512.5)])
        fake.emit_ticks([raw_index_tick(3001, last_price=24530.0)])

        stats = bridge.get_statistics()
        assert stats.ticks_with_greeks == 0
        assert stats.ticks_greeks_unavailable == 0

    def test_greeks_source_and_computed_at_set(self) -> None:
        bridge, ws_client, fake = make_bridge()
        bridge._streaming_engine.start()
        bridge.register_instruments(
            [spot_descriptor(1001, "NIFTY"), option_descriptor(1011, "NIFTY", 24500.0, "CE")]
        )
        bridge.start()
        ws_client.connect()

        fake.emit_ticks([raw_index_tick(1001, last_price=24512.5)])
        fake.emit_ticks([raw_option_tick(1011)])

        snapshot = bridge._streaming_engine.get_snapshot("NIFTY")
        # Greeks are consumed into flat contract fields by the streaming
        # engine; verify via a direct _compute_greeks call for attachment
        # metadata (source/computed_at) not exposed on OptionContractSnapshot.
        descriptor = option_descriptor(1011, "NIFTY", 24500.0, "CE")
        attachment = bridge._compute_greeks(
            descriptor, raw_option_tick(1011), received_at=FIXED_NOW
        )
        assert attachment is not None
        assert attachment.source == "option_greeks_engine"
        assert attachment.computed_at == FIXED_NOW

    def test_greeks_engine_failure_does_not_break_tick_forwarding(self) -> None:
        class _RaisingGreeksEngine(OptionGreeksEngine):
            def enrich_contract(self, *args, **kwargs):
                raise RuntimeError("greeks boom")

        bridge, ws_client, fake = make_bridge()
        bridge._streaming_engine.start()
        bridge._greeks_engine = _RaisingGreeksEngine()
        bridge.register_instruments(
            [spot_descriptor(1001, "NIFTY"), option_descriptor(1011, "NIFTY", 24500.0, "CE")]
        )
        bridge.start()
        ws_client.connect()

        fake.emit_ticks([raw_index_tick(1001, last_price=24512.5)])
        fake.emit_ticks([raw_option_tick(1011)])  # must not raise

        stats = bridge.get_statistics()
        assert stats.ticks_forwarded == 2
        assert stats.ticks_dropped_error == 0
        snapshot = bridge._streaming_engine.get_snapshot("NIFTY")
        assert snapshot.option_chain.contracts[0].delta is None

    def test_invalid_greeks_result_yields_none(self) -> None:
        class _AlwaysInvalidGreeksEngine(OptionGreeksEngine):
            def enrich_contract(self, *args, **kwargs):
                return {"valid": False, "reason": "TEST", "errors": [], "contract": None}

        bridge, ws_client, fake = make_bridge()
        bridge._streaming_engine.start()
        bridge._greeks_engine = _AlwaysInvalidGreeksEngine()
        bridge.register_instruments(
            [spot_descriptor(1001, "NIFTY"), option_descriptor(1011, "NIFTY", 24500.0, "CE")]
        )
        bridge.start()
        ws_client.connect()

        fake.emit_ticks([raw_index_tick(1001, last_price=24512.5)])
        fake.emit_ticks([raw_option_tick(1011)])

        assert bridge.get_statistics().ticks_greeks_unavailable == 1
        assert bridge.get_statistics().ticks_with_greeks == 0

    def test_custom_greeks_engine_injection(self) -> None:
        calls: list = []

        class _SpyGreeksEngine(OptionGreeksEngine):
            def enrich_contract(self, contract, **kwargs):
                calls.append(contract["tradingsymbol"])
                return super().enrich_contract(contract, **kwargs)

        ws_client, fake = make_client(underlyings=("NIFTY",), clock=fixed_clock)
        engine = make_engine(("NIFTY",), clock=fixed_clock)
        bridge = WebSocketMarketDataBridge(
            ws_client=ws_client,
            streaming_engine=engine,
            greeks_engine=_SpyGreeksEngine(),
            clock=fixed_clock,
        )
        bridge.register_instruments(
            [spot_descriptor(1001, "NIFTY"), option_descriptor(1011, "NIFTY", 24500.0, "CE")]
        )
        bridge.start()
        ws_client.connect()

        fake.emit_ticks([raw_index_tick(1001, last_price=24512.5)])
        fake.emit_ticks([raw_option_tick(1011)])

        assert calls  # custom engine instance was actually invoked

    def test_missing_option_metadata_on_descriptor_skips_greeks(self) -> None:
        # MarketDataStreamingEngine.register_instruments() itself rejects
        # option descriptors missing strike/option_type/expiry, so this
        # exercises _compute_greeks directly (the bridge's own defensive
        # guard, independent of the engine's registration validation).
        from broker.market_data_streaming import InstrumentDescriptor, InstrumentRole

        bridge, _, _ = make_bridge()
        bridge.register_instruments([spot_descriptor(1001, "NIFTY")])
        bridge._on_tick(raw_index_tick(1001, last_price=24512.5))

        incomplete_descriptor = InstrumentDescriptor(
            instrument_token=1011,
            underlying="NIFTY",
            quote_key="NFO:NIFTY24500CE",
            exchange="NFO",
            tradingsymbol="NIFTY24500CE",
            instrument_kind="CE",
            instrument_role=InstrumentRole.OPTION_CE,
            # strike/option_type/expiry deliberately omitted.
        )
        attachment = bridge._compute_greeks(
            incomplete_descriptor, raw_option_tick(1011), received_at=FIXED_NOW
        )
        assert attachment is None

    def test_statistics_track_mixed_greeks_outcomes(self) -> None:
        bridge, ws_client, fake = make_bridge()
        bridge._streaming_engine.start()
        bridge.register_instruments(
            [
                spot_descriptor(1001, "NIFTY"),
                option_descriptor(1011, "NIFTY", 24500.0, "CE"),
                option_descriptor(1012, "NIFTY", 24500.0, "PE"),
            ]
        )
        bridge.start()
        ws_client.connect()

        # PE tick arrives before spot -> unavailable; then spot arrives;
        # then CE tick arrives -> available.
        fake.emit_ticks([raw_option_tick(1012, last_price=110.0)])
        fake.emit_ticks([raw_index_tick(1001, last_price=24512.5)])
        fake.emit_ticks([raw_option_tick(1011, last_price=120.0)])

        stats = bridge.get_statistics()
        assert stats.ticks_greeks_unavailable == 1
        assert stats.ticks_with_greeks == 1
