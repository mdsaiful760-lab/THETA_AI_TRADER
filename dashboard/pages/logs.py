"""Logs page."""

from __future__ import annotations

import streamlit as st

from dashboard.components.page_header import render_page_header
from dashboard.view_models import DashboardRenderContext

SECRET_MARKERS: tuple[str, ...] = (
    "api_key",
    "access_token",
    "secret",
    "password",
    "token",
)


def _redact_message(message: str) -> str:
    """Redact secret-looking substrings from log messages."""
    lowered = message.lower()
    for marker in SECRET_MARKERS:
        if marker in lowered:
            return "[REDACTED]"
    return message


def render(ctx: DashboardRenderContext) -> None:
    """Render the logs page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Logs", "Recent platform events")
    snapshot = ctx.facade.get_logs()

    levels = ["All", "INFO", "WARN", "ERROR"]
    selected_level = st.selectbox("Level filter", options=levels)

    entries = snapshot.entries
    if selected_level != "All":
        entries = tuple(entry for entry in entries if entry.level.upper() == selected_level)

    if not entries:
        st.info("Awaiting backend log stream")
        return

    for entry in entries:
        message = _redact_message(entry.message)
        st.text(f"[{entry.timestamp}] {entry.level}: {message}")
