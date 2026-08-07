"""APME page."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.page_header import render_page_header
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the APME page body (re-invoked on every live refresh tick)."""
    st.warning(
        "APME decisions are informational; execution remains orchestrator-owned."
    )
    snapshot = ctx.facade.get_apme()

    decision_rows = [
        {
            "position_id": row.position_id,
            "action": row.action,
            "rationale": row.rationale,
            "timestamp": row.timestamp,
        }
        for row in snapshot.decisions
    ]
    decisions_df = pd.DataFrame(decision_rows)
    if decisions_df.empty:
        decisions_df = pd.DataFrame(
            columns=["position_id", "action", "rationale", "timestamp"]
        )
    st.subheader("Latest Decisions")
    render_table(decisions_df)

    hint_rows = [{"position_id": pos, "hint": hint} for pos, hint in snapshot.hints]
    hints_df = pd.DataFrame(hint_rows)
    if hints_df.empty:
        hints_df = pd.DataFrame(columns=["position_id", "hint"])
    st.subheader("Management Hints")
    render_table(hints_df)


def render(ctx: DashboardRenderContext) -> None:
    """Render the APME page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("APME", "Adaptive position management decisions")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="apme_refresh",
    )
