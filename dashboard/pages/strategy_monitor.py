"""Strategy monitor page — read-only strategy evaluation display."""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import (
    STRATEGY_MONITOR_FAMILIES,
    empty_strategy_status,
    strategy_status_to_monitor_view,
)
from dashboard.view_models import (
    DashboardRenderContext,
    PLACEHOLDER,
    StrategyMonitorView,
    strategy_monitor_kpi_cards,
)

_logger = logging.getLogger("dashboard.pages.strategy_monitor")

_TABLE_COLUMNS: tuple[str, ...] = (
    "Strategy",
    "Score",
    "Status",
    "Reason",
    "Eligible / Rejected",
)


def _offline_monitor_view() -> StrategyMonitorView:
    """Return placeholder Strategy Monitor view when facade read fails."""
    return strategy_status_to_monitor_view(empty_strategy_status(source="offline"))


def _resolve_monitor_view(ctx: DashboardRenderContext) -> StrategyMonitorView:
    """Load Strategy Monitor snapshot exclusively via DashboardFacade methods.

    Args:
        ctx: Render context with facade.

    Returns:
        Strategy monitor view (placeholders on failure).
    """
    try:
        getter = getattr(ctx.facade, "get_strategy_monitor", None)
        if callable(getter):
            snapshot = getter()
            if isinstance(snapshot, StrategyMonitorView):
                return snapshot
        status_getter = getattr(ctx.facade, "get_strategy_status", None)
        if callable(status_getter):
            return strategy_status_to_monitor_view(status_getter())
    except Exception as exc:  # noqa: BLE001 - page must not crash
        _logger.warning("strategy monitor unavailable: %s", exc)
        render_error(f"Strategy monitor unavailable: {exc}")
    return _offline_monitor_view()


def _strategy_score_frame(view: StrategyMonitorView) -> pd.DataFrame:
    """Build the four-strategy score table as a DataFrame.

    Args:
        view: Strategy monitor snapshot.

    Returns:
        DataFrame with Strategy / Score / Status / Reason / Eligible columns.
    """
    by_family = {row.family: row for row in view.strategies}
    by_name = {row.display_name: row for row in view.strategies}
    rows: list[dict[str, str]] = []
    for family_id, display_name in STRATEGY_MONITOR_FAMILIES:
        row = by_family.get(family_id) or by_name.get(display_name)
        if row is None:
            rows.append(
                {
                    "Strategy": display_name,
                    "Score": PLACEHOLDER,
                    "Status": PLACEHOLDER,
                    "Reason": PLACEHOLDER,
                    "Eligible / Rejected": PLACEHOLDER,
                }
            )
            continue
        rows.append(
            {
                "Strategy": row.display_name
                if row.display_name and row.display_name != PLACEHOLDER
                else display_name,
                "Score": row.score,
                "Status": row.status,
                "Reason": row.reason
                if row.reason != PLACEHOLDER
                else (", ".join(row.reasons) if row.reasons else PLACEHOLDER),
                "Eligible / Rejected": row.eligibility,
            }
        )
    return pd.DataFrame(rows, columns=list(_TABLE_COLUMNS))


def render(ctx: DashboardRenderContext) -> None:
    """Render the strategy monitor page.

    Displays market regime, active strategy, confidence, evaluation time, and
    the four-strategy score table. Read-only — does not evaluate strategies or
    place orders.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header(
        "Strategy Monitor",
        "Last strategy evaluation snapshot (read-only)",
    )
    snapshot = _resolve_monitor_view(ctx)
    render_kpi_row(strategy_monitor_kpi_cards(snapshot))
    st.caption(
        "Scores and eligibility reflect the latest backend evaluation only — "
        "this page never executes strategies."
    )
    render_table(_strategy_score_frame(snapshot))

    detail_rows = [
        row
        for row in snapshot.strategies
        if row.reasons or (row.reason and row.reason != PLACEHOLDER)
    ]
    if detail_rows:
        with st.expander("Evaluation details"):
            for row in detail_rows:
                label = (
                    row.display_name
                    if row.display_name and row.display_name != PLACEHOLDER
                    else row.strategy_id
                )
                st.markdown(f"**{label}**")
                reasons = row.reasons or (
                    (row.reason,) if row.reason != PLACEHOLDER else ()
                )
                for reason in reasons:
                    st.write(f"- {reason}")
    elif all(
        row.score == PLACEHOLDER and row.status == PLACEHOLDER
        for row in snapshot.strategies
    ):
        st.info("Awaiting backend strategy evaluations")
