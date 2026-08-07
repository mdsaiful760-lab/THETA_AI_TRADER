"""Paper trading page — read-only virtual ledger display."""

from __future__ import annotations

import logging
from typing import Any, Sequence

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.components.plotly_charts import build_drawdown, build_equity_curve
from dashboard.facade import NullIntegrationFacade
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import (
    DashboardRenderContext,
    KpiCardModel,
    PLACEHOLDER,
    PaperTradingPageView,
    paper_trading_position_summary_cards,
    paper_trading_runner_status_cards,
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


def _resolve_analytics(facade: object) -> object | None:
    """Soft-read the optional analytics snapshot from the facade.

    Never places trades or computes metrics; a pure pass-through read.

    Args:
        facade: Dashboard facade handle.

    Returns:
        Analytics object, or ``None`` when unavailable.
    """
    getter = getattr(facade, "get_analytics", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 - optional soft-read
        return None


def _soft_metric(
    view: object,
    analytics: object,
    *names: str,
) -> str:
    """Soft-read the first matching metric name from ``view``, then ``analytics``.

    Never computes derived metrics; only reads already-computed upstream values.

    Args:
        view: Primary display view (already-computed upstream fields).
        analytics: Secondary soft-read analytics object.
        *names: Candidate attribute names in priority order.

    Returns:
        First non-placeholder display value found, else ``PLACEHOLDER``.
    """
    for name in names:
        value = _attr(view, name, default=None)
        if value is not None and _display(value) != PLACEHOLDER:
            return _display(value)
        if analytics is not None:
            value = _attr(analytics, name, default=None)
            if value is not None and _display(value) != PLACEHOLDER:
                return _display(value)
    return PLACEHOLDER


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
    analytics = _resolve_analytics(facade)
    return (
        KpiCardModel("Win Rate", _soft_metric(view, analytics, "win_rate")),
        KpiCardModel(
            "Average Winner",
            _soft_metric(view, analytics, "average_winner", "avg_winner"),
        ),
        KpiCardModel(
            "Average Loser",
            _soft_metric(view, analytics, "average_loser", "avg_loser"),
        ),
        KpiCardModel("Profit Factor", _soft_metric(view, analytics, "profit_factor")),
        KpiCardModel("Expectancy", _soft_metric(view, analytics, "expectancy")),
    )


def _performance_kpi_cards(
    view: PaperTradingPageView,
    analytics: object,
) -> tuple[KpiCardModel, ...]:
    """Build the top Performance KPI row.

    Never computes PnL, win rate, or profit factor in the page.

    Args:
        view: Paper trading page snapshot.
        analytics: Soft-read analytics object from ``facade.get_analytics()``.

    Returns:
        Account Balance, Today's P&L, Total P&L, Win Rate, Profit Factor cards.
    """
    return (
        KpiCardModel(
            "Account Balance", _soft_metric(view, analytics, "total_equity", "equity")
        ),
        KpiCardModel(
            "Today's P&L", _soft_metric(view, analytics, "todays_pnl", "today_pnl")
        ),
        KpiCardModel("Total P&L", _soft_metric(view, analytics, "total_pnl", "net_pnl")),
        KpiCardModel("Win Rate", _soft_metric(view, analytics, "win_rate")),
        KpiCardModel("Profit Factor", _soft_metric(view, analytics, "profit_factor")),
    )


def _statistics_cards(
    view: PaperTradingPageView,
    analytics: object,
) -> tuple[KpiCardModel, ...]:
    """Build the Statistics KPI cards from soft-read facade/analytics fields.

    Never computes win rate, expectancy, or related metrics in the page.

    Args:
        view: Paper trading page snapshot.
        analytics: Soft-read analytics object from ``facade.get_analytics()``.

    Returns:
        Eight statistic KPI cards in display order.
    """
    return (
        KpiCardModel(
            "Average Winner",
            _soft_metric(view, analytics, "average_winner", "avg_winner"),
        ),
        KpiCardModel(
            "Average Loser",
            _soft_metric(view, analytics, "average_loser", "avg_loser"),
        ),
        KpiCardModel(
            "Largest Win", _soft_metric(view, analytics, "largest_win", "max_win")
        ),
        KpiCardModel(
            "Largest Loss", _soft_metric(view, analytics, "largest_loss", "max_loss")
        ),
        KpiCardModel("Expectancy", _soft_metric(view, analytics, "expectancy")),
        KpiCardModel(
            "Risk Reward",
            _soft_metric(view, analytics, "risk_reward", "risk_reward_ratio"),
        ),
        KpiCardModel("Sharpe", _soft_metric(view, analytics, "sharpe", "sharpe_ratio")),
        KpiCardModel(
            "Max Drawdown",
            _soft_metric(view, analytics, "max_drawdown", "max_dd"),
        ),
    )


def _is_empty_account(view: PaperTradingPageView) -> bool:
    """Return True when account summary values are all placeholders."""
    cards = _account_summary_cards(view)
    return all(card.value == PLACEHOLDER for card in cards) and not view.positions


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


def _filter_trade_history(
    df: pd.DataFrame,
    *,
    search: str = "",
    statuses: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Filter the trade history table by free-text search and status.

    Pure display filter — never mutates ``df`` or recomputes trade fields.

    Args:
        df: Source trade history dataframe.
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
            lambda row: row.astype(str).str.lower().str.contains(term, regex=False).any(),
            axis=1,
        )
        filtered = filtered[mask]
    return filtered.reset_index(drop=True)


def _trade_history_csv(df: pd.DataFrame) -> str:
    """Serialize the trade history table to CSV text.

    Args:
        df: Trade history dataframe.

    Returns:
        UTF-8 CSV text without the pandas index column.
    """
    return df.to_csv(index=False)


def _render_trade_history_section(view: PaperTradingPageView) -> None:
    """Render the searchable, filterable, downloadable trade history table.

    Sorting is provided natively by the underlying table widget. Read-only —
    never places, cancels, or recomputes trades.

    Args:
        view: Paper trading page snapshot.
    """
    st.subheader("Trade History")
    trades_df = _orders_frame(view)

    search = ""
    statuses: list[str] = []
    try:
        search_col, filter_col = st.columns([2, 1])
        with search_col:
            search_raw = st.text_input(
                "Search trades", key="paper_trade_history_search"
            )
        with filter_col:
            status_options = sorted(trades_df["Status"].unique().tolist())
            statuses_raw = st.multiselect(
                "Filter by status",
                options=status_options,
                default=[],
                key="paper_trade_history_status_filter",
            )
        search = str(search_raw) if isinstance(search_raw, str) else ""
        statuses = list(statuses_raw) if isinstance(statuses_raw, (list, tuple)) else []
        filtered_df = _filter_trade_history(trades_df, search=search, statuses=statuses)
    except Exception:  # noqa: BLE001 - page must not crash
        filtered_df = trades_df

    render_table(filtered_df)
    if trades_df.empty:
        st.caption("No trade history — awaiting backend orders")
    elif filtered_df.empty:
        st.caption("No trades match the current search/filter")

    st.download_button(
        "Download CSV",
        data=_trade_history_csv(filtered_df),
        file_name="paper_trade_history.csv",
        mime="text/csv",
        key="paper_trade_history_download",
    )


def _render_paper_body(ctx: DashboardRenderContext) -> None:
    """Render Paper Trading body panels (re-read on every call).

    Args:
        ctx: Immutable render context with facade.
    """
    snapshot = _resolve_paper_view(ctx)
    analytics = _resolve_analytics(ctx.facade)

    st.subheader("Paper account summary")
    render_kpi_row(_account_summary_cards(snapshot))
    if _is_empty_account(snapshot):
        st.info("Empty paper portfolio — awaiting backend ledger")

    st.subheader("Open Positions")
    positions_df = _positions_frame(snapshot)
    render_table(positions_df)
    if positions_df.empty:
        st.caption("No open paper positions")

    st.subheader("Execution timeline")
    timeline_df = _execution_timeline_frame(snapshot, ctx.facade)
    render_table(timeline_df)
    if timeline_df.empty:
        st.caption("No execution timeline events")

    st.subheader("Performance KPIs")
    render_kpi_row(_performance_kpi_cards(snapshot, analytics))

    st.subheader("Equity Curve")
    equity_df = _series_frame(snapshot.equity_series, "equity")
    st.plotly_chart(build_equity_curve(equity_df), use_container_width=True)
    if equity_df.empty:
        st.caption("Equity curve unavailable — awaiting backend series")

    st.subheader("Drawdown")
    drawdown_df = _series_frame(snapshot.drawdown_series, "drawdown")
    st.plotly_chart(build_drawdown(drawdown_df), use_container_width=True)
    if drawdown_df.empty:
        st.caption("Drawdown chart unavailable — awaiting backend series")

    st.subheader("Position Summary")
    render_kpi_row(paper_trading_position_summary_cards(snapshot))

    _render_trade_history_section(snapshot)

    st.subheader("Statistics")
    render_kpi_row(_statistics_cards(snapshot, analytics))
    if all(card.value == PLACEHOLDER for card in _statistics_cards(snapshot, analytics)):
        st.caption("Statistics unavailable — awaiting backend aggregates")

    st.subheader("Runner Status")
    render_kpi_row(paper_trading_runner_status_cards(snapshot))

    if snapshot.source == "offline" and _is_empty_account(snapshot):
        st.caption("Offline mode — awaiting backend paper trading ledger")


def _enable_paper_trading_autorefresh(ctx: DashboardRenderContext) -> None:
    """Live-refresh the Paper Trading body in place — never the whole page.

    Args:
        ctx: Immutable render context with facade and config.
    """
    if not ctx.config.enable_paper_trading_autorefresh:
        _render_paper_body(ctx)
        return
    live_fragment(
        lambda: _render_paper_body(ctx),
        interval_seconds=ctx.config.paper_trading_refresh_seconds,
        key="paper_trading_refresh",
    )


def render(ctx: DashboardRenderContext) -> None:
    """Render the paper trading page.

    Displays account summary, open positions, execution timeline, performance
    KPIs, equity/drawdown charts, position summary, a searchable/filterable
    trade history table with CSV export, statistics, and runner status.
    Read-only — does not place trades, calculate PnL, or access brokers.

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
    "_performance_kpi_cards",
    "_statistics_cards",
    "_series_frame",
    "_filter_trade_history",
    "_trade_history_csv",
)
