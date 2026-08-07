"""Styled dataframe table renderer."""

from __future__ import annotations

import pandas as pd
import streamlit as st


def render_table(
    df: "pd.DataFrame | pd.io.formats.style.Styler", *, height: int | None = None
) -> None:
    """Render a dataframe (optionally pre-styled) with consistent dashboard styling.

    Args:
        df: Dataframe or Styler to display.
        height: Optional fixed viewport height.
    """
    kwargs: dict[str, object] = {
        "use_container_width": True,
        "hide_index": True,
    }
    if height is not None:
        kwargs["height"] = height
    st.dataframe(df, **kwargs)
