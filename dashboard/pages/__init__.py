"""Dashboard page registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from dashboard.view_models import DashboardRenderContext

from . import backtesting as backtesting_page
from . import builder as builder_page
from . import engine_status as engine_status_page
from . import exposure as exposure_page
from . import greeks as greeks_page
from . import heatmap as heatmap_page
from . import home as home_page
from . import liquidity as liquidity_page
from . import library as library_page
from . import logs as logs_page
from . import market_regime as market_regime_page
from . import option_chain as option_chain_page
from . import orders as orders_page
from . import paper_trading as paper_trading_page
from . import portfolio as portfolio_page
from . import position_sizing as position_sizing_page
from . import positions as positions_page
from . import risk as risk_page
from . import settings as settings_page
from . import strategy_monitor as strategy_monitor_page
from . import trade_log as trade_log_page
from . import volatility as volatility_page

RenderFn = Callable[[DashboardRenderContext], None]


@dataclass(frozen=True)
class DashboardPage:
    """Registered dashboard page metadata."""

    page_id: str
    label: str
    render: RenderFn


# Order matches dashboard.components.sidebar.NAV_GROUPS exactly — the
# TestPageRegistry.test_sidebar_page_ids_match_registry invariant depends
# on this ordering, not just set equality.
PAGE_REGISTRY: dict[str, DashboardPage] = {
    "home": DashboardPage("home", "Dashboard", home_page.render),
    "market_regime": DashboardPage("market_regime", "Market Regime", market_regime_page.render),
    "greeks": DashboardPage("greeks", "Greeks Intelligence", greeks_page.render),
    "liquidity": DashboardPage("liquidity", "Liquidity Analysis", liquidity_page.render),
    "volatility": DashboardPage("volatility", "Volatility Surface", volatility_page.render),
    "option_chain": DashboardPage("option_chain", "Option Chain", option_chain_page.render),
    "heatmap": DashboardPage("heatmap", "Market Heatmap", heatmap_page.render),
    "scanner": DashboardPage("scanner", "Strategy Scanner", strategy_monitor_page.render),
    "builder": DashboardPage("builder", "Strategy Builder", builder_page.render),
    "backtesting": DashboardPage("backtesting", "Backtesting", backtesting_page.render),
    "library": DashboardPage("library", "Strategy Library", library_page.render),
    "risk_dashboard": DashboardPage("risk_dashboard", "Risk Dashboard", risk_page.render),
    "position_sizing": DashboardPage(
        "position_sizing", "Position Sizing", position_sizing_page.render
    ),
    "portfolio": DashboardPage("portfolio", "Portfolio Overview", portfolio_page.render),
    "exposure": DashboardPage("exposure", "Exposure Analysis", exposure_page.render),
    "trade_execution": DashboardPage(
        "trade_execution", "Trade Execution", paper_trading_page.render
    ),
    "orders": DashboardPage("orders", "Orders", orders_page.render),
    "positions": DashboardPage("positions", "Positions", positions_page.render),
    "trade_log": DashboardPage("trade_log", "Trade Log", trade_log_page.render),
    "engine_status": DashboardPage("engine_status", "Engine Status", engine_status_page.render),
    "logs": DashboardPage("logs", "Logs", logs_page.render),
    "settings": DashboardPage("settings", "Settings", settings_page.render),
}

__all__ = ["DashboardPage", "PAGE_REGISTRY"]
