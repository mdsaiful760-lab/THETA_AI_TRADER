"""Reusable dashboard UI components."""

from dashboard.components.chart_placeholder import render_tradingview_placeholder
from dashboard.components.data_table import render_table
from dashboard.components.error_banner import render_error, render_warning
from dashboard.components.index_ticker import render_index_strip
from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.page_header import render_page_header
from dashboard.components.plotly_charts import (
    build_allocation_pie,
    build_drawdown,
    build_equity_curve,
)
from dashboard.components.sidebar import render_sidebar
from dashboard.components.status_badges import render_status_badges

__all__ = [
    "build_allocation_pie",
    "build_drawdown",
    "build_equity_curve",
    "render_error",
    "render_index_strip",
    "render_kpi_row",
    "render_page_header",
    "render_sidebar",
    "render_status_badges",
    "render_table",
    "render_tradingview_placeholder",
    "render_warning",
]
