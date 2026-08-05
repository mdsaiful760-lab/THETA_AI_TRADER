"""Consistent page header component."""

from __future__ import annotations

import streamlit as st


def render_page_header(title: str, subtitle: str | None = None) -> None:
    """Render a page title and optional subtitle.

    Args:
        title: Page title text.
        subtitle: Optional one-line description.
    """
    st.markdown(f"<h1 class='theta-page-title'>{title}</h1>", unsafe_allow_html=True)
    if subtitle:
        st.markdown(
            f"<p class='theta-page-subtitle'>{subtitle}</p>",
            unsafe_allow_html=True,
        )
