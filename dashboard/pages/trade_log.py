"""Trade Log page — real historical fill/terminal-order log (read-only)."""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.page_header import render_page_header
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext, OrdersPageView, PLACEHOLDER

_logger = logging.getLogger("dashboard.pages.trade_log")

_TERMINAL_STATUSES = frozenset(
    {"FILLED", "COMPLETE", "COMPLETED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "FAILED"}
)


def _display(value: object | None) -> str:
    if value is None:
        return PLACEHOLDER
    text = str(value).strip()
    return text if text else PLACEHOLDER


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Trade Log page body (re-invoked on every live refresh)."""
    try:
        getter = getattr(ctx.facade, "get_orders", None)
        view = getter() if callable(getter) else OrdersPageView()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("trade log unavailable: %s", exc)
        render_error(f"Trade log unavailable: {exc}")
        view = OrdersPageView()

    terminal = [order for order in view.orders if order.status.strip().upper() in _TERMINAL_STATUSES]
    st.caption(f"{len(terminal)} completed order(s) in this session's real order history")

    if not terminal:
        render_table(
            pd.DataFrame(columns=["Time", "Order ID", "Strategy", "Instrument", "Side", "Qty", "Fill Price", "Status"])
        )
        st.info(
            "No completed trades yet — this platform's execution history is not "
            "currently exposed by the paper-trading runner beyond live positions/PnL"
        )
        return

    frame = pd.DataFrame(
        [
            (
                _display(order.timestamp), _display(order.order_id), _display(order.strategy),
                _display(order.symbol), _display(order.side), _display(order.quantity),
                _display(order.price), _display(order.status),
            )
            for order in terminal
        ],
        columns=["Time", "Order ID", "Strategy", "Instrument", "Side", "Qty", "Fill Price", "Status"],
    )
    render_table(frame, height=560)


def render(ctx: DashboardRenderContext) -> None:
    """Render the Trade Log page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Trade Log", "Real historical execution log (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="trade_log_refresh",
    )
