"""Real TradingView Lightweight Charts renderer for candlestick market data.

Renders exclusively from an already-fetched :class:`MarketChartView` — never
fetches data itself, never fabricates candles, EMA/VWAP overlays, or markers.
EMA(9)/EMA(20)/EMA(50)/VWAP are always present in the view; the indicator
toggles here only control client-side visibility, never a refetch or a
recomputation. Entry/AI-signal markers and SL/Target price lines are drawn
only when the view carries real backend-sourced values for them.
"""

from __future__ import annotations

import json
from datetime import datetime

import streamlit as st

from dashboard.view_models import MarketChartView

_LIGHTWEIGHT_CHARTS_CDN = (
    "https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"
)

_INDICATOR_COLORS: dict[str, str] = {
    "ema9": "#f5a623",
    "ema20": "#9b6dff",
    "ema50": "#3d8bff",
    "vwap": "#00c9a7",
}

_MARKER_STYLE: dict[str, dict[str, str]] = {
    "ai_signal": {"color": "#3d8bff", "shape_above": "arrowDown", "shape_below": "arrowUp"},
    "entry": {"color": "#3ddc84", "shape_above": "arrowDown", "shape_below": "arrowUp"},
    "exit": {"color": "#ff5252", "shape_above": "arrowDown", "shape_below": "arrowUp"},
}

_PRICE_LINE_STYLE: dict[str, str] = {
    "sl": "#ff5252",
    "target": "#3ddc84",
}


def _to_unix(iso_time: str) -> int:
    """Convert an ISO-8601 timestamp to Unix seconds for the charting library."""
    return int(datetime.fromisoformat(iso_time).timestamp())


def _render_indicator_toggles(key_prefix: str) -> dict[str, bool]:
    """Render EMA9/EMA20/EMA50/VWAP enable/disable checkboxes.

    Pure display toggles — every series is already computed in the real
    fetched view; this only controls which already-real overlays are
    drawn on the chart.
    """
    cols = st.columns(4)
    labels = (("ema9", "EMA 9"), ("ema20", "EMA 20"), ("ema50", "EMA 50"), ("vwap", "VWAP"))
    visible: dict[str, bool] = {}
    for column, (field, label) in zip(cols, labels):
        with column:
            visible[field] = st.checkbox(label, value=True, key=f"{key_prefix}_{field}")
    return visible


def render_lightweight_chart(
    view: MarketChartView, *, height: int = 440, key_prefix: str = "theta_chart"
) -> None:
    """Render a real candlestick chart with volume, EMA(9/20/50), VWAP, and AI overlays.

    Args:
        view: Real chart series produced by the dashboard facade. An empty
            ``view.candles`` renders an honest empty-state panel instead of
            a chart.
        height: Panel height in pixels.
        key_prefix: Unique Streamlit widget key prefix — required whenever
            more than one chart instance can render in the same page run.
    """
    if not view.candles:
        st.markdown(
            (
                f"<div class='theta-chart-panel' style='height:{height}px;'>"
                "<div class='theta-chart-caption'>"
                f"No real candle data available for {view.underlying} ({view.source})"
                "</div></div>"
            ),
            unsafe_allow_html=True,
        )
        return

    visible = _render_indicator_toggles(f"{key_prefix}_{view.underlying}")

    candles = [
        {"time": _to_unix(t), "open": o, "high": h, "low": l, "close": c}
        for t, o, h, l, c, _v in view.candles
    ]
    volumes = [
        {
            "time": _to_unix(t),
            "value": v,
            "color": "rgba(61, 139, 255, 0.35)" if c >= o else "rgba(255, 82, 82, 0.35)",
        }
        for t, o, _h, _l, c, v in view.candles
    ]
    indicator_series = {
        "ema9": [{"time": _to_unix(t), "value": v} for t, v in view.ema9] if visible["ema9"] else [],
        "ema20": (
            [{"time": _to_unix(t), "value": v} for t, v in view.ema20] if visible["ema20"] else []
        ),
        "ema50": (
            [{"time": _to_unix(t), "value": v} for t, v in view.ema50] if visible["ema50"] else []
        ),
        "vwap": [{"time": _to_unix(t), "value": v} for t, v in view.vwap] if visible["vwap"] else [],
    }
    markers = [
        {
            "time": _to_unix(marker.time),
            "position": "aboveBar" if marker.position == "above" else "belowBar",
            "color": _MARKER_STYLE.get(marker.kind, _MARKER_STYLE["ai_signal"])["color"],
            "shape": _MARKER_STYLE.get(marker.kind, _MARKER_STYLE["ai_signal"])[
                "shape_above" if marker.position == "above" else "shape_below"
            ],
            "text": marker.label,
        }
        for marker in view.markers
    ]
    price_lines = [
        {
            "price": line.price,
            "color": _PRICE_LINE_STYLE.get(line.kind, "#8892a0"),
            "title": line.label,
        }
        for line in view.price_lines
    ]

    payload = json.dumps(
        {
            "candles": candles,
            "volumes": volumes,
            "indicators": indicator_series,
            "colors": _INDICATOR_COLORS,
            "markers": markers,
            "priceLines": price_lines,
            "underlying": view.underlying,
            "interval": view.interval,
        }
    )

    root_id = f"{key_prefix}-{view.underlying.replace(' ', '_')}"
    storage_key = f"theta_chart_state_{root_id}"
    html = f"""
    <div id="{root_id}" style="height:{height}px; width:100%;"></div>
    <script src="{_LIGHTWEIGHT_CHARTS_CDN}"></script>
    <script>
      (function() {{
        const data = {payload};
        const storageKey = {json.dumps(storage_key)};
        const container = document.getElementById('{root_id}');
        if (!container || typeof LightweightCharts === 'undefined') {{ return; }}

        const chart = LightweightCharts.createChart(container, {{
          width: container.clientWidth,
          height: {height},
          layout: {{ background: {{ color: '#0b0e14' }}, textColor: '#c7ccd6' }},
          grid: {{
            vertLines: {{ color: 'rgba(255,255,255,0.05)' }},
            horzLines: {{ color: 'rgba(255,255,255,0.05)' }}
          }},
          timeScale: {{ timeVisible: true, secondsVisible: false, borderColor: '#232838' }},
          rightPriceScale: {{ borderColor: '#232838', autoScale: true }},
          crosshair: {{ mode: 0 }},
          handleScroll: {{ mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true }},
          handleScale: {{ mouseWheel: true, pinch: true, axisPressedMouseMove: true }}
        }});

        const candleSeries = chart.addCandlestickSeries({{
          upColor: '#3d8bff', downColor: '#ff5252',
          borderUpColor: '#3d8bff', borderDownColor: '#ff5252',
          wickUpColor: '#3d8bff', wickDownColor: '#ff5252'
        }});

        // Live-update continuity: identical earlier bars use series.update()
        // (append/refresh) instead of a full setData() reset, and the
        // user's own pan/zoom viewport is restored across refreshes — a
        // fresh iframe is unavoidable on each Streamlit fragment rerun, but
        // the chart state itself is preserved via localStorage rather than
        // visibly resetting.
        let previous = null;
        try {{ previous = JSON.parse(window.localStorage.getItem(storageKey) || 'null'); }}
        catch (e) {{ previous = null; }}

        const sameSeries = previous && previous.underlying === data.underlying
          && previous.interval === data.interval && previous.candles && previous.candles.length;

        if (sameSeries && data.candles.length >= previous.candles.length) {{
          const prevLen = previous.candles.length;
          const overlapOk = data.candles.length >= prevLen
            && (prevLen < 2 || data.candles[prevLen - 2].time === previous.candles[prevLen - 2].time);
          if (overlapOk) {{
            candleSeries.setData(data.candles.slice(0, prevLen - 1));
            for (let i = prevLen - 1; i < data.candles.length; i++) {{
              candleSeries.update(data.candles[i]);
            }}
          }} else {{
            candleSeries.setData(data.candles);
          }}
        }} else {{
          candleSeries.setData(data.candles);
        }}

        if (data.markers.length) {{
          candleSeries.setMarkers(data.markers);
        }}
        for (const line of data.priceLines) {{
          candleSeries.createPriceLine({{
            price: line.price,
            color: line.color,
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: line.title
          }});
        }}

        const volumeSeries = chart.addHistogramSeries({{
          priceFormat: {{ type: 'volume' }},
          priceScaleId: '',
          scaleMargins: {{ top: 0.82, bottom: 0 }}
        }});
        volumeSeries.setData(data.volumes);

        for (const key of Object.keys(data.indicators)) {{
          const series = data.indicators[key];
          if (series.length) {{
            chart.addLineSeries({{ color: data.colors[key], lineWidth: 1 }}).setData(series);
          }}
        }}

        if (sameSeries && previous.range) {{
          try {{ chart.timeScale().setVisibleLogicalRange(previous.range); }}
          catch (e) {{ chart.timeScale().fitContent(); }}
        }} else {{
          chart.timeScale().fitContent();
        }}

        const persistState = () => {{
          let range = null;
          try {{ range = chart.timeScale().getVisibleLogicalRange(); }} catch (e) {{ range = null; }}
          try {{
            window.localStorage.setItem(storageKey, JSON.stringify({{
              underlying: data.underlying,
              interval: data.interval,
              candles: data.candles,
              range: range
            }}));
          }} catch (e) {{ /* storage unavailable — live updates still render */ }}
        }};
        chart.timeScale().subscribeVisibleLogicalRangeChange(persistState);
        persistState();

        new ResizeObserver((entries) => {{
          for (const entry of entries) {{
            chart.applyOptions({{ width: entry.contentRect.width }});
          }}
        }}).observe(container);
      }})();
    </script>
    """
    st.components.v1.html(html, height=height + 10)
