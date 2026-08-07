"""Option Chain page — full institutional chain table (read-only).

``st.dataframe`` already provides sticky headers and virtualized row
rendering natively, so a large real chain renders smoothly without any
extra plumbing here.
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

_logger = logging.getLogger("dashboard.pages.option_chain")

_DISPLAY_COLUMNS = (
    "strike", "type", "ltp", "bid", "ask", "oi", "oi_change",
    "volume", "iv", "delta", "gamma", "theta", "vega",
)
_DISPLAY_LABELS = {
    "strike": "Strike", "type": "Type", "ltp": "LTP", "bid": "Bid", "ask": "Ask",
    "oi": "OI", "oi_change": "OI Chg", "volume": "Volume", "iv": "IV",
    "delta": "Delta", "gamma": "Gamma", "theta": "Theta", "vega": "Vega",
}


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


def _chain_frame(view: MarketPageView) -> pd.DataFrame:
    columns = list(view.option_chain_columns)
    indices = [columns.index(name) for name in _DISPLAY_COLUMNS if name in columns]
    labels = [_DISPLAY_LABELS[columns[i]] for i in indices]
    if not view.option_chain_rows:
        return pd.DataFrame(columns=labels)
    rows = [tuple(row[i] for i in indices) for row in view.option_chain_rows]
    return pd.DataFrame(rows, columns=labels)


def _highlight_atm(df: pd.DataFrame, atm_strike: str) -> "pd.io.formats.style.Styler | pd.DataFrame":
    if df.empty or "Strike" not in df.columns or atm_strike in (PLACEHOLDER, "", None):
        return df

    def _row_style(row: pd.Series) -> list[str]:
        is_atm = str(row.get("Strike", "")).strip() == str(atm_strike).strip()
        return ["background-color: rgba(61, 139, 255, 0.14)" if is_atm else "" for _ in row]

    return df.style.apply(_row_style, axis=1)


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Option Chain page body (re-invoked on every live refresh)."""
    view = _resolve_snapshot(ctx)
    st.caption(
        f"Underlying: {view.selected_underlying} · Source: {view.source} "
        f"· Connection: {view.connection_status}"
    )
    frame = _chain_frame(view)
    if frame.empty:
        render_table(frame)
        st.info("Option chain unavailable — awaiting backend market snapshot")
        return

    if view.atm_strike not in (PLACEHOLDER, "", None):
        st.caption(f"ATM strike: {view.atm_strike} (highlighted below)")

    calls = frame[frame["Type"].astype(str).str.upper().isin(("CE", "CALL"))]
    puts = frame[frame["Type"].astype(str).str.upper().isin(("PE", "PUT"))]
    all_tab, calls_tab, puts_tab = st.tabs(["All", "Calls", "Puts"])
    with all_tab:
        render_table(_highlight_atm(frame, view.atm_strike), height=560)
    with calls_tab:
        render_table(_highlight_atm(calls, view.atm_strike), height=560)
    with puts_tab:
        render_table(_highlight_atm(puts, view.atm_strike), height=560)


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
