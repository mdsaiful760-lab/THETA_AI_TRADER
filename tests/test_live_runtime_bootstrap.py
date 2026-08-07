"""Unit tests for system.live_runtime_bootstrap."""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from broker.base_broker import Exchange, InstrumentRequest
from broker.instrument_loader import InstrumentLoader, default_instrument_loader_config
from broker.kite_authentication import (
    EnvironmentProfile,
    KiteAuthenticationConfig,
    KiteAuthenticator,
    TokenPersistenceMode,
    default_kite_authentication_config,
)
from broker.market_data_streaming import InstrumentRole
from dashboard.live_session_adapter import clear_live_handles, get_registered_live_handles
from system.live_runtime_bootstrap import (
    _KiteInstrumentMasterClient,
    _nearest_expiry_option_descriptors,
    bootstrap_live_runtime,
)
from tests.test_instrument_loader import row
from tests.test_kite_authentication import FakeKiteConnect
from tests.test_kite_websocket import FakeKiteTicker, make_client


@pytest.fixture(autouse=True)
def _clean_dashboard_handles():
    """Bootstrap tests register process-global dashboard handles; never leak them."""
    clear_live_handles()
    yield
    clear_live_handles()


def _build_catalog(rows_list):
    loader = InstrumentLoader(default_instrument_loader_config(enabled_underlyings=("NIFTY",)))
    return loader.load_from_rows(rows_list)


class TestNearestExpiryOptionDescriptors:
    def test_selects_only_nearest_expiry_nifty_options(self) -> None:
        rows_list = [
            row(1, "INDEX", "NIFTY 50", name="NIFTY", lot="1"),
            row(10, "CE", "NIFTY24500CE", expiry="2026-08-07", strike="24500"),
            row(11, "PE", "NIFTY24500PE", expiry="2026-08-07", strike="24500"),
            row(20, "CE", "NIFTY24500SEPCE", expiry="2026-09-24", strike="24500"),
            row(30, "CE", "BANKNIFTY50000CE", name="BANKNIFTY", expiry="2026-08-07", strike="50000"),
            row(40, "FUT", "NIFTYFUT", expiry="2026-08-28"),
        ]
        catalog = _build_catalog(rows_list)

        descriptors = _nearest_expiry_option_descriptors(catalog, underlying="NIFTY")

        assert len(descriptors) == 2
        instrument_tokens = {d.instrument_token for d in descriptors}
        assert instrument_tokens == {10, 11}
        for descriptor in descriptors:
            assert descriptor.underlying == "NIFTY"
            assert descriptor.expiry == "2026-08-07"
            assert descriptor.option_type in ("CE", "PE")
            assert descriptor.instrument_role in (InstrumentRole.OPTION_CE, InstrumentRole.OPTION_PE)
            assert descriptor.support_tier is None  # never pass the wrong-module enum through

    def test_returns_empty_tuple_when_no_options_match(self) -> None:
        catalog = _build_catalog([row(1, "INDEX", "NIFTY 50", name="NIFTY", lot="1")])

        assert _nearest_expiry_option_descriptors(catalog, underlying="NIFTY") == ()


class TestKiteInstrumentMasterClient:
    def test_adapts_fetch_instruments_to_the_master_client_protocol(self) -> None:
        calls: list[InstrumentRequest] = []

        class _FakeBrokerClient:
            def fetch_instruments(self, request: InstrumentRequest):
                calls.append(request)
                return ({"instrument_token": "1"},)

        adapter = _KiteInstrumentMasterClient(_FakeBrokerClient())

        rows_out = adapter.fetch_instrument_rows(exchange="nfo")

        assert rows_out == ({"instrument_token": "1"},)
        assert len(calls) == 1
        assert calls[0].exchange is Exchange.NFO


class TestBootstrapGracefulDegradation:
    """No credentials/network available: every later-stage component still starts."""

    def test_bootstrap_never_crashes_and_degrades_honestly(self) -> None:
        handles = bootstrap_live_runtime(env={}, ai_loop_interval_seconds=100.0)
        try:
            status = handles.status
            assert status.authenticated is False
            assert status.websocket_connected is False
            assert status.instruments_loaded == 0
            assert status.bridge_running is False

            assert status.streaming_running is True
            assert status.ai_loop_running is True
            assert status.paper_runtime_running is True
            assert status.dashboard_registered is True

            registered = get_registered_live_handles()
            assert registered is not None
            assert registered.paper_runner is handles.paper_runtime.paper_trading_runner
        finally:
            handles.stop()

    def test_summary_lines_mark_every_stage(self) -> None:
        handles = bootstrap_live_runtime(env={}, ai_loop_interval_seconds=100.0)
        try:
            lines = handles.status.summary_lines()
            assert len(lines) == 8
            assert any("Zerodha session" in line and "✗" in line for line in lines)
            assert any("PaperTradingRuntime running" in line and "✓" in line for line in lines)
        finally:
            handles.stop()


class TestBootstrapFullSuccess:
    """Every stage wired with real engines behind fake network/SDK boundaries."""

    def test_bootstrap_reaches_every_component_when_fully_wired(self, tmp_path) -> None:
        fake_sdk = FakeKiteConnect("api-key-123456")
        auth_config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.PAPER,
            persistence_mode=TokenPersistenceMode.FILE,
            token_store_path=str(tmp_path / "kite_session.json"),
            require_profile_probe=True,
            allow_env_file_persistence=False,
            fail_closed_on_expiry=True,
            runner_kind="test",
        )
        real_authenticator = KiteAuthenticator(
            auth_config,
            api_key="api-key-123456",
            api_secret="api-secret-123456",
            access_token="fake-access-token",
            sdk_factory=lambda key: fake_sdk,
            env={},
            clock=lambda: datetime(2026, 8, 4, 4, 30, tzinfo=timezone.utc),
        )

        fake_ticker = FakeKiteTicker("api-key-123456", "fake-access-token")
        real_ws_client, _ = make_client(underlyings=("NIFTY",), ticker=fake_ticker)

        rows_list = [
            row(1, "INDEX", "NIFTY 50", name="NIFTY", lot="1"),
            row(10, "CE", "NIFTY24500CE", expiry="2026-08-07", strike="24500"),
            row(11, "PE", "NIFTY24500PE", expiry="2026-08-07", strike="24500"),
        ]
        catalog = _build_catalog(rows_list)

        class _StubInstrumentLoader:
            def load_from_broker(self, *, exchanges):
                return catalog

        class _StubBrokerClient:
            def connect(self) -> None:
                pass

        handles = bootstrap_live_runtime(
            underlying="NIFTY",
            authenticator_factory=lambda config, **kwargs: real_authenticator,
            ws_client_factory=lambda config, **kwargs: real_ws_client,
            broker_client_factory=lambda session: _StubBrokerClient(),
            instrument_loader_factory=lambda config, **kwargs: _StubInstrumentLoader(),
            ai_loop_interval_seconds=100.0,
        )
        try:
            status = handles.status
            assert status.authenticated is True, status.authentication_note
            assert status.websocket_connected is True, status.websocket_note
            assert status.instruments_loaded == 2, status.instruments_note
            assert status.bridge_running is True, status.bridge_note
            assert status.streaming_running is True
            assert status.ai_loop_running is True
            assert status.paper_runtime_running is True
            assert status.dashboard_registered is True

            assert handles.bridge is not None
            assert handles.bridge.is_started
        finally:
            handles.stop()
