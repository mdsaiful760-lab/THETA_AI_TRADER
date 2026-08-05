"""System and runtime status badges."""

from __future__ import annotations

import streamlit as st


def _badge(label: str, value: str, css_class: str) -> None:
    """Render a single status badge."""
    st.markdown(
        (
            f"<div class='theta-status-row'>"
            f"<span class='theta-status-label'>{label}</span>"
            f"<span class='theta-badge {css_class}'>{value}</span>"
            f"</div>"
        ),
        unsafe_allow_html=True,
    )


def render_status_badges(
    *,
    system_status: str,
    broker_status: str,
    execution_mode: str,
    market_status: str,
) -> None:
    """Render sidebar status badges.

    Args:
        system_status: Overall system health status.
        broker_status: Broker connectivity status.
        execution_mode: Current execution mode label.
        market_status: Market open/closed indicator.
    """
    _badge("System", system_status, _status_class(system_status))
    _badge("Broker", broker_status, _status_class(broker_status))
    _badge("Execution", execution_mode, "theta-badge-neutral")
    _badge("Market", market_status, _status_class(market_status))


def _status_class(status: str) -> str:
    """Map status text to CSS badge class."""
    normalized = status.upper()
    if normalized in {"RUNNING", "CONNECTED", "OPEN", "APPROVED"}:
        return "theta-badge-positive"
    if normalized in {"STOPPED", "DISCONNECTED", "CLOSED", "REJECTED"}:
        return "theta-badge-negative"
    if normalized in {"DEGRADED", "UNKNOWN", "SKIPPED"}:
        return "theta-badge-warning"
    return "theta-badge-neutral"
