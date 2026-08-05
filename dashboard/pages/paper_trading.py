"""Paper trading page — read-only virtual ledger display."""

from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.components.plotly_charts import build_equity_curve
from dashboard.dashboard_facade import (
    FacadePaperTradingLedger,
    empty_paper_trading_ledger,
    paper_ledger_to_page_view,
)
from dashboard.utils.polling import paper_trading_refresh_interval_ms
from dashboard.view_models import (
    DashboardRenderContext,
    PLACEHOLDER,
    PaperTradingPageView,
    paper_order_count_cards,
    paper_trading_kpi_cards,
)

_logger = logging.getLogger("dashboard.pages.paper_trading")

_POSITION_COLUMNS: tuple[str, ...] = (
    "Symbol",
    "Strategy",
    "Qty",
    "Entry",
    "Current",
    "MTM",
    "Status",
)


def _offline_view() -> PaperTradingPageView:
    """Return offline Paper Trading placeholders."""
    return paper_ledger_to_page_view(empty_paper_trading_ledger(source="offline"))


def _is_empty_portfolio(view: PaperTradingPageView) -> bool:
    """Return True when the ledger has no positions and no money values."""
    money_fields = (
        view.available_cash,
        view.virtual_cash,
        view.capital_used,
        view.total_equity,
        view.todays_pnl,
        view.realized_pnl,
        view.unrealized_pnl,
    )
    money_empty = all(value == PLACEHOLDER for value in money_fields)
    return money_empty and not view.positions


def _resolve_paper_view(ctx: DashboardRenderContext) -> PaperTradingPageView:
    """Load Paper Trading snapshot exclusively via DashboardFacade methods.

    Prefers ``get_paper_trading_ledger()`` (normative), then presentation
    ``get_paper_trading()``. Never places trades or calls brokers.

    Args:
        ctx: Render context with facade.

    Returns:
        Paper trading page view (placeholders on failure).
    """
    facade = ctx.facade
    try:
        ledger_getter = getattr(facade, "get_paper_trading_ledger", None)
        if callable(ledger_getter):
            ledger = ledger_getter()
            if isinstance(ledger, FacadePaperTradingLedger):
                return paper_ledger_to_page_view(ledger)
        getter = getattr(facade, "get_paper_trading", None)
        if callable(getter):
            snapshot = getter()
            if isinstance(snapshot, PaperTradingPageView):
                return snapshot
    except Exception as exc:  # noqa: BLE001 - page must not crash
        _logger.warning("paper trading ledger unavailable: %s", exc)
        render_error(f"Paper trading data unavailable: {exc}")
    return _offline_view()


def _positions_frame(view: PaperTradingPageView) -> pd.DataFrame:
    """Build the paper positions DataFrame.

    Args:
        view: Paper trading page snapshot.

    Returns:
        DataFrame with Symbol / Strategy / Qty / Entry / Current / MTM / Status.
    """
    rows = [
        {
            "Symbol": row.symbol,
            "Strategy": row.strategy,
            "Qty": row.quantity,
            "Entry": row.entry if row.entry != PLACEHOLDER else row.avg_price,
            "Current": row.current if row.current != PLACEHOLDER else row.mark,
            "MTM": row.mtm if row.mtm != PLACEHOLDER else row.pnl,
            "Status": row.status,
        }
        for row in view.positions
    ]
    if not rows:
        return pd.DataFrame(columns=list(_POSITION_COLUMNS))
    return pd.DataFrame(rows, columns=list(_POSITION_COLUMNS))


def _orders_frame(view: PaperTradingPageView) -> pd.DataFrame:
    """Build a compact recent-orders DataFrame.

    Args:
        view: Paper trading page snapshot.

    Returns:
        DataFrame of recent order rows, or an empty frame with headers.
    """
    rows = [
        {
            "Order ID": order.order_id,
            "Symbol": order.symbol,
            "Side": order.side,
            "Qty": order.quantity,
            "Status": order.status,
            "Timestamp": order.timestamp,
        }
        for order in view.orders
    ]
    if not rows:
        return pd.DataFrame(
            columns=["Order ID", "Symbol", "Side", "Qty", "Status", "Timestamp"]
        )
    return pd.DataFrame(rows)


def _render_paper_body(ctx: DashboardRenderContext) -> None:
    """Render Paper Trading body (re-read on every call)."""
    snapshot = _resolve_paper_view(ctx)

    st.subheader("Portfolio Summary")
    render_kpi_row(paper_trading_kpi_cards(snapshot))
    if _is_empty_portfolio(snapshot):
        st.info("Empty paper portfolio — awaiting backend ledger")

    st.subheader("Order Summary")
    render_kpi_row(paper_order_count_cards(snapshot))

    st.subheader("Positions")
    positions_df = _positions_frame(snapshot)
    render_table(positions_df)
    if positions_df.empty:
        st.caption("No open paper positions")

    if snapshot.orders:
        st.subheader("Recent Orders")
        render_table(_orders_frame(snapshot))

    st.subheader("Equity Curve")
    if snapshot.equity_series:
        equity_df = pd.DataFrame(
            snapshot.equity_series,
            columns=["timestamp", "equity"],
        )
        st.plotly_chart(build_equity_curve(equity_df), use_container_width=True)
    else:
        st.caption("Equity curve unavailable — awaiting backend series")


def _enable_paper_trading_autorefresh(ctx: DashboardRenderContext) -> None:
    """Enable one-second Paper Trading autorefresh without trading cycles.

    Prefers ``streamlit-autorefresh`` for a full-script rerun. Falls back to a
    Streamlit ``fragment(run_every=...)`` that re-renders only the page body.
    """
    if not ctx.config.enable_paper_trading_autorefresh:
        _render_paper_body(ctx)
        return

    interval_ms = paper_trading_refresh_interval_ms(ctx.config)
    try:
        from streamlit_autorefresh import st_autorefresh

        st_autorefresh(interval=interval_ms, key="paper_trading_refresh")
        _render_paper_body(ctx)
        return
    except Exception:  # noqa: BLE001 - optional dependency
        pass

    fragment = getattr(st, "fragment", None)
    if fragment is not None:
        seconds = float(ctx.config.paper_trading_refresh_seconds)

        @fragment(run_every=timedelta(seconds=seconds))
        def _paper_trading_fragment() -> None:
            """Re-render Paper Trading body on the refresh interval."""
            _render_paper_body(ctx)

        _paper_trading_fragment()
        return

    _logger.warning(
        "Paper Trading autorefresh unavailable; install streamlit-autorefresh "
        "or use Streamlit >= 1.33 for fragment refresh"
    )
    _render_paper_body(ctx)
    st.caption(
        f"Paper trading refresh interval: {ctx.config.paper_trading_refresh_seconds:.1f}s "
        "(install streamlit-autorefresh for automatic refresh)"
    )


def render(ctx: DashboardRenderContext) -> None:
    """Render the paper trading page.

    Displays portfolio summary, order summary counts, positions, and optional
    equity curve. Read-only — does not place trades or run simulations.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Paper Trading", "Virtual ledger (read-only)")
    _enable_paper_trading_autorefresh(ctx)
