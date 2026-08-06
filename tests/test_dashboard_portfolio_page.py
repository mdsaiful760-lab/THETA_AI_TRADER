"""Tests for ``dashboard/pages/portfolio.py`` and its facade/view-model plumbing."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from dashboard import default_dashboard_ui_config
from dashboard.dashboard_facade import (
    DashboardFacade,
    DashboardIntegrationFacade,
    empty_portfolio,
    portfolio_to_page_view,
)
from dashboard.facade import NullIntegrationFacade
from dashboard.pages import portfolio as portfolio_page
from dashboard.view_models import (
    DashboardRenderContext,
    DashboardSessionView,
    PLACEHOLDER,
    PortfolioPageView,
    PortfolioPositionView,
    portfolio_exposure_cards,
    portfolio_position_breakdown_cards,
    portfolio_risk_snapshot_cards,
    portfolio_status_cards,
    portfolio_summary_kpi_cards,
)

FIXED_NOW = datetime(2026, 8, 7, 3, 30, 0, tzinfo=timezone.utc)
PAGE_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "pages" / "portfolio.py"


def _render_ctx(facade: object) -> DashboardRenderContext:
    """Build a minimal render context for Portfolio page tests."""
    return DashboardRenderContext(
        config=default_dashboard_ui_config(),
        facade=facade,  # type: ignore[arg-type]
        session=DashboardSessionView(
            active_page="portfolio",
            last_error=None,
            last_refresh_at=None,
            facade_action_pending=False,
            ui_prefs=MappingProxyType({}),
        ),
        clock=lambda: FIXED_NOW,
        version="1.0.0",
    )


def _live_portfolio_view() -> PortfolioPageView:
    """Return a live-shaped PortfolioPageView for page display tests."""
    return PortfolioPageView(
        equity="1000000.00",
        exposure="450000.00",
        utilization="45%",
        positions=(
            PortfolioPositionView(
                symbol="NIFTY24P24000",
                quantity="75",
                exposure="150000.00",
                pnl="2500.00",
                product="OPTIDX",
                avg_price="120.00",
                current_price="153.33",
                market_value="150000.00",
                unrealized_pnl="2500.00",
                realized_pnl="0.00",
                day_change_pct="+1.25%",
                weight_pct="15.00%",
            ),
            PortfolioPositionView(
                symbol="BANKNIFTY24C51000",
                quantity="-25",
                exposure="-95000.00",
                pnl="-800.00",
                product="OPTIDX",
                avg_price="380.00",
                current_price="412.00",
                market_value="95000.00",
                unrealized_pnl="-800.00",
                realized_pnl="150.00",
                day_change_pct="-0.85%",
                weight_pct="9.50%",
            ),
        ),
        equity_series=(("2026-08-05", 980000.0), ("2026-08-06", 1000000.0)),
        allocation=(("NIFTY", 0.6), ("BANKNIFTY", 0.4)),
        cash_available="550000.00",
        margin_used="450000.00",
        todays_pnl="1700.00",
        total_pnl="1850.00",
        allocation_by_sector=(("Index", 1.0),),
        allocation_by_instrument=(("NIFTY", 0.6), ("BANKNIFTY", 0.4)),
        allocation_by_product=(("OPTIDX", 1.0),),
        long_exposure="150000.00",
        short_exposure="95000.00",
        net_exposure="55000.00",
        gross_exposure="245000.00",
        daily_pnl_series=(("2026-08-05", -300.0), ("2026-08-06", 1700.0)),
        cumulative_pnl_series=(("2026-08-05", 150.0), ("2026-08-06", 1850.0)),
        largest_position="NIFTY24P24000",
        largest_loss="BANKNIFTY24C51000",
        largest_gain="NIFTY24P24000",
        portfolio_beta="0.85",
        diversification_score="62",
        total_positions_count="2",
        open_positions_count="2",
        closed_positions_count="5",
        long_positions_count="1",
        short_positions_count="1",
        broker_connection_status="CONNECTED",
        portfolio_sync_status="SYNCED",
        last_update="2026-08-07 03:20:00 UTC",
        source="live",
    )


class TestOfflineRendering:
    """Offline mode must show placeholders and empty tables — never fabricate data."""

    def test_offline_summary_kpi_placeholders(self) -> None:
        view = NullIntegrationFacade().get_portfolio()
        cards = portfolio_summary_kpi_cards(view)
        assert [card.label for card in cards] == [
            "Total Portfolio Value",
            "Cash Available",
            "Margin Used",
            "Today's P&L",
            "Total P&L",
        ]
        assert all(card.value == PLACEHOLDER for card in cards)

    def test_offline_exposure_cards_placeholders(self) -> None:
        view = NullIntegrationFacade().get_portfolio()
        cards = portfolio_exposure_cards(view)
        assert all(card.value == PLACEHOLDER for card in cards)

    def test_offline_risk_snapshot_placeholders(self) -> None:
        view = NullIntegrationFacade().get_portfolio()
        cards = portfolio_risk_snapshot_cards(view)
        assert all(card.value == PLACEHOLDER for card in cards)

    def test_offline_position_breakdown_zero(self) -> None:
        view = NullIntegrationFacade().get_portfolio()
        cards = portfolio_position_breakdown_cards(view)
        assert all(card.value == "0" for card in cards)

    def test_offline_status_cards(self) -> None:
        view = NullIntegrationFacade().get_portfolio()
        cards = portfolio_status_cards(view)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Broker Connection"] == "DISCONNECTED"
        assert by_label["Portfolio Sync"] == "UNKNOWN"
        assert by_label["Last Update"] == PLACEHOLDER
        assert by_label["Data Source"] == "OFFLINE"

    def test_offline_holdings_table_empty_with_headers(self) -> None:
        view = NullIntegrationFacade().get_portfolio()
        df = portfolio_page._holdings_frame(view)
        assert list(df.columns) == [
            "Symbol",
            "Product",
            "Quantity",
            "Average Price",
            "Current Price",
            "Market Value",
            "Unrealized P&L",
            "Realized P&L",
            "Day Change %",
            "Weight %",
        ]
        assert df.empty

    def test_offline_allocation_frames_empty(self) -> None:
        view = NullIntegrationFacade().get_portfolio()
        assert portfolio_page._allocation_frame(view.allocation_by_sector).empty
        assert portfolio_page._allocation_frame(view.allocation_by_instrument).empty
        assert portfolio_page._allocation_frame(view.allocation_by_product).empty

    def test_offline_performance_series_empty(self) -> None:
        view = NullIntegrationFacade().get_portfolio()
        assert portfolio_page._series_frame(view.equity_series, "equity").empty
        assert portfolio_page._series_frame(view.daily_pnl_series, "equity").empty
        assert portfolio_page._series_frame(view.cumulative_pnl_series, "equity").empty

    def test_page_renders_offline_without_raise(self) -> None:
        facade = NullIntegrationFacade()
        ctx = _render_ctx(facade)
        with patch("dashboard.pages.portfolio.st") as st_mock:
            st_mock.columns.return_value = [MagicMock(), MagicMock(), MagicMock()]
            with patch("dashboard.pages.portfolio.render_page_header") as header:
                with patch("dashboard.pages.portfolio.render_kpi_row") as kpi:
                    with patch("dashboard.pages.portfolio.render_table") as table:
                        portfolio_page.render(ctx)
                        header.assert_called_once()
                        assert kpi.call_count >= 5
                        assert table.call_count == 1
            for title in (
                "Portfolio Summary",
                "Holdings",
                "Allocation",
                "Exposure",
                "Portfolio Performance",
                "Portfolio Risk Snapshot",
                "Position Breakdown",
                "Portfolio Status",
            ):
                st_mock.subheader.assert_any_call(title)
            st_mock.plotly_chart.assert_called()


class TestLiveMapping:
    """Live facade snapshot drives every section of the page."""

    def test_resolve_uses_get_portfolio_only(self) -> None:
        live = _live_portfolio_view()
        facade = MagicMock()
        facade.get_portfolio.return_value = live
        view = portfolio_page._resolve_portfolio_view(_render_ctx(facade))
        assert view is live
        facade.get_portfolio.assert_called_once()

    def test_summary_kpi_cards_live(self) -> None:
        view = _live_portfolio_view()
        cards = portfolio_summary_kpi_cards(view)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Total Portfolio Value"] == "1000000.00"
        assert by_label["Cash Available"] == "550000.00"
        assert by_label["Margin Used"] == "450000.00"
        assert by_label["Today's P&L"] == "1700.00"
        assert by_label["Total P&L"] == "1850.00"

    def test_resolve_exception_falls_back_to_placeholders(self) -> None:
        broken = MagicMock()
        broken.get_portfolio.side_effect = RuntimeError("boom")
        with patch("dashboard.pages.portfolio.render_error"):
            view = portfolio_page._resolve_portfolio_view(_render_ctx(broken))
        cards = portfolio_summary_kpi_cards(view)
        assert all(card.value == PLACEHOLDER for card in cards)
        assert view.source == "offline"

    def test_page_renders_live_panels(self) -> None:
        live = _live_portfolio_view()
        facade = MagicMock()
        facade.get_portfolio.return_value = live
        ctx = _render_ctx(facade)
        with patch("dashboard.pages.portfolio.st") as st_mock:
            st_mock.columns.return_value = [MagicMock(), MagicMock(), MagicMock()]
            with patch("dashboard.pages.portfolio.render_page_header"):
                with patch("dashboard.pages.portfolio.render_kpi_row") as kpi:
                    with patch("dashboard.pages.portfolio.render_table") as table:
                        portfolio_page.render(ctx)
                        assert kpi.call_count >= 5
                        holdings_df = table.call_args_list[0].args[0]
                        assert not holdings_df.empty


class TestHoldingsTable:
    """Holdings Table column mapping from the live PortfolioPageView."""

    def test_holdings_frame_live_columns_and_rows(self) -> None:
        view = _live_portfolio_view()
        df = portfolio_page._holdings_frame(view)
        assert len(df) == 2
        row = df.iloc[0]
        assert row["Symbol"] == "NIFTY24P24000"
        assert row["Product"] == "OPTIDX"
        assert row["Quantity"] == "75"
        assert row["Average Price"] == "120.00"
        assert row["Current Price"] == "153.33"
        assert row["Market Value"] == "150000.00"
        assert row["Unrealized P&L"] == "2500.00"
        assert row["Realized P&L"] == "0.00"
        assert row["Day Change %"] == "+1.25%"
        assert row["Weight %"] == "15.00%"


class TestSearch:
    """Free-text search over the Holdings Table."""

    def test_search_matches_symbol(self) -> None:
        view = _live_portfolio_view()
        df = portfolio_page._holdings_frame(view)
        filtered = portfolio_page._filter_holdings(df, search="banknifty")
        assert list(filtered["Symbol"]) == ["BANKNIFTY24C51000"]

    def test_search_no_match_returns_empty(self) -> None:
        view = _live_portfolio_view()
        df = portfolio_page._holdings_frame(view)
        filtered = portfolio_page._filter_holdings(df, search="doesnotexist")
        assert filtered.empty

    def test_search_blank_returns_all(self) -> None:
        view = _live_portfolio_view()
        df = portfolio_page._holdings_frame(view)
        filtered = portfolio_page._filter_holdings(df, search="")
        assert len(filtered) == len(df)


class TestCsvExport:
    """Holdings Table CSV export."""

    def test_csv_export_contains_rows(self) -> None:
        view = _live_portfolio_view()
        df = portfolio_page._holdings_frame(view)
        csv_text = portfolio_page._holdings_csv(df)
        assert "Symbol" in csv_text
        assert "NIFTY24P24000" in csv_text
        assert csv_text.count("\n") >= 2

    def test_csv_export_offline_is_header_only(self) -> None:
        view = NullIntegrationFacade().get_portfolio()
        df = portfolio_page._holdings_frame(view)
        csv_text = portfolio_page._holdings_csv(df)
        assert csv_text.strip() == (
            "Symbol,Product,Quantity,Average Price,Current Price,"
            "Market Value,Unrealized P&L,Realized P&L,Day Change %,Weight %"
        )


class TestAllocationCharts:
    """Allocation by Sector / Instrument / Product chart data."""

    def test_allocation_frame_live(self) -> None:
        view = _live_portfolio_view()
        sector_df = portfolio_page._allocation_frame(view.allocation_by_sector)
        assert list(sector_df.columns) == ["label", "weight"]
        assert sector_df["weight"].sum() == 1.0

    def test_allocation_frame_offline_is_empty(self) -> None:
        view = NullIntegrationFacade().get_portfolio()
        assert portfolio_page._allocation_frame(view.allocation_by_instrument).empty


class TestExposureCards:
    """Exposure KPI cards (Total/Long/Short/Net/Gross)."""

    def test_exposure_cards_live(self) -> None:
        view = _live_portfolio_view()
        cards = portfolio_exposure_cards(view)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Total Exposure"] == "450000.00"
        assert by_label["Long Exposure"] == "150000.00"
        assert by_label["Short Exposure"] == "95000.00"
        assert by_label["Net Exposure"] == "55000.00"
        assert by_label["Gross Exposure"] == "245000.00"


class TestPerformanceCharts:
    """Equity Curve, Daily P&L, and Cumulative P&L series data."""

    def test_equity_series_frame_live(self) -> None:
        view = _live_portfolio_view()
        df = portfolio_page._series_frame(view.equity_series, "equity")
        assert list(df.columns) == ["timestamp", "equity"]
        assert len(df) == 2

    def test_daily_pnl_series_frame_live(self) -> None:
        view = _live_portfolio_view()
        df = portfolio_page._series_frame(view.daily_pnl_series, "equity")
        assert len(df) == 2

    def test_cumulative_pnl_series_frame_live(self) -> None:
        view = _live_portfolio_view()
        df = portfolio_page._series_frame(view.cumulative_pnl_series, "equity")
        assert len(df) == 2


class TestRiskSnapshot:
    """Portfolio Risk Snapshot KPI cards."""

    def test_risk_snapshot_cards_live(self) -> None:
        view = _live_portfolio_view()
        cards = portfolio_risk_snapshot_cards(view)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Largest Position"] == "NIFTY24P24000"
        assert by_label["Largest Loss"] == "BANKNIFTY24C51000"
        assert by_label["Largest Gain"] == "NIFTY24P24000"
        assert by_label["Portfolio Beta"] == "0.85"
        assert by_label["Diversification Score"] == "62"

    def test_risk_snapshot_cards_offline_are_placeholders(self) -> None:
        view = NullIntegrationFacade().get_portfolio()
        cards = portfolio_risk_snapshot_cards(view)
        assert all(card.value == PLACEHOLDER for card in cards)


class TestPositionBreakdown:
    """Position Breakdown count cards."""

    def test_breakdown_cards_live(self) -> None:
        view = _live_portfolio_view()
        cards = portfolio_position_breakdown_cards(view)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Total Positions"] == "2"
        assert by_label["Open Positions"] == "2"
        assert by_label["Closed Positions"] == "5"
        assert by_label["Long Positions"] == "1"
        assert by_label["Short Positions"] == "1"


class TestFacadeMapping:
    """dashboard_facade.py soft-reads powering the Portfolio page."""

    def test_empty_portfolio_defaults(self) -> None:
        snap = empty_portfolio(as_of=FIXED_NOW)
        assert snap.equity == "—"
        assert snap.cash_available == "—"
        assert snap.margin_used == "—"
        assert snap.total_positions_count == "0"
        assert snap.broker_connection_status == "DISCONNECTED"
        assert snap.allocation_by_sector == ()
        assert snap.daily_pnl_series == ()
        assert snap.source == "offline"

    def test_live_portfolio_soft_reads_new_fields(self) -> None:
        session = SimpleNamespace(
            get_portfolio=MagicMock(
                return_value=SimpleNamespace(
                    equity="1000000",
                    exposure="450000",
                    utilization="45%",
                    positions=[
                        SimpleNamespace(
                            symbol="NIFTY",
                            quantity="75",
                            exposure="150000",
                            pnl="2500",
                            product="OPTIDX",
                            avg_price="120.00",
                            current_price="153.33",
                            market_value="150000",
                            unrealized_pnl="2500",
                            realized_pnl="0",
                            day_change_pct="+1.25%",
                            weight_pct="15.00%",
                        ),
                        SimpleNamespace(
                            symbol="BANKNIFTY",
                            quantity="-25",
                            exposure="-95000",
                            pnl="-800",
                        ),
                    ],
                    equity_series=[("2026-08-06", 1000000.0)],
                    allocation_by_sector=[("Index", 1.0)],
                    allocation_by_instrument=[("NIFTY", 0.6), ("BANKNIFTY", 0.4)],
                    allocation_by_product=[("OPTIDX", 1.0)],
                    cash_available="550000",
                    margin_used="450000",
                    todays_pnl="1700",
                    total_pnl="1850",
                    long_exposure="150000",
                    short_exposure="95000",
                    net_exposure="55000",
                    gross_exposure="245000",
                    daily_pnl_series=[("2026-08-06", 1700.0)],
                    cumulative_pnl_series=[("2026-08-06", 1850.0)],
                    largest_position="NIFTY",
                    largest_loss="BANKNIFTY",
                    largest_gain="NIFTY",
                    portfolio_beta="0.85",
                    diversification_score="62",
                    closed_positions_count="5",
                    broker_connection_status="CONNECTED",
                    portfolio_sync_status="SYNCED",
                )
            )
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        snap = facade.get_portfolio()
        assert snap.equity == "1000000"
        assert snap.cash_available == "550000"
        assert snap.margin_used == "450000"
        assert snap.total_pnl == "1850"
        assert snap.long_exposure == "150000"
        assert snap.short_exposure == "95000"
        assert snap.positions[0].product == "OPTIDX"
        assert snap.positions[0].current_price == "153.33"
        assert snap.positions[0].weight_pct == "15.00%"
        assert snap.total_positions_count == "2"
        assert snap.open_positions_count == "2"
        assert snap.closed_positions_count == "5"
        # Quantity signs are bucketed since no explicit long/short counts given.
        assert snap.long_positions_count == "1"
        assert snap.short_positions_count == "1"
        assert snap.broker_connection_status == "CONNECTED"
        assert snap.portfolio_sync_status == "SYNCED"
        assert snap.allocation_by_sector == (("Index", 1.0),)

    def test_broker_status_falls_back_to_system_status(self) -> None:
        session = SimpleNamespace(
            get_portfolio=MagicMock(
                return_value=SimpleNamespace(equity="1", positions=[])
            ),
            get_health=MagicMock(
                return_value=SimpleNamespace(
                    session_state=SimpleNamespace(value="running"),
                    overall_status=SimpleNamespace(value="healthy"),
                    broker_connection=SimpleNamespace(state="connected", connected=True),
                    message="ok",
                )
            ),
            get_runtime_state=MagicMock(
                return_value=SimpleNamespace(
                    execution_mode="PAPER",
                    market_status="OPEN",
                )
            ),
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        snap = facade.get_portfolio()
        assert snap.broker_connection_status != "UNKNOWN"

    def test_portfolio_to_page_view_maps_all_sections(self) -> None:
        snap = empty_portfolio(as_of=FIXED_NOW)
        view = portfolio_to_page_view(snap)
        assert isinstance(view, PortfolioPageView)
        assert view.equity == snap.equity
        assert view.cash_available == snap.cash_available
        assert view.broker_connection_status == snap.broker_connection_status
        assert view.source == snap.source

    def test_presentation_adapter_maps_portfolio_page_view(self) -> None:
        facade = DashboardFacade(session=None, clock=lambda: FIXED_NOW)
        view = facade.as_presentation_facade().get_portfolio()
        assert view.equity == PLACEHOLDER
        assert view.positions == ()
        assert view.total_positions_count == "0"
        assert view.source == "offline"


class TestNoForbiddenImports:
    """Page must remain presentation-only and reuse DashboardFacade exclusively."""

    FORBIDDEN_ROOTS = (
        "broker",
        "kiteconnect",
        "execution",
        "market_data",
        "strategy",
        "risk",
        "decision",
        "paper_trading",
        "apme",
    )

    def test_page_has_no_forbidden_imports(self) -> None:
        tree = ast.parse(PAGE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in self.FORBIDDEN_ROOTS
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in self.FORBIDDEN_ROOTS

    def test_page_only_reads_via_facade(self) -> None:
        source = PAGE_PATH.read_text(encoding="utf-8")
        assert "ctx.facade.get_portfolio" not in source or "getattr(ctx.facade" in source
        tree = ast.parse(source)
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)
        assert all(not name.startswith("dashboard.pages.") for name in imported_names)
