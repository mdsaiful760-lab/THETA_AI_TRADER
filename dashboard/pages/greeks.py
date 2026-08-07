"""Greeks Intelligence page — real per-contract option Greeks (read-only)."""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from dashboard.components.error_banner import render_error
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.data_table import render_table
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import empty_market_snapshot, market_snapshot_to_page_view
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext, KpiCardModel, MarketPageView, PLACEHOLDER

_logger = logging.getLogger("dashboard.pages.greeks")

_GREEK_COLUMNS = ("strike", "type", "ltp", "iv", "delta", "gamma", "theta", "vega")


def _resolve_snapshot(ctx: DashboardRenderContext) -> MarketPageView:
    try:
        getter = getattr(ctx.facade, "get_market_snapshot", None)
        if callable(getter):
            snapshot = getter()
            if isinstance(snapshot, MarketPageView):
                return snapshot
    except Exception as exc:  # noqa: BLE001
        _logger.warning("greeks snapshot unavailable: %s", exc)
        render_error(f"Greeks data unavailable: {exc}")
    return market_snapshot_to_page_view(
        empty_market_snapshot(), indices=(), market_regime=PLACEHOLDER, connected=False
    )


def _greeks_frame(view: MarketPageView) -> pd.DataFrame:
    """Project the real option chain onto Greeks-only columns."""
    if not view.option_chain_rows:
        return pd.DataFrame(columns=_GREEK_COLUMNS)
    columns = list(view.option_chain_columns)
    indices = [columns.index(name) for name in _GREEK_COLUMNS if name in columns]
    rows = [tuple(row[i] for i in indices) for row in view.option_chain_rows]
    return pd.DataFrame(rows, columns=[columns[i] for i in indices])


def _atm_greek_cards(view: MarketPageView, frame: pd.DataFrame) -> tuple[KpiCardModel, ...]:
    """Real ATM CE/PE delta/gamma/theta/vega cards — no chain, no cards."""
    if frame.empty or view.atm_strike in (PLACEHOLDER, "", None):
        return ()
    atm_rows = frame[frame["strike"].astype(str).str.strip() == str(view.atm_strike).strip()]
    if atm_rows.empty:
        return ()
    cards = []
    for _, row in atm_rows.iterrows():
        label = f"ATM {row['type']} Delta / Gamma"
        cards.append(KpiCardModel(label, f"{row['delta']} / {row['gamma']}"))
    return tuple(cards)


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Greeks Intelligence page body (re-invoked on every live refresh)."""
    view = _resolve_snapshot(ctx)
    frame = _greeks_frame(view)

    st.caption(f"Underlying: {view.selected_underlying} · ATM strike: {view.atm_strike}")
    atm_cards = _atm_greek_cards(view, frame)
    if atm_cards:
        render_kpi_row(atm_cards)

    st.markdown("**Chain Greeks**")
    if frame.empty:
        render_table(frame)
        st.info("Greeks unavailable — awaiting backend market snapshot")
        return
    render_table(frame, height=520)


def render(ctx: DashboardRenderContext) -> None:
    """Render the Greeks Intelligence page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Greeks Intelligence", "Live per-contract option Greeks (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="greeks_refresh",
    )
