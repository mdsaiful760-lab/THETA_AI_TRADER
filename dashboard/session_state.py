"""Typed Streamlit session state helpers."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, MutableMapping

import streamlit as st

from dashboard.view_models import DashboardSessionView

SESSION_KEY: str = "theta_dashboard_session"


@dataclass(frozen=True)
class SessionKeys:
    """Canonical session state keys."""

    active_page: str = "active_page"
    last_error: str = "last_error"
    last_refresh_at: str = "last_refresh_at"
    facade_action_pending: str = "facade_action_pending"
    ui_prefs: str = "ui_prefs"


KEYS = SessionKeys()


def _empty_session(default_page: str) -> dict[str, Any]:
    """Build initial session payload."""
    return {
        KEYS.active_page: default_page,
        KEYS.last_error: None,
        KEYS.last_refresh_at: None,
        KEYS.facade_action_pending: False,
        KEYS.ui_prefs: {},
    }


def ensure_session_state(*, default_page: str = "home") -> None:
    """Ensure typed dashboard session keys exist in Streamlit session state.

    Args:
        default_page: Page id used when initializing session state.
    """
    if SESSION_KEY not in st.session_state:
        st.session_state[SESSION_KEY] = _empty_session(default_page)


def _session_payload() -> MutableMapping[str, Any]:
    """Return mutable dashboard session payload."""
    ensure_session_state()
    return st.session_state[SESSION_KEY]


def get_session_view() -> DashboardSessionView:
    """Return an immutable snapshot of current UI session state."""
    payload = _session_payload()
    ui_prefs = payload.get(KEYS.ui_prefs, {})
    if not isinstance(ui_prefs, Mapping):
        ui_prefs = {}
    return DashboardSessionView(
        active_page=str(payload.get(KEYS.active_page, "home")),
        last_error=payload.get(KEYS.last_error),
        last_refresh_at=payload.get(KEYS.last_refresh_at),
        facade_action_pending=bool(payload.get(KEYS.facade_action_pending, False)),
        ui_prefs=MappingProxyType(dict(ui_prefs)),
    )


def set_active_page(page_id: str) -> None:
    """Update the active page id."""
    payload = _session_payload()
    payload[KEYS.active_page] = page_id


def set_last_error(message: str | None) -> None:
    """Store the latest presentation error message."""
    payload = _session_payload()
    payload[KEYS.last_error] = message


def set_last_refresh_at(timestamp: str | None) -> None:
    """Store the latest refresh timestamp."""
    payload = _session_payload()
    payload[KEYS.last_refresh_at] = timestamp


def set_facade_action_pending(pending: bool) -> None:
    """Mark whether a facade action is in progress."""
    payload = _session_payload()
    payload[KEYS.facade_action_pending] = pending


def update_ui_prefs(values: Mapping[str, str]) -> None:
    """Merge UI preference values into session state."""
    payload = _session_payload()
    current = dict(payload.get(KEYS.ui_prefs, {}))
    current.update(values)
    payload[KEYS.ui_prefs] = current
