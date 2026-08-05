"""Analytics page."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.components.page_header import render_page_header
from dashboard.components.plotly_charts import build_drawdown, build_equity_curve
from dashboard.view_models import DashboardRenderContext


def render(ctx: DashboardRenderContext) -> None:
    """Render the analytics page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Analytics", "Performance aggregates when available")
    snapshot = ctx.facade.get_analytics()

    if not snapshot.available:
        st.info("Analytics service not wired — awaiting backend aggregates")
        return

    col1, col2 = st.columns(2)
    col1.metric("Win Rate", snapshot.win_rate)
    col2.metric("Expectancy", snapshot.expectancy)

    if snapshot.performance_series:
        perf_df = pd.DataFrame(snapshot.performance_series, columns=["timestamp", "equity"])
        st.plotly_chart(build_equity_curve(perf_df), use_container_width=True)
    else:
        st.info("Performance series unavailable")

    if snapshot.regime_histogram:
        regime_df = pd.DataFrame(snapshot.regime_histogram, columns=["regime", "count"])
        drawdown_df = pd.DataFrame(columns=["timestamp", "drawdown"])
        st.plotly_chart(build_drawdown(drawdown_df), use_container_width=True)
        st.bar_chart(regime_df.set_index("regime"))
    else:
        st.info("Regime histogram unavailable")
