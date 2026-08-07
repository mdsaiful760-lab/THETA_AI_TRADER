"""Page-level tests for ``dashboard/pages/paper_trading.py``."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from dashboard import default_dashboard_ui_config
from dashboard.dashboard_facade import (
    DashboardFacade,
    DashboardIntegrationFacade,
    empty_paper_trading_ledger,
    paper_ledger_to_page_view,
)
from dashboard.facade import NullIntegrationFacade
from dashboard.pages import paper_trading as paper_trading_page
from dashboard.view_models import (
    AnalyticsPageView,
    DashboardRenderContext,
    DashboardSessionView,
    OrderRowView,
    PLACEHOLDER,
    PaperPositionView,
    PaperTradingPageView,
    paper_trading_position_summary_cards,
    paper_trading_runner_status_cards,
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
            "Exposure",
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
                        assert kpi.call_count >= 5
                        assert table.call_count == 3
            for title in (
                "Open Positions",
                "Execution timeline",
                "Performance KPIs",
                "Equity Curve",
                "Drawdown",
                "Position Summary",
                "Trade History",
                "Statistics",
                "Runner Status",
            ):
                st_mock.subheader.assert_any_call(title)
            st_mock.plotly_chart.assert_called()
            st_mock.download_button.assert_called()


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
                        assert kpi.call_count >= 5
                        assert table.call_count == 3
                        positions, timeline, trade_history = [
                            call.args[0] for call in table.call_args_list
                        ]
                        assert not positions.empty
                        assert not timeline.empty
                        assert not trade_history.empty

    def test_resolve_exception_falls_back_to_placeholders(self) -> None:
        broken = MagicMock()
        broken.get_paper_trading.side_effect = RuntimeError("boom")
        with patch("dashboard.pages.paper_trading.render_error"):
            view = paper_trading_page._resolve_paper_view(_render_ctx(broken))
        cards = paper_trading_page._account_summary_cards(view)
        assert all(card.value == PLACEHOLDER for card in cards)
        assert view.source == "offline"


def _live_view_with_stats() -> PaperTradingPageView:
    """Return a live-shaped view including the new professional dashboard fields."""
    return replace(
        _live_paper_view(),
        total_pnl="2,500.00",
        exposure="60,000.00",
        open_positions_count="1",
        closed_positions_count="3",
        equity_series=(("2026-08-01", 970000.0), ("2026-08-06", 975000.0)),
        drawdown_series=(("2026-08-01", 0.0), ("2026-08-06", -1.5)),
        runner_state="RUNNING",
        runner_connection_status="CONNECTED",
        runner_latency="45 ms",
        runner_last_update="2026-08-06 03:29:00 UTC",
    )


class TestPerformanceKpiCards:
    """New Performance KPI row (#1): Account Balance / PnL / Win Rate / Profit Factor."""

    def test_offline_placeholders(self) -> None:
        view = NullIntegrationFacade().get_paper_trading()
        cards = paper_trading_page._performance_kpi_cards(view, None)
        assert [card.label for card in cards] == [
            "Account Balance",
            "Today's P&L",
            "Total P&L",
            "Win Rate",
            "Profit Factor",
        ]
        assert all(card.value == PLACEHOLDER for card in cards)

    def test_live_values(self) -> None:
        view = _live_view_with_stats()
        analytics = SimpleNamespace(win_rate="55%", profit_factor="1.75")
        cards = paper_trading_page._performance_kpi_cards(view, analytics)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Account Balance"] == "975,000.00"
        assert by_label["Today's P&L"] == "2,500.00"
        assert by_label["Total P&L"] == "2,500.00"
        assert by_label["Win Rate"] == "55%"
        assert by_label["Profit Factor"] == "1.75"


class TestStatisticsCards:
    """New Statistics section (#6): 8 soft-read metrics, never computed here."""

    def test_offline_placeholders(self) -> None:
        view = NullIntegrationFacade().get_paper_trading()
        cards = paper_trading_page._statistics_cards(view, None)
        assert [card.label for card in cards] == [
            "Average Winner",
            "Average Loser",
            "Largest Win",
            "Largest Loss",
            "Expectancy",
            "Risk Reward",
            "Sharpe",
            "Max Drawdown",
        ]
        assert all(card.value == PLACEHOLDER for card in cards)

    def test_live_values(self) -> None:
        view = _live_view_with_stats()
        analytics = SimpleNamespace(
            average_winner="800.00",
            average_loser="400.00",
            largest_win="3,000.00",
            largest_loss="1,200.00",
            expectancy="120.00",
            risk_reward="1.8",
            sharpe="1.2",
            max_drawdown="4.5%",
        )
        cards = paper_trading_page._statistics_cards(view, analytics)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Average Winner"] == "800.00"
        assert by_label["Largest Win"] == "3,000.00"
        assert by_label["Risk Reward"] == "1.8"
        assert by_label["Sharpe"] == "1.2"
        assert by_label["Max Drawdown"] == "4.5%"


class TestViewModelMapping:
    """view_models.py builder functions (#4 Position Summary, #7 Runner Status)."""

    def test_position_summary_offline_placeholders(self) -> None:
        view = NullIntegrationFacade().get_paper_trading()
        cards = paper_trading_position_summary_cards(view)
        assert [card.label for card in cards] == [
            "Open Positions",
            "Closed Positions",
            "Capital Used",
            "Exposure",
        ]
        by_label = {card.label: card.value for card in cards}
        assert by_label["Open Positions"] == "0"
        assert by_label["Closed Positions"] == "0"
        assert by_label["Capital Used"] == PLACEHOLDER
        assert by_label["Exposure"] == PLACEHOLDER

    def test_position_summary_live_values(self) -> None:
        view = _live_view_with_stats()
        cards = paper_trading_position_summary_cards(view)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Open Positions"] == "1"
        assert by_label["Closed Positions"] == "3"
        assert by_label["Exposure"] == "60,000.00"

    def test_runner_status_offline_placeholders(self) -> None:
        view = NullIntegrationFacade().get_paper_trading()
        cards = paper_trading_runner_status_cards(view)
        assert [card.label for card in cards] == [
            "Runner",
            "Connection",
            "Latency",
            "Last Update",
        ]
        by_label = {card.label: card.value for card in cards}
        assert by_label["Runner"] == "STOPPED"
        assert by_label["Connection"] == "DISCONNECTED"
        assert by_label["Latency"] == PLACEHOLDER

    def test_runner_status_live_values(self) -> None:
        view = _live_view_with_stats()
        cards = paper_trading_runner_status_cards(view)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Runner"] == "RUNNING"
        assert by_label["Connection"] == "CONNECTED"
        assert by_label["Latency"] == "45 ms"

    def test_analytics_page_view_new_fields_default_to_placeholder(self) -> None:
        view = AnalyticsPageView()
        assert view.profit_factor == PLACEHOLDER
        assert view.average_winner == PLACEHOLDER
        assert view.largest_win == PLACEHOLDER
        assert view.risk_reward == PLACEHOLDER
        assert view.sharpe == PLACEHOLDER
        assert view.max_drawdown == PLACEHOLDER


class TestEquityAndDrawdownCharts:
    """Equity curve (#2) and drawdown (#3) series-to-dataframe mapping."""

    def test_offline_series_are_empty_with_expected_columns(self) -> None:
        view = NullIntegrationFacade().get_paper_trading()
        equity_df = paper_trading_page._series_frame(view.equity_series, "equity")
        drawdown_df = paper_trading_page._series_frame(view.drawdown_series, "drawdown")
        assert equity_df.empty
        assert list(equity_df.columns) == ["timestamp", "equity"]
        assert drawdown_df.empty
        assert list(drawdown_df.columns) == ["timestamp", "drawdown"]

    def test_live_series_populate_dataframe(self) -> None:
        view = _live_view_with_stats()
        equity_df = paper_trading_page._series_frame(view.equity_series, "equity")
        drawdown_df = paper_trading_page._series_frame(view.drawdown_series, "drawdown")
        assert list(equity_df["equity"]) == [970000.0, 975000.0]
        assert list(drawdown_df["drawdown"]) == [0.0, -1.5]


class TestTradeHistory:
    """Trade History (#5): search, filter, native sorting, CSV export."""

    def _sample_trades(self) -> pd.DataFrame:
        view = replace(
            _live_paper_view(),
            orders=(
                OrderRowView(
                    order_id="1",
                    status="FILLED",
                    symbol="NIFTY",
                    side="SELL",
                    quantity="1",
                    timestamp="t1",
                ),
                OrderRowView(
                    order_id="2",
                    status="PENDING",
                    symbol="BANKNIFTY",
                    side="BUY",
                    quantity="2",
                    timestamp="t2",
                ),
                OrderRowView(
                    order_id="3",
                    status="CANCELLED",
                    symbol="NIFTY",
                    side="SELL",
                    quantity="1",
                    timestamp="t3",
                ),
            ),
        )
        df = paper_trading_page._orders_frame(view)
        assert isinstance(df, pd.DataFrame)
        return df

    def test_filter_by_status(self) -> None:
        df = self._sample_trades()
        filtered = paper_trading_page._filter_trade_history(df, statuses=["FILLED"])
        assert list(filtered["Order ID"]) == ["1"]

    def test_filter_by_search_term(self) -> None:
        df = self._sample_trades()
        filtered = paper_trading_page._filter_trade_history(df, search="banknifty")
        assert list(filtered["Order ID"]) == ["2"]

    def test_filter_combined_search_and_status(self) -> None:
        df = self._sample_trades()
        filtered = paper_trading_page._filter_trade_history(
            df, search="nifty", statuses=["FILLED", "CANCELLED"]
        )
        assert set(filtered["Order ID"]) == {"1", "3"}

    def test_filter_no_match_returns_empty(self) -> None:
        df = self._sample_trades()
        filtered = paper_trading_page._filter_trade_history(df, search="doesnotexist")
        assert filtered.empty

    def test_no_filters_returns_all_rows(self) -> None:
        df = self._sample_trades()
        filtered = paper_trading_page._filter_trade_history(df)
        assert len(filtered) == len(df)

    def test_csv_export_contains_rows(self) -> None:
        df = self._sample_trades()
        csv_text = paper_trading_page._trade_history_csv(df)
        assert "Order ID" in csv_text
        assert "1" in csv_text and "FILLED" in csv_text
        assert csv_text.count("\n") >= 3

    def test_csv_export_offline_is_header_only(self) -> None:
        view = NullIntegrationFacade().get_paper_trading()
        df = paper_trading_page._orders_frame(view)
        csv_text = paper_trading_page._trade_history_csv(df)
        assert csv_text.strip() == "Order ID,Symbol,Side,Qty,Status,Timestamp"


class TestFacadeMapping:
    """dashboard_facade.py soft-reads powering the new page sections."""

    def test_empty_ledger_new_field_defaults(self) -> None:
        ledger = empty_paper_trading_ledger(as_of=FIXED_NOW)
        assert ledger.total_pnl == PLACEHOLDER
        assert ledger.exposure == PLACEHOLDER
        assert ledger.open_positions_count == "0"
        assert ledger.closed_positions_count == "0"
        assert ledger.drawdown_series == ()
        assert ledger.runner_state == "STOPPED"
        assert ledger.runner_connection_status == "DISCONNECTED"
        assert ledger.runner_latency == PLACEHOLDER

    def test_paper_ledger_to_page_view_carries_new_fields(self) -> None:
        ledger = empty_paper_trading_ledger(as_of=FIXED_NOW)
        view = paper_ledger_to_page_view(ledger)
        assert view.total_pnl == ledger.total_pnl
        assert view.drawdown_series == ledger.drawdown_series
        assert view.runner_state == ledger.runner_state
        assert view.exposure == ledger.exposure

    def test_live_ledger_soft_reads_new_fields(self) -> None:
        session = SimpleNamespace(
            get_paper_trading_ledger=MagicMock(
                return_value=SimpleNamespace(
                    available_cash=100000.0,
                    realized_pnl=200.0,
                    unrealized_pnl=250.0,
                    exposure=60000.0,
                    closed_positions=3,
                    drawdown_series=(("2026-08-05", -1.2),),
                    runner_state="RUNNING",
                    connected=True,
                    latency_ms=42,
                    positions=(),
                    orders=(),
                )
            )
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        ledger = facade.get_paper_trading_ledger()
        assert ledger.total_pnl == "450.00"
        assert ledger.exposure == "60,000.00"
        assert ledger.closed_positions_count == "3"
        assert ledger.drawdown_series == (("2026-08-05", -1.2),)
        assert ledger.runner_state == "RUNNING"
        assert ledger.runner_connection_status == "CONNECTED"
        assert ledger.runner_latency == "42 ms"

    def test_open_positions_count_derived_from_positions_length(self) -> None:
        session = SimpleNamespace(
            get_paper_trading_ledger=MagicMock(
                return_value=SimpleNamespace(
                    positions=(
                        SimpleNamespace(symbol="NIFTY", quantity=1, status="OPEN"),
                        SimpleNamespace(symbol="BANKNIFTY", quantity=-1, status="OPEN"),
                    ),
                )
            )
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        ledger = facade.get_paper_trading_ledger()
        assert ledger.open_positions_count == "2"

    def test_analytics_statistics_mapped_from_metrics_dict(self) -> None:
        session = SimpleNamespace(
            get_performance=MagicMock(
                return_value=SimpleNamespace(
                    metrics={
                        "win_rate": "55%",
                        "profit_factor": "1.75",
                        "average_winner": "800.00",
                        "largest_win": "3000.00",
                        "sharpe": "1.2",
                        "max_drawdown": "4.5%",
                    },
                    series=(),
                )
            )
        )
        facade = DashboardFacade(session=session, clock=lambda: FIXED_NOW)
        view = facade.as_presentation_facade().get_analytics()
        assert view.win_rate == "55%"
        assert view.profit_factor == "1.75"
        assert view.average_winner == "800.00"
        assert view.largest_win == "3000.00"
        assert view.sharpe == "1.2"
        assert view.max_drawdown == "4.5%"


class TestNoForbiddenImports:
    """Page must remain presentation-only."""

    FORBIDDEN_ROOTS = (
        "broker",
        "kiteconnect",
        "execution",
        "paper_trading",
        "strategy",
        "risk",
        "decision",
        "market_data",
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
