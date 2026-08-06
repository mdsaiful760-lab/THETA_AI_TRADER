"""Orders page — read-only execution monitor."""

from __future__ import annotations

import logging
from typing import Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.components.plotly_charts import build_allocation_pie
from dashboard.facade import NullIntegrationFacade
from dashboard.view_models import (
    DashboardRenderContext,
    OrdersPageView,
    PLACEHOLDER,
    orders_broker_status_cards,
    orders_status_distribution,
    orders_summary_kpi_cards,
)

_logger = logging.getLogger("dashboard.pages.orders")

_ORDER_COLUMNS: tuple[str, ...] = (
    "Order ID",
    "Time",
    "Symbol",
    "Strategy",
    "Side",
    "Qty",
    "Price",
    "Status",
)

_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "FILLED",
        "COMPLETE",
        "COMPLETED",
        "CANCELLED",
        "CANCELED",
        "REJECTED",
        "EXPIRED",
        "FAILED",
    }
)

_ORDER_HISTORY_LIMIT: int = 100

_STATUS_COLORS: dict[str, str] = {
    "FILLED": "#3DDC84",
    "PENDING": "#F5A623",
    "CANCELLED": "#8892A0",
    "REJECTED": "#E74C3C",
}
_DEFAULT_STATUS_COLOR: str = "#3D8BFF"


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


def _offline_view() -> OrdersPageView:
    """Return offline Orders placeholders via null facade."""
    return NullIntegrationFacade().get_orders()


def _resolve_orders_view(ctx: DashboardRenderContext) -> OrdersPageView:
    """Load the Orders snapshot exclusively via DashboardFacade.

    Never places, cancels, or modifies orders; a pure read-only soft-read.

    Args:
        ctx: Render context with facade.

    Returns:
        Orders page view (placeholders on failure).
    """
    try:
        getter = getattr(ctx.facade, "get_orders", None)
        if callable(getter):
            snapshot = getter()
            if isinstance(snapshot, OrdersPageView):
                return snapshot
        render_error("Orders unavailable: get_orders missing")
    except Exception as exc:  # noqa: BLE001 - page must not crash
        _logger.warning("orders snapshot unavailable: %s", exc)
        render_error(f"Orders data unavailable: {exc}")
    return _offline_view()


def _orders_frame(orders: Sequence[object]) -> pd.DataFrame:
    """Build an orders DataFrame for display.

    Args:
        orders: Order row objects (already-computed upstream values).

    Returns:
        Orders table (empty with headers when unavailable).
    """
    rows: list[dict[str, str]] = []
    for order in orders:
        rows.append(
            {
                "Order ID": _display(getattr(order, "order_id", None)),
                "Time": _display(getattr(order, "timestamp", None)),
                "Symbol": _display(getattr(order, "symbol", None)),
                "Strategy": _display(getattr(order, "strategy", None)),
                "Side": _display(getattr(order, "side", None)),
                "Qty": _display(getattr(order, "quantity", None)),
                "Price": _display(getattr(order, "price", None)),
                "Status": _display(getattr(order, "status", None)),
            }
        )
    if not rows:
        return pd.DataFrame(columns=list(_ORDER_COLUMNS))
    return pd.DataFrame(rows, columns=list(_ORDER_COLUMNS))


def _is_active_status(status: str) -> bool:
    """Return whether an order status represents a non-terminal, active order.

    Args:
        status: Already-computed upstream status display string.

    Returns:
        ``True`` when the status is not one of the known terminal states.
    """
    token = status.strip().upper()
    if not token or token == PLACEHOLDER:
        return False
    return token not in _TERMINAL_STATUSES


def _active_orders_frame(view: OrdersPageView) -> pd.DataFrame:
    """Build the Active Orders DataFrame (non-terminal statuses only).

    Args:
        view: Orders page snapshot.

    Returns:
        Active orders table (empty with headers when none are active).
    """
    active = [order for order in view.orders if _is_active_status(order.status)]
    return _orders_frame(active)


def _order_history_frame(view: OrdersPageView) -> pd.DataFrame:
    """Build the Recent Order History DataFrame (newest-first, capped at 100).

    Args:
        view: Orders page snapshot.

    Returns:
        Order history table (empty with headers when unavailable).
    """
    return _orders_frame(view.orders[:_ORDER_HISTORY_LIMIT])


def _filter_orders(
    df: pd.DataFrame,
    *,
    search: str = "",
    statuses: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Filter an orders table by free-text search and status.

    Pure display filter — never mutates ``df`` or recomputes order fields.

    Args:
        df: Source orders dataframe.
        search: Case-insensitive substring match across all columns.
        statuses: Optional status values to keep; empty/``None`` keeps all.

    Returns:
        Filtered dataframe with a reset index.
    """
    filtered = df
    if statuses:
        filtered = filtered[filtered["Status"].isin(list(statuses))]
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


def _orders_csv(df: pd.DataFrame) -> str:
    """Serialize an orders table to CSV text.

    Args:
        df: Orders dataframe.

    Returns:
        UTF-8 CSV text without the pandas index column.
    """
    return df.to_csv(index=False)


def _build_order_timeline_figure(df: pd.DataFrame) -> go.Figure:
    """Build a dark-themed order-event timeline figure.

    Styling mirrors ``dashboard/components/plotly_charts.py`` since no shared
    timeline builder exists there yet for this milestone.

    Args:
        df: DataFrame with ``Time``, ``Symbol``, and ``Status`` columns
            (already-computed upstream display values; nothing is derived).

    Returns:
        Dark-themed Plotly figure (empty trace set when ``df`` has no rows).
    """
    figure = go.Figure()
    if not df.empty and {"Time", "Symbol", "Status"}.issubset(df.columns):
        for status_label, group in df.groupby("Status"):
            figure.add_trace(
                go.Scatter(
                    x=group["Time"],
                    y=group["Symbol"],
                    mode="markers",
                    name=str(status_label),
                    marker={
                        "color": _STATUS_COLORS.get(
                            str(status_label).upper(), _DEFAULT_STATUS_COLOR
                        ),
                        "size": 11,
                    },
                )
            )
    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121821",
        plot_bgcolor="#121821",
        margin={"l": 24, "r": 24, "t": 36, "b": 24},
        title="Order Timeline",
        xaxis_title="Time",
        yaxis_title="Symbol",
    )
    return figure


def _order_status_distribution_frame(view: OrdersPageView) -> pd.DataFrame:
    """Build the label/weight dataframe for the status distribution donut.

    Args:
        view: Orders page snapshot.

    Returns:
        DataFrame with ``label``/``weight`` columns (empty when all counts
        are zero, e.g. offline).
    """
    series = orders_status_distribution(view)
    if not series or not any(value > 0 for _, value in series):
        return pd.DataFrame(columns=["label", "weight"])
    return pd.DataFrame(series, columns=["label", "weight"])


def _render_orders_table_section(
    *,
    title: str,
    df: pd.DataFrame,
    key_prefix: str,
    show_filter: bool,
    show_download: bool,
    empty_caption: str,
) -> None:
    """Render a searchable (optionally filterable/downloadable) orders table.

    Sorting is provided natively by the underlying table widget. Shared by
    both the Active Orders and Recent Order History sections to avoid
    duplicated search/filter/CSV logic. Read-only — never places, cancels,
    or recomputes orders.

    Args:
        title: Section subheader text.
        df: Source orders dataframe for this section.
        key_prefix: Unique Streamlit widget key prefix for this section.
        show_filter: Whether to render a status multiselect filter.
        show_download: Whether to render a CSV download button.
        empty_caption: Caption shown when the source table has no rows.
    """
    st.subheader(title)
    search = ""
    statuses: list[str] = []
    filtered_df = df
    try:
        if show_filter:
            search_col, filter_col = st.columns([2, 1])
            with search_col:
                search_raw = st.text_input("Search orders", key=f"{key_prefix}_search")
            with filter_col:
                status_options = sorted(df["Status"].unique().tolist())
                statuses_raw = st.multiselect(
                    "Filter by status",
                    options=status_options,
                    default=[],
                    key=f"{key_prefix}_status_filter",
                )
            statuses = (
                list(statuses_raw) if isinstance(statuses_raw, (list, tuple)) else []
            )
        else:
            search_raw = st.text_input("Search orders", key=f"{key_prefix}_search")
        search = str(search_raw) if isinstance(search_raw, str) else ""
        filtered_df = _filter_orders(df, search=search, statuses=statuses)
    except Exception:  # noqa: BLE001 - page must not crash
        filtered_df = df

    render_table(filtered_df)
    if df.empty:
        st.caption(empty_caption)
    elif filtered_df.empty:
        st.caption("No orders match the current search/filter")

    if show_download:
        st.download_button(
            "Download CSV",
            data=_orders_csv(filtered_df),
            file_name=f"{key_prefix}.csv",
            mime="text/csv",
            key=f"{key_prefix}_download",
        )


def _render_orders_body(ctx: DashboardRenderContext) -> None:
    """Render Orders body panels (re-read on every call).

    Args:
        ctx: Immutable render context with facade.
    """
    snapshot = _resolve_orders_view(ctx)

    st.subheader("Order Summary")
    render_kpi_row(orders_summary_kpi_cards(snapshot))

    _render_orders_table_section(
        title="Active Orders",
        df=_active_orders_frame(snapshot),
        key_prefix="orders_active",
        show_filter=False,
        show_download=False,
        empty_caption="No active orders",
    )

    _render_orders_table_section(
        title="Recent Order History",
        df=_order_history_frame(snapshot),
        key_prefix="orders_history",
        show_filter=True,
        show_download=True,
        empty_caption="No order history — awaiting backend orders",
    )

    st.subheader("Order Status Distribution")
    distribution_df = _order_status_distribution_frame(snapshot)
    st.plotly_chart(build_allocation_pie(distribution_df), use_container_width=True)
    if distribution_df.empty:
        st.caption("Order status distribution unavailable — awaiting backend data")

    st.subheader("Order Timeline")
    timeline_df = _order_history_frame(snapshot)
    st.plotly_chart(_build_order_timeline_figure(timeline_df), use_container_width=True)
    if timeline_df.empty:
        st.caption("Order timeline unavailable — awaiting backend data")

    st.subheader("Broker Status")
    render_kpi_row(orders_broker_status_cards(snapshot))

    if snapshot.source == "offline" and not snapshot.orders:
        st.caption("Offline mode — awaiting backend order book")


def render(ctx: DashboardRenderContext) -> None:
    """Render the orders page.

    Displays order summary KPIs, active orders, a searchable/filterable
    recent order history with CSV export, order status distribution, an
    order timeline, and broker status. Read-only — never places, cancels,
    or modifies orders.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Orders", "Execution monitor (read-only)")
    _render_orders_body(ctx)
    st.caption("Order mutation not available in dashboard v1")


__all__ = (
    "render",
    "_resolve_orders_view",
    "_orders_frame",
    "_active_orders_frame",
    "_order_history_frame",
    "_filter_orders",
    "_orders_csv",
    "_build_order_timeline_figure",
    "_order_status_distribution_frame",
)
