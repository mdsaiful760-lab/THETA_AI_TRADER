"""Honest 'capability not available' panel — never a fabricated preview."""

from __future__ import annotations

import html

import streamlit as st


def render_unavailable_panel(*, icon: str, title: str, detail: str) -> None:
    """Render a professional, honest empty-capability panel.

    Used for sidebar destinations that have no backing engine in this
    platform (e.g. Strategy Builder, Backtesting) — shows a clear,
    themed message instead of fabricating sample output.

    Args:
        icon: Single glyph/emoji shown above the title.
        title: Short capability name.
        detail: One or two sentences explaining what is missing.
    """
    st.markdown(
        (
            "<div class='theta-unavailable-panel'>"
            f"<div class='icon'>{icon}</div>"
            f"<div class='title'>{html.escape(title)}</div>"
            f"<div>{html.escape(detail)}</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
