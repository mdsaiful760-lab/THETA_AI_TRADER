"""Unit tests for Paper Trading dashboard integration."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dashboard import default_dashboard_ui_config
from dashboard.config import DashboardConfigurationError, DashboardUiConfig
from dashboard.dashboard_facade import (
    DashboardFacade,
    DashboardIntegrationFacade,
    empty_paper_trading_ledger,
    paper_ledger_to_page_view,
)
from dashboard.facade import NullIntegrationFacade
from dashboard.pages import paper_trading as paper_trading_page
from dashboard.utils.polling import (
    paper_trading_refresh_interval_ms,
    should_paper_trading_autorefresh,
)
from dashboard.view_models import (
    DashboardRenderContext,
    DashboardSessionView,
    PLACEHOLDER,
    paper_order_count_cards,
    paper_trading_kpi_cards,
)


FIXED_NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
DASHBOARD_ROOT = Path(__file__).resolve().parents[1] / "dashboard"


def _connected_session(**extra: object) -> SimpleNamespace:
    """Build a connected session stub with optional paper accessors."""
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


def _render_ctx(facade: object) -> DashboardRenderContext:
    """Build a minimal render context for Paper Trading tests."""
    return DashboardRenderContext(
        config=default_dashboard_ui_config(),
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


class TestOfflinePaperLedger:
    """T01: Offline ledger placeholders."""

    def test_offline_money_placeholders_and_zero_order_counts(self) -> None:
        facade = DashboardIntegrationFacade(session=None, clock=lambda: FIXED_NOW)
        ledger = facade.get_paper_trading_ledger()
        assert ledger.source == "offline"
        assert ledger.available_cash == PLACEHOLDER
        assert ledger.capital_used == PLACEHOLDER
        assert ledger.total_equity == PLACEHOLDER
        assert ledger.todays_pnl == PLACEHOLDER
        assert ledger.realized_pnl == PLACEHOLDER
        assert ledger.unrealized_pnl == PLACEHOLDER
        assert ledger.positions == ()
        assert ledger.orders_filled == "0"
        assert ledger.orders_pending == "0"
        assert ledger.orders_cancelled == "0"
        assert ledger.orders_rejected == "0"

    def test_null_facade_kpis(self) -> None:
        view = NullIntegrationFacade().get_paper_trading()
        cards = paper_trading_kpi_cards(view)
        assert [card.label for card in cards] == [
            "Available Cash",
            "Capital Used",
            "Total Equity",
            "Today's P&L",
            "Realized P&L",
            "Unrealized P&L",
        ]
        assert all(card.value == PLACEHOLDER for card in cards)
        order_cards = paper_order_count_cards(view)
        assert [card.value for card in order_cards] == ["0", "0", "0", "0"]


class TestLivePaperLedgerMapping:
    """T02/T03/T04: Live mapping and order buckets."""

    def test_live_maps_capital_and_positions(self) -> None:
        session = _connected_session(
            get_paper_trading_snapshot=MagicMock(
                return_value=SimpleNamespace(
                    available_cash=100000.0,
                    capital_used=25000.0,
                    total_equity=100450.0,
                    todays_pnl=450.0,
                    realized_pnl=200.0,
                    unrealized_pnl=250.0,
                    positions=(
                        SimpleNamespace(
                            symbol="NIFTY",
                            strategy_id="iron_condor",
                            quantity=-50,
                            average_price=120.5,
                            mark_price=118.0,
                            unrealized_pnl=125.0,
                            status="OPEN",
                        ),
                    ),
                    equity_series=(("2026-08-05", 100450.0),),
                )
            ),
            get_order_book=MagicMock(
                return_value=SimpleNamespace(
                    orders=(
                        SimpleNamespace(
                            order_id="1",
                            plan_id="p",
                            status="FILLED",
                            symbol="NIFTY",
                            side="SELL",
                            quantity="1",
                            timestamp="t1",
                        ),
                        SimpleNamespace(
                            order_id="2",
                            plan_id="p",
                            status="PENDING",
                            symbol="NIFTY",
                            side="BUY",
                            quantity="1",
                            timestamp="t2",
                        ),
                        SimpleNamespace(
                            order_id="3",
                            plan_id="p",
                            status="CANCELLED",
                            symbol="BANKNIFTY",
                            side="SELL",
                            quantity="1",
                            timestamp="t3",
                        ),
                        SimpleNamespace(
                            order_id="4",
                            plan_id="p",
                            status="REJECTED",
                            symbol="SENSEX",
                            side="BUY",
                            quantity="1",
                            timestamp="t4",
                        ),
                    )
                )
            ),
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        ledger = facade.get_paper_trading_ledger()
        assert ledger.source == "live"
        assert ledger.available_cash == "100,000.00"
        assert ledger.capital_used == "25,000.00"
        assert ledger.total_equity == "100,450.00"
        assert ledger.todays_pnl == "450.00"
        assert ledger.realized_pnl == "200.00"
        assert ledger.unrealized_pnl == "250.00"
        assert len(ledger.positions) == 1
        pos = ledger.positions[0]
        assert pos.symbol == "NIFTY"
        assert pos.strategy == "iron_condor"
        assert pos.quantity == "-50"
        assert pos.entry == "120.50"
        assert pos.current == "118.00"
        assert pos.mtm == "125.00"
        assert pos.status == "OPEN"
        assert ledger.orders_filled == "1"
        assert ledger.orders_pending == "1"
        assert ledger.orders_cancelled == "1"
        assert ledger.orders_rejected == "1"

    def test_partial_positions_and_equity_composition(self) -> None:
        session = _connected_session(
            get_paper_positions=MagicMock(
                return_value=SimpleNamespace(
                    cash=50000.0,
                    unrealized_pnl=100.0,
                    realized_pnl=0.0,
                    positions=(
                        SimpleNamespace(
                            instrument_key="BANKNIFTY",
                            qty=10,
                            avg_price=100,
                            mark=101,
                            pnl=10,
                        ),
                    ),
                )
            )
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        ledger = facade.get_paper_trading_ledger()
        assert ledger.available_cash == "50,000.00"
        assert ledger.total_equity == "50,100.00"
        assert ledger.positions[0].symbol == "BANKNIFTY"
        assert ledger.positions[0].status == "OPEN"
        assert ledger.orders_filled == "0"


class TestPresentationAndPage:
    """T05/T06: Presentation adapter and page render."""

    def test_adapter_populates_from_ledger(self) -> None:
        facade = DashboardFacade(session=None, clock=lambda: FIXED_NOW)
        view = facade.as_presentation_facade().get_paper_trading()
        assert view.available_cash == PLACEHOLDER
        assert view.orders_filled == "0"
        assert view.positions == ()
        mapped = paper_ledger_to_page_view(empty_paper_trading_ledger(as_of=FIXED_NOW))
        assert mapped.virtual_cash == PLACEHOLDER

    def test_page_renders_offline_without_raise(self) -> None:
        facade = DashboardFacade(session=None, clock=lambda: FIXED_NOW)
        ctx = _render_ctx(facade.as_presentation_facade())
        with patch("dashboard.pages.paper_trading.st") as st_mock:
            st_mock.fragment = None
            with patch("dashboard.pages.paper_trading.render_page_header"):
                with patch("dashboard.pages.paper_trading.render_kpi_row") as kpi:
                    with patch("dashboard.pages.paper_trading.render_table"):
                        paper_trading_page.render(ctx)
                        assert kpi.call_count >= 2


class TestPaperRefreshConfig:
    """T07/T08: Refresh defaults and helper."""

    def test_refresh_defaults_to_one_second(self) -> None:
        config = default_dashboard_ui_config()
        assert config.paper_trading_refresh_seconds == 1.0
        assert config.enable_paper_trading_autorefresh is True
        assert paper_trading_refresh_interval_ms(config) == 1000

    def test_refresh_validation(self) -> None:
        with pytest.raises(DashboardConfigurationError) as exc:
            DashboardUiConfig(paper_trading_refresh_seconds=0.0)
        assert exc.value.code == "CFG-DASH-PAPER-001"

    def test_should_autorefresh_after_interval(self) -> None:
        config = default_dashboard_ui_config()
        now = FIXED_NOW
        assert should_paper_trading_autorefresh(
            config, last_refresh_at=None, now=now
        )
        assert not should_paper_trading_autorefresh(
            config,
            last_refresh_at=now,
            now=now + timedelta(milliseconds=500),
        )
        assert should_paper_trading_autorefresh(
            config,
            last_refresh_at=now,
            now=now + timedelta(seconds=1.0),
        )


class TestNoForbiddenImports:
    """T09: No broker / runner imports on page path."""

    FORBIDDEN = ("broker", "kiteconnect", "paper_trading")

    def test_page_has_no_forbidden_imports(self) -> None:
        path = DASHBOARD_ROOT / "pages" / "paper_trading.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in self.FORBIDDEN
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in self.FORBIDDEN
