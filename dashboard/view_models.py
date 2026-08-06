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
class StrategyGateView:
    """Single gate evaluation row for Strategy Monitor display."""

    name: str
    outcome: str = PLACEHOLDER
    detail: str = PLACEHOLDER


@dataclass(frozen=True)
class StrategyLegView:
    """Recommended option leg row for Strategy Monitor display."""

    side: str = PLACEHOLDER
    option_type: str = PLACEHOLDER
    strike: str = PLACEHOLDER
    quantity: str = PLACEHOLDER
    symbol: str = PLACEHOLDER
    delta: str = PLACEHOLDER


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
    rank: str = PLACEHOLDER
    recommendation_state: str = PLACEHOLDER
    detail_summary: str = PLACEHOLDER
    gates: tuple[StrategyGateView, ...] = ()
    legs: tuple[StrategyLegView, ...] = ()


@dataclass(frozen=True)
class StrategyMonitorView:
    """Strategy monitor page snapshot."""

    strategies: tuple[StrategyRowView, ...] = ()
    market_regime: str = PLACEHOLDER
    active_strategy: str = PLACEHOLDER
    confidence_score: str = PLACEHOLDER
    evaluation_time: str = PLACEHOLDER
    recommendation_banner: str = PLACEHOLDER
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


def selected_strategy_detail_cards(row: StrategyRowView) -> tuple[KpiCardModel, ...]:
    """Build KPI cards for the selected strategy detail panel.

    Args:
        row: Selected strategy row from the monitor snapshot.

    Returns:
        Detail KPI cards for score, status, eligibility, and confidence.
    """
    return (
        KpiCardModel("Score", row.score),
        KpiCardModel("Status", row.status),
        KpiCardModel("Eligible / Rejected", row.eligibility),
        KpiCardModel("Confidence", row.confidence),
    )


def resolve_selected_strategy(
    view: StrategyMonitorView,
    *,
    selected_display_name: str | None = None,
) -> StrategyRowView | None:
    """Resolve the selected strategy row for detail panels.

    Args:
        view: Strategy monitor snapshot.
        selected_display_name: Optional UI selection override.

    Returns:
        Matching strategy row, or ``None`` when the snapshot has no rows.
    """
    if not view.strategies:
        return None
    if selected_display_name:
        for row in view.strategies:
            if row.display_name == selected_display_name:
                return row
    active = view.active_strategy
    if active and active != PLACEHOLDER:
        for row in view.strategies:
            if row.display_name == active or row.family == active or row.strategy_id == active:
                return row
    for row in view.strategies:
        if row.rank == "1":
            return row
    return view.strategies[0]


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
    total_pnl: str = PLACEHOLDER
    orders_filled: str = "0"
    orders_pending: str = "0"
    orders_cancelled: str = "0"
    orders_rejected: str = "0"
    positions: tuple[PaperPositionView, ...] = ()
    orders: tuple["OrderRowView", ...] = ()
    equity_series: tuple[tuple[str, float], ...] = ()
    drawdown_series: tuple[tuple[str, float], ...] = ()
    exposure: str = PLACEHOLDER
    open_positions_count: str = "0"
    closed_positions_count: str = "0"
    runner_state: str = "UNKNOWN"
    runner_connection_status: str = "UNKNOWN"
    runner_latency: str = PLACEHOLDER
    runner_last_update: str = PLACEHOLDER
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


def paper_trading_position_summary_cards(
    view: PaperTradingPageView,
) -> tuple[KpiCardModel, ...]:
    """Build Paper Trading position summary KPI cards.

    Args:
        view: Paper trading page snapshot.

    Returns:
        Open Positions, Closed Positions, Capital Used, Exposure cards.
    """
    return (
        KpiCardModel("Open Positions", view.open_positions_count),
        KpiCardModel("Closed Positions", view.closed_positions_count),
        KpiCardModel("Capital Used", view.capital_used),
        KpiCardModel("Exposure", view.exposure),
    )


def paper_trading_runner_status_cards(
    view: PaperTradingPageView,
) -> tuple[KpiCardModel, ...]:
    """Build Paper Trading runner status KPI cards.

    Args:
        view: Paper trading page snapshot.

    Returns:
        Runner, Connection, Latency, Last Update cards.
    """
    return (
        KpiCardModel("Runner", view.runner_state),
        KpiCardModel("Connection", view.runner_connection_status),
        KpiCardModel("Latency", view.runner_latency),
        KpiCardModel("Last Update", view.runner_last_update),
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
    strategy: str = PLACEHOLDER
    price: str = PLACEHOLDER


@dataclass(frozen=True)
class OrdersPageView:
    """Orders page snapshot."""

    orders: tuple[OrderRowView, ...] = ()
    total_orders: str = "0"
    orders_pending: str = "0"
    orders_filled: str = "0"
    orders_cancelled: str = "0"
    orders_rejected: str = "0"
    broker_connection_status: str = "DISCONNECTED"
    oms_status: str = "UNKNOWN"
    exchange_status: str = "UNKNOWN"
    last_order_time: str = PLACEHOLDER
    broker_latency: str = PLACEHOLDER
    source: str = "offline"


def orders_summary_kpi_cards(view: OrdersPageView) -> tuple[KpiCardModel, ...]:
    """Build Order Summary KPI cards.

    Args:
        view: Orders page snapshot.

    Returns:
        Total Orders, Pending, Filled, Cancelled, Rejected cards.
    """
    return (
        KpiCardModel("Total Orders", view.total_orders),
        KpiCardModel("Pending", view.orders_pending),
        KpiCardModel("Filled", view.orders_filled),
        KpiCardModel("Cancelled", view.orders_cancelled),
        KpiCardModel("Rejected", view.orders_rejected),
    )


def orders_broker_status_cards(view: OrdersPageView) -> tuple[KpiCardModel, ...]:
    """Build Broker Status KPI cards.

    Args:
        view: Orders page snapshot.

    Returns:
        Broker Connection, OMS Status, Exchange Status, Last Order Time, and
        Latency cards.
    """
    return (
        KpiCardModel("Broker Connection", view.broker_connection_status),
        KpiCardModel("OMS Status", view.oms_status),
        KpiCardModel("Exchange Status", view.exchange_status),
        KpiCardModel("Last Order Time", view.last_order_time),
        KpiCardModel("Latency", view.broker_latency),
    )


def orders_status_distribution(view: OrdersPageView) -> tuple[tuple[str, float], ...]:
    """Build the Order Status Distribution series from already-computed counts.

    Never recounts orders; reuses the same counts shown on the summary KPI row.

    Args:
        view: Orders page snapshot.

    Returns:
        ``(label, count)`` pairs for Pending, Filled, Cancelled, and Rejected.
    """

    def _count(value: str) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    return (
        ("Pending", _count(view.orders_pending)),
        ("Filled", _count(view.orders_filled)),
        ("Cancelled", _count(view.orders_cancelled)),
        ("Rejected", _count(view.orders_rejected)),
    )


@dataclass(frozen=True)
class PortfolioPositionView:
    """Portfolio holding row (Holdings Table)."""

    symbol: str
    quantity: str = PLACEHOLDER
    exposure: str = PLACEHOLDER
    pnl: str = PLACEHOLDER
    product: str = PLACEHOLDER
    avg_price: str = PLACEHOLDER
    current_price: str = PLACEHOLDER
    market_value: str = PLACEHOLDER
    unrealized_pnl: str = PLACEHOLDER
    realized_pnl: str = PLACEHOLDER
    day_change_pct: str = PLACEHOLDER
    weight_pct: str = PLACEHOLDER


@dataclass(frozen=True)
class PortfolioPageView:
    """Portfolio page snapshot."""

    equity: str = PLACEHOLDER
    exposure: str = PLACEHOLDER
    utilization: str = PLACEHOLDER
    positions: tuple[PortfolioPositionView, ...] = ()
    equity_series: tuple[tuple[str, float], ...] = ()
    allocation: tuple[tuple[str, float], ...] = ()
    # Portfolio Summary
    cash_available: str = PLACEHOLDER
    margin_used: str = PLACEHOLDER
    todays_pnl: str = PLACEHOLDER
    total_pnl: str = PLACEHOLDER
    # Allocation breakdowns
    allocation_by_sector: tuple[tuple[str, float], ...] = ()
    allocation_by_instrument: tuple[tuple[str, float], ...] = ()
    allocation_by_product: tuple[tuple[str, float], ...] = ()
    # Exposure
    long_exposure: str = PLACEHOLDER
    short_exposure: str = PLACEHOLDER
    net_exposure: str = PLACEHOLDER
    gross_exposure: str = PLACEHOLDER
    # Portfolio Performance
    daily_pnl_series: tuple[tuple[str, float], ...] = ()
    cumulative_pnl_series: tuple[tuple[str, float], ...] = ()
    # Portfolio Risk Snapshot
    largest_position: str = PLACEHOLDER
    largest_loss: str = PLACEHOLDER
    largest_gain: str = PLACEHOLDER
    portfolio_beta: str = PLACEHOLDER
    diversification_score: str = PLACEHOLDER
    # Position Breakdown
    total_positions_count: str = "0"
    open_positions_count: str = "0"
    closed_positions_count: str = "0"
    long_positions_count: str = "0"
    short_positions_count: str = "0"
    # Portfolio Status
    broker_connection_status: str = "DISCONNECTED"
    portfolio_sync_status: str = "UNKNOWN"
    last_update: str = PLACEHOLDER
    source: str = "offline"


def portfolio_summary_kpi_cards(view: PortfolioPageView) -> tuple[KpiCardModel, ...]:
    """Build Portfolio Summary KPI cards.

    Args:
        view: Portfolio page snapshot.

    Returns:
        Total Portfolio Value, Cash Available, Margin Used, Today's P&L,
        Total P&L cards.
    """
    return (
        KpiCardModel("Total Portfolio Value", view.equity),
        KpiCardModel("Cash Available", view.cash_available),
        KpiCardModel("Margin Used", view.margin_used),
        KpiCardModel("Today's P&L", view.todays_pnl),
        KpiCardModel("Total P&L", view.total_pnl),
    )


def portfolio_exposure_cards(view: PortfolioPageView) -> tuple[KpiCardModel, ...]:
    """Build Exposure KPI cards.

    Args:
        view: Portfolio page snapshot.

    Returns:
        Total, Long, Short, Net, and Gross exposure cards.
    """
    return (
        KpiCardModel("Total Exposure", view.exposure),
        KpiCardModel("Long Exposure", view.long_exposure),
        KpiCardModel("Short Exposure", view.short_exposure),
        KpiCardModel("Net Exposure", view.net_exposure),
        KpiCardModel("Gross Exposure", view.gross_exposure),
    )


def portfolio_risk_snapshot_cards(view: PortfolioPageView) -> tuple[KpiCardModel, ...]:
    """Build Portfolio Risk Snapshot KPI cards.

    Args:
        view: Portfolio page snapshot.

    Returns:
        Largest Position, Largest Loss, Largest Gain, Portfolio Beta, and
        Diversification Score cards.
    """
    return (
        KpiCardModel("Largest Position", view.largest_position),
        KpiCardModel("Largest Loss", view.largest_loss),
        KpiCardModel("Largest Gain", view.largest_gain),
        KpiCardModel("Portfolio Beta", view.portfolio_beta),
        KpiCardModel("Diversification Score", view.diversification_score),
    )


def portfolio_position_breakdown_cards(
    view: PortfolioPageView,
) -> tuple[KpiCardModel, ...]:
    """Build Position Breakdown count cards.

    Args:
        view: Portfolio page snapshot.

    Returns:
        Total, Open, Closed, Long, and Short position count cards.
    """
    return (
        KpiCardModel("Total Positions", view.total_positions_count),
        KpiCardModel("Open Positions", view.open_positions_count),
        KpiCardModel("Closed Positions", view.closed_positions_count),
        KpiCardModel("Long Positions", view.long_positions_count),
        KpiCardModel("Short Positions", view.short_positions_count),
    )


def portfolio_status_cards(view: PortfolioPageView) -> tuple[KpiCardModel, ...]:
    """Build Portfolio Status cards.

    Args:
        view: Portfolio page snapshot.

    Returns:
        Broker Connection, Portfolio Sync, Last Update, and Data Source cards.
    """
    data_source = view.source.upper() if view.source else PLACEHOLDER
    return (
        KpiCardModel("Broker Connection", view.broker_connection_status),
        KpiCardModel("Portfolio Sync", view.portfolio_sync_status),
        KpiCardModel("Last Update", view.last_update),
        KpiCardModel("Data Source", data_source),
    )


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
    profit_factor: str = PLACEHOLDER
    average_winner: str = PLACEHOLDER
    average_loser: str = PLACEHOLDER
    largest_win: str = PLACEHOLDER
    largest_loss: str = PLACEHOLDER
    risk_reward: str = PLACEHOLDER
    sharpe: str = PLACEHOLDER
    max_drawdown: str = PLACEHOLDER
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
