"""Exposure Analysis page — real gross/net/long/short exposure (read-only)."""

from __future__ import annotations

import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.components.error_banner import render_error
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext, KpiCardModel, PortfolioPageView

_logger = logging.getLogger("dashboard.pages.exposure")


def _allocation_figure(series: tuple[tuple[str, float], ...], *, title: str) -> go.Figure:
    figure = go.Figure()
    if series:
        labels = [label for label, _value in series]
        values = [value for _label, value in series]
        figure.add_trace(go.Pie(labels=labels, values=values, hole=0.45))
    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121821",
        plot_bgcolor="#121821",
        margin={"l": 24, "r": 24, "t": 36, "b": 24},
        title=title,
        height=320,
    )
    return figure


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Exposure Analysis page body (re-invoked on every live refresh)."""
    try:
        view = ctx.facade.get_portfolio()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("exposure unavailable: %s", exc)
        render_error(f"Exposure data unavailable: {exc}")
        view = PortfolioPageView()

    render_kpi_row(
        (
            KpiCardModel("Total Exposure", view.exposure),
            KpiCardModel("Long Exposure", view.long_exposure),
            KpiCardModel("Short Exposure", view.short_exposure),
            KpiCardModel("Utilization", view.utilization),
        )
    )

    col_sector, col_instrument = st.columns(2)
    with col_sector:
        st.plotly_chart(
            _allocation_figure(view.allocation_by_sector, title="Exposure by Sector"),
            use_container_width=True,
        )
    with col_instrument:
        st.plotly_chart(
            _allocation_figure(view.allocation_by_instrument, title="Exposure by Instrument"),
            use_container_width=True,
        )

    st.markdown("**Per-Position Exposure**")
    if not view.positions:
        st.caption("No open positions")
        return
    frame = pd.DataFrame(
        [
            (
                pos.symbol, pos.product, pos.quantity, pos.exposure,
                pos.market_value, pos.weight_pct, pos.pnl,
            )
            for pos in view.positions
        ],
        columns=["Symbol", "Product", "Qty", "Exposure", "Market Value", "Weight %", "P&L"],
    )
    st.dataframe(frame, use_container_width=True, hide_index=True)


def render(ctx: DashboardRenderContext) -> None:
    """Render the Exposure Analysis page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Exposure Analysis", "Real gross/net exposure breakdown (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="exposure_refresh",
    )
