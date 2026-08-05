"""Page-level tests for ``dashboard/pages/paper_trading.py``."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import MagicMock, patch

from dashboard import default_dashboard_ui_config
from dashboard.facade import NullIntegrationFacade
from dashboard.pages import paper_trading as paper_trading_page
from dashboard.view_models import (
    DashboardRenderContext,
    DashboardSessionView,
    OrderRowView,
    PLACEHOLDER,
    PaperPositionView,
    PaperTradingPageView,
)


FIXED_NOW = datetime(2026, 8, 6, 3, 30, 0, tzinfo=timezone.utc)
PAGE_PATH = (
    Path(__file__).resolve().parents[1] / "dashboard" / "pages" / "paper_trading.py"
)


def _render_ctx(facade: object) -> DashboardRenderContext:
    """Build a minimal render context for Paper Trading page tests."""
    config = replace(
        default_dashboard_ui_config(),
        enable_paper_trading_autorefresh=False,
    )
    return DashboardRenderContext(
        config=config,
        facade=facade,  # type: ignore[arg-type]
        session=DashboardSessionView(
            active_page="paper_trading",
            last_error=None,
            last_refresh_at=None,
            facade_action_pending=False,
            ui_prefs=MappingProxyType({}),
        ),
        clock=lambda: FIXED_NOW,
        version="1.0.0",
    )


def _live_paper_view() -> PaperTradingPageView:
    """Return a live-shaped PaperTradingPageView for page display tests."""
    return PaperTradingPageView(
        virtual_cash="1,000,000.00",
        available_cash="950,000.00",
        capital_used="50,000.00",
        total_equity="975,000.00",
        todays_pnl="2,500.00",
        realized_pnl="1,000.00",
        unrealized_pnl="1,500.00",
        source="live",
        positions=(
            PaperPositionView(
                symbol="NIFTY24P24000",
                strategy="Iron Condor",
                quantity="1",
                entry="120.00",
                current="95.00",
                mtm="25.00",
                status="OPEN",
            ),
        ),
        orders=(
            OrderRowView(
                order_id="ord-1",
                plan_id="plan-1",
                status="FILLED",
                symbol="NIFTY24P24000",
                side="SELL",
                quantity="1",
                timestamp="2026-08-06 03:00:00 UTC",
            ),
        ),
    )


class TestOfflinePlaceholders:
    """T01–T03/T05: Offline mode must show placeholders only."""

    def test_offline_account_summary_placeholders(self) -> None:
        view = NullIntegrationFacade().get_paper_trading()
        cards = paper_trading_page._account_summary_cards(view)
        assert [card.label for card in cards] == [
            "Cash",
            "Used Margin",
            "Available Margin",
            "Equity",
            "Today's PnL",
        ]
        assert all(card.value == PLACEHOLDER for card in cards)

    def test_offline_tables_empty_with_headers(self) -> None:
        view = NullIntegrationFacade().get_paper_trading()
        facade = NullIntegrationFacade()
        positions = paper_trading_page._positions_frame(view)
        orders = paper_trading_page._orders_frame(view)
        timeline = paper_trading_page._execution_timeline_frame(view, facade)
        assert list(positions.columns) == [
            "Symbol",
            "Strategy",
            "Qty",
            "Entry",
            "Current",
            "MTM",
            "Status",
        ]
        assert positions.empty
        assert list(orders.columns) == [
            "Order ID",
            "Symbol",
            "Side",
            "Qty",
            "Status",
            "Timestamp",
        ]
        assert orders.empty
        assert list(timeline.columns) == [
            "Time",
            "Event",
            "Symbol",
            "Status",
            "Detail",
        ]
        assert timeline.empty

    def test_offline_performance_placeholders(self) -> None:
        view = NullIntegrationFacade().get_paper_trading()
        facade = NullIntegrationFacade()
        cards = paper_trading_page._performance_summary_cards(view, facade)
        assert [card.label for card in cards] == [
            "Win Rate",
            "Average Winner",
            "Average Loser",
            "Profit Factor",
            "Expectancy",
        ]
        assert all(card.value == PLACEHOLDER for card in cards)

    def test_page_renders_offline_without_raise(self) -> None:
        facade = NullIntegrationFacade()
        ctx = _render_ctx(facade)
        with patch("dashboard.pages.paper_trading.st") as st_mock:
            with patch("dashboard.pages.paper_trading.render_page_header") as header:
                with patch("dashboard.pages.paper_trading.render_kpi_row") as kpi:
                    with patch("dashboard.pages.paper_trading.render_table") as table:
                        paper_trading_page.render(ctx)
                        header.assert_called_once()
                        assert kpi.call_count >= 2
                        assert table.call_count == 3
            st_mock.subheader.assert_any_call("Paper account summary")
            st_mock.subheader.assert_any_call("Open Positions")
            st_mock.subheader.assert_any_call("Paper Orders")
            st_mock.subheader.assert_any_call("Execution timeline")
            st_mock.subheader.assert_any_call("Performance summary")


class TestLivePageDisplay:
    """T04/T06: Live facade snapshot drives all five required panels."""

    def test_resolve_uses_get_paper_trading_only(self) -> None:
        live = _live_paper_view()
        facade = MagicMock()
        facade.get_paper_trading.return_value = live
        facade.get_paper_trading_ledger.side_effect = AssertionError(
            "page must not require ledger companion for resolve"
        )
        view = paper_trading_page._resolve_paper_view(_render_ctx(facade))
        assert view is live
        facade.get_paper_trading.assert_called_once()

    def test_live_account_positions_orders_timeline(self) -> None:
        view = _live_paper_view()
        facade = MagicMock()
        cards = paper_trading_page._account_summary_cards(view)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Cash"] == "950,000.00"
        assert by_label["Used Margin"] == "50,000.00"
        assert by_label["Available Margin"] == "950,000.00"
        assert by_label["Equity"] == "975,000.00"
        assert by_label["Today's PnL"] == "2,500.00"

        positions = paper_trading_page._positions_frame(view)
        assert list(positions["Symbol"]) == ["NIFTY24P24000"]
        orders = paper_trading_page._orders_frame(view)
        assert list(orders["Order ID"]) == ["ord-1"]
        timeline = paper_trading_page._execution_timeline_frame(view, facade)
        assert list(timeline["Symbol"]) == ["NIFTY24P24000"]
        assert "ORDER" in timeline.loc[0, "Event"]

    def test_performance_soft_reads_analytics(self) -> None:
        view = _live_paper_view()
        facade = MagicMock()
        facade.get_analytics.return_value = SimpleNamespace(
            win_rate="55%",
            average_winner="800.00",
            average_loser="400.00",
            profit_factor="1.75",
            expectancy="120.00",
            available=True,
        )
        cards = paper_trading_page._performance_summary_cards(view, facade)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Win Rate"] == "55%"
        assert by_label["Average Winner"] == "800.00"
        assert by_label["Average Loser"] == "400.00"
        assert by_label["Profit Factor"] == "1.75"
        assert by_label["Expectancy"] == "120.00"

    def test_page_renders_live_panels(self) -> None:
        live = _live_paper_view()
        facade = MagicMock()
        facade.get_paper_trading.return_value = live
        facade.get_analytics.return_value = SimpleNamespace(
            win_rate="60%",
            average_winner="900.00",
            average_loser="450.00",
            profit_factor="2.00",
            expectancy="150.00",
        )
        ctx = _render_ctx(facade)
        with patch("dashboard.pages.paper_trading.st"):
            with patch("dashboard.pages.paper_trading.render_page_header"):
                with patch("dashboard.pages.paper_trading.render_kpi_row") as kpi:
                    with patch("dashboard.pages.paper_trading.render_table") as table:
                        paper_trading_page.render(ctx)
                        assert kpi.call_count >= 2
                        assert table.call_count == 3
                        positions, orders, timeline = [
                            call.args[0] for call in table.call_args_list
                        ]
                        assert not positions.empty
                        assert not orders.empty
                        assert not timeline.empty

    def test_resolve_exception_falls_back_to_placeholders(self) -> None:
        broken = MagicMock()
        broken.get_paper_trading.side_effect = RuntimeError("boom")
        with patch("dashboard.pages.paper_trading.render_error"):
            view = paper_trading_page._resolve_paper_view(_render_ctx(broken))
        cards = paper_trading_page._account_summary_cards(view)
        assert all(card.value == PLACEHOLDER for card in cards)
        assert view.source == "offline"


class TestNoForbiddenImports:
    """Page must remain presentation-only."""

    FORBIDDEN_ROOTS = (
        "broker",
        "kiteconnect",
        "execution",
        "paper_trading",
        "strategy",
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
