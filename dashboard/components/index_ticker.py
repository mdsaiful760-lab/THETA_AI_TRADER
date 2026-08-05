"""Index ticker strip for major benchmarks."""

from __future__ import annotations

from typing import Sequence

import streamlit as st

from dashboard.view_models import IndexQuoteView, PLACEHOLDER


def _signed_class(value: str) -> str:
    """Return CSS class for a signed change display value."""
    if value.startswith("+"):
        return "theta-change-positive"
    if value.startswith("-"):
        return "theta-change-negative"
    return "theta-change-muted"


def _conn_class(status: str) -> str:
    """Return CSS class for connection status badge."""
    normalized = status.upper()
    if normalized == "LIVE":
        return "theta-badge-positive live"
    if normalized == "DELAYED":
        return "theta-badge-warning delayed"
    if normalized == "OFFLINE":
        return "theta-badge-negative offline"
    return "theta-badge-neutral unknown"


def render_index_strip(indices: Sequence[IndexQuoteView]) -> None:
    """Render the home page index quote strip.

    Each card shows LTP, absolute change, percentage change, last update
    timestamp, and connection status.

    Args:
        indices: Index quote view models (NIFTY, BANKNIFTY, SENSEX, INDIA VIX).
    """
    if not indices:
        st.info("Awaiting backend index quotes")
        return

    columns = st.columns(len(indices))
    for column, quote in zip(columns, indices):
        connection = quote.connection_status or quote.status or "UNKNOWN"
        change_abs = quote.change_abs if quote.change_abs else PLACEHOLDER
        change_pct = quote.change_pct if quote.change_pct else PLACEHOLDER
        abs_css = _signed_class(change_abs)
        pct_css = _signed_class(change_pct)
        with column:
            st.markdown(
                (
                    f"<div class='theta-index-card'>"
                    f"<div class='theta-index-symbol'>{quote.symbol}</div>"
                    f"<div class='theta-index-value'>{quote.value}</div>"
                    f"<div class='theta-index-change'>"
                    f"<span class='theta-index-change-abs {abs_css}'>{change_abs}</span>"
                    f"&nbsp;&nbsp;"
                    f"<span class='theta-index-change-pct {pct_css}'>{change_pct}</span>"
                    f"</div>"
                    f"<div class='theta-index-updated'>{quote.last_update}</div>"
                    f"<div class='theta-index-conn'>"
                    f"<span class='theta-badge {_conn_class(connection)}'>{connection}</span>"
                    f"</div>"
                    f"</div>"
                ),
                unsafe_allow_html=True,
            )
