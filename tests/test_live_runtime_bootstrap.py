"""Unit tests for system.live_runtime_bootstrap."""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from broker.base_broker import Exchange, InstrumentRequest
from broker.instrument_loader import (
    InstrumentLoader,
    InstrumentLoaderConfig,
    default_instrument_loader_config,
)
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
    _index_and_volatility_descriptors,
    _nearest_expiry_option_descriptors,
    _with_index_lot_size_patched,
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


def _build_catalog(rows_list, *, enabled_underlyings=("NIFTY",)):
    loader = InstrumentLoader(default_instrument_loader_config(enabled_underlyings=enabled_underlyings))
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


_REAL_NSE_INDEX_ROWS = [
    {
        "instrument_token": "256265", "exchange_token": "1001", "tradingsymbol": "NIFTY 50",
        "name": "NIFTY 50", "expiry": "", "strike": "", "tick_size": "0.05", "lot_size": "0",
        "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE",
    },
    {
        "instrument_token": "260105", "exchange_token": "1016", "tradingsymbol": "NIFTY BANK",
        "name": "NIFTY BANK", "expiry": "", "strike": "", "tick_size": "0.05", "lot_size": "0",
        "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE",
    },
    {
        "instrument_token": "264969", "exchange_token": "1035", "tradingsymbol": "INDIA VIX",
        "name": "INDIA VIX", "expiry": "", "strike": "", "tick_size": "0.05", "lot_size": "0",
        "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE",
    },
    {
        "instrument_token": "999999", "exchange_token": "999", "tradingsymbol": "NIFTY 500",
        "name": "NIFTY 500", "expiry": "", "strike": "", "tick_size": "0.05", "lot_size": "0",
        "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE",
    },
]


class TestIndexLotSizePatch:
    """Real Zerodha NSE index rows report lot_size=0 and get silently
    dropped by InstrumentLoader's own validation without this patch."""

    def test_patches_lot_size_only_for_the_three_target_symbols(self) -> None:
        patched = _with_index_lot_size_patched(_REAL_NSE_INDEX_ROWS)

        by_symbol = {row["tradingsymbol"]: row for row in patched}
        assert by_symbol["NIFTY 50"]["lot_size"] == 1
        assert by_symbol["NIFTY BANK"]["lot_size"] == 1
        assert by_symbol["INDIA VIX"]["lot_size"] == 1
        # NIFTY 500 is not one of the three symbols the dashboard reads —
        # left untouched, proving the patch is narrowly targeted.
        assert by_symbol["NIFTY 500"]["lot_size"] == "0"

    def test_does_not_mutate_the_input_rows(self) -> None:
        original = [dict(row) for row in _REAL_NSE_INDEX_ROWS]

        _with_index_lot_size_patched(_REAL_NSE_INDEX_ROWS)

        assert _REAL_NSE_INDEX_ROWS == original

    def test_does_not_override_a_real_nonzero_lot_size(self) -> None:
        rows = [dict(_REAL_NSE_INDEX_ROWS[0])]
        rows[0]["lot_size"] = "5"

        patched = _with_index_lot_size_patched(rows)

        assert patched[0]["lot_size"] == "5"


class TestIndexAndVolatilityDescriptors:
    def test_builds_spot_and_volatility_descriptors_with_correct_roles(self) -> None:
        loader = InstrumentLoader(
            InstrumentLoaderConfig(
                enabled_underlyings=("NIFTY", "BANKNIFTY"),
                environment_profile=EnvironmentProfile.PAPER,
                cache_enabled=False,
                volatility_index_map={"NIFTY": "INDIA VIX"},
            )
        )
        catalog = loader.load_from_rows(_with_index_lot_size_patched(_REAL_NSE_INDEX_ROWS))

        descriptors = _index_and_volatility_descriptors(
            catalog, spot_underlyings=("NIFTY", "BANKNIFTY"), volatility_underlying="NIFTY"
        )

        by_symbol = {d.tradingsymbol: d for d in descriptors}
        assert set(by_symbol) == {"NIFTY 50", "NIFTY BANK", "INDIA VIX"}
        assert by_symbol["NIFTY 50"].underlying == "NIFTY"
        assert by_symbol["NIFTY 50"].instrument_role is InstrumentRole.SPOT
        assert by_symbol["NIFTY BANK"].underlying == "BANKNIFTY"
        assert by_symbol["NIFTY BANK"].instrument_role is InstrumentRole.SPOT
        assert by_symbol["INDIA VIX"].underlying == "NIFTY"
        assert by_symbol["INDIA VIX"].instrument_role is InstrumentRole.VOLATILITY_INDEX

    def test_omits_spot_symbols_not_in_spot_underlyings(self) -> None:
        loader = InstrumentLoader(
            InstrumentLoaderConfig(
                enabled_underlyings=("NIFTY",),
                environment_profile=EnvironmentProfile.PAPER,
                cache_enabled=False,
                volatility_index_map={"NIFTY": "INDIA VIX"},
            )
        )
        catalog = loader.load_from_rows(
            _with_index_lot_size_patched(
                [r for r in _REAL_NSE_INDEX_ROWS if r["tradingsymbol"] != "NIFTY BANK"]
            )
        )

        descriptors = _index_and_volatility_descriptors(
            catalog, spot_underlyings=("NIFTY",), volatility_underlying="NIFTY"
        )

        assert {d.tradingsymbol for d in descriptors} == {"NIFTY 50", "INDIA VIX"}


def _disabled_persistence_authenticator_factory(config, **kwargs):
    """Force TokenPersistenceMode.DISABLED so these tests never accidentally
    restore a real session left on disk at the shared default token store
    path (broker/kite_authentication.py's DEFAULT_STORE_PATH) by an actual
    live run elsewhere on this machine — env={} alone does not isolate
    file-backed persistence, only credential resolution."""
    from dataclasses import replace as _replace

    return KiteAuthenticator(_replace(config, persistence_mode=TokenPersistenceMode.DISABLED), **kwargs)


class TestBootstrapGracefulDegradation:
    """No credentials/network available: every later-stage component still starts."""

    def test_bootstrap_never_crashes_and_degrades_honestly(self) -> None:
        handles = bootstrap_live_runtime(
            env={},
            authenticator_factory=_disabled_persistence_authenticator_factory,
            ai_loop_interval_seconds=100.0,
        )
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
        handles = bootstrap_live_runtime(
            env={},
            authenticator_factory=_disabled_persistence_authenticator_factory,
            ai_loop_interval_seconds=100.0,
        )
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
        real_ws_client, _ = make_client(underlyings=("NIFTY", "BANKNIFTY"), ticker=fake_ticker)

        option_rows = [
            row(10, "CE", "NIFTY24500CE", expiry="2026-08-07", strike="24500"),
            row(11, "PE", "NIFTY24500PE", expiry="2026-08-07", strike="24500"),
            row(20, "CE", "BANKNIFTY52000CE", name="BANKNIFTY", expiry="2026-08-07", strike="52000"),
            row(21, "PE", "BANKNIFTY52000PE", name="BANKNIFTY", expiry="2026-08-07", strike="52000"),
        ]
        option_catalog = _build_catalog(option_rows, enabled_underlyings=("NIFTY", "BANKNIFTY"))

        # Real Zerodha NSE index rows: instrument_type="EQ", lot_size=0.
        index_rows = [
            {
                "instrument_token": "256265", "exchange_token": "1001", "tradingsymbol": "NIFTY 50",
                "name": "NIFTY 50", "expiry": "", "strike": "", "tick_size": "0.05", "lot_size": "0",
                "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE",
            },
            {
                "instrument_token": "260105", "exchange_token": "1016", "tradingsymbol": "NIFTY BANK",
                "name": "NIFTY BANK", "expiry": "", "strike": "", "tick_size": "0.05", "lot_size": "0",
                "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE",
            },
            {
                "instrument_token": "264969", "exchange_token": "1035", "tradingsymbol": "INDIA VIX",
                "name": "INDIA VIX", "expiry": "", "strike": "", "tick_size": "0.05", "lot_size": "0",
                "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE",
            },
        ]

        class _StubInstrumentLoader:
            def __init__(self, config, *, master_client=None):
                self._config = config

            def load_from_broker(self, *, exchanges):
                return option_catalog

            def load_from_rows(self, rows, **kwargs):
                real_loader = InstrumentLoader(self._config)
                return real_loader.load_from_rows(rows, **kwargs)

        class _StubBrokerClient:
            def connect(self) -> None:
                pass

            def fetch_instruments(self, request):
                return tuple(index_rows)

        handles = bootstrap_live_runtime(
            underlying="NIFTY",
            authenticator_factory=lambda config, **kwargs: real_authenticator,
            ws_client_factory=lambda config, **kwargs: real_ws_client,
            broker_client_factory=lambda session: _StubBrokerClient(),
            instrument_loader_factory=lambda config, **kwargs: _StubInstrumentLoader(config, **kwargs),
            ai_loop_interval_seconds=100.0,
        )
        try:
            status = handles.status
            assert status.authenticated is True, status.authentication_note
            assert status.websocket_connected is True, status.websocket_note
            # 4 option contracts (NIFTY + BANKNIFTY) + 3 index/VIX instruments.
            assert status.instruments_loaded == 7, status.instruments_note
            assert status.bridge_running is True, status.bridge_note
            assert status.streaming_running is True
            assert status.ai_loop_running is True
            assert status.paper_runtime_running is True
            assert status.dashboard_registered is True

            assert handles.bridge is not None
            assert handles.bridge.is_started
        finally:
            handles.stop()
