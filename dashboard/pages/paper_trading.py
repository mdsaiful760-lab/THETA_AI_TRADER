"""Paper trading page — read-only virtual ledger display."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Sequence

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.facade import NullIntegrationFacade
from dashboard.utils.polling import paper_trading_refresh_interval_ms
from dashboard.view_models import (
    DashboardRenderContext,
    KpiCardModel,
    PLACEHOLDER,
    PaperTradingPageView,
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

_ORDER_COLUMNS: tuple[str, ...] = (
    "Order ID",
    "Symbol",
    "Side",
    "Qty",
    "Status",
    "Timestamp",
)

_TIMELINE_COLUMNS: tuple[str, ...] = (
    "Time",
    "Event",
    "Symbol",
    "Status",
    "Detail",
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
    if not text or text == placeholder:
        return placeholder
    return text


def _attr(obj: object, *names: str, default: object = None) -> object:
    """Soft-read the first present attribute from an object.

    Args:
        obj: Source object.
        *names: Candidate attribute names in priority order.
        default: Fallback when none are present.

    Returns:
        First non-``None`` attribute value, or ``default``.
    """
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _offline_view() -> PaperTradingPageView:
    """Return offline Paper Trading placeholders via null facade."""
    return NullIntegrationFacade().get_paper_trading()


def _resolve_paper_view(ctx: DashboardRenderContext) -> PaperTradingPageView:
    """Load Paper Trading snapshot exclusively via DashboardFacade.

    Prefers ``get_paper_trading()``. Never places trades, calculates PnL, or
    calls brokers.

    Args:
        ctx: Render context with facade.

    Returns:
        Paper trading page view (placeholders on failure).
    """
    try:
        getter = getattr(ctx.facade, "get_paper_trading", None)
        if callable(getter):
            snapshot = getter()
            if isinstance(snapshot, PaperTradingPageView):
                return snapshot
        render_error("Paper trading unavailable: get_paper_trading missing")
    except Exception as exc:  # noqa: BLE001 - page must not crash
        _logger.warning("paper trading ledger unavailable: %s", exc)
        render_error(f"Paper trading data unavailable: {exc}")
    return _offline_view()


def _account_summary_cards(view: PaperTradingPageView) -> tuple[KpiCardModel, ...]:
    """Build paper account summary KPI cards.

    Args:
        view: Paper trading page snapshot.

    Returns:
        Cash, Used Margin, Available Margin, Equity, Today's PnL cards.
    """
    cash = _display(
        _attr(view, "cash", "available_cash", "virtual_cash", default=PLACEHOLDER)
    )
    used_margin = _display(
        _attr(view, "used_margin", "capital_used", default=PLACEHOLDER)
    )
    available_margin = _display(
        _attr(view, "available_margin", default=None)
    )
    if available_margin == PLACEHOLDER:
        # Soft-read only — reuse available cash when dedicated margin is absent.
        available_margin = _display(
            _attr(view, "available_cash", "virtual_cash", default=PLACEHOLDER)
        )
    equity = _display(_attr(view, "equity", "total_equity", default=PLACEHOLDER))
    todays_pnl = _display(
        _attr(view, "todays_pnl", "today_pnl", "daily_pnl", default=PLACEHOLDER)
    )
    return (
        KpiCardModel("Cash", cash),
        KpiCardModel("Used Margin", used_margin),
        KpiCardModel("Available Margin", available_margin),
        KpiCardModel("Equity", equity),
        KpiCardModel("Today's PnL", todays_pnl),
    )


def _positions_frame(view: PaperTradingPageView) -> pd.DataFrame:
    """Build the open positions DataFrame.

    Args:
        view: Paper trading page snapshot.

    Returns:
        Positions table (empty with headers when offline / empty).
    """
    rows: list[dict[str, str]] = []
    for row in view.positions:
        rows.append(
            {
                "Symbol": _display(_attr(row, "symbol", default=PLACEHOLDER)),
                "Strategy": _display(_attr(row, "strategy", default=PLACEHOLDER)),
                "Qty": _display(_attr(row, "quantity", "qty", default=PLACEHOLDER)),
                "Entry": _display(
                    _attr(row, "entry", "avg_price", default=PLACEHOLDER)
                ),
                "Current": _display(
                    _attr(row, "current", "mark", default=PLACEHOLDER)
                ),
                "MTM": _display(_attr(row, "mtm", "pnl", default=PLACEHOLDER)),
                "Status": _display(_attr(row, "status", default=PLACEHOLDER)),
            }
        )
    if not rows:
        return pd.DataFrame(columns=list(_POSITION_COLUMNS))
    return pd.DataFrame(rows, columns=list(_POSITION_COLUMNS))


def _orders_frame(view: PaperTradingPageView) -> pd.DataFrame:
    """Build the paper orders DataFrame.

    Args:
        view: Paper trading page snapshot.

    Returns:
        Orders table (empty with headers when offline / empty).
    """
    rows: list[dict[str, str]] = []
    for order in view.orders:
        rows.append(
            {
                "Order ID": _display(_attr(order, "order_id", default=PLACEHOLDER)),
                "Symbol": _display(_attr(order, "symbol", default=PLACEHOLDER)),
                "Side": _display(_attr(order, "side", default=PLACEHOLDER)),
                "Qty": _display(
                    _attr(order, "quantity", "qty", default=PLACEHOLDER)
                ),
                "Status": _display(_attr(order, "status", default=PLACEHOLDER)),
                "Timestamp": _display(
                    _attr(order, "timestamp", "time", default=PLACEHOLDER)
                ),
            }
        )
    if not rows:
        return pd.DataFrame(columns=list(_ORDER_COLUMNS))
    return pd.DataFrame(rows, columns=list(_ORDER_COLUMNS))


def _timeline_rows_from_orders(view: PaperTradingPageView) -> list[dict[str, str]]:
    """Map already-computed orders into timeline display rows.

    Args:
        view: Paper trading page snapshot.

    Returns:
        Timeline row dicts (display-only; no new events invented).
    """
    rows: list[dict[str, str]] = []
    for order in view.orders:
        status = _display(_attr(order, "status", default=PLACEHOLDER))
        rows.append(
            {
                "Time": _display(
                    _attr(order, "timestamp", "time", default=PLACEHOLDER)
                ),
                "Event": "ORDER" if status == PLACEHOLDER else f"ORDER_{status}",
                "Symbol": _display(_attr(order, "symbol", default=PLACEHOLDER)),
                "Status": status,
                "Detail": _display(
                    _attr(
                        order,
                        "detail",
                        "message",
                        "side",
                        default=PLACEHOLDER,
                    )
                ),
            }
        )
    return rows


def _timeline_rows_from_events(events: Sequence[Any]) -> list[dict[str, str]]:
    """Map an upstream execution-timeline sequence into display rows.

    Args:
        events: Upstream timeline event objects or mappings.

    Returns:
        Timeline row dicts.
    """
    rows: list[dict[str, str]] = []
    try:
        for event in events:
            if isinstance(event, dict):
                rows.append(
                    {
                        "Time": _display(
                            event.get("time") or event.get("timestamp")
                        ),
                        "Event": _display(
                            event.get("event") or event.get("event_type")
                        ),
                        "Symbol": _display(event.get("symbol")),
                        "Status": _display(event.get("status")),
                        "Detail": _display(
                            event.get("detail") or event.get("message")
                        ),
                    }
                )
                continue
            rows.append(
                {
                    "Time": _display(
                        _attr(event, "time", "timestamp", default=PLACEHOLDER)
                    ),
                    "Event": _display(
                        _attr(
                            event,
                            "event",
                            "event_type",
                            "name",
                            default=PLACEHOLDER,
                        )
                    ),
                    "Symbol": _display(
                        _attr(event, "symbol", default=PLACEHOLDER)
                    ),
                    "Status": _display(
                        _attr(event, "status", default=PLACEHOLDER)
                    ),
                    "Detail": _display(
                        _attr(
                            event,
                            "detail",
                            "message",
                            "description",
                            default=PLACEHOLDER,
                        )
                    ),
                }
            )
    except TypeError:
        return []
    return rows


def _execution_timeline_frame(
    view: PaperTradingPageView,
    facade: object,
) -> pd.DataFrame:
    """Build the execution timeline DataFrame from facade soft-reads.

    Prefers an explicit timeline on the view or facade accessors; otherwise
    displays order timestamps as a read-only timeline.

    Args:
        view: Paper trading page snapshot.
        facade: Dashboard facade handle.

    Returns:
        Timeline table (empty with headers when unavailable).
    """
    events = _attr(view, "execution_timeline", "timeline", "events", default=None)
    if events is None:
        for method_name in (
            "get_paper_execution_timeline",
            "get_execution_timeline",
            "get_paper_timeline",
        ):
            getter = getattr(facade, method_name, None)
            if not callable(getter):
                continue
            try:
                payload = getter()
            except Exception:  # noqa: BLE001 - optional soft-read
                continue
            if payload is None:
                continue
            events = _attr(payload, "events", "timeline", "rows", default=payload)
            break

    rows: list[dict[str, str]] = []
    if events is not None:
        rows = _timeline_rows_from_events(events)  # type: ignore[arg-type]
    if not rows:
        rows = _timeline_rows_from_orders(view)
    if not rows:
        return pd.DataFrame(columns=list(_TIMELINE_COLUMNS))
    return pd.DataFrame(rows, columns=list(_TIMELINE_COLUMNS))


def _performance_summary_cards(
    view: PaperTradingPageView,
    facade: object,
) -> tuple[KpiCardModel, ...]:
    """Build performance summary KPI cards from soft-read facade fields.

    Never computes win rate, expectancy, or related metrics in the page.

    Args:
        view: Paper trading page snapshot.
        facade: Dashboard facade handle.

    Returns:
        Win Rate, Average Winner, Average Loser, Profit Factor, Expectancy.
    """
    analytics: object | None = None
    getter = getattr(facade, "get_analytics", None)
    if callable(getter):
        try:
            analytics = getter()
        except Exception:  # noqa: BLE001 - optional soft-read
            analytics = None

    def _metric(*names: str) -> str:
        for name in names:
            value = _attr(view, name, default=None)
            if value is not None and _display(value) != PLACEHOLDER:
                return _display(value)
            if analytics is not None:
                value = _attr(analytics, name, default=None)
                if value is not None and _display(value) != PLACEHOLDER:
                    return _display(value)
        return PLACEHOLDER

    return (
        KpiCardModel("Win Rate", _metric("win_rate")),
        KpiCardModel("Average Winner", _metric("average_winner", "avg_winner")),
        KpiCardModel("Average Loser", _metric("average_loser", "avg_loser")),
        KpiCardModel("Profit Factor", _metric("profit_factor")),
        KpiCardModel("Expectancy", _metric("expectancy")),
    )


def _is_empty_account(view: PaperTradingPageView) -> bool:
    """Return True when account summary values are all placeholders."""
    cards = _account_summary_cards(view)
    return all(card.value == PLACEHOLDER for card in cards) and not view.positions


def _render_paper_body(ctx: DashboardRenderContext) -> None:
    """Render Paper Trading body panels (re-read on every call).

    Args:
        ctx: Immutable render context with facade.
    """
    snapshot = _resolve_paper_view(ctx)

    st.subheader("Paper account summary")
    render_kpi_row(_account_summary_cards(snapshot))
    if _is_empty_account(snapshot):
        st.info("Empty paper portfolio — awaiting backend ledger")

    st.subheader("Open Positions")
    positions_df = _positions_frame(snapshot)
    render_table(positions_df)
    if positions_df.empty:
        st.caption("No open paper positions")

    st.subheader("Paper Orders")
    orders_df = _orders_frame(snapshot)
    render_table(orders_df)
    if orders_df.empty:
        st.caption("No paper orders")

    st.subheader("Execution timeline")
    timeline_df = _execution_timeline_frame(snapshot, ctx.facade)
    render_table(timeline_df)
    if timeline_df.empty:
        st.caption("No execution timeline events")

    st.subheader("Performance summary")
    render_kpi_row(_performance_summary_cards(snapshot, ctx.facade))
    if all(
        card.value == PLACEHOLDER
        for card in _performance_summary_cards(snapshot, ctx.facade)
    ):
        st.caption("Performance metrics unavailable — awaiting backend aggregates")

    if snapshot.source == "offline" and _is_empty_account(snapshot):
        st.caption("Offline mode — awaiting backend paper trading ledger")


def _enable_paper_trading_autorefresh(ctx: DashboardRenderContext) -> None:
    """Enable Paper Trading autorefresh without trading cycles.

    Prefers ``streamlit-autorefresh`` for a full-script rerun. Falls back to a
    Streamlit ``fragment(run_every=...)`` that re-renders only the page body.

    Args:
        ctx: Immutable render context with facade and config.
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

    Displays account summary, open positions, paper orders, execution timeline,
    and performance summary. Read-only — does not place trades, calculate PnL,
    or access brokers.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Paper Trading", "Virtual ledger (read-only)")
    _enable_paper_trading_autorefresh(ctx)


__all__ = (
    "render",
    "_resolve_paper_view",
    "_account_summary_cards",
    "_positions_frame",
    "_orders_frame",
    "_execution_timeline_frame",
    "_performance_summary_cards",
)
