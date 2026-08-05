"""Dark theme configuration and CSS injection."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
THEME_CSS_PATH = ASSETS_DIR / "theme.css"


def configure_page(*, title: str = "THETA AI TRADER", icon: str = "📈") -> None:
    """Configure Streamlit page layout and metadata.

    Args:
        title: Browser tab title.
        icon: Page favicon emoji or path.
    """
    st.set_page_config(
        page_title=title,
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )


def apply_theme() -> None:
    """Inject dashboard dark theme CSS overrides."""
    css = THEME_CSS_PATH.read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
