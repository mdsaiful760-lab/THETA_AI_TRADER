"""Option Chain page — institutional CALLS | STRIKE | PUTS chain (read-only)."""

from __future__ import annotations

import logging

import streamlit as st

from dashboard.components.error_banner import render_error
from dashboard.components.option_chain_table import render_option_chain_table
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import empty_market_snapshot, market_snapshot_to_page_view
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext, MarketPageView, PLACEHOLDER

_logger = logging.getLogger("dashboard.pages.option_chain")


def _resolve_snapshot(ctx: DashboardRenderContext) -> MarketPageView:
    try:
        getter = getattr(ctx.facade, "get_market_snapshot", None)
        if callable(getter):
            snapshot = getter()
            if isinstance(snapshot, MarketPageView):
                return snapshot
    except Exception as exc:  # noqa: BLE001
        _logger.warning("option chain snapshot unavailable: %s", exc)
        render_error(f"Option chain unavailable: {exc}")
    return market_snapshot_to_page_view(
        empty_market_snapshot(), indices=(), market_regime=PLACEHOLDER, connected=False
    )


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Option Chain page body (re-invoked on every live refresh)."""
    view = _resolve_snapshot(ctx)
    st.caption(
        f"Underlying: {view.selected_underlying} · Source: {view.source} "
        f"· Connection: {view.connection_status}"
    )
    if view.atm_strike not in (PLACEHOLDER, "", None):
        st.caption(f"ATM strike: {view.atm_strike}")
    render_option_chain_table(
        columns=view.option_chain_columns,
        rows=view.option_chain_rows,
        atm_strike=view.atm_strike,
        ai_selected_strikes=view.ai_selected_strikes,
        height=600,
    )


def render(ctx: DashboardRenderContext) -> None:
    """Render the Option Chain page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Option Chain", "Live institutional option chain (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="option_chain_refresh",
    )
