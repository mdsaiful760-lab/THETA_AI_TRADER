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


def _contract(
    *,
    tradingsymbol: str,
    strike: float,
    option_type: str,
    ltp: float,
    bid: float,
    ask: float,
    open_interest: int,
    volume: int,
    delta: float,
    gamma: float,
    theta: float,
    vega: float,
    iv: float,
) -> SimpleNamespace:
    return SimpleNamespace(
        tradingsymbol=tradingsymbol,
        strike=strike,
        option_type=SimpleNamespace(value=option_type),
        ltp=ltp,
        bid=bid,
        ask=ask,
        open_interest=open_interest,
        volume=volume,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        iv=iv,
    )


def _streaming_stub_with_option_chain(open_interest: int = 12_000) -> MagicMock:
    """Build a streaming stub whose NIFTY snapshot carries real option contracts."""
    contracts = (
        _contract(
            tradingsymbol="NIFTY24500CE", strike=24500.0, option_type="CE",
            ltp=142.5, bid=142.0, ask=143.0, open_interest=open_interest,
            volume=50_000, delta=0.52, gamma=0.002, theta=-8.1, vega=12.4, iv=14.8,
        ),
        _contract(
            tradingsymbol="NIFTY24500PE", strike=24500.0, option_type="PE",
            ltp=118.2, bid=117.5, ask=118.8, open_interest=open_interest + 500,
            volume=42_000, delta=-0.48, gamma=0.002, theta=-7.6, vega=12.1, iv=15.1,
        ),
    )
    nifty = SimpleNamespace(
        underlying=_underlying("NIFTY", last_price=24512.4, change=85.2, change_percent=0.35),
        volatility=None,
        option_chain=SimpleNamespace(
            contracts=contracts,
            metadata=SimpleNamespace(atm_strike=24500.0),
        ),
    )
    streaming = MagicMock()
    streaming.get_snapshot.side_effect = lambda symbol: nifty if symbol == "NIFTY" else None
    streaming.get_health.return_value = SimpleNamespace(status="healthy")
    return streaming


class TestOptionChainWithGreeks:
    """Real per-contract Greeks/OI reach the Market page, never fabricated."""

    def test_option_chain_rows_carry_real_greeks_and_oi(self) -> None:
        adapter = DashboardLiveSessionAdapter(
            DashboardLiveHandles(market_streaming=_streaming_stub_with_option_chain())
        )

        snap = adapter.get_market_snapshot()

        assert snap.atm_strike == "24500.0"
        assert len(snap.option_chain_rows) == 2
        by_symbol = {}
        for row in snap.option_chain_rows:
            values = dict(zip(snap.option_chain_columns, row))
            by_symbol[values["type"]] = values
        assert by_symbol["CE"]["delta"] == "0.52"
        assert by_symbol["CE"]["iv"] == "14.8"
        assert by_symbol["PE"]["gamma"] == "0.002"
        assert by_symbol["CE"]["bid"] == "142.0"
        assert by_symbol["CE"]["ask"] == "143.0"
        # First observation: no prior OI to diff against yet.
        assert by_symbol["CE"]["oi_change"] == PLACEHOLDER

    def test_oi_change_diffs_two_real_polls_never_fabricated(self) -> None:
        adapter = DashboardLiveSessionAdapter(
            DashboardLiveHandles(market_streaming=_streaming_stub_with_option_chain(open_interest=10_000))
        )
        adapter.get_market_snapshot()  # first poll seeds the baseline

        adapter._handles.market_streaming = _streaming_stub_with_option_chain(open_interest=10_750)
        snap = adapter.get_market_snapshot()

        values = {
            dict(zip(snap.option_chain_columns, row))["type"]: dict(zip(snap.option_chain_columns, row))
            for row in snap.option_chain_rows
        }
        assert values["CE"]["oi_change"] == "750"


class TestAiPanel:
    """AI reasoning/countdown are read-only soft-reads off AIDecisionLoop."""

    def test_returns_none_without_a_decision_loop_handle(self) -> None:
        adapter = DashboardLiveSessionAdapter(DashboardLiveHandles())
        assert adapter.get_ai_panel() is None

    def test_surfaces_real_reasons_and_computes_countdown_from_real_timing(self) -> None:
        decision = SimpleNamespace(
            reasons=("setup meets short strangle criteria", "confidence above threshold"),
            sizing_reason=None,
            decision_status="selected",
            risk_verdict="approved",
            strategy_id="short_strangle",
        )
        loop = MagicMock()
        loop.get_latest_decision.return_value = decision
        loop.get_health.return_value = SimpleNamespace(last_cycle_at=FIXED_NOW)
        loop.config = SimpleNamespace(interval_seconds=5.0)

        adapter = DashboardLiveSessionAdapter(
            DashboardLiveHandles(ai_decision_loop=loop), clock=lambda: FIXED_NOW
        )
        panel = adapter.get_ai_panel()

        assert panel is not None
        assert panel.reasons == decision.reasons
        assert panel.strategy_id == "short_strangle"
        # Same instant as last_cycle_at -> full interval remains.
        assert panel.next_evaluation_display == "5s"

    def test_returns_none_when_no_decision_has_run_yet(self) -> None:
        loop = MagicMock()
        loop.get_latest_decision.return_value = None
        adapter = DashboardLiveSessionAdapter(DashboardLiveHandles(ai_decision_loop=loop))

        assert adapter.get_ai_panel() is None


class TestUnderlyingCandles:
    """The chart's candle fetch reuses the already-connected market data
    engine's own broker link — never a new broker connection."""

    def test_delegates_to_streaming_engine_with_real_request_shape(self) -> None:
        engine = MagicMock()
        engine.fetch_historical_candles.return_value = SimpleNamespace(
            candles=(
                {
                    "date": FIXED_NOW,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 1000,
                },
            )
        )
        adapter = DashboardLiveSessionAdapter(
            DashboardLiveHandles(market_streaming=engine), clock=lambda: FIXED_NOW
        )

        result = adapter.get_underlying_candles("NIFTY")

        assert result is not None
        assert result.underlying == "NIFTY"
        assert len(result.candles) == 1
        request = engine.fetch_historical_candles.call_args.args[0]
        assert request.instrument_key == "NSE:NIFTY 50"
        assert request.interval == "5minute"
        assert request.to_ts == FIXED_NOW
        assert request.from_ts < FIXED_NOW

    def test_returns_none_for_unrecognized_underlying(self) -> None:
        adapter = DashboardLiveSessionAdapter(
            DashboardLiveHandles(market_streaming=MagicMock())
        )
        assert adapter.get_underlying_candles("DOWJONES") is None

    def test_returns_none_without_streaming_handle(self) -> None:
        adapter = DashboardLiveSessionAdapter(DashboardLiveHandles())
        assert adapter.get_underlying_candles("NIFTY") is None

    def test_returns_none_when_fetch_raises(self) -> None:
        engine = MagicMock()
        engine.fetch_historical_candles.side_effect = RuntimeError("broker timeout")
        adapter = DashboardLiveSessionAdapter(DashboardLiveHandles(market_streaming=engine))
        assert adapter.get_underlying_candles("NIFTY") is None


def _paper_runner_stub(*, cash: str, unrealized: str, starting_cash: str = "1000000.00") -> MagicMock:
    from decimal import Decimal

    runner = MagicMock()
    runner.get_capital_snapshot.return_value = SimpleNamespace(
        cash=Decimal(cash), starting_cash=Decimal(starting_cash)
    )
    runner.get_portfolio_view.return_value = SimpleNamespace(
        capital=SimpleNamespace(cash=Decimal(cash)),
        positions=SimpleNamespace(positions=()),
        total_realized_pnl=Decimal("0.00"),
        total_unrealized_pnl=Decimal(unrealized),
        gross_notional=Decimal("0.00"),
    )
    runner.get_position_book.return_value = SimpleNamespace(positions=())
    return runner


class TestEquityCurveAndRoi:
    """The paper ledger has no history of its own — the dashboard captures
    one real equity reading per poll and derives equity/drawdown/ROI from
    only those real, observed values."""

    def test_first_poll_seeds_a_single_real_equity_point(self) -> None:
        adapter = DashboardLiveSessionAdapter(
            DashboardLiveHandles(
                paper_runner=_paper_runner_stub(cash="1000000.00", unrealized="0.00")
            ),
            clock=lambda: FIXED_NOW,
        )

        snap = adapter.get_paper_trading_snapshot()

        assert len(snap.equity_series) == 1
        assert snap.equity_series[0][1] == 1000000.0
        assert snap.total_equity == 1000000.0
        assert snap.roi == 0.0

    def test_second_poll_appends_and_roi_reflects_real_pnl(self) -> None:
        times = iter([FIXED_NOW, FIXED_NOW.replace(minute=31)])
        handles = DashboardLiveHandles(
            paper_runner=_paper_runner_stub(cash="1000000.00", unrealized="0.00")
        )
        adapter = DashboardLiveSessionAdapter(handles, clock=lambda: next(times))
        adapter.get_paper_trading_snapshot()

        handles.paper_runner = _paper_runner_stub(cash="1004000.00", unrealized="500.00")
        snap = adapter.get_paper_trading_snapshot()

        assert len(snap.equity_series) == 2
        assert snap.total_equity == 1004500.0
        assert snap.roi == pytest.approx(0.45, rel=1e-3)
        # Equity only rose -> no drawdown yet.
        assert snap.drawdown_series[-1][1] == 0.0
