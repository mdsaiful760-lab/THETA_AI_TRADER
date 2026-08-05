"""Deterministic display formatting helpers."""

from __future__ import annotations

from datetime import datetime


def format_money(value: float | None, *, currency: str = "INR") -> str:
    """Format a monetary value for display.

    Args:
        value: Numeric amount; ``None`` renders as an em dash placeholder.
        currency: ISO currency label prefix.

    Returns:
        Formatted currency string or ``"—"`` when value is missing.
    """
    if value is None:
        return "—"
    return f"{currency} {value:,.2f}"


def format_percent(value: float | None, *, digits: int = 2) -> str:
    """Format a percentage value for display.

    Args:
        value: Fractional percent value (e.g. ``1.5`` for ``1.50%``).
        digits: Number of decimal places.

    Returns:
        Percent string or ``"—"`` when value is missing.
    """
    if value is None:
        return "—"
    return f"{value:.{digits}f}%"


def format_timestamp(value: datetime | None, *, fmt: str = "%Y-%m-%d %H:%M:%S UTC") -> str:
    """Format a timestamp deterministically.

    Args:
        value: Datetime to format.
        fmt: ``strftime`` format string.

    Returns:
        Formatted timestamp or ``"—"`` when value is missing.
    """
    if value is None:
        return "—"
    return value.strftime(fmt)
