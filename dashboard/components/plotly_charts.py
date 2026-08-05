"""Plotly chart builders for portfolio and analytics views."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


def build_equity_curve(df: pd.DataFrame) -> go.Figure:
    """Build an equity curve figure from tabular series data.

    Args:
        df: DataFrame with ``timestamp`` and ``equity`` columns.

    Returns:
        Dark-themed Plotly figure.
    """
    figure = go.Figure()
    if not df.empty and {"timestamp", "equity"}.issubset(df.columns):
        figure.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["equity"],
                mode="lines",
                name="Equity",
                line={"color": "#3D8BFF"},
            )
        )
    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121821",
        plot_bgcolor="#121821",
        margin={"l": 24, "r": 24, "t": 36, "b": 24},
        title="Equity Curve",
    )
    return figure


def build_allocation_pie(df: pd.DataFrame) -> go.Figure:
    """Build an allocation pie chart from tabular data.

    Args:
        df: DataFrame with ``label`` and ``weight`` columns.

    Returns:
        Dark-themed Plotly figure.
    """
    figure = go.Figure()
    if not df.empty and {"label", "weight"}.issubset(df.columns):
        figure.add_trace(
            go.Pie(
                labels=df["label"],
                values=df["weight"],
                hole=0.45,
            )
        )
    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121821",
        plot_bgcolor="#121821",
        margin={"l": 24, "r": 24, "t": 36, "b": 24},
        title="Allocation",
    )
    return figure


def build_drawdown(df: pd.DataFrame) -> go.Figure:
    """Build a drawdown chart from tabular series data.

    Args:
        df: DataFrame with ``timestamp`` and ``drawdown`` columns.

    Returns:
        Dark-themed Plotly figure.
    """
    figure = go.Figure()
    if not df.empty and {"timestamp", "drawdown"}.issubset(df.columns):
        figure.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["drawdown"],
                mode="lines",
                name="Drawdown",
                fill="tozeroy",
                line={"color": "#E74C3C"},
            )
        )
    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121821",
        plot_bgcolor="#121821",
        margin={"l": 24, "r": 24, "t": 36, "b": 24},
        title="Drawdown",
    )
    return figure
