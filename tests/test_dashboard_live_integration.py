"""Deterministic unit tests for Dashboard Live Integration."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dashboard.dashboard_facade import HOME_MARKET_INDEX_SYMBOLS, DashboardFacade
from dashboard.live_session_adapter import (
    DashboardLiveHandles,
    DashboardLiveSessionAdapter,
    build_default_presentation_facade,
    build_live_presentation_facade,
    clear_live_handles,
    register_live_handles,
)
from dashboard.view_models import PLACEHOLDER


FIXED_NOW = datetime(2026, 8, 6, 6, 30, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_handles() -> None:
    """Ensure process-level live handles do not leak across tests."""
    clear_live_handles()
    yield
    clear_live_handles()


def _underlying(
    symbol: str,
    *,
    last_price: float,
    change: float,
    change_percent: float,
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        last_price=last_price,
        change=change,
        change_percent=change_percent,
        quote_timestamp=FIXED_NOW,
        volume=1_000_000,
    )


def _streaming_stub() -> MagicMock:
    """Build a MarketDataStreamingEngine-like stub with three index snapshots."""
    nifty = SimpleNamespace(
        underlying=_underlying("NIFTY", last_price=24512.4, change=85.2, change_percent=0.35),
        volatility=SimpleNamespace(
            symbol="INDIA VIX",
            last_price=13.22,
            change=-0.15,
            change_percent=-1.12,
            quote_timestamp=FIXED_NOW,
        ),
    )
    bank = SimpleNamespace(
        underlying=_underlying(
            "BANKNIFTY", last_price=52100.15, change=-120.4, change_percent=-0.23
        ),
        volatility=None,
    )
    sensex = SimpleNamespace(
        underlying=_underlying(
            "SENSEX", last_price=81234.5, change=10.0, change_percent=0.01
        ),
        volatility=None,
    )
    streaming = MagicMock()
    streaming.get_snapshot.side_effect = lambda symbol: {
        "NIFTY": nifty,
        "BANKNIFTY": bank,
        "SENSEX": sensex,
    }.get(symbol)
    streaming.get_health.return_value = SimpleNamespace(status="healthy")
    return streaming


class TestOfflineDefault:
    """T01: Offline preserved when no handles registered."""

    def test_default_facade_is_offline(self) -> None:
        facade = build_default_presentation_facade()
        assert facade.is_connected is False
        indices = facade.get_home_market_indices()
        assert len(indices.indices) == 4
        assert all(q.connection_status == "OFFLINE" for q in indices.indices)
        assert all(q.ltp == PLACEHOLDER for q in indices.indices)

    def test_empty_handles_stay_offline(self) -> None:
        adapter = DashboardLiveSessionAdapter(DashboardLiveHandles())
        assert adapter.get_index_quotes() == ()
        health = adapter.get_health()
        assert health.session_state.value == "stopped"


class TestStreamingIndexMapping:
    """T02/T03: Streaming + websocket connection mapping."""

    def test_maps_four_symbols_including_india_vix(self) -> None:
        ws = MagicMock()
        ws.get_status.return_value = SimpleNamespace(value="connected")
        handles = DashboardLiveHandles(
            market_streaming=_streaming_stub(),
            kite_websocket=ws,
        )
        facade = build_live_presentation_facade(handles, clock=lambda: FIXED_NOW)
        payload = facade.get_home_market_indices()
        symbols = tuple(q.symbol for q in payload.indices)
        assert symbols == HOME_MARKET_INDEX_SYMBOLS
        by_symbol = {q.symbol: q for q in payload.indices}
        assert by_symbol["NIFTY"].ltp == "24,512.40"
        assert by_symbol["NIFTY"].change_abs == "+85.20"
        assert by_symbol["NIFTY"].change_pct == "+0.35%"
        assert "2026-08-06" in by_symbol["NIFTY"].last_update
        assert by_symbol["NIFTY"].connection_status == "LIVE"
        assert by_symbol["INDIA VIX"].ltp == "13.22"
        assert by_symbol["INDIA VIX"].connection_status == "LIVE"

    def test_websocket_disconnected_marks_offline(self) -> None:
        ws = MagicMock()
        ws.get_status.return_value = "disconnected"
        streaming = _streaming_stub()
        # Force connectivity inference to honor websocket disconnect.
        streaming.get_health.return_value = SimpleNamespace(status="stopped")
        streaming.get_snapshot.return_value = None
        streaming.get_snapshot.side_effect = None
        handles = DashboardLiveHandles(
            market_streaming=streaming,
            kite_websocket=ws,
        )
        adapter = DashboardLiveSessionAdapter(handles, clock=lambda: FIXED_NOW)
        assert adapter.get_health().broker_connection.connected is False
        facade = DashboardFacade(session=adapter, clock=lambda: FIXED_NOW)
        payload = facade.get_home_market_indices()
        assert all(q.connection_status == "OFFLINE" for q in payload.indices)


class TestPaperAndStrategyMapping:
    """T04/T05: Paper runner and evaluation bundle mapping."""

    def test_paper_runner_maps_home_kpis(self) -> None:
        runner = MagicMock()
        runner.simulate_plan = MagicMock()
        runner.get_portfolio_view.return_value = SimpleNamespace(
            capital=SimpleNamespace(cash=100000.0, reserved_margin_hint=25000.0),
            positions=SimpleNamespace(
                positions=(
                    SimpleNamespace(
                        instrument_key="NIFTY",
                        quantity=-50,
                        average_price=120.5,
                        mark_price=118.0,
                        unrealized_pnl=125.0,
                        strategy_id="iron_condor",
                    ),
                )
            ),
            total_realized_pnl=200.0,
            total_unrealized_pnl=250.0,
            open_position_count=1,
            gross_notional=25000.0,
        )
        runner.get_capital_snapshot.return_value = SimpleNamespace(
            cash=100000.0,
            reserved_margin_hint=25000.0,
            cumulative_realized_pnl=200.0,
        )
        runner.get_position_book.return_value = SimpleNamespace(
            positions=runner.get_portfolio_view.return_value.positions.positions
        )
        handles = DashboardLiveHandles(paper_runner=runner)
        facade = build_live_presentation_facade(handles, clock=lambda: FIXED_NOW)
        home = facade.get_home_snapshot()
        assert home.kpis.paper_pnl in {"250.0", "250.00", "250"}
        assert home.kpis.open_positions == "1"
        runner.simulate_plan.assert_not_called()

    def test_evaluation_bundle_maps_strategy_and_confidence(self) -> None:
        bundle = SimpleNamespace(
            market_regime="RANGE_BOUND",
            evaluated_at=FIXED_NOW,
            summary=SimpleNamespace(
                top_strategy_id="iron_condor",
                top_ranking_score=88.25,
            ),
            ranked_reports=(
                SimpleNamespace(
                    strategy_id="iron_condor",
                    strategy_family="iron_condor",
                    display_name="Iron Condor",
                    ranking_score=88.25,
                    evaluation_status="success",
                    outcome_class="actionable",
                    confidence=SimpleNamespace(overall_score=0.77),
                    reasons=("regime_fit",),
                    evaluated_at=FIXED_NOW,
                ),
            ),
            reports=(),
            metadata={"market_regime": "RANGE_BOUND"},
        )
        handles = DashboardLiveHandles(
            evaluation_bundle_provider=lambda: bundle,
            market_regime_provider=lambda: SimpleNamespace(regime="RANGE_BOUND"),
        )
        facade = build_live_presentation_facade(handles, clock=lambda: FIXED_NOW)
        home = facade.get_home_snapshot()
        assert home.kpis.active_strategy == "Iron Condor"
        assert home.kpis.confidence == "77.0%"
        assert home.kpis.market_regime == "RANGE_BOUND"
        monitor = facade.get_strategy_monitor()
        assert monitor.active_strategy == "Iron Condor"
        assert monitor.strategies[1].score == "88.25"


class TestNoComputationSideEffects:
    """T06/T07: Never score or simulate from the adapter."""

    def test_scoring_framework_score_never_called(self) -> None:
        framework = MagicMock()
        framework.health.return_value = SimpleNamespace(health="healthy")
        framework.statistics.return_value = SimpleNamespace()
        handles = DashboardLiveHandles(scoring_framework=framework)
        adapter = DashboardLiveSessionAdapter(handles)
        adapter.get_health()
        framework.health.assert_called()
        framework.score.assert_not_called()

    def test_paper_simulate_never_called_on_ledger_read(self) -> None:
        runner = MagicMock()
        runner.get_portfolio_view.return_value = SimpleNamespace(
            capital=SimpleNamespace(cash=1.0),
            positions=SimpleNamespace(positions=()),
            total_realized_pnl=0.0,
            total_unrealized_pnl=0.0,
            gross_notional=0.0,
        )
        runner.get_capital_snapshot.return_value = SimpleNamespace(cash=1.0)
        runner.get_position_book.return_value = SimpleNamespace(positions=())
        handles = DashboardLiveHandles(paper_runner=runner)
        facade = build_live_presentation_facade(handles, clock=lambda: FIXED_NOW)
        facade.get_paper_trading()
        runner.simulate_plan.assert_not_called()
        runner.mark_to_market.assert_not_called()


class TestConcurrencyAndRegistration:
    """T08/T09: Thread-safety and handle registration."""

    def test_concurrent_reads_are_stable(self) -> None:
        handles = DashboardLiveHandles(
            market_streaming=_streaming_stub(),
            kite_websocket=MagicMock(get_status=MagicMock(return_value="connected")),
        )
        adapter = DashboardLiveSessionAdapter(handles, clock=lambda: FIXED_NOW)
        errors: list[BaseException] = []

        def _reader() -> None:
            try:
                for _ in range(25):
                    quotes = adapter.get_index_quotes()
                    assert len(quotes) == 4
                    adapter.get_health()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_reader) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []

    def test_register_and_clear_live_handles(self) -> None:
        handles = DashboardLiveHandles(market_streaming=_streaming_stub())
        register_live_handles(handles)
        facade = build_default_presentation_facade()
        assert facade.is_connected is True
        clear_live_handles()
        offline = build_default_presentation_facade()
        assert offline.is_connected is False
