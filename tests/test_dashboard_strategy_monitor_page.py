"""Page-level tests for ``dashboard/pages/strategy_monitor.py``."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from unittest.mock import MagicMock, patch

from dashboard import default_dashboard_ui_config
from dashboard.facade import NullIntegrationFacade
from dashboard.pages import strategy_monitor as strategy_monitor_page
from dashboard.view_models import (
    DashboardRenderContext,
    DashboardSessionView,
    PLACEHOLDER,
    StrategyGateView,
    StrategyLegView,
    StrategyMonitorView,
    StrategyRowView,
)


FIXED_NOW = datetime(2026, 8, 6, 3, 0, 0, tzinfo=timezone.utc)
PAGE_PATH = (
    Path(__file__).resolve().parents[1] / "dashboard" / "pages" / "strategy_monitor.py"
)


def _render_ctx(facade: object) -> DashboardRenderContext:
    """Build a minimal render context for Strategy Monitor page tests."""
    return DashboardRenderContext(
        config=default_dashboard_ui_config(),
        facade=facade,  # type: ignore[arg-type]
        session=DashboardSessionView(
            active_page="strategy_monitor",
            last_error=None,
            last_refresh_at=None,
            facade_action_pending=False,
            ui_prefs=MappingProxyType({}),
        ),
        clock=lambda: FIXED_NOW,
        version="1.0.0",
    )


def _live_monitor_view() -> StrategyMonitorView:
    """Return a live-shaped StrategyMonitorView for page display tests."""
    return StrategyMonitorView(
        market_regime="RANGE_BOUND",
        active_strategy="Iron Condor",
        confidence_score="80.0%",
        evaluation_time="2026-08-06 03:00:00 UTC",
        recommendation_banner="Recommended: Iron Condor · Eligible · Score 90.00",
        source="live",
        as_of=FIXED_NOW.isoformat(),
        strategies=(
            StrategyRowView(
                strategy_id="ss",
                family="short_strangle",
                display_name="Short Strangle",
                status="abstain",
                confidence=PLACEHOLDER,
                score="55.00",
                eligibility="Rejected",
                reason="pop_low",
                rank="2",
            ),
            StrategyRowView(
                strategy_id="ic",
                family="iron_condor",
                display_name="Iron Condor",
                status="success",
                confidence="80.0%",
                score="90.00",
                eligibility="Eligible",
                reason="regime_fit",
                rank="1",
                recommendation_state="ENTER",
                detail_summary="Iron Condor · Score 90.00 · Eligible",
                reasons=("regime_fit",),
                gates=(
                    StrategyGateView(name="REGIME", outcome="PASS", detail="range-bound"),
                    StrategyGateView(name="IV_RANK", outcome="PASS", detail="62"),
                ),
                legs=(
                    StrategyLegView(
                        side="SELL",
                        option_type="PUT",
                        strike="24000",
                        quantity="1",
                        symbol="NIFTY24P24000",
                        delta="-0.16",
                    ),
                    StrategyLegView(
                        side="BUY",
                        option_type="PUT",
                        strike="23800",
                        quantity="1",
                        symbol="NIFTY24P23800",
                        delta="-0.08",
                    ),
                ),
            ),
            StrategyRowView(
                strategy_id="bps",
                family="bull_put_spread",
                display_name="Bull Put Spread",
                status="abstain",
                score="40.00",
                eligibility="Rejected",
                reason="direction_mismatch",
                rank="3",
            ),
            StrategyRowView(
                strategy_id="bcs",
                family="bear_call_spread",
                display_name="Bear Call Spread",
                status="failed",
                score="12.00",
                eligibility="Rejected",
                reason="no_candidates",
                rank="4",
            ),
        ),
    )


class TestOfflinePlaceholders:
    """Offline mode must show placeholders without crashing."""

    def test_offline_view_has_four_placeholder_strategies(self) -> None:
        view = NullIntegrationFacade().get_strategy_monitor()
        assert len(view.strategies) == 4
        assert view.recommendation_banner == PLACEHOLDER
        assert view.market_regime == PLACEHOLDER
        assert view.active_strategy == PLACEHOLDER
        for row in view.strategies:
            assert row.score == PLACEHOLDER
            assert row.rank == PLACEHOLDER
            assert row.gates == ()
            assert row.legs == ()

    def test_offline_ranking_frame_uses_placeholders(self) -> None:
        view = NullIntegrationFacade().get_strategy_monitor()
        frame = strategy_monitor_page._strategy_ranking_frame(view)
        assert list(frame.columns) == [
            "Rank",
            "Strategy",
            "Score",
            "Status",
            "Reason",
            "Eligible / Rejected",
        ]
        assert list(frame["Strategy"]) == [
            "Short Strangle",
            "Iron Condor",
            "Bull Put Spread",
            "Bear Call Spread",
        ]
        assert all(value == PLACEHOLDER for value in frame["Score"])
        assert all(value == PLACEHOLDER for value in frame["Rank"])

    def test_offline_gates_and_legs_empty_with_headers(self) -> None:
        view = NullIntegrationFacade().get_strategy_monitor()
        selected = view.strategies[0]
        gates = strategy_monitor_page._gates_frame(selected)
        legs = strategy_monitor_page._legs_frame(selected)
        assert list(gates.columns) == ["Gate", "Outcome", "Detail"]
        assert gates.empty
        assert list(legs.columns) == ["Side", "Type", "Strike", "Qty", "Symbol", "Delta"]
        assert legs.empty

    def test_page_renders_offline_without_raise(self) -> None:
        facade = NullIntegrationFacade()
        ctx = _render_ctx(facade)
        with patch("dashboard.pages.strategy_monitor.st") as st_mock:
            st_mock.selectbox.return_value = "Short Strangle"
            st_mock.expander.return_value.__enter__ = MagicMock(return_value=st_mock)
            st_mock.expander.return_value.__exit__ = MagicMock(return_value=False)
            with patch("dashboard.pages.strategy_monitor.render_page_header") as header:
                with patch("dashboard.pages.strategy_monitor.render_kpi_row"):
                    with patch("dashboard.pages.strategy_monitor.render_table") as table:
                        strategy_monitor_page.render(ctx)
                        header.assert_called_once()
                        assert table.call_count >= 3
                        ranking = table.call_args_list[0].args[0]
                        assert list(ranking["Strategy"]) == [
                            "Short Strangle",
                            "Iron Condor",
                            "Bull Put Spread",
                            "Bear Call Spread",
                        ]
            st_mock.info.assert_any_call(PLACEHOLDER)


class TestLivePageDisplay:
    """Live facade snapshot must drive all five required panels."""

    def test_resolve_uses_get_strategy_monitor_only(self) -> None:
        live = _live_monitor_view()
        facade = MagicMock()
        facade.get_strategy_monitor.return_value = live
        facade.get_strategy_status.side_effect = AssertionError(
            "page must not call get_strategy_status"
        )
        view = strategy_monitor_page._resolve_monitor_view(_render_ctx(facade))
        assert view is live
        facade.get_strategy_monitor.assert_called_once()

    def test_ranking_gates_legs_and_banner_from_live_view(self) -> None:
        view = _live_monitor_view()
        ranking = strategy_monitor_page._strategy_ranking_frame(view)
        assert ranking.loc[1, "Strategy"] == "Iron Condor"
        assert ranking.loc[1, "Rank"] == "1"
        assert ranking.loc[1, "Score"] == "90.00"

        selected = view.strategies[1]
        gates = strategy_monitor_page._gates_frame(selected)
        legs = strategy_monitor_page._legs_frame(selected)
        assert list(gates["Gate"]) == ["REGIME", "IV_RANK"]
        assert list(legs["Strike"]) == ["24000", "23800"]
        assert view.recommendation_banner.startswith("Recommended: Iron Condor")

    def test_page_renders_live_panels(self) -> None:
        live = _live_monitor_view()
        facade = MagicMock()
        facade.get_strategy_monitor.return_value = live
        ctx = _render_ctx(facade)

        with patch("dashboard.pages.strategy_monitor.st") as st_mock:
            st_mock.selectbox.return_value = "Iron Condor"
            st_mock.expander.return_value.__enter__ = MagicMock(return_value=st_mock)
            st_mock.expander.return_value.__exit__ = MagicMock(return_value=False)
            with patch("dashboard.pages.strategy_monitor.render_page_header"):
                with patch("dashboard.pages.strategy_monitor.render_kpi_row") as kpi:
                    with patch("dashboard.pages.strategy_monitor.render_table") as table:
                        strategy_monitor_page.render(ctx)
                        assert kpi.call_count >= 2
                        assert table.call_count == 3
                        ranking, gates, legs = [call.args[0] for call in table.call_args_list]
                        assert "Rank" in ranking.columns
                        assert list(gates["Gate"]) == ["REGIME", "IV_RANK"]
                        assert list(legs["Side"]) == ["SELL", "BUY"]
            st_mock.success.assert_called()
            st_mock.subheader.assert_any_call("Strategy ranking")
            st_mock.subheader.assert_any_call("Selected strategy details")
            st_mock.subheader.assert_any_call("Gate evaluation")
            st_mock.subheader.assert_any_call("Recommended option legs")

    def test_resolve_exception_falls_back_to_placeholders(self) -> None:
        broken = MagicMock()
        broken.get_strategy_monitor.side_effect = RuntimeError("boom")
        with patch("dashboard.pages.strategy_monitor.render_error"):
            view = strategy_monitor_page._resolve_monitor_view(_render_ctx(broken))
        assert len(view.strategies) == 4
        assert view.recommendation_banner == PLACEHOLDER
        assert view.source == "offline"


class TestNoForbiddenImports:
    """Strategy Monitor page must stay presentation-only."""

    FORBIDDEN_ROOTS = ("broker", "kiteconnect", "execution", "paper_trading")

    def test_page_has_no_forbidden_imports(self) -> None:
        tree = ast.parse(PAGE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in self.FORBIDDEN_ROOTS
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in self.FORBIDDEN_ROOTS
                assert root != "strategy"
