"""Streamlit application entrypoint for THETA AI TRADER dashboard."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

import streamlit as st

from dashboard import DASHBOARD_VERSION, default_dashboard_ui_config
from dashboard.components.error_banner import render_error
from dashboard.components.right_dock import main_and_dock_columns
from dashboard.components.sidebar import render_sidebar
from dashboard.components.topbar import render_topbar
from dashboard.facade import DashboardBackendFacade
from dashboard.live_session_adapter import build_default_presentation_facade
from dashboard.pages import PAGE_REGISTRY
from dashboard.pages.home import resolve_home_indices
from dashboard.session_state import ensure_session_state, get_session_view, set_last_error
from dashboard.theme import apply_theme, configure_page
from dashboard.view_models import DashboardRenderContext


def _utc_now() -> datetime:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


def build_render_context(
    facade: DashboardBackendFacade | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
) -> DashboardRenderContext:
    """Build the dashboard render context.

    Args:
        facade: Optional backend facade; defaults to
            ``build_default_presentation_facade()`` which stays offline
            unless live handles were registered by the host process.
        clock: Injectable clock for tests.

    Returns:
        Immutable render context for page modules.
    """
    backend = facade or build_default_presentation_facade()
    config = default_dashboard_ui_config(
        show_demo_banners=not getattr(backend, "is_connected", False)
    )
    ensure_session_state(default_page=config.default_page)
    return DashboardRenderContext(
        config=config,
        facade=backend,
        session=get_session_view(),
        clock=clock or _utc_now,
        version=DASHBOARD_VERSION,
    )


_ALERT_LEVELS = frozenset({"WARN", "WARNING", "ERROR", "CRITICAL"})


def _real_alert_count(ctx: DashboardRenderContext) -> int:
    """Return the real count of WARN+/ERROR+ log entries for the top bar bell."""
    try:
        logs = ctx.facade.get_logs(limit=50)
        return sum(1 for entry in logs.entries if entry.level.strip().upper() in _ALERT_LEVELS)
    except Exception:  # noqa: BLE001 - shell must not crash
        return 0


def resolve_page(page_id: str):
    """Resolve a page id to a registered page object.

    Args:
        page_id: Registered page identifier.

    Returns:
        ``DashboardPage`` instance.

    Raises:
        KeyError: When the page id is unknown.
    """
    if page_id not in PAGE_REGISTRY:
        return PAGE_REGISTRY["home"]
    return PAGE_REGISTRY[page_id]


def main() -> None:
    """Run the THETA AI TRADER Streamlit dashboard shell."""
    config = default_dashboard_ui_config()
    configure_page(title=config.app_title)
    apply_theme()
    ensure_session_state(default_page=config.default_page)
    ctx = build_render_context()
    render_topbar(
        ctx,
        indices=resolve_home_indices(ctx),
        broker_status=ctx.facade.get_runtime_state().broker_status,
        websocket_status=ctx.facade.get_websocket_status(),
        alert_count=_real_alert_count(ctx),
    )
    render_sidebar(ctx)
    # render_sidebar() may have just written a new active_page into session
    # state (e.g. the user clicked a different nav item this rerun) — ctx
    # is an immutable snapshot taken before that write, so it must be
    # refreshed here or the page resolved below would always be one click
    # behind the sidebar selection.
    ctx = replace(ctx, session=get_session_view())
    page = resolve_page(ctx.session.active_page)
    with main_and_dock_columns() as main_col, main_col:
        render_error(ctx.session.last_error)
        try:
            page.render(ctx)
        except Exception as exc:  # noqa: BLE001 - presentation shell must not crash
            set_last_error(f"DASH.PAGE.RENDER_FAILED: {exc}")
            render_error(str(exc))


if __name__ == "__main__":
    main()
