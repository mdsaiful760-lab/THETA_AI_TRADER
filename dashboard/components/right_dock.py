"""Optional collapsible right dock — app-shell structural container.

Sprint 1 only establishes the shell's right-dock region and its
expand/collapse toggle, collapsed by default, with no widgets inside it
yet — never fabricated content, just the layout container per spec.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import streamlit as st

_STATE_KEY = "theta_dock_expanded"


def _is_expanded() -> bool:
    """Return the real session-persisted expand/collapse state."""
    return bool(st.session_state.get(_STATE_KEY, False))


def _toggle() -> None:
    """Flip the dock's expand/collapse state."""
    st.session_state[_STATE_KEY] = not _is_expanded()


@contextmanager
def main_and_dock_columns() -> Iterator[object]:
    """Split the content area into (main content, right dock).

    The dock renders its own toggle/state as a side effect; callers only
    need the yielded main-content column to render the active page into.
    """
    expanded = _is_expanded()
    ratios = [2.4, 1.0] if expanded else [1.0, 0.05]
    main_col, dock_col = st.columns(ratios, gap="small")
    with dock_col:
        _render_dock(expanded)
    yield main_col


def _render_dock(expanded: bool) -> None:
    """Render the dock's collapsed toggle strip or its expanded shell."""
    if not expanded:
        if st.button(
            "◀", key="theta_dock_toggle_collapsed", help="Expand dock", use_container_width=True
        ):
            _toggle()
            st.rerun()
        return

    with st.container(border=True, key="theta_panel_right_dock"):
        header_col, close_col = st.columns([4, 1])
        with header_col:
            st.markdown("**Dock**")
        with close_col:
            if st.button("▶", key="theta_dock_toggle_expanded", help="Collapse dock"):
                _toggle()
                st.rerun()
        st.caption("Reserved for future quick-access widgets — no content yet.")
