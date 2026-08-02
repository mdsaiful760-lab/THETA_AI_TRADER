"""Unit tests for market_data.market_snapshot."""

from __future__ import annotations

import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from market_data.market_snapshot import (
    MARKET_SNAPSHOT_SCHEMA_VERSION,
    MarketSnapshot,
    OptionChainMetadata,
    OptionChainSnapshot,
    OptionContractSnapshot,
    OptionType,
    SnapshotBuildError,
    SnapshotFreshnessPolicy,
    SnapshotFreshnessStatus,
    SnapshotProvenance,
    SnapshotQuality,
    SnapshotSource,
    SnapshotValidationError,
    SnapshotValidationStatus,
    UnderlyingSnapshot,
    ValidationPolicy,
    VolatilitySnapshot,
    build_market_snapshot,
    evaluate_snapshot_freshness,
    from_dict,
    from_json,
    from_legacy_option_snapshot,
    is_live_trade_ready,
    to_dict,
    to_json,
    validate_market_snapshot,
    with_freshness,
)

IST = ZoneInfo("Asia/Kolkata")


def fixed_as_of() -> datetime:
    """Monday during regular NSE session."""
    return datetime(2026, 8, 3, 10, 15, 0, tzinfo=IST)


def fixed_captured_at() -> datetime:
    return datetime(2026, 8, 3, 10, 15, 1, 42000, tzinfo=IST)


def weekend_reference() -> datetime:
    """Sunday outside regular NSE session."""
    return datetime(2026, 8, 2, 10, 15, 0, tzinfo=IST)


def make_contract(
    *,
    strike: float = 24300.0,
    option_type: OptionType = OptionType.CE,
    tradingsymbol: str | None = None,
    bid: float = 109.65,
    ask: float = 109.9,
    ltp: float = 110.0,
    quote_timestamp: datetime | None = None,
) -> OptionContractSnapshot:
    suffix = option_type.value
    return OptionContractSnapshot(
        underlying="NIFTY",
        exchange="NFO",
        tradingsymbol=tradingsymbol or f"NIFTY2680724300{suffix}",
        expiry="2026-08-07",
        strike=strike,
        option_type=option_type,
        lot_size=75,
        ltp=ltp,
        bid=bid,
        ask=ask,
        volume=1000,
        open_interest=10000,
        quote_timestamp=quote_timestamp or fixed_as_of(),
    )


def make_underlying(*, last_price: float = 24296.75) -> UnderlyingSnapshot:
    return UnderlyingSnapshot(
        symbol="NIFTY 50",
        exchange="NSE",
        quote_key="NSE:NIFTY 50",
        last_price=last_price,
        quote_timestamp=fixed_as_of(),
    )


def minimal_valid_snapshot(*, snapshot_id: str = "test-snapshot-001") -> MarketSnapshot:
    contracts = (
        make_contract(strike=24300.0, option_type=OptionType.CE),
        make_contract(
            strike=24300.0,
            option_type=OptionType.PE,
            tradingsymbol="NIFTY2680724300PE",
            ltp=115.0,
            bid=115.0,
            ask=115.15,
        ),
    )
    return build_market_snapshot(
        underlying=make_underlying(),
        contracts=contracts,
        underlying_symbol="NIFTY",
        exchange="NFO",
        expiry="2026-08-07",
        atm_strike=24300.0,
        strike_step=50.0,
        strike_window_strikes=1,
        minimum_strike=24300.0,
        maximum_strike=24300.0,
        lot_size=75,
        as_of=fixed_as_of(),
        captured_at=fixed_captured_at(),
        snapshot_id=snapshot_id,
        reference_time=fixed_captured_at(),
    )


def full_nifty_contracts() -> tuple[OptionContractSnapshot, ...]:
    contracts: list[OptionContractSnapshot] = []
    spot = 24296.75
    atm = 24300.0
    strike_step = 50.0
    for offset in range(-10, 11):
        strike = atm + offset * strike_step
        for option_type in (OptionType.CE, OptionType.PE):
            contracts.append(
                make_contract(
                    strike=strike,
                    option_type=option_type,
                    tradingsymbol=f"NIFTY26807{int(strike)}{option_type.value}",
                    ltp=abs(spot - strike) / 10.0 + 50.0,
                    bid=50.0,
                    ask=50.5,
                )
            )
    return tuple(contracts)


def full_nifty_snapshot() -> MarketSnapshot:
    return build_market_snapshot(
        underlying=make_underlying(),
        contracts=full_nifty_contracts(),
        underlying_symbol="NIFTY",
        exchange="NFO",
        expiry="2026-08-07",
        atm_strike=24300.0,
        strike_step=50.0,
        strike_window_strikes=10,
        minimum_strike=23800.0,
        maximum_strike=24800.0,
        lot_size=75,
        as_of=fixed_as_of(),
        captured_at=fixed_captured_at(),
        snapshot_id="full-nifty-snapshot",
        volatility=VolatilitySnapshot(
            symbol="INDIA VIX",
            exchange="NSE",
            quote_key="NSE:INDIA VIX",
            last_price=13.24,
            quote_timestamp=fixed_as_of(),
        ),
        reference_time=fixed_captured_at(),
    )


def legacy_option_snapshot_dict() -> dict[str, object]:
    return {
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


class TestConstruction:
    def test_build_produces_immutable_sorted_snapshot(self) -> None:
        snapshot = minimal_valid_snapshot()
        assert isinstance(snapshot, MarketSnapshot)
        assert snapshot.option_chain.metadata.contract_count == 2
        assert snapshot.option_chain.metadata.complete_pairs == 1
        assert snapshot.option_chain.contracts[0].option_type == OptionType.CE
        assert snapshot.option_chain.contracts[1].option_type == OptionType.PE

    def test_build_sorts_unsorted_contract_input(self) -> None:
        contracts = (
            make_contract(strike=24300.0, option_type=OptionType.PE, tradingsymbol="PE"),
            make_contract(strike=24250.0, option_type=OptionType.CE, tradingsymbol="CE"),
        )
        snapshot = build_market_snapshot(
            underlying=make_underlying(last_price=24250.0),
            contracts=contracts,
            underlying_symbol="NIFTY",
            exchange="NFO",
            expiry="2026-08-07",
            atm_strike=24250.0,
            strike_step=50.0,
            strike_window_strikes=1,
            minimum_strike=24250.0,
            maximum_strike=24300.0,
            lot_size=75,
            as_of=fixed_as_of(),
            captured_at=fixed_captured_at(),
            reference_time=fixed_captured_at(),
        )
        assert snapshot.option_chain.contracts[0].strike == 24250.0
        assert snapshot.option_chain.contracts[1].strike == 24300.0

    def test_build_raises_on_empty_contracts(self) -> None:
        with pytest.raises(SnapshotBuildError):
            build_market_snapshot(
                underlying=make_underlying(),
                contracts=(),
                underlying_symbol="NIFTY",
                exchange="NFO",
                expiry="2026-08-07",
                atm_strike=24300.0,
                strike_step=50.0,
                strike_window_strikes=1,
                minimum_strike=24300.0,
                maximum_strike=24300.0,
                lot_size=75,
                as_of=fixed_as_of(),
            )

    def test_build_raises_on_invalid_spot(self) -> None:
        with pytest.raises(SnapshotBuildError):
            build_market_snapshot(
                underlying=make_underlying(last_price=0.0),
                contracts=(make_contract(),),
                underlying_symbol="NIFTY",
                exchange="NFO",
                expiry="2026-08-07",
                atm_strike=24300.0,
                strike_step=50.0,
                strike_window_strikes=1,
                minimum_strike=24300.0,
                maximum_strike=24300.0,
                lot_size=75,
                as_of=fixed_as_of(),
            )

    def test_deterministic_equality_for_same_inputs_except_snapshot_id(self) -> None:
        first = minimal_valid_snapshot(snapshot_id="id-1")
        second = minimal_valid_snapshot(snapshot_id="id-2")
        assert first.provenance.snapshot_id != second.provenance.snapshot_id
        assert first.underlying == second.underlying
        assert first.option_chain == second.option_chain


class TestImmutability:
    def test_frozen_dataclasses_reject_mutation(self) -> None:
        snapshot = minimal_valid_snapshot()
        with pytest.raises(Exception):
            snapshot.underlying.last_price = 1.0  # type: ignore[misc]


class TestValidation:
    def test_validate_rejects_invalid_spot(self) -> None:
        snapshot = minimal_valid_snapshot()
        invalid_underlying = UnderlyingSnapshot(
            symbol="NIFTY 50",
            exchange="NSE",
            quote_key="NSE:NIFTY 50",
            last_price=float("nan"),
            quote_timestamp=fixed_as_of(),
        )
        invalid = MarketSnapshot(
            provenance=snapshot.provenance,
            freshness=snapshot.freshness,
            quality=snapshot.quality,
            underlying=invalid_underlying,
            option_chain=snapshot.option_chain,
        )
        result = validate_market_snapshot(invalid)
        assert result.validation_status == SnapshotValidationStatus.INVALID

    def test_validate_rejects_empty_chain(self) -> None:
        snapshot = minimal_valid_snapshot()
        empty_chain = OptionChainSnapshot(
            metadata=replace_metadata(snapshot.option_chain.metadata, contract_count=0, complete_pairs=0),
            contracts=(),
        )
        invalid = MarketSnapshot(
            provenance=snapshot.provenance,
            freshness=snapshot.freshness,
            quality=snapshot.quality,
            underlying=snapshot.underlying,
            option_chain=empty_chain,
        )
        result = validate_market_snapshot(invalid)
        assert result.validation_status == SnapshotValidationStatus.INVALID
        assert any(error.code.endswith("EMPTY") for error in result.errors)

    def test_validate_rejects_duplicate_contract(self) -> None:
        duplicate = (
            make_contract(strike=24300.0, option_type=OptionType.CE),
            make_contract(strike=24300.0, option_type=OptionType.CE, tradingsymbol="DUP"),
        )
        snapshot = minimal_valid_snapshot()
        invalid = MarketSnapshot(
            provenance=snapshot.provenance,
            freshness=snapshot.freshness,
            quality=snapshot.quality,
            underlying=snapshot.underlying,
            option_chain=OptionChainSnapshot(
                metadata=replace_metadata(snapshot.option_chain.metadata, contract_count=2, complete_pairs=0),
                contracts=duplicate,
            ),
        )
        result = validate_market_snapshot(invalid)
        assert result.validation_status == SnapshotValidationStatus.INVALID

    def test_validate_rejects_unsorted_contracts(self) -> None:
        unsorted = (
            make_contract(strike=24300.0, option_type=OptionType.PE, tradingsymbol="PE"),
            make_contract(strike=24300.0, option_type=OptionType.CE, tradingsymbol="CE"),
        )
        snapshot = minimal_valid_snapshot()
        invalid = MarketSnapshot(
            provenance=snapshot.provenance,
            freshness=snapshot.freshness,
            quality=snapshot.quality,
            underlying=snapshot.underlying,
            option_chain=OptionChainSnapshot(
                metadata=snapshot.option_chain.metadata,
                contracts=unsorted,
            ),
        )
        result = validate_market_snapshot(invalid)
        assert any(error.code.endswith("UNSORTED") for error in result.errors)

    def test_validate_rejects_negative_open_interest(self) -> None:
        bad_contract = make_contract()
        bad = OptionContractSnapshot(**{**bad_contract.__dict__, "open_interest": -1})
        snapshot = minimal_valid_snapshot()
        invalid = MarketSnapshot(
            provenance=snapshot.provenance,
            freshness=snapshot.freshness,
            quality=snapshot.quality,
            underlying=snapshot.underlying,
            option_chain=OptionChainSnapshot(
                metadata=snapshot.option_chain.metadata,
                contracts=(bad,),
            ),
        )
        result = validate_market_snapshot(invalid)
        assert result.validation_status == SnapshotValidationStatus.INVALID

    def test_validate_cross_field_strike_outside_window(self) -> None:
        out_of_range = make_contract(strike=25000.0)
        snapshot = minimal_valid_snapshot()
        invalid = MarketSnapshot(
            provenance=snapshot.provenance,
            freshness=snapshot.freshness,
            quality=snapshot.quality,
            underlying=snapshot.underlying,
            option_chain=OptionChainSnapshot(
                metadata=snapshot.option_chain.metadata,
                contracts=(out_of_range,),
            ),
        )
        result = validate_market_snapshot(invalid)
        assert any("STRIKE_OUT_OF_RANGE" in error.code for error in result.errors)

    def test_quality_scoring_reduces_with_missing_quotes(self) -> None:
        contracts = full_nifty_contracts()
        bad_contracts = list(contracts)
        first = bad_contracts[0]
        bad_contracts[0] = OptionContractSnapshot(
            underlying=first.underlying,
            exchange=first.exchange,
            tradingsymbol=first.tradingsymbol,
            expiry=first.expiry,
            strike=first.strike,
            option_type=first.option_type,
            lot_size=first.lot_size,
            ltp=first.ltp,
            bid=0.0,
            ask=0.0,
            volume=first.volume,
            open_interest=first.open_interest,
            quote_timestamp=first.quote_timestamp,
        )
        snapshot = build_market_snapshot(
            underlying=make_underlying(),
            contracts=bad_contracts,
            underlying_symbol="NIFTY",
            exchange="NFO",
            expiry="2026-08-07",
            atm_strike=24300.0,
            strike_step=50.0,
            strike_window_strikes=10,
            minimum_strike=23800.0,
            maximum_strike=24800.0,
            lot_size=75,
            as_of=fixed_as_of(),
            captured_at=fixed_captured_at(),
            reference_time=fixed_captured_at(),
        )
        assert snapshot.quality.missing_quotes == 1
        assert snapshot.quality.completeness_score < 100.0


class TestFreshness:
    def test_fresh_during_open_session(self) -> None:
        snapshot = minimal_valid_snapshot()
        assert snapshot.freshness.status == SnapshotFreshnessStatus.FRESH
        assert snapshot.freshness.is_usable_for_live_decisions is True

    def test_stale_during_open_session(self) -> None:
        snapshot = minimal_valid_snapshot()
        reference = fixed_captured_at() + timedelta(minutes=10)
        freshness = evaluate_snapshot_freshness(snapshot, reference_time=reference)
        assert freshness.status == SnapshotFreshnessStatus.STALE
        assert freshness.is_usable_for_live_decisions is False

    def test_market_closed_on_weekend(self) -> None:
        snapshot = build_market_snapshot(
            underlying=UnderlyingSnapshot(
                symbol="NIFTY 50",
                exchange="NSE",
                quote_key="NSE:NIFTY 50",
                last_price=24296.75,
                quote_timestamp=weekend_reference(),
            ),
            contracts=(
                make_contract(
                    quote_timestamp=weekend_reference(),
                ),
                make_contract(
                    strike=24300.0,
                    option_type=OptionType.PE,
                    tradingsymbol="NIFTY2680724300PE",
                    ltp=115.0,
                    bid=115.0,
                    ask=115.15,
                    quote_timestamp=weekend_reference(),
                ),
            ),
            underlying_symbol="NIFTY",
            exchange="NFO",
            expiry="2026-08-07",
            atm_strike=24300.0,
            strike_step=50.0,
            strike_window_strikes=1,
            minimum_strike=24300.0,
            maximum_strike=24300.0,
            lot_size=75,
            as_of=weekend_reference(),
            captured_at=weekend_reference(),
            reference_time=weekend_reference(),
        )
        freshness = evaluate_snapshot_freshness(
            snapshot,
            reference_time=weekend_reference(),
        )
        assert freshness.status == SnapshotFreshnessStatus.MARKET_CLOSED
        assert freshness.is_usable_for_live_decisions is False

    def test_future_timestamp(self) -> None:
        snapshot = minimal_valid_snapshot()
        reference = fixed_as_of() - timedelta(minutes=5)
        freshness = evaluate_snapshot_freshness(snapshot, reference_time=reference)
        assert freshness.status == SnapshotFreshnessStatus.FUTURE_TIMESTAMP
        assert freshness.age_seconds < 0

    def test_unknown_when_no_quote_timestamps(self) -> None:
        contracts = (
            OptionContractSnapshot(
                underlying="NIFTY",
                exchange="NFO",
                tradingsymbol="NIFTY2680724300CE",
                expiry="2026-08-07",
                strike=24300.0,
                option_type=OptionType.CE,
                lot_size=75,
                ltp=110.0,
                bid=109.65,
                ask=109.9,
                volume=1000,
                open_interest=10000,
            ),
        )
        underlying = UnderlyingSnapshot(
            symbol="NIFTY 50",
            exchange="NSE",
            quote_key="NSE:NIFTY 50",
            last_price=24296.75,
        )
        snapshot = build_market_snapshot(
            underlying=underlying,
            contracts=contracts,
            underlying_symbol="NIFTY",
            exchange="NFO",
            expiry="2026-08-07",
            atm_strike=24300.0,
            strike_step=50.0,
            strike_window_strikes=1,
            minimum_strike=24300.0,
            maximum_strike=24300.0,
            lot_size=75,
            as_of=fixed_as_of(),
            captured_at=fixed_captured_at(),
            reference_time=fixed_captured_at(),
        )
        freshness = evaluate_snapshot_freshness(
            snapshot,
            reference_time=fixed_captured_at(),
        )
        assert freshness.status == SnapshotFreshnessStatus.UNKNOWN


class TestLiveTradeReady:
    def test_live_trade_ready_when_fresh_and_valid(self) -> None:
        snapshot = minimal_valid_snapshot()
        assert is_live_trade_ready(snapshot) is True

    def test_not_live_trade_ready_when_stale(self) -> None:
        snapshot = minimal_valid_snapshot()
        stale = with_freshness(
            snapshot,
            evaluate_snapshot_freshness(
                snapshot,
                reference_time=fixed_captured_at() + timedelta(minutes=10),
            ),
        )
        assert is_live_trade_ready(stale) is False

    def test_non_strict_allows_partial(self) -> None:
        snapshot = minimal_valid_snapshot()
        fresh = with_freshness(
            snapshot,
            evaluate_snapshot_freshness(
                snapshot,
                reference_time=fixed_captured_at(),
            ),
        )
        partial = MarketSnapshot(
            provenance=fresh.provenance,
            freshness=fresh.freshness,
            quality=SnapshotQuality(
                validation_status=SnapshotValidationStatus.PARTIAL,
                completeness_score=80.0,
                missing_quotes=0,
                inverted_markets=0,
                warnings=(),
                errors=(),
            ),
            underlying=fresh.underlying,
            option_chain=fresh.option_chain,
        )
        assert partial.freshness.status == SnapshotFreshnessStatus.FRESH
        assert is_live_trade_ready(partial, strict=False) is True
        assert is_live_trade_ready(partial, strict=True) is False


class TestSerialization:
    def test_dict_round_trip_equality(self) -> None:
        snapshot = minimal_valid_snapshot()
        restored = from_dict(to_dict(snapshot))
        assert restored == snapshot

    def test_json_round_trip_equality(self) -> None:
        snapshot = full_nifty_snapshot()
        restored = from_json(to_json(snapshot))
        assert restored == snapshot

    def test_schema_version_constant(self) -> None:
        snapshot = minimal_valid_snapshot()
        payload = to_dict(snapshot)
        assert payload["schema_version"] == MARKET_SNAPSHOT_SCHEMA_VERSION

    def test_from_json_rejects_malformed_payload(self) -> None:
        with pytest.raises(SnapshotValidationError):
            from_json("{not-json")

    def test_from_dict_rejects_unsupported_major_version(self) -> None:
        snapshot = minimal_valid_snapshot()
        payload = to_dict(snapshot)
        payload["schema_version"] = "2.0.0"
        with pytest.raises(SnapshotValidationError):
            from_dict(payload)


class TestLegacyImport:
    def test_from_legacy_option_snapshot_maps_fields(self) -> None:
        snapshot = from_legacy_option_snapshot(legacy_option_snapshot_dict())
        assert snapshot.underlying.last_price == 24296.75
        assert snapshot.provenance.source == SnapshotSource.REPLAY
        assert snapshot.option_chain.metadata.expiry == "2026-08-04"
        assert len(snapshot.option_chain.contracts) == 2


class TestEquality:
    def test_snapshots_with_same_values_are_equal(self) -> None:
        first = minimal_valid_snapshot(snapshot_id="same")
        second = minimal_valid_snapshot(snapshot_id="same")
        assert first == second


class TestThreadSafety:
    def test_parallel_build_and_validate(self) -> None:
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait()
            snapshot = minimal_valid_snapshot(snapshot_id=f"parallel-{threading.get_ident()}")
            result = validate_market_snapshot(snapshot)
            assert result.validation_status == SnapshotValidationStatus.VALID

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker) for _ in range(8)]
            for future in futures:
                future.result()


class TestPerformanceSmoke:
    def test_build_and_validate_full_snapshot_under_threshold(self) -> None:
        import time

        start = time.perf_counter()
        snapshot = full_nifty_snapshot()
        result = validate_market_snapshot(snapshot)
        elapsed = time.perf_counter() - start
        assert result.validation_status == SnapshotValidationStatus.VALID
        assert elapsed < 1.0


class TestStrictBuild:
    def test_strict_build_rejects_warnings(self) -> None:
        contracts = (
            make_contract(bid=0.0, ask=109.9),
            make_contract(
                strike=24300.0,
                option_type=OptionType.PE,
                tradingsymbol="PE",
                ltp=115.0,
                bid=115.0,
                ask=115.15,
            ),
        )
        with pytest.raises(SnapshotBuildError):
            build_market_snapshot(
                underlying=make_underlying(),
                contracts=contracts,
                underlying_symbol="NIFTY",
                exchange="NFO",
                expiry="2026-08-07",
                atm_strike=24300.0,
                strike_step=50.0,
                strike_window_strikes=1,
                minimum_strike=24300.0,
                maximum_strike=24300.0,
                lot_size=75,
                as_of=fixed_as_of(),
                captured_at=fixed_captured_at(),
                reference_time=fixed_captured_at(),
                strict=True,
            )


def replace_metadata(
    metadata: OptionChainMetadata,
    **changes: object,
) -> OptionChainMetadata:
    data = metadata.__dict__.copy()
    data.update(changes)
    return OptionChainMetadata(**data)
