"""Unit tests for Market dashboard page integration."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import MagicMock, patch

from dashboard.dashboard_facade import (
    HOME_MARKET_INDEX_SYMBOLS,
    DashboardFacade,
    DashboardIntegrationFacade,
)
from dashboard.facade import NullIntegrationFacade
from dashboard.pages import market as market_page
from dashboard.view_models import (
    DashboardRenderContext,
    DashboardSessionView,
    PLACEHOLDER,
    market_page_statistic_cards,
    market_regime_cards,
)


FIXED_NOW = datetime(2026, 8, 6, 8, 0, 0, tzinfo=timezone.utc)
DASHBOARD_ROOT = Path(__file__).resolve().parents[1] / "dashboard"


def _render_ctx(facade: object) -> DashboardRenderContext:
    """Build a minimal render context for Market page tests."""
    from dashboard import default_dashboard_ui_config

    return DashboardRenderContext(
        config=default_dashboard_ui_config(),
        facade=facade,  # type: ignore[arg-type]
        session=DashboardSessionView(
            active_page="market",
            last_error=None,
            last_refresh_at=None,
            facade_action_pending=False,
            ui_prefs=MappingProxyType({}),
        ),
        clock=lambda: FIXED_NOW,
        version="1.0.0",
    )


class TestOfflineMarketPage:
    """T01/T02: Offline Market page placeholders."""

    def test_offline_has_four_offline_index_cards(self) -> None:
        view = NullIntegrationFacade().get_market_snapshot()
        assert len(view.indices) == 4
        assert tuple(q.symbol for q in view.indices) == HOME_MARKET_INDEX_SYMBOLS
        assert all(q.connection_status == "OFFLINE" for q in view.indices)
        assert all(q.value == PLACEHOLDER for q in view.indices)

    def test_offline_regime_and_stats_placeholders(self) -> None:
        view = DashboardFacade(session=None, clock=lambda: FIXED_NOW).as_presentation_facade().get_market_snapshot()
        assert view.market_regime == PLACEHOLDER
        assert view.ltp == PLACEHOLDER
        assert view.change == PLACEHOLDER
        assert view.volume == PLACEHOLDER
        assert view.connection_status == "OFFLINE"
        assert view.source == "offline"
        stats = market_page_statistic_cards(view)
        assert [card.label for card in stats] == [
            "LTP",
            "Change",
            "Volume",
            "Connection",
            "Last Update",
        ]
        assert market_regime_cards(view)[0].value == PLACEHOLDER


class TestLiveMarketPageMapping:
    """T03/T06: Live stub mapping through facade."""

    def test_live_stub_maps_snapshot_regime_and_indices(self) -> None:
        session = SimpleNamespace(
            get_health=MagicMock(
                return_value=SimpleNamespace(
                    session_state=SimpleNamespace(value="running"),
                    overall_status=SimpleNamespace(value="healthy"),
                    broker_connection=SimpleNamespace(state="connected", connected=True),
                    message="ok",
                )
            ),
            get_runtime_state=MagicMock(
                return_value=SimpleNamespace(execution_mode="PAPER", market_status="OPEN")
            ),
            get_market_snapshot=MagicMock(
                return_value=SimpleNamespace(
                    underlyings=["NIFTY", "BANKNIFTY", "SENSEX"],
                    selected_underlying="NIFTY",
                    ltp="24512.40",
                    change="+85.20",
                    volume="1.2M",
                    option_chain_columns=["strike", "type", "ltp"],
                    option_chain_rows=[("24500", "CE", "100")],
                )
            ),
            get_index_quotes=MagicMock(
                return_value=(
                    SimpleNamespace(
                        symbol="NIFTY",
                        ltp=24512.4,
                        change=85.2,
                        change_percent=0.35,
                        timestamp=FIXED_NOW,
                        connection_status="LIVE",
                    ),
                    SimpleNamespace(
                        symbol="BANKNIFTY",
                        ltp=52100.15,
                        change=-120.4,
                        change_percent=-0.23,
                        timestamp=FIXED_NOW,
                        connection_status="LIVE",
                    ),
                    SimpleNamespace(
                        symbol="SENSEX",
                        ltp=81234.5,
                        change=10.0,
                        change_percent=0.01,
                        timestamp=FIXED_NOW,
                        connection_status="LIVE",
                    ),
                    SimpleNamespace(
                        symbol="INDIA VIX",
                        ltp=13.22,
                        change=-0.15,
                        change_percent=-1.12,
                        timestamp=FIXED_NOW,
                        connection_status="LIVE",
                    ),
                )
            ),
            get_strategy_status=MagicMock(
                return_value=SimpleNamespace(
                    market_regime="RANGE_BOUND",
                    strategies=(),
                )
            ),
        )
        facade = DashboardIntegrationFacade(
            session=session,
            clock=lambda: FIXED_NOW,
        ).as_presentation_facade()
        view = facade.get_market_snapshot()
        assert view.ltp == "24512.40"
        assert view.change == "+85.20"
        assert view.volume == "1.2M"
        assert view.market_regime == "RANGE_BOUND"
        assert view.connection_status == "LIVE"
        assert len(view.indices) == 4
        assert view.indices[0].value == "24,512.40"
        assert view.option_chain_rows == (("24500", "CE", "100"),)


class TestMarketPageRender:
    """T04: Page render offline without raise."""

    def test_page_renders_offline_without_raise(self) -> None:
        facade = DashboardFacade(session=None, clock=lambda: FIXED_NOW)
        ctx = _render_ctx(facade.as_presentation_facade())
        with patch("dashboard.pages.market.st") as st_mock:
            st_mock.selectbox.return_value = "NIFTY"
            with patch("dashboard.pages.market.render_page_header"):
                with patch("dashboard.pages.market.render_index_strip") as strip:
                    with patch("dashboard.pages.market.render_kpi_row") as kpi:
                        with patch("dashboard.pages.market.render_table"):
                            with patch(
                                "dashboard.pages.market.render_tradingview_placeholder"
                            ) as chart:
                                market_page.render(ctx)
                                assert strip.called
                                assert kpi.call_count >= 2
                                assert chart.called


class TestNoForbiddenImports:
    """T05: Market page must not import broker/strategy packages."""

    FORBIDDEN = ("broker", "strategy", "kiteconnect", "paper_trading")

    def test_market_page_imports(self) -> None:
        path = DASHBOARD_ROOT / "pages" / "market.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in self.FORBIDDEN
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in self.FORBIDDEN
