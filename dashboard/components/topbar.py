"""Top bar: live ticker, broker status, clock, and quick actions.

Renders across the top of the main content area (right of the sidebar —
Streamlit does not support a single DOM element spanning both the sidebar
and main content, so this is the closest faithful reproduction of the
approved design within that constraint). All values are real: index
quotes come from the same facade reads the rest of the app uses, the
clock is the server's real wall-clock time at render, and the
notification count is a real count of real WARN+/ERROR+ log entries.
"""

from __future__ import annotations

import html

import streamlit as st

from dashboard.session_state import set_active_page
from dashboard.view_models import DashboardRenderContext, IndexQuoteView, PLACEHOLDER

_TICKER_SYMBOLS: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "INDIA VIX")


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


def render_topbar(
    ctx: DashboardRenderContext,
    *,
    indices: tuple[IndexQuoteView, ...],
    broker_connected: bool,
    alert_count: int,
) -> None:
    """Render the top bar.

    Args:
        ctx: Render context (used for navigation actions).
        indices: Real index quotes; only the three ticker symbols are shown.
        broker_connected: Real broker connectivity flag for the LIVE badge.
        alert_count: Real count of real WARN+/ERROR+ log entries.
    """
    by_symbol = {quote.symbol: quote for quote in indices}
    ticker_html = "".join(
        _ticker_item(by_symbol[symbol]) for symbol in _TICKER_SYMBOLS if symbol in by_symbol
    )
    live_state = "on" if broker_connected else "off"
    live_label = "LIVE" if broker_connected else "OFFLINE"
    now_display = ctx.clock().strftime("%I:%M:%S %p")

    left, right = st.columns([3, 2])
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
        badge_col, clock_col, bell_col, gear_col = st.columns([1.3, 1.5, 0.6, 0.6])
        with badge_col:
            st.markdown(
                (
                    f"<div class='theta-live-dot {live_state}'>"
                    f"<span class='dot'></span>{live_label}</div>"
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
