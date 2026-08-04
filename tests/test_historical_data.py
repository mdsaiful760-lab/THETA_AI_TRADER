"""Unit tests for the historical-data service."""

from __future__ import annotations

import concurrent.futures
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from broker import historical_data as hd
from config.application_configuration import EnvironmentProfile

NOW = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)
TOKEN = 256265


class FakeClient:
    """Deterministic injected historical client."""

    def __init__(self, rows, fail: bool = False, chunk_aware: bool = False) -> None:
        self.rows = list(rows)
        self.fail = fail
        self.chunk_aware = chunk_aware
        self.calls = 0
        self.ranges: list[tuple[datetime, datetime]] = []

    def fetch_historical_rows(self, **kwargs):
        self.calls += 1
        self.ranges.append((kwargs["from_ts"], kwargs["to_ts"]))
        if self.fail:
            raise RuntimeError("network")
        if not self.chunk_aware:
            return self.rows
        start, end = kwargs["from_ts"], kwargs["to_ts"]
        return [
            row
            for row in self.rows
            if start <= hd.normalize_candle_timestamp(row["date"]) <= end
        ]


class Resolver:
    """Tiny deterministic token resolver."""

    def resolve_token(self, *, exchange, tradingsymbol):
        if exchange == "NSE" and tradingsymbol == "NIFTY":
            return TOKEN
        raise LookupError("unknown")


class RecordingBus:
    """Capture published topics for assertions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, topic, payload):
        self.events.append((topic, dict(payload)))


def row(minute: int, day: int = 5, **overrides):
    result = {
        "date": datetime(2026, 8, day, 9, minute),
        "open": 100.0,
        "high": 105.0,
        "low": 99.0,
        "close": 102.0,
        "volume": 10,
        "oi": 2,
    }
    result.update(overrides)
    return result


def config(**overrides):
    result = {
        "enabled_underlyings": ("NIFTY",),
        "disk_cache_enabled": False,
        "memory_cache_ttl_seconds": 1000.0,
    }
    result.update(overrides)
    return hd.HistoricalDataConfig(**result)


def service(rows=(), *, bus=None, holiday_calendar=None, adjustment_factors=None,
            corporate_action_dates=None, **kwargs):
    cfg = kwargs.pop("config", {})
    client = kwargs.pop("client", None)
    return hd.HistoricalDataService(
        config(**cfg),
        client=client or FakeClient(rows),
        resolver=Resolver(),
        event_bus=bus,
        clock=lambda: NOW,
        id_factory=lambda: "fixed",
        holiday_calendar=holiday_calendar,
        adjustment_factors=adjustment_factors,
        corporate_action_dates=corporate_action_dates,
    )


def test_underlying_catalog_parity():
    from broker import instrument_loader, kite_websocket, market_data_streaming

    assert hd.SUPPORTED_PRIMARY_UNDERLYINGS == instrument_loader.SUPPORTED_PRIMARY_UNDERLYINGS
    assert hd.SUPPORTED_PRIMARY_UNDERLYINGS == kite_websocket.SUPPORTED_PRIMARY_UNDERLYINGS
    assert hd.SUPPORTED_PRIMARY_UNDERLYINGS == market_data_streaming.SUPPORTED_PRIMARY_UNDERLYINGS
    assert hd.SUPPORTED_SECONDARY_UNDERLYINGS == instrument_loader.SUPPORTED_SECONDARY_UNDERLYINGS
    assert hd.SUPPORTED_SECONDARY_UNDERLYINGS == kite_websocket.SUPPORTED_SECONDARY_UNDERLYINGS
    assert hd.SUPPORTED_SECONDARY_UNDERLYINGS == market_data_streaming.SUPPORTED_SECONDARY_UNDERLYINGS


def test_timeframe_kite_interval_map():
    assert [hd.to_kite_interval(item) for item in hd.CandleTimeframe] == [
        "minute",
        "3minute",
        "5minute",
        "10minute",
        "15minute",
        "30minute",
        "60minute",
        "day",
    ]


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"enabled_underlyings": ()}, "HD.CONFIG.UNDERLYING_REQUIRED"),
        ({"enabled_underlyings": ("NIFTY", "nifty")}, "HD.CONFIG.UNDERLYING_DUPLICATE"),
        ({"enabled_underlyings": ("X",)}, "HD.CONFIG.UNDERLYING_UNSUPPORTED"),
        (
            {"enabled_underlyings": ("NIFTY",), "session_open": "15:30", "session_close": "09:15"},
            "HD.CONFIG.SESSION_INVALID",
        ),
        (
            {"enabled_underlyings": ("NIFTY",), "session_timezone": "Not/AZone"},
            "HD.CONFIG.SESSION_INVALID",
        ),
        ({"enabled_underlyings": ("NIFTY",), "max_gap_ratio": 2}, "HD.CONFIG.THRESHOLD_OUT_OF_RANGE"),
        (
            {
                "enabled_underlyings": ("NIFTY",),
                "environment_profile": EnvironmentProfile.PRODUCTION,
                "disk_cache_enabled": True,
            },
            "HD.CONFIG.CACHE_PATH_REQUIRED",
        ),
        (
            {
                "enabled_underlyings": ("NIFTY",),
                "environment_profile": EnvironmentProfile.PRODUCTION,
                "disk_cache_enabled": False,
                "missing_candle_policy": "FORWARD_FILL",
            },
            "HD.CONFIG.POLICY_INVALID",
        ),
        ({"enabled_underlyings": ("NIFTY",), "enabled_timeframes": ()}, "HD.CONFIG.TIMEFRAME_INVALID"),
        ({"enabled_underlyings": ("NIFTY",), "duplicate_policy": "BAD"}, "HD.CONFIG.POLICY_INVALID"),
    ],
)
def test_config_validation(kwargs, code):
    with pytest.raises(hd.HistoricalDataConfigurationError) as exc:
        hd.HistoricalDataConfig(**kwargs)
    assert exc.value.code == code


def test_default_configs_and_equity_path():
    assert not hd.default_historical_data_config().disk_cache_enabled
    paper = hd.default_historical_data_config(EnvironmentProfile.PAPER)
    assert paper.disk_cache_directory == "cache/historical"
    production = hd.default_historical_data_config(EnvironmentProfile.PRODUCTION)
    assert production.strict_validation is True
    allowed = hd.HistoricalDataConfig(
        enabled_underlyings=("RELIANCE",),
        allow_equity_fo=True,
        enabled_equity_underlyings=("RELIANCE",),
        disk_cache_enabled=False,
    )
    assert allowed.enabled_underlyings == ("RELIANCE",)


def test_timestamp_normalization():
    assert hd.normalize_candle_timestamp(datetime(2026, 8, 5, 9, 15)) == datetime(
        2026, 8, 5, 3, 45, tzinfo=timezone.utc
    )
    assert hd.normalize_candle_timestamp(
        datetime(2026, 8, 5, 9, 15, tzinfo=timezone.utc)
    ).tzinfo == timezone.utc


@pytest.mark.parametrize(
    "bad,code",
    [
        ({"high": 90}, "HD.VALIDATION.OHLC_INCONSISTENT"),
        ({"volume": -1}, "HD.VALIDATION.INVALID_VOLUME"),
        ({"oi": -1}, "HD.VALIDATION.INVALID_OI"),
        ({"open": 0}, "HD.VALIDATION.INVALID_PRICE"),
    ],
)
def test_strict_row_validation(bad, code):
    instance = service([row(15, **bad)], config={"strict_validation": True})
    with pytest.raises(hd.HistoricalDataValidationError) as exc:
        instance.load_from_rows(
            instance._client.rows,
            instrument_token=TOKEN,
            timeframe=hd.CandleTimeframe.MINUTE_5,
        )
    assert exc.value.code == code


@pytest.mark.parametrize("policy,close", [("KEEP_FIRST_STABLE", 102), ("KEEP_LAST_STABLE", 104)])
def test_duplicate_policy(policy, close):
    instance = service([row(15), row(15, close=close)], config={"duplicate_policy": policy})
    series = instance.load_from_rows(
        instance._client.rows,
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
    )
    assert series.duplicate_count == 1
    assert series.candles[0].close == close


def test_reject_duplicate_empty_and_naive_public():
    instance = service([row(15), row(15)], config={"duplicate_policy": "REJECT"})
    with pytest.raises(hd.HistoricalDataValidationError) as exc:
        instance.load_from_rows(
            instance._client.rows,
            instrument_token=TOKEN,
            timeframe=hd.CandleTimeframe.MINUTE_5,
        )
    assert exc.value.code == "HD.VALIDATION.DUPLICATE_CANDLE"
    with pytest.raises(hd.HistoricalDataValidationError) as exc:
        instance.fetch_range(
            instrument_token=TOKEN,
            from_ts=datetime(2026, 8, 5),
            to_ts=NOW,
        )
    assert exc.value.code == "HD.VALIDATION.NAIVE_TIMESTAMP"
    empty = service([], config={"require_non_empty_series": True})
    with pytest.raises(hd.HistoricalDataValidationError) as exc:
        empty.load_from_rows([], instrument_token=TOKEN, timeframe=hd.CandleTimeframe.MINUTE_5)
    assert exc.value.code == "HD.VALIDATION.EMPTY_SERIES"


def test_gaps_mark_fail_forward_fill_and_severity():
    rows = [row(15), row(25)]
    marked = service(rows).load_from_rows(
        rows,
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
    )
    assert marked.validation_status is hd.SeriesValidationStatus.PARTIAL
    assert marked.missing_bar_count == 1
    assert marked.candles[0].gap_after
    assert marked.gap_severity is hd.GapSeverity.CRITICAL
    with pytest.raises(hd.HistoricalDataValidationError) as exc:
        service(rows, config={"missing_candle_policy": "FAIL"}).load_from_rows(
            rows,
            instrument_token=TOKEN,
            timeframe=hd.CandleTimeframe.MINUTE_5,
        )
    assert exc.value.code == "HD.VALIDATION.MISSING_CANDLES"
    filled = service(rows, config={"missing_candle_policy": "FORWARD_FILL"}).load_from_rows(
        rows,
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
    )
    assert len(filled.candles) == 3
    assert filled.candles[1].metadata["synthetic"] == "true"


def test_fetch_apis_identity_memory_cache_and_status():
    rows = [row(15), row(20), row(25)]
    instance = service(rows)
    assert instance.get_status() is hd.HistoricalLifecycleState.CREATED
    start = datetime(2026, 8, 5, 3, 45, tzinfo=timezone.utc)
    end = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    first = instance.fetch_range(
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
        from_ts=start,
        to_ts=end,
        underlying="NIFTY",
    )
    again = instance.fetch_range(
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
        from_ts=start,
        to_ts=end,
    )
    assert first.candle_count == 3
    assert again.source_kind is hd.CandleSourceKind.MEMORY_CACHE
    assert instance._client.calls == 1
    assert instance.get_status() is hd.HistoricalLifecycleState.READY
    assert instance.fetch_latest_n(exchange="NSE", tradingsymbol="NIFTY", n=2).candle_count == 2
    assert instance.fetch_session(
        instrument_token=TOKEN,
        session_date=date(2026, 8, 5),
    ).candle_count == 3
    assert instance.fetch_rolling_window(
        instrument_token=TOKEN,
        window=timedelta(hours=1),
    ).candle_count == 3


def test_identity_errors_client_error_and_lifecycle():
    no_resolver = hd.HistoricalDataService(config(), client=FakeClient([]), clock=lambda: NOW)
    with pytest.raises(hd.HistoricalDataStateError) as exc:
        no_resolver.fetch_range(
            exchange="NSE",
            tradingsymbol="NIFTY",
            from_ts=NOW,
            to_ts=NOW,
        )
    assert exc.value.code == "HD.STATE.RESOLVER_NOT_CONFIGURED"
    instance = service()
    instance.close()
    with pytest.raises(hd.HistoricalDataStateError):
        instance.get_cached_series("x")
    assert instance.get_status() is hd.HistoricalLifecycleState.CLOSED


def test_disk_cache_roundtrip_stale_corrupt_and_invalidate(tmp_path: Path):
    cfg = config(
        disk_cache_enabled=True,
        disk_cache_directory=str(tmp_path),
        disk_cache_ttl_seconds=100,
    )
    cache = hd.HistoricalCache(cfg, clock=lambda: NOW)
    series = service([row(15)]).load_from_rows(
        [row(15)],
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
    )
    cache.put("key", series)
    disk_cache = hd.HistoricalCache(cfg, clock=lambda: NOW)
    assert disk_cache.get("key").source_kind is hd.CandleSourceKind.DISK_CACHE
    path = cache._path("key")
    path.write_text("{}", encoding="utf-8")
    assert cache.get("key") is not None
    cache.invalidate()
    path.write_text("{}", encoding="utf-8")
    assert cache.get("key") is None
    service_instance = hd.HistoricalDataService(cfg, clock=lambda: NOW, id_factory=lambda: "fixed")
    service_instance._cache.put("one", series)
    service_instance.invalidate_cache("one")
    assert service_instance.get_cached_series("one") is None


def test_stale_disk_allowed(tmp_path: Path):
    cfg = config(
        disk_cache_enabled=True,
        disk_cache_directory=str(tmp_path),
        disk_cache_ttl_seconds=0,
        allow_stale_disk_cache=True,
        memory_cache_enabled=False,
    )
    series = service([row(15)]).load_from_rows(
        [row(15)],
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
    )
    writer = hd.HistoricalCache(
        config(disk_cache_enabled=True, disk_cache_directory=str(tmp_path), memory_cache_enabled=False),
        clock=lambda: NOW,
    )
    writer.put("stale", series)
    reader = hd.HistoricalCache(cfg, clock=lambda: NOW + timedelta(days=2))
    assert reader.get("stale") is not None


def test_serialization_roundtrip_and_errors():
    value = service([row(15)]).load_from_rows(
        [row(15)],
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
    )
    assert hd.historical_series_from_json(hd.historical_series_to_json(value)) == value
    assert hd.historical_candle_from_json(
        hd.historical_candle_to_json(value.candles[0])
    ) == value.candles[0]
    stats = service().get_statistics()
    assert hd.historical_statistics_from_json(hd.historical_statistics_to_json(stats)) == stats
    health = service([row(15)]).get_health()
    assert hd.historical_health_from_json(hd.historical_health_to_json(health)).report_id == health.report_id
    with pytest.raises(hd.HistoricalDataSerializationError):
        hd.deserialize_historical_candle({"schema_version": "2.0.0"})
    with pytest.raises(hd.HistoricalDataSerializationError):
        hd.deserialize_historical_candle({"timestamp": "not-a-date"})
    with pytest.raises(hd.HistoricalDataSerializationError):
        hd.deserialize_historical_series({"schema_version": "1.0.0"})
    with pytest.raises(hd.HistoricalDataSerializationError):
        hd.deserialize_historical_statistics({})
    with pytest.raises(hd.HistoricalDataSerializationError):
        hd.deserialize_historical_health({})


def test_single_flight_concurrent_fetches():
    rows = [row(15)]
    instance = service(rows)
    start = datetime(2026, 8, 5, 3, 45, tzinfo=timezone.utc)

    def fetch():
        return instance.fetch_range(
            instrument_token=TOKEN,
            timeframe=hd.CandleTimeframe.MINUTE_5,
            from_ts=start,
            to_ts=start,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: fetch(), range(8)))
    assert all(item.candles == results[0].candles for item in results)
    assert instance._client.calls == 1


def test_chunked_broker_fetch():
    rows = [
        {"date": datetime(2026, 6, 1, 9, 15), "open": 100, "high": 105, "low": 99, "close": 102, "volume": 1},
        {"date": datetime(2026, 8, 3, 9, 15), "open": 100, "high": 105, "low": 99, "close": 102, "volume": 1},
    ]
    client = FakeClient(rows, chunk_aware=True)
    instance = service(
        client=client,
        config={
            "broker_chunk_days": 30,
            "missing_candle_policy": "MARK_GAP",
            "require_non_empty_series": False,
        },
    )
    start = datetime(2026, 6, 1, 3, 45, tzinfo=timezone.utc)
    end = datetime(2026, 8, 3, 3, 45, tzinfo=timezone.utc)
    series = instance.fetch_range(
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
        from_ts=start,
        to_ts=end,
    )
    assert client.calls >= 2
    assert series.candle_count == 2


def test_determinism_and_events():
    bus = RecordingBus()
    rows = [row(15), row(20)]
    left = service(rows, bus=bus, config={"publish_events": True}).fetch_range(
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
        from_ts=datetime(2026, 8, 5, 3, 45, tzinfo=timezone.utc),
        to_ts=datetime(2026, 8, 5, 3, 50, tzinfo=timezone.utc),
    )
    right = service(rows, config={"publish_events": True}).fetch_range(
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
        from_ts=datetime(2026, 8, 5, 3, 45, tzinfo=timezone.utc),
        to_ts=datetime(2026, 8, 5, 3, 50, tzinfo=timezone.utc),
    )
    assert left.candles == right.candles
    assert left.validation_status == right.validation_status
    topics = {topic for topic, _ in bus.events}
    assert hd.TOPIC_SERIES_FETCHED in topics
    assert hd.TOPIC_CACHE_UPDATED in topics


def test_corporate_action_policies():
    rows = [row(15), row(20)]
    with pytest.raises(hd.HistoricalDataValidationError) as exc:
        service(
            rows,
            config={"corporate_action_policy": "APPLY_ADJUSTMENTS"},
        ).load_from_rows(rows, instrument_token=TOKEN, timeframe=hd.CandleTimeframe.MINUTE_5)
    assert exc.value.code == "HD.CA.FACTORS_REQUIRED"
    adjusted = service(
        rows,
        config={"corporate_action_policy": "APPLY_ADJUSTMENTS"},
        adjustment_factors={"2026-08-05": 0.5},
    ).load_from_rows(rows, instrument_token=TOKEN, timeframe=hd.CandleTimeframe.MINUTE_5)
    assert adjusted.candles[0].close == 51.0
    assert adjusted.candles[0].is_adjusted
    with pytest.raises(hd.HistoricalDataValidationError) as exc:
        service(
            rows,
            config={"corporate_action_policy": "REJECT_IF_UNADJUSTED"},
            corporate_action_dates=frozenset({date(2026, 8, 5)}),
        ).load_from_rows(rows, instrument_token=TOKEN, timeframe=hd.CandleTimeframe.MINUTE_5)
    assert exc.value.code == "HD.CA.UNADJUSTED_SPAN"


def test_statistics_health_reset_and_boundaries():
    instance = service([row(15)])
    instance.load_from_rows(
        [row(15)],
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
    )
    assert instance.get_statistics().series_sealed_count == 1
    health = instance.get_health()
    assert health.overall_health is hd.HistoricalHealthStatus.HEALTHY
    assert any(issue.issue_code == "HD.HEALTH.HOLIDAY_CALENDAR_MISSING" for issue in health.issues)
    instance.reset_statistics()
    assert instance.get_statistics().series_sealed_count == 0
    source = Path(hd.__file__).read_text(encoding="utf-8")
    for forbidden in ("kiteconnect", "KiteTicker", "place_order", "def ema(", "def rsi(", "NSE:NIFTY 50"):
        assert forbidden not in source
    assert "256265" not in source


def test_broker_failure_identity_failures_and_unhealthy():
    instance = hd.HistoricalDataService(
        config(),
        client=FakeClient([], fail=True),
        clock=lambda: NOW,
        id_factory=lambda: "fixed",
    )
    with pytest.raises(hd.HistoricalDataLookupError):
        instance.fetch_range(instrument_token=TOKEN, exchange="NSE", from_ts=NOW, to_ts=NOW)
    with pytest.raises(hd.HistoricalDataLookupError):
        instance.fetch_range(from_ts=NOW, to_ts=NOW)
    with pytest.raises(hd.HistoricalDataValidationError):
        instance.fetch_range(instrument_token=0, from_ts=NOW, to_ts=NOW)
    with pytest.raises(hd.HistoricalDataIOError) as exc:
        instance.fetch_range(instrument_token=TOKEN, from_ts=NOW, to_ts=NOW)
    assert exc.value.code == "HD.IO.BROKER_FETCH_FAILED"
    assert instance.get_health().overall_health is hd.HistoricalHealthStatus.UNHEALTHY
    instance.close()
    assert instance.get_health().overall_health is hd.HistoricalHealthStatus.UNHEALTHY


def test_no_client_and_disk_health_issue():
    diskless = hd.HistoricalDataService(
        hd.HistoricalDataConfig(enabled_underlyings=("NIFTY",), disk_cache_enabled=True),
        clock=lambda: NOW,
        id_factory=lambda: "fixed",
    )
    assert diskless.get_health().overall_health is hd.HistoricalHealthStatus.UNKNOWN
    codes = {issue.issue_code for issue in diskless.get_health().issues}
    assert "HD.HEALTH.NO_CLIENT" in codes
    assert "HD.HEALTH.DISK_CACHE_UNAVAILABLE" in codes


def test_day_gap_warning_and_soft_discard():
    rows = [
        {"date": datetime(2026, 8, 3, 9, 15), "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1},
        {"date": datetime(2026, 8, 5, 9, 15), "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1},
    ]
    series = service(rows).load_from_rows(
        rows,
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.DAY_1,
    )
    assert "HD.HEALTH.HOLIDAY_CALENDAR_MISSING" in series.warnings
    assert series.missing_bar_count >= 1
    soft = service(
        [row(15, high=90), row(20)],
        config={"strict_validation": False},
    ).load_from_rows(
        [row(15, high=90), row(20)],
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
    )
    assert soft.candle_count == 1


def test_resolver_miss_and_invalid_range_window():
    instance = service([row(15)])
    with pytest.raises(hd.HistoricalDataLookupError) as exc:
        instance.fetch_range(
            exchange="NSE",
            tradingsymbol="UNKNOWN",
            from_ts=NOW,
            to_ts=NOW,
        )
    assert exc.value.code == "HD.LOOKUP.SYMBOL_UNRESOLVED"
    with pytest.raises(hd.HistoricalDataValidationError):
        instance.fetch_rolling_window(instrument_token=TOKEN, window=timedelta(0))
    with pytest.raises(hd.HistoricalDataValidationError):
        instance.fetch_latest_n(instrument_token=TOKEN, n=0)
    with pytest.raises(hd.HistoricalDataValidationError):
        instance.fetch_range(
            instrument_token=TOKEN,
            from_ts=NOW,
            to_ts=NOW - timedelta(minutes=1),
        )


def test_write_failed_and_parse_datetime_type(tmp_path: Path, monkeypatch):
    cfg = config(disk_cache_enabled=True, disk_cache_directory=str(tmp_path))
    cache = hd.HistoricalCache(cfg, clock=lambda: NOW)
    series = service([row(15)]).load_from_rows(
        [row(15)],
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
    )

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(hd.HistoricalDataIOError) as exc:
        cache.put("k", series)
    assert exc.value.code == "HD.IO.WRITE_FAILED"
    with pytest.raises(TypeError):
        hd._parse_datetime(123)


def test_experimental_underlying_and_disabled_timeframe():
    cfg = hd.HistoricalDataConfig(
        enabled_underlyings=("CUSTOMX",),
        allow_experimental_underlyings=True,
        disk_cache_enabled=False,
        enabled_timeframes=(hd.CandleTimeframe.MINUTE_5,),
    )
    assert cfg.enabled_underlyings == ("CUSTOMX",)
    instance = hd.HistoricalDataService(
        cfg,
        client=FakeClient([row(15)]),
        clock=lambda: NOW,
        id_factory=lambda: "fixed",
    )
    with pytest.raises(hd.HistoricalDataValidationError) as exc:
        instance.fetch_range(
            instrument_token=TOKEN,
            timeframe=hd.CandleTimeframe.MINUTE_1,
            from_ts=NOW,
            to_ts=NOW,
        )
    assert exc.value.code == "HD.CONFIG.TIMEFRAME_INVALID"


def test_coalesced_waiters_and_failed_coalesce():
    import threading

    release = threading.Event()
    started = threading.Event()

    class SlowClient(FakeClient):
        def fetch_historical_rows(self, **kwargs):
            started.set()
            release.wait(timeout=2)
            return super().fetch_historical_rows(**kwargs)

    client = SlowClient([row(15)])
    instance = service(client=client)
    start = datetime(2026, 8, 5, 3, 45, tzinfo=timezone.utc)
    results = []

    def fetch():
        results.append(
            instance.fetch_range(
                instrument_token=TOKEN,
                timeframe=hd.CandleTimeframe.MINUTE_5,
                from_ts=start,
                to_ts=start,
            )
        )

    first = threading.Thread(target=fetch)
    second = threading.Thread(target=fetch)
    first.start()
    assert started.wait(timeout=2)
    second.start()
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert len(results) == 2
    assert client.calls == 1

    failing = service(client=FakeClient([], fail=True))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(
                failing.fetch_range,
                instrument_token=TOKEN,
                timeframe=hd.CandleTimeframe.MINUTE_5,
                from_ts=start,
                to_ts=start,
            )
            for _ in range(4)
        ]
        errors = []
        for future in futures:
            try:
                future.result()
            except hd.HistoricalDataError as exc:
                errors.append(exc.code)
    assert "HD.IO.BROKER_FETCH_FAILED" in errors


def test_client_missing_too_many_candles_and_severity_bands():
    no_client = hd.HistoricalDataService(config(), clock=lambda: NOW, id_factory=lambda: "fixed")
    with pytest.raises(hd.HistoricalDataStateError) as exc:
        no_client.fetch_range(instrument_token=TOKEN, from_ts=NOW, to_ts=NOW)
    assert exc.value.code == "HD.STATE.CLIENT_NOT_CONFIGURED"
    many = [row(minute) for minute in range(0, 50, 5)]
    with pytest.raises(hd.HistoricalDataValidationError) as exc:
        service(many, config={"max_candles_per_request": 2}).load_from_rows(
            many,
            instrument_token=TOKEN,
            timeframe=hd.CandleTimeframe.MINUTE_5,
        )
    assert exc.value.code == "HD.VALIDATION.TOO_MANY_CANDLES"
    assert service()._severity(0, 100) is hd.GapSeverity.NONE
    assert service(config={"max_gap_ratio": 0.2})._severity(1, 100) is hd.GapSeverity.MINOR
    assert service(config={"max_gap_ratio": 0.05})._severity(10, 100) is hd.GapSeverity.MAJOR
    assert service(config={"max_gap_ratio": 0.05})._severity(20, 100) is hd.GapSeverity.CRITICAL


def test_holiday_calendar_and_day_latest_n():
    holidays = frozenset({date(2026, 8, 4)})
    rows = [
        {"date": datetime(2026, 8, 3, 9, 15), "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1},
        {"date": datetime(2026, 8, 5, 9, 15), "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1},
    ]
    series = service(rows, holiday_calendar=holidays).load_from_rows(
        rows,
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.DAY_1,
    )
    assert series.missing_bar_count == 0
    daily = service(
        [
            {"date": datetime(2026, 8, day, 9, 15), "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1}
            for day in (3, 4, 5)
        ]
    ).fetch_latest_n(
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.DAY_1,
        n=2,
        as_of=NOW,
    )
    assert daily.candle_count == 2


def test_unsupported_underlying_tag_and_allow_concurrent():
    instance = service([row(15)])
    with pytest.raises(hd.HistoricalDataLookupError):
        instance.fetch_range(
            instrument_token=TOKEN,
            underlying="BANKNIFTY",
            from_ts=NOW,
            to_ts=NOW,
        )
    concurrent_svc = service([row(15)], config={"allow_concurrent_fetches": True})
    start = datetime(2026, 8, 5, 3, 45, tzinfo=timezone.utc)
    series = concurrent_svc.fetch_range(
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
        from_ts=start,
        to_ts=start,
    )
    assert series.candle_count == 1


def test_health_serialization_with_issues_and_invalid_token_load():
    instance = service([row(15)])
    instance.load_from_rows([row(15)], instrument_token=TOKEN, timeframe=hd.CandleTimeframe.MINUTE_5)
    payload = hd.serialize_historical_health(instance.get_health())
    restored = hd.deserialize_historical_health(payload)
    assert restored.lifecycle_state is hd.HistoricalLifecycleState.READY
    with pytest.raises(hd.HistoricalDataValidationError):
        instance.load_from_rows([row(15)], instrument_token=0, timeframe=hd.CandleTimeframe.MINUTE_5)
    with pytest.raises(hd.HistoricalDataValidationError):
        hd.to_kite_interval("nope")  # type: ignore[arg-type]


def test_partial_bar_exclusion_and_include_flag():
    near_now = {
        "date": datetime(2026, 8, 5, 15, 25, tzinfo=timezone.utc),
        "open": 100,
        "high": 101,
        "low": 99,
        "close": 100,
        "volume": 1,
    }
    # 15:25 UTC + 5m = 15:30 > NOW(10:00)? Wait NOW is 10:00 UTC so 15:25 > now already closed...
    # Use bar that started just before now: 09:56 UTC + 5m = 10:01 > NOW → partial
    partial = {
        "date": datetime(2026, 8, 5, 9, 56, tzinfo=timezone.utc),
        "open": 100,
        "high": 101,
        "low": 99,
        "close": 100,
        "volume": 1,
    }
    closed = {
        "date": datetime(2026, 8, 5, 9, 50, tzinfo=timezone.utc),
        "open": 100,
        "high": 101,
        "low": 99,
        "close": 100,
        "volume": 1,
    }
    excluded = service([closed, partial]).load_from_rows(
        [closed, partial],
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
    )
    assert excluded.candle_count == 1
    included = service(
        [closed, partial],
        config={"include_partial_bar": True},
    ).load_from_rows(
        [closed, partial],
        instrument_token=TOKEN,
        timeframe=hd.CandleTimeframe.MINUTE_5,
    )
    assert included.candle_count == 2
