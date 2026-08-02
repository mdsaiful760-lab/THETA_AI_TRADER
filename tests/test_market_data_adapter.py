"""Unit tests for market_data.market_data_adapter."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from market_data.market_data_adapter import (
    MARKET_DATA_ADAPTER_VERSION,
    AdapterBuildRequest,
    AdapterPermission,
    AdapterPolicy,
    AdapterRejectionReason,
    MarketDataAdapter,
    NormalizedGreeks,
)
from market_data.market_snapshot import (
    OptionType,
    SnapshotSource,
    from_legacy_option_snapshot,
    is_live_trade_ready,
    validate_market_snapshot,
)

IST = ZoneInfo("Asia/Kolkata")


def fixed_as_of() -> datetime:
    return datetime(2026, 8, 3, 10, 15, 0, tzinfo=IST)


def fixed_captured_at() -> datetime:
    return datetime(2026, 8, 3, 10, 15, 1, 42000, tzinfo=IST)


def kite_instrument_factory(
    *,
    symbol: str,
    strike: float,
    option_type: str,
    expiry: date | str = date(2026, 8, 7),
    underlying: str = "NIFTY",
    exchange: str = "NFO",
    lot_size: int = 75,
    instrument_token: int = 100001,
) -> dict[str, object]:
    return {
        "instrument_token": instrument_token,
        "exchange_token": 200001,
        "tradingsymbol": symbol,
        "name": underlying,
        "last_price": 0.0,
        "expiry": expiry,
        "strike": strike,
        "tick_size": 0.05,
        "lot_size": lot_size,
        "instrument_type": option_type,
        "segment": f"{exchange}-OPT",
        "exchange": exchange,
    }


def kite_quote_factory(
    *,
    ltp: float = 100.0,
    bid: float = 99.5,
    ask: float = 100.5,
    volume: int = 100000,
    oi: int = 200000,
    timestamp: datetime | None = None,
) -> dict[str, object]:
    ts = timestamp or fixed_as_of()
    return {
        "instrument_token": 100001,
        "timestamp": ts,
        "last_trade_time": ts,
        "last_price": ltp,
        "last_quantity": 75,
        "buy_quantity": 10000,
        "sell_quantity": 9000,
        "volume": volume,
        "average_price": 98.75,
        "oi": oi,
        "oi_day_high": oi + 10000,
        "oi_day_low": max(0, oi - 10000),
        "depth": {
            "buy": [
                {"quantity": 750, "price": bid - 0.50, "orders": 2},
                {"quantity": 1500, "price": bid, "orders": 5},
            ],
            "sell": [
                {"quantity": 1500, "price": ask, "orders": 4},
                {"quantity": 500, "price": ask + 1.00, "orders": 2},
            ],
        },
    }


def kite_index_quote_factory(
    *,
    last_price: float = 24296.75,
    timestamp: datetime | None = None,
) -> dict[str, object]:
    ts = timestamp or fixed_as_of()
    return {
        "last_price": last_price,
        "timestamp": ts,
        "volume": 0,
        "ohlc": {
            "open": 24200.0,
            "high": 24350.0,
            "low": 24180.0,
            "close": 24210.5,
        },
    }


def standard_nifty_chain(*, strikes_each_side: int = 2) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    spot = 24300.0
    atm = 24300.0
    step = 50.0
    instruments: list[dict[str, object]] = []
    quotes: dict[str, dict[str, object]] = {}
    token = 1000

    for offset in range(-strikes_each_side, strikes_each_side + 1):
        strike = atm + offset * step
        for option_type in ("CE", "PE"):
            symbol = f"NIFTY26807{int(strike)}{option_type}"
            token += 1
            instruments.append(
                kite_instrument_factory(
                    symbol=symbol,
                    strike=strike,
                    option_type=option_type,
                    instrument_token=token,
                )
            )
            key = f"NFO:{symbol}"
            quotes[key] = kite_quote_factory(
                ltp=abs(spot - strike) / 10.0 + 50.0,
                bid=50.0,
                ask=50.5,
                timestamp=fixed_as_of(),
            )
    return instruments, quotes


class TestSymbolNormalization:
    def test_build_quote_key_uppercases(self) -> None:
        adapter = MarketDataAdapter()
        assert adapter.build_quote_key("nfo", "nifty2680724300ce") == "NFO:NIFTY2680724300CE"

    def test_build_quote_key_returns_none_for_empty(self) -> None:
        adapter = MarketDataAdapter()
        assert adapter.build_quote_key("", "SYMBOL") is None


class TestExpiryNormalization:
    def test_normalize_instrument_accepts_date_object(self) -> None:
        adapter = MarketDataAdapter()
        raw = kite_instrument_factory(
            symbol="NIFTY2680724300CE",
            strike=24300.0,
            option_type="CE",
            expiry=date(2026, 8, 7),
        )
        result = adapter.normalize_instrument(raw)
        assert result.valid is True
        assert result.value is not None
        assert result.value.expiry == "2026-08-07"

    def test_get_nearest_expiry_excludes_past(self) -> None:
        adapter = MarketDataAdapter()
        instruments = [
            kite_instrument_factory(
                symbol="OLD",
                strike=24000.0,
                option_type="CE",
                expiry=date(2020, 1, 1),
            ),
            kite_instrument_factory(
                symbol="NEW",
                strike=24300.0,
                option_type="CE",
                expiry=date(2026, 8, 7),
            ),
        ]
        assert adapter.get_nearest_expiry(
            instruments,
            "NIFTY",
            reference_date=date(2026, 8, 3),
        ) == "2026-08-07"


class TestStrikeNormalization:
    def test_get_atm_strike_selects_nearest(self) -> None:
        adapter = MarketDataAdapter()
        instruments, _ = standard_nifty_chain(strikes_each_side=1)
        atm = adapter.get_atm_strike(
            instruments,
            "NIFTY",
            "2026-08-07",
            24296.75,
        )
        assert atm == 24300.0

    def test_detect_strike_step(self) -> None:
        adapter = MarketDataAdapter()
        assert adapter.detect_strike_step((24200.0, 24250.0, 24300.0)) == 50.0

    def test_get_nearby_strikes_window(self) -> None:
        adapter = MarketDataAdapter()
        strikes = tuple(24200.0 + index * 50.0 for index in range(5))
        nearby = adapter.get_nearby_strikes(strikes, 24300.0, strikes_each_side=1)
        assert nearby == (24250.0, 24300.0, 24350.0)


class TestTimestampNormalization:
    def test_naive_datetime_becomes_timezone_aware(self) -> None:
        adapter = MarketDataAdapter()
        quote = kite_quote_factory(timestamp=datetime(2026, 8, 3, 10, 15, 0))
        result = adapter.normalize_quote(quote)
        assert result.valid is True
        assert result.value is not None
        assert result.value.quote_timestamp is not None
        assert result.value.quote_timestamp.tzinfo is not None

    def test_unparseable_timestamp_yields_warning(self) -> None:
        adapter = MarketDataAdapter()
        quote = kite_quote_factory()
        quote["timestamp"] = "not-a-timestamp"
        result = adapter.normalize_quote(quote)
        assert any("TIMESTAMP.UNPARSEABLE" in warning.code for warning in result.warnings)


class TestQuoteNormalization:
    def test_valid_quote_normalization(self) -> None:
        adapter = MarketDataAdapter()
        result = adapter.normalize_quote(kite_quote_factory())
        assert result.valid is True
        assert result.value is not None
        assert result.value.ltp == 100.0
        assert result.value.bid == 99.5
        assert result.value.ask == 100.5

    def test_missing_bid_ask_lenient_defaults_to_zero(self) -> None:
        adapter = MarketDataAdapter()
        quote = kite_quote_factory()
        quote["depth"] = {"buy": [], "sell": []}
        result = adapter.normalize_quote(quote)
        assert result.valid is True
        assert result.value is not None
        assert result.value.bid == 0.0
        assert result.value.ask == 0.0

    def test_missing_bid_ask_strict_rejects(self) -> None:
        adapter = MarketDataAdapter(policy=AdapterPolicy(strict=True))
        quote = kite_quote_factory()
        quote["depth"] = {"buy": [], "sell": []}
        result = adapter.normalize_quote(quote)
        assert result.valid is False

    def test_negative_volume_rejected(self) -> None:
        adapter = MarketDataAdapter()
        quote = kite_quote_factory(volume=-1)
        result = adapter.normalize_quote(quote)
        assert result.valid is False


class TestInstrumentNormalization:
    def test_valid_instrument(self) -> None:
        adapter = MarketDataAdapter()
        result = adapter.normalize_instrument(
            kite_instrument_factory(
                symbol="NIFTY2680724300CE",
                strike=24300.0,
                option_type="CE",
            )
        )
        assert result.valid is True
        assert result.value is not None
        assert result.value.option_type == OptionType.CE

    def test_invalid_exchange_rejected(self) -> None:
        adapter = MarketDataAdapter()
        raw = kite_instrument_factory(
            symbol="BAD",
            strike=24300.0,
            option_type="CE",
            exchange="INVALID",
        )
        result = adapter.normalize_instrument(raw)
        assert result.valid is False


class TestIndexQuoteNormalization:
    def test_ohlc_and_change_calculation(self) -> None:
        adapter = MarketDataAdapter()
        result = adapter.normalize_index_quote(
            kite_index_quote_factory(),
            symbol="NIFTY 50",
            exchange="NSE",
            quote_key="NSE:NIFTY 50",
        )
        assert result.valid is True
        assert result.value is not None
        assert result.value.open == 24200.0
        assert result.value.previous_close == 24210.5
        assert result.value.change == pytest.approx(86.25)
        expected_change_percent = (86.25 / 24210.5) * 100.0
        assert result.value.change_percent == pytest.approx(expected_change_percent)


class TestGreeksNormalization:
    def test_invalid_greeks_return_none_fields(self) -> None:
        adapter = MarketDataAdapter()
        greeks = adapter.normalize_greeks({"delta": "bad", "iv": float("nan")})
        assert greeks.delta is None
        assert greeks.iv is None

    def test_greeks_lookup_by_quote_key(self) -> None:
        adapter = MarketDataAdapter()
        instruments, quotes = standard_nifty_chain(strikes_each_side=0)
        greeks_map = {
            "NFO:NIFTY2680724300CE": {
                "delta": 0.5,
                "iv": 13.2,
                "gamma": 0.01,
                "theta": -5.0,
                "vega": 2.0,
            }
        }
        chain = adapter.build_option_chain(
            instruments,
            quotes,
            "NIFTY",
            expiry="2026-08-07",
            greeks_map=greeks_map,
        )
        ce = next(c for c in chain.contracts if c.option_type == OptionType.CE)
        assert ce.delta == 0.5
        assert ce.iv == 13.2


class TestOptionChainBuild:
    def test_build_option_chain_filters_and_sorts(self) -> None:
        adapter = MarketDataAdapter()
        instruments, quotes = standard_nifty_chain(strikes_each_side=1)
        result = adapter.build_option_chain(
            instruments,
            quotes,
            "NIFTY",
            expiry="2026-08-07",
        )
        assert result.normalized_count == 6
        strikes = [contract.strike for contract in result.contracts]
        assert strikes == sorted(strikes)

    def test_missing_quote_recorded_as_rejection(self) -> None:
        adapter = MarketDataAdapter()
        instruments, quotes = standard_nifty_chain(strikes_each_side=0)
        del quotes["NFO:NIFTY2680724300PE"]
        result = adapter.build_option_chain(
            instruments,
            quotes,
            "NIFTY",
            expiry="2026-08-07",
        )
        assert result.normalized_count == 1
        assert any(
            rejection.reason == AdapterRejectionReason.QUOTE_NOT_FOUND
            for rejection in result.rejections
        )


class TestSnapshotBuild:
    def test_build_market_snapshot_from_kite_allow(self) -> None:
        adapter = MarketDataAdapter()
        instruments, quotes = standard_nifty_chain(strikes_each_side=2)
        request = AdapterBuildRequest(
            underlying="NIFTY",
            as_of=fixed_as_of(),
            captured_at=fixed_captured_at(),
            expiry="2026-08-07",
            strikes_each_side=2,
        )
        result = adapter.build_market_snapshot_from_kite(
            kite_instruments=instruments,
            kite_quotes=quotes,
            kite_spot_quote=kite_index_quote_factory(),
            request=request,
        )
        assert result.permission == AdapterPermission.ALLOW
        assert result.adapter_allowed is True
        assert result.broker_order_allowed is False
        assert result.snapshot is not None
        assert result.snapshot.provenance.adapter_name == "market_data_adapter"
        assert result.snapshot.provenance.adapter_version == MARKET_DATA_ADAPTER_VERSION
        validation = validate_market_snapshot(result.snapshot)
        assert validation.validation_status.value in {"VALID", "PARTIAL"}

    def test_build_market_snapshot_partial_on_rejections(self) -> None:
        adapter = MarketDataAdapter()
        instruments, quotes = standard_nifty_chain(strikes_each_side=2)
        del quotes["NFO:NIFTY2680724200PE"]
        request = AdapterBuildRequest(
            underlying="NIFTY",
            as_of=fixed_as_of(),
            captured_at=fixed_captured_at(),
            expiry="2026-08-07",
            strikes_each_side=2,
        )
        result = adapter.build_market_snapshot_from_kite(
            kite_instruments=instruments,
            kite_quotes=quotes,
            kite_spot_quote=kite_index_quote_factory(),
            request=request,
        )
        assert result.permission == AdapterPermission.PARTIAL
        assert result.snapshot is not None
        assert result.rejected_count == 1

    def test_build_market_snapshot_blocks_on_invalid_spot(self) -> None:
        adapter = MarketDataAdapter()
        instruments, quotes = standard_nifty_chain(strikes_each_side=1)
        request = AdapterBuildRequest(
            underlying="NIFTY",
            as_of=fixed_as_of(),
            expiry="2026-08-07",
        )
        result = adapter.build_market_snapshot_from_kite(
            kite_instruments=instruments,
            kite_quotes=quotes,
            kite_spot_quote={"last_price": 0},
            request=request,
        )
        assert result.permission == AdapterPermission.BLOCK
        assert result.snapshot is None

    def test_live_trade_ready_after_successful_build(self) -> None:
        adapter = MarketDataAdapter()
        instruments, quotes = standard_nifty_chain(strikes_each_side=1)
        request = AdapterBuildRequest(
            underlying="NIFTY",
            as_of=fixed_as_of(),
            captured_at=fixed_captured_at(),
            expiry="2026-08-07",
            strikes_each_side=1,
        )
        result = adapter.build_market_snapshot_from_kite(
            kite_instruments=instruments,
            kite_quotes=quotes,
            kite_spot_quote=kite_index_quote_factory(),
            request=request,
        )
        assert result.snapshot is not None
        assert is_live_trade_ready(result.snapshot) is True


class TestInvalidPayloadHandling:
    def test_non_mapping_instrument_rejected(self) -> None:
        adapter = MarketDataAdapter()
        result = adapter.normalize_instrument(["not", "a", "dict"])
        assert result.valid is False

    def test_invalid_request_blocks_snapshot(self) -> None:
        adapter = MarketDataAdapter()
        instruments, quotes = standard_nifty_chain(strikes_each_side=0)
        request = AdapterBuildRequest(
            underlying="",
            as_of=fixed_as_of(),
        )
        result = adapter.build_market_snapshot_from_kite(
            kite_instruments=instruments,
            kite_quotes=quotes,
            kite_spot_quote=kite_index_quote_factory(),
            request=request,
        )
        assert result.permission == AdapterPermission.BLOCK


class TestLegacyPayloadMigration:
    def test_legacy_option_snapshot_dict_maps_via_snapshot_module(self) -> None:
        legacy = {
            "timestamp": "2026-07-30T13:42:17.653767+05:30",
            "spot": 24296.75,
            "expiry": "2026-08-04",
            "atm": 24300.0,
            "strike_step": 50.0,
            "options": [
                {
                    "strike": 24300.0,
                    "option_type": "CE",
                    "symbol": "NIFTY2680424300CE",
                    "price": 110.0,
                    "oi": 8490430,
                    "volume": 141895845,
                    "bid": 109.65,
                    "ask": 109.9,
                },
                {
                    "strike": 24300.0,
                    "option_type": "PE",
                    "symbol": "NIFTY2680424300PE",
                    "price": 115.0,
                    "oi": 7525375,
                    "volume": 105397890,
                    "bid": 115.0,
                    "ask": 115.15,
                },
            ],
        }
        snapshot = from_legacy_option_snapshot(legacy)
        assert snapshot.underlying.last_price == 24296.75
        assert len(snapshot.option_chain.contracts) == 2

    def test_kite_payload_produces_same_contract_fields_as_legacy(self) -> None:
        adapter = MarketDataAdapter()
        instrument = kite_instrument_factory(
            symbol="NIFTY2680424300CE",
            strike=24300.0,
            option_type="CE",
            expiry=date(2026, 8, 4),
        )
        quote = kite_quote_factory(ltp=110.0, bid=109.65, ask=109.9, oi=8490430, volume=141895845)
        contract = adapter.build_contract(instrument, quote).value
        assert contract is not None
        assert contract.strike == 24300.0
        assert contract.ltp == 110.0
        assert contract.open_interest == 8490430


class TestDeterministicBehaviour:
    def test_identical_inputs_produce_equal_snapshots(self) -> None:
        adapter = MarketDataAdapter()
        instruments, quotes = standard_nifty_chain(strikes_each_side=1)
        request = AdapterBuildRequest(
            underlying="NIFTY",
            as_of=fixed_as_of(),
            captured_at=fixed_captured_at(),
            expiry="2026-08-07",
            strikes_each_side=1,
            correlation_id="deterministic-run",
        )

        first = adapter.build_market_snapshot_from_kite(
            kite_instruments=instruments,
            kite_quotes=quotes,
            kite_spot_quote=kite_index_quote_factory(),
            request=request,
        )
        second = adapter.build_market_snapshot_from_kite(
            kite_instruments=instruments,
            kite_quotes=quotes,
            kite_spot_quote=kite_index_quote_factory(),
            request=request,
        )
        assert first.snapshot is not None
        assert second.snapshot is not None
        assert first.snapshot.underlying == second.snapshot.underlying
        assert first.snapshot.option_chain == second.snapshot.option_chain


class TestThreadSafety:
    def test_parallel_normalization(self) -> None:
        adapter = MarketDataAdapter()
        instruments, quotes = standard_nifty_chain(strikes_each_side=1)
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait()
            result = adapter.build_option_chain(
                instruments,
                quotes,
                "NIFTY",
                expiry="2026-08-07",
            )
            assert result.normalized_count == 6

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker) for _ in range(8)]
            for future in futures:
                future.result()


class TestPerformanceSmoke:
    def test_full_snapshot_build_under_threshold(self) -> None:
        adapter = MarketDataAdapter()
        instruments, quotes = standard_nifty_chain(strikes_each_side=10)
        request = AdapterBuildRequest(
            underlying="NIFTY",
            as_of=fixed_as_of(),
            captured_at=fixed_captured_at(),
            expiry="2026-08-07",
            strikes_each_side=10,
        )
        start = time.perf_counter()
        result = adapter.build_market_snapshot_from_kite(
            kite_instruments=instruments,
            kite_quotes=quotes,
            kite_spot_quote=kite_index_quote_factory(),
            request=request,
        )
        elapsed = time.perf_counter() - start
        assert result.snapshot is not None
        assert elapsed < 1.0


class TestAdapterPolicy:
    def test_invalid_policy_raises(self) -> None:
        with pytest.raises(Exception):
            AdapterPolicy(minimum_contracts=0)

    def test_build_contract_with_prebuilt_normalized_types(self) -> None:
        adapter = MarketDataAdapter()
        instrument = adapter.normalize_instrument(
            kite_instrument_factory(
                symbol="NIFTY2680724300CE",
                strike=24300.0,
                option_type="CE",
            )
        ).value
        quote = adapter.normalize_quote(kite_quote_factory()).value
        assert instrument is not None
        assert quote is not None
        result = adapter.build_contract(
            instrument,
            quote,
            NormalizedGreeks(delta=0.4),
        )
        assert result.valid is True
        assert result.value is not None
        assert result.value.delta == 0.4
