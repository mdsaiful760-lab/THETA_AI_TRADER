"""Sidebar navigation, status, and lifecycle controls."""

from __future__ import annotations

import streamlit as st

from dashboard.components.error_banner import render_error
from dashboard.components.status_badges import render_status_badges
from dashboard.session_state import (
    set_active_page,
    set_facade_action_pending,
    set_last_error,
    set_last_refresh_at,
)
from dashboard.view_models import DashboardRenderContext

PAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("home", "Home"),
    ("market", "Market"),
    ("strategy_monitor", "Strategy Monitor"),
    ("paper_trading", "Paper Trading"),
    ("orders", "Orders"),
    ("portfolio", "Portfolio"),
    ("risk", "Risk"),
    ("apme", "APME"),
    ("logs", "Logs"),
    ("analytics", "Analytics"),
    ("settings", "Settings"),
)

PAGE_LABELS: dict[str, str] = dict(PAGE_OPTIONS)
PAGE_IDS: tuple[str, ...] = tuple(page_id for page_id, _ in PAGE_OPTIONS)


def render_sidebar(ctx: DashboardRenderContext) -> None:
    """Render sidebar navigation, status badges, and lifecycle controls.

    Args:
        ctx: Dashboard render context.
    """
    health = ctx.facade.get_health()
    runtime = ctx.facade.get_runtime_state()

    with st.sidebar:
        st.markdown(
            f"<div class='theta-brand'>{ctx.config.app_title}</div>",
            unsafe_allow_html=True,
        )
        st.caption(f"v{ctx.version} · schema {ctx.config.schema_version}")

        labels = [label for _, label in PAGE_OPTIONS]
        ids = [page_id for page_id, _ in PAGE_OPTIONS]
        current_index = ids.index(ctx.session.active_page) if ctx.session.active_page in ids else 0
        selected_label = st.radio(
            "Navigation",
            options=labels,
            index=current_index,
            label_visibility="collapsed",
        )
        selected_page = ids[labels.index(selected_label)]
        if selected_page != ctx.session.active_page:
            set_active_page(selected_page)

        st.divider()
        render_status_badges(
            system_status=health.status,
            broker_status=runtime.broker_status,
            execution_mode=runtime.execution_mode,
            market_status=runtime.market_status,
        )

        st.divider()
        _render_controls(ctx, health.status)

        refresh_label = ctx.session.last_refresh_at or "Never"
        st.caption(f"Last refresh: {refresh_label}")

        if ctx.config.show_demo_banners and not ctx.facade.is_connected:
            st.info("Demo mode — backend session not connected")


def _render_controls(ctx: DashboardRenderContext, system_status: str) -> None:
    """Render Start / Stop / Refresh controls."""
    running = system_status.upper() == "RUNNING"
    stopped = system_status.upper() in {"STOPPED", "DISCONNECTED", "UNKNOWN"}
    connected = ctx.facade.is_connected
    pending = ctx.session.facade_action_pending

    col_start, col_stop, col_refresh = st.columns(3)

    with col_start:
        if st.button(
            "Start",
            disabled=not connected or running or pending,
            help="Backend session not connected" if not connected else None,
            use_container_width=True,
        ):
            _run_facade_action(ctx, "start")

    with col_stop:
        if st.button(
            "Stop",
            disabled=not connected or stopped or pending,
            help="Backend session not connected" if not connected else None,
            use_container_width=True,
        ):
            _run_facade_action(ctx, "stop")

    with col_refresh:
        if st.button("Refresh", disabled=pending, use_container_width=True):
            _run_facade_action(ctx, "refresh")


def _run_facade_action(ctx: DashboardRenderContext, action: str) -> None:
    """Delegate lifecycle action to the backend facade."""
    set_facade_action_pending(True)
    try:
        if action == "start":
            result = ctx.facade.start()
        elif action == "stop":
            result = ctx.facade.stop()
        else:
            result = ctx.facade.refresh_snapshots()
        if result.success:
            set_last_error(None)
            set_last_refresh_at(ctx.clock().isoformat())
            if action == "refresh":
                st.rerun()
        else:
            set_last_error(result.message)
            render_error(result.message)
    finally:
        set_facade_action_pending(False)
