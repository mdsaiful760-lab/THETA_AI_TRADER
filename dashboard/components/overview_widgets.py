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


def _trend_arrow(trend: tuple[float, ...]) -> str:
    """Real trend-direction arrow from the last two real captured readings.

    Never fabricated — returns a neutral dash until at least two real
    readings have actually been observed.
    """
    if len(trend) < 2:
        return "<span class='theta-trend-arrow flat'>&#8212;</span>"
    delta = trend[-1] - trend[-2]
    if delta > 0:
        return "<span class='theta-trend-arrow up'>&#9650;</span>"
    if delta < 0:
        return "<span class='theta-trend-arrow down'>&#9660;</span>"
    return "<span class='theta-trend-arrow flat'>&#8212;</span>"


def render_metric_cards(cards: tuple[MetricCardView, ...], *, spark_color: str = "#3D8BFF") -> None:
    """Render the top-row premium metric cards (Market Regime / VIX / PCR / Max Pain / FII-DII)."""
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
            arrow = _trend_arrow(card.trend)
            st.markdown(
                (
                    "<div class='theta-metric-card'>"
                    "<div class='theta-metric-head'>"
                    f"<span>{html.escape(card.label)}</span>"
                    f"<span class='theta-metric-icon'>{icon}</span>"
                    "</div>"
                    "<div class='theta-metric-value-row'>"
                    f"<span class='theta-metric-value'>{html.escape(card.value)}</span>"
                    f"{arrow}"
                    "</div>"
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
        dot_class = {"healthy": "up", "degraded": "warn", "down": "down"}.get(row.state, "down")
        state_label = {"healthy": "Healthy", "degraded": "Degraded", "down": "Down"}.get(
            row.state, "Down"
        )
        body.append(
            "<div class='theta-engine-row'>"
            "<span class='theta-engine-name'>"
            f"<span class='theta-engine-dot {dot_class}'></span>"
            f"{html.escape(row.name)}</span>"
            "<span class='theta-engine-right'>"
            f"<span>{html.escape(state_label)}</span>"
            f"<span>{html.escape(row.latency_display)}</span>"
            f"<span class='theta-engine-heartbeat'>{html.escape(row.heartbeat)}</span>"
            "</span></div>"
        )
    st.markdown("".join(body), unsafe_allow_html=True)


def _oi_rows_table(rows: tuple[OiBuildupRow, ...], *, value_label: str) -> None:
    """Render one CALLS/PUTS ranked table (shared by Top OI and Top Volume)."""
    if not rows:
        st.caption("Option chain unavailable — awaiting backend market snapshot")
        return
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
        columns=["Strike", value_label, "OI Chg", "LTP", "Trend"],
    )
    render_table(frame)


def render_option_summary(
    *,
    atm_strike: str,
    atm_iv: str,
    pcr: str,
    max_pain: str,
    nearest_expiry: str,
    oi_calls: tuple[OiBuildupRow, ...],
    oi_puts: tuple[OiBuildupRow, ...],
    volume_calls: tuple[OiBuildupRow, ...],
    volume_puts: tuple[OiBuildupRow, ...],
) -> None:
    """Render the Option Summary panel: real ATM/IV/PCR/Max Pain/Expiry
    KPIs plus real Top OI and Top Volume rankings (CALLS/PUTS)."""
    st.markdown("<div class='theta-panel-title'>Option Summary</div>", unsafe_allow_html=True)

    row1 = st.columns(3)
    row1[0].metric("ATM Strike", atm_strike)
    row1[1].metric("ATM IV", atm_iv)
    row1[2].metric("PCR", pcr)
    row2 = st.columns(2)
    row2[0].metric("Max Pain", max_pain)
    row2[1].metric("Nearest Expiry", nearest_expiry)

    metric_choice = st.radio(
        "Rank by", options=("Top OI", "Top Volume"), horizontal=True,
        label_visibility="collapsed", key="theta_option_summary_metric",
    )
    calls, puts, value_label = (
        (oi_calls, oi_puts, "OI") if metric_choice == "Top OI" else (volume_calls, volume_puts, "Volume")
    )
    calls_tab, puts_tab = st.tabs(["CALLS", "PUTS"])
    with calls_tab:
        _oi_rows_table(calls, value_label=value_label)
    with puts_tab:
        _oi_rows_table(puts, value_label=value_label)


def render_strategy_scanner(rows: tuple[ScannerRow, ...]) -> None:
    """Render the Strategy Scanner panel."""
    st.markdown("<div class='theta-panel-title'>Strategy Scanner</div>", unsafe_allow_html=True)
    if not rows:
        st.caption("No monitored strategies available")
        return
    frame = pd.DataFrame(
        [
            (
                row.strategy, row.confidence, row.expected_pop, row.expected_roi,
                row.expected_theta, row.status,
            )
            for row in rows
        ],
        columns=[
            "Strategy", "Confidence", "Expected POP", "Expected ROI", "Expected Theta", "Status",
        ],
    )
    render_table(frame)
    if any(row.expected_roi == PLACEHOLDER for row in rows):
        st.caption(
            "Expected ROI and Expected Theta have no corresponding field in the "
            "strategy evaluation engine yet — shown as placeholders, never fabricated."
        )


_ALERT_CATEGORIES: tuple[str, ...] = ("All", "Market", "AI", "Broker", "Execution", "System")


def _render_alert_rows(alerts: tuple[AlertRow, ...]) -> None:
    """Render one flat real alert timeline."""
    if not alerts:
        st.caption("No alerts in this category")
        return
    body = []
    for alert in alerts:
        color = _SEVERITY_COLORS.get(alert.severity, "var(--theta-accent)")
        body.append(
            "<div class='theta-alert-row'>"
            f"<span class='theta-alert-dot' style='background:{color};'></span>"
            "<div>"
            f"<div class='theta-alert-title'>{html.escape(alert.title)}</div>"
            f"<div class='theta-alert-detail'>{html.escape(alert.category)} · "
            f"{html.escape(alert.detail)}</div>"
            "</div>"
            f"<span class='theta-alert-time'>{html.escape(alert.timestamp)}</span>"
            "</div>"
        )
    st.markdown("".join(body), unsafe_allow_html=True)


def render_alerts(alerts: tuple[AlertRow, ...]) -> None:
    """Render the Recent Alerts timeline, filterable by real category
    (Market / AI / Broker / Execution / System) derived from each real log
    entry's own logger name."""
    st.markdown("<div class='theta-panel-title'>Recent Alerts</div>", unsafe_allow_html=True)
    if not alerts:
        st.caption("No recent log activity")
        return
    present = {alert.category for alert in alerts}
    options = tuple(cat for cat in _ALERT_CATEGORIES if cat == "All" or cat in present)
    choice = st.radio(
        "Alert category", options=options, horizontal=True,
        label_visibility="collapsed", key="theta_alerts_category",
    )
    filtered = alerts if choice == "All" else tuple(a for a in alerts if a.category == choice)
    _render_alert_rows(filtered)


def render_market_breadth_placeholder() -> None:
    """Render an honest unavailable state for Market Breadth (no NSE-wide feed)."""
    st.markdown("<div class='theta-panel-title'>Market Breadth (NSE)</div>", unsafe_allow_html=True)
    st.markdown(
        (
            "<div class='theta-unavailable-panel' style='padding:1.4rem 1rem;'>"
            "<div class='title' style='font-size:0.92rem;'>Feed unavailable</div>"
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
