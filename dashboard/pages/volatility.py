"""Volatility Surface page — real IV-by-strike skew (read-only)."""

from __future__ import annotations

import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import empty_market_snapshot, market_snapshot_to_page_view
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext, MarketPageView, PLACEHOLDER

_logger = logging.getLogger("dashboard.pages.volatility")


def _resolve_snapshot(ctx: DashboardRenderContext) -> MarketPageView:
    try:
        getter = getattr(ctx.facade, "get_market_snapshot", None)
        if callable(getter):
            snapshot = getter()
            if isinstance(snapshot, MarketPageView):
                return snapshot
    except Exception as exc:  # noqa: BLE001
        _logger.warning("volatility snapshot unavailable: %s", exc)
        render_error(f"Volatility data unavailable: {exc}")
    return market_snapshot_to_page_view(
        empty_market_snapshot(), indices=(), market_regime=PLACEHOLDER, connected=False
    )


def _iv_points(view: MarketPageView) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Real (strike, IV) points split by CE/PE from the live option chain."""
    columns = list(view.option_chain_columns)
    try:
        idx = {name: columns.index(name) for name in ("strike", "type", "iv")}
    except ValueError:
        return pd.DataFrame(columns=["strike", "iv"]), pd.DataFrame(columns=["strike", "iv"])

    calls: list[tuple[float, float]] = []
    puts: list[tuple[float, float]] = []
    for row in view.option_chain_rows:
        try:
            strike = float(row[idx["strike"]])
            iv = float(row[idx["iv"]])
        except (ValueError, IndexError):
            continue
        target = calls if row[idx["type"]].strip().upper() in ("CE", "CALL") else puts
        target.append((strike, iv))
    calls.sort(key=lambda pair: pair[0])
    puts.sort(key=lambda pair: pair[0])
    return (
        pd.DataFrame(calls, columns=["strike", "iv"]),
        pd.DataFrame(puts, columns=["strike", "iv"]),
    )


def _skew_figure(calls: pd.DataFrame, puts: pd.DataFrame) -> go.Figure:
    """Build a dark-themed IV skew line chart from real chain IV values."""
    figure = go.Figure()
    if not calls.empty:
        figure.add_trace(
            go.Scatter(
                x=calls["strike"], y=calls["iv"], mode="lines+markers",
                name="Call IV", line={"color": "#3D8BFF"},
            )
        )
    if not puts.empty:
        figure.add_trace(
            go.Scatter(
                x=puts["strike"], y=puts["iv"], mode="lines+markers",
                name="Put IV", line={"color": "#E74C3C"},
            )
        )
    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121821",
        plot_bgcolor="#121821",
        margin={"l": 24, "r": 24, "t": 36, "b": 24},
        title="IV Skew by Strike",
        xaxis_title="Strike",
        yaxis_title="Implied Volatility (%)",
    )
    return figure


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Volatility Surface page body (re-invoked on every live refresh)."""
    view = _resolve_snapshot(ctx)
    st.caption(f"Underlying: {view.selected_underlying} · ATM strike: {view.atm_strike}")
    calls, puts = _iv_points(view)
    if calls.empty and puts.empty:
        st.info("IV data unavailable — awaiting backend market snapshot")
        return
    st.plotly_chart(_skew_figure(calls, puts), use_container_width=True)

    st.markdown("**Raw IV Table**")
    combined = pd.concat(
        [calls.assign(type="CE"), puts.assign(type="PE")], ignore_index=True
    ).sort_values("strike")
    render_table(combined.rename(columns={"strike": "Strike", "iv": "IV", "type": "Type"}))


def render(ctx: DashboardRenderContext) -> None:
    """Render the Volatility Surface page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Volatility", "Live IV skew by strike (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="volatility_refresh",
    )
