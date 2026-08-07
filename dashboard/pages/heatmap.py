"""Market Heatmap page — real open-interest heatmap by strike (read-only)."""

from __future__ import annotations

import logging

import plotly.graph_objects as go
import streamlit as st

from dashboard.components.error_banner import render_error
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import empty_market_snapshot, market_snapshot_to_page_view
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext, MarketPageView, PLACEHOLDER

_logger = logging.getLogger("dashboard.pages.heatmap")


def _resolve_snapshot(ctx: DashboardRenderContext) -> MarketPageView:
    try:
        getter = getattr(ctx.facade, "get_market_snapshot", None)
        if callable(getter):
            snapshot = getter()
            if isinstance(snapshot, MarketPageView):
                return snapshot
    except Exception as exc:  # noqa: BLE001
        _logger.warning("heatmap snapshot unavailable: %s", exc)
        render_error(f"Heatmap data unavailable: {exc}")
    return market_snapshot_to_page_view(
        empty_market_snapshot(), indices=(), market_regime=PLACEHOLDER, connected=False
    )


def _oi_matrix(view: MarketPageView) -> tuple[list[str], list[float], list[float]]:
    """Real per-strike CALL/PUT OI arrays, strikes sorted ascending."""
    columns = list(view.option_chain_columns)
    try:
        idx = {name: columns.index(name) for name in ("strike", "type", "oi")}
    except ValueError:
        return [], [], []

    by_strike: dict[float, dict[str, float]] = {}
    for row in view.option_chain_rows:
        try:
            strike = float(row[idx["strike"]])
            oi = float(row[idx["oi"]])
        except (ValueError, IndexError):
            continue
        option_type = row[idx["type"]].strip().upper()
        bucket = by_strike.setdefault(strike, {})
        if option_type in ("CE", "CALL"):
            bucket["CE"] = oi
        elif option_type in ("PE", "PUT"):
            bucket["PE"] = oi

    strikes = sorted(by_strike)
    calls = [by_strike[strike].get("CE", 0.0) for strike in strikes]
    puts = [by_strike[strike].get("PE", 0.0) for strike in strikes]
    return [f"{strike:g}" for strike in strikes], calls, puts


def _heatmap_figure(strikes: list[str], calls: list[float], puts: list[float]) -> go.Figure:
    figure = go.Figure(
        data=go.Heatmap(
            z=[calls, puts],
            x=strikes,
            y=["Call OI", "Put OI"],
            colorscale=[[0.0, "#121821"], [0.5, "#3D8BFF"], [1.0, "#F0B429"]],
            colorbar={"title": "OI"},
        )
    )
    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121821",
        plot_bgcolor="#121821",
        margin={"l": 24, "r": 24, "t": 36, "b": 24},
        title="Open Interest Heatmap by Strike",
        height=320,
    )
    return figure


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Market Heatmap page body (re-invoked on every live refresh)."""
    view = _resolve_snapshot(ctx)
    st.caption(f"Underlying: {view.selected_underlying} · ATM strike: {view.atm_strike}")
    strikes, calls, puts = _oi_matrix(view)
    if not strikes:
        st.info("Heatmap unavailable — awaiting backend market snapshot")
        return
    st.plotly_chart(_heatmap_figure(strikes, calls, puts), use_container_width=True)
    st.caption("Real open interest per strike/type from the live option chain")


def render(ctx: DashboardRenderContext) -> None:
    """Render the Market Heatmap page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Market Heatmap", "Live OI concentration by strike (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="heatmap_refresh",
    )
