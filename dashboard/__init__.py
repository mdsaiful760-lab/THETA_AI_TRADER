"""THETA AI TRADER dashboard presentation package."""

from __future__ import annotations

from dashboard.config import (
    DASHBOARD_UI_SCHEMA_VERSION,
    DashboardUiConfig,
    default_dashboard_ui_config,
)

DASHBOARD_VERSION: str = "1.0.0"

__all__ = [
    "DASHBOARD_VERSION",
    "DASHBOARD_UI_SCHEMA_VERSION",
    "DashboardUiConfig",
    "default_dashboard_ui_config",
]
