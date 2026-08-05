"""Streamlit application entrypoint for THETA AI TRADER dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import streamlit as st

from dashboard import DASHBOARD_VERSION, default_dashboard_ui_config
from dashboard.components.error_banner import render_error
from dashboard.components.sidebar import render_sidebar
from dashboard.dashboard_facade import DashboardFacade
from dashboard.facade import DashboardBackendFacade
from dashboard.pages import PAGE_REGISTRY
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
        facade: Optional backend facade; defaults to ``DashboardFacade``
            presentation adapter (offline when no session is injected).
        clock: Injectable clock for tests.

    Returns:
        Immutable render context for page modules.
    """
    backend = facade or DashboardFacade().as_presentation_facade()
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
    render_sidebar(ctx)
    render_error(ctx.session.last_error)
    page = resolve_page(ctx.session.active_page)
    try:
        page.render(ctx)
    except Exception as exc:  # noqa: BLE001 - presentation shell must not crash
        set_last_error(f"DASH.PAGE.RENDER_FAILED: {exc}")
        render_error(str(exc))


if __name__ == "__main__":
    main()
