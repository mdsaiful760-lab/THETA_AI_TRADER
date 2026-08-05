"""Tests for the deterministic bull-put-spread strategy plugin."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, time, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import strategy.bull_put_spread_strategy as bull_put_module
from core.engine_context import EngineContext
from core.exceptions import EngineExecutionError
from market_data.market_snapshot import (
    OptionContractSnapshot,
    OptionType,
    UnderlyingSnapshot,
    VolatilitySnapshot,
    build_market_snapshot,
)
from strategy.base_strategy import StrategyContext
from strategy.bull_put_spread_strategy import (
    BullPutSpreadConfiguration,
    BullPutSpreadContext,
    BullPutSpreadStrategy,
    EntryRecommendationState,
    EventRiskEvidence,
    MarketRegimeEvidence,
    PremiumPricePolicy,
    TimeWindow,
    TrendEvidence,
    WingSelectionPolicy,
    default_bull_put_spread_configuration,
    from_json,
    to_json,
)
from strategy.signals import RiskProfileHint, SignalAction, SignalDirection
from strategy.strategy_scoring_framework import (
    ScoringFrameworkConfig,
    StrategyScoringFramework,
)

AS_OF = datetime(2026, 8, 5, 5, 30, tzinfo=timezone.utc)


def contract(
    strike: float,
    option_type: OptionType,
    delta: float,
    *,
    bid: float,
    ask: float,
) -> OptionContractSnapshot:
    """Build one liquid option fixture contract with explicit quotes."""
    mid = (bid + ask) / 2.0
    return OptionContractSnapshot(
        underlying="NIFTY",
        exchange="NFO",
        tradingsymbol=f"NIFTY26813{strike:.0f}{option_type.value}",
        expiry="2026-08-13",
        strike=strike,
        option_type=option_type,
        lot_size=75,
        ltp=mid,
        bid=bid,
        ask=ask,
        volume=1000,
        open_interest=10000,
        delta=delta,
        quote_timestamp=AS_OF,
    )


def snapshot(*, deltas: bool = True, reverse: bool = False):
    """Build a fresh NIFTY chain with positive bull-put-spread credit."""
    contracts = [
        contract(24000, OptionType.PE, -0.04, bid=6.0, ask=8.0),
        contract(24000, OptionType.CE, 0.80, bid=450.0, ask=452.0),
        contract(24100, OptionType.PE, -0.06, bid=10.0, ask=12.0),
        contract(24100, OptionType.CE, 0.75, bid=380.0, ask=382.0),
        contract(24200, OptionType.PE, -0.10, bid=18.0, ask=20.0),
        contract(24200, OptionType.CE, 0.70, bid=320.0, ask=322.0),
        contract(24300, OptionType.PE, -0.18, bid=38.0, ask=40.0),
        contract(24300, OptionType.CE, 0.60, bid=260.0, ask=262.0),
        contract(24400, OptionType.PE, -0.25, bid=58.0, ask=60.0),
        contract(24400, OptionType.CE, 0.55, bid=210.0, ask=212.0),
        contract(24500, OptionType.PE, -0.50, bid=150.0, ask=152.0),
        contract(24500, OptionType.CE, 0.50, bid=180.0, ask=182.0),
        contract(24600, OptionType.PE, -0.55, bid=210.0, ask=212.0),
        contract(24600, OptionType.CE, 0.45, bid=200.0, ask=202.0),
        contract(24700, OptionType.PE, -0.60, bid=260.0, ask=262.0),
        contract(24700, OptionType.CE, 0.40, bid=280.0, ask=282.0),
    ]
    if reverse:
        contracts = list(reversed(contracts))
    if not deltas:
        contracts = [replace(item, delta=None) for item in contracts]
    return build_market_snapshot(
        underlying=UnderlyingSnapshot(
            "NIFTY", "NSE", "NSE:NIFTY 50", 24500.0, quote_timestamp=AS_OF
        ),
        contracts=tuple(contracts),
        underlying_symbol="NIFTY",
        exchange="NFO",
        expiry="2026-08-13",
        atm_strike=24500.0,
        strike_step=100.0,
        strike_window_strikes=5,
        minimum_strike=24000.0,
        maximum_strike=24700.0,
        lot_size=75,
        as_of=AS_OF,
        captured_at=AS_OF,
        reference_time=AS_OF,
        snapshot_id="bull-put-fixture",
        volatility=VolatilitySnapshot("INDIA VIX", "NSE", "NSE:INDIA VIX", 15.0, AS_OF),
    )


def context(**overrides: object) -> StrategyContext:
    """Build a conventional tags-backed strategy context."""
    values: dict[str, object] = {
        "correlation_id": "bull-put-correlation",
        "as_of": AS_OF,
        "snapshot": snapshot(),
        "tags": {
            "regime_tag": "TRENDING_UP",
            "iv_rank": "55",
            "event_adverse": "false",
            "trend_direction": "BULLISH",
            "trend_strength": "0.35",
        },
    }
    values.update(overrides)
    return StrategyContext(**values)  # type: ignore[arg-type]


def strategy(
    configuration: BullPutSpreadConfiguration | None = None,
) -> BullPutSpreadStrategy:
    """Build a test strategy with deterministic framework configuration."""
    framework = StrategyScoringFramework(
        ScoringFrameworkConfig(enable_statistics=False),
        clock=lambda: AS_OF,
    )
    return BullPutSpreadStrategy(
        configuration or default_bull_put_spread_configuration(), framework
    )


def test_enter_happy_path_and_signal_mapping() -> None:
    """A liquid bearish elevated-IV setup enters with defined risk."""
    result = strategy().evaluate_recommendation(context())
    signal = strategy().run(context())
    assert result.state is EntryRecommendationState.ENTER
    assert result.strike_selection is not None
    assert result.strike_selection.short_put_strike == Decimal("24400.0")
    assert result.strike_selection.long_put_strike == Decimal("24200.0")
    assert result.risk_metrics is not None
    assert result.risk_metrics.max_loss > Decimal("0")
    assert result.risk_metrics.max_loss_label == "DEFINED_RISK"
    assert result.risk_metrics.net_credit > Decimal("0")
    assert signal.action is SignalAction.EVALUATE
    assert signal.direction is SignalDirection.BULLISH
    assert signal.risk is not None
    assert signal.risk.profile is RiskProfileHint.DEFINED
    assert signal.structure_hint is not None
    assert signal.structure_hint.leg_count == 2
    assert signal.metadata["max_loss_label"] == "DEFINED_RISK"


@pytest.mark.parametrize(
    ("tags", "state", "code"),
    [
        (
            {"iv_rank": "55", "trend_direction": "BULLISH", "trend_strength": "0.35"},
            EntryRecommendationState.REJECT,
            "BPS.REGIME.MISSING",
        ),
        (
            {
                "regime_tag": "HIGH_VOLATILITY_CRISIS",
                "iv_rank": "55",
                "trend_direction": "BULLISH",
                "trend_strength": "0.35",
            },
            EntryRecommendationState.REJECT,
            "BPS.REGIME.CRISIS",
        ),
        (
            {
                "regime_tag": "TRENDING_DOWN",
                "iv_rank": "55",
                "trend_direction": "BULLISH",
                "trend_strength": "0.35",
            },
            EntryRecommendationState.ABSTAIN,
            "BPS.REGIME.UNSUITABLE",
        ),
        (
            {
                "regime_tag": "TRENDING_UP",
                "iv_rank": "10",
                "trend_direction": "BULLISH",
                "trend_strength": "0.35",
            },
            EntryRecommendationState.ABSTAIN,
            "BPS.IV_RANK.LOW",
        ),
        (
            {
                "regime_tag": "TRENDING_UP",
                "iv_rank": "55",
                "trend_direction": "BEARISH",
                "trend_strength": "0.35",
            },
            EntryRecommendationState.ABSTAIN,
            "BPS.TREND.STRONG_BEARISH",
        ),
        (
            {
                "regime_tag": "BULLISH",
                "iv_rank": "55",
                "trend_direction": "BULLISH",
                "trend_strength": "0.35",
            },
            EntryRecommendationState.ENTER,
            "BPS.GATES.PASS",
        ),
        (
            {
                "regime_tag": "TRENDING_UP",
                "iv_rank": "55",
                "trend_strength": "0.80",
            },
            EntryRecommendationState.ABSTAIN,
            "BPS.TREND.STRONG_BEARISH",
        ),
    ],
)
def test_regime_iv_and_trend_gates(
    tags: dict[str, str], state: EntryRecommendationState, code: str
) -> None:
    """Regime, IV, and bullish-trend gates preserve specified classifications."""
    result = strategy().evaluate_recommendation(context(tags=tags))
    assert result.state is state
    assert result.reasons[0] == code


def test_time_premium_and_greeks_abstentions() -> None:
    """Valid but unsuitable time, premium and greek inputs fail closed."""
    bounded = BullPutSpreadConfiguration(entry_time_window=TimeWindow(time(9), time(11)))
    assert strategy(bounded).evaluate_recommendation(context()).reasons == (
        "BPS.TIME.OUTSIDE_ENTRY_WINDOW",
    )
    assert strategy(
        BullPutSpreadConfiguration(minimum_premium=Decimal("1000"))
    ).evaluate_recommendation(context()).reasons == ("BPS.PREMIUM.BELOW_MINIMUM",)
    assert strategy().evaluate_recommendation(
        context(snapshot=snapshot(deltas=False))
    ).reasons == ("BPS.GREEKS.MISSING",)


def test_typed_context_and_serialization_round_trip() -> None:
    """Typed evidence works and entry recommendation JSON is canonical."""
    base = context(tags={})
    typed = BullPutSpreadContext(
        base,
        MarketRegimeEvidence("TRENDING_UP", AS_OF),
        EventRiskEvidence(False, AS_OF),
        TrendEvidence(AS_OF, "BULLISH", Decimal("0.35")),
        Decimal("55"),
    )
    recommendation = strategy().evaluate_bull_put_spread(typed)
    restored = from_json(to_json(recommendation))
    assert restored.state is EntryRecommendationState.ENTER
    assert restored.strike_selection == recommendation.strike_selection
    assert restored.risk_metrics == recommendation.risk_metrics
    assert restored.schema_version == "1.0"


def test_configuration_validation_and_concurrent_calls() -> None:
    """Configuration fails closed and simultaneous evaluation is identical."""
    with pytest.raises(ValueError, match="CFG-BPS"):
        BullPutSpreadConfiguration(short_target_delta=Decimal("0.50"))
    with pytest.raises(ValueError, match="CFG-BPS"):
        BullPutSpreadConfiguration(long_target_delta=Decimal("0.30"))
    with pytest.raises(ValueError, match="CFG-BPS"):
        BullPutSpreadConfiguration(supported_underlyings=frozenset())
    instance = strategy()
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(instance.evaluate_recommendation, [context()] * 16))
    assert len({item.recommendation_id for item in results}) == 1
    assert len({to_json(item) for item in results}) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"short_target_delta": Decimal("NaN")},
        {"minimum_iv_rank": Decimal("101")},
        {"maximum_bearish_trend_strength": Decimal("1.5")},
        {"maximum_spread_width": Decimal("0")},
        {"maximum_relative_spread_width": Decimal("0")},
        {"minimum_premium": Decimal("-1")},
        {"short_delta_selection_tolerance": Decimal("0.50")},
        {"long_delta_selection_tolerance": Decimal("0.50")},
        {"minimum_open_interest": -1},
        {"max_snapshot_age_seconds": 0},
        {"minimum_dte": 2, "maximum_dte": 1},
        {"scoring_profile_name": "BALANCED"},
        {"premium_price_policy": "bad"},
        {"wing_selection_policy": "bad"},
        {"target_wing_width": Decimal("-1")},
        {"contract_multiplier": Decimal("0")},
        {"minimum_liquidity_score": Decimal("2")},
        {"short_delta_selection_tolerance": Decimal("-0.01")},
        {"minimum_wing_width": Decimal("300"), "maximum_wing_width": Decimal("100")},
        {"minimum_volume": -1},
        {"iv_rank_lookback_observations": 0},
            {
                "short_put_target_delta": Decimal("0.10"),
                "long_put_target_delta": Decimal("0.12"),
            },
    ],
)
def test_all_configuration_invariants_fail_closed(kwargs: dict[str, object]) -> None:
    """Every policy-boundary invariant rejects invalid static configuration."""
    with pytest.raises(ValueError, match="CFG-BPS"):
        BullPutSpreadConfiguration(**kwargs)  # type: ignore[arg-type]


def test_remaining_recommendation_gates_and_public_aliases() -> None:
    """Event, IV, stale, trend-missing and unsupported paths retain stable outcomes."""
    instance = strategy()
    assert instance.evaluate(context()).state is EntryRecommendationState.ENTER
    assert instance.evaluate_recommendation(
        context(
            tags={
                "regime_tag": "TRENDING_UP",
                "iv_rank": "55",
                "event_adverse": "true",
                "trend_direction": "BULLISH",
                "trend_strength": "0.35",
            }
        )
    ).reasons == ("BPS.EVENT.ADVERSE",)
    assert instance.evaluate_recommendation(
        context(
            tags={
                "regime_tag": "TRENDING_UP",
                "trend_direction": "BULLISH",
                "trend_strength": "0.35",
            }
        )
    ).reasons == ("BPS.IV_RANK.MISSING",)
    assert instance.evaluate_recommendation(
        context(
            tags={
                "regime_tag": "TRENDING_UP",
                "iv_rank": "nan",
                "trend_direction": "BULLISH",
                "trend_strength": "0.35",
            }
        )
    ).reasons == ("BPS.METRIC.NON_FINITE",)
    assert instance.evaluate_recommendation(
        context(tags={"regime_tag": "TRENDING_UP", "iv_rank": "55"})
    ).reasons == ("BPS.TREND.MISSING",)
    assert strategy(
        BullPutSpreadConfiguration(supported_underlyings=frozenset({"SENSEX"}))
    ).evaluate_recommendation(context()).reasons == ("BPS.UNDERLYING.UNSUPPORTED",)
    stale = replace(context().snapshot.freshness, age_seconds=6.0)
    assert instance.evaluate_recommendation(
        context(snapshot=replace(context().snapshot, freshness=stale))
    ).reasons == ("BPS.SNAPSHOT.STALE",)


def test_reversed_chain_is_deterministic() -> None:
    """Input option-chain order must not influence selection."""
    forward = strategy().evaluate_recommendation(context())
    reversed_result = strategy().evaluate_recommendation(
        context(snapshot=snapshot(reverse=True))
    )
    assert forward.state is EntryRecommendationState.ENTER
    assert reversed_result.strike_selection == forward.strike_selection
    assert reversed_result.risk_metrics == forward.risk_metrics


def test_wing_policies_and_pricing() -> None:
    """Wing policies and quote policies remain deterministic."""
    fixed = BullPutSpreadConfiguration(
        wing_selection_policy=WingSelectionPolicy.FIXED_WIDTH,
        target_wing_width=Decimal("200.0"),
        long_delta_selection_tolerance=Decimal("0.10"),
    )
    result = strategy(fixed).evaluate_recommendation(context())
    assert result.state is EntryRecommendationState.ENTER
    assert result.strike_selection is not None
    assert result.strike_selection.wing_width == Decimal("200.0")
    delta_policy = BullPutSpreadConfiguration(
        wing_selection_policy=WingSelectionPolicy.DELTA_TARGET
    )
    assert strategy(delta_policy).evaluate_recommendation(context()).state is (
        EntryRecommendationState.ENTER
    )
    configured = BullPutSpreadConfiguration(
        premium_price_policy=PremiumPricePolicy.CONSERVATIVE,
        contract_multiplier=Decimal("75"),
    )
    priced = strategy(configured).evaluate_recommendation(context())
    assert priced.state is EntryRecommendationState.ENTER
    assert priced.risk_metrics is not None
    assert priced.risk_metrics.contract_multiplier == Decimal("75")
    ask_credit = BullPutSpreadConfiguration(
        premium_price_policy=PremiumPricePolicy.ASK_CREDIT
    )
    assert strategy(ask_credit).evaluate_recommendation(context()).state is (
        EntryRecommendationState.ENTER
    )


def test_window_and_serialization_error_boundaries() -> None:
    """Window validation and malformed recommendation JSON fail closed."""
    with pytest.raises(ValueError, match="CFG-BPS"):
        TimeWindow(time(12), time(12))
    with pytest.raises(ValueError, match="CFG-BPS"):
        TimeWindow(time(9), time(10), "Not/A_Timezone")
    recommendation = strategy().evaluate_recommendation(context())
    assert recommendation.to_json() == to_json(recommendation)
    assert recommendation.from_json(recommendation.to_json()).state is (
        EntryRecommendationState.ENTER
    )
    with pytest.raises(ValueError, match="BPS.SERIALIZATION.INVALID"):
        from_json('{"schema_version":"2.0"}')


def test_numeric_helpers_and_boundary_greps() -> None:
    """Helpers reject bad input and the module stays analysis-only."""
    with pytest.raises(ValueError):
        bull_put_module._decimal(object())
    assert bull_put_module._parse_expiry("not-an-expiry") is None
    tokenized = replace(
        contract(24400, OptionType.PE, -0.25, bid=58.0, ask=60.0),
        instrument_token=99,
    )
    assert bull_put_module._instrument_id(tokenized) == "99"
    source = Path(__file__).parents[1].joinpath(
        "strategy/bull_put_spread_strategy.py"
    ).read_text()
    assert "kiteconnect" not in source.lower()
    assert "place_order" not in source.lower()
    assert "from broker" not in source.lower()
    assert "from risk" not in source.lower()
    assert "from execution" not in source.lower()


def test_context_validation_and_engine_dispatch() -> None:
    """Missing/invalid context and EngineContext dispatch fail closed."""
    instance = strategy()
    with pytest.raises(EngineExecutionError):
        instance.evaluate(EngineContext(correlation_id="x", as_of=AS_OF, payload={}))
    assert instance.evaluate_recommendation(context(snapshot=None)).reasons == (
        "BPS.SNAPSHOT.MISSING",
    )
    naive = AS_OF.replace(tzinfo=None)
    assert instance.evaluate_recommendation(context(as_of=naive)).reasons == (
        "BPS.CONTEXT.INVALID",
    )
    bad_spot = replace(
        context().snapshot,
        underlying=replace(context().snapshot.underlying, last_price=0.0),
    )
    assert instance.evaluate_recommendation(context(snapshot=bad_spot)).reasons == (
        "BPS.CONTEXT.INVALID",
    )


def test_liquidity_filters_and_empty_chain() -> None:
    """Liquidity floors and empty chains abstain or reject."""
    tight = BullPutSpreadConfiguration(maximum_spread_width=Decimal("0.10"))
    assert strategy(tight).evaluate_recommendation(context()).reasons == (
        "BPS.STRIKE.NO_ELIGIBLE_SHORT",
    )
    oi_floor = BullPutSpreadConfiguration(minimum_open_interest=50000)
    assert strategy(oi_floor).evaluate_recommendation(context()).reasons == (
        "BPS.STRIKE.NO_ELIGIBLE_SHORT",
    )
    snap = snapshot()
    empty = replace(snap, option_chain=replace(snap.option_chain, contracts=()))
    assert strategy()._select(empty, AS_OF.date()) == "BPS.CHAIN.MISSING"
    bad_expiry = replace(
        snap,
        option_chain=replace(
            snap.option_chain,
            contracts=tuple(
                replace(item, expiry="not-a-date") for item in snap.option_chain.contracts
            ),
        ),
    )
    assert strategy()._select(bad_expiry, AS_OF.date()) == "BPS.CHAIN.INCOMPLETE"


def test_risk_metrics_edges_and_event_sink() -> None:
    """Degenerate credit/wing geometry rejects and sinks receive ENTER events."""
    instance = strategy()
    entered = instance.evaluate_recommendation(context())
    assert entered.strike_selection is not None
    selection = replace(entered.strike_selection, wing_width=Decimal("10"))
    expensive = contract(24400, OptionType.PE, -0.25, bid=90.0, ask=100.0)
    cheap = contract(24200, OptionType.PE, -0.10, bid=1.0, ask=2.0)
    assert instance._risk_metrics(selection, expensive, cheap) == (
        "BPS.RISK.NON_POSITIVE_MAX_LOSS"
    )
    zero_short = contract(24400, OptionType.PE, -0.25, bid=1.0, ask=2.0)
    zero_long = contract(24200, OptionType.PE, -0.10, bid=90.0, ask=100.0)
    assert instance._risk_metrics(
        entered.strike_selection, zero_short, zero_long
    ) == "BPS.PREMIUM.BELOW_MINIMUM"

    class RecordingSink:
        def __init__(self) -> None:
            self.topics: list[str] = []

        def publish(self, topic: str, payload: object) -> None:
            self.topics.append(topic)

    class BrokenSink:
        def publish(self, topic: str, payload: object) -> None:
            raise RuntimeError("sink failed")

    sink = RecordingSink()
    framework = StrategyScoringFramework(
        ScoringFrameworkConfig(enable_statistics=False),
        clock=lambda: AS_OF,
    )
    publishing = BullPutSpreadStrategy(
        default_bull_put_spread_configuration(), framework, event_sink=sink
    )
    sealed = publishing.evaluate_recommendation(context())
    assert sealed.state is EntryRecommendationState.ENTER
    assert "strategy.bull_put_spread.entered_candidate" in sink.topics
    broken = BullPutSpreadStrategy(
        default_bull_put_spread_configuration(), framework, event_sink=BrokenSink()
    )
    assert broken.evaluate_recommendation(context()).state is EntryRecommendationState.ENTER


def test_illiquid_quotes_and_helper_branches() -> None:
    """Crossed quotes fail liquidity and helper branches stay fail-closed."""
    crossed = contract(24400, OptionType.PE, -0.25, bid=60.0, ask=58.0)
    assert strategy()._liquid(crossed) is False
    invalid_quote = replace(
        contract(24400, OptionType.PE, -0.25, bid=58.0, ask=60.0),
        bid="bad",  # type: ignore[arg-type]
    )
    assert strategy()._liquid(invalid_quote) is False
    assert bull_put_module._clamp(Decimal("-1"), Decimal("0"), Decimal("1")) == Decimal(
        "0"
    )
    snap = snapshot()
    assert strategy()._select(
        replace(snap, underlying=replace(snap.underlying, last_price="bad")),  # type: ignore[arg-type]
        AS_OF.date(),
    ) == "BPS.CHAIN.INCOMPLETE"
    dte_blocked = BullPutSpreadConfiguration(minimum_dte=30, maximum_dte=30)
    assert strategy(dte_blocked)._select(snap, AS_OF.date()) == "BPS.CHAIN.INCOMPLETE"
    fixed_mismatch = BullPutSpreadConfiguration(
        wing_selection_policy=WingSelectionPolicy.FIXED_WIDTH,
        target_wing_width=Decimal("350"),
    )
    assert (
        strategy(fixed_mismatch)._long_candidates(
            [item for item in snap.option_chain.contracts if item.option_type is OptionType.PE],
            Decimal("24500"),
            contract(24400, OptionType.PE, -0.25, bid=58.0, ask=60.0),
            Decimal("0.10"),
        )
        == []
    )
    no_target = BullPutSpreadConfiguration(
        wing_selection_policy=WingSelectionPolicy.FIXED_WIDTH
    )
    assert strategy(no_target).evaluate_recommendation(context()).reasons == (
        "BPS.STRIKE.NO_ELIGIBLE_SHORT",
    )
    malformed_trend = strategy().evaluate_recommendation(
        context(
            tags={
                "regime_tag": "TRENDING_UP",
                "iv_rank": "55",
                "trend_strength": "not-a-number",
            }
        )
    )
    assert malformed_trend.reasons == ("BPS.METRIC.NON_FINITE",)
