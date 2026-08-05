"""Unit tests for Strategy Monitor dashboard integration."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dashboard import default_dashboard_ui_config
from dashboard.dashboard_facade import (
    STRATEGY_MONITOR_FAMILIES,
    DashboardFacade,
    DashboardIntegrationFacade,
    empty_strategy_status,
    strategy_status_to_monitor_view,
)
from dashboard.facade import NullIntegrationFacade
from dashboard.pages import strategy_monitor as strategy_monitor_page
from dashboard.view_models import (
    DashboardRenderContext,
    DashboardSessionView,
    PLACEHOLDER,
    strategy_monitor_kpi_cards,
)
from types import MappingProxyType


FIXED_NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = REPO_ROOT / "dashboard"


def _connected_session(**extra: object) -> SimpleNamespace:
    """Build a connected session stub with optional strategy accessors."""
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
    """Build a minimal render context for Strategy Monitor tests."""
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


class TestOfflineStrategyMonitor:
    """Offline placeholders for the four monitor strategies."""

    def test_offline_returns_four_placeholder_strategies(self) -> None:
        facade = DashboardIntegrationFacade(session=None, clock=lambda: FIXED_NOW)
        status = facade.get_strategy_status()
        assert len(status.strategies) == 4
        assert status.source == "offline"
        assert status.market_regime == PLACEHOLDER
        assert status.active_strategy == PLACEHOLDER
        assert status.confidence_score == PLACEHOLDER
        assert status.evaluation_time == PLACEHOLDER
        for row in status.strategies:
            assert row.score == PLACEHOLDER
            assert row.status == PLACEHOLDER
            assert row.eligibility == PLACEHOLDER
            assert row.reason == PLACEHOLDER

    def test_family_order(self) -> None:
        status = empty_strategy_status(as_of=FIXED_NOW)
        families = tuple(row.family for row in status.strategies)
        assert families == tuple(fid for fid, _ in STRATEGY_MONITOR_FAMILIES)
        names = tuple(row.display_name for row in status.strategies)
        assert names == (
            "Short Strangle",
            "Iron Condor",
            "Bull Put Spread",
            "Bear Call Spread",
        )

    def test_null_facade_monitor_placeholders(self) -> None:
        view = NullIntegrationFacade().get_strategy_monitor()
        assert len(view.strategies) == 4
        assert view.market_regime == PLACEHOLDER
        cards = strategy_monitor_kpi_cards(view)
        assert [card.label for card in cards] == [
            "Market Regime",
            "Active Strategy",
            "Confidence Score",
            "Strategy Evaluation Time",
        ]


class TestLiveStrategyMonitorMapping:
    """Live stub mapping for scores, eligibility, and header fields."""

    def test_live_maps_four_strategies_and_header(self) -> None:
        session = _connected_session(
            get_strategy_status=MagicMock(
                return_value=SimpleNamespace(
                    market_regime="TRENDING_BULL",
                    active_strategy="iron_condor",
                    confidence_score=0.77,
                    evaluated_at=FIXED_NOW,
                    strategies=(
                        SimpleNamespace(
                            strategy_id="ss",
                            family="short_strangle",
                            ranking_score=55.0,
                            status="abstain",
                            reasons=("pop_low",),
                            eligible=False,
                        ),
                        SimpleNamespace(
                            strategy_id="ic",
                            family="iron_condor",
                            ranking_score=88.25,
                            status="success",
                            outcome_class="actionable",
                            confidence=SimpleNamespace(overall_score=0.77),
                            reasons=("regime_fit",),
                            eligible=True,
                        ),
                        SimpleNamespace(
                            strategy_id="bps",
                            family="bull_put_spread",
                            suitability_score=40.0,
                            status="abstain",
                            reason="direction_mismatch",
                            eligible=False,
                        ),
                        SimpleNamespace(
                            strategy_id="bcs",
                            family="bear_call_spread",
                            score=12.0,
                            status="failed",
                            reasons=("no_candidates",),
                            is_eligible=False,
                        ),
                    ),
                )
            )
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        status = facade.get_strategy_status()
        assert status.source == "live"
        assert status.market_regime == "TRENDING_BULL"
        assert status.active_strategy == "Iron Condor"
        assert status.confidence_score == "77.0%"
        assert "2026-08-05" in status.evaluation_time

        by_family = {row.family: row for row in status.strategies}
        assert by_family["short_strangle"].score == "55.00"
        assert by_family["short_strangle"].eligibility == "Rejected"
        assert by_family["iron_condor"].score == "88.25"
        assert by_family["iron_condor"].eligibility == "Eligible"
        assert by_family["bull_put_spread"].reason == "direction_mismatch"
        assert by_family["bear_call_spread"].eligibility == "Rejected"

    def test_partial_upstream_fills_missing_families(self) -> None:
        session = _connected_session(
            get_strategy_status=MagicMock(
                return_value=SimpleNamespace(
                    strategies=(
                        SimpleNamespace(
                            strategy_id="ic",
                            family="iron_condor",
                            ranking_score=70.0,
                            status="ACTIVE",
                            eligible=True,
                            reasons=("ok",),
                        ),
                    )
                )
            )
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        status = facade.get_strategy_status()
        assert len(status.strategies) == 4
        assert status.strategies[1].family == "iron_condor"
        assert status.strategies[1].score == "70.00"
        assert status.strategies[0].score == PLACEHOLDER
        assert status.strategies[2].eligibility == PLACEHOLDER

    def test_evaluation_bundle_reports_shape(self) -> None:
        session = _connected_session(
            get_strategy_evaluation_summary=MagicMock(
                return_value=SimpleNamespace(
                    evaluated_at=FIXED_NOW,
                    summary=SimpleNamespace(
                        top_strategy_id="bull_put_spread",
                        top_ranking_score=91.0,
                    ),
                    reports=(
                        SimpleNamespace(
                            strategy_id="bps",
                            strategy_family="bull_put_spread",
                            display_name="Bull Put Spread",
                            ranking_score=91.0,
                            evaluation_status="success",
                            outcome_class="actionable",
                            confidence=SimpleNamespace(overall_score=0.91),
                            reasons=("trend_aligned",),
                            evaluated_at=FIXED_NOW,
                        ),
                    ),
                )
            )
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        status = facade.get_strategy_status()
        assert status.active_strategy == "Bull Put Spread"
        bps = status.strategies[2]
        assert bps.display_name == "Bull Put Spread"
        assert bps.score == "91.00"
        assert bps.eligibility == "Eligible"


class TestPresentationAndPage:
    """Presentation adapter and Strategy Monitor page wiring."""

    def test_adapter_populates_monitor_view(self) -> None:
        facade = DashboardFacade(session=None, clock=lambda: FIXED_NOW)
        view = facade.as_presentation_facade().get_strategy_monitor()
        assert len(view.strategies) == 4
        mapped = strategy_status_to_monitor_view(
            facade.get_strategy_status()
        )
        assert mapped.strategies[0].display_name == "Short Strangle"

    def test_page_renders_offline_without_raise(self) -> None:
        facade = DashboardFacade(session=None, clock=lambda: FIXED_NOW)
        ctx = _render_ctx(facade.as_presentation_facade())
        with patch("dashboard.pages.strategy_monitor.st") as st_mock:
            st_mock.expander.return_value.__enter__ = MagicMock(return_value=st_mock)
            st_mock.expander.return_value.__exit__ = MagicMock(return_value=False)
            with patch("dashboard.pages.strategy_monitor.render_page_header"):
                with patch("dashboard.pages.strategy_monitor.render_kpi_row") as kpi:
                    with patch("dashboard.pages.strategy_monitor.render_table") as table:
                        strategy_monitor_page.render(ctx)
                        assert kpi.called
                        assert table.called
                        frame = table.call_args.args[0]
                        assert list(frame["Strategy"]) == [
                            "Short Strangle",
                            "Iron Condor",
                            "Bull Put Spread",
                            "Bear Call Spread",
                        ]

    def test_resolve_handles_exception(self) -> None:
        broken = MagicMock()
        broken.get_strategy_monitor.side_effect = RuntimeError("boom")
        with patch("dashboard.pages.strategy_monitor.render_error"):
            view = strategy_monitor_page._resolve_monitor_view(_render_ctx(broken))
        assert len(view.strategies) == 4
        assert view.market_regime == PLACEHOLDER


class TestNoForbiddenImports:
    """Strategy Monitor path must not import broker or execute strategies."""

    FORBIDDEN = ("broker", "kiteconnect")

    def test_no_broker_imports(self) -> None:
        paths = (
            DASHBOARD_ROOT / "pages" / "strategy_monitor.py",
            DASHBOARD_ROOT / "dashboard_facade.py",
        )
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in self.FORBIDDEN
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".")[0]
                    assert root not in self.FORBIDDEN
                    # Must not import strategy engines for evaluation.
                    if root == "strategy":
                        assert "strategy_evaluation" not in (node.module or "")
