"""Market context page."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.components.page_header import render_page_header
from dashboard.view_models import DashboardRenderContext


def render(ctx: DashboardRenderContext) -> None:
    """Render the market page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    render_page_header("Market", "Underlying quotes and option chain placeholders")
    snapshot = ctx.facade.get_market_snapshot()

    underlyings = list(snapshot.underlyings) or ["NIFTY", "BANKNIFTY"]
    st.selectbox("Underlying", options=underlyings, index=0, disabled=True)

    col1, col2, col3 = st.columns(3)
    col1.metric("LTP", snapshot.ltp)
    col2.metric("Change", snapshot.change)
    col3.metric("Volume", snapshot.volume)

    st.subheader("Option Chain")
    columns = list(snapshot.option_chain_columns)
    df = pd.DataFrame(columns=columns)
    render_table(df)
    st.caption("Strike selection and trading actions are display-only in v1")
