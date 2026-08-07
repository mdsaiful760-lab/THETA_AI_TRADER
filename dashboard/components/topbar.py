"""Top bar: live ticker, connection/AI status, clock, and quick actions.

Renders across the top of the main content area (right of the sidebar —
Streamlit does not support a single DOM element spanning both the sidebar
and main content, so this is the closest faithful reproduction of the
approved design within that constraint). Every value is real: index
quotes and status strings come from the same facade reads the rest of the
app uses, the clock is the server's real wall-clock time at render, and
the notification count is a real count of real WARN+/ERROR+ log entries.
"""

from __future__ import annotations

import getpass
import html

import streamlit as st

from dashboard.session_state import set_active_page
from dashboard.view_models import DashboardRenderContext, IndexQuoteView, PLACEHOLDER

_TICKER_SYMBOLS: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "INDIA VIX")

_STATUS_ON = {"CONNECTED", "LIVE", "AUTHENTICATED", "ACTIVE", "RUNNING"}
_STATUS_OFF = {"DISCONNECTED", "OFFLINE", "ERROR", "FAILED", "STOPPED", "IDLE"}


def _signed_class(value: str) -> str:
    """Return CSS class for a signed change display value."""
    if value.startswith("+"):
        return "theta-change-positive"
    if value.startswith("-"):
        return "theta-change-negative"
    return "theta-change-muted"


def _ticker_item(quote: IndexQuoteView) -> str:
    """Render one ticker entry as an HTML fragment."""
    change_abs = quote.change_abs or PLACEHOLDER
    change_pct = quote.change_pct or PLACEHOLDER
    css = _signed_class(change_abs)
    symbol = html.escape(quote.symbol)
    value = html.escape(quote.value)
    change = html.escape(f"{change_abs} ({change_pct})")
    return (
        "<div class='theta-ticker-item'>"
        f"<span class='theta-ticker-symbol'>{symbol}</span>"
        f"<span class='theta-ticker-value'>{value}</span>"
        f"<span class='theta-ticker-change {css}'>{change}</span>"
        "</div>"
    )


def _status_dot_class(value: str) -> str:
    """Classify a real status string into on/off/unknown for the status dot."""
    token = value.strip().upper()
    if token in _STATUS_ON:
        return "on"
    if token in _STATUS_OFF or token in (PLACEHOLDER, ""):
        return "off"
    return "unknown"


def _status_chip(label: str, value: str) -> str:
    """Render one compact real status indicator (Broker / WebSocket / AI)."""
    state = _status_dot_class(value)
    display = html.escape(value if value not in ("", None) else PLACEHOLDER)
    return (
        f"<div class='theta-status-chip {state}' title='{html.escape(label)}: {display}'>"
        "<span class='dot'></span>"
        f"<span class='theta-status-chip-label'>{html.escape(label)}</span>"
        f"<span class='theta-status-chip-value'>{display}</span>"
        "</div>"
    )


def _ai_status(ctx: DashboardRenderContext) -> str:
    """Derive a real AI status label from the AI panel soft-read."""
    getter = getattr(ctx.facade, "get_ai_panel", None)
    panel = getter() if callable(getter) else None
    if panel is None:
        return "OFFLINE"
    decision_status = getattr(panel, "decision_status", PLACEHOLDER)
    return "ACTIVE" if decision_status not in (PLACEHOLDER, "", None) else "IDLE"


def render_topbar(
    ctx: DashboardRenderContext,
    *,
    indices: tuple[IndexQuoteView, ...],
    broker_status: str,
    websocket_status: str,
    alert_count: int,
) -> None:
    """Render the top bar.

    Args:
        ctx: Render context (used for navigation actions and the AI panel).
        indices: Real index quotes; only the three ticker symbols are shown.
        broker_status: Real broker connectivity status string.
        websocket_status: Real WebSocket connectivity status string.
        alert_count: Real count of real WARN+/ERROR+ log entries.
    """
    by_symbol = {quote.symbol: quote for quote in indices}
    ticker_html = "".join(
        _ticker_item(by_symbol[symbol]) for symbol in _TICKER_SYMBOLS if symbol in by_symbol
    )
    ai_status = _ai_status(ctx)
    now_display = ctx.clock().strftime("%I:%M:%S %p")

    try:
        operator = getpass.getuser()
    except Exception:  # noqa: BLE001
        operator = "operator"
    initials = "".join(part[0] for part in operator.replace(".", " ").split()[:2]).upper() or "OP"

    left, right = st.columns([3, 2.6])
    with left:
        st.markdown(
            (
                "<div class='theta-topbar'>"
                "<div class='theta-topbar-brand'>"
                "<span class='theta-brand-mark'>&#920;</span>"
                "<span>THETA AI TRADER</span>"
                "</div>"
                f"<div class='theta-ticker'>{ticker_html}</div>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )
    with right:
        status_col, clock_col, bell_col, gear_col, profile_col = st.columns(
            [3.2, 1.5, 0.55, 0.55, 0.6]
        )
        with status_col:
            st.markdown(
                (
                    "<div class='theta-status-chip-row'>"
                    + _status_chip("Broker", broker_status)
                    + _status_chip("WS", websocket_status)
                    + _status_chip("AI", ai_status)
                    + "</div>"
                ),
                unsafe_allow_html=True,
            )
        with clock_col:
            st.markdown(
                f"<div class='theta-topbar-clock'>{now_display}</div>",
                unsafe_allow_html=True,
            )
        with bell_col:
            if st.button(f"\U0001F514 {alert_count}", key="topbar_bell", help="Recent alerts"):
                set_active_page("logs")
                st.rerun()
        with gear_col:
            if st.button("⚙", key="topbar_gear", help="Settings"):
                set_active_page("settings")
                st.rerun()
        with profile_col:
            if st.button(initials, key="topbar_profile", help=f"Signed in as {operator}"):
                set_active_page("settings")
                st.rerun()
