"""Shared fragment-based live-refresh helper.

Every live page uses ``st.fragment(run_every=...)`` (Streamlit >= 1.33) so
only the fragment's own render function re-executes on each tick — never
the whole page/app. This replaces the previous per-page
``streamlit-autorefresh`` path, which forced a full-script rerun every
tick (fighting any in-flight user interaction and re-running every other
page component unnecessarily). Autorefresh only re-reads already-computed
snapshots; it never starts a trading cycle, evaluates a strategy, or calls
a broker.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import streamlit as st


def _has_active_script_run_context() -> bool:
    """Return whether a real Streamlit app run is driving this call.

    ``st.fragment`` silently never invokes its wrapped function outside a
    real script run (bare mode / unit tests importing the page module
    directly) — this lets callers fall back to a single direct,
    synchronously-testable render instead of one that silently no-ops.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:  # noqa: BLE001 - defensive; treat as bare mode
        return False


def live_fragment(render_fn: Callable[[], None], *, interval_seconds: float, key: str) -> None:
    """Run ``render_fn`` now, and again every ``interval_seconds`` in place.

    Falls back to a single synchronous render when there is no live
    Streamlit script run context (bare mode / unit tests) or on Streamlit
    versions without ``st.fragment`` — never raises, never silently no-ops.

    Args:
        render_fn: Zero-arg render callback; re-invoked in place on each tick.
        interval_seconds: Refresh cadence in seconds (must be > 0).
        key: Stable, page-unique fragment key.
    """
    fragment = getattr(st, "fragment", None)
    if fragment is None or not _has_active_script_run_context():
        render_fn()
        return

    @fragment(run_every=timedelta(seconds=interval_seconds))
    def _tick() -> None:
        render_fn()

    _tick()
