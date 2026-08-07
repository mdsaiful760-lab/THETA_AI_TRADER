"""Positions page — real live paper positions with MTM and P&L (read-only)."""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext, KpiCardModel, PaperTradingPageView

_logger = logging.getLogger("dashboard.pages.positions")


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Positions page body (re-invoked on every live refresh)."""
    try:
        view = ctx.facade.get_paper_trading()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("positions unavailable: %s", exc)
        render_error(f"Positions unavailable: {exc}")
        view = PaperTradingPageView()

    open_positions = [pos for pos in view.positions if pos.status.upper() == "OPEN"]
    render_kpi_row(
        (
            KpiCardModel("Open Positions", str(len(open_positions))),
            KpiCardModel("Total Positions", str(len(view.positions))),
            KpiCardModel("Unrealized P&L", view.unrealized_pnl),
            KpiCardModel("Realized P&L", view.realized_pnl),
        )
    )

    st.markdown("**Live Positions**")
    if not view.positions:
        st.caption("No paper positions")
        return
    frame = pd.DataFrame(
        [
            (
                pos.symbol, pos.strategy, pos.quantity, pos.entry,
                pos.current, pos.mtm, pos.status,
            )
            for pos in view.positions
        ],
        columns=["Symbol", "Strategy", "Qty", "Entry", "Current", "MTM", "Status"],
    )
    render_table(frame, height=480)
    st.caption(
        "Per-position Greeks require the position's own live instrument quote — "
        "see Greeks Intelligence for the full chain-level view"
    )


def render(ctx: DashboardRenderContext) -> None:
    """Render the Positions page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Positions", "Live paper positions, MTM, and P&L (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="positions_refresh",
    )
