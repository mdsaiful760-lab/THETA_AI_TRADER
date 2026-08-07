"""Unit tests for the real Option Summary derived values (ATM IV, Top OI/Volume)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from dashboard.dashboard_facade import (
    DashboardIntegrationFacade,
    _build_top_by_metric_rows,
    _classify_alert_category,
    _compute_atm_iv,
)

FIXED_NOW = datetime(2026, 8, 8, 9, 0, 0, tzinfo=timezone.utc)

_COLUMNS = (
    "strike", "type", "ltp", "bid", "ask", "oi", "oi_change", "volume",
    "iv", "delta", "gamma", "theta", "vega",
)


def _row(
    strike: str, option_type: str, *, ltp: str, oi: str, oi_change: str, volume: str, iv: str
) -> tuple[str, ...]:
    return (strike, option_type, ltp, "0", "0", oi, oi_change, volume, iv, "0", "0", "0", "0")


class TestAtmIv:
    def test_averages_real_ce_and_pe_iv_at_the_real_atm_strike(self) -> None:
        rows = (
            _row("24500", "CE", ltp="150", oi="1000", oi_change="0", volume="500", iv="14.0"),
            _row("24500", "PE", ltp="90", oi="900", oi_change="0", volume="400", iv="16.0"),
            _row("24600", "CE", ltp="100", oi="800", oi_change="0", volume="300", iv="20.0"),
        )
        assert _compute_atm_iv(rows, "24500", "—") == "15.00%"

    def test_no_atm_strike_returns_placeholder(self) -> None:
        rows = (_row("24500", "CE", ltp="150", oi="1000", oi_change="0", volume="500", iv="14.0"),)
        assert _compute_atm_iv(rows, "—", "—") == "—"

    def test_no_matching_rows_returns_placeholder(self) -> None:
        rows = (_row("24500", "CE", ltp="150", oi="1000", oi_change="0", volume="500", iv="14.0"),)
        assert _compute_atm_iv(rows, "99999", "—") == "—"


class TestTopByMetric:
    def test_ranks_by_real_oi_descending_per_side(self) -> None:
        rows = (
            _row("24000", "CE", ltp="150", oi="500", oi_change="10", volume="100", iv="14.0"),
            _row("24500", "CE", ltp="120", oi="1500", oi_change="-5", volume="200", iv="13.5"),
            _row("24000", "PE", ltp="90", oi="900", oi_change="20", volume="150", iv="15.0"),
            _row("24500", "PE", ltp="60", oi="300", oi_change="0", volume="50", iv="16.0"),
        )
        calls, puts = _build_top_by_metric_rows(rows, metric="oi", placeholder="—")
        assert [row.strike for row in calls] == ["24500", "24000"]
        assert [row.strike for row in puts] == ["24000", "24500"]
        assert calls[0].open_interest == "1,500"
        assert calls[0].change_percent == "-5"
        assert calls[0].trend == "down"

    def test_ranks_by_real_volume_descending_per_side(self) -> None:
        rows = (
            _row("24000", "CE", ltp="150", oi="500", oi_change="0", volume="100", iv="14.0"),
            _row("24500", "CE", ltp="120", oi="1500", oi_change="0", volume="900", iv="13.5"),
        )
        calls, _puts = _build_top_by_metric_rows(rows, metric="volume", placeholder="—")
        # 24500 has less OI but far more volume -> ranked first by volume.
        assert calls[0].strike == "24500"

    def test_caps_at_five_per_side(self) -> None:
        rows = tuple(
            _row(str(24000 + i * 100), "CE", ltp="100", oi=str(1000 - i), oi_change="0",
                 volume="10", iv="14.0")
            for i in range(8)
        )
        calls, _puts = _build_top_by_metric_rows(rows, metric="oi", placeholder="—")
        assert len(calls) == 5

    def test_empty_chain_returns_empty_both_sides(self) -> None:
        calls, puts = _build_top_by_metric_rows((), metric="oi", placeholder="—")
        assert calls == ()
        assert puts == ()


class TestScannerExpectedPop:
    """Expected POP is real when the upstream evaluation report exposes it;
    Expected ROI/Theta have no such field and must stay honest placeholders."""

    def _session_with_strategy(self, **strategy_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            get_health=lambda: SimpleNamespace(
                session_state=SimpleNamespace(value="running"),
                overall_status=SimpleNamespace(value="healthy"),
                broker_connection=SimpleNamespace(state="connected", connected=True),
                message="ok",
            ),
            get_runtime_state=lambda: SimpleNamespace(execution_mode="PAPER", market_status="OPEN"),
            get_market_snapshot=lambda: SimpleNamespace(
                underlyings=("NIFTY",), selected_underlying="NIFTY", ltp="24500.0",
                change="+10.0", volume="1.0M",
                option_chain_columns=(
                    "strike", "type", "ltp", "bid", "ask", "oi", "oi_change", "volume",
                    "iv", "delta", "gamma", "theta", "vega",
                ),
                option_chain_rows=(), atm_strike="—",
            ),
            get_strategy_status=lambda: SimpleNamespace(
                market_regime="RANGE_BOUND",
                strategies=(
                    SimpleNamespace(
                        family="short_strangle", display_name="Short Strangle",
                        status="READY", confidence=0.7, recommendation_state="BULLISH",
                        **strategy_kwargs,
                    ),
                ),
            ),
            get_paper_trading_ledger=lambda: SimpleNamespace(
                available_cash=None, capital_used=None, total_equity=None, todays_pnl=None,
                realized_pnl=None, unrealized_pnl=None, positions=(), orders_filled=0,
                orders_pending=0, orders_cancelled=0, orders_rejected=0,
            ),
            get_home_market_indices=lambda: SimpleNamespace(indices=()),
            get_logs=lambda **_kw: SimpleNamespace(entries=()),
            get_order_book=lambda: SimpleNamespace(orders=()),
        )

    def test_real_expected_pop_from_upstream_report_flows_through(self) -> None:
        session = self._session_with_strategy(expected_pop=0.62)
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        overview = facade.get_dashboard_overview("NIFTY")
        row = next(r for r in overview.scanner_rows if r.strategy == "Short Strangle")
        assert row.expected_pop == "62.0%"
        # No backend field exists for these -> always an honest placeholder.
        assert row.expected_roi == "—"
        assert row.expected_theta == "—"
        assert row.status == "READY"

    def test_missing_expected_pop_is_placeholder_not_fabricated(self) -> None:
        session = self._session_with_strategy()
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        overview = facade.get_dashboard_overview("NIFTY")
        row = next(r for r in overview.scanner_rows if r.strategy == "Short Strangle")
        assert row.expected_pop == "—"


class TestAlertCategoryClassification:
    """Category is a real classification of the log entry's own real
    logger name — never a fabricated tag."""

    def test_market_data_logger_classified_as_market(self) -> None:
        assert _classify_alert_category("market_data.market_data_engine") == "Market"
        assert _classify_alert_category("broker.market_data_bridge") == "Market"

    def test_decision_and_apme_loggers_classified_as_ai(self) -> None:
        assert _classify_alert_category("decision.ai_decision_loop") == "AI"
        assert _classify_alert_category("apme.adaptive_position_management_engine") == "AI"

    def test_broker_logger_classified_as_broker(self) -> None:
        assert _classify_alert_category("broker.kite_authentication") == "Broker"
        assert _classify_alert_category("broker.zerodha.kite_ws") == "Broker"

    def test_execution_and_paper_trading_loggers_classified_as_execution(self) -> None:
        assert _classify_alert_category("execution.order_manager") == "Execution"
        assert _classify_alert_category("paper_trading.paper_trading_runner") == "Execution"

    def test_unrecognized_logger_falls_back_to_system(self) -> None:
        assert _classify_alert_category("system.system_orchestrator") == "System"
        assert _classify_alert_category("dashboard.pages.home") == "System"
