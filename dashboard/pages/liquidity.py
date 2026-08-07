"""Liquidity page — real bid-ask spread and volume (read-only).

Spread, spread %, and liquidity classification are computed once in
``DashboardIntegrationFacade.get_liquidity_rows()`` — this page only
displays the already-computed real values, never recomputes them.
"""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import LIQUIDITY_COLUMNS
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext

_logger = logging.getLogger("dashboard.pages.liquidity")


def _resolve_liquidity_rows(ctx: DashboardRenderContext) -> tuple[tuple[str, ...], ...]:
    """Load the real, already-computed liquidity table via the facade."""
    try:
        getter = getattr(ctx.facade, "get_liquidity_rows", None)
        if callable(getter):
            rows = getter()
            if isinstance(rows, tuple):
                return rows
    except Exception as exc:  # noqa: BLE001
        _logger.warning("liquidity rows unavailable: %s", exc)
        render_error(f"Liquidity data unavailable: {exc}")
    return ()


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Liquidity page body (re-invoked on every live refresh)."""
    rows = _resolve_liquidity_rows(ctx)
    frame = pd.DataFrame(list(rows), columns=list(LIQUIDITY_COLUMNS))
    if frame.empty:
        render_table(frame)
        st.info("Liquidity data unavailable — awaiting backend market snapshot")
        return
    render_table(frame, height=520)
    st.caption(
        "Liquidity: Excellent ≤0.5% · Good ≤1.5% · Fair ≤3.0% · Poor >3.0% "
        "(spread as % of real mid-price, computed by the facade)"
    )


def render(ctx: DashboardRenderContext) -> None:
    """Render the Liquidity page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Liquidity", "Live bid-ask spread and depth (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="liquidity_refresh",
    )
