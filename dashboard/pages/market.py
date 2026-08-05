"""Market context page — read-only market display via DashboardFacade."""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from dashboard.components.chart_placeholder import render_tradingview_placeholder
from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.index_ticker import render_index_strip
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import (
    HOME_MARKET_INDEX_SYMBOLS,
    empty_home_market_indices,
    empty_market_snapshot,
    home_indices_to_quote_views,
    market_snapshot_to_page_view,
)
from dashboard.view_models import (
    DashboardRenderContext,
    MarketPageView,
    PLACEHOLDER,
    default_index_quotes,
    market_page_statistic_cards,
    market_regime_cards,
)

_logger = logging.getLogger("dashboard.pages.market")


def _offline_market_view() -> MarketPageView:
    """Return offline Market page placeholders."""
    return market_snapshot_to_page_view(
        empty_market_snapshot(),
        indices=home_indices_to_quote_views(
            empty_home_market_indices(
                facade_connected=False,
                connection_status="OFFLINE",
            )
        ),
        market_regime=PLACEHOLDER,
        connected=False,
    )


def _resolve_market_view(ctx: DashboardRenderContext) -> MarketPageView:
    """Load Market page snapshot exclusively via DashboardFacade methods.

    Args:
        ctx: Render context with facade.

    Returns:
        Market page view (placeholders on failure).
    """
    try:
        getter = getattr(ctx.facade, "get_market_snapshot", None)
        if callable(getter):
            snapshot = getter()
            if isinstance(snapshot, MarketPageView):
                if not snapshot.indices:
                    return MarketPageView(
                        underlyings=snapshot.underlyings or HOME_MARKET_INDEX_SYMBOLS,
                        selected_underlying=snapshot.selected_underlying,
                        ltp=snapshot.ltp,
                        change=snapshot.change,
                        volume=snapshot.volume,
                        market_regime=snapshot.market_regime,
                        connection_status=snapshot.connection_status,
                        last_update=snapshot.last_update,
                        source=snapshot.source,
                        indices=default_index_quotes(HOME_MARKET_INDEX_SYMBOLS),
                        option_chain_columns=snapshot.option_chain_columns,
                        option_chain_rows=snapshot.option_chain_rows,
                    )
                return snapshot
    except Exception as exc:  # noqa: BLE001 - page must not crash
        _logger.warning("market snapshot unavailable: %s", exc)
        render_error(f"Market data unavailable: {exc}")
    return _offline_market_view()


def _option_chain_frame(view: MarketPageView) -> pd.DataFrame:
    """Build option-chain DataFrame from the market snapshot.

    Args:
        view: Market page snapshot.

    Returns:
        DataFrame with configured columns (possibly empty).
    """
    columns = list(view.option_chain_columns) or [
        "strike",
        "type",
        "ltp",
        "oi",
        "iv",
    ]
    if not view.option_chain_rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(list(view.option_chain_rows), columns=columns)


def render(ctx: DashboardRenderContext) -> None:
    """Render the market page.

    Displays live index cards, market regime, market statistics, market
    snapshot (option chain), and a TradingView placeholder. Read-only —
    no broker, strategy, or execution logic.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Market", "Live market context (read-only)")
    snapshot = _resolve_market_view(ctx)

    st.subheader("Live Indices")
    render_index_strip(snapshot.indices)

    st.subheader("Market Regime")
    render_kpi_row(market_regime_cards(snapshot))

    st.subheader("Market Statistics")
    render_kpi_row(market_page_statistic_cards(snapshot))

    st.subheader("Market Snapshot")
    underlyings = list(snapshot.underlyings) or list(HOME_MARKET_INDEX_SYMBOLS)
    selected = snapshot.selected_underlying
    selected_index = underlyings.index(selected) if selected in underlyings else 0
    st.selectbox(
        "Underlying",
        options=underlyings,
        index=selected_index,
        disabled=True,
    )
    st.caption(
        f"Source: {snapshot.source} · Connection: {snapshot.connection_status}"
    )
    st.markdown("**Option Chain**")
    chain_df = _option_chain_frame(snapshot)
    render_table(chain_df)
    if chain_df.empty:
        st.info("Option chain unavailable — awaiting backend market snapshot")
    st.caption("Strike selection and trading actions are display-only in v1")

    st.subheader("Chart")
    render_tradingview_placeholder()
