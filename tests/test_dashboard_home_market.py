"""Unit tests for Home dashboard market data integration."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dashboard import default_dashboard_ui_config
from dashboard.components.index_ticker import render_index_strip
from dashboard.config import DashboardConfigurationError, DashboardUiConfig
from dashboard.dashboard_facade import (
    HOME_MARKET_INDEX_SYMBOLS,
    DashboardFacade,
    DashboardIntegrationFacade,
    FacadeHomeMarketIndices,
    HomeIndexQuote,
    empty_home_market_indices,
    home_indices_to_quote_views,
)
from dashboard.pages import home as home_page
from dashboard.utils.polling import (
    home_market_refresh_interval_ms,
    should_home_market_autorefresh,
)
from dashboard.view_models import (
    DashboardRenderContext,
    IndexQuoteView,
    PLACEHOLDER,
    default_index_quotes,
)


FIXED_NOW = datetime(2026, 8, 5, 12, 1, 3, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = REPO_ROOT / "dashboard"


def _connected_session(**extra: object) -> SimpleNamespace:
    """Build a connected session stub with optional home-market accessors."""
    base: dict[str, object] = {
        "get_health": MagicMock(
            return_value=SimpleNamespace(
                session_state=SimpleNamespace(value="running"),
                overall_status=SimpleNamespace(value="healthy"),
                broker_connection=SimpleNamespace(state="connected", connected=True),
                message="ok",
            )
        ),
        "get_runtime_state": MagicMock(
            return_value=SimpleNamespace(
                execution_mode="PAPER",
                market_status="OPEN",
            )
        ),
    }
    base.update(extra)
    return SimpleNamespace(**base)


class TestOfflineHomeMarketIndices:
    """T01/T02: Offline facade returns ordered placeholders."""

    def test_offline_returns_four_offline_placeholders(self) -> None:
        facade = DashboardIntegrationFacade(session=None, clock=lambda: FIXED_NOW)
        payload = facade.get_home_market_indices()
        assert len(payload.indices) == 4
        assert payload.source == "offline"
        assert payload.facade_connected is False
        for quote in payload.indices:
            assert quote.ltp == PLACEHOLDER
            assert quote.change_abs == PLACEHOLDER
            assert quote.change_pct == PLACEHOLDER
            assert quote.last_update == PLACEHOLDER
            assert quote.connection_status == "OFFLINE"

    def test_symbol_order(self) -> None:
        facade = DashboardIntegrationFacade(session=None, clock=lambda: FIXED_NOW)
        symbols = tuple(q.symbol for q in facade.get_home_market_indices().indices)
        assert symbols == HOME_MARKET_INDEX_SYMBOLS
        assert symbols == ("NIFTY", "BANKNIFTY", "SENSEX", "INDIA VIX")


class TestLiveHomeMarketMapping:
    """T03/T04/T05: Live stub mapping and partial fill."""

    def test_live_stub_maps_fields(self) -> None:
        quotes = (
            SimpleNamespace(
                symbol="NIFTY",
                ltp=24512.40,
                change=85.20,
                change_percent=0.35,
                timestamp=FIXED_NOW,
                connection_status="LIVE",
            ),
            SimpleNamespace(
                symbol="BANKNIFTY",
                last_price=52100.15,
                net_change=-120.40,
                pchange=-0.23,
                exchange_timestamp=FIXED_NOW,
            ),
            SimpleNamespace(
                symbol="SENSEX",
                ltp=81234.50,
                change_abs=10.0,
                change_pct=0.01,
                last_update="2026-08-05 12:01:03 UTC",
            ),
            SimpleNamespace(
                symbol="INDIA VIX",
                ltp=13.22,
                change=-0.15,
                change_percent=-1.12,
                timestamp=FIXED_NOW,
                is_stale=False,
            ),
        )
        session = _connected_session(
            get_index_quotes=MagicMock(return_value=quotes),
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        payload = facade.get_home_market_indices()
        assert payload.source == "live"
        nifty = payload.indices[0]
        assert nifty.symbol == "NIFTY"
        assert nifty.ltp == "24,512.40"
        assert nifty.change_abs == "+85.20"
        assert nifty.change_pct == "+0.35%"
        assert nifty.last_update == "2026-08-05 12:01:03 UTC"
        assert nifty.connection_status == "LIVE"
        bank = payload.indices[1]
        assert bank.ltp == "52,100.15"
        assert bank.change_abs == "-120.40"
        assert bank.change_pct == "-0.23%"
        assert bank.connection_status == "LIVE"

    def test_partial_upstream_fills_missing_with_placeholders(self) -> None:
        session = _connected_session(
            get_index_quotes=MagicMock(
                return_value=(
                    SimpleNamespace(
                        symbol="NIFTY",
                        ltp=100.0,
                        change=1.0,
                        change_percent=1.0,
                        timestamp=FIXED_NOW,
                    ),
                )
            ),
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        payload = facade.get_home_market_indices()
        assert payload.indices[0].ltp == "100.00"
        assert payload.indices[0].connection_status == "LIVE"
        for quote in payload.indices[1:]:
            assert quote.ltp == PLACEHOLDER
            assert quote.change_abs == PLACEHOLDER
            assert quote.change_pct == PLACEHOLDER
            assert quote.connection_status == "UNKNOWN"

    def test_index_quote_view_value_equals_ltp(self) -> None:
        payload = FacadeHomeMarketIndices(
            indices=(
                HomeIndexQuote(
                    symbol="NIFTY",
                    ltp="24,512.40",
                    change_abs="+85.20",
                    change_pct="+0.35%",
                    last_update="2026-08-05 12:01:03 UTC",
                    connection_status="LIVE",
                ),
                *empty_home_market_indices(as_of=FIXED_NOW).indices[1:],
            ),
            as_of=FIXED_NOW,
            source="live",
            market_status="OPEN",
            facade_connected=True,
        )
        mapped = home_indices_to_quote_views(payload)
        assert mapped[0].value == "24,512.40"
        assert mapped[0].change == "+85.20 (+0.35%)"
        assert mapped[0].change_abs == "+85.20"
        assert mapped[0].connection_status == "LIVE"


class TestIndexTickerRender:
    """T06: Index strip renders placeholders without raising."""

    def test_render_index_strip_placeholders(self) -> None:
        quotes = default_index_quotes(HOME_MARKET_INDEX_SYMBOLS)
        columns = [MagicMock() for _ in quotes]
        with patch("dashboard.components.index_ticker.st") as st_mock:
            st_mock.columns.return_value = columns
            for column in columns:
                column.__enter__ = MagicMock(return_value=column)
                column.__exit__ = MagicMock(return_value=False)
            render_index_strip(quotes)
            assert st_mock.columns.called
            assert st_mock.markdown.call_count == 4
            html = "".join(
                call.args[0] for call in st_mock.markdown.call_args_list
            )
            assert "NIFTY" in html
            assert "OFFLINE" in html
            assert PLACEHOLDER in html


class TestHomeRefreshConfig:
    """T07/T08: Home refresh defaults and polling helper."""

    def test_home_refresh_defaults_to_one_second(self) -> None:
        config = default_dashboard_ui_config()
        assert config.home_market_refresh_seconds == 1.0
        assert config.enable_home_market_autorefresh is True
        assert home_market_refresh_interval_ms(config) == 1000

    def test_home_refresh_validation(self) -> None:
        with pytest.raises(DashboardConfigurationError) as exc:
            DashboardUiConfig(home_market_refresh_seconds=0.0)
        assert exc.value.code == "CFG-DASH-HOME-001"

    def test_should_home_market_autorefresh_after_interval(self) -> None:
        config = default_dashboard_ui_config()
        now = FIXED_NOW
        assert should_home_market_autorefresh(
            config, last_refresh_at=None, now=now
        )
        assert not should_home_market_autorefresh(
            config,
            last_refresh_at=now,
            now=now + timedelta(milliseconds=500),
        )
        assert should_home_market_autorefresh(
            config,
            last_refresh_at=now,
            now=now + timedelta(seconds=1.0),
        )


class TestNoForbiddenImports:
    """T09: No broker/strategy imports on Home market path."""

    FORBIDDEN_MODULES = ("broker", "strategy", "kiteconnect")

    def _imports_from(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        return found

    def test_home_path_has_no_broker_or_strategy_imports(self) -> None:
        paths = (
            DASHBOARD_ROOT / "pages" / "home.py",
            DASHBOARD_ROOT / "components" / "index_ticker.py",
            DASHBOARD_ROOT / "dashboard_facade.py",
        )
        for path in paths:
            imports = self._imports_from(path)
            for forbidden in self.FORBIDDEN_MODULES:
                assert forbidden not in imports, f"{path} imports {forbidden}"


class TestPresentationAdapter:
    """T10: Presentation adapter populates HomePageView.indices."""

    def test_adapter_uses_home_market_indices(self) -> None:
        facade = DashboardFacade(session=None, clock=lambda: FIXED_NOW)
        presentation = facade.as_presentation_facade()
        home = presentation.get_home_snapshot()
        assert len(home.indices) == 4
        assert tuple(q.symbol for q in home.indices) == HOME_MARKET_INDEX_SYMBOLS
        assert all(q.connection_status == "OFFLINE" for q in home.indices)
        assert all(isinstance(q, IndexQuoteView) for q in home.indices)
        assert all(q.value == PLACEHOLDER for q in home.indices)

    def test_presentation_exposes_get_home_market_indices(self) -> None:
        facade = DashboardFacade(session=None, clock=lambda: FIXED_NOW)
        presentation = facade.as_presentation_facade()
        payload = presentation.get_home_market_indices()
        assert len(payload.indices) == 4


class TestHomePageResolve:
    """Smoke: Home page resolves indices via facade only."""

    def test_resolve_offline_indices(self) -> None:
        facade = DashboardFacade(session=None, clock=lambda: FIXED_NOW)
        ctx = DashboardRenderContext(
            config=default_dashboard_ui_config(),
            facade=facade.as_presentation_facade(),
            session=SimpleNamespace(
                active_page="home",
                last_error=None,
                last_refresh_at=None,
            ),
            clock=lambda: FIXED_NOW,
            version="1.0.0",
        )
        views = home_page.resolve_home_indices(ctx)
        assert len(views) == 4
        assert views[0].symbol == "NIFTY"
        assert views[0].connection_status == "OFFLINE"

    def test_resolve_handles_facade_exception(self) -> None:
        broken = MagicMock()
        broken.get_home_market_indices.side_effect = RuntimeError("boom")
        broken.get_home_snapshot.side_effect = RuntimeError("boom")
        ctx = DashboardRenderContext(
            config=default_dashboard_ui_config(),
            facade=broken,
            session=SimpleNamespace(
                active_page="home",
                last_error=None,
                last_refresh_at=None,
            ),
            clock=lambda: FIXED_NOW,
            version="1.0.0",
        )
        with patch("dashboard.pages.home.render_error"):
            views = home_page.resolve_home_indices(ctx)
        assert len(views) == 4
        assert all(v.connection_status == "OFFLINE" for v in views)
