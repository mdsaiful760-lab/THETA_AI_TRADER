"""Strategy Builder page — honest unavailable state.

This platform has no interactive strategy-construction engine; the
evaluated strategies are fixed families defined in the strategy
evaluation engine (see Strategy Library / Strategy Scanner for the real,
live evaluation of those families). Rather than fabricate a builder UI
with no backend behind it, this page states that plainly.
"""

from __future__ import annotations

from dashboard.components.page_header import render_page_header
from dashboard.components.unavailable_panel import render_unavailable_panel
from dashboard.view_models import DashboardRenderContext


def render(ctx: DashboardRenderContext) -> None:
    """Render the Strategy Builder page.

    Args:
        ctx: Immutable render context with facade and session handles.
    """
    del ctx
    render_page_header("Strategy Builder", "Custom strategy construction")
    render_unavailable_panel(
        icon="🛠️",
        title="Strategy Builder is not available",
        detail=(
            "This platform evaluates a fixed set of strategy families rather than "
            "custom-built ones — see Strategy Scanner for live evaluation and "
            "Strategy Library for the full catalog."
        ),
    )
