"""Immutable presentation view models for the dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Sequence

PLACEHOLDER: str = "—"


@dataclass(frozen=True)
class IndexQuoteView:
    """Single index quote display row for the Home terminal strip.

    Attributes:
        symbol: Index symbol label (e.g. ``NIFTY``).
        value: Last traded / index value (LTP) display string.
        change: Compact change string (abs and/or pct).
        change_abs: Absolute change display string.
        change_pct: Percentage change display string.
        last_update: Last update timestamp display string.
        status: Connection status (legacy alias field).
        connection_status: Connection status (``LIVE`` / ``DELAYED`` / ``OFFLINE`` / ``UNKNOWN``).
    """

    symbol: str
    value: str = PLACEHOLDER
    change: str = PLACEHOLDER
    change_abs: str = PLACEHOLDER
    change_pct: str = PLACEHOLDER
    last_update: str = PLACEHOLDER
    status: str = "UNKNOWN"
    connection_status: str = "UNKNOWN"


@dataclass(frozen=True)
class HomeKpiView:
    """Home page KPI card values."""

    active_strategy: str = PLACEHOLDER
    confidence: str = PLACEHOLDER
    market_regime: str = PLACEHOLDER
    paper_pnl: str = PLACEHOLDER
    open_positions: str = PLACEHOLDER


@dataclass(frozen=True)
class HomePageView:
    """Home trading terminal snapshot."""

    indices: tuple[IndexQuoteView, ...] = ()
    kpis: HomeKpiView = field(default_factory=HomeKpiView)
    cycle_summary: str | None = None


@dataclass(frozen=True)
class KpiCardModel:
    """Single KPI card for rendering."""

    label: str
    value: str
    hint: str | None = None


@dataclass(frozen=True)
class SystemStatusView:
    """System health summary."""

    status: str = "UNKNOWN"
    message: str = PLACEHOLDER


@dataclass(frozen=True)
class RuntimeStateView:
    """Runtime execution state summary."""

    broker_status: str = "N/A"
    execution_mode: str = "ANALYSIS"
    market_status: str = "UNKNOWN"
    connected: bool = False


@dataclass(frozen=True)
class MarketPageView:
    """Market page snapshot for the Market dashboard page.

    Attributes:
        underlyings: Selectable underlying labels.
        selected_underlying: Currently displayed underlying.
        ltp: Last traded / index value for the selected underlying.
        change: Absolute/compact change display.
        volume: Volume display.
        market_regime: Current market regime label.
        connection_status: Aggregate market connection status.
        last_update: Last update timestamp display.
        source: Payload source (``live`` / ``offline`` / ``cached``).
        indices: Live index cards (NIFTY, BANKNIFTY, SENSEX, INDIA VIX).
        option_chain_columns: Option chain column headers.
        option_chain_rows: Option chain display rows.
    """

    underlyings: tuple[str, ...] = ()
    selected_underlying: str = PLACEHOLDER
    ltp: str = PLACEHOLDER
    change: str = PLACEHOLDER
    volume: str = PLACEHOLDER
    market_regime: str = PLACEHOLDER
    connection_status: str = "OFFLINE"
    last_update: str = PLACEHOLDER
    source: str = "offline"
    indices: tuple[IndexQuoteView, ...] = ()
    option_chain_columns: tuple[str, ...] = (
        "strike",
        "type",
        "ltp",
        "oi",
        "iv",
    )
    option_chain_rows: tuple[tuple[str, ...], ...] = ()


def market_page_statistic_cards(view: MarketPageView) -> tuple[KpiCardModel, ...]:
    """Build Market Statistics KPI cards.

    Args:
        view: Market page snapshot.

    Returns:
        Five statistic KPI cards in display order.
    """
    return (
        KpiCardModel("LTP", view.ltp),
        KpiCardModel("Change", view.change),
        KpiCardModel("Volume", view.volume),
        KpiCardModel("Connection", view.connection_status),
        KpiCardModel("Last Update", view.last_update),
    )


def market_regime_cards(view: MarketPageView) -> tuple[KpiCardModel, ...]:
    """Build the Market Regime KPI card row.

    Args:
        view: Market page snapshot.

    Returns:
        Single-item KPI tuple for market regime.
    """
    return (KpiCardModel("Market Regime", view.market_regime),)


@dataclass(frozen=True)
class StrategyRowView:
    """Strategy monitor table row."""

    strategy_id: str
    family: str = PLACEHOLDER
    display_name: str = PLACEHOLDER
    status: str = PLACEHOLDER
    confidence: str = PLACEHOLDER
    last_signal: str = PLACEHOLDER
    timestamp: str = PLACEHOLDER
    reasons: tuple[str, ...] = ()
    score: str = PLACEHOLDER
    eligibility: str = PLACEHOLDER
    reason: str = PLACEHOLDER


@dataclass(frozen=True)
class StrategyMonitorView:
    """Strategy monitor page snapshot."""

    strategies: tuple[StrategyRowView, ...] = ()
    market_regime: str = PLACEHOLDER
    active_strategy: str = PLACEHOLDER
    confidence_score: str = PLACEHOLDER
    evaluation_time: str = PLACEHOLDER
    source: str = "offline"
    as_of: str = PLACEHOLDER


def strategy_monitor_kpi_cards(view: StrategyMonitorView) -> tuple[KpiCardModel, ...]:
    """Build Strategy Monitor header KPI cards.

    Args:
        view: Strategy monitor snapshot.

    Returns:
        Four KPI cards: regime, active strategy, confidence, evaluation time.
    """
    return (
        KpiCardModel("Market Regime", view.market_regime),
        KpiCardModel("Active Strategy", view.active_strategy),
        KpiCardModel("Confidence Score", view.confidence_score),
        KpiCardModel("Strategy Evaluation Time", view.evaluation_time),
    )


@dataclass(frozen=True)
class PaperPositionView:
    """Paper trading position row."""

    symbol: str
    quantity: str = PLACEHOLDER
    avg_price: str = PLACEHOLDER
    mark: str = PLACEHOLDER
    pnl: str = PLACEHOLDER
    strategy: str = PLACEHOLDER
    entry: str = PLACEHOLDER
    current: str = PLACEHOLDER
    mtm: str = PLACEHOLDER
    status: str = PLACEHOLDER


@dataclass(frozen=True)
class PaperTradingPageView:
    """Paper trading page snapshot."""

    virtual_cash: str = PLACEHOLDER
    available_cash: str = PLACEHOLDER
    capital_used: str = PLACEHOLDER
    total_equity: str = PLACEHOLDER
    todays_pnl: str = PLACEHOLDER
    realized_pnl: str = PLACEHOLDER
    unrealized_pnl: str = PLACEHOLDER
    orders_filled: str = "0"
    orders_pending: str = "0"
    orders_cancelled: str = "0"
    orders_rejected: str = "0"
    positions: tuple[PaperPositionView, ...] = ()
    orders: tuple["OrderRowView", ...] = ()
    equity_series: tuple[tuple[str, float], ...] = ()
    source: str = "offline"


def paper_trading_kpi_cards(view: PaperTradingPageView) -> tuple[KpiCardModel, ...]:
    """Build Paper Trading capital/PnL KPI cards.

    Args:
        view: Paper trading page snapshot.

    Returns:
        Six KPI cards in display order.
    """
    cash = (
        view.available_cash
        if view.available_cash != PLACEHOLDER
        else view.virtual_cash
    )
    return (
        KpiCardModel("Available Cash", cash),
        KpiCardModel("Capital Used", view.capital_used),
        KpiCardModel("Total Equity", view.total_equity),
        KpiCardModel("Today's P&L", view.todays_pnl),
        KpiCardModel("Realized P&L", view.realized_pnl),
        KpiCardModel("Unrealized P&L", view.unrealized_pnl),
    )


def paper_order_count_cards(view: PaperTradingPageView) -> tuple[KpiCardModel, ...]:
    """Build Paper Trading order status count cards.

    Args:
        view: Paper trading page snapshot.

    Returns:
        Four order-count KPI cards.
    """
    return (
        KpiCardModel("Filled", view.orders_filled),
        KpiCardModel("Pending", view.orders_pending),
        KpiCardModel("Cancelled", view.orders_cancelled),
        KpiCardModel("Rejected", view.orders_rejected),
    )


@dataclass(frozen=True)
class OrderRowView:
    """Order summary row."""

    order_id: str
    plan_id: str = PLACEHOLDER
    status: str = PLACEHOLDER
    symbol: str = PLACEHOLDER
    side: str = PLACEHOLDER
    quantity: str = PLACEHOLDER
    timestamp: str = PLACEHOLDER


@dataclass(frozen=True)
class OrdersPageView:
    """Orders page snapshot."""

    orders: tuple[OrderRowView, ...] = ()


@dataclass(frozen=True)
class PortfolioPositionView:
    """Portfolio position row."""

    symbol: str
    quantity: str = PLACEHOLDER
    exposure: str = PLACEHOLDER
    pnl: str = PLACEHOLDER


@dataclass(frozen=True)
class PortfolioPageView:
    """Portfolio page snapshot."""

    equity: str = PLACEHOLDER
    exposure: str = PLACEHOLDER
    utilization: str = PLACEHOLDER
    positions: tuple[PortfolioPositionView, ...] = ()
    equity_series: tuple[tuple[str, float], ...] = ()
    allocation: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class RiskPageView:
    """Risk page snapshot."""

    verdict: str = PLACEHOLDER
    reason_codes: tuple[str, ...] = ()
    limits: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class ApmeDecisionView:
    """APME decision summary row."""

    position_id: str
    action: str = PLACEHOLDER
    rationale: str = PLACEHOLDER
    timestamp: str = PLACEHOLDER


@dataclass(frozen=True)
class ApmePageView:
    """APME page snapshot."""

    decisions: tuple[ApmeDecisionView, ...] = ()
    hints: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class LogEntryView:
    """Single log/event line."""

    level: str
    message: str
    timestamp: str = PLACEHOLDER


@dataclass(frozen=True)
class LogsPageView:
    """Logs page snapshot."""

    entries: tuple[LogEntryView, ...] = ()


@dataclass(frozen=True)
class AnalyticsPageView:
    """Analytics page snapshot."""

    win_rate: str = PLACEHOLDER
    expectancy: str = PLACEHOLDER
    regime_histogram: tuple[tuple[str, float], ...] = ()
    performance_series: tuple[tuple[str, float], ...] = ()
    available: bool = False


@dataclass(frozen=True)
class SettingsPageView:
    """Settings page snapshot."""

    config_entries: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    ui_preferences: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class FacadeActionResult:
    """Result of a facade lifecycle action."""

    success: bool
    message: str
    code: str | None = None


@dataclass(frozen=True)
class DashboardSessionView:
    """Immutable snapshot of UI session state."""

    active_page: str
    last_error: str | None
    last_refresh_at: str | None
    facade_action_pending: bool
    ui_prefs: Mapping[str, str]


@dataclass(frozen=True)
class DashboardRenderContext:
    """Render context passed to every page module.

    Attributes:
        config: Versioned dashboard UI configuration.
        facade: Backend facade for read-only snapshots and lifecycle actions.
        session: Current UI session snapshot.
        clock: Injectable clock for deterministic tests.
        version: Dashboard package version string.
    """

    config: "DashboardUiConfig"
    facade: "DashboardBackendFacade"
    session: DashboardSessionView
    clock: "Callable[[], datetime]"
    version: str


def home_kpi_cards(kpis: HomeKpiView) -> tuple[KpiCardModel, ...]:
    """Build KPI card models from a home KPI view.

    Args:
        kpis: Home KPI snapshot values.

    Returns:
        Tuple of five KPI card models in display order.
    """
    return (
        KpiCardModel("Active Strategy", kpis.active_strategy),
        KpiCardModel("Confidence", kpis.confidence),
        KpiCardModel("Market Regime", kpis.market_regime),
        KpiCardModel("Paper PnL", kpis.paper_pnl),
        KpiCardModel("Open Positions", kpis.open_positions),
    )


def default_index_quotes(symbols: Sequence[str]) -> tuple[IndexQuoteView, ...]:
    """Build placeholder index quotes for the given symbols.

    Args:
        symbols: Index symbol labels.

    Returns:
        Tuple of placeholder ``IndexQuoteView`` rows with ``OFFLINE`` status.
    """
    return tuple(
        IndexQuoteView(
            symbol=symbol,
            status="OFFLINE",
            connection_status="OFFLINE",
        )
        for symbol in symbols
    )


# Deferred imports for type checking only.
from datetime import datetime  # noqa: E402
from typing import Callable  # noqa: E402

if False:  # pragma: no cover - typing only
    from dashboard.config import DashboardUiConfig
    from dashboard.facade import DashboardBackendFacade
