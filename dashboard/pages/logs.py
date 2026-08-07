"""Logs page."""

from __future__ import annotations

import html

import streamlit as st

from dashboard.components.page_header import render_page_header
from dashboard.utils.autorefresh import live_fragment
from dashboard.view_models import DashboardRenderContext, LogEntryView

SECRET_MARKERS: tuple[str, ...] = (
    "api_key",
    "access_token",
    "secret",
    "password",
    "token",
)

_LEVELS: tuple[str, ...] = ("All", "ERROR", "WARN", "INFO", "DEBUG")

_LEVEL_CSS: dict[str, str] = {
    "ERROR": "level-error",
    "CRITICAL": "level-critical",
    "WARN": "level-warn",
    "WARNING": "level-warning",
    "INFO": "level-info",
    "DEBUG": "level-debug",
}

_BADGE_VARIANT: dict[str, str] = {
    "ERROR": "negative",
    "CRITICAL": "negative",
    "WARN": "warning",
    "WARNING": "warning",
    "INFO": "neutral",
    "DEBUG": "neutral",
}


def _redact_message(message: str) -> str:
    """Redact secret-looking substrings from log messages."""
    lowered = message.lower()
    for marker in SECRET_MARKERS:
        if marker in lowered:
            return "[REDACTED]"
    return message


def _render_event_card(entry: LogEntryView) -> None:
    """Render one structured, color-coded event card for a log entry."""
    level = entry.level.upper().strip()
    css_class = _LEVEL_CSS.get(level, "level-info")
    badge_variant = _BADGE_VARIANT.get(level, "neutral")
    message = html.escape(_redact_message(entry.message))
    timestamp = html.escape(entry.timestamp)
    st.markdown(
        (
            f"<div class='theta-event-card {css_class}'>"
            f"<div class='theta-event-time'>{timestamp}</div>"
            f"<div class='theta-event-body'>"
            f"<div class='theta-event-message'>"
            f"<span class='theta-badge theta-badge-{badge_variant}'>{level or 'INFO'}</span>"
            f"&nbsp;&nbsp;{message}"
            f"</div>"
            f"</div>"
            f"</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_body(ctx: DashboardRenderContext) -> None:
    """Render the logs page body (re-invoked on every live refresh tick)."""
    snapshot = ctx.facade.get_logs()

    selected_level = st.selectbox("Level filter", options=_LEVELS, key="logs_level_filter")

    entries = snapshot.entries
    if selected_level != "All":
        entries = tuple(entry for entry in entries if entry.level.upper() == selected_level)

    if not entries:
        st.info("Awaiting backend log stream")
        return

    st.caption(f"{len(entries)} event(s)")
    for entry in entries:
        _render_event_card(entry)


def render(ctx: DashboardRenderContext) -> None:
    """Render the logs page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Logs", "Recent platform events")
    if not ctx.config.enable_autorefresh:
        _render_body(ctx)
        return
    live_fragment(
        lambda: _render_body(ctx),
        interval_seconds=ctx.config.refresh_interval_seconds,
        key="logs_refresh",
    )
