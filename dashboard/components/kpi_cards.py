"""KPI metric card row."""

from __future__ import annotations

from typing import Sequence

import streamlit as st

from dashboard.view_models import KpiCardModel


def render_kpi_row(cards: Sequence[KpiCardModel]) -> None:
    """Render a responsive row of KPI cards.

    Args:
        cards: KPI card models to display.
    """
    if not cards:
        st.info("No KPI data available")
        return

    columns = st.columns(len(cards))
    for column, card in zip(columns, cards):
        with column:
            st.markdown(
                (
                    f"<div class='theta-kpi-card'>"
                    f"<div class='theta-kpi-label'>{card.label}</div>"
                    f"<div class='theta-kpi-value'>{card.value}</div>"
                    f"</div>"
                ),
                unsafe_allow_html=True,
            )
            if card.hint:
                st.caption(card.hint)
