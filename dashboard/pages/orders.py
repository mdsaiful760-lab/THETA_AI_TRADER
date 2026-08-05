"""Orders page."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.page_header import render_page_header
from dashboard.view_models import DashboardRenderContext


def render(ctx: DashboardRenderContext) -> None:
    """Render the orders page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Orders", "Recent order tracker summaries")
    snapshot = ctx.facade.get_orders()

    rows = [
        {
            "order_id": row.order_id,
            "plan_id": row.plan_id,
            "status": row.status,
            "symbol": row.symbol,
            "side": row.side,
            "quantity": row.quantity,
            "timestamp": row.timestamp,
        }
        for row in snapshot.orders
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "order_id",
                "plan_id",
                "status",
                "symbol",
                "side",
                "quantity",
                "timestamp",
            ]
        )

    statuses = sorted({row.status for row in snapshot.orders if row.status})
    plan_ids = sorted({row.plan_id for row in snapshot.orders if row.plan_id})
    col1, col2 = st.columns(2)
    status_filter = col1.selectbox("Status filter", options=["All", *statuses])
    plan_filter = col2.selectbox("Plan filter", options=["All", *plan_ids])

    filtered = df.copy()
    if status_filter != "All" and not filtered.empty:
        filtered = filtered[filtered["status"] == status_filter]
    if plan_filter != "All" and not filtered.empty:
        filtered = filtered[filtered["plan_id"] == plan_filter]

    render_table(filtered)
    st.warning("Order mutation not available in dashboard v1")
