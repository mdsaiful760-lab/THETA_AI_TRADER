"""Backend facade protocol and offline null implementation."""

from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable

from dashboard.config import DEFAULT_INDEX_SYMBOLS
from dashboard.view_models import (
    AnalyticsPageView,
    ApmePageView,
    FacadeActionResult,
    HomeKpiView,
    HomePageView,
    LogsPageView,
    MarketPageView,
    OrdersPageView,
    PaperTradingPageView,
    PortfolioPageView,
    RiskPageView,
    RuntimeStateView,
    SettingsPageView,
    StrategyMonitorView,
    SystemStatusView,
    default_index_quotes,
)


@runtime_checkable
class DashboardBackendFacade(Protocol):
    """Read-only backend facade with lifecycle delegation."""

    def get_health(self) -> SystemStatusView:
        """Return current system health."""

    def get_runtime_state(self) -> RuntimeStateView:
        """Return current runtime execution state."""

    def get_home_snapshot(self) -> HomePageView:
        """Return home page snapshot."""

    def get_home_market_indices(self) -> object:
        """Return Home market index quotes for the terminal strip."""

    def get_market_snapshot(self) -> MarketPageView:
        """Return market page snapshot."""

    def get_strategy_monitor(self) -> StrategyMonitorView:
        """Return strategy monitor snapshot."""

    def get_paper_trading(self) -> PaperTradingPageView:
        """Return paper trading snapshot."""

    def get_paper_trading_ledger(self) -> object:
        """Return Paper Trading ledger aggregation for the page."""

    def get_orders(self) -> OrdersPageView:
        """Return orders snapshot."""

    def get_portfolio(self) -> PortfolioPageView:
        """Return portfolio snapshot."""

    def get_risk(self) -> RiskPageView:
        """Return risk snapshot."""

    def get_apme(self) -> ApmePageView:
        """Return APME snapshot."""

    def get_logs(self, *, limit: int = 200) -> LogsPageView:
        """Return recent log entries."""

    def get_analytics(self) -> AnalyticsPageView:
        """Return analytics snapshot."""

    def get_settings_view(self) -> SettingsPageView:
        """Return redacted settings view."""

    def start(self) -> FacadeActionResult:
        """Start backend session."""

    def stop(self) -> FacadeActionResult:
        """Stop backend session."""

    def refresh_snapshots(self) -> FacadeActionResult:
        """Refresh cached snapshots."""

    @property
    def is_connected(self) -> bool:
        """Return whether a live backend session is connected."""


class NullIntegrationFacade:
    """Offline facade returning explicit placeholders without fabricated metrics."""

    def __init__(
        self,
        *,
        index_symbols: tuple[str, ...] = DEFAULT_INDEX_SYMBOLS,
    ) -> None:
        """Initialize null facade with placeholder index symbols.

        Args:
            index_symbols: Symbols displayed on the home index strip.
        """
        self._index_symbols = index_symbols
        self._lock = threading.RLock()
        self._home = HomePageView(
            indices=default_index_quotes(index_symbols),
            kpis=HomeKpiView(),
            cycle_summary=None,
        )

    @property
    def is_connected(self) -> bool:
        """Return ``False`` because no backend session is attached."""
        return False

    def get_health(self) -> SystemStatusView:
        """Return disconnected system health."""
        with self._lock:
            return SystemStatusView(status="DISCONNECTED", message="Backend unavailable")

    def get_runtime_state(self) -> RuntimeStateView:
        """Return offline runtime state."""
        with self._lock:
            return RuntimeStateView(
                broker_status="DISCONNECTED",
                execution_mode="ANALYSIS",
                market_status="UNKNOWN",
                connected=False,
            )

    def get_home_snapshot(self) -> HomePageView:
        """Return cached home placeholder snapshot."""
        with self._lock:
            return self._home

    def get_home_market_indices(self) -> object:
        """Return offline Home market index placeholders.

        Returns:
            Object with ``indices`` matching facade home-market shape for the
            Home page strip.
        """
        from dashboard.dashboard_facade import empty_home_market_indices

        with self._lock:
            return empty_home_market_indices(
                facade_connected=False,
                connection_status="OFFLINE",
            )

    def get_market_snapshot(self) -> MarketPageView:
        """Return offline Market page placeholders including index cards."""
        from dashboard.dashboard_facade import (
            empty_home_market_indices,
            empty_market_snapshot,
            home_indices_to_quote_views,
            market_snapshot_to_page_view,
        )
        from dashboard.view_models import PLACEHOLDER

        with self._lock:
            return market_snapshot_to_page_view(
                empty_market_snapshot(),
                indices=home_indices_to_quote_views(
                    empty_home_market_indices(
                        facade_connected=False,
                        connection_status="OFFLINE",
                    )
                ),
                market_regime=PLACEHOLDER,
                connected=False,
            )

    def get_strategy_monitor(self) -> StrategyMonitorView:
        """Return offline Strategy Monitor placeholders for the four families."""
        from dashboard.dashboard_facade import (
            empty_strategy_status,
            strategy_status_to_monitor_view,
        )

        with self._lock:
            return strategy_status_to_monitor_view(
                empty_strategy_status(source="offline")
            )

    def get_paper_trading(self) -> PaperTradingPageView:
        """Return offline Paper Trading ledger placeholders."""
        from dashboard.dashboard_facade import (
            empty_paper_trading_ledger,
            paper_ledger_to_page_view,
        )

        with self._lock:
            return paper_ledger_to_page_view(
                empty_paper_trading_ledger(source="offline")
            )

    def get_paper_trading_ledger(self) -> object:
        """Return offline Paper Trading ledger DTO."""
        from dashboard.dashboard_facade import empty_paper_trading_ledger

        with self._lock:
            return empty_paper_trading_ledger(source="offline")

    def get_orders(self) -> OrdersPageView:
        """Return empty orders snapshot."""
        with self._lock:
            return OrdersPageView()

    def get_portfolio(self) -> PortfolioPageView:
        """Return empty portfolio snapshot."""
        with self._lock:
            return PortfolioPageView()

    def get_risk(self) -> RiskPageView:
        """Return empty risk snapshot."""
        with self._lock:
            return RiskPageView()

    def get_apme(self) -> ApmePageView:
        """Return empty APME snapshot."""
        with self._lock:
            return ApmePageView()

    def get_logs(self, *, limit: int = 200) -> LogsPageView:
        """Return empty logs snapshot."""
        del limit
        with self._lock:
            return LogsPageView()

    def get_analytics(self) -> AnalyticsPageView:
        """Return unavailable analytics snapshot."""
        with self._lock:
            return AnalyticsPageView(available=False)

    def get_settings_view(self) -> SettingsPageView:
        """Return redacted offline settings view."""
        with self._lock:
            return SettingsPageView(
                config_entries={"mode": "offline", "backend": "disconnected"},
                ui_preferences={"theme": "dark"},
            )

    def start(self) -> FacadeActionResult:
        """Reject start because backend is unavailable."""
        with self._lock:
            return FacadeActionResult(
                success=False,
                message="Backend session not connected",
                code="DASH.FACADE.UNAVAILABLE",
            )

    def stop(self) -> FacadeActionResult:
        """Reject stop because backend is unavailable."""
        with self._lock:
            return FacadeActionResult(
                success=False,
                message="Backend session not connected",
                code="DASH.FACADE.UNAVAILABLE",
            )

    def refresh_snapshots(self) -> FacadeActionResult:
        """Refresh placeholder snapshots without side effects."""
        with self._lock:
            self._home = HomePageView(
                indices=default_index_quotes(self._index_symbols),
                kpis=HomeKpiView(),
                cycle_summary=None,
            )
            return FacadeActionResult(
                success=True,
                message="Placeholder snapshots refreshed",
            )
