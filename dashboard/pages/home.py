"""Home trading terminal page."""

from __future__ import annotations

import logging

import streamlit as st

from dashboard.components.error_banner import render_error
from dashboard.components.index_ticker import render_index_strip
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.lightweight_chart import render_lightweight_chart
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import (
    FacadeHomeMarketIndices,
    HomeIndexQuote,
    home_indices_to_quote_views,
)
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import (
    DashboardRenderContext,
    HomeKpiView,
    IndexQuoteView,
    MarketChartView,
    PLACEHOLDER,
    default_index_quotes,
    home_kpi_cards,
)

_DEFAULT_CHART_UNDERLYING = "NIFTY"

_logger = logging.getLogger("dashboard.pages.home")


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


def _resolve_home_indices(ctx: DashboardRenderContext) -> tuple[IndexQuoteView, ...]:
    """Load Home index quotes exclusively via DashboardFacade methods.

    Args:
        ctx: Render context with facade.

    Returns:
        Four index quote views (placeholders on failure).
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


def _render_home_market_strip(ctx: DashboardRenderContext) -> None:
    """Render the Home market index strip (re-read on every call)."""
    indices = _resolve_home_indices(ctx)
    render_index_strip(indices)


def _enable_home_market_autorefresh(ctx: DashboardRenderContext) -> None:
    """Live-refresh the Home market strip in place — never the whole page."""
    if not ctx.config.enable_home_market_autorefresh:
        _render_home_market_strip(ctx)
        return
    live_fragment(
        lambda: _render_home_market_strip(ctx),
        interval_seconds=ctx.config.home_market_refresh_seconds,
        key="home_market_refresh",
    )


def _render_ai_panel(ctx: DashboardRenderContext) -> None:
    """Render the AI reasoning panel from already-computed decision output."""
    getter = getattr(ctx.facade, "get_ai_panel", None)
    panel = getter() if callable(getter) else None
    st.markdown("**AI Reasoning**")
    if panel is None:
        st.info("Awaiting backend AI decision loop")
        return

    countdown = getattr(panel, "next_evaluation_display", PLACEHOLDER)
    decision_status = getattr(panel, "decision_status", PLACEHOLDER)
    risk_verdict = getattr(panel, "risk_verdict", PLACEHOLDER)
    st.markdown(
        (
            f"<div class='theta-status-row'>"
            f"<span class='theta-status-label'>Next evaluation in</span>"
            f"<span class='theta-countdown'>{countdown}</span>"
            f"</div>"
        ),
        unsafe_allow_html=True,
    )
    st.caption(f"Decision: {decision_status} · Risk: {risk_verdict}")

    reasons = getattr(panel, "reasons", ())
    if reasons:
        items = "".join(f"<li>{reason}</li>" for reason in reasons)
        st.markdown(f"<ul class='theta-reasoning-list'>{items}</ul>", unsafe_allow_html=True)
    else:
        st.caption("No reasoning recorded for the latest cycle")


def _resolve_primary_chart(ctx: DashboardRenderContext) -> MarketChartView:
    """Load the real candlestick/EMA/VWAP series for the AI's configured underlying.

    Uses the AI panel's own real ``underlying`` field when a decision has
    run; otherwise falls back to the platform default. Optional capability
    — present only when the facade exposes ``get_market_chart``.
    """
    underlying = _DEFAULT_CHART_UNDERLYING
    panel_getter = getattr(ctx.facade, "get_ai_panel", None)
    panel = panel_getter() if callable(panel_getter) else None
    panel_underlying = getattr(panel, "underlying", None) if panel is not None else None
    if panel_underlying and panel_underlying != PLACEHOLDER:
        underlying = panel_underlying

    chart_getter = getattr(ctx.facade, "get_market_chart", None)
    if not callable(chart_getter):
        return MarketChartView(underlying=underlying, source="offline")
    try:
        chart = chart_getter(underlying)
        if isinstance(chart, MarketChartView):
            return chart
    except Exception as exc:  # noqa: BLE001 - Home must not crash
        _logger.warning("home chart unavailable: %s", exc)
    return MarketChartView(underlying=underlying, source="offline")


def _render_chart(ctx: DashboardRenderContext) -> None:
    """Render the primary underlying's real candlestick chart."""
    chart = _resolve_primary_chart(ctx)
    render_lightweight_chart(chart)


def _enable_chart_autorefresh(ctx: DashboardRenderContext) -> None:
    """Live-refresh the primary chart in place — never the whole page."""
    if not ctx.config.enable_autorefresh:
        _render_chart(ctx)
        return
    live_fragment(
        lambda: _render_chart(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="home_chart_refresh",
    )


def _enable_ai_panel_autorefresh(ctx: DashboardRenderContext) -> None:
    """Live-refresh the AI reasoning panel in place — never the whole page."""
    if not ctx.config.enable_autorefresh:
        _render_ai_panel(ctx)
        return
    live_fragment(
        lambda: _render_ai_panel(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="ai_panel_refresh",
    )


def render(ctx: DashboardRenderContext) -> None:
    """Render the home trading terminal page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Trading Terminal", "Primary operator console")
    _enable_home_market_autorefresh(ctx)

    cycle_summary: str | None = None
    try:
        snapshot = ctx.facade.get_home_snapshot()
        render_kpi_row(home_kpi_cards(snapshot.kpis))
        cycle_summary = snapshot.cycle_summary
    except Exception as exc:  # noqa: BLE001
        _logger.warning("home snapshot unavailable: %s", exc)
        render_kpi_row(home_kpi_cards(HomeKpiView()))

    _enable_chart_autorefresh(ctx)
    if cycle_summary:
        st.caption(cycle_summary)
    else:
        st.caption("Awaiting backend cycle summary")

    _enable_ai_panel_autorefresh(ctx)
