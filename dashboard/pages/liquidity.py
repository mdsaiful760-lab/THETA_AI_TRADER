"""Liquidity Analysis page — real bid-ask spread and volume (read-only).

Liquidity scores are a transparent, deterministic classification of real
bid/ask/volume values already in the option chain — never a fabricated
or estimated number.
"""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import empty_market_snapshot, market_snapshot_to_page_view
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext, MarketPageView, PLACEHOLDER

_logger = logging.getLogger("dashboard.pages.liquidity")


def _resolve_snapshot(ctx: DashboardRenderContext) -> MarketPageView:
    try:
        getter = getattr(ctx.facade, "get_market_snapshot", None)
        if callable(getter):
            snapshot = getter()
            if isinstance(snapshot, MarketPageView):
                return snapshot
    except Exception as exc:  # noqa: BLE001
        _logger.warning("liquidity snapshot unavailable: %s", exc)
        render_error(f"Liquidity data unavailable: {exc}")
    return market_snapshot_to_page_view(
        empty_market_snapshot(), indices=(), market_regime=PLACEHOLDER, connected=False
    )


def _classify(spread_pct: float) -> str:
    """Deterministic liquidity classification of a real spread percentage."""
    if spread_pct <= 0.5:
        return "Excellent"
    if spread_pct <= 1.5:
        return "Good"
    if spread_pct <= 3.0:
        return "Fair"
    return "Poor"


def _liquidity_frame(view: MarketPageView) -> pd.DataFrame:
    """Build the real spread/volume liquidity table from the option chain."""
    columns = list(view.option_chain_columns)
    try:
        idx = {name: columns.index(name) for name in ("strike", "type", "bid", "ask", "volume", "oi")}
    except ValueError:
        return pd.DataFrame(columns=["strike", "type", "bid", "ask", "spread", "spread_pct", "volume", "oi", "liquidity"])

    rows = []
    for row in view.option_chain_rows:
        try:
            bid = float(row[idx["bid"]])
            ask = float(row[idx["ask"]])
        except (ValueError, IndexError):
            continue
        mid = (bid + ask) / 2.0
        spread = ask - bid
        spread_pct = (spread / mid) * 100.0 if mid > 0 else None
        rows.append(
            (
                row[idx["strike"]],
                row[idx["type"]],
                f"{bid:.2f}",
                f"{ask:.2f}",
                f"{spread:.2f}",
                f"{spread_pct:.2f}%" if spread_pct is not None else PLACEHOLDER,
                row[idx["volume"]],
                row[idx["oi"]],
                _classify(spread_pct) if spread_pct is not None else PLACEHOLDER,
            )
        )
    return pd.DataFrame(
        rows,
        columns=["Strike", "Type", "Bid", "Ask", "Spread", "Spread %", "Volume", "OI", "Liquidity"],
    )


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Liquidity Analysis page body (re-invoked on every live refresh)."""
    view = _resolve_snapshot(ctx)
    st.caption(f"Underlying: {view.selected_underlying} · Real bid/ask spread, computed live")
    frame = _liquidity_frame(view)
    if frame.empty:
        render_table(frame)
        st.info("Liquidity data unavailable — awaiting backend market snapshot")
        return
    render_table(frame, height=520)
    st.caption(
        "Liquidity: Excellent ≤0.5% · Good ≤1.5% · Fair ≤3.0% · Poor >3.0% "
        "(spread as % of real mid-price)"
    )


def render(ctx: DashboardRenderContext) -> None:
    """Render the Liquidity Analysis page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Liquidity Analysis", "Live bid-ask spread and depth (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="liquidity_refresh",
    )
