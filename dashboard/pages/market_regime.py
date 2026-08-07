"""Market Regime page — real regime classification (read-only)."""

from __future__ import annotations

import logging

import streamlit as st

from dashboard.components.error_banner import render_error
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import (
    DashboardRenderContext,
    KpiCardModel,
    StrategyMonitorView,
)

_logger = logging.getLogger("dashboard.pages.market_regime")


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Market Regime page body (re-invoked on every live refresh)."""
    try:
        view = ctx.facade.get_strategy_monitor()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("market regime unavailable: %s", exc)
        render_error(f"Market regime unavailable: {exc}")
        view = StrategyMonitorView()

    render_kpi_row(
        (
            KpiCardModel("Current Regime", view.market_regime),
            KpiCardModel("Confidence", view.confidence_score),
            KpiCardModel("Active Strategy", view.active_strategy),
            KpiCardModel("Evaluation Time", view.evaluation_time),
        )
    )

    st.markdown("**Recommendation**")
    if view.recommendation_banner and view.recommendation_banner != "—":
        st.info(view.recommendation_banner)
    else:
        st.caption("No recommendation recorded for the latest evaluation cycle")

    st.markdown("**Per-Strategy Regime Read**")
    if not view.strategies:
        st.caption("No monitored strategies available")
        return
    for row in view.strategies:
        with st.expander(f"{row.display_name} · {row.status}", expanded=False):
            st.caption(f"Family: {row.family} · Confidence: {row.confidence} · Score: {row.score}")
            st.caption(f"Last signal: {row.last_signal} at {row.timestamp}")
            if row.reasons:
                for reason in row.reasons:
                    st.markdown(f"- {reason}")


def render(ctx: DashboardRenderContext) -> None:
    """Render the Market Regime page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Market Regime", "Live regime classification (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="market_regime_refresh",
    )
