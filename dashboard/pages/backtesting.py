"""Backtesting page — honest unavailable state.

This platform has no historical backtesting engine. Rather than fabricate
sample results, this page states that plainly and points to the real,
live paper-trading track record instead.
"""

from __future__ import annotations

from dashboard.components.page_header import render_page_header
from dashboard.components.unavailable_panel import render_unavailable_panel
from dashboard.view_models import DashboardRenderContext


def render(ctx: DashboardRenderContext) -> None:
    """Render the Backtesting page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    del ctx
    render_page_header("Backtesting", "Historical strategy simulation")
    render_unavailable_panel(
        icon="⏱️",
        title="Backtesting is not available",
        detail=(
            "No historical backtesting engine is wired into this platform yet — "
            "see Trade Execution for the real, live paper-trading track record."
        ),
    )
