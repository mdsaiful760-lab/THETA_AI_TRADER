"""Unit tests for broker.kite_websocket."""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from broker.kite_websocket import (
    KITE_WEBSOCKET_VERSION,
    PRODUCER_NAME,
    SUPPORTED_PRIMARY_UNDERLYINGS,
    SUPPORTED_SECONDARY_UNDERLYINGS,
    SUPPORTED_UNDERLYINGS,
    KiteWebSocketClient,
    KiteWebSocketConfig,
    KiteWebSocketConfigurationError,
    KiteWebSocketConnectionError,
    KiteWebSocketError,
    KiteWebSocketSubscriptionError,
    KiteWebSocketTickMode,
    KiteWebSocketValidationError,
    SubscriptionInstrument,
    SubscriptionManager,
    TOPIC_CONNECTION,
    TOPIC_SUBSCRIPTION,
    UnderlyingStreamStats,
    UnderlyingSupportTier,
    WebSocketConnectionStatus,
    WebSocketDashboardConnectionSnapshot,
    WebSocketHealthIssue,
    WebSocketHealthReport,
    WebSocketHealthStatus,
    WebSocketStatistics,
    build_dashboard_connection_snapshot,
    classify_dashboard_index_status,
    classify_underlying_tier,
    default_kite_websocket_config,
    deserialize_websocket_health_report,
    deserialize_websocket_statistics,
    normalize_underlying_name,
    serialize_subscription_records,
    serialize_websocket_health_report,
    serialize_websocket_statistics,
)
from config.application_configuration import EnvironmentProfile
from core.event_bus import EventBus, EventBusPolicy

FIXED_NOW = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)


def fixed_clock() -> datetime:
    """Deterministic clock."""
    return FIXED_NOW


class FakeKiteTicker:
    """Deterministic KiteTicker double for WebSocket tests."""

    MODE_FULL = "full"
    MODE_QUOTE = "quote"

    def __init__(self, api_key: str, access_token: str) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.connected = False
        self.subscribed: set[int] = set()
        self.modes: dict[int, str] = {}
        self.subscribe_fail = False
        self.unsubscribe_fail = False
        self.connect_fail = False
        self.on_ticks = None
        self.on_connect = None
        self.on_close = None
        self.on_error = None
        self.on_reconnect = None
        self.on_noreconnect = None

    def connect(self, threaded: bool = True) -> None:
        if self.connect_fail:
            raise RuntimeError("connect failed")
        self.connected = True
        if callable(self.on_connect):
            self.on_connect(self)

    def close(self) -> None:
        self.connected = False
        if callable(self.on_close):
            self.on_close(self)

    def subscribe(self, tokens: list[int]) -> None:
        if self.subscribe_fail:
            raise RuntimeError("subscribe failed")
        self.subscribed.update(int(t) for t in tokens)

    def unsubscribe(self, tokens: list[int]) -> None:
        if self.unsubscribe_fail:
            raise RuntimeError("unsubscribe failed")
        for token in tokens:
            self.subscribed.discard(int(token))
            self.modes.pop(int(token), None)

    def set_mode(self, mode: str, tokens: list[int]) -> None:
        for token in tokens:
            self.modes[int(token)] = mode

    def emit_ticks(self, ticks: list[dict[str, Any]]) -> None:
        if callable(self.on_ticks):
            self.on_ticks(self, ticks)


def make_instrument(
    token: int,
    underlying: str,
    *,
    quote_key: str | None = None,
    exchange: str = "NSE",
    symbol: str | None = None,
) -> SubscriptionInstrument:
    """Build a resolved instrument for tests (tokens are test inputs, not module constants)."""
    name = underlying.upper()
    return SubscriptionInstrument(
        instrument_token=token,
        underlying=name,
        quote_key=quote_key or f"{exchange}:TEST-{name}-{token}",
        exchange=exchange,
        tradingsymbol=symbol or f"SYM-{name}-{token}",
        instrument_kind="INDEX",
    )


def make_client(
    *,
    underlyings: tuple[str, ...] = ("NIFTY", "BANKNIFTY"),
    profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT,
    publish_events: bool = True,
    event_bus: EventBus | None = None,
    ticker: FakeKiteTicker | None = None,
    clock=fixed_clock,
    **kwargs: Any,
) -> tuple[KiteWebSocketClient, FakeKiteTicker]:
    """Build client with fake ticker."""
    fake = ticker or FakeKiteTicker("api-key", "access-token")
    config = KiteWebSocketConfig(
        environment_profile=profile,
        enabled_underlyings=underlyings,
        tick_mode=KiteWebSocketTickMode.FULL,
        max_subscriptions=kwargs.pop("max_subscriptions", 500),
        publish_events=publish_events,
        publish_tick_events=kwargs.pop("publish_tick_events", False),
        fail_closed_on_empty_instruments=kwargs.pop(
            "fail_closed_on_empty_instruments", False
        ),
        per_underlying_silence_seconds=kwargs.pop("per_underlying_silence_seconds", 5.0),
        heartbeat_silence_seconds=kwargs.pop("heartbeat_silence_seconds", 5.0),
        allow_experimental_underlyings=kwargs.pop("allow_experimental_underlyings", False),
        runner_kind="test",
    )
    client = KiteWebSocketClient(
        config,
        api_key="api-key",
        access_token="access-token",
        event_bus=event_bus,
        clock=clock,
        ticker_factory=lambda key, token: fake,
        id_factory=kwargs.pop("id_factory", lambda: "evt-1"),
    )
    return client, fake


class TestCatalogAndConfig:
    """Catalog and configuration validation."""

    def test_catalog_constants(self) -> None:
        assert SUPPORTED_PRIMARY_UNDERLYINGS == frozenset(
            {"NIFTY", "BANKNIFTY", "SENSEX"}
        )
        assert SUPPORTED_SECONDARY_UNDERLYINGS == frozenset({"FINNIFTY", "MIDCPNIFTY"})
        assert SUPPORTED_UNDERLYINGS == (
            SUPPORTED_PRIMARY_UNDERLYINGS | SUPPORTED_SECONDARY_UNDERLYINGS
        )

    def test_rejects_unsupported_underlying(self) -> None:
        with pytest.raises(KiteWebSocketConfigurationError) as exc:
            KiteWebSocketConfig(enabled_underlyings=("FOO",))
        assert exc.value.code == "KITE_WS.CONFIG.UNDERLYING_UNSUPPORTED"

    def test_accepts_primary_and_secondary_mix(self) -> None:
        config = KiteWebSocketConfig(enabled_underlyings=("NIFTY", "FINNIFTY"))
        assert config.enabled_underlyings == ("NIFTY", "FINNIFTY")
        assert classify_underlying_tier("FINNIFTY") is UnderlyingSupportTier.SECONDARY

    def test_rejects_duplicates_and_empty(self) -> None:
        with pytest.raises(KiteWebSocketConfigurationError) as exc:
            KiteWebSocketConfig(enabled_underlyings=("NIFTY", "nifty"))
        assert exc.value.code == "KITE_WS.CONFIG.UNDERLYING_DUPLICATE"
        with pytest.raises(KiteWebSocketConfigurationError) as exc2:
            KiteWebSocketConfig(enabled_underlyings=())
        assert exc2.value.code == "KITE_WS.CONFIG.UNDERLYING_REQUIRED"

    def test_experimental_allowed(self) -> None:
        config = KiteWebSocketConfig(
            enabled_underlyings=("CUSTOMUL",),
            allow_experimental_underlyings=True,
        )
        assert config.enabled_underlyings == ("CUSTOMUL",)

    def test_missing_credentials(self) -> None:
        config = default_kite_websocket_config()
        with pytest.raises(KiteWebSocketConfigurationError):
            KiteWebSocketClient(config, api_key="", access_token="tok")
        with pytest.raises(KiteWebSocketConfigurationError):
            KiteWebSocketClient(config, api_key="key", access_token="")


class TestSubscriptionManager:
    """Configurable instrument list behaviour."""

    def test_set_instruments_multi_underlying(self) -> None:
        manager = SubscriptionManager(
            max_subscriptions=10,
            enabled_underlyings=("NIFTY", "BANKNIFTY", "SENSEX"),
        )
        instruments = (
            make_instrument(101, "NIFTY"),
            make_instrument(202, "BANKNIFTY"),
            make_instrument(303, "SENSEX"),
        )
        manager.set_instruments(instruments)
        desired = manager.desired()
        assert [i.instrument_token for i in desired] == [101, 202, 303]

    def test_rejects_non_enabled_underlying(self) -> None:
        manager = SubscriptionManager(
            max_subscriptions=10,
            enabled_underlyings=("NIFTY",),
        )
        with pytest.raises(KiteWebSocketValidationError) as exc:
            manager.set_instruments((make_instrument(1, "BANKNIFTY"),))
        assert exc.value.code == "KITE_WS.VALIDATION.UNDERLYING_NOT_ENABLED"

    def test_rejects_invalid_and_duplicate_tokens(self) -> None:
        manager = SubscriptionManager(
            max_subscriptions=10,
            enabled_underlyings=("NIFTY",),
        )
        with pytest.raises(KiteWebSocketValidationError) as exc:
            manager.set_instruments((make_instrument(0, "NIFTY"),))
        assert exc.value.code == "KITE_WS.VALIDATION.INVALID_TOKEN"
        with pytest.raises(KiteWebSocketValidationError) as exc2:
            manager.set_instruments(
                (make_instrument(5, "NIFTY"), make_instrument(5, "NIFTY"))
            )
        assert exc2.value.code == "KITE_WS.VALIDATION.DUPLICATE_TOKEN"

    def test_add_remove_clear_and_diff(self) -> None:
        manager = SubscriptionManager(
            max_subscriptions=10,
            enabled_underlyings=("NIFTY", "BANKNIFTY"),
        )
        manager.set_instruments((make_instrument(1, "NIFTY"),))
        manager.add_instruments((make_instrument(2, "BANKNIFTY"),))
        assert len(manager.desired()) == 2
        manager.mark_active((1, 2), at=FIXED_NOW)
        manager.remove_tokens((1,))
        diff = manager.diff()
        assert diff.unsubscribe_tokens == (1,)
        assert diff.subscribe_tokens == ()
        manager.clear()
        assert manager.desired() == ()


class TestConnectSubscribeFlow:
    """Connection and multi-underlying subscription flow."""

    def test_connect_and_subscribe_multiple_underlyings(self) -> None:
        bus = EventBus(EventBusPolicy())
        received: list[object] = []
        bus.subscribe(TOPIC_SUBSCRIPTION, lambda env: received.append(env.payload))
        client, fake = make_client(
            underlyings=("NIFTY", "BANKNIFTY", "SENSEX"),
            event_bus=bus,
        )
        client.set_instruments(
            (
                make_instrument(11, "NIFTY"),
                make_instrument(22, "BANKNIFTY"),
                make_instrument(33, "SENSEX"),
            )
        )
        client.connect()
        assert client.get_status() is WebSocketConnectionStatus.CONNECTED
        event = client.apply_subscriptions()
        assert event.subscribed_count == 3
        assert set(event.underlyings) == {"NIFTY", "BANKNIFTY", "SENSEX"}
        assert fake.subscribed == {11, 22, 33}
        assert len(received) == 1

    def test_dynamic_add_banknifty_only_new_tokens(self) -> None:
        client, fake = make_client(underlyings=("NIFTY", "BANKNIFTY"))
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        assert fake.subscribed == {11}
        client.set_instruments(
            (make_instrument(11, "NIFTY"), make_instrument(22, "BANKNIFTY"))
        )
        event = client.apply_subscriptions()
        assert event.subscribed_tokens == (22,)
        assert event.unsubscribed_tokens == ()
        assert fake.subscribed == {11, 22}

    def test_apply_while_disconnected(self) -> None:
        client, _ = make_client()
        client.set_instruments((make_instrument(1, "NIFTY"),))
        with pytest.raises(KiteWebSocketConnectionError) as exc:
            client.apply_subscriptions()
        assert exc.value.code == "KITE_WS.CONNECTION.NOT_CONNECTED"

    def test_disconnect_clears_active_retains_desired(self) -> None:
        client, _fake = make_client()
        client.set_instruments(
            (make_instrument(11, "NIFTY"), make_instrument(22, "BANKNIFTY"))
        )
        client.connect()
        client.apply_subscriptions()
        client.disconnect()
        assert client.get_subscriptions() == ()
        assert client.get_status() is WebSocketConnectionStatus.CLOSED
        assert len(client._subscriptions.desired()) == 2

    def test_subscribe_sdk_failure(self) -> None:
        fake = FakeKiteTicker("api-key", "access-token")
        fake.subscribe_fail = True
        client, _ = make_client(ticker=fake)
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        with pytest.raises(KiteWebSocketSubscriptionError) as exc:
            client.apply_subscriptions()
        assert exc.value.code == "KITE_WS.SUBSCRIBE.SDK_FAILED"


class TestTicksHealthStatsEvents:
    """Ticks, health, statistics, and events for multiple underlyings."""

    def test_ticks_and_per_underlying_stats(self) -> None:
        client, fake = make_client(underlyings=("NIFTY", "BANKNIFTY", "FINNIFTY"))
        client.set_instruments(
            (
                make_instrument(11, "NIFTY"),
                make_instrument(22, "BANKNIFTY"),
                make_instrument(33, "FINNIFTY"),
            )
        )
        client.connect()
        client.apply_subscriptions()
        ticks_seen: list[Any] = []
        client.set_tick_handler(lambda t: ticks_seen.append(t))
        fake.emit_ticks(
            [
                {"instrument_token": 11, "last_price": 1.0},
                {"instrument_token": 22, "last_price": 2.0},
                {"instrument_token": 11, "last_price": 1.5},
            ]
        )
        stats = client.get_statistics()
        assert stats.total_tick_count == 3
        by_ul = {e.underlying: e.tick_count for e in stats.per_underlying}
        assert by_ul["NIFTY"] == 2
        assert by_ul["BANKNIFTY"] == 1
        assert by_ul["FINNIFTY"] == 0
        assert len(stats.per_underlying) == 3
        assert len(ticks_seen) == 3

    def test_health_partitions_multiple_underlyings(self) -> None:
        client, fake = make_client(underlyings=("NIFTY", "BANKNIFTY", "SENSEX"))
        client.set_instruments(
            (
                make_instrument(11, "NIFTY"),
                make_instrument(22, "BANKNIFTY"),
            )
        )
        client.connect()
        client.apply_subscriptions()
        fake.emit_ticks([{"instrument_token": 11, "last_price": 1.0}])
        health = client.get_health()
        assert "NIFTY" in health.healthy_underlyings
        assert "SENSEX" in health.degraded_underlyings
        assert health.enabled_underlyings == ("NIFTY", "BANKNIFTY", "SENSEX")
        assert "access-token" not in serialize_websocket_health_report(health)

    def test_connection_events_include_enabled_underlyings(self) -> None:
        bus = EventBus(EventBusPolicy())
        events: list[object] = []
        bus.subscribe(TOPIC_CONNECTION, lambda env: events.append(env.payload))
        client, _ = make_client(
            underlyings=("NIFTY", "MIDCPNIFTY"),
            event_bus=bus,
        )
        client.connect()
        assert events
        assert events[-1].enabled_underlyings == ("NIFTY", "MIDCPNIFTY")

    def test_unattributed_ticks(self) -> None:
        client, fake = make_client()
        client.connect()
        fake.emit_ticks([{"instrument_token": 999999, "last_price": 1.0}])
        stats = client.get_statistics()
        assert stats.unattributed_tick_count == 1

    def test_handler_error_isolation(self) -> None:
        client, fake = make_client()
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()

        def boom(_tick: Any) -> None:
            raise RuntimeError("boom")

        client.set_tick_handler(boom)
        fake.emit_ticks([{"instrument_token": 11, "last_price": 1.0}])
        assert client.get_statistics().handler_error_count == 1


class TestValidationSerializationConcurrency:
    """Validation, serialization, concurrency, and boundary checks."""

    def test_validate_no_instruments_fail_closed(self) -> None:
        client, _ = make_client(
            profile=EnvironmentProfile.PRODUCTION,
            underlyings=("NIFTY",),
            fail_closed_on_empty_instruments=True,
        )
        issues = client.validate()
        assert any(i.code == "KITE_WS.VALIDATION.NO_INSTRUMENTS" for i in issues)

    def test_serialization_round_trip(self) -> None:
        client, fake = make_client(underlyings=("NIFTY", "BANKNIFTY"))
        client.set_instruments(
            (make_instrument(11, "NIFTY"), make_instrument(22, "BANKNIFTY"))
        )
        client.connect()
        client.apply_subscriptions()
        fake.emit_ticks([{"instrument_token": 11, "last_price": 1.0}])
        stats = client.get_statistics()
        restored_stats = deserialize_websocket_statistics(
            serialize_websocket_statistics(stats)
        )
        assert restored_stats.total_tick_count == stats.total_tick_count
        health = client.get_health()
        restored_health = deserialize_websocket_health_report(
            serialize_websocket_health_report(health)
        )
        assert restored_health.overall_health is health.overall_health
        payload = serialize_subscription_records(client.get_subscriptions())
        assert "11" in payload

    def test_malformed_serialization(self) -> None:
        with pytest.raises(KiteWebSocketError) as exc:
            deserialize_websocket_statistics("{bad")
        assert exc.value.code == "KITE_WS.SERIALIZATION.MALFORMED"
        with pytest.raises(KiteWebSocketError):
            deserialize_websocket_health_report('{"schema_version":"0.0.1"}')

    def test_concurrent_health_during_ticks(self) -> None:
        client, fake = make_client(underlyings=("NIFTY", "BANKNIFTY"))
        client.set_instruments(
            (make_instrument(11, "NIFTY"), make_instrument(22, "BANKNIFTY"))
        )
        client.connect()
        client.apply_subscriptions()
        barrier = threading.Barrier(4)
        errors: list[BaseException] = []

        def reader() -> None:
            try:
                barrier.wait()
                for _ in range(30):
                    report = client.get_health()
                    assert report.enabled_underlyings == ("NIFTY", "BANKNIFTY")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def writer() -> None:
            try:
                barrier.wait()
                for _ in range(30):
                    fake.emit_ticks(
                        [
                            {"instrument_token": 11, "last_price": 1.0},
                            {"instrument_token": 22, "last_price": 2.0},
                        ]
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
        source = Path(__file__).resolve().parents[1] / "broker" / "kite_websocket.py"
        text = source.read_text(encoding="utf-8")
        assert "256265" not in text
        assert "260105" not in text
        assert "NSE:NIFTY 50" not in text
        assert "NSE:NIFTY BANK" not in text
        assert not re.search(r"instrument_token\s*=\s*\d{4,}", text)

    def test_repr_redacts_secrets(self) -> None:
        client, _ = make_client()
        text = repr(client)
        assert "access-token" not in text
        assert "<redacted>" in text
        assert PRODUCER_NAME
        assert KITE_WEBSOCKET_VERSION == "1.1.0"

    def test_reconnect_and_error_callbacks(self) -> None:
        client, fake = make_client()
        client.connect()
        assert fake.on_reconnect is not None
        fake.on_reconnect(fake, 2)
        assert client.get_status() is WebSocketConnectionStatus.RECONNECTING
        fake.on_noreconnect(fake)
        assert client.get_status() is WebSocketConnectionStatus.FAILED
        assert client.get_statistics().reconnect_count == 1

    def test_limit_exceeded(self) -> None:
        client, _ = make_client(max_subscriptions=1)
        with pytest.raises(KiteWebSocketSubscriptionError) as exc:
            client.set_instruments(
                (make_instrument(1, "NIFTY"), make_instrument(2, "BANKNIFTY"))
            )
        assert exc.value.code == "KITE_WS.SUBSCRIBE.LIMIT_EXCEEDED"

    def test_connect_failure(self) -> None:
        fake = FakeKiteTicker("api-key", "access-token")
        fake.connect_fail = True
        client, _ = make_client(ticker=fake)
        with pytest.raises(KiteWebSocketConnectionError):
            client.connect()
        assert client.get_status() is WebSocketConnectionStatus.FAILED

    def test_get_instruments_for_underlying(self) -> None:
        client, _ = make_client()
        client.set_instruments(
            (make_instrument(11, "NIFTY"), make_instrument(22, "BANKNIFTY"))
        )
        client.connect()
        client.apply_subscriptions()
        nifty = client.get_instruments_for_underlying("nifty")
        assert len(nifty) == 1
        assert nifty[0].instrument_token == 11

    def test_reset_statistics(self) -> None:
        client, fake = make_client()
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        fake.emit_ticks([{"instrument_token": 11, "last_price": 1.0}])
        client.reset_statistics()
        stats = client.get_statistics()
        assert stats.total_tick_count == 0

    def test_silent_underlying_health(self) -> None:
        times = [FIXED_NOW]

        def clock() -> datetime:
            return times[0]

        client, fake = make_client(
            clock=clock,
            per_underlying_silence_seconds=1.0,
            underlyings=("NIFTY",),
        )
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        fake.emit_ticks([{"instrument_token": 11, "last_price": 1.0}])
        times[0] = FIXED_NOW + timedelta(seconds=10)
        health = client.get_health()
        assert "NIFTY" in health.degraded_underlyings
        assert any(i.issue_code == "KITE_WS.HEALTH.UNDERLYING_SILENT" for i in health.issues)

    def test_normalize_helper(self) -> None:
        assert normalize_underlying_name(" banknifty ") == "BANKNIFTY"

    def test_unsubscribe_all_on_empty_desired(self) -> None:
        client, fake = make_client()
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        client.set_instruments(())
        event = client.apply_subscriptions()
        assert event.unsubscribed_count == 1
        assert fake.subscribed == set()

    def test_default_config_helper(self) -> None:
        config = default_kite_websocket_config(
            EnvironmentProfile.PAPER,
            enabled_underlyings=("NIFTY", "SENSEX"),
        )
        assert config.enabled_underlyings == ("NIFTY", "SENSEX")
        assert config.runner_kind == "paper"


class TestCoverageGaps:
    """Additional edge paths for >95% coverage."""

    def test_config_numeric_guards(self) -> None:
        with pytest.raises(KiteWebSocketConfigurationError):
            KiteWebSocketConfig(enabled_underlyings=("NIFTY",), max_subscriptions=0)
        with pytest.raises(KiteWebSocketConfigurationError):
            KiteWebSocketConfig(enabled_underlyings=("NIFTY",), connect_timeout_seconds=-1)
        with pytest.raises(KiteWebSocketConfigurationError):
            KiteWebSocketConfig(
                enabled_underlyings=("NIFTY",), heartbeat_silence_seconds=-1
            )

    def test_instrument_metadata_proxy(self) -> None:
        instrument = SubscriptionInstrument(
            instrument_token=1,
            underlying="nifty",
            quote_key="EX:SYM",
            exchange="NSE",
            tradingsymbol="SYM",
            metadata={"a": "b"},
        )
        assert instrument.underlying == "NIFTY"
        assert dict(instrument.metadata) == {"a": "b"}

    def test_empty_underlying_name_in_config(self) -> None:
        with pytest.raises(KiteWebSocketConfigurationError):
            KiteWebSocketConfig(enabled_underlyings=("  ",))

    def test_mark_failed_and_records(self) -> None:
        manager = SubscriptionManager(
            max_subscriptions=10, enabled_underlyings=("NIFTY",)
        )
        manager.set_instruments((make_instrument(1, "NIFTY"),))
        manager.mark_failed((1,), error_code="KITE_WS.SUBSCRIBE.SDK_FAILED", at=FIXED_NOW)
        records = manager.active()
        assert records[0].state.value == "failed"
        manager.mark_unsubscribed((1,))
        assert manager.active() == ()

    def test_empty_quote_key_rejected(self) -> None:
        manager = SubscriptionManager(
            max_subscriptions=10, enabled_underlyings=("NIFTY",)
        )
        bad = SubscriptionInstrument(
            instrument_token=1,
            underlying="NIFTY",
            quote_key="   ",
            exchange="NSE",
            tradingsymbol="X",
        )
        with pytest.raises(KiteWebSocketValidationError):
            manager.set_instruments((bad,))

    def test_handlers_none_and_connection_handler_error(self) -> None:
        client, fake = make_client()
        client.set_tick_handler(None)
        client.set_error_handler(None)
        client.set_connection_handler(lambda _e: (_ for _ in ()).throw(RuntimeError("x")))
        client.connect()
        assert client.get_statistics().handler_error_count >= 1
        client.set_error_handler(lambda _e: (_ for _ in ()).throw(RuntimeError("y")))
        fake.on_error(fake, 1, "boom")
        assert client.get_status() in {
            WebSocketConnectionStatus.DEGRADED,
            WebSocketConnectionStatus.CONNECTED,
            WebSocketConnectionStatus.FAILED,
        }

    def test_disconnect_close_failure(self) -> None:
        class BadClose(FakeKiteTicker):
            def close(self) -> None:
                raise RuntimeError("close failed")

        fake = BadClose("api-key", "access-token")
        client, _ = make_client(ticker=fake)
        client.connect()
        client.disconnect()
        assert client.get_status() is WebSocketConnectionStatus.CLOSED

    def test_unsubscribe_sdk_failure(self) -> None:
        fake = FakeKiteTicker("api-key", "access-token")
        client, _ = make_client(ticker=fake)
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        fake.unsubscribe_fail = True
        client.set_instruments(())
        with pytest.raises(KiteWebSocketSubscriptionError):
            client.apply_subscriptions()

    def test_validate_injected_bad_desired(self) -> None:
        client, _ = make_client(underlyings=("NIFTY", "FINNIFTY"), max_subscriptions=2)
        # Bypass manager validation to exercise client.validate branches.
        client._subscriptions._desired = {
            0: SubscriptionInstrument(
                instrument_token=0,
                underlying="BANKNIFTY",
                quote_key="   ",
                exchange="NSE",
                tradingsymbol="X",
            ),
            1: make_instrument(1, "NIFTY"),
            2: make_instrument(1, "NIFTY"),
        }
        # Force oversized desired count relative to max.
        client._subscriptions._desired[3] = make_instrument(3, "NIFTY")
        issues = client.validate()
        codes = {i.code for i in issues}
        assert "KITE_WS.VALIDATION.INVALID_TOKEN" in codes
        assert "KITE_WS.VALIDATION.UNDERLYING_NOT_ENABLED" in codes
        assert "KITE_WS.SUBSCRIBE.LIMIT_EXCEEDED" in codes
        assert any(i.severity == "info" for i in issues)  # secondary FINNIFTY

    def test_unhealthy_all_underlyings(self) -> None:
        client, _ = make_client(underlyings=("NIFTY",))
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        # Desired set present but never applied → active 0 → unhealthy
        health = client.get_health()
        assert "NIFTY" in health.unhealthy_underlyings
        assert health.overall_health is WebSocketHealthStatus.UNHEALTHY

    def test_partial_coverage_health(self) -> None:
        client, fake = make_client(underlyings=("NIFTY",))
        client.set_instruments(
            (make_instrument(11, "NIFTY"), make_instrument(12, "NIFTY"))
        )
        client.connect()
        client.apply_subscriptions()
        # Force one active removed without updating desired
        client._subscriptions.mark_unsubscribed((12,))
        fake.emit_ticks([{"instrument_token": 11, "last_price": 1.0}])
        health = client.get_health()
        assert "NIFTY" in health.degraded_underlyings
        assert any(i.issue_code == "KITE_WS.HEALTH.PARTIAL_COVERAGE" for i in health.issues)

    def test_on_close_callback(self) -> None:
        client, fake = make_client()
        client.connect()
        fake.on_close(fake)
        assert client.get_status() is WebSocketConnectionStatus.CLOSED

    def test_tick_publish_and_bad_token(self) -> None:
        bus = EventBus(EventBusPolicy())
        client, fake = make_client(
            event_bus=bus,
            publish_tick_events=True,
            underlyings=("NIFTY",),
        )
        # need publish_tick_events on config - make_client passes it
        client, fake = make_client(
            underlyings=("NIFTY",),
            event_bus=bus,
            publish_tick_events=True,
        )
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        fake.emit_ticks(
            [
                {"instrument_token": "bad", "last_price": 1.0},
                {"instrument_token": 11, "last_price": 1.0},
            ]
        )
        stats = client.get_statistics()
        assert stats.total_tick_count == 2

    def test_idempotent_connect(self) -> None:
        client, _ = make_client()
        client.connect()
        client.connect()
        assert client.get_status() is WebSocketConnectionStatus.CONNECTED

    def test_connect_without_connect_method(self) -> None:
        class NoConnect:
            pass

        config = KiteWebSocketConfig(enabled_underlyings=("NIFTY",))
        client = KiteWebSocketClient(
            config,
            api_key="k",
            access_token="t",
            clock=fixed_clock,
            ticker_factory=lambda _a, _b: NoConnect(),
        )
        with pytest.raises(KiteWebSocketConnectionError):
            client.connect()

    def test_health_unknown_never_connected(self) -> None:
        client, _ = make_client()
        health = client.get_health()
        assert health.overall_health is WebSocketHealthStatus.UNKNOWN

    def test_mode_override_on_instrument(self) -> None:
        client, fake = make_client()
        instrument = SubscriptionInstrument(
            instrument_token=11,
            underlying="NIFTY",
            quote_key="EX:A",
            exchange="NSE",
            tradingsymbol="A",
            mode=KiteWebSocketTickMode.QUOTE,
        )
        client.set_instruments((instrument, make_instrument(22, "BANKNIFTY")))
        client.connect()
        client.apply_subscriptions()
        assert fake.modes[11] == "quote"

    def test_deserialize_stats_naive_datetime(self) -> None:
        payload = serialize_websocket_statistics(
            make_client()[0].get_statistics()
        )
        # Force a naive timestamp path via direct parse helper usage in deserialize
        data = __import__("json").loads(payload)
        data["as_of"] = "2026-08-04T10:00:00"
        restored = deserialize_websocket_statistics(__import__("json").dumps(data))
        assert restored.as_of.tzinfo is not None

    def test_enabled_underlyings_method(self) -> None:
        client, _ = make_client(underlyings=("NIFTY", "SENSEX"))
        assert client.enabled_underlyings() == ("NIFTY", "SENSEX")

    def test_production_forces_fail_closed(self) -> None:
        config = KiteWebSocketConfig(
            environment_profile=EnvironmentProfile.PRODUCTION,
            enabled_underlyings=("NIFTY",),
            fail_closed_on_empty_instruments=False,
        )
        assert config.fail_closed_on_empty_instruments is True


class TestReconnect:
    """Explicit reconnect(): active teardown/reconnect distinct from SDK auto-reconnect."""

    def test_reconnect_reestablishes_connection(self) -> None:
        client, fake = make_client()
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        client.reconnect()
        assert client.get_status() is WebSocketConnectionStatus.CONNECTED
        assert fake.connected is True

    def test_reconnect_reapplies_desired_subscriptions(self) -> None:
        client, fake = make_client()
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        assert fake.subscribed == {11}
        client.reconnect()
        assert fake.subscribed == {11}

    def test_reconnect_increments_reconnect_count(self) -> None:
        client, _ = make_client()
        client.connect()
        assert client.get_statistics().reconnect_count == 0
        client.reconnect()
        client.reconnect()
        assert client.get_statistics().reconnect_count == 2

    def test_reconnect_from_disconnected_state(self) -> None:
        client, _ = make_client()
        client.reconnect()
        assert client.get_status() is WebSocketConnectionStatus.CONNECTED
        assert client.get_statistics().reconnect_count == 1


class TestHeartbeatMonitor:
    """check_heartbeat() and the background heartbeat monitor thread."""

    def _mutable_clock(self) -> tuple[list[datetime], Any]:
        times = [FIXED_NOW]

        def clock() -> datetime:
            return times[0]

        return times, clock

    def test_check_heartbeat_healthy_with_recent_tick(self) -> None:
        times, clock = self._mutable_clock()
        client, fake = make_client(clock=clock, underlyings=("NIFTY",))
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        fake.emit_ticks([{"instrument_token": 11, "last_price": 1.0}])
        assert client.check_heartbeat() is True

    def test_check_heartbeat_stale_after_silence_exceeds_threshold(self) -> None:
        times, clock = self._mutable_clock()
        client, fake = make_client(
            clock=clock,
            underlyings=("NIFTY",),
            heartbeat_silence_seconds=5.0,
        )
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        fake.emit_ticks([{"instrument_token": 11, "last_price": 1.0}])
        times[0] = FIXED_NOW + timedelta(seconds=10)
        assert client.check_heartbeat() is False

    def test_check_heartbeat_true_when_never_ticked(self) -> None:
        client, _ = make_client()
        client.connect()
        assert client.check_heartbeat() is True

    def test_check_heartbeat_true_when_disconnected(self) -> None:
        client, _ = make_client(heartbeat_silence_seconds=0.0)
        assert client.get_status() is WebSocketConnectionStatus.DISCONNECTED
        assert client.check_heartbeat() is True

    def test_start_heartbeat_monitor_invalid_interval(self) -> None:
        client, _ = make_client()
        with pytest.raises(KiteWebSocketConfigurationError):
            client.start_heartbeat_monitor(interval_seconds=0.0)

    def test_start_heartbeat_monitor_calls_on_stale_when_stale(self) -> None:
        client, fake = make_client(heartbeat_silence_seconds=0.0)
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        fake.emit_ticks([{"instrument_token": 11, "last_price": 1.0}])
        fired = threading.Event()
        client.start_heartbeat_monitor(interval_seconds=0.01, on_stale=fired.set)
        try:
            assert fired.wait(timeout=2.0)
        finally:
            client.stop_heartbeat_monitor()

    def test_start_heartbeat_monitor_does_not_fire_when_healthy(self) -> None:
        client, fake = make_client(heartbeat_silence_seconds=30.0)
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        fake.emit_ticks([{"instrument_token": 11, "last_price": 1.0}])
        fired = threading.Event()
        client.start_heartbeat_monitor(interval_seconds=0.01, on_stale=fired.set)
        try:
            assert fired.wait(timeout=0.1) is False
        finally:
            client.stop_heartbeat_monitor()

    def test_start_heartbeat_monitor_idempotent(self) -> None:
        client, _ = make_client(heartbeat_silence_seconds=30.0)
        client.connect()
        client.start_heartbeat_monitor(interval_seconds=0.05)
        first_thread = client._heartbeat_thread
        client.start_heartbeat_monitor(interval_seconds=0.05)
        second_thread = client._heartbeat_thread
        assert first_thread is second_thread
        client.stop_heartbeat_monitor()

    def test_stop_heartbeat_monitor_safe_when_never_started(self) -> None:
        client, _ = make_client()
        client.stop_heartbeat_monitor()

    def test_default_on_stale_triggers_reconnect(self) -> None:
        client, fake = make_client(heartbeat_silence_seconds=0.0)
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        fake.emit_ticks([{"instrument_token": 11, "last_price": 1.0}])
        calls: list[int] = []
        original_reconnect = client.reconnect

        def spy() -> None:
            calls.append(1)
            original_reconnect()

        client.reconnect = spy  # type: ignore[method-assign]
        client.start_heartbeat_monitor(interval_seconds=0.01)
        try:
            deadline = time.time() + 2.0
            while not calls and time.time() < deadline:
                time.sleep(0.01)
        finally:
            client.stop_heartbeat_monitor()
        assert calls


class TestDynamicSubscribe:
    """add_instruments() / remove_instruments(): client-facing dynamic subscribe."""

    def test_add_instruments_while_connected_applies_immediately(self) -> None:
        client, fake = make_client()
        client.connect()
        event = client.add_instruments((make_instrument(11, "NIFTY"),))
        assert event is not None
        assert event.subscribed_tokens == (11,)
        assert fake.subscribed == {11}

    def test_add_instruments_while_disconnected_only_updates_desired(self) -> None:
        client, fake = make_client()
        result = client.add_instruments((make_instrument(11, "NIFTY"),))
        assert result is None
        assert fake.subscribed == set()
        assert any(i.instrument_token == 11 for i in client._subscriptions.desired())

    def test_remove_instruments_while_connected_applies_immediately(self) -> None:
        client, fake = make_client()
        client.connect()
        client.add_instruments((make_instrument(11, "NIFTY"),))
        event = client.remove_instruments([11])
        assert event is not None
        assert event.unsubscribed_tokens == (11,)
        assert fake.subscribed == set()

    def test_remove_instruments_while_disconnected_only_updates_desired(self) -> None:
        client, _ = make_client()
        client.set_instruments((make_instrument(11, "NIFTY"),))
        result = client.remove_instruments([11])
        assert result is None
        assert client._subscriptions.desired() == ()

    def test_add_instruments_merges_with_existing_desired_set(self) -> None:
        client, fake = make_client()
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        client.add_instruments((make_instrument(22, "BANKNIFTY"),))
        assert fake.subscribed == {11, 22}


class TestGracefulShutdown:
    """shutdown(): stops the heartbeat monitor and closes the connection cleanly."""

    def test_shutdown_stops_monitor_and_disconnects(self) -> None:
        client, fake = make_client(heartbeat_silence_seconds=30.0)
        client.set_instruments((make_instrument(11, "NIFTY"),))
        client.connect()
        client.apply_subscriptions()
        client.start_heartbeat_monitor(interval_seconds=0.05)
        client.shutdown()
        assert client.get_status() is WebSocketConnectionStatus.CLOSED
        assert fake.connected is False
        assert client._heartbeat_thread is None

    def test_shutdown_clears_handlers(self) -> None:
        client, _ = make_client()
        client.set_tick_handler(lambda tick: None)
        client.set_error_handler(lambda event: None)
        client.set_connection_handler(lambda event: None)
        client.connect()
        client.shutdown()
        assert client._tick_handler is None
        assert client._error_handler is None
        assert client._connection_handler is None

    def test_shutdown_safe_when_never_connected(self) -> None:
        client, _ = make_client()
        client.shutdown()
        assert client.get_status() is WebSocketConnectionStatus.DISCONNECTED

    def test_shutdown_idempotent(self) -> None:
        client, _ = make_client()
        client.connect()
        client.shutdown()
        client.shutdown()
        assert client.get_status() is WebSocketConnectionStatus.CLOSED


class TestDashboardIntegration:
    """Dashboard connection-status mapping for the WebSocket manager."""

    def _health_report(
        self,
        status: WebSocketConnectionStatus,
        *,
        issues: tuple[WebSocketHealthIssue, ...] = (),
    ) -> WebSocketHealthReport:
        stats = WebSocketStatistics(
            as_of=FIXED_NOW,
            connection_status=status,
            total_desired_instruments=1,
            total_active_instruments=1 if status is WebSocketConnectionStatus.CONNECTED else 0,
            total_tick_count=0,
            last_tick_at=None,
            reconnect_count=0,
            handler_error_count=0,
            enabled_underlyings=("NIFTY",),
            per_underlying=(),
        )
        return WebSocketHealthReport(
            report_id="r1",
            as_of=FIXED_NOW,
            overall_health=WebSocketHealthStatus.HEALTHY,
            connection_status=status,
            enabled_underlyings=("NIFTY",),
            healthy_underlyings=(),
            degraded_underlyings=(),
            unhealthy_underlyings=(),
            active_subscription_count=stats.total_active_instruments,
            issues=issues,
            statistics=stats,
        )

    def test_build_dashboard_connection_snapshot_connected(self) -> None:
        report = self._health_report(WebSocketConnectionStatus.CONNECTED)
        snapshot = build_dashboard_connection_snapshot(report)
        assert isinstance(snapshot, WebSocketDashboardConnectionSnapshot)
        assert snapshot.state == "connected"
        assert snapshot.connected is True

    def test_build_dashboard_connection_snapshot_disconnected(self) -> None:
        report = self._health_report(WebSocketConnectionStatus.DISCONNECTED)
        snapshot = build_dashboard_connection_snapshot(report)
        assert snapshot.state == "disconnected"
        assert snapshot.connected is False

    def test_build_dashboard_connection_snapshot_reconnecting(self) -> None:
        report = self._health_report(WebSocketConnectionStatus.RECONNECTING)
        snapshot = build_dashboard_connection_snapshot(report)
        assert snapshot.state == "reconnecting"
        assert snapshot.connected is False

    def test_build_dashboard_connection_snapshot_uses_issue_message(self) -> None:
        issue = WebSocketHealthIssue(
            issue_code="KITE_WS.HEALTH.UNDERLYING_SILENT",
            severity="warning",
            message="Underlying NIFTY is silent.",
            underlying="NIFTY",
        )
        report = self._health_report(
            WebSocketConnectionStatus.CONNECTED, issues=(issue,)
        )
        snapshot = build_dashboard_connection_snapshot(report)
        assert snapshot.message == "Underlying NIFTY is silent."

    def test_classify_dashboard_index_status_offline_when_unsubscribed(self) -> None:
        config = default_kite_websocket_config(enabled_underlyings=("NIFTY",))
        entry = UnderlyingStreamStats(
            underlying="NIFTY",
            support_tier=UnderlyingSupportTier.PRIMARY,
            desired_instrument_count=1,
            active_instrument_count=0,
            tick_count=0,
            last_tick_at=None,
            seconds_since_last_tick=None,
            subscribe_success_count=0,
            subscribe_failure_count=0,
            error_count=0,
        )
        assert classify_dashboard_index_status(entry, config) == "OFFLINE"

    def test_classify_dashboard_index_status_unknown_before_first_tick(self) -> None:
        config = default_kite_websocket_config(enabled_underlyings=("NIFTY",))
        entry = UnderlyingStreamStats(
            underlying="NIFTY",
            support_tier=UnderlyingSupportTier.PRIMARY,
            desired_instrument_count=1,
            active_instrument_count=1,
            tick_count=0,
            last_tick_at=None,
            seconds_since_last_tick=None,
            subscribe_success_count=1,
            subscribe_failure_count=0,
            error_count=0,
        )
        assert classify_dashboard_index_status(entry, config) == "UNKNOWN"

    def test_classify_dashboard_index_status_live_within_threshold(self) -> None:
        config = KiteWebSocketConfig(
            enabled_underlyings=("NIFTY",), per_underlying_silence_seconds=5.0
        )
        entry = UnderlyingStreamStats(
            underlying="NIFTY",
            support_tier=UnderlyingSupportTier.PRIMARY,
            desired_instrument_count=1,
            active_instrument_count=1,
            tick_count=5,
            last_tick_at=FIXED_NOW,
            seconds_since_last_tick=1.0,
            subscribe_success_count=1,
            subscribe_failure_count=0,
            error_count=0,
        )
        assert classify_dashboard_index_status(entry, config) == "LIVE"

    def test_classify_dashboard_index_status_delayed_past_threshold(self) -> None:
        config = KiteWebSocketConfig(
            enabled_underlyings=("NIFTY",), per_underlying_silence_seconds=5.0
        )
        entry = UnderlyingStreamStats(
            underlying="NIFTY",
            support_tier=UnderlyingSupportTier.PRIMARY,
            desired_instrument_count=1,
            active_instrument_count=1,
            tick_count=5,
            last_tick_at=FIXED_NOW,
            seconds_since_last_tick=9.0,
            subscribe_success_count=1,
            subscribe_failure_count=0,
            error_count=0,
        )
        assert classify_dashboard_index_status(entry, config) == "DELAYED"

    def test_client_get_dashboard_connection_snapshot(self) -> None:
        client, _ = make_client()
        client.connect()
        snapshot = client.get_dashboard_connection_snapshot()
        assert snapshot.state == "connected"
        assert snapshot.connected is True

    def test_client_get_dashboard_index_connection_statuses(self) -> None:
        client, fake = make_client(underlyings=("NIFTY", "BANKNIFTY"))
        client.set_instruments(
            (make_instrument(11, "NIFTY"), make_instrument(22, "BANKNIFTY"))
        )
        client.connect()
        client.apply_subscriptions()
        fake.emit_ticks([{"instrument_token": 11, "last_price": 1.0}])
        statuses = client.get_dashboard_index_connection_statuses()
        assert statuses["NIFTY"] == "LIVE"
        assert statuses["BANKNIFTY"] == "UNKNOWN"


class TestThreadSafeDynamicSubscribe:
    """Concurrent ticks + dynamic subscribe/unsubscribe stay thread-safe."""

    def test_concurrent_add_remove_and_ticks(self) -> None:
        client, fake = make_client(underlyings=("NIFTY", "BANKNIFTY"))
        client.connect()
        barrier = threading.Barrier(3)
        errors: list[BaseException] = []

        def subscriber() -> None:
            try:
                barrier.wait()
                for i in range(20):
                    client.add_instruments((make_instrument(100 + i, "NIFTY"),))
                    client.remove_instruments([100 + i])
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def ticker_emitter() -> None:
            try:
                barrier.wait()
                for _ in range(20):
                    fake.emit_ticks([{"instrument_token": 22, "last_price": 2.0}])
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def reader() -> None:
            try:
                barrier.wait()
                for _ in range(20):
                    client.get_health()
                    client.get_dashboard_index_connection_statuses()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(subscriber),
                pool.submit(ticker_emitter),
                pool.submit(reader),
            ]
            for future in futures:
                future.result()
        assert not errors
