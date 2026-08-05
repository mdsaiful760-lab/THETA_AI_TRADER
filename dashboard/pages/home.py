"""Home trading terminal page."""

from __future__ import annotations

import logging
from datetime import timedelta

import streamlit as st

from dashboard.components.chart_placeholder import render_tradingview_placeholder
from dashboard.components.error_banner import render_error
from dashboard.components.index_ticker import render_index_strip
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import (
    FacadeHomeMarketIndices,
    HomeIndexQuote,
    home_indices_to_quote_views,
)
from dashboard.utils.polling import home_market_refresh_interval_ms
from dashboard.view_models import (
    DashboardRenderContext,
    HomeKpiView,
    IndexQuoteView,
    PLACEHOLDER,
    default_index_quotes,
    home_kpi_cards,
)

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
    """Enable one-second Home market strip autorefresh without trading cycles.

    Prefers ``streamlit-autorefresh`` for a full-script rerun. Falls back to a
    Streamlit ``fragment(run_every=...)`` that re-renders only the index strip.
    """
    if not ctx.config.enable_home_market_autorefresh:
        _render_home_market_strip(ctx)
        return

    interval_ms = home_market_refresh_interval_ms(ctx.config)
    try:
        from streamlit_autorefresh import st_autorefresh

        st_autorefresh(interval=interval_ms, key="home_market_refresh")
        _render_home_market_strip(ctx)
        return
    except Exception:  # noqa: BLE001 - optional dependency
        pass

    fragment = getattr(st, "fragment", None)
    if fragment is not None:
        seconds = float(ctx.config.home_market_refresh_seconds)

        @fragment(run_every=timedelta(seconds=seconds))
        def _home_market_fragment() -> None:
            """Re-render the index strip on the Home refresh interval."""
            _render_home_market_strip(ctx)

        _home_market_fragment()
        return

    _logger.warning(
        "Home market autorefresh unavailable; install streamlit-autorefresh "
        "or use Streamlit >= 1.33 for fragment refresh"
    )
    _render_home_market_strip(ctx)
    st.caption(
        f"Home market refresh interval: {ctx.config.home_market_refresh_seconds:.1f}s "
        "(install streamlit-autorefresh for automatic refresh)"
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

    render_tradingview_placeholder()
    if cycle_summary:
        st.caption(cycle_summary)
    else:
        st.caption("Awaiting backend cycle summary")
