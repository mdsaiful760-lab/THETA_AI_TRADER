"""Engine Status page — real component health and measured response times."""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error
from dashboard.components.page_header import render_page_header
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardOverviewView, DashboardRenderContext

_logger = logging.getLogger("dashboard.pages.engine_status")


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the Engine Status page body (re-invoked on every live refresh)."""
    getter = getattr(ctx.facade, "get_dashboard_overview", None)
    try:
        overview = getter() if callable(getter) else DashboardOverviewView()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("engine status unavailable: %s", exc)
        render_error(f"Engine status unavailable: {exc}")
        overview = DashboardOverviewView()

    col1, col2, col3 = st.columns(3)
    col1.metric("Overall Health", overview.engines_overall_health)
    healthy = sum(1 for row in overview.engines if row.state == "healthy")
    down = sum(1 for row in overview.engines if row.state == "down")
    col2.metric("Healthy", f"{healthy} / {len(overview.engines)}")
    col3.metric("Down", f"{down} / {len(overview.engines)}")

    if not overview.engines:
        st.info("No engine handles registered")
        return

    state_label = {"healthy": "🟢 Healthy", "degraded": "🟡 Degraded", "down": "🔴 Down"}
    frame = pd.DataFrame(
        [
            (
                row.name,
                state_label.get(row.state, "🔴 Down"),
                row.latency_display,
                row.heartbeat,
            )
            for row in overview.engines
        ],
        columns=["Component", "State", "Response Time", "Last Heartbeat"],
    )
    render_table(frame)
    st.caption(
        "Response Time is the real measured round-trip of this dashboard's own "
        "read call for that data domain (cache-warmed when applicable) — not a "
        "raw broker/engine-internal timer."
    )


def render(ctx: DashboardRenderContext) -> None:
    """Render the Engine Status page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Engine Status", "Real component health (read-only)")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="engine_status_refresh",
    )
