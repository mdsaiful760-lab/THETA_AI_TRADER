"""Position Sizing page — real, already-computed sizing/risk-budget output."""

from __future__ import annotations

import logging

import streamlit as st

from dashboard.components.error_banner import render_error
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext, KpiCardModel, PLACEHOLDER

_logger = logging.getLogger("dashboard.pages.position_sizing")


def _fmt(value: object) -> str:
    if value is None:
        return PLACEHOLDER
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Position Sizing page body (re-invoked on every live refresh)."""
    getter = getattr(ctx.facade, "get_ai_panel", None)
    try:
        panel = getter() if callable(getter) else None
    except Exception as exc:  # noqa: BLE001
        _logger.warning("position sizing unavailable: %s", exc)
        render_error(f"Position sizing unavailable: {exc}")
        panel = None

    if panel is None:
        st.info("Awaiting backend AI decision loop")
        return

    render_kpi_row(
        (
            KpiCardModel("Final Lots", _fmt(getattr(panel, "final_lots", None))),
            KpiCardModel("Final Quantity", _fmt(getattr(panel, "final_quantity", None))),
            KpiCardModel(
                "Approved Risk Budget", _fmt(getattr(panel, "approved_risk_budget", None))
            ),
            KpiCardModel("Approved Risk %", _fmt(getattr(panel, "approved_risk_pct", None))),
        )
    )

    st.markdown("**Sizing Reason**")
    reason = getattr(panel, "sizing_reason", PLACEHOLDER)
    if reason and reason != PLACEHOLDER:
        st.info(reason)
    else:
        st.caption("No sizing reason recorded for the latest cycle")

    st.markdown("**Cycle Context**")
    st.caption(
        f"Strategy: {getattr(panel, 'strategy_id', PLACEHOLDER)} · "
        f"Risk verdict: {getattr(panel, 'risk_verdict', PLACEHOLDER)} · "
        f"Decision: {getattr(panel, 'decision_status', PLACEHOLDER)}"
    )


def render(ctx: DashboardRenderContext) -> None:
    """Render the Position Sizing page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Position Sizing", "Real, already-computed sizing output (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="position_sizing_refresh",
    )
