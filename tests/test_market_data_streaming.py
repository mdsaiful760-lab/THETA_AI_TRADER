"""Unit tests for broker.market_data_streaming."""

from __future__ import annotations

import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from broker.market_data_streaming import (
    MARKET_DATA_STREAMING_VERSION,
    PRODUCER_NAME,
    SUPPORTED_PRIMARY_UNDERLYINGS,
    SUPPORTED_SECONDARY_UNDERLYINGS,
    SUPPORTED_UNDERLYINGS,
    TOPIC_SNAPSHOT_FAILED,
    TOPIC_SNAPSHOT_PUBLISHED,
    TOPIC_SNAPSHOT_SKIPPED,
    ExpectedMoveEstimate,
    GreeksAttachment,
    InstrumentDescriptor,
    InstrumentRole,
    InstrumentValidationError,
    LatestQuoteBook,
    MarketDataStreamingConfig,
    MarketDataStreamingConfigurationError,
    MarketDataStreamingEngine,
    MarketDataStreamingSerializationError,
    MarketDataStreamingStateError,
    SnapshotCache,
    SnapshotHistory,
    SnapshotPublishOutcome,
    StreamingHealthStatus,
    StreamingLifecycleState,
    StreamingSnapshotService,
    TickEvent,
    TickValidationError,
    UnderlyingSupportTier,
    classify_underlying_tier,
    compute_expected_move,
    default_market_data_streaming_config,
    derive_atm,
    deserialize_snapshot_statistics,
    deserialize_streaming_health_report,
    deserialize_streaming_publish_event,
    deserialize_streaming_snapshot_view,
    normalize_exchange_timestamp,
    normalize_underlying_name,
    resolve_instrument_role,
    serialize_snapshot_statistics,
    serialize_streaming_health_report,
    serialize_streaming_publish_event,
    serialize_streaming_snapshot_view,
    snapshot_statistics_from_json,
    snapshot_statistics_to_json,
    streaming_health_report_from_json,
    streaming_health_report_to_json,
)
from config.application_configuration import EnvironmentProfile
from core.event_bus import EventBus, EventBusPolicy
from market_data.market_snapshot import MarketSnapshot, SnapshotValidationStatus

FIXED_NOW = datetime(2026, 8, 5, 5, 0, tzinfo=timezone.utc)
EXPIRY = "2026-08-07"


def fixed_clock() -> datetime:
    """Deterministic engine clock."""
    return FIXED_NOW


def make_config(
    underlyings: tuple[str, ...] = ("NIFTY",),
    **kwargs: Any,
) -> MarketDataStreamingConfig:
    """Build a test config with assembly throttle disabled.

    ``strike_window_strikes`` defaults to 2 so a two-strike CE/PE chain
    clears the canonical completeness floor (default ValidationPolicy).
    """
    defaults: dict[str, Any] = {
        "enabled_underlyings": underlyings,
        "snapshot_min_interval_seconds": 0.0,
        "tick_staleness_seconds": 30.0,
        "history_ring_size": 10,
        "max_missing_quote_ratio": 0.5,
        "min_complete_pairs": 1,
        "strike_window_strikes": 2,
        "default_strike_step": 50.0,
        "expected_move_enabled": True,
        "runner_kind": "test",
    }
    defaults.update(kwargs)
    return MarketDataStreamingConfig(**defaults)


def make_engine(
    underlyings: tuple[str, ...] = ("NIFTY",),
    *,
    event_bus: EventBus | None = None,
    clock=fixed_clock,
    **config_kwargs: Any,
) -> MarketDataStreamingEngine:
    """Construct and start a streaming engine for tests."""
    config = make_config(underlyings, **config_kwargs)
    engine = MarketDataStreamingEngine(
        config,
        event_bus=event_bus,
        clock=clock,
        id_factory=lambda: "id-fixed",
    )
    engine.start()
    return engine


def spot_descriptor(
    token: int = 1001,
    underlying: str = "NIFTY",
) -> InstrumentDescriptor:
    """Resolved spot descriptor (token supplied by test, not module)."""
    return InstrumentDescriptor(
        instrument_token=token,
        underlying=underlying,
        quote_key=f"EX:SPOT-{underlying}-{token}",
        exchange="NSE",
        tradingsymbol=f"SPOT-{underlying}",
        instrument_kind="INDEX",
        instrument_role=InstrumentRole.SPOT,
    )


def option_descriptor(
    token: int,
    underlying: str,
    strike: float,
    option_type: str,
    *,
    expiry: str = EXPIRY,
) -> InstrumentDescriptor:
    """Resolved option descriptor."""
    return InstrumentDescriptor(
        instrument_token=token,
        underlying=underlying,
        quote_key=f"NFO:{underlying}-{int(strike)}{option_type}",
        exchange="NFO",
        tradingsymbol=f"{underlying}{int(strike)}{option_type}",
        instrument_kind=option_type,
        instrument_role=(
            InstrumentRole.OPTION_CE if option_type == "CE" else InstrumentRole.OPTION_PE
        ),
        strike=strike,
        option_type=option_type,
        expiry=expiry,
        lot_size=50,
        tick_size=0.05,
    )


def future_descriptor(
    token: int = 3001,
    underlying: str = "NIFTY",
    *,
    expiry: str = EXPIRY,
) -> InstrumentDescriptor:
    """Resolved futures descriptor."""
    return InstrumentDescriptor(
        instrument_token=token,
        underlying=underlying,
        quote_key=f"NFO:{underlying}-FUT",
        exchange="NFO",
        tradingsymbol=f"{underlying}-FUT",
        instrument_kind="FUT",
        instrument_role=InstrumentRole.FUTURE,
        expiry=expiry,
        lot_size=50,
    )


def vix_descriptor(token: int = 4001, underlying: str = "NIFTY") -> InstrumentDescriptor:
    """Resolved volatility-index descriptor tagged to an underlying universe."""
    return InstrumentDescriptor(
        instrument_token=token,
        underlying=underlying,
        quote_key="NSE:INDIA-VIX",
        exchange="NSE",
        tradingsymbol="INDIA VIX",
        instrument_kind="VIX",
        instrument_role=InstrumentRole.VOLATILITY_INDEX,
    )


def make_tick(
    token: int,
    underlying: str,
    kind: str,
    *,
    last_price: float,
    bid: float | None = None,
    ask: float | None = None,
    volume: int = 100,
    oi: int | None = 1000,
    sequence: int | None = 1,
    greeks: GreeksAttachment | None = None,
    received_at: datetime | None = None,
    exchange_timestamp: datetime | None = None,
    metadata: dict[str, str] | None = None,
) -> TickEvent:
    """Build a normalized TickEvent for tests."""
    return TickEvent(
        instrument_token=token,
        underlying=underlying,
        quote_key=f"EX:{underlying}-{token}",
        exchange="NSE" if kind in {"INDEX", "VIX"} else "NFO",
        tradingsymbol=f"SYM-{token}",
        instrument_kind=kind,
        last_price=last_price,
        volume=volume,
        received_at=received_at or FIXED_NOW,
        bid=bid if bid is not None else max(last_price - 0.5, 0.05),
        ask=ask if ask is not None else last_price + 0.5,
        open_interest=oi,
        open=last_price,
        high=last_price + 1,
        low=last_price - 1,
        close=last_price - 0.25,
        sequence=sequence,
        greeks=greeks,
        exchange_timestamp=exchange_timestamp or FIXED_NOW,
        metadata=metadata or {},
    )


def register_chain(
    engine: MarketDataStreamingEngine,
    *,
    underlying: str = "NIFTY",
    spot_token: int = 1001,
    atm: float = 24500.0,
    include_future: bool = False,
    include_vix: bool = False,
) -> None:
    """Register spot + one ATM CE/PE pair (and optional future/vix)."""
    descriptors: list[InstrumentDescriptor] = [
        spot_descriptor(spot_token, underlying),
        option_descriptor(spot_token + 10, underlying, atm, "CE"),
        option_descriptor(spot_token + 11, underlying, atm, "PE"),
        option_descriptor(spot_token + 12, underlying, atm + 50, "CE"),
        option_descriptor(spot_token + 13, underlying, atm + 50, "PE"),
    ]
    if include_future:
        descriptors.append(future_descriptor(spot_token + 20, underlying))
    if include_vix:
        descriptors.append(vix_descriptor(spot_token + 30, underlying))
    engine.register_instruments(descriptors)


def feed_chain(
    engine: MarketDataStreamingEngine,
    *,
    underlying: str = "NIFTY",
    spot_token: int = 1001,
    spot: float = 24512.0,
    atm: float = 24500.0,
    with_iv: bool = False,
    include_future: bool = False,
    include_vix: bool = False,
    sequence: int = 1,
    at: datetime | None = None,
    metadata: dict[str, str] | None = None,
) -> None:
    """Ingest spot + option (and optional future/vix) ticks for one underlying."""
    ts = at or FIXED_NOW
    meta = metadata or {"correlation_id": f"corr-{underlying}-{sequence}"}
    greeks = (
        GreeksAttachment(iv=0.15, delta=0.5, gamma=0.01, theta=-10.0, vega=5.0)
        if with_iv
        else None
    )
    common: dict[str, Any] = {
        "sequence": sequence,
        "received_at": ts,
        "exchange_timestamp": ts,
        "metadata": meta,
    }
    engine.ingest_tick(
        make_tick(
            spot_token,
            underlying,
            "INDEX",
            last_price=spot,
            oi=None,
            **common,
        )
    )
    engine.ingest_tick(
        make_tick(
            spot_token + 10,
            underlying,
            "CE",
            last_price=120.0,
            greeks=greeks,
            **common,
        )
    )
    engine.ingest_tick(
        make_tick(
            spot_token + 11,
            underlying,
            "PE",
            last_price=110.0,
            greeks=greeks,
            **common,
        )
    )
    engine.ingest_tick(
        make_tick(spot_token + 12, underlying, "CE", last_price=95.0, **common)
    )
    engine.ingest_tick(
        make_tick(spot_token + 13, underlying, "PE", last_price=130.0, **common)
    )
    if include_future:
        engine.ingest_tick(
            make_tick(
                spot_token + 20,
                underlying,
                "FUT",
                last_price=spot + 15,
                **common,
            )
        )
    if include_vix:
        engine.ingest_tick(
            make_tick(
                spot_token + 30,
                underlying,
                "VIX",
                last_price=13.5,
                oi=None,
                **common,
            )
        )


class TestCatalogHelpers:
    """Catalog, role mapping, and pure helpers."""

    def test_catalog_constants(self) -> None:
        assert SUPPORTED_PRIMARY_UNDERLYINGS == frozenset(
            {"NIFTY", "BANKNIFTY", "SENSEX"}
        )
        assert SUPPORTED_SECONDARY_UNDERLYINGS == frozenset({"FINNIFTY", "MIDCPNIFTY"})
        assert SUPPORTED_UNDERLYINGS == (
            SUPPORTED_PRIMARY_UNDERLYINGS | SUPPORTED_SECONDARY_UNDERLYINGS
        )

    def test_normalize_and_classify(self) -> None:
        assert normalize_underlying_name(" banknifty ") == "BANKNIFTY"
        assert classify_underlying_tier("NIFTY") is UnderlyingSupportTier.PRIMARY
        assert classify_underlying_tier("FINNIFTY") is UnderlyingSupportTier.SECONDARY
        assert classify_underlying_tier("CUSTOM") is UnderlyingSupportTier.EXPERIMENTAL

    def test_resolve_instrument_role(self) -> None:
        assert resolve_instrument_role("index") is InstrumentRole.SPOT
        assert resolve_instrument_role("FUT") is InstrumentRole.FUTURE
        assert resolve_instrument_role("ce") is InstrumentRole.OPTION_CE
        assert resolve_instrument_role("PE") is InstrumentRole.OPTION_PE
        assert resolve_instrument_role("vix") is InstrumentRole.VOLATILITY_INDEX
        assert resolve_instrument_role("unknown-tag") is InstrumentRole.UNKNOWN

    def test_derive_atm_and_expected_move(self) -> None:
        atm = derive_atm(24512.0, 50.0, (24450.0, 24500.0, 24550.0))
        assert atm == 24500.0
        assert derive_atm(100.0, 50.0, ()) == 100.0
        estimate = compute_expected_move(
            underlying="NIFTY",
            spot=24500.0,
            atm_iv=0.15,
            days_to_expiry=7.0,
            trading_days_per_year=365.0,
            now=FIXED_NOW,
        )
        assert isinstance(estimate, ExpectedMoveEstimate)
        assert estimate.method == "ATM_IV_SQRT_TIME"
        assert estimate.expected_move_points > 0
        assert math.isclose(
            estimate.upper_bound, estimate.spot + estimate.expected_move_points
        )

    def test_normalize_exchange_timestamp(self) -> None:
        naive = datetime(2026, 8, 5, 10, 30)
        aware = normalize_exchange_timestamp(naive)
        assert aware is not None
        assert aware.tzinfo is not None
        assert normalize_exchange_timestamp(None) is None
        already = FIXED_NOW
        assert normalize_exchange_timestamp(already) == FIXED_NOW


class TestConfigValidation:
    """MarketDataStreamingConfig validation."""

    def test_rejects_unsupported_and_duplicates(self) -> None:
        with pytest.raises(MarketDataStreamingConfigurationError) as exc:
            MarketDataStreamingConfig(enabled_underlyings=("FOO",))
        assert exc.value.code == "MDS.CONFIG.UNDERLYING_UNSUPPORTED"
        with pytest.raises(MarketDataStreamingConfigurationError) as exc2:
            MarketDataStreamingConfig(enabled_underlyings=("NIFTY", "nifty"))
        assert exc2.value.code == "MDS.CONFIG.UNDERLYING_DUPLICATE"
        with pytest.raises(MarketDataStreamingConfigurationError) as exc3:
            MarketDataStreamingConfig(enabled_underlyings=())
        assert exc3.value.code == "MDS.CONFIG.UNDERLYING_REQUIRED"

    def test_accepts_primary_secondary_mix(self) -> None:
        config = MarketDataStreamingConfig(
            enabled_underlyings=("NIFTY", "FINNIFTY", "SENSEX")
        )
        assert config.enabled_underlyings == ("NIFTY", "FINNIFTY", "SENSEX")

    def test_threshold_guards(self) -> None:
        with pytest.raises(MarketDataStreamingConfigurationError):
            MarketDataStreamingConfig(
                enabled_underlyings=("NIFTY",), tick_staleness_seconds=-1
            )
        with pytest.raises(MarketDataStreamingConfigurationError):
            MarketDataStreamingConfig(
                enabled_underlyings=("NIFTY",), max_missing_quote_ratio=1.5
            )
        with pytest.raises(MarketDataStreamingConfigurationError):
            MarketDataStreamingConfig(
                enabled_underlyings=("NIFTY",), history_ring_size=0
            )
        with pytest.raises(MarketDataStreamingConfigurationError):
            MarketDataStreamingConfig(
                enabled_underlyings=("NIFTY",),
                strike_step={"NIFTY": -10.0},
            )
        with pytest.raises(MarketDataStreamingConfigurationError):
            MarketDataStreamingConfig(
                enabled_underlyings=("NIFTY",),
                min_complete_pairs=-1,
            )
        with pytest.raises(MarketDataStreamingConfigurationError):
            MarketDataStreamingConfig(
                enabled_underlyings=("NIFTY",),
                strike_window_strikes=0,
            )
        with pytest.raises(MarketDataStreamingConfigurationError):
            MarketDataStreamingConfig(
                enabled_underlyings=("NIFTY",),
                default_strike_step=0.0,
            )
        config = MarketDataStreamingConfig(
            enabled_underlyings=("NIFTY",),
            strike_step={"nifty": 50.0},
            metadata={"runner": "unit"},
        )
        assert config.strike_step["NIFTY"] == 50.0
        assert config.metadata["runner"] == "unit"

    def test_experimental_allowed(self) -> None:
        config = MarketDataStreamingConfig(
            enabled_underlyings=("CUSTOMUL",),
            allow_experimental_underlyings=True,
        )
        assert config.enabled_underlyings == ("CUSTOMUL",)

    def test_default_config_helper(self) -> None:
        paper = default_market_data_streaming_config(
            EnvironmentProfile.PAPER,
            enabled_underlyings=("NIFTY", "BANKNIFTY"),
        )
        assert paper.enabled_underlyings == ("NIFTY", "BANKNIFTY")
        assert paper.environment_profile is EnvironmentProfile.PAPER
        assert paper.publish_events is True
        assert paper.runner_kind == "unknown"

        production = default_market_data_streaming_config(
            EnvironmentProfile.PRODUCTION,
            enabled_underlyings=("NIFTY",),
        )
        assert production.require_futures_for_snapshot is True
        assert production.require_volatility_index is True
        assert production.min_complete_pairs == 3

        development = default_market_data_streaming_config(
            EnvironmentProfile.DEVELOPMENT,
        )
        assert development.allow_experimental_underlyings is True
        assert development.snapshot_min_interval_seconds == 0.0


class TestLifecycleAndRegistration:
    """Lifecycle and instrument registration."""

    def test_start_stop_and_restart_forbidden(self) -> None:
        engine = MarketDataStreamingEngine(make_config(), clock=fixed_clock)
        assert engine.get_status() is StreamingLifecycleState.CREATED
        engine.start()
        assert engine.get_status() is StreamingLifecycleState.RUNNING
        engine.start()  # idempotent
        engine.stop()
        assert engine.get_status() is StreamingLifecycleState.STOPPED
        with pytest.raises(MarketDataStreamingStateError) as exc:
            engine.start()
        assert exc.value.code == "MDS.STATE.INVALID_TRANSITION"

    def test_ingest_requires_running(self) -> None:
        engine = MarketDataStreamingEngine(make_config(), clock=fixed_clock)
        with pytest.raises(MarketDataStreamingStateError) as exc:
            engine.ingest_tick(
                make_tick(1, "NIFTY", "INDEX", last_price=100.0)
            )
        assert exc.value.code == "MDS.STATE.NOT_RUNNING"

    def test_register_validation(self) -> None:
        engine = make_engine(("NIFTY",))
        with pytest.raises(InstrumentValidationError) as exc:
            engine.register_instruments(
                (
                    InstrumentDescriptor(
                        instrument_token=0,
                        underlying="NIFTY",
                        quote_key="X",
                        exchange="NSE",
                        tradingsymbol="X",
                        instrument_kind="INDEX",
                    ),
                )
            )
        assert exc.value.code == "MDS.INSTRUMENT.INVALID_TOKEN"

        with pytest.raises(InstrumentValidationError) as exc2:
            engine.register_instruments(
                (spot_descriptor(1), spot_descriptor(1))
            )
        assert exc2.value.code == "MDS.INSTRUMENT.DUPLICATE_TOKEN"

        with pytest.raises(InstrumentValidationError) as exc3:
            engine.register_instruments((spot_descriptor(1, "BANKNIFTY"),))
        assert exc3.value.code == "MDS.INSTRUMENT.UNDERLYING_NOT_ENABLED"

        with pytest.raises(InstrumentValidationError) as exc4:
            engine.register_instruments(
                (
                    InstrumentDescriptor(
                        instrument_token=2,
                        underlying="NIFTY",
                        quote_key="X",
                        exchange="NFO",
                        tradingsymbol="X",
                        instrument_kind="CE",
                        instrument_role=InstrumentRole.OPTION_CE,
                        strike=None,
                        option_type="CE",
                        expiry=EXPIRY,
                        lot_size=50,
                    ),
                )
            )
        assert exc4.value.code == "MDS.INSTRUMENT.INCOMPLETE_OPTION_METADATA"

    def test_deregister_and_alias(self) -> None:
        engine = make_engine()
        register_chain(engine)
        engine.deregister_instruments((1001,))
        assert engine.get_quote(1001) is None
        assert StreamingSnapshotService is MarketDataStreamingEngine
        assert engine.enabled_underlyings() == ("NIFTY",)


class TestTickValidationAndQuoteBook:
    """Tick validation and LatestQuoteBook behaviour."""

    def test_tick_validation_errors(self) -> None:
        engine = make_engine()
        register_chain(engine)
        with pytest.raises(TickValidationError) as exc:
            engine.ingest_tick(
                make_tick(0, "NIFTY", "INDEX", last_price=100.0)
            )
        assert exc.value.code == "MDS.TICK.INVALID_TOKEN"

        with pytest.raises(TickValidationError) as exc2:
            engine.ingest_tick(
                TickEvent(
                    instrument_token=1001,
                    underlying="NIFTY",
                    quote_key="X",
                    exchange="NSE",
                    tradingsymbol="X",
                    instrument_kind="INDEX",
                    last_price=100.0,
                    volume=1,
                    received_at=datetime(2026, 8, 5, 10, 0),  # naive
                )
            )
        assert exc2.value.code == "MDS.TICK.NAIVE_TIMESTAMP"

        with pytest.raises(TickValidationError) as exc3:
            engine.ingest_tick(
                make_tick(1001, "NIFTY", "INDEX", last_price=float("nan"))
            )
        assert exc3.value.code == "MDS.TICK.INVALID_PRICE"

        with pytest.raises(TickValidationError) as exc4:
            engine.ingest_tick(
                make_tick(1001, "NIFTY", "INDEX", last_price=100.0, volume=-1)
            )
        assert exc4.value.code == "MDS.TICK.INVALID_VOLUME"

    def test_unattributed_and_duplicate_tolerance(self) -> None:
        engine = make_engine()
        register_chain(engine)
        engine.ingest_tick(make_tick(9999, "NIFTY", "INDEX", last_price=100.0))
        stats = engine.get_statistics()
        assert stats.unattributed_tick_count == 1

        engine.ingest_tick(
            make_tick(1001, "NIFTY", "INDEX", last_price=100.0, sequence=5)
        )
        engine.ingest_tick(
            make_tick(1001, "NIFTY", "INDEX", last_price=101.0, sequence=4)
        )  # ignored when tolerant
        quote = engine.get_quote(1001)
        assert quote is not None
        assert quote.last_tick.last_price == 100.0

    def test_out_of_order_raises_when_intolerant(self) -> None:
        engine = make_engine(duplicate_tick_tolerance=False)
        register_chain(engine)
        engine.ingest_tick(
            make_tick(1001, "NIFTY", "INDEX", last_price=100.0, sequence=5)
        )
        with pytest.raises(TickValidationError) as exc:
            engine.ingest_tick(
                make_tick(1001, "NIFTY", "INDEX", last_price=101.0, sequence=4)
            )
        assert exc.value.code == "MDS.TICK.OUT_OF_ORDER"

    def test_latest_quote_book_direct(self) -> None:
        book = LatestQuoteBook(
            enabled_underlyings=("NIFTY",),
            tick_staleness_seconds=5.0,
        )
        book.register_instruments((spot_descriptor(),))
        tick = make_tick(1001, "NIFTY", "INDEX", last_price=100.0)
        record = book.update(tick, now=FIXED_NOW)
        assert record.update_count == 1
        assert book.get(1001) is not None
        assert book.token_count() == 1
        assert book.underlying_token_count("NIFTY") == 1
        assert book.get_by_role("NIFTY", InstrumentRole.SPOT)
        assert book.is_stale(1001, now=FIXED_NOW + timedelta(seconds=10)) is True
        assert book.is_stale(1001, now=FIXED_NOW) is False


class TestSnapshotBuildingCacheHistory:
    """Snapshot assembly, cache, history, and views."""

    def test_builds_and_caches_snapshot(self) -> None:
        events: list[Any] = []
        engine = make_engine()
        engine.add_publish_callback(events.append)
        register_chain(engine, include_future=True, include_vix=True)
        feed_chain(engine, with_iv=True, include_future=True, include_vix=True)

        snapshot = engine.get_snapshot("NIFTY")
        assert isinstance(snapshot, MarketSnapshot)
        assert snapshot.provenance.underlying_symbol == "NIFTY"
        assert snapshot.option_chain.metadata.atm_strike == 24500.0
        assert snapshot.quality.validation_status in {
            SnapshotValidationStatus.VALID,
            SnapshotValidationStatus.PARTIAL,
        }

        view = engine.get_streaming_view("NIFTY")
        assert view is not None
        assert view.atm_strike == 24500.0
        assert view.futures is not None
        assert view.futures.basis is not None
        assert view.atm_iv is not None
        assert view.expected_move is not None
        assert view.total_call_oi > 0
        assert view.total_volume > 0

        history = engine.get_history("NIFTY")
        assert len(history) >= 1
        assert history[-1].provenance.snapshot_id == snapshot.provenance.snapshot_id

        quotes = engine.get_quotes_for_underlying("NIFTY")
        assert len(quotes) >= 5
        assert events
        assert events[-1].outcome is SnapshotPublishOutcome.PUBLISHED

    def test_multi_underlying(self) -> None:
        engine = make_engine(("NIFTY", "BANKNIFTY", "SENSEX"))
        register_chain(engine, underlying="NIFTY", spot_token=1001, atm=24500.0)
        register_chain(engine, underlying="BANKNIFTY", spot_token=2001, atm=52000.0)
        register_chain(engine, underlying="SENSEX", spot_token=3001, atm=81000.0)
        feed_chain(engine, underlying="NIFTY", spot_token=1001, spot=24512.0, atm=24500.0)
        feed_chain(
            engine,
            underlying="BANKNIFTY",
            spot_token=2001,
            spot=52010.0,
            atm=52000.0,
        )
        feed_chain(
            engine,
            underlying="SENSEX",
            spot_token=3001,
            spot=81025.0,
            atm=81000.0,
        )
        assert engine.get_snapshot("NIFTY") is not None
        assert engine.get_snapshot("BANKNIFTY") is not None
        assert engine.get_snapshot("SENSEX") is not None
        stats = engine.get_statistics()
        assert len(stats.per_underlying) == 3
        assert {u.underlying for u in stats.per_underlying} == {
            "NIFTY",
            "BANKNIFTY",
            "SENSEX",
        }

    def test_cache_and_history_components(self) -> None:
        cache = SnapshotCache()
        history = SnapshotHistory(
            enabled_underlyings=("NIFTY",),
            history_ring_size=2,
        )
        engine = make_engine(history_ring_size=2)
        register_chain(engine)
        feed_chain(engine, sequence=1)
        snap1 = engine.get_snapshot("NIFTY")
        assert snap1 is not None
        view1 = engine.get_streaming_view("NIFTY")
        assert view1 is not None
        cache.put("NIFTY", snap1, view1)
        assert cache.get("NIFTY") is snap1
        assert cache.get_view("NIFTY") is view1
        assert "NIFTY" in cache.all_snapshots()
        history.append("NIFTY", snap1)
        feed_chain(engine, sequence=2)
        feed_chain(engine, sequence=3)
        hist = engine.get_history("NIFTY", limit=2)
        assert len(hist) <= 2
        cache.clear("NIFTY")
        assert cache.get("NIFTY") is None
        history.clear()
        assert history.size("NIFTY") == 0
        assert history.capacity() == 2

    def test_missing_spot_fails_assembly(self) -> None:
        events: list[Any] = []
        engine = make_engine()
        engine.add_publish_callback(events.append)
        engine.register_instruments(
            (
                option_descriptor(1010, "NIFTY", 24500.0, "CE"),
                option_descriptor(1011, "NIFTY", 24500.0, "PE"),
            )
        )
        engine.ingest_tick(
            make_tick(1010, "NIFTY", "CE", last_price=10.0)
        )
        assert any(
            e.outcome is SnapshotPublishOutcome.FAILED
            and e.reason_code == "MDS.SNAPSHOT.MISSING_SPOT"
            for e in events
        )

    def test_futures_required_gate(self) -> None:
        events: list[Any] = []
        engine = make_engine(require_futures_for_snapshot=True)
        engine.add_publish_callback(events.append)
        register_chain(engine, include_future=False)
        feed_chain(engine, include_future=False)
        assert any(
            e.outcome is SnapshotPublishOutcome.SKIPPED
            and e.reason_code == "MDS.SNAPSHOT.FUTURES_REQUIRED"
            for e in events
        )

    def test_volatility_required_gate(self) -> None:
        events: list[Any] = []
        engine = make_engine(require_volatility_index=True)
        engine.add_publish_callback(events.append)
        register_chain(engine)
        feed_chain(engine)
        assert any(
            e.outcome is SnapshotPublishOutcome.SKIPPED
            and e.reason_code == "MDS.SNAPSHOT.VOLATILITY_REQUIRED"
            for e in events
        )

    def test_throttle_skips_assembly(self) -> None:
        times = [FIXED_NOW]

        def clock() -> datetime:
            return times[0]

        engine = make_engine(clock=clock, snapshot_min_interval_seconds=10.0)
        register_chain(engine)
        # Advance past the throttle window between ticks so the chain can assemble.
        for offset, tick in enumerate(
            (
                make_tick(1001, "NIFTY", "INDEX", last_price=24512.0, oi=None),
                make_tick(1011, "NIFTY", "CE", last_price=120.0),
                make_tick(1012, "NIFTY", "PE", last_price=110.0),
                make_tick(1013, "NIFTY", "CE", last_price=95.0),
                make_tick(1014, "NIFTY", "PE", last_price=130.0),
            )
        ):
            times[0] = FIXED_NOW + timedelta(seconds=11 * offset)
            engine.ingest_tick(
                make_tick(
                    tick.instrument_token,
                    tick.underlying,
                    tick.instrument_kind,
                    last_price=tick.last_price,
                    oi=tick.open_interest,
                    sequence=offset + 1,
                    received_at=times[0],
                    exchange_timestamp=times[0],
                )
            )
        published = engine.get_statistics().total_snapshot_published_count
        attempts = engine.get_statistics().per_underlying[0].snapshot_attempt_count
        assert published >= 1
        # Same clock → throttle; no new assembly attempt
        engine.ingest_tick(
            make_tick(
                1001,
                "NIFTY",
                "INDEX",
                last_price=24520.0,
                sequence=20,
                received_at=times[0],
                exchange_timestamp=times[0],
            )
        )
        assert (
            engine.get_statistics().per_underlying[0].snapshot_attempt_count
            == attempts
        )
        times[0] = times[0] + timedelta(seconds=11)
        feed_chain(engine, sequence=30, at=times[0])
        assert (
            engine.get_statistics().per_underlying[0].snapshot_attempt_count
            > attempts
        )


class TestHealthStatisticsEvents:
    """Health, statistics, callbacks, and EventBus publishing."""

    def test_health_and_statistics(self) -> None:
        engine = make_engine(("NIFTY", "BANKNIFTY"))
        register_chain(engine, underlying="NIFTY", spot_token=1001)
        feed_chain(engine, underlying="NIFTY", spot_token=1001, with_iv=True)
        health = engine.get_health()
        assert health.enabled_underlyings == ("NIFTY", "BANKNIFTY")
        assert len(health.per_underlying) == 2
        nifty_health = next(h for h in health.per_underlying if h.underlying == "NIFTY")
        assert nifty_health.has_snapshot is True
        bank_health = next(h for h in health.per_underlying if h.underlying == "BANKNIFTY")
        assert bank_health.has_snapshot is False
        assert health.overall_health in {
            StreamingHealthStatus.HEALTHY,
            StreamingHealthStatus.DEGRADED,
            StreamingHealthStatus.UNHEALTHY,
        }

        stats = engine.get_statistics()
        assert stats.total_tick_count > 0
        assert stats.total_snapshot_published_count >= 1
        nifty_stats = next(s for s in stats.per_underlying if s.underlying == "NIFTY")
        assert nifty_stats.tick_count > 0
        engine.reset_statistics()
        reset = engine.get_statistics()
        assert reset.total_tick_count == 0
        assert engine.get_snapshot("NIFTY") is not None  # cache retained

    def test_validate_static_issues(self) -> None:
        engine = make_engine(("NIFTY", "BANKNIFTY"))
        issues = engine.validate()
        codes = {i.issue_code for i in issues}
        assert "MDS.VALIDATION.UNDERLYING_WITHOUT_INSTRUMENTS" in codes
        register_chain(engine, underlying="NIFTY")
        engine.register_instruments(
            (
                option_descriptor(2010, "BANKNIFTY", 52000.0, "CE"),
                option_descriptor(2011, "BANKNIFTY", 52000.0, "PE"),
            )
        )
        issues2 = engine.validate()
        assert any(
            i.issue_code == "MDS.VALIDATION.UNDERLYING_WITHOUT_SPOT"
            for i in issues2
        )

    def test_callback_isolation_and_dedup(self) -> None:
        seen: list[int] = []

        def good(event: Any) -> None:
            seen.append(1)

        def bad(event: Any) -> None:
            raise RuntimeError("boom")

        engine = make_engine()
        engine.add_publish_callback(good)
        engine.add_publish_callback(good)  # dedupe
        engine.add_publish_callback(bad)
        register_chain(engine)
        feed_chain(engine)
        assert seen  # good still invoked despite bad
        engine.remove_publish_callback(good)
        engine.remove_publish_callback(bad)

    def test_event_bus_publish(self) -> None:
        bus = EventBus(EventBusPolicy())
        received: list[Any] = []
        bus.subscribe(
            TOPIC_SNAPSHOT_PUBLISHED,
            lambda env: received.append(env.payload),
        )
        engine = make_engine(event_bus=bus, publish_events=True)
        register_chain(engine)
        feed_chain(engine)
        assert received
        assert received[-1].outcome is SnapshotPublishOutcome.PUBLISHED

    def test_ingest_raw_tick(self) -> None:
        def normalizer(raw: dict[str, Any], *, instrument_token: int) -> TickEvent:
            return make_tick(
                instrument_token,
                "NIFTY",
                "INDEX",
                last_price=float(raw["last_price"]),
                sequence=int(raw.get("sequence", 1)),
            )

        engine = MarketDataStreamingEngine(
            make_config(),
            clock=fixed_clock,
            tick_normalizer=normalizer,
        )
        engine.start()
        register_chain(engine)
        # Need options too for publish; feed options via ingest_tick
        feed_chain(engine)
        engine.ingest_raw_tick({"last_price": 24550.0, "sequence": 99}, instrument_token=1001)
        assert engine.get_quote(1001) is not None

        bare = MarketDataStreamingEngine(make_config(), clock=fixed_clock)
        bare.start()
        with pytest.raises(MarketDataStreamingStateError) as exc:
            bare.ingest_raw_tick({"last_price": 1.0}, instrument_token=1)
        assert exc.value.code == "MDS.STATE.NORMALIZER_NOT_CONFIGURED"


class TestSerialization:
    """Serialization and deserialization round-trips."""

    def test_health_and_stats_json_round_trip(self) -> None:
        engine = make_engine(("NIFTY", "FINNIFTY"))
        register_chain(engine)
        feed_chain(engine, with_iv=True)
        health = engine.get_health()
        stats = engine.get_statistics()

        health_json = streaming_health_report_to_json(health)
        restored_health = streaming_health_report_from_json(health_json)
        assert restored_health.overall_health is health.overall_health
        assert restored_health.enabled_underlyings == health.enabled_underlyings

        stats_json = snapshot_statistics_to_json(stats)
        restored_stats = snapshot_statistics_from_json(stats_json)
        assert restored_stats.total_tick_count == stats.total_tick_count

        # dict serializers
        restored2 = deserialize_streaming_health_report(
            serialize_streaming_health_report(health)
        )
        assert restored2.report_id == health.report_id
        restored3 = deserialize_snapshot_statistics(
            serialize_snapshot_statistics(stats)
        )
        assert restored3.total_snapshot_published_count == (
            stats.total_snapshot_published_count
        )

    def test_view_and_publish_event_serialization(self) -> None:
        events: list[Any] = []
        engine = make_engine()
        engine.add_publish_callback(events.append)
        register_chain(engine, include_future=True)
        feed_chain(engine, with_iv=True, include_future=True)
        view = engine.get_streaming_view("NIFTY")
        assert view is not None
        payload = serialize_streaming_snapshot_view(view)
        restored_view = deserialize_streaming_snapshot_view(payload)
        assert restored_view.underlying == "NIFTY"
        assert restored_view.atm_strike == view.atm_strike
        assert restored_view.expected_move is not None

        pub = next(e for e in events if e.outcome is SnapshotPublishOutcome.PUBLISHED)
        pub_payload = serialize_streaming_publish_event(pub)
        restored_pub = deserialize_streaming_publish_event(pub_payload)
        assert restored_pub.outcome is SnapshotPublishOutcome.PUBLISHED
        assert restored_pub.underlying == "NIFTY"

    def test_malformed_serialization(self) -> None:
        with pytest.raises(MarketDataStreamingSerializationError):
            streaming_health_report_from_json("{bad")
        with pytest.raises(MarketDataStreamingSerializationError):
            snapshot_statistics_from_json("{bad")
        with pytest.raises(MarketDataStreamingSerializationError):
            deserialize_streaming_health_report({"schema_version": "0.0.1"})


class TestConcurrencyAndBoundaries:
    """Thread safety and architecture boundary checks."""

    def test_concurrent_ingest_and_health(self) -> None:
        engine = make_engine(("NIFTY", "BANKNIFTY"))
        register_chain(engine, underlying="NIFTY", spot_token=1001)
        register_chain(engine, underlying="BANKNIFTY", spot_token=2001)
        feed_chain(engine, underlying="NIFTY", spot_token=1001, sequence=1)
        feed_chain(
            engine,
            underlying="BANKNIFTY",
            spot_token=2001,
            spot=52010.0,
            atm=52000.0,
            sequence=1,
        )
        barrier = threading.Barrier(4)
        errors: list[BaseException] = []

        def reader() -> None:
            try:
                barrier.wait()
                for _ in range(40):
                    report = engine.get_health()
                    assert len(report.per_underlying) == 2
                    _ = engine.get_statistics()
                    _ = engine.get_snapshot("NIFTY")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def writer() -> None:
            try:
                barrier.wait()
                for i in range(40):
                    engine.ingest_tick(
                        make_tick(
                            1001,
                            "NIFTY",
                            "INDEX",
                            last_price=24500.0 + i,
                            sequence=10 + i,
                        )
                    )
                    engine.ingest_tick(
                        make_tick(
                            2001,
                            "BANKNIFTY",
                            "INDEX",
                            last_price=52000.0 + i,
                            sequence=10 + i,
                        )
                    )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(reader) for _ in range(3)]
            futures.append(pool.submit(writer))
            for future in futures:
                future.result()
        assert not errors

    def test_no_hardcoded_instrument_tokens_in_module(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "broker" / "market_data_streaming.py"
        )
        text = source.read_text(encoding="utf-8")
        assert "256265" not in text
        assert "NSE:NIFTY 50" not in text
        assert "NSE:NIFTY BANK" not in text
        assert "kiteconnect" not in text.lower()
        assert "kite_websocket" not in text
        assert "kite_authentication" not in text
        assert not re.search(r"instrument_token\s*=\s*\d{5,}", text)

    def test_module_identity(self) -> None:
        assert MARKET_DATA_STREAMING_VERSION == "1.0.0"
        assert PRODUCER_NAME == "broker.market_data_streaming"

    def test_secondary_underlying_finnifty_midcpnifty(self) -> None:
        engine = make_engine(("FINNIFTY", "MIDCPNIFTY"))
        register_chain(engine, underlying="FINNIFTY", spot_token=5001, atm=24000.0)
        register_chain(engine, underlying="MIDCPNIFTY", spot_token=6001, atm=12000.0)
        feed_chain(
            engine,
            underlying="FINNIFTY",
            spot_token=5001,
            spot=24010.0,
            atm=24000.0,
            with_iv=True,
        )
        feed_chain(
            engine,
            underlying="MIDCPNIFTY",
            spot_token=6001,
            spot=12005.0,
            atm=12000.0,
        )
        assert engine.get_snapshot("FINNIFTY") is not None
        assert engine.get_snapshot("MIDCPNIFTY") is not None


class TestErrorHandlingEdges:
    """Additional error-handling and edge coverage."""

    def test_empty_underlying_on_tick(self) -> None:
        engine = make_engine()
        register_chain(engine)
        with pytest.raises(TickValidationError) as exc:
            engine.ingest_tick(
                TickEvent(
                    instrument_token=1001,
                    underlying="   ",
                    quote_key="X",
                    exchange="NSE",
                    tradingsymbol="X",
                    instrument_kind="INDEX",
                    last_price=100.0,
                    volume=1,
                    received_at=FIXED_NOW,
                )
            )
        assert exc.value.code == "MDS.TICK.MISSING_UNDERLYING"

    def test_invalid_oi_and_bid(self) -> None:
        engine = make_engine()
        register_chain(engine)
        with pytest.raises(TickValidationError) as exc:
            engine.ingest_tick(
                make_tick(1001, "NIFTY", "INDEX", last_price=100.0, oi=-1)
            )
        assert exc.value.code == "MDS.TICK.INVALID_OI"
        with pytest.raises(TickValidationError) as exc2:
            engine.ingest_tick(
                make_tick(
                    1001,
                    "NIFTY",
                    "INDEX",
                    last_price=100.0,
                    bid=float("inf"),
                )
            )
        assert exc2.value.code == "MDS.TICK.INVALID_QUOTE"

    def test_ambiguous_spot(self) -> None:
        events: list[Any] = []
        engine = make_engine()
        engine.add_publish_callback(events.append)
        engine.register_instruments(
            (
                spot_descriptor(1001),
                spot_descriptor(1002),
                option_descriptor(1010, "NIFTY", 24500.0, "CE"),
                option_descriptor(1011, "NIFTY", 24500.0, "PE"),
            )
        )
        engine.ingest_tick(make_tick(1001, "NIFTY", "INDEX", last_price=24500.0))
        engine.ingest_tick(make_tick(1002, "NIFTY", "INDEX", last_price=24501.0))
        engine.ingest_tick(make_tick(1010, "NIFTY", "CE", last_price=10.0))
        engine.ingest_tick(make_tick(1011, "NIFTY", "PE", last_price=10.0))
        assert any(
            e.reason_code == "MDS.SNAPSHOT.AMBIGUOUS_SPOT" for e in events
        )

    def test_stale_input_gate(self) -> None:
        times = [FIXED_NOW]

        def clock() -> datetime:
            return times[0]

        events: list[Any] = []
        engine = make_engine(clock=clock, tick_staleness_seconds=1.0)
        engine.add_publish_callback(events.append)
        register_chain(engine)
        feed_chain(engine, sequence=1, at=times[0])
        assert engine.get_snapshot("NIFTY") is not None
        # Advance clock far beyond staleness; leave other quote timestamps at FIXED_NOW
        times[0] = FIXED_NOW + timedelta(seconds=60)
        engine.ingest_tick(
            make_tick(
                1011,  # ATM CE token = spot_token + 10
                "NIFTY",
                "CE",
                last_price=121.0,
                sequence=2,
                received_at=times[0],
                exchange_timestamp=FIXED_NOW,
            )
        )
        assert any(
            e.outcome is SnapshotPublishOutcome.SKIPPED
            and e.reason_code == "MDS.SNAPSHOT.STALE_INPUT"
            for e in events
        )

    def test_unsupported_instrument_underlying_at_register(self) -> None:
        engine = make_engine(
            ("CUSTOMUL",),
            allow_experimental_underlyings=True,
        )
        # Supported path for experimental
        engine.register_instruments(
            (
                InstrumentDescriptor(
                    instrument_token=1,
                    underlying="CUSTOMUL",
                    quote_key="X",
                    exchange="NSE",
                    tradingsymbol="X",
                    instrument_kind="INDEX",
                    instrument_role=InstrumentRole.SPOT,
                ),
            )
        )
        # Non-experimental engine rejects unsupported catalog at registration
        engine2 = make_engine(("NIFTY",))
        with pytest.raises(InstrumentValidationError):
            engine2.register_instruments(
                (
                    InstrumentDescriptor(
                        instrument_token=9,
                        underlying="FOO",
                        quote_key="X",
                        exchange="NSE",
                        tradingsymbol="X",
                        instrument_kind="INDEX",
                    ),
                )
            )


class TestCoverageExpansion:
    """Additional paths for quote book, history, gates, serialization, health."""

    def test_quote_book_descriptors_and_stale_unknown(self) -> None:
        book = LatestQuoteBook(
            enabled_underlyings=("NIFTY",),
            tick_staleness_seconds=5.0,
        )
        book.register_instruments((spot_descriptor(), option_descriptor(1011, "NIFTY", 24500.0, "CE")))
        assert book.get_descriptor(1001) is not None
        assert {d.instrument_token for d in book.get_descriptors_for_underlying("NIFTY")} == {
            1001,
            1011,
        }
        assert book.is_stale(99999, now=FIXED_NOW) is True
        # Tick without prior descriptor still indexes by tick underlying
        book.update(
            make_tick(7777, "NIFTY", "INDEX", last_price=1.0, exchange_timestamp=None),
            now=FIXED_NOW,
        )
        assert book.get(7777) is not None
        assert book.is_stale(7777, now=FIXED_NOW + timedelta(seconds=6)) is True
        book.deregister_instruments((1001, 1011, 7777))
        assert book.get(1001) is None
        assert book.token_count() == 0

    def test_cache_and_history_clear_all_and_unknown(self) -> None:
        cache = SnapshotCache()
        history = SnapshotHistory(
            enabled_underlyings=("NIFTY",),
            history_ring_size=2,
        )
        engine = make_engine(history_ring_size=2)
        register_chain(engine)
        feed_chain(engine, sequence=1)
        snap = engine.get_snapshot("NIFTY")
        view = engine.get_streaming_view("NIFTY")
        assert snap is not None and view is not None
        cache.put("NIFTY", snap, view)
        cache.clear()
        assert cache.get("NIFTY") is None
        history.append("NIFTY", snap)
        history.append("BANKNIFTY", snap)  # lazy-creates unknown underlying ring
        assert history.size("BANKNIFTY") == 1
        assert history.get("MISSING") == ()
        assert history.get("NIFTY", limit=0) == ()
        assert history.size("MISSING") == 0
        history.clear("NIFTY")
        assert history.size("NIFTY") == 0
        history.clear()
        assert history.size("BANKNIFTY") == 0

    def test_insufficient_pairs_and_missing_quote_gates(self) -> None:
        events: list[Any] = []
        engine = make_engine(min_complete_pairs=3)
        engine.add_publish_callback(events.append)
        register_chain(engine)
        feed_chain(engine)
        assert any(
            e.reason_code == "MDS.SNAPSHOT.INSUFFICIENT_PAIRS" for e in events
        )

        events2: list[Any] = []
        engine2 = make_engine(max_missing_quote_ratio=0.0)
        engine2.add_publish_callback(events2.append)
        register_chain(engine2)
        # Zero bid/ask on one option → missing quote for canonical validation path
        engine2.ingest_tick(
            make_tick(1001, "NIFTY", "INDEX", last_price=24512.0, oi=None)
        )
        engine2.ingest_tick(
            make_tick(1011, "NIFTY", "CE", last_price=120.0, bid=0.0, ask=0.0)
        )
        engine2.ingest_tick(make_tick(1012, "NIFTY", "PE", last_price=110.0))
        engine2.ingest_tick(make_tick(1013, "NIFTY", "CE", last_price=95.0))
        engine2.ingest_tick(make_tick(1014, "NIFTY", "PE", last_price=130.0))
        assert any(
            e.reason_code
            in {
                "MDS.SNAPSHOT.INSUFFICIENT_COVERAGE",
                "MDS.SNAPSHOT.BUILD_FAILED",
                "MDS.SNAPSHOT.CANONICAL_INVALID",
            }
            for e in events2
        )

    def test_nearest_expiry_and_future_selection(self) -> None:
        engine = make_engine()
        engine.register_instruments(
            (
                spot_descriptor(1001),
                option_descriptor(1011, "NIFTY", 24500.0, "CE", expiry="2026-07-01"),
                option_descriptor(1012, "NIFTY", 24500.0, "PE", expiry="2026-07-01"),
                option_descriptor(1013, "NIFTY", 24500.0, "CE", expiry="2026-08-07"),
                option_descriptor(1014, "NIFTY", 24500.0, "PE", expiry="2026-08-07"),
                future_descriptor(1020, expiry="2026-09-01"),
                future_descriptor(1021, expiry="2026-08-07"),
            )
        )
        for token, kind, price in (
            (1001, "INDEX", 24512.0),
            (1011, "CE", 10.0),
            (1012, "PE", 10.0),
            (1013, "CE", 120.0),
            (1014, "PE", 110.0),
            (1020, "FUT", 24540.0),
            (1021, "FUT", 24520.0),
        ):
            engine.ingest_tick(
                make_tick(token, "NIFTY", kind, last_price=price, oi=None if kind == "INDEX" else 1000)
            )
        view = engine.get_streaming_view("NIFTY")
        assert view is not None
        assert view.snapshot.option_chain.metadata.expiry == "2026-08-07"
        assert view.futures is not None
        assert view.futures.expiry == "2026-08-07"

    def test_past_only_expiry_falls_back(self) -> None:
        engine = make_engine()
        engine.register_instruments(
            (
                spot_descriptor(1001),
                option_descriptor(1011, "NIFTY", 24500.0, "CE", expiry="2020-01-01"),
                option_descriptor(1012, "NIFTY", 24500.0, "PE", expiry="2020-01-01"),
                option_descriptor(1013, "NIFTY", 24550.0, "CE", expiry="2020-01-01"),
                option_descriptor(1014, "NIFTY", 24550.0, "PE", expiry="2020-01-01"),
            )
        )
        feed_chain(engine)
        snap = engine.get_snapshot("NIFTY")
        assert snap is not None
        assert snap.option_chain.metadata.expiry == "2020-01-01"

    def test_event_bus_skip_and_fail_topics(self) -> None:
        bus = EventBus(EventBusPolicy())
        skipped: list[Any] = []
        failed: list[Any] = []
        bus.subscribe(TOPIC_SNAPSHOT_SKIPPED, lambda env: skipped.append(env.payload))
        bus.subscribe(TOPIC_SNAPSHOT_FAILED, lambda env: failed.append(env.payload))
        engine = make_engine(
            event_bus=bus,
            publish_events=True,
            require_futures_for_snapshot=True,
        )
        register_chain(engine, include_future=False)
        feed_chain(engine, include_future=False)
        assert skipped
        assert skipped[-1].reason_code == "MDS.SNAPSHOT.FUTURES_REQUIRED"

        engine2 = make_engine(event_bus=bus, publish_events=True)
        engine2.register_instruments(
            (
                option_descriptor(2010, "NIFTY", 24500.0, "CE"),
                option_descriptor(2011, "NIFTY", 24500.0, "PE"),
            )
        )
        engine2.ingest_tick(make_tick(2010, "NIFTY", "CE", last_price=10.0))
        assert failed
        assert failed[-1].reason_code == "MDS.SNAPSHOT.MISSING_SPOT"

    def test_event_bus_isolation_on_publish_failure(self) -> None:
        class BoomBus:
            def publish(self, *args: Any, **kwargs: Any) -> None:
                raise RuntimeError("bus down")

        engine = make_engine(event_bus=BoomBus(), publish_events=True)  # type: ignore[arg-type]
        register_chain(engine)
        feed_chain(engine)
        assert engine.get_snapshot("NIFTY") is not None

    def test_serialization_error_paths(self) -> None:
        with pytest.raises(MarketDataStreamingSerializationError):
            deserialize_snapshot_statistics({"schema_version": "2.0.0", "as_of": "x"})
        with pytest.raises(MarketDataStreamingSerializationError):
            deserialize_snapshot_statistics(
                {"schema_version": "1.0.0", "as_of": "not-a-date"}
            )
        with pytest.raises(MarketDataStreamingSerializationError):
            deserialize_streaming_health_report(
                {"schema_version": 1, "report_id": "x"}
            )
        with pytest.raises(MarketDataStreamingSerializationError):
            deserialize_streaming_snapshot_view({"schema_version": "1.0.0"})
        with pytest.raises(MarketDataStreamingSerializationError):
            deserialize_streaming_publish_event({"schema_version": "1.0.0"})
        with pytest.raises(MarketDataStreamingSerializationError):
            snapshot_statistics_from_json("{bad")
        # Naive timestamp rejected
        with pytest.raises(MarketDataStreamingSerializationError):
            deserialize_snapshot_statistics(
                {
                    "schema_version": "1.0.0",
                    "as_of": "2026-08-05T05:00:00",
                    "total_tick_count": 0,
                    "total_rejected_tick_count": 0,
                    "unattributed_tick_count": 0,
                    "total_snapshot_published_count": 0,
                    "total_snapshot_skipped_count": 0,
                    "total_snapshot_failed_count": 0,
                    "enabled_underlyings": [],
                    "per_underlying": [],
                }
            )

    def test_publish_event_round_trip_without_snapshot(self) -> None:
        from broker.market_data_streaming import StreamingPublishEvent

        event = StreamingPublishEvent(
            event_id="e1",
            underlying="NIFTY",
            outcome=SnapshotPublishOutcome.SKIPPED,
            published_at=FIXED_NOW,
            sequence=1,
            reason_code="MDS.SNAPSHOT.FUTURES_REQUIRED",
            reason_message="missing",
        )
        payload = serialize_streaming_publish_event(event)
        restored = deserialize_streaming_publish_event(payload)
        assert restored.snapshot is None
        assert restored.view is None
        assert restored.reason_code == "MDS.SNAPSHOT.FUTURES_REQUIRED"

    def test_health_stopped_and_missing_spot_issue(self) -> None:
        engine = make_engine(("NIFTY", "BANKNIFTY"))
        engine.register_instruments(
            (
                option_descriptor(1011, "NIFTY", 24500.0, "CE"),
                option_descriptor(1012, "NIFTY", 24500.0, "PE"),
            )
        )
        health = engine.get_health()
        nifty = next(h for h in health.per_underlying if h.underlying == "NIFTY")
        assert any(
            i.issue_code == "MDS.HEALTH.MISSING_SPOT_INSTRUMENT" for i in nifty.issues
        )
        engine.stop()
        stopped = engine.get_health()
        assert stopped.lifecycle_state is StreamingLifecycleState.STOPPED
        assert stopped.overall_health is StreamingHealthStatus.UNKNOWN

    def test_expected_move_disabled(self) -> None:
        engine = make_engine(expected_move_enabled=False)
        register_chain(engine)
        feed_chain(engine, with_iv=True)
        view = engine.get_streaming_view("NIFTY")
        assert view is not None
        assert view.expected_move is None
        assert view.atm_iv is not None

    def test_future_without_expiry_selected(self) -> None:
        engine = make_engine()
        engine.register_instruments(
            (
                spot_descriptor(1001),
                option_descriptor(1011, "NIFTY", 24500.0, "CE"),
                option_descriptor(1012, "NIFTY", 24500.0, "PE"),
                option_descriptor(1013, "NIFTY", 24550.0, "CE"),
                option_descriptor(1014, "NIFTY", 24550.0, "PE"),
                InstrumentDescriptor(
                    instrument_token=1020,
                    underlying="NIFTY",
                    quote_key="NFO:NIFTY-FUT",
                    exchange="NFO",
                    tradingsymbol="NIFTY-FUT",
                    instrument_kind="FUT",
                    instrument_role=InstrumentRole.FUTURE,
                    expiry=None,
                    lot_size=50,
                ),
            )
        )
        feed_chain(engine, include_future=False)
        engine.ingest_tick(make_tick(1020, "NIFTY", "FUT", last_price=24530.0))
        view = engine.get_streaming_view("NIFTY")
        assert view is not None
        assert view.futures is not None

    def test_deterministic_repeated_feed(self) -> None:
        engine_a = make_engine()
        engine_b = make_engine()
        register_chain(engine_a)
        register_chain(engine_b)
        feed_chain(engine_a, with_iv=True, include_future=True)
        feed_chain(engine_b, with_iv=True, include_future=True)
        snap_a = engine_a.get_snapshot("NIFTY")
        snap_b = engine_b.get_snapshot("NIFTY")
        assert snap_a is not None and snap_b is not None
        assert snap_a.option_chain.metadata.atm_strike == snap_b.option_chain.metadata.atm_strike
        assert snap_a.underlying.last_price == snap_b.underlying.last_price
        assert len(snap_a.option_chain.contracts) == len(snap_b.option_chain.contracts)
