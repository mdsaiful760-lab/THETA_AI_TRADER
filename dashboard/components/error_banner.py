"""Error and warning banner components."""

from __future__ import annotations

import streamlit as st


def render_error(message: str | None) -> None:
    """Render an error banner when a message is present.

    Args:
        message: Error message text.
    """
    if message:
        st.error(message)


def render_warning(message: str | None) -> None:
    """Render a warning banner when a message is present.

    Args:
        message: Warning message text.
    """
    if message:
        st.warning(message)
