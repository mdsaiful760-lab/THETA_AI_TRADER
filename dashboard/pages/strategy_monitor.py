"""Strategy monitor page — read-only strategy evaluation display."""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.dashboard_facade import STRATEGY_MONITOR_FAMILIES
from dashboard.facade import NullIntegrationFacade
from dashboard.view_models import (
    DashboardRenderContext,
    PLACEHOLDER,
    StrategyMonitorView,
    StrategyRowView,
    resolve_selected_strategy,
    selected_strategy_detail_cards,
    strategy_monitor_kpi_cards,
)

_logger = logging.getLogger("dashboard.pages.strategy_monitor")

_RANKING_COLUMNS: tuple[str, ...] = (
    "Rank",
    "Strategy",
    "Score",
    "Status",
    "Reason",
    "Eligible / Rejected",
)

_GATE_COLUMNS: tuple[str, ...] = ("Gate", "Outcome", "Detail")

_LEG_COLUMNS: tuple[str, ...] = (
    "Side",
    "Type",
    "Strike",
    "Qty",
    "Symbol",
    "Delta",
)


def _offline_monitor_view() -> StrategyMonitorView:
    """Return placeholder Strategy Monitor view when facade read fails."""
    return NullIntegrationFacade().get_strategy_monitor()


def _resolve_monitor_view(ctx: DashboardRenderContext) -> StrategyMonitorView:
    """Load Strategy Monitor snapshot exclusively via DashboardFacade.

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
        render_error("Strategy monitor unavailable: get_strategy_monitor missing")
    except Exception as exc:  # noqa: BLE001 - page must not crash
        _logger.warning("strategy monitor unavailable: %s", exc)
        render_error(f"Strategy monitor unavailable: {exc}")
    return _offline_monitor_view()


def _lookup_row(
    view: StrategyMonitorView,
    family_id: str,
    display_name: str,
) -> StrategyRowView | None:
    """Find a strategy row by family id or display name.

    Args:
        view: Strategy monitor snapshot.
        family_id: Canonical family identifier.
        display_name: Human-readable strategy name.

    Returns:
        Matching row, or ``None`` when absent.
    """
    by_family = {row.family: row for row in view.strategies}
    by_name = {row.display_name: row for row in view.strategies}
    return by_family.get(family_id) or by_name.get(display_name)


def _strategy_ranking_frame(view: StrategyMonitorView) -> pd.DataFrame:
    """Build the strategy ranking table as a DataFrame.

    Args:
        view: Strategy monitor snapshot.

    Returns:
        DataFrame with Rank / Strategy / Score / Status / Reason / Eligible.
    """
    rows: list[dict[str, str]] = []
    for family_id, display_name in STRATEGY_MONITOR_FAMILIES:
        row = _lookup_row(view, family_id, display_name)
        if row is None:
            rows.append(
                {
                    "Rank": PLACEHOLDER,
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
                "Rank": row.rank,
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
    return pd.DataFrame(rows, columns=list(_RANKING_COLUMNS))


def _gates_frame(row: StrategyRowView | None) -> pd.DataFrame:
    """Build the gate evaluation table for the selected strategy.

    Args:
        row: Selected strategy row, or ``None``.

    Returns:
        Gate table (empty with headers when no gates are available).
    """
    if row is None or not row.gates:
        return pd.DataFrame(columns=list(_GATE_COLUMNS))
    return pd.DataFrame(
        [
            {
                "Gate": gate.name,
                "Outcome": gate.outcome,
                "Detail": gate.detail,
            }
            for gate in row.gates
        ],
        columns=list(_GATE_COLUMNS),
    )


def _legs_frame(row: StrategyRowView | None) -> pd.DataFrame:
    """Build the recommended option legs table for the selected strategy.

    Args:
        row: Selected strategy row, or ``None``.

    Returns:
        Legs table (empty with headers when no legs are available).
    """
    if row is None or not row.legs:
        return pd.DataFrame(columns=list(_LEG_COLUMNS))
    return pd.DataFrame(
        [
            {
                "Side": leg.side,
                "Type": leg.option_type,
                "Strike": leg.strike,
                "Qty": leg.quantity,
                "Symbol": leg.symbol,
                "Delta": leg.delta,
            }
            for leg in row.legs
        ],
        columns=list(_LEG_COLUMNS),
    )


def _render_recommendation_banner(view: StrategyMonitorView) -> None:
    """Render the recommendation banner from facade text.

    Offline / missing banner values render as the configured placeholder (``—``).

    Args:
        view: Strategy monitor snapshot.
    """
    banner = view.recommendation_banner or PLACEHOLDER
    if banner == PLACEHOLDER:
        st.info(PLACEHOLDER)
        return
    lowered = banner.lower()
    if "reject" in lowered or "no trade" in lowered or "abstain" in lowered:
        st.warning(banner)
        return
    if "eligible" in lowered or "recommended" in lowered or "enter" in lowered:
        st.success(banner)
        return
    st.info(banner)


def _render_selected_details(view: StrategyMonitorView) -> StrategyRowView | None:
    """Render selected strategy controls and detail KPIs.

    Args:
        view: Strategy monitor snapshot.

    Returns:
        The selected strategy row used for gates/legs panels.
    """
    options = [
        row.display_name
        if row.display_name and row.display_name != PLACEHOLDER
        else row.family
        for row in view.strategies
    ] or [name for _, name in STRATEGY_MONITOR_FAMILIES]

    default_row = resolve_selected_strategy(view)
    default_name = (
        default_row.display_name
        if default_row is not None
        and default_row.display_name
        and default_row.display_name != PLACEHOLDER
        else options[0]
    )
    try:
        default_index = options.index(default_name)
    except ValueError:
        default_index = 0

    st.subheader("Selected strategy details")
    selected_name = st.selectbox(
        "Selected strategy",
        options=options,
        index=default_index,
        help="Display-only selection over the latest facade evaluation snapshot.",
    )
    selected = resolve_selected_strategy(view, selected_display_name=selected_name)
    if selected is None:
        st.info("No strategy details available")
        return None

    render_kpi_row(selected_strategy_detail_cards(selected))
    if selected.detail_summary and selected.detail_summary != PLACEHOLDER:
        st.caption(selected.detail_summary)
    if selected.recommendation_state and selected.recommendation_state != PLACEHOLDER:
        st.caption(f"Recommendation state: {selected.recommendation_state}")

    reasons = selected.reasons or (
        (selected.reason,) if selected.reason != PLACEHOLDER else ()
    )
    if reasons:
        with st.expander("Evaluation reasons", expanded=False):
            for reason in reasons:
                st.write(f"- {reason}")
    return selected


def render(ctx: DashboardRenderContext) -> None:
    """Render the strategy monitor page.

    Displays recommendation banner, header KPIs, ranking table, selected
    strategy details, gate evaluation, and recommended option legs. Read-only —
    does not evaluate strategies or place orders.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header(
        "Strategy Monitor",
        "Last strategy evaluation snapshot (read-only)",
    )
    snapshot = _resolve_monitor_view(ctx)

    _render_recommendation_banner(snapshot)
    render_kpi_row(strategy_monitor_kpi_cards(snapshot))
    st.caption(
        "Scores, gates, and legs reflect the latest backend evaluation only — "
        "this page never executes strategies."
    )

    st.subheader("Strategy ranking")
    render_table(_strategy_ranking_frame(snapshot))

    selected = _render_selected_details(snapshot)

    st.subheader("Gate evaluation")
    gates = _gates_frame(selected)
    if gates.empty:
        st.info("Awaiting gate evaluation for the selected strategy")
    render_table(gates)

    st.subheader("Recommended option legs")
    legs = _legs_frame(selected)
    if legs.empty:
        st.info("No recommended option legs in the latest evaluation snapshot")
    render_table(legs)

    if snapshot.source == "offline" and all(
        row.score == PLACEHOLDER and row.status == PLACEHOLDER
        for row in snapshot.strategies
    ):
        st.caption("Offline mode — awaiting backend strategy evaluations")


__all__ = (
    "render",
    "_resolve_monitor_view",
    "_strategy_ranking_frame",
    "_gates_frame",
    "_legs_frame",
)
