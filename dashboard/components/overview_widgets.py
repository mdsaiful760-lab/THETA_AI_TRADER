"""Home / Dashboard Overview widgets: metric cards, engine status, OI
build-up, strategy scanner, and recent alerts.

Every renderer here is a pure presentation layer over an already-built
view model — no backend reads happen in this module.
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from dashboard.components.data_table import render_table
from dashboard.view_models import (
    AlertRow,
    EngineStatusRow,
    MetricCardView,
    OiBuildupRow,
    PLACEHOLDER,
    ScannerRow,
)

_METRIC_ICONS: dict[str, str] = {
    "Market Regime": "&#9650;",
    "India VIX": "&#128737;",
    "Put Call Ratio": "&#9878;",
    "Max Pain (Weekly)": "&#9888;",
    "FII / DII (Net)": "&#128101;",
}

_SEVERITY_COLORS: dict[str, str] = {
    "critical": "var(--theta-negative)",
    "error": "var(--theta-negative)",
    "warn": "var(--theta-warning)",
    "warning": "var(--theta-warning)",
    "info": "var(--theta-accent)",
    "debug": "var(--theta-muted-dim)",
}


def _sparkline_svg(values: tuple[float, ...], *, color: str) -> str:
    """Build a tiny inline SVG sparkline from real captured readings."""
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    width, height = 100.0, 26.0
    step = width / (len(values) - 1)
    points = []
    for index, value in enumerate(values):
        x = index * step
        y = height - ((value - low) / span) * height
        points.append(f"{x:.1f},{y:.1f}")
    path = " ".join(points)
    return (
        f"<svg viewBox='0 0 {width:.0f} {height:.0f}' preserveAspectRatio='none' "
        f"style='width:100%;height:28px;margin-top:0.3rem;'>"
        f"<polyline points='{path}' fill='none' stroke='{color}' stroke-width='1.6' "
        "stroke-linejoin='round' stroke-linecap='round'/>"
        "</svg>"
    )


def render_metric_cards(cards: tuple[MetricCardView, ...], *, spark_color: str = "#3D8BFF") -> None:
    """Render the top-row metric cards (Market Regime / VIX / PCR / Max Pain / FII-DII)."""
    if not cards:
        return
    columns = st.columns(len(cards))
    for column, card in zip(columns, cards):
        with column:
            icon = _METRIC_ICONS.get(card.label, "&#8226;")
            if not card.available:
                st.markdown(
                    (
                        "<div class='theta-metric-card theta-metric-unavailable'>"
                        "<div class='theta-metric-head'>"
                        f"<span>{html.escape(card.label)}</span>"
                        f"<span class='theta-metric-icon'>{icon}</span>"
                        "</div>"
                        f"<div class='theta-metric-value'>{PLACEHOLDER}</div>"
                        f"<div class='theta-metric-caption'>{html.escape(card.caption)}</div>"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
                continue
            spark = _sparkline_svg(card.trend, color=spark_color)
            st.markdown(
                (
                    "<div class='theta-metric-card'>"
                    "<div class='theta-metric-head'>"
                    f"<span>{html.escape(card.label)}</span>"
                    f"<span class='theta-metric-icon'>{icon}</span>"
                    "</div>"
                    f"<div class='theta-metric-value'>{html.escape(card.value)}</div>"
                    f"<div class='theta-metric-caption'>{html.escape(card.caption)}</div>"
                    f"{spark}"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )


_HEALTH_BADGE_CLASS: dict[str, str] = {
    "EXCELLENT": "theta-badge-positive",
    "DEGRADED": "theta-badge-warning",
    "CRITICAL": "theta-badge-negative",
}


def render_engine_status(rows: tuple[EngineStatusRow, ...], *, overall_health: str) -> None:
    """Render the Engine Status panel."""
    badge_class = _HEALTH_BADGE_CLASS.get(overall_health, "theta-badge-neutral")
    st.markdown(
        (
            "<div class='theta-panel-title'>"
            "<span>Engine Status</span>"
            f"<span class='theta-badge {badge_class}'>{html.escape(overall_health)}</span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    if not rows:
        st.caption("No engine handles registered")
        return
    body = []
    for row in rows:
        dot_class = "up" if row.running else "down"
        state_label = "Running" if row.running else "Stopped"
        body.append(
            "<div class='theta-engine-row'>"
            "<span class='theta-engine-name'>"
            f"<span class='theta-engine-dot {dot_class}'></span>"
            f"{html.escape(row.name)}</span>"
            "<span class='theta-engine-right'>"
            f"<span>{html.escape(state_label)}</span>"
            f"<span>{html.escape(row.latency_display)}</span>"
            "</span></div>"
        )
    st.markdown("".join(body), unsafe_allow_html=True)


def render_oi_buildup(
    calls: tuple[OiBuildupRow, ...], puts: tuple[OiBuildupRow, ...]
) -> None:
    """Render the Option Summary panel (top real OI build-up, CALLS/PUTS tabs)."""
    st.markdown("<div class='theta-panel-title'>Option Summary</div>", unsafe_allow_html=True)
    calls_tab, puts_tab = st.tabs(["CALLS", "PUTS"])
    columns = ["Strike", "OI", "OI Chg", "LTP", "Trend"]
    for tab, rows in ((calls_tab, calls), (puts_tab, puts)):
        with tab:
            if not rows:
                st.caption("Option chain unavailable — awaiting backend market snapshot")
                continue
            frame = pd.DataFrame(
                [
                    (
                        row.strike,
                        row.open_interest,
                        row.change_percent,
                        row.ltp,
                        "▲" if row.trend == "up" else "▼" if row.trend == "down" else "—",
                    )
                    for row in rows
                ],
                columns=columns,
            )
            render_table(frame)


def render_strategy_scanner(rows: tuple[ScannerRow, ...]) -> None:
    """Render the Strategy Scanner panel."""
    st.markdown("<div class='theta-panel-title'>Strategy Scanner</div>", unsafe_allow_html=True)
    if not rows:
        st.caption("No monitored strategies available")
        return
    frame = pd.DataFrame(
        [(row.strategy, row.score, row.signal, row.confidence) for row in rows],
        columns=["Strategy", "Score", "Signal", "Confidence"],
    )
    render_table(frame)


def render_alerts(alerts: tuple[AlertRow, ...]) -> None:
    """Render the Recent Alerts feed from real structured log entries."""
    st.markdown("<div class='theta-panel-title'>Recent Alerts</div>", unsafe_allow_html=True)
    if not alerts:
        st.caption("No recent log activity")
        return
    body = []
    for alert in alerts:
        color = _SEVERITY_COLORS.get(alert.severity, "var(--theta-accent)")
        body.append(
            "<div class='theta-alert-row'>"
            f"<span class='theta-alert-dot' style='background:{color};'></span>"
            "<div>"
            f"<div class='theta-alert-title'>{html.escape(alert.title)}</div>"
            f"<div class='theta-alert-detail'>{html.escape(alert.detail)}</div>"
            "</div>"
            f"<span class='theta-alert-time'>{html.escape(alert.timestamp)}</span>"
            "</div>"
        )
    st.markdown("".join(body), unsafe_allow_html=True)


def render_market_breadth_placeholder() -> None:
    """Render an honest unavailable state for Market Breadth (no NSE-wide feed)."""
    st.markdown("<div class='theta-panel-title'>Market Breadth (NSE)</div>", unsafe_allow_html=True)
    st.markdown(
        (
            "<div class='theta-unavailable-panel' style='padding:1.4rem 1rem;'>"
            "<div class='title' style='font-size:0.92rem;'>Not available</div>"
            "<div>This platform has no NSE-wide advance/decline feed — "
            "only the configured index underlyings are tracked.</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def render_footer_bar(
    *, broker_connected: bool, as_of: str, system_operational: bool, version: str
) -> None:
    """Render the bottom status bar."""
    broker_color = "var(--theta-positive)" if broker_connected else "var(--theta-negative)"
    broker_label = "Connected to Zerodha Kite" if broker_connected else "Broker disconnected"
    sys_color = "var(--theta-positive)" if system_operational else "var(--theta-warning)"
    sys_label = "All systems operational" if system_operational else "Degraded"
    st.markdown(
        (
            "<div class='theta-footer-bar'>"
            "<span>"
            f"<span class='theta-footer-dot' style='background:{broker_color};'></span>"
            f"{html.escape(broker_label)}"
            f"&nbsp;&nbsp;·&nbsp;&nbsp;Data as of: {html.escape(as_of)}"
            f"&nbsp;&nbsp;·&nbsp;&nbsp;<span class='theta-footer-dot' "
            f"style='background:{sys_color};'></span>{html.escape(sys_label)}"
            "</span>"
            f"<span>THETA AI TRADER {html.escape(version)}</span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
