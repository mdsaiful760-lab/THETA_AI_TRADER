"""Grouped sidebar navigation, status, and lifecycle controls."""

from __future__ import annotations

import getpass
import html

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

# (group_label, ((page_id, display_label, icon), ...)) — group_label is
# ``None`` for the standalone top-level "Dashboard" entry.
NAV_GROUPS: tuple[tuple[str | None, tuple[tuple[str, str, str], ...]], ...] = (
    (None, (("home", "Dashboard", "🏠"),)),
    (
        "Market Intelligence",
        (
            ("market_regime", "Market Regime", "🧭"),
            ("greeks", "Greeks Intelligence", "🔬"),
            ("liquidity", "Liquidity Analysis", "💧"),
            ("volatility", "Volatility Surface", "📉"),
            ("option_chain", "Option Chain", "⛓️"),
            ("heatmap", "Market Heatmap", "🔥"),
        ),
    ),
    (
        "Strategy Intelligence",
        (
            ("scanner", "Strategy Scanner", "🔍"),
            ("builder", "Strategy Builder", "🛠️"),
            ("backtesting", "Backtesting", "⏱️"),
            ("library", "Strategy Library", "📚"),
        ),
    ),
    (
        "Risk & Portfolio",
        (
            ("risk_dashboard", "Risk Dashboard", "⚠️"),
            ("position_sizing", "Position Sizing", "⚖️"),
            ("portfolio", "Portfolio Overview", "💼"),
            ("exposure", "Exposure Analysis", "🎯"),
        ),
    ),
    (
        "Execution",
        (
            ("trade_execution", "Trade Execution", "⚡"),
            ("orders", "Orders", "🧾"),
            ("positions", "Positions", "📍"),
            ("trade_log", "Trade Log", "📜"),
        ),
    ),
    (
        "System",
        (
            ("engine_status", "Engine Status", "🖥️"),
            ("logs", "Logs", "🗒️"),
            ("settings", "Settings", "⚙️"),
        ),
    ),
)

PAGE_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (page_id, label) for _group, items in NAV_GROUPS for page_id, label, _icon in items
)
PAGE_LABELS: dict[str, str] = dict(PAGE_OPTIONS)
PAGE_IDS: tuple[str, ...] = tuple(page_id for page_id, _ in PAGE_OPTIONS)


def render_sidebar(ctx: DashboardRenderContext) -> None:
    """Render grouped sidebar navigation, status badges, and lifecycle controls.

    Args:
        ctx: Dashboard render context.
    """
    health = ctx.facade.get_health()
    runtime = ctx.facade.get_runtime_state()
    active_page = ctx.session.active_page

    with st.sidebar:
        st.markdown(
            (
                "<div class='theta-topbar-brand' style='margin-bottom:0.4rem;'>"
                "<span class='theta-brand-mark'>&#920;</span>"
                f"<div><div class='theta-brand'>{html.escape(ctx.config.app_title)}</div>"
                f"<span style='color:var(--theta-muted-dim);font-size:0.68rem;'>"
                f"v{ctx.version}</span></div>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )

        for group_label, items in NAV_GROUPS:
            if group_label is not None:
                st.markdown(
                    f"<div class='theta-sidebar-group-label'>{html.escape(group_label)}</div>",
                    unsafe_allow_html=True,
                )
            for page_id, label, icon in items:
                is_active = page_id == active_page
                if st.button(
                    f"{icon}  {label}",
                    key=f"nav_{page_id}",
                    type="primary" if is_active else "secondary",
                    use_container_width=True,
                ):
                    if page_id != active_page:
                        set_active_page(page_id)
                        st.rerun()

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

        _render_user_footer()


def _render_user_footer() -> None:
    """Render the sidebar's operator identity chip.

    Uses the real OS account running this process (never a hardcoded
    name) — this dashboard has no login/auth system of its own.
    """
    try:
        operator = getpass.getuser()
    except Exception:  # noqa: BLE001
        operator = "operator"
    initials = "".join(part[0] for part in operator.replace(".", " ").split()[:2]).upper() or "OP"
    st.markdown(
        (
            "<div class='theta-sidebar-user'>"
            f"<div class='theta-sidebar-user-avatar'>{html.escape(initials)}</div>"
            "<div>"
            f"<div class='theta-sidebar-user-name'>{html.escape(operator)}</div>"
            "<div class='theta-sidebar-user-role'>Operator</div>"
            "</div></div>"
        ),
        unsafe_allow_html=True,
    )


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
