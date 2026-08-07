"""Real TradingView Lightweight Charts renderer for candlestick market data.

Renders exclusively from an already-fetched :class:`MarketChartView` — never
fetches data itself, never fabricates candles, EMA/VWAP overlays, or markers.
"""

from __future__ import annotations

import json
from datetime import datetime

import streamlit as st

from dashboard.view_models import MarketChartView

_LIGHTWEIGHT_CHARTS_CDN = (
    "https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"
)


def _to_unix(iso_time: str) -> int:
    """Convert an ISO-8601 timestamp to Unix seconds for the charting library."""
    return int(datetime.fromisoformat(iso_time).timestamp())


def render_lightweight_chart(view: MarketChartView, *, height: int = 440) -> None:
    """Render a real candlestick chart with volume, EMA, VWAP, and markers.

    Args:
        view: Real chart series produced by the dashboard facade. An empty
            ``view.candles`` renders an honest empty-state panel instead of
            a chart.
        height: Panel height in pixels.
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
    ema_fast = [{"time": _to_unix(t), "value": v} for t, v in view.ema_fast]
    ema_slow = [{"time": _to_unix(t), "value": v} for t, v in view.ema_slow]
    vwap = [{"time": _to_unix(t), "value": v} for t, v in view.vwap]
    markers = [
        {
            "time": _to_unix(marker.time),
            "position": "aboveBar" if marker.position == "above" else "belowBar",
            "color": "#3d8bff" if marker.kind == "ai_signal" else "#f5a623",
            "shape": "arrowDown" if marker.position == "above" else "arrowUp",
            "text": marker.label,
        }
        for marker in view.markers
    ]

    payload = json.dumps(
        {
            "candles": candles,
            "volumes": volumes,
            "emaFast": ema_fast,
            "emaSlow": ema_slow,
            "vwap": vwap,
            "markers": markers,
        }
    )

    root_id = f"theta-tv-chart-{view.underlying.replace(' ', '_')}"
    html = f"""
    <div id="{root_id}" style="height:{height}px; width:100%;"></div>
    <script src="{_LIGHTWEIGHT_CHARTS_CDN}"></script>
    <script>
      (function() {{
        const data = {payload};
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
          rightPriceScale: {{ borderColor: '#232838' }},
          crosshair: {{ mode: 0 }}
        }});

        const candleSeries = chart.addCandlestickSeries({{
          upColor: '#3d8bff', downColor: '#ff5252',
          borderUpColor: '#3d8bff', borderDownColor: '#ff5252',
          wickUpColor: '#3d8bff', wickDownColor: '#ff5252'
        }});
        candleSeries.setData(data.candles);
        if (data.markers.length) {{
          candleSeries.setMarkers(data.markers);
        }}

        const volumeSeries = chart.addHistogramSeries({{
          priceFormat: {{ type: 'volume' }},
          priceScaleId: '',
          scaleMargins: {{ top: 0.82, bottom: 0 }}
        }});
        volumeSeries.setData(data.volumes);

        if (data.emaFast.length) {{
          chart.addLineSeries({{ color: '#f5a623', lineWidth: 1 }}).setData(data.emaFast);
        }}
        if (data.emaSlow.length) {{
          chart.addLineSeries({{ color: '#9b6dff', lineWidth: 1 }}).setData(data.emaSlow);
        }}
        if (data.vwap.length) {{
          chart.addLineSeries({{ color: '#00c9a7', lineWidth: 1 }}).setData(data.vwap);
        }}

        chart.timeScale().fitContent();
        new ResizeObserver((entries) => {{
          for (const entry of entries) {{
            chart.applyOptions({{ width: entry.contentRect.width }});
          }}
        }}).observe(container);
      }})();
    </script>
    """
    st.components.v1.html(html, height=height + 10)
