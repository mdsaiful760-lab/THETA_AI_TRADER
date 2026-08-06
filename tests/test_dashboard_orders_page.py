"""Tests for ``dashboard/pages/orders.py`` and its facade/view-model plumbing."""

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
    empty_order_book,
)
from dashboard.facade import NullIntegrationFacade
from dashboard.pages import orders as orders_page
from dashboard.view_models import (
    DashboardRenderContext,
    DashboardSessionView,
    OrderRowView,
    OrdersPageView,
    PLACEHOLDER,
    orders_broker_status_cards,
    orders_status_distribution,
    orders_summary_kpi_cards,
)

FIXED_NOW = datetime(2026, 8, 7, 3, 30, 0, tzinfo=timezone.utc)
PAGE_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "pages" / "orders.py"


def _render_ctx(facade: object) -> DashboardRenderContext:
    """Build a minimal render context for Orders page tests."""
    return DashboardRenderContext(
        config=default_dashboard_ui_config(),
        facade=facade,  # type: ignore[arg-type]
        session=DashboardSessionView(
            active_page="orders",
            last_error=None,
            last_refresh_at=None,
            facade_action_pending=False,
            ui_prefs=MappingProxyType({}),
        ),
        clock=lambda: FIXED_NOW,
        version="1.0.0",
    )


def _live_orders_view() -> OrdersPageView:
    """Return a live-shaped OrdersPageView for page display tests."""
    return OrdersPageView(
        orders=(
            OrderRowView(
                order_id="1",
                plan_id="p1",
                status="FILLED",
                symbol="NIFTY24P24000",
                side="SELL",
                quantity="1",
                timestamp="2026-08-07 03:00:00 UTC",
                strategy="Iron Condor",
                price="120.00",
            ),
            OrderRowView(
                order_id="2",
                plan_id="p2",
                status="PENDING",
                symbol="BANKNIFTY24C51000",
                side="BUY",
                quantity="2",
                timestamp="2026-08-07 03:10:00 UTC",
                strategy="Short Strangle",
                price="95.00",
            ),
            OrderRowView(
                order_id="3",
                plan_id="p3",
                status="CANCELLED",
                symbol="NIFTY24P24000",
                side="SELL",
                quantity="1",
                timestamp="2026-08-07 03:20:00 UTC",
                strategy="Iron Condor",
                price="118.00",
            ),
        ),
        total_orders="3",
        orders_pending="1",
        orders_filled="1",
        orders_cancelled="1",
        orders_rejected="0",
        broker_connection_status="CONNECTED",
        oms_status="RUNNING",
        exchange_status="OPEN",
        last_order_time="2026-08-07 03:20:00 UTC",
        broker_latency="38 ms",
        source="live",
    )


class TestOfflineRendering:
    """Offline mode must show placeholders and never fabricate data."""

    def test_offline_summary_kpi_placeholders(self) -> None:
        view = NullIntegrationFacade().get_orders()
        cards = orders_summary_kpi_cards(view)
        assert [card.label for card in cards] == [
            "Total Orders",
            "Pending",
            "Filled",
            "Cancelled",
            "Rejected",
        ]
        assert all(card.value == "0" for card in cards)

    def test_offline_broker_status_placeholders(self) -> None:
        view = NullIntegrationFacade().get_orders()
        cards = orders_broker_status_cards(view)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Broker Connection"] == "DISCONNECTED"
        assert by_label["OMS Status"] == "UNKNOWN"
        assert by_label["Exchange Status"] == "UNKNOWN"
        assert by_label["Last Order Time"] == PLACEHOLDER
        assert by_label["Latency"] == PLACEHOLDER

    def test_offline_tables_empty_with_headers(self) -> None:
        view = NullIntegrationFacade().get_orders()
        active = orders_page._active_orders_frame(view)
        history = orders_page._order_history_frame(view)
        expected_columns = [
            "Order ID",
            "Time",
            "Symbol",
            "Strategy",
            "Side",
            "Qty",
            "Price",
            "Status",
        ]
        assert list(active.columns) == expected_columns
        assert active.empty
        assert list(history.columns) == expected_columns
        assert history.empty

    def test_offline_status_distribution_is_empty(self) -> None:
        view = NullIntegrationFacade().get_orders()
        df = orders_page._order_status_distribution_frame(view)
        assert df.empty
        assert list(df.columns) == ["label", "weight"]

    def test_page_renders_offline_without_raise(self) -> None:
        facade = NullIntegrationFacade()
        ctx = _render_ctx(facade)
        with patch("dashboard.pages.orders.st") as st_mock:
            with patch("dashboard.pages.orders.render_page_header") as header:
                with patch("dashboard.pages.orders.render_kpi_row") as kpi:
                    with patch("dashboard.pages.orders.render_table") as table:
                        orders_page.render(ctx)
                        header.assert_called_once()
                        assert kpi.call_count >= 2
                        assert table.call_count == 2
            for title in (
                "Order Summary",
                "Active Orders",
                "Recent Order History",
                "Order Status Distribution",
                "Order Timeline",
                "Broker Status",
            ):
                st_mock.subheader.assert_any_call(title)
            st_mock.plotly_chart.assert_called()


class TestLiveMapping:
    """Live facade snapshot drives every section of the page."""

    def test_resolve_uses_get_orders_only(self) -> None:
        live = _live_orders_view()
        facade = MagicMock()
        facade.get_orders.return_value = live
        view = orders_page._resolve_orders_view(_render_ctx(facade))
        assert view is live
        facade.get_orders.assert_called_once()

    def test_summary_kpi_cards_live(self) -> None:
        view = _live_orders_view()
        cards = orders_summary_kpi_cards(view)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Total Orders"] == "3"
        assert by_label["Pending"] == "1"
        assert by_label["Filled"] == "1"
        assert by_label["Cancelled"] == "1"
        assert by_label["Rejected"] == "0"

    def test_broker_status_cards_live(self) -> None:
        view = _live_orders_view()
        cards = orders_broker_status_cards(view)
        by_label = {card.label: card.value for card in cards}
        assert by_label["Broker Connection"] == "CONNECTED"
        assert by_label["OMS Status"] == "RUNNING"
        assert by_label["Exchange Status"] == "OPEN"
        assert by_label["Last Order Time"] == "2026-08-07 03:20:00 UTC"
        assert by_label["Latency"] == "38 ms"

    def test_active_orders_excludes_terminal_statuses(self) -> None:
        view = _live_orders_view()
        active = orders_page._active_orders_frame(view)
        assert list(active["Order ID"]) == ["2"]
        assert list(active["Status"]) == ["PENDING"]

    def test_order_history_includes_all_and_caps_at_100(self) -> None:
        many_orders = tuple(
            OrderRowView(order_id=str(i), status="FILLED", symbol="NIFTY")
            for i in range(150)
        )
        view = replace(_live_orders_view(), orders=many_orders)
        history = orders_page._order_history_frame(view)
        assert len(history) == 100
        assert list(history["Order ID"]) == [str(i) for i in range(100)]

    def test_page_renders_live_panels(self) -> None:
        live = _live_orders_view()
        facade = MagicMock()
        facade.get_orders.return_value = live
        ctx = _render_ctx(facade)
        with patch("dashboard.pages.orders.st"):
            with patch("dashboard.pages.orders.render_page_header"):
                with patch("dashboard.pages.orders.render_kpi_row") as kpi:
                    with patch("dashboard.pages.orders.render_table") as table:
                        orders_page.render(ctx)
                        assert kpi.call_count >= 2
                        assert table.call_count == 2
                        active, history = [
                            call.args[0] for call in table.call_args_list
                        ]
                        assert not active.empty
                        assert not history.empty

    def test_resolve_exception_falls_back_to_placeholders(self) -> None:
        broken = MagicMock()
        broken.get_orders.side_effect = RuntimeError("boom")
        with patch("dashboard.pages.orders.render_error"):
            view = orders_page._resolve_orders_view(_render_ctx(broken))
        cards = orders_summary_kpi_cards(view)
        assert all(card.value == "0" for card in cards)
        assert view.source == "offline"


class TestCharts:
    """Order Status Distribution donut and Order Timeline chart data."""

    def test_status_distribution_reuses_summary_counts(self) -> None:
        view = _live_orders_view()
        series = orders_status_distribution(view)
        assert series == (
            ("Pending", 1.0),
            ("Filled", 1.0),
            ("Cancelled", 1.0),
            ("Rejected", 0.0),
        )

    def test_status_distribution_frame_live(self) -> None:
        view = _live_orders_view()
        df = orders_page._order_status_distribution_frame(view)
        assert set(df["label"]) == {"Pending", "Filled", "Cancelled", "Rejected"}
        assert df["weight"].sum() == 3.0

    def test_timeline_figure_offline_has_no_traces(self) -> None:
        empty_df = pd.DataFrame(
            columns=["Order ID", "Time", "Symbol", "Strategy", "Side", "Qty", "Price", "Status"]
        )
        figure = orders_page._build_order_timeline_figure(empty_df)
        assert figure.data == ()

    def test_timeline_figure_live_has_one_trace_per_status(self) -> None:
        view = _live_orders_view()
        history_df = orders_page._order_history_frame(view)
        figure = orders_page._build_order_timeline_figure(history_df)
        trace_names = {trace.name for trace in figure.data}
        assert trace_names == {"FILLED", "PENDING", "CANCELLED"}


class TestCsvExport:
    """Recent Order History CSV export."""

    def test_csv_export_contains_rows(self) -> None:
        view = _live_orders_view()
        df = orders_page._order_history_frame(view)
        csv_text = orders_page._orders_csv(df)
        assert "Order ID" in csv_text
        assert "1" in csv_text and "FILLED" in csv_text
        assert csv_text.count("\n") >= 3

    def test_csv_export_offline_is_header_only(self) -> None:
        view = NullIntegrationFacade().get_orders()
        df = orders_page._order_history_frame(view)
        csv_text = orders_page._orders_csv(df)
        assert csv_text.strip() == (
            "Order ID,Time,Symbol,Strategy,Side,Qty,Price,Status"
        )


class TestFilterOrders:
    """Search + status filter for the Recent Order History table."""

    def test_filter_by_status(self) -> None:
        view = _live_orders_view()
        df = orders_page._order_history_frame(view)
        filtered = orders_page._filter_orders(df, statuses=["FILLED"])
        assert list(filtered["Order ID"]) == ["1"]

    def test_filter_by_search_term(self) -> None:
        view = _live_orders_view()
        df = orders_page._order_history_frame(view)
        filtered = orders_page._filter_orders(df, search="banknifty")
        assert list(filtered["Order ID"]) == ["2"]

    def test_filter_no_match_returns_empty(self) -> None:
        view = _live_orders_view()
        df = orders_page._order_history_frame(view)
        filtered = orders_page._filter_orders(df, search="doesnotexist")
        assert filtered.empty


class TestFacadeMapping:
    """dashboard_facade.py soft-reads powering the Orders page."""

    def test_empty_order_book_defaults(self) -> None:
        book = empty_order_book(as_of=FIXED_NOW)
        assert book.total_orders == "0"
        assert book.orders_pending == "0"
        assert book.broker_connection_status == "DISCONNECTED"
        assert book.oms_status == "UNKNOWN"
        assert book.exchange_status == "UNKNOWN"
        assert book.last_order_time == PLACEHOLDER
        assert book.broker_latency == PLACEHOLDER

    def test_live_order_book_soft_reads_new_fields(self) -> None:
        session = SimpleNamespace(
            get_order_book=MagicMock(
                return_value=SimpleNamespace(
                    orders=(
                        SimpleNamespace(
                            order_id="1",
                            plan_id="p1",
                            status="FILLED",
                            symbol="NIFTY",
                            side="SELL",
                            quantity="1",
                            timestamp="t1",
                            strategy="Iron Condor",
                            price=120.5,
                        ),
                        SimpleNamespace(
                            order_id="2",
                            plan_id="p2",
                            status="PENDING",
                            symbol="BANKNIFTY",
                            side="BUY",
                            quantity="2",
                            timestamp="t2",
                            strategy="Short Strangle",
                            price=95.0,
                        ),
                    ),
                    broker_connection_status="CONNECTED",
                    oms_status="RUNNING",
                    exchange_status="OPEN",
                    latency_ms=27,
                )
            )
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        book = facade.get_order_book()
        assert book.total_orders == "2"
        assert book.orders_filled == "1"
        assert book.orders_pending == "1"
        assert book.orders[0].strategy == "Iron Condor"
        assert book.orders[0].price == "120.50"
        assert book.broker_connection_status == "CONNECTED"
        assert book.oms_status == "RUNNING"
        assert book.exchange_status == "OPEN"
        assert book.broker_latency == "27 ms"
        assert book.last_order_time == "t1"

    def test_broker_and_exchange_status_fall_back_to_system_status(self) -> None:
        session = SimpleNamespace(
            get_order_book=MagicMock(
                return_value=SimpleNamespace(orders=())
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
        book = facade.get_order_book()
        assert book.exchange_status == "OPEN"

    def test_presentation_adapter_maps_orders_page_view(self) -> None:
        facade = DashboardFacade(session=None, clock=lambda: FIXED_NOW)
        view = facade.as_presentation_facade().get_orders()
        assert view.total_orders == "0"
        assert view.broker_connection_status == "DISCONNECTED"
        assert view.orders == ()


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
