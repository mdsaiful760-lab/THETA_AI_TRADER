"""Refresh interval helpers for optional autorefresh."""

from __future__ import annotations

from datetime import datetime, timedelta

from dashboard.config import DashboardUiConfig


def should_autorefresh(
    config: DashboardUiConfig,
    *,
    last_refresh_at: datetime | None,
    now: datetime,
) -> bool:
    """Determine whether the dashboard should trigger an autorefresh.

    Autorefresh only re-reads snapshots; it never starts a trading cycle.

    Args:
        config: Dashboard UI configuration.
        last_refresh_at: Timestamp of the previous refresh, if any.
        now: Current clock reading.

    Returns:
        ``True`` when autorefresh is enabled and the interval has elapsed.
    """
    if not config.enable_autorefresh:
        return False
    if last_refresh_at is None:
        return True
    elapsed = now - last_refresh_at
    return elapsed >= timedelta(seconds=config.refresh_interval_seconds)


def should_home_market_autorefresh(
    config: DashboardUiConfig,
    *,
    last_refresh_at: datetime | None,
    now: datetime,
) -> bool:
    """Determine whether the Home market strip should autorefresh.

    Args:
        config: Dashboard UI configuration.
        last_refresh_at: Timestamp of the previous Home market refresh.
        now: Current clock reading.

    Returns:
        ``True`` when Home market autorefresh is enabled and the interval
        (default 1.0s) has elapsed.
    """
    if not config.enable_home_market_autorefresh:
        return False
    if last_refresh_at is None:
        return True
    elapsed = now - last_refresh_at
    return elapsed >= timedelta(seconds=config.home_market_refresh_seconds)


def home_market_refresh_interval_ms(config: DashboardUiConfig) -> int:
    """Return Home market autorefresh interval in milliseconds.

    Args:
        config: Dashboard UI configuration.

    Returns:
        Interval in milliseconds (minimum 1 ms).
    """
    return max(1, int(config.home_market_refresh_seconds * 1000))


def should_paper_trading_autorefresh(
    config: DashboardUiConfig,
    *,
    last_refresh_at: datetime | None,
    now: datetime,
) -> bool:
    """Determine whether the Paper Trading page should autorefresh.

    Args:
        config: Dashboard UI configuration.
        last_refresh_at: Timestamp of the previous Paper Trading refresh.
        now: Current clock reading.

    Returns:
        ``True`` when Paper Trading autorefresh is enabled and the interval
        (default 1.0s) has elapsed.
    """
    if not config.enable_paper_trading_autorefresh:
        return False
    if last_refresh_at is None:
        return True
    elapsed = now - last_refresh_at
    return elapsed >= timedelta(seconds=config.paper_trading_refresh_seconds)


def paper_trading_refresh_interval_ms(config: DashboardUiConfig) -> int:
    """Return Paper Trading autorefresh interval in milliseconds.

    Args:
        config: Dashboard UI configuration.

    Returns:
        Interval in milliseconds (minimum 1 ms).
    """
    return max(1, int(config.paper_trading_refresh_seconds * 1000))
