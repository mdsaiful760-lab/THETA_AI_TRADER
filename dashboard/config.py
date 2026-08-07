"""Versioned dashboard UI configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

DASHBOARD_UI_SCHEMA_VERSION: str = "1.0.0"

VALID_PAGE_IDS: frozenset[str] = frozenset(
    {
        "home",
        "market",
        "strategy_monitor",
        "paper_trading",
        "orders",
        "portfolio",
        "risk",
        "apme",
        "logs",
        "analytics",
        "settings",
    }
)

DEFAULT_INDEX_SYMBOLS: tuple[str, ...] = (
    "NIFTY",
    "BANKNIFTY",
    "SENSEX",
    "INDIA VIX",
)


class DashboardConfigurationError(ValueError):
    """Raised when dashboard UI configuration is invalid."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize with a stable error code and human-readable message.

        Args:
            code: Stable configuration error code (e.g. ``CFG-DASH-001``).
            message: Human-readable validation failure description.
        """
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DashboardUiConfig:
    """Immutable dashboard UI configuration (schema ``1.0.0``).

    Attributes:
        schema_version: Configuration schema version; must equal
            ``DASHBOARD_UI_SCHEMA_VERSION``.
        app_title: Application title displayed in the shell.
        default_page: Default active page identifier.
        refresh_interval_seconds: Polling interval for optional autorefresh.
        enable_autorefresh: Whether automatic snapshot refresh is enabled.
        show_demo_banners: Whether offline/demo banners are shown.
        plotly_template: Plotly figure template name.
        index_symbols: Index symbols shown on the home terminal strip.
        home_market_refresh_seconds: Home market strip autorefresh interval.
        enable_home_market_autorefresh: Whether Home market autorefresh is on.
        paper_trading_refresh_seconds: Paper Trading page autorefresh interval.
        enable_paper_trading_autorefresh: Whether Paper Trading autorefresh is on.
        metadata: Additional non-secret metadata key/value pairs.
    """

    schema_version: str = DASHBOARD_UI_SCHEMA_VERSION
    app_title: str = "THETA AI TRADER"
    default_page: str = "home"
    refresh_interval_seconds: float = 2.0
    enable_autorefresh: bool = True
    show_demo_banners: bool = True
    plotly_template: str = "plotly_dark"
    index_symbols: tuple[str, ...] = DEFAULT_INDEX_SYMBOLS
    home_market_refresh_seconds: float = 1.0
    enable_home_market_autorefresh: bool = True
    paper_trading_refresh_seconds: float = 1.0
    enable_paper_trading_autorefresh: bool = True
    metadata: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        """Validate configuration invariants."""
        if self.schema_version != DASHBOARD_UI_SCHEMA_VERSION:
            raise DashboardConfigurationError(
                "CFG-DASH-001",
                f"schema_version must be {DASHBOARD_UI_SCHEMA_VERSION!r}, "
                f"got {self.schema_version!r}",
            )
        if not self.app_title.strip():
            raise DashboardConfigurationError(
                "CFG-DASH-002",
                "app_title must be non-empty",
            )
        if self.default_page not in VALID_PAGE_IDS:
            raise DashboardConfigurationError(
                "CFG-DASH-003",
                f"default_page must be a valid page id, got {self.default_page!r}",
            )
        if self.refresh_interval_seconds <= 0:
            raise DashboardConfigurationError(
                "CFG-DASH-004",
                "refresh_interval_seconds must be > 0",
            )
        if not self.index_symbols:
            raise DashboardConfigurationError(
                "CFG-DASH-005",
                "index_symbols must be non-empty",
            )
        if self.home_market_refresh_seconds <= 0:
            raise DashboardConfigurationError(
                "CFG-DASH-HOME-001",
                "home_market_refresh_seconds must be > 0",
            )
        if self.paper_trading_refresh_seconds <= 0:
            raise DashboardConfigurationError(
                "CFG-DASH-PAPER-001",
                "paper_trading_refresh_seconds must be > 0",
            )


def default_dashboard_ui_config(
    *,
    refresh_interval_seconds: float = 2.0,
    show_demo_banners: bool = True,
) -> DashboardUiConfig:
    """Return the default dashboard UI configuration.

    Args:
        refresh_interval_seconds: Snapshot refresh interval in seconds.
        show_demo_banners: Whether to display demo/offline banners.

    Returns:
        Validated ``DashboardUiConfig`` instance with schema ``1.0.0``.
    """
    return DashboardUiConfig(
        refresh_interval_seconds=refresh_interval_seconds,
        show_demo_banners=show_demo_banners,
    )
