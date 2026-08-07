"""Portfolio page — read-only holdings, allocation, and exposure display."""

from __future__ import annotations

import logging
from typing import Sequence

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.components.plotly_charts import build_allocation_pie, build_equity_curve
from dashboard.facade import NullIntegrationFacade
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import (
    DashboardRenderContext,
    PLACEHOLDER,
    PortfolioPageView,
    portfolio_exposure_cards,
    portfolio_position_breakdown_cards,
    portfolio_risk_snapshot_cards,
    portfolio_status_cards,
    portfolio_summary_kpi_cards,
)

_logger = logging.getLogger("dashboard.pages.portfolio")

_HOLDINGS_COLUMNS: tuple[str, ...] = (
    "Symbol",
    "Product",
    "Quantity",
    "Average Price",
    "Current Price",
    "Market Value",
    "Unrealized P&L",
    "Realized P&L",
    "Day Change %",
    "Weight %",
)


def _display(value: object | None, placeholder: str = PLACEHOLDER) -> str:
    """Format an optional upstream value for display.

    Args:
        value: Already-computed upstream value.
        placeholder: Placeholder when value is missing.

    Returns:
        Display string.
    """
    if value is None:
        return placeholder
    text = str(value).strip()
    return text if text else placeholder


def _offline_view() -> PortfolioPageView:
    """Return offline Portfolio placeholders via null facade."""
    return NullIntegrationFacade().get_portfolio()


def _resolve_portfolio_view(ctx: DashboardRenderContext) -> PortfolioPageView:
    """Load the Portfolio snapshot exclusively via DashboardFacade.

    Never computes positions, exposure, or PnL; a pure read-only soft-read.

    Args:
        ctx: Render context with facade.

    Returns:
        Portfolio page view (placeholders on failure).
    """
    try:
        getter = getattr(ctx.facade, "get_portfolio", None)
        if callable(getter):
            snapshot = getter()
            if isinstance(snapshot, PortfolioPageView):
                return snapshot
        render_error("Portfolio unavailable: get_portfolio missing")
    except Exception as exc:  # noqa: BLE001 - page must not crash
        _logger.warning("portfolio snapshot unavailable: %s", exc)
        render_error(f"Portfolio data unavailable: {exc}")
    return _offline_view()


def _holdings_frame(view: PortfolioPageView) -> pd.DataFrame:
    """Build the Holdings Table DataFrame.

    Args:
        view: Portfolio page snapshot.

    Returns:
        Holdings table (empty with headers when offline / empty).
    """
    rows: list[dict[str, str]] = []
    for row in view.positions:
        rows.append(
            {
                "Symbol": _display(getattr(row, "symbol", None)),
                "Product": _display(getattr(row, "product", None)),
                "Quantity": _display(getattr(row, "quantity", None)),
                "Average Price": _display(getattr(row, "avg_price", None)),
                "Current Price": _display(getattr(row, "current_price", None)),
                "Market Value": _display(getattr(row, "market_value", None)),
                "Unrealized P&L": _display(getattr(row, "unrealized_pnl", None)),
                "Realized P&L": _display(getattr(row, "realized_pnl", None)),
                "Day Change %": _display(getattr(row, "day_change_pct", None)),
                "Weight %": _display(getattr(row, "weight_pct", None)),
            }
        )
    if not rows:
        return pd.DataFrame(columns=list(_HOLDINGS_COLUMNS))
    return pd.DataFrame(rows, columns=list(_HOLDINGS_COLUMNS))


def _filter_holdings(df: pd.DataFrame, *, search: str = "") -> pd.DataFrame:
    """Filter the Holdings Table by free-text search.

    Pure display filter — never mutates ``df`` or recomputes holding fields.
    Sorting is provided natively by the underlying table widget.

    Args:
        df: Source holdings dataframe.
        search: Case-insensitive substring match across all columns.

    Returns:
        Filtered dataframe with a reset index.
    """
    filtered = df
    term = search.strip().lower()
    if term:
        mask = filtered.apply(
            lambda row: row.astype(str).str.lower().str.contains(
                term, regex=False
            ).any(),
            axis=1,
        )
        filtered = filtered[mask]
    return filtered.reset_index(drop=True)


def _holdings_csv(df: pd.DataFrame) -> str:
    """Serialize the Holdings Table to CSV text.

    Args:
        df: Holdings dataframe.

    Returns:
        UTF-8 CSV text without the pandas index column.
    """
    return df.to_csv(index=False)


def _allocation_frame(series: Sequence[tuple[str, float]]) -> pd.DataFrame:
    """Build a label/weight dataframe from an already-computed allocation series.

    Args:
        series: Ordered ``(label, weight)`` pairs already computed upstream.

    Returns:
        DataFrame with ``label``/``weight`` columns (empty when unavailable).
    """
    if not series:
        return pd.DataFrame(columns=["label", "weight"])
    return pd.DataFrame(list(series), columns=["label", "weight"])


def _series_frame(
    series: Sequence[tuple[str, float]],
    value_column: str,
) -> pd.DataFrame:
    """Build a timestamp/value chart dataframe from an already-computed series.

    Args:
        series: Ordered ``(label, value)`` pairs already computed upstream.
        value_column: Name of the value column expected by the chart builder.

    Returns:
        DataFrame with ``timestamp`` and ``value_column`` columns (empty when
        the series is unavailable).
    """
    if not series:
        return pd.DataFrame(columns=["timestamp", value_column])
    return pd.DataFrame(list(series), columns=["timestamp", value_column])


def _render_holdings_section(view: PortfolioPageView) -> None:
    """Render the searchable, sortable, downloadable Holdings Table.

    Read-only — never recomputes holdings, prices, or PnL.

    Args:
        view: Portfolio page snapshot.
    """
    st.subheader("Holdings")
    holdings_df = _holdings_frame(view)

    search = ""
    try:
        search_raw = st.text_input("Search holdings", key="portfolio_holdings_search")
        search = str(search_raw) if isinstance(search_raw, str) else ""
        filtered_df = _filter_holdings(holdings_df, search=search)
    except Exception:  # noqa: BLE001 - page must not crash
        filtered_df = holdings_df

    render_table(filtered_df)
    if holdings_df.empty:
        st.caption("No holdings — awaiting backend portfolio data")
    elif filtered_df.empty:
        st.caption("No holdings match the current search")

    st.download_button(
        "Download CSV",
        data=_holdings_csv(filtered_df),
        file_name="portfolio_holdings.csv",
        mime="text/csv",
        key="portfolio_holdings_download",
    )


def _render_allocation_section(view: PortfolioPageView) -> None:
    """Render Allocation by Sector, Instrument, and Product charts.

    Reuses ``build_allocation_pie``; offline/empty series render an empty
    (traceless) chart rather than fabricated data.

    Args:
        view: Portfolio page snapshot.
    """
    st.subheader("Allocation")
    sector_col, instrument_col, product_col = st.columns(3)

    with sector_col:
        st.caption("By Sector")
        sector_df = _allocation_frame(view.allocation_by_sector)
        figure = build_allocation_pie(sector_df)
        figure.update_layout(title="Allocation by Sector")
        st.plotly_chart(figure, use_container_width=True)
        if sector_df.empty:
            st.caption("Sector allocation unavailable")

    with instrument_col:
        st.caption("By Instrument")
        instrument_df = _allocation_frame(view.allocation_by_instrument)
        figure = build_allocation_pie(instrument_df)
        figure.update_layout(title="Allocation by Instrument")
        st.plotly_chart(figure, use_container_width=True)
        if instrument_df.empty:
            st.caption("Instrument allocation unavailable")

    with product_col:
        st.caption("By Product")
        product_df = _allocation_frame(view.allocation_by_product)
        figure = build_allocation_pie(product_df)
        figure.update_layout(title="Allocation by Product")
        st.plotly_chart(figure, use_container_width=True)
        if product_df.empty:
            st.caption("Product allocation unavailable")


def _render_performance_section(view: PortfolioPageView) -> None:
    """Render Equity Curve, Daily P&L, and Cumulative P&L charts.

    Reuses ``build_equity_curve``; offline/empty series render an empty
    placeholder chart rather than fabricated data.

    Args:
        view: Portfolio page snapshot.
    """
    st.subheader("Portfolio Performance")
    equity_col, daily_col, cumulative_col = st.columns(3)

    with equity_col:
        st.caption("Equity Curve")
        equity_df = _series_frame(view.equity_series, "equity")
        st.plotly_chart(build_equity_curve(equity_df), use_container_width=True)
        if equity_df.empty:
            st.caption("Equity curve unavailable")

    with daily_col:
        st.caption("Daily P&L")
        daily_df = _series_frame(view.daily_pnl_series, "equity")
        figure = build_equity_curve(daily_df)
        figure.update_layout(title="Daily P&L")
        st.plotly_chart(figure, use_container_width=True)
        if daily_df.empty:
            st.caption("Daily P&L unavailable")

    with cumulative_col:
        st.caption("Cumulative P&L")
        cumulative_df = _series_frame(view.cumulative_pnl_series, "equity")
        figure = build_equity_curve(cumulative_df)
        figure.update_layout(title="Cumulative P&L")
        st.plotly_chart(figure, use_container_width=True)
        if cumulative_df.empty:
            st.caption("Cumulative P&L unavailable")


def _render_portfolio_body(ctx: DashboardRenderContext) -> None:
    """Render Portfolio body panels (re-read on every call).

    Args:
        ctx: Immutable render context with facade.
    """
    snapshot = _resolve_portfolio_view(ctx)

    st.subheader("Portfolio Summary")
    render_kpi_row(portfolio_summary_kpi_cards(snapshot))

    _render_holdings_section(snapshot)

    _render_allocation_section(snapshot)

    st.subheader("Exposure")
    render_kpi_row(portfolio_exposure_cards(snapshot))

    _render_performance_section(snapshot)

    st.subheader("Portfolio Risk Snapshot")
    render_kpi_row(portfolio_risk_snapshot_cards(snapshot))

    st.subheader("Position Breakdown")
    render_kpi_row(portfolio_position_breakdown_cards(snapshot))

    st.subheader("Portfolio Status")
    render_kpi_row(portfolio_status_cards(snapshot))

    if snapshot.source == "offline" and not snapshot.positions:
        st.caption("Offline mode — awaiting backend portfolio snapshot")


def render(ctx: DashboardRenderContext) -> None:
    """Render the Portfolio page.

    Displays portfolio summary KPIs, a searchable/sortable holdings table
    with CSV export, allocation charts (sector/instrument/product), exposure
    cards, performance charts (equity curve/daily P&L/cumulative P&L), a risk
    snapshot, position breakdown counts, and portfolio status. Read-only —
    obtains all data through ``DashboardFacade`` only.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Portfolio", "Holdings, allocation, and exposure (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_portfolio_body(ctx)
        return
    live_fragment(
        lambda: _render_portfolio_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="portfolio_refresh",
    )


__all__ = (
    "render",
    "_resolve_portfolio_view",
    "_holdings_frame",
    "_filter_holdings",
    "_holdings_csv",
    "_allocation_frame",
    "_series_frame",
)
