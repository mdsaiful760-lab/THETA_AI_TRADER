"""Unit tests for the real Market page candlestick/EMA/VWAP chart series."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dashboard.dashboard_facade import DashboardIntegrationFacade
from dashboard.view_models import MarketChartView

FIXED_NOW = datetime(2026, 8, 6, 10, 0, 0, tzinfo=timezone.utc)


def _candle_row(offset_minutes: int, *, o: float, h: float, l: float, c: float, v: int) -> dict:
    """Build one raw Kite-shaped candle row (real broker field names)."""
    return {
        "date": FIXED_NOW + timedelta(minutes=offset_minutes),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
    }


_THREE_CANDLES = (
    _candle_row(0, o=100.0, h=101.0, l=99.0, c=100.0, v=1000),
    _candle_row(5, o=100.0, h=102.0, l=100.0, c=101.0, v=1200),
    _candle_row(10, o=101.0, h=103.0, l=100.0, c=102.0, v=900),
)


class TestMarketChartOffline:
    def test_offline_without_session(self) -> None:
        facade = DashboardIntegrationFacade(session=None, clock=lambda: FIXED_NOW)
        chart = facade.get_market_chart("NIFTY")
        assert isinstance(chart, MarketChartView)
        assert chart.source == "offline"
        assert chart.candles == ()
        assert chart.markers == ()

    def test_none_from_session_yields_offline(self) -> None:
        session = SimpleNamespace(get_underlying_candles=lambda underlying: None)
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        chart = facade.get_market_chart("NIFTY")
        assert chart.source == "offline"


class TestMarketChartLiveCandles:
    def test_real_candles_are_normalized_sorted_and_deduped(self) -> None:
        # Includes a duplicate timestamp (last one wins) and one malformed row.
        rows = (
            _THREE_CANDLES[1],
            _THREE_CANDLES[0],
            {"date": FIXED_NOW, "open": "bad", "high": 1, "low": 1, "close": 1, "volume": 1},
            _THREE_CANDLES[2],
            dict(_THREE_CANDLES[0], close=999.0),  # duplicate ts, different close
        )
        session = SimpleNamespace(
            get_underlying_candles=lambda underlying: SimpleNamespace(
                underlying=underlying, interval="5minute", candles=rows
            )
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)

        chart = facade.get_market_chart("NIFTY")

        assert chart.source == "live"
        assert len(chart.candles) == 3
        times = [row[0] for row in chart.candles]
        assert times == sorted(times)
        assert chart.candles[0][4] == 999.0  # last duplicate wins, real value kept

    def test_ema_and_vwap_are_real_arithmetic_over_fetched_candles(self) -> None:
        session = SimpleNamespace(
            get_underlying_candles=lambda underlying: SimpleNamespace(
                underlying=underlying, interval="5minute", candles=_THREE_CANDLES
            )
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)

        chart = facade.get_market_chart("NIFTY")

        assert len(chart.ema_fast) == 3
        assert len(chart.ema_slow) == 3
        assert len(chart.vwap) == 3
        # EMA seeds at the first real close.
        assert chart.ema_fast[0][1] == pytest.approx(100.0)
        assert chart.ema_slow[0][1] == pytest.approx(100.0)
        # Second EMA(9) point: k=2/10=0.2 -> 101*0.2 + 100*0.8 = 100.2
        assert chart.ema_fast[1][1] == pytest.approx(100.2)
        # VWAP is cumulative(typical*volume)/cumulative(volume) over real bars.
        vwap0 = (101.0 + 99.0 + 100.0) / 3.0
        assert chart.vwap[0][1] == pytest.approx(vwap0)
        pv1 = vwap0 * 1000.0 + ((102.0 + 100.0 + 101.0) / 3.0) * 1200.0
        vwap1 = pv1 / 2200.0
        assert chart.vwap[1][1] == pytest.approx(vwap1)

    def test_empty_candles_from_upstream_still_reports_live_source(self) -> None:
        session = SimpleNamespace(
            get_underlying_candles=lambda underlying: SimpleNamespace(
                underlying=underlying, interval="5minute", candles=()
            )
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        chart = facade.get_market_chart("NIFTY")
        assert chart.source == "live"
        assert chart.candles == ()
        assert chart.ema_fast == ()


class TestMarketChartAiSignalMarkers:
    def _session(self, *, panel: object | None) -> SimpleNamespace:
        kwargs = {
            "get_underlying_candles": lambda underlying: SimpleNamespace(
                underlying=underlying, interval="5minute", candles=_THREE_CANDLES
            ),
        }
        if panel is not None:
            kwargs["get_ai_panel"] = lambda: panel
        return SimpleNamespace(**kwargs)

    def test_marker_placed_at_nearest_real_candle_on_trade_candidate(self) -> None:
        panel = SimpleNamespace(
            signal_active=True,
            underlying="NIFTY",
            as_of=FIXED_NOW + timedelta(minutes=5),
            strategy_id="iron_condor",
        )
        facade = DashboardIntegrationFacade(
            session=self._session(panel=panel), clock=lambda: FIXED_NOW
        )

        chart = facade.get_market_chart("NIFTY")

        assert len(chart.markers) == 1
        marker = chart.markers[0]
        assert marker.kind == "ai_signal"
        assert marker.price == 101.0  # real close of the candle at +5min
        assert "iron_condor" in marker.label

    def test_no_marker_when_signal_not_active(self) -> None:
        panel = SimpleNamespace(
            signal_active=False,
            underlying="NIFTY",
            as_of=FIXED_NOW,
            strategy_id="iron_condor",
        )
        facade = DashboardIntegrationFacade(
            session=self._session(panel=panel), clock=lambda: FIXED_NOW
        )
        chart = facade.get_market_chart("NIFTY")
        assert chart.markers == ()

    def test_no_marker_when_decision_is_for_a_different_underlying(self) -> None:
        panel = SimpleNamespace(
            signal_active=True,
            underlying="BANKNIFTY",
            as_of=FIXED_NOW,
            strategy_id="iron_condor",
        )
        facade = DashboardIntegrationFacade(
            session=self._session(panel=panel), clock=lambda: FIXED_NOW
        )
        chart = facade.get_market_chart("NIFTY")
        assert chart.markers == ()

    def test_no_marker_when_no_ai_panel_attached(self) -> None:
        facade = DashboardIntegrationFacade(
            session=self._session(panel=None), clock=lambda: FIXED_NOW
        )
        chart = facade.get_market_chart("NIFTY")
        assert chart.markers == ()
