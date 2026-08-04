"""Unit tests for the immutable instrument catalog."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timezone

import pytest

from broker import instrument_loader as module
from broker.instrument_loader import (
    CatalogLifecycleState,
    DuplicatePolicy,
    InstrumentLoader,
    InstrumentLoaderConfigurationError,
    InstrumentLoaderIOError,
    InstrumentLoaderStateError,
    InstrumentLoaderConfig,
    InstrumentParseError,
    InstrumentSourceKind,
    InstrumentValidationError,
    LookupStatus,
    SUPPORTED_PRIMARY_UNDERLYINGS,
    deserialize_instrument_catalog,
    instrument_catalog_from_json,
    instrument_catalog_to_json,
    normalize_underlying_name,
)

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


def row(token: int, kind: str, symbol: str, *, name: str = "NIFTY",
        expiry: str = "", strike: str = "", exchange: str | None = None,
        lot: str = "75") -> dict[str, str]:
    """Build a small Zerodha-shaped fixture row."""
    return {
        "instrument_token": str(token), "exchange_token": str(token),
        "tradingsymbol": symbol, "name": name, "expiry": expiry,
        "strike": strike, "tick_size": "0.05", "lot_size": lot,
        "instrument_type": kind, "segment": "NFO-OPT",
        "exchange": exchange or ("NSE" if kind == "INDEX" else "NFO"),
    }


@pytest.fixture
def rows() -> list[dict[str, str]]:
    values = [row(1, "INDEX", "NIFTY 50", name="NIFTY 50", lot="1")]
    for index, strike in enumerate((24400, 24450, 24500, 24550, 24600), start=10):
        values.extend([
            row(index, "CE", f"NIFTY{strike}CE", expiry="2026-08-07", strike=str(strike)),
            row(index + 100, "PE", f"NIFTY{strike}PE", expiry="2026-08-07", strike=str(strike)),
        ])
    values.extend([
        row(300, "FUT", "NIFTYFUT", expiry="2026-08-28", strike=""),
        row(301, "CE", "NIFTYMONTHCE", expiry="2026-08-28", strike="24500"),
        row(401, "CE", "NIFTYSEPTCE", expiry="2026-09-24", strike="24500"),
    ])
    return values


def config(**changes: object) -> InstrumentLoaderConfig:
    """Create an isolated test configuration."""
    values: dict[str, object] = {"enabled_underlyings": ("NIFTY",), "cache_enabled": False}
    values.update(changes)
    return InstrumentLoaderConfig(**values)


def loader(**changes: object) -> InstrumentLoader:
    """Create a deterministic loader."""
    return InstrumentLoader(config(**changes), clock=lambda: NOW, id_factory=lambda: "catalog-test")


def test_config_validation_and_normalization(tmp_path: object) -> None:
    assert normalize_underlying_name(" nifty 50 ") == "NIFTY"
    assert config(enabled_underlyings=("nifty",)).enabled_underlyings == ("NIFTY",)
    with pytest.raises(InstrumentLoaderConfigurationError, match="At least"):
        InstrumentLoaderConfig(enabled_underlyings=(), cache_enabled=False)
    with pytest.raises(InstrumentLoaderConfigurationError) as duplicate:
        InstrumentLoaderConfig(enabled_underlyings=("NIFTY", "nifty"), cache_enabled=False)
    assert duplicate.value.code == "IL.CONFIG.UNDERLYING_DUPLICATE"
    with pytest.raises(InstrumentLoaderConfigurationError):
        config(default_strike_step=0)
    with pytest.raises(InstrumentLoaderConfigurationError):
        config(enabled_exchanges=("BAD",))
    with pytest.raises(InstrumentLoaderConfigurationError):
        config(duplicate_policy="BAD")
    with pytest.raises(InstrumentLoaderConfigurationError):
        InstrumentLoaderConfig(enabled_underlyings=("NIFTY",), environment_profile=module.EnvironmentProfile.PAPER)
    assert config(duplicate_policy="KEEP_LAST_STABLE").duplicate_policy is DuplicatePolicy.KEEP_LAST_STABLE


def test_load_pipeline_indexes_queries_and_projections(rows: list[dict[str, str]]) -> None:
    subject = loader()
    catalog = subject.load_from_rows(rows)
    assert catalog.record_count == len(rows)
    assert subject.get_status() is CatalogLifecycleState.READY
    assert subject.get_by_token(10).status is LookupStatus.HIT
    assert subject.get_by_token(0).status is LookupStatus.REJECTED
    assert subject.get_by_token(999).reason_code == "IL.LOOKUP.TOKEN_NOT_FOUND"
    assert subject.get_by_quote_key("NSE:NIFTY 50").primary is not None
    assert subject.get_by_tradingsymbol("nfo", "NIFTY24500CE").primary is not None
    assert len(subject.get_by_underlying_and_expiry("NIFTY", "07-Aug-2026").records) == 10
    assert len(subject.get_options("NIFTY", expiry="2026-08-07", strike=24500).records) == 2
    assert subject.get_futures("NIFTY").primary is not None
    assert subject.get_spot("NIFTY").primary is not None
    assert subject.get_lot_size("NIFTY") == 75
    assert subject.find_nearest_expiry("NIFTY").primary.expiry == "2026-08-07"
    assert subject.find_weekly_expiries("NIFTY").diagnostics["expiries"] == ("2026-08-07",)
    assert subject.find_monthly_expiries("NIFTY").diagnostics["expiries"] == ("2026-08-28", "2026-09-24")
    assert subject.find_closest_expiry("NIFTY", target=date(2026, 8, 20)).primary.expiry == "2026-08-28"
    assert subject.resolve_atm_strike("NIFTY", spot=24512, expiry="2026-08-07") == 24500
    assert {item.option_type for item in subject.find_nearest_strike("NIFTY", expiry="2026-08-07", target_price=24511).records} == {"CE", "PE"}
    assert len(subject.query_atm_options("NIFTY", spot=24512, expiry="2026-08-07").records) == 2
    assert subject.query_itm_options("NIFTY", spot=24512, expiry="2026-08-07", option_type="CE", depth=2).records[0].strike == 24500
    assert subject.query_otm_options("NIFTY", spot=24512, expiry="2026-08-07", option_type="PE").records[0].strike == 24500
    assert len(subject.project_descriptors("NIFTY", spot=24512, strikes_each_side=1)) == 8
    assert len(subject.project_subscriptions("NIFTY", spot=24512, strikes_each_side=1)) == 8
    assert subject.get_health().overall_health.value == "HEALTHY"


def test_validation_duplicates_expiry_and_strict_modes(rows: list[dict[str, str]]) -> None:
    invalid = row(-2, "CE", "BAD", expiry="2026-08-07", strike="0")
    expired = row(999, "CE", "OLD", expiry="2026-08-04", strike="24500")
    duplicate = dict(rows[1]); duplicate["tradingsymbol"] = "OTHER"
    subject = loader()
    catalog = subject.load_from_rows([*rows, invalid, expired, duplicate])
    assert catalog.statistics.discarded_invalid_count == 1
    assert catalog.statistics.discarded_expired_count == 1
    assert catalog.statistics.discarded_duplicate_count == 1
    assert subject.get_by_token(10).primary.tradingsymbol == rows[1]["tradingsymbol"]
    last = loader(duplicate_policy=DuplicatePolicy.KEEP_LAST_STABLE)
    assert last.load_from_rows([rows[1], duplicate]).records[0].tradingsymbol == "OTHER"
    with pytest.raises(InstrumentValidationError):
        loader(duplicate_policy=DuplicatePolicy.REJECT).load_from_rows([rows[1], duplicate])
    with pytest.raises(InstrumentValidationError):
        loader(strict_validation=True).load_from_rows([invalid])


def test_file_parsing_and_serialization(tmp_path: object, rows: list[dict[str, str]]) -> None:
    path = tmp_path / "master.csv"
    header = list(rows[0])
    path.write_text(",".join(header) + "\n" + "\n".join(",".join(item[key] for key in header) for item in rows), encoding="utf-8")
    subject = loader()
    catalog = subject.load_from_file(path)
    payload = instrument_catalog_to_json(catalog)
    restored = instrument_catalog_from_json(payload)
    assert restored.records == catalog.records
    assert deserialize_instrument_catalog(json.loads(payload)).indexes.by_token[10].instrument_token == 10
    broken = tmp_path / "broken.csv"
    broken.write_text("instrument_token,name\n1,NIFTY\n", encoding="utf-8")
    with pytest.raises(InstrumentParseError):
        loader().load_from_file(broken)
    json_path = tmp_path / "master.json"
    json_path.write_text(json.dumps(rows), encoding="utf-8")
    assert loader().load_from_file(json_path).record_count == len(rows)


def test_cache_broker_reload_and_state(tmp_path: object, rows: list[dict[str, str]]) -> None:
    cached = InstrumentLoader(config(cache_enabled=True, cache_directory=str(tmp_path)), clock=lambda: NOW, id_factory=lambda: "cache")
    cached.load_from_rows(rows)
    path = cached.save_cache()
    assert path.exists()
    loaded = InstrumentLoader(config(cache_enabled=True, cache_directory=str(tmp_path)), clock=lambda: NOW, id_factory=lambda: "cache")
    assert loaded.load_from_cache().record_count == len(rows)
    corrupt = json.loads(path.read_text()); corrupt["checksum"] = "sha256:bad"; path.write_text(json.dumps(corrupt))
    with pytest.raises(InstrumentLoaderIOError) as error:
        loaded.load_from_cache()
    assert error.value.code == "IL.IO.CACHE_CORRUPT"

    class Client:
        def fetch_instrument_rows(self, *, exchange: str) -> list[dict[str, str]]:
            return rows if exchange == "NFO" else []

    broker = InstrumentLoader(config(enabled_exchanges=("NFO",)), master_client=Client(), clock=lambda: NOW, id_factory=lambda: "broker")
    assert broker.load_from_broker().record_count == len(rows) - 1
    assert broker.reload().record_count == len(rows) - 1
    with pytest.raises(InstrumentLoaderStateError):
        loader().reload()
    closed = loader(); closed.close()
    with pytest.raises(InstrumentLoaderStateError):
        closed.get_catalog()


def test_concurrent_readers_and_contract_boundaries(rows: list[dict[str, str]]) -> None:
    subject = loader()
    subject.load_from_rows(rows)
    failures: list[Exception] = []

    def read() -> None:
        try:
            for _ in range(100):
                assert subject.get_by_token(10).status is LookupStatus.HIT
        except Exception as exc:  # pragma: no cover - diagnostic only
            failures.append(exc)

    threads = [threading.Thread(target=read) for _ in range(8)]
    for thread in threads: thread.start()
    subject.load_from_rows(rows)
    for thread in threads: thread.join()
    assert not failures
    source = (module.__file__ and open(module.__file__, encoding="utf-8").read()) or ""
    assert "kiteconnect" not in source.lower()
    assert "KiteTicker" not in source
    assert "place_order" not in source
    from broker import kite_websocket, market_data_streaming
    assert SUPPORTED_PRIMARY_UNDERLYINGS == kite_websocket.SUPPORTED_PRIMARY_UNDERLYINGS == market_data_streaming.SUPPORTED_PRIMARY_UNDERLYINGS


def test_serializers_helpers_and_failure_paths(tmp_path: object, rows: list[dict[str, str]]) -> None:
    """Exercise each public serializer and defensive source paths."""
    subject = loader()
    catalog = subject.load_from_rows(rows)
    record = catalog.records[0]
    assert module.deserialize_instrument_record(module.serialize_instrument_record(record)) == record
    assert module.instrument_record_from_json(module.instrument_record_to_json(record)) == record
    stats = module.deserialize_catalog_statistics(module.serialize_catalog_statistics(catalog.statistics))
    assert module.catalog_statistics_from_json(module.catalog_statistics_to_json(stats)) == stats
    result = subject.get_by_token(10)
    assert module.deserialize_lookup_result(module.serialize_lookup_result(result)) == result
    assert module.lookup_result_from_json(module.lookup_result_to_json(result)) == result
    health = subject.get_health()
    assert module.deserialize_catalog_health(module.serialize_catalog_health(health)) == health
    assert module.catalog_health_from_json(module.catalog_health_to_json(health)) == health
    assert module.serialize_instrument_catalog(catalog, include_indexes=True)["indexes_included"]
    for payload in ({}, {"schema_version": "2.0.0"}):
        with pytest.raises(module.InstrumentLoaderSerializationError):
            module.deserialize_instrument_record(payload)
    with pytest.raises(module.InstrumentLoaderSerializationError):
        module.instrument_record_from_json("{")
    assert module.classify_underlying_tier("FINNIFTY") is module.UnderlyingSupportTier.SECONDARY
    assert module.classify_underlying_tier("TCS", equity_underlyings=("TCS",)) is module.UnderlyingSupportTier.EQUITY_FO
    assert module.classify_underlying_tier("OTHER") is module.UnderlyingSupportTier.EXPERIMENTAL
    assert module.resolve_instrument_role("EQ") is module.InstrumentRole.EQUITY
    assert module.resolve_instrument_role("X", name="INDIA VIX") is module.InstrumentRole.VOLATILITY_INDEX
    assert module.resolve_instrument_role("X") is module.InstrumentRole.UNKNOWN
    with pytest.raises(InstrumentValidationError):
        normalize_underlying_name("")
    assert module.default_instrument_loader_config().allow_experimental_underlyings
    assert loader(require_non_empty_catalog=False).load_from_rows([]).record_count == 0
    with pytest.raises(InstrumentLoaderStateError):
        loader().load_from_broker()
    with pytest.raises(InstrumentLoaderIOError):
        loader().load_from_cache()
    with pytest.raises(InstrumentLoaderIOError):
        loader().load_from_file(tmp_path / "missing.csv")
    unsupported = tmp_path / "master.txt"
    unsupported.write_text("x", encoding="utf-8")
    with pytest.raises(InstrumentParseError):
        loader().load_from_file(unsupported)
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{}", encoding="utf-8")
    with pytest.raises(InstrumentParseError):
        loader().load_from_file(bad_json)
    no_catalog = loader()
    assert not no_catalog.get_health().has_catalog
    with pytest.raises(InstrumentLoaderConfigurationError):
        config(enabled_underlyings=("UNSUPPORTED",))
    with pytest.raises(InstrumentLoaderConfigurationError):
        config(enabled_underlyings=("TCS",), enabled_equity_underlyings=("TCS",))
    with pytest.raises(InstrumentLoaderConfigurationError):
        config(expiry_timezone="Not/A_Zone")
    with pytest.raises(InstrumentValidationError):
        module._parse_expiry("not-a-date")
    with pytest.raises(InstrumentValidationError):
        module._number("x", int, "token")
    with pytest.raises(InstrumentValidationError):
        module._number(None, int, "token")

    class BrokenClient:
        def fetch_instrument_rows(self, *, exchange: str) -> list[dict[str, str]]:
            raise RuntimeError("offline")

    with pytest.raises(InstrumentLoaderIOError):
        InstrumentLoader(config(enabled_exchanges=("NFO",)), master_client=BrokenClient()).load_from_broker()


def test_spot_overrides_vix_filters_and_edge_queries(
    tmp_path: object, rows: list[dict[str, str]]
) -> None:
    """Cover overrides, VIX mapping, filters, reload paths, and lock conflicts."""
    options_only = [item for item in rows if item["instrument_type"] != "INDEX"]
    subject = InstrumentLoader(
        config(
            spot_overrides={
                "NIFTY": {
                    "instrument_token": 9001,
                    "tradingsymbol": "NIFTY 50",
                    "name": "NIFTY 50",
                    "exchange": "NSE",
                }
            },
            volatility_index_map={"NIFTY": "INDIA VIX"},
            enabled_exchanges=("NSE", "NFO"),
        ),
        clock=lambda: NOW,
        id_factory=lambda: "override",
    )
    vix_row = row(8001, "EQ", "INDIA VIX", name="INDIA VIX", exchange="NSE", lot="1")
    vix_row["instrument_type"] = "EQ"
    catalog = subject.load_from_rows([*options_only, vix_row, row(7001, "CE", "BANKCE", name="BANKNIFTY", expiry="2026-08-07", strike="52000", exchange="NFO")])
    assert subject.get_spot("NIFTY").primary is not None
    assert subject.get_spot("NIFTY").primary.instrument_token == 9001
    assert any(item.instrument_role.value == "VOLATILITY_INDEX" for item in catalog.records)
    assert catalog.statistics.discarded_underlying_count >= 1

    filtered = loader(include_options=False, include_futures=False)
    filtered_catalog = filtered.load_from_rows(rows)
    assert filtered_catalog.statistics.option_count == 0
    assert filtered_catalog.statistics.future_count == 0

    path = tmp_path / "reload.csv"
    header = list(rows[0])
    path.write_text(
        ",".join(header)
        + "\n"
        + "\n".join(",".join(item[key] for key in header) for item in rows),
        encoding="utf-8",
    )
    reloader = loader()
    reloader.load_from_file(path)
    assert reloader.reload().record_count == len(rows)

    cache_dir = tmp_path / "cache"
    cache_loader = InstrumentLoader(
        config(cache_enabled=True, cache_directory=str(cache_dir)),
        clock=lambda: NOW,
        id_factory=lambda: "cache2",
    )
    cache_loader.load_from_rows(rows)
    cache_path = cache_loader.save_cache()
    assert cache_path.exists()
    from_cache = InstrumentLoader(
        config(cache_enabled=True, cache_directory=str(cache_dir)),
        clock=lambda: NOW,
        id_factory=lambda: "cache3",
    )
    assert from_cache.load_from_cache().record_count == len(rows)
    assert from_cache.reload().record_count == len(rows)

    class Client:
        def fetch_instrument_rows(self, *, exchange: str) -> list[dict[str, str]]:
            return rows if exchange == "NFO" else []

    prefer = InstrumentLoader(
        config(
            enabled_exchanges=("NFO",),
            cache_enabled=True,
            cache_directory=str(cache_dir),
            prefer_cache_before_download=True,
        ),
        master_client=Client(),
        clock=lambda: NOW,
        id_factory=lambda: "prefer",
    )
    assert prefer.load_from_broker().source_kind is InstrumentSourceKind.CACHE

    with pytest.raises(InstrumentLoaderConfigurationError):
        loader(max_records=1).load_from_rows(rows)
    with pytest.raises(InstrumentValidationError):
        loader().load_from_rows([])
    assert module._parse_expiry(1722816000000) is not None
    empty = loader(require_non_empty_catalog=False)
    empty.load_from_rows([])
    assert empty.get_statistics().retained_record_count == 0

    busy = loader()
    busy._begin()
    try:
        with pytest.raises(InstrumentLoaderStateError) as conflict:
            busy.load_from_rows(rows)
        assert conflict.value.code == "IL.STATE.LOAD_IN_PROGRESS"
    finally:
        busy._load_lock.release()

    closed = loader()
    closed.close()
    with pytest.raises(InstrumentLoaderStateError):
        closed._begin()

    class BoomBus:
        def publish(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("bus down")

    published = InstrumentLoader(
        config(publish_events=True),
        event_bus=BoomBus(),
        clock=lambda: NOW,
        id_factory=lambda: "bus",
    )
    assert published.load_from_rows(rows).record_count == len(rows)
    assert published.get_by_underlying("MISSING").status is LookupStatus.MISS
    assert published.find_nearest_expiry("MISSING").status is LookupStatus.MISS
    with pytest.raises(module.InstrumentLookupError):
        published.get_lot_size("MISSING")
    # Force stale cache rejection (catalog sealed at NOW; read clock is later)
    later = NOW.replace(year=2026, month=8, day=6)
    stale = InstrumentLoader(
        config(
            cache_enabled=True,
            cache_directory=str(cache_dir),
            cache_max_age_seconds=1.0,
            allow_stale_cache=False,
        ),
        clock=lambda: later,
        id_factory=lambda: "stale",
    )
    with pytest.raises(InstrumentLoaderIOError) as stale_error:
        stale.load_from_cache()
    assert stale_error.value.code == "IL.IO.CACHE_STALE"
    # Epoch-ms expiry + quote override + unknown role allow
    epoch_row = row(9100, "CE", "NIFTYEPOCHCE", expiry=str(1722816000000), strike="24500")
    override_meta = row(9101, "INDEX", "SPOTX", name="NIFTY 50", lot="1")
    override_meta["metadata"] = {"quote_key_override": "NSE:CUSTOM-SPOT"}
    unknown = row(9102, "XYZ", "WEIRD", name="NIFTY")
    allowed = loader(allow_unknown_roles=True, require_non_empty_catalog=False)
    weird = allowed.load_from_rows(
        [epoch_row, override_meta, unknown],
    )
    assert any(item.quote_key == "NSE:CUSTOM-SPOT" for item in weird.records)
    assert any(item.instrument_role.value == "UNKNOWN" for item in weird.records)
    # Epoch-ms expiry parses then is dropped by the expiry filter on as-of 2026-08-05.
    assert weird.statistics.discarded_expired_count >= 1
    assert module._parse_expiry("1722816000000") == "2024-08-05"
