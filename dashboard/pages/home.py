"""Dashboard Overview (Home) page — institutional trading-terminal landing page."""

from __future__ import annotations

import logging

import streamlit as st

from dashboard.components.error_banner import render_error
from dashboard.components.lightweight_chart import render_lightweight_chart
from dashboard.components.overview_widgets import (
    render_alerts,
    render_engine_status,
    render_footer_bar,
    render_market_breadth_placeholder,
    render_metric_cards,
    render_oi_buildup,
    render_strategy_scanner,
)
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import FacadeHomeMarketIndices, HomeIndexQuote, home_indices_to_quote_views
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import (
    DashboardOverviewView,
    DashboardRenderContext,
    IndexQuoteView,
    MarketChartView,
    PLACEHOLDER,
    default_index_quotes,
)

_logger = logging.getLogger("dashboard.pages.home")

_CHART_UNDERLYINGS: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")
_TIMEFRAMES: tuple[str, ...] = ("1D", "5D", "1M", "3M", "6M", "1Y")


def _payload_to_views(payload: object) -> tuple[IndexQuoteView, ...] | None:
    """Convert a facade home-market payload to presentation views."""
    if isinstance(payload, FacadeHomeMarketIndices):
        return home_indices_to_quote_views(payload)
    indices = getattr(payload, "indices", None)
    if indices is None:
        return None
    quotes: list[IndexQuoteView] = []
    for item in indices:
        if isinstance(item, HomeIndexQuote):
            compact = PLACEHOLDER
            if item.change_abs != PLACEHOLDER and item.change_pct != PLACEHOLDER:
                compact = f"{item.change_abs} ({item.change_pct})"
            quotes.append(
                IndexQuoteView(
                    symbol=item.symbol,
                    value=item.ltp,
                    change=compact,
                    change_abs=item.change_abs,
                    change_pct=item.change_pct,
                    last_update=item.last_update,
                    status=item.connection_status,
                    connection_status=item.connection_status,
                )
            )
        elif isinstance(item, IndexQuoteView):
            quotes.append(item)
    return tuple(quotes) if quotes else None


def resolve_home_indices(ctx: DashboardRenderContext) -> tuple[IndexQuoteView, ...]:
    """Load Home index quotes exclusively via DashboardFacade methods.

    Shared with the top bar's live ticker so both read the same real,
    already-cached snapshot.

    Args:
        ctx: Render context with facade.

    Returns:
        Real index quote views (placeholders on failure).
    """
    facade = ctx.facade
    try:
        getter = getattr(facade, "get_home_market_indices", None)
        if callable(getter):
            views = _payload_to_views(getter())
            if views:
                return views
        snapshot = facade.get_home_snapshot()
        if snapshot.indices:
            return snapshot.indices
    except Exception as exc:  # noqa: BLE001 - Home must not crash
        _logger.warning("home market indices unavailable: %s", exc)
        render_error(f"Home market data unavailable: {exc}")
    return default_index_quotes(ctx.config.index_symbols)


def _selected_underlying() -> str:
    """Return the real, session-persisted underlying selection."""
    return st.session_state.get("home_underlying", "NIFTY")


def _render_header_controls(ctx: DashboardRenderContext) -> str:
    """Render the underlying selector + date, return the selected underlying."""
    _left, right = st.columns([3, 1])
    with right:
        underlying = st.selectbox(
            "Underlying",
            options=_CHART_UNDERLYINGS,
            index=_CHART_UNDERLYINGS.index(_selected_underlying())
            if _selected_underlying() in _CHART_UNDERLYINGS
            else 0,
            key="home_underlying",
            label_visibility="collapsed",
        )
        st.caption(ctx.clock().strftime("%d %b %Y"))
    return underlying


def _resolve_overview(ctx: DashboardRenderContext, underlying: str) -> DashboardOverviewView:
    """Load the composed Dashboard Overview snapshot for ``underlying``."""
    getter = getattr(ctx.facade, "get_dashboard_overview", None)
    if not callable(getter):
        return DashboardOverviewView(selected_underlying=underlying, source="offline")
    try:
        result = getter(underlying)
        if isinstance(result, DashboardOverviewView):
            return result
    except Exception as exc:  # noqa: BLE001 - Home must not crash
        _logger.warning("dashboard overview unavailable: %s", exc)
        render_error(f"Dashboard overview unavailable: {exc}")
    return DashboardOverviewView(selected_underlying=underlying, source="offline")


def _resolve_trend_chart(
    ctx: DashboardRenderContext, underlying: str, timeframe: str
) -> MarketChartView:
    """Load the real candlestick/EMA(20)/EMA(50) series for the trend panel."""
    getter = getattr(ctx.facade, "get_market_chart", None)
    if not callable(getter):
        return MarketChartView(underlying=underlying, source="offline")
    try:
        chart = getter(underlying, fast_span=20, slow_span=50, timeframe=timeframe)
        if isinstance(chart, MarketChartView):
            return chart
    except Exception as exc:  # noqa: BLE001 - Home must not crash
        _logger.warning("home trend chart unavailable: %s", exc)
    return MarketChartView(underlying=underlying, source="offline")


def _render_trend_panel(ctx: DashboardRenderContext, underlying: str) -> None:
    """Render the Market Trend panel with real timeframe tabs."""
    st.markdown(
        f"<div class='theta-panel-title'>Market Trend ({underlying})</div>",
        unsafe_allow_html=True,
    )
    timeframe = st.segmented_control(
        "Timeframe",
        options=_TIMEFRAMES,
        default="1D",
        key="home_chart_timeframe",
        label_visibility="collapsed",
    ) or "1D"
    chart = _resolve_trend_chart(ctx, underlying, timeframe)
    if chart.candles:
        _t, o, h, l, c, _v = chart.candles[-1]
        st.caption(f"O {o:,.2f}  H {h:,.2f}  L {l:,.2f}  C {c:,.2f}")
    render_lightweight_chart(chart, height=420)


def _render_metrics_row(ctx: DashboardRenderContext) -> None:
    """Render the 5-card metric row (re-invoked independently on refresh)."""
    overview = _resolve_overview(ctx, _selected_underlying())
    render_metric_cards(
        (
            overview.market_regime,
            overview.india_vix,
            overview.put_call_ratio,
            overview.max_pain,
            overview.fii_dii,
        )
    )


def _render_trend_breadth_engine_row(ctx: DashboardRenderContext) -> None:
    """Render the chart/breadth/engine row (re-invoked independently on refresh)."""
    underlying = _selected_underlying()
    overview = _resolve_overview(ctx, underlying)

    col_chart, col_breadth, col_engine = st.columns([2, 1, 1])
    with col_chart, st.container(border=True, key="theta_panel_trend"):
        _render_trend_panel(ctx, underlying)
    with col_breadth, st.container(border=True, key="theta_panel_breadth"):
        if overview.breadth_available:
            st.metric("Advancing", overview.breadth_advancing)
            st.metric("Declining", overview.breadth_declining)
            st.metric("Unchanged", overview.breadth_unchanged)
        else:
            render_market_breadth_placeholder()
    with col_engine, st.container(border=True, key="theta_panel_engine"):
        render_engine_status(overview.engines, overall_health=overview.engines_overall_health)


def _render_summary_row(ctx: DashboardRenderContext) -> None:
    """Render the option-summary/scanner/alerts row + footer (independent refresh)."""
    overview = _resolve_overview(ctx, _selected_underlying())

    col_oi, col_scanner, col_alerts = st.columns(3)
    with col_oi, st.container(border=True, key="theta_panel_oi"):
        render_oi_buildup(overview.oi_buildup_calls, overview.oi_buildup_puts)
    with col_scanner, st.container(border=True, key="theta_panel_scanner"):
        render_strategy_scanner(overview.scanner_rows)
    with col_alerts, st.container(border=True, key="theta_panel_alerts"):
        render_alerts(overview.alerts)

    render_footer_bar(
        broker_connected=overview.broker_connected,
        as_of=overview.as_of,
        system_operational=overview.system_operational,
        version=ctx.version,
    )


def render(ctx: DashboardRenderContext) -> None:
    """Render the Dashboard Overview (Home) page.

    Each row is its own independent live fragment so a refresh tick only
    redraws the widgets whose data actually changed, instead of rerunning
    the whole page (the underlying selector above them is interactive and
    intentionally stays outside any auto-refreshing fragment).

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Dashboard Overview", "Real-time intelligence from THETA AI engines")
    _render_header_controls(ctx)

    if not ctx.config.enable_autorefresh:
        _render_metrics_row(ctx)
        _render_trend_breadth_engine_row(ctx)
        _render_summary_row(ctx)
        return

    live_fragment(
        lambda: _render_metrics_row(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="dashboard_metrics_refresh",
    )
    live_fragment(
        lambda: _render_trend_breadth_engine_row(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="dashboard_trend_refresh",
    )
    live_fragment(
        lambda: _render_summary_row(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="dashboard_summary_refresh",
    )
