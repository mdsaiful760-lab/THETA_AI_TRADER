"""Unit tests for the real 3-tier Engine Status classification."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from dashboard.dashboard_facade import DashboardIntegrationFacade

FIXED_NOW = datetime(2026, 8, 8, 9, 0, 0, tzinfo=timezone.utc)


def _base_session(**overrides: object) -> SimpleNamespace:
    """Build a session stub with every accessor absent by default."""
    defaults: dict[str, object] = {
        "get_health": lambda: SimpleNamespace(
            session_state=SimpleNamespace(value="running"),
            overall_status=SimpleNamespace(value="healthy"),
            broker_connection=SimpleNamespace(state="connected", connected=True),
            message="ok",
        ),
        "get_runtime_state": lambda: SimpleNamespace(execution_mode="PAPER", market_status="OPEN"),
        "get_market_snapshot": lambda: SimpleNamespace(
            underlyings=("NIFTY",), selected_underlying="NIFTY", ltp="24500.0",
            change="+10.0", volume="1.0M",
            option_chain_columns=(
                "strike", "type", "ltp", "bid", "ask", "oi", "oi_change", "volume",
                "iv", "delta", "gamma", "theta", "vega",
            ),
            option_chain_rows=(), atm_strike="—",
        ),
        "get_strategy_status": lambda: SimpleNamespace(market_regime="—", strategies=()),
        "get_paper_trading_ledger": lambda: SimpleNamespace(
            available_cash=None, capital_used=None, total_equity=None, todays_pnl=None,
            realized_pnl=None, unrealized_pnl=None, positions=(), orders_filled=0,
            orders_pending=0, orders_cancelled=0, orders_rejected=0,
        ),
        "get_home_market_indices": lambda: SimpleNamespace(indices=()),
        "get_logs": lambda **_kw: SimpleNamespace(entries=()),
        "get_order_book": lambda: SimpleNamespace(orders=()),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _engine_by_name(overview, name: str):
    return next(row for row in overview.engines if row.name == name)


class TestEngineStatusThreeTier:
    def test_fully_offline_session_reports_no_engines_registered(self) -> None:
        # No session at all -> the overview short-circuits before building
        # any engine rows (distinct from a connected-but-empty-data cycle,
        # which is covered by the "degraded" test below).
        facade = DashboardIntegrationFacade(session=None, clock=lambda: FIXED_NOW)
        overview = facade.get_dashboard_overview("NIFTY")
        assert overview.engines == ()
        assert overview.engines_overall_health == "—"
        assert overview.source == "offline"

    def test_connected_session_with_no_real_data_is_degraded_not_healthy(self) -> None:
        # Session exists (registered) but every domain's real data is empty
        # this cycle -> degraded, never silently promoted to healthy.
        facade = DashboardIntegrationFacade(session=_base_session(), clock=lambda: FIXED_NOW)
        overview = facade.get_dashboard_overview("NIFTY")

        market_row = _engine_by_name(overview, "Market Data Engine")
        assert market_row.state == "degraded"
        assert market_row.heartbeat == "09:00:00"

        ai_row = _engine_by_name(overview, "AI Decision Engine")
        assert ai_row.state == "down"  # no ai_decision_loop handle -> panel is None

    def test_real_option_chain_rows_mark_market_and_greeks_healthy(self) -> None:
        rows = (
            (
                "24500", "CE", "150.0", "148", "152", "12000", "500", "1000",
                "14.2", "0.62", "0.02", "-4.1", "8.5",
            ),
        )
        session = _base_session(
            get_market_snapshot=lambda: SimpleNamespace(
                underlyings=("NIFTY",), selected_underlying="NIFTY", ltp="24500.0",
                change="+10.0", volume="1.0M",
                option_chain_columns=(
                    "strike", "type", "ltp", "bid", "ask", "oi", "oi_change", "volume",
                    "iv", "delta", "gamma", "theta", "vega",
                ),
                option_chain_rows=rows, atm_strike="24500",
            ),
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        overview = facade.get_dashboard_overview("NIFTY")

        assert _engine_by_name(overview, "Market Data Engine").state == "healthy"
        assert _engine_by_name(overview, "Greeks & OI Pipeline").state == "healthy"

    def test_ai_panel_present_with_real_decision_marks_three_engines_healthy(self) -> None:
        session = _base_session(
            get_ai_panel=lambda: SimpleNamespace(
                decision_status="TRADE_CANDIDATE", risk_verdict="APPROVED",
                strategy_id="short_strangle", reasons=("gate ok",),
                next_evaluation_display="30s", signal_active=True,
                as_of=FIXED_NOW, underlying="NIFTY",
            ),
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        overview = facade.get_dashboard_overview("NIFTY")

        assert _engine_by_name(overview, "AI Decision Engine").state == "healthy"
        assert _engine_by_name(overview, "Risk Engine").state == "healthy"
        assert _engine_by_name(overview, "Position Sizing Engine").state == "healthy"

    def test_overall_health_excellent_only_when_every_engine_is_healthy(self) -> None:
        rows = (
            (
                "24500", "CE", "150.0", "148", "152", "12000", "500", "1000",
                "14.2", "0.62", "0.02", "-4.1", "8.5",
            ),
        )
        session = _base_session(
            get_market_snapshot=lambda: SimpleNamespace(
                underlyings=("NIFTY",), selected_underlying="NIFTY", ltp="24500.0",
                change="+10.0", volume="1.0M",
                option_chain_columns=(
                    "strike", "type", "ltp", "bid", "ask", "oi", "oi_change", "volume",
                    "iv", "delta", "gamma", "theta", "vega",
                ),
                option_chain_rows=rows, atm_strike="24500",
            ),
            get_ai_panel=lambda: SimpleNamespace(
                decision_status="TRADE_CANDIDATE", risk_verdict="APPROVED",
                strategy_id="short_strangle", reasons=("gate ok",),
                next_evaluation_display="30s", signal_active=True,
                as_of=FIXED_NOW, underlying="NIFTY",
            ),
            get_strategy_status=lambda: SimpleNamespace(
                market_regime="RANGE_BOUND",
                strategies=(
                    SimpleNamespace(
                        display_name="Short Strangle", family="short_strangle", status="READY",
                        score="80", confidence="75%", recommendation_state="BULLISH",
                    ),
                ),
            ),
            get_paper_trading_ledger=lambda: SimpleNamespace(
                available_cash=1000000.0, capital_used=0.0, total_equity=1000000.0,
                todays_pnl=0.0, realized_pnl=0.0, unrealized_pnl=0.0, positions=(),
                orders_filled=0, orders_pending=0, orders_cancelled=0, orders_rejected=0,
            ),
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        overview = facade.get_dashboard_overview("NIFTY")

        assert all(row.state == "healthy" for row in overview.engines), overview.engines
        assert overview.engines_overall_health == "EXCELLENT"
