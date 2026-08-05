"""TradingView Lightweight Charts placeholder container."""

from __future__ import annotations

import streamlit as st


def render_tradingview_placeholder(*, height: int = 420) -> None:
    """Render a reserved TradingView chart region.

    Args:
        height: Panel height in pixels.
    """
    st.markdown(
        (
            f"<div class='theta-chart-panel' style='height:{height}px;'>"
            "<div class='theta-chart-caption'>"
            "Chart integration pending — TradingView Lightweight Charts"
            "</div>"
            "<div id='theta-tv-chart'></div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    st.components.v1.html(
        f"<div id='theta-tv-chart' style='height:{height - 48}px;'></div>",
        height=height,
    )
