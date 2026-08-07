"""Risk page."""

from __future__ import annotations

import streamlit as st

from dashboard.components.page_header import render_page_header
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the risk page body (re-invoked on every live refresh tick)."""
    snapshot = ctx.facade.get_risk()

    st.markdown(
        (
            f"<div class='theta-kpi-card'>"
            f"<div class='theta-kpi-label'>Last Verdict</div>"
            f"<div class='theta-kpi-value'>{snapshot.verdict}</div>"
            f"</div>"
        ),
        unsafe_allow_html=True,
    )

    st.subheader("Reason Codes")
    if snapshot.reason_codes:
        for code in snapshot.reason_codes:
            st.write(f"- {code}")
    else:
        st.info("Awaiting backend risk verdict")

    st.subheader("Limits")
    if snapshot.limits:
        for key, value in snapshot.limits.items():
            st.text(f"{key}: {value}")
    else:
        st.info("Redacted risk limits unavailable")

    st.caption("Risk override controls are not available in dashboard v1")


def render(ctx: DashboardRenderContext) -> None:
    """Render the risk page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Risk", "Last verdict and configured limits")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="risk_refresh",
    )
