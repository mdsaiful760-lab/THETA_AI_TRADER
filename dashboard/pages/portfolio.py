"""Portfolio page."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.page_header import render_page_header
from dashboard.components.plotly_charts import build_allocation_pie, build_equity_curve
from dashboard.view_models import DashboardRenderContext


def render(ctx: DashboardRenderContext) -> None:
    """Render the portfolio page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Portfolio", "Exposure, utilization, and allocation")
    snapshot = ctx.facade.get_portfolio()

    col1, col2, col3 = st.columns(3)
    col1.metric("Equity", snapshot.equity)
    col2.metric("Exposure", snapshot.exposure)
    col3.metric("Utilization", snapshot.utilization)

    rows = [
        {
            "symbol": row.symbol,
            "quantity": row.quantity,
            "exposure": row.exposure,
            "pnl": row.pnl,
        }
        for row in snapshot.positions
    ]
    positions_df = pd.DataFrame(rows)
    if positions_df.empty:
        positions_df = pd.DataFrame(
            columns=["symbol", "quantity", "exposure", "pnl"]
        )
    st.subheader("Open Positions")
    render_table(positions_df)

    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        if snapshot.equity_series:
            equity_df = pd.DataFrame(snapshot.equity_series, columns=["timestamp", "equity"])
            st.plotly_chart(build_equity_curve(equity_df), use_container_width=True)
        else:
            st.info("Equity series unavailable")
    with chart_col2:
        if snapshot.allocation:
            allocation_df = pd.DataFrame(snapshot.allocation, columns=["label", "weight"])
            st.plotly_chart(build_allocation_pie(allocation_df), use_container_width=True)
        else:
            st.info("Allocation data unavailable")
