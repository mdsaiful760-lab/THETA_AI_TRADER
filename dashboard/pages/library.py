"""Strategy Library page — the real catalog of monitored strategy families."""

from __future__ import annotations

import logging

import streamlit as st

from dashboard.components.error_banner import render_error
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import STRATEGY_MONITOR_FAMILIES
from dashboard.view_models import DashboardRenderContext, StrategyMonitorView

_logger = logging.getLogger("dashboard.pages.library")


def render(ctx: DashboardRenderContext) -> None:
    """Render the Strategy Library page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Strategy Library", "Real catalog of monitored strategy families")

    try:
        monitor = ctx.facade.get_strategy_monitor()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("strategy library unavailable: %s", exc)
        render_error(f"Strategy library unavailable: {exc}")
        monitor = StrategyMonitorView()

    by_family = {row.family: row for row in monitor.strategies}

    for family_id, display_name in STRATEGY_MONITOR_FAMILIES:
        row = by_family.get(family_id)
        with st.container():
            st.markdown(f"### {display_name}")
            if row is None:
                st.caption("Not currently evaluated")
                continue
            cols = st.columns(4)
            cols[0].metric("Status", row.status)
            cols[1].metric("Confidence", row.confidence)
            cols[2].metric("Score", row.score)
            cols[3].metric("Eligibility", row.eligibility)
            if row.legs:
                st.caption("Recommended legs")
                for leg in row.legs:
                    st.markdown(
                        f"- {leg.side} {leg.option_type} {leg.strike} "
                        f"× {leg.quantity} ({leg.symbol}, Δ {leg.delta})"
                    )
            st.divider()
