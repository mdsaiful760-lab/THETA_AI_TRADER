"""Dashboard page registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from dashboard.view_models import DashboardRenderContext

from . import analytics as analytics_page
from . import apme as apme_page
from . import home as home_page
from . import logs as logs_page
from . import market as market_page
from . import orders as orders_page
from . import paper_trading as paper_trading_page
from . import portfolio as portfolio_page
from . import risk as risk_page
from . import settings as settings_page
from . import strategy_monitor as strategy_monitor_page

RenderFn = Callable[[DashboardRenderContext], None]


@dataclass(frozen=True)
class DashboardPage:
    """Registered dashboard page metadata."""

    page_id: str
    label: str
    render: RenderFn


PAGE_REGISTRY: dict[str, DashboardPage] = {
    "home": DashboardPage("home", "Home", home_page.render),
    "market": DashboardPage("market", "Market", market_page.render),
    "strategy_monitor": DashboardPage(
        "strategy_monitor",
        "Strategy Monitor",
        strategy_monitor_page.render,
    ),
    "paper_trading": DashboardPage(
        "paper_trading",
        "Paper Trading",
        paper_trading_page.render,
    ),
    "orders": DashboardPage("orders", "Orders", orders_page.render),
    "portfolio": DashboardPage("portfolio", "Portfolio", portfolio_page.render),
    "risk": DashboardPage("risk", "Risk", risk_page.render),
    "apme": DashboardPage("apme", "APME", apme_page.render),
    "logs": DashboardPage("logs", "Logs", logs_page.render),
    "analytics": DashboardPage("analytics", "Analytics", analytics_page.render),
    "settings": DashboardPage("settings", "Settings", settings_page.render),
}

__all__ = ["DashboardPage", "PAGE_REGISTRY"]
