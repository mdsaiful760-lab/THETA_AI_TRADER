"""Settings page."""

from __future__ import annotations

import streamlit as st

from dashboard.components.page_header import render_page_header
from dashboard.session_state import update_ui_prefs
from dashboard.view_models import DashboardRenderContext

SECRET_MARKERS: tuple[str, ...] = (
    "api_key",
    "access_token",
    "secret",
    "password",
    "token",
)


def _redact_key(key: str) -> str:
    """Return redacted placeholder for secret-looking config keys."""
    lowered = key.lower()
    for marker in SECRET_MARKERS:
        if marker in lowered:
            return "[REDACTED]"
    return key


def render(ctx: DashboardRenderContext) -> None:
    """Render the settings page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Settings", "Redacted configuration and UI preferences")
    snapshot = ctx.facade.get_settings_view()

    st.subheader("Application Configuration")
    if snapshot.config_entries:
        for key, value in snapshot.config_entries.items():
            display_key = _redact_key(key)
            display_value = "[REDACTED]" if display_key == "[REDACTED]" else value
            st.text(f"{display_key}: {display_value}")
    else:
        st.info("Configuration snapshot unavailable")

    st.subheader("UI Preferences")
    refresh_interval = st.number_input(
        "Refresh interval (seconds)",
        min_value=0.5,
        max_value=60.0,
        value=float(ctx.config.refresh_interval_seconds),
        step=0.5,
    )
    page_options = [
        "home",
        "market",
        "strategy_monitor",
        "paper_trading",
        "orders",
        "portfolio",
        "risk",
        "apme",
        "logs",
        "analytics",
        "settings",
    ]
    default_index = (
        page_options.index(ctx.config.default_page)
        if ctx.config.default_page in page_options
        else 0
    )
    default_page = st.selectbox(
        "Default page",
        options=page_options,
        index=default_index,
    )
    if st.button("Save UI preferences"):
        update_ui_prefs(
            {
                "refresh_interval_seconds": str(refresh_interval),
                "default_page": default_page,
                "theme": "dark",
            }
        )
        st.success("UI preferences saved to session")

    st.caption("Theme is fixed to dark in dashboard v1. Secret editing is disabled.")
