"""Tests for the deterministic iron-condor strategy plugin."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, time, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import strategy.iron_condor_strategy as iron_condor_module
from market_data.market_snapshot import (
    OptionContractSnapshot,
    OptionType,
    UnderlyingSnapshot,
    VolatilitySnapshot,
    build_market_snapshot,
)
from strategy.base_strategy import StrategyContext
from strategy.iron_condor_strategy import (
    EntryRecommendationState,
    EventRiskEvidence,
    IronCondorConfiguration,
    IronCondorContext,
    IronCondorStrategy,
    MarketRegimeEvidence,
    PremiumPricePolicy,
    TimeWindow,
    TrendStrengthEvidence,
    WingSelectionPolicy,
    default_iron_condor_configuration,
    from_json,
    to_json,
)
from strategy.signals import RiskProfileHint, SignalAction
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
    """Build a complete, fresh NIFTY chain with positive iron-condor credit."""
    contracts = [
        contract(24000, OptionType.PE, -0.05, bid=12.0, ask=13.0),
        contract(24000, OptionType.CE, 0.55, bid=480.0, ask=482.0),
        contract(24200, OptionType.PE, -0.16, bid=38.0, ask=40.0),
        contract(24200, OptionType.CE, 0.45, bid=320.0, ask=322.0),
        contract(24300, OptionType.PE, -0.22, bid=55.0, ask=57.0),
        contract(24300, OptionType.CE, 0.40, bid=260.0, ask=262.0),
        contract(24700, OptionType.PE, -0.40, bid=250.0, ask=252.0),
        contract(24700, OptionType.CE, 0.22, bid=55.0, ask=57.0),
        contract(24800, OptionType.PE, -0.45, bid=310.0, ask=312.0),
        contract(24800, OptionType.CE, 0.16, bid=38.0, ask=40.0),
        contract(25000, OptionType.PE, -0.55, bid=470.0, ask=472.0),
        contract(25000, OptionType.CE, 0.05, bid=12.0, ask=13.0),
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
        maximum_strike=25000.0,
        lot_size=75,
        as_of=AS_OF,
        captured_at=AS_OF,
        reference_time=AS_OF,
        snapshot_id="iron-condor-fixture",
        volatility=VolatilitySnapshot("INDIA VIX", "NSE", "NSE:INDIA VIX", 15.0, AS_OF),
    )


def context(**overrides: object) -> StrategyContext:
    """Build a conventional tags-backed strategy context."""
    values: dict[str, object] = {
        "correlation_id": "iron-condor-correlation",
        "as_of": AS_OF,
        "snapshot": snapshot(),
        "tags": {
            "regime_tag": "RANGE_BOUND",
            "iv_rank": "70",
            "event_adverse": "false",
            "trend_strength": "0.25",
        },
    }
    values.update(overrides)
    return StrategyContext(**values)  # type: ignore[arg-type]


def strategy(configuration: IronCondorConfiguration | None = None) -> IronCondorStrategy:
    """Build a test strategy with deterministic framework configuration."""
    framework = StrategyScoringFramework(
        ScoringFrameworkConfig(enable_statistics=False),
        clock=lambda: AS_OF,
    )
    return IronCondorStrategy(configuration or default_iron_condor_configuration(), framework)


def test_enter_happy_path_and_signal_mapping() -> None:
    """A liquid, range-bound, elevated-IV setup enters with defined risk."""
    result = strategy().evaluate_recommendation(context())
    signal = strategy().run(context())
    assert result.state is EntryRecommendationState.ENTER
    assert result.strike_selection is not None
    assert result.strike_selection.short_put_strike == Decimal("24200.0")
    assert result.strike_selection.long_put_strike == Decimal("24000.0")
    assert result.strike_selection.short_call_strike == Decimal("24800.0")
    assert result.strike_selection.long_call_strike == Decimal("25000.0")
    assert result.risk_metrics is not None
    assert result.risk_metrics.max_loss > Decimal("0")
    assert result.risk_metrics.max_loss_label == "DEFINED_RISK"
    assert result.risk_metrics.net_credit > Decimal("0")
    assert signal.action is SignalAction.EVALUATE
    assert signal.risk is not None
    assert signal.risk.profile is RiskProfileHint.DEFINED
    assert signal.risk.max_loss_category == "DEFINED_RISK"
    assert signal.structure_hint is not None
    assert signal.structure_hint.leg_count == 4
    assert signal.metadata["max_loss_label"] == "DEFINED_RISK"


@pytest.mark.parametrize(
    ("tags", "state", "code"),
    [
        (
            {"iv_rank": "70", "trend_strength": "0.25"},
            EntryRecommendationState.REJECT,
            "ICS.REGIME.MISSING",
        ),
        (
            {
                "regime_tag": "HIGH_VOLATILITY_CRISIS",
                "iv_rank": "70",
                "trend_strength": "0.25",
            },
            EntryRecommendationState.REJECT,
            "ICS.REGIME.CRISIS",
        ),
        (
            {"regime_tag": "TRENDING_UP", "iv_rank": "70", "trend_strength": "0.25"},
            EntryRecommendationState.ABSTAIN,
            "ICS.REGIME.UNSUITABLE",
        ),
        (
            {"regime_tag": "RANGE_BOUND", "iv_rank": "10", "trend_strength": "0.25"},
            EntryRecommendationState.ABSTAIN,
            "ICS.IV_RANK.LOW",
        ),
        (
            {"regime_tag": "RANGE_BOUND", "iv_rank": "70", "trend_strength": "0.80"},
            EntryRecommendationState.ABSTAIN,
            "ICS.TREND.HIGH_STRENGTH",
        ),
        (
            {"regime_tag": "SIDEWAYS", "iv_rank": "70", "trend_strength": "0.25"},
            EntryRecommendationState.ENTER,
            "ICS.GATES.PASS",
        ),
    ],
)
def test_regime_iv_and_trend_gates(
    tags: dict[str, str], state: EntryRecommendationState, code: str
) -> None:
    """Regime, IV, and trend gates preserve their specified classifications."""
    result = strategy().evaluate_recommendation(context(tags=tags))
    assert result.state is state
    assert result.reasons[0] == code


def test_time_premium_and_greeks_abstentions() -> None:
    """Valid but unsuitable time, premium and greek inputs fail closed."""
    bounded = IronCondorConfiguration(entry_time_window=TimeWindow(time(9), time(11)))
    assert strategy(bounded).evaluate_recommendation(context()).reasons == (
        "ICS.TIME.OUTSIDE_ENTRY_WINDOW",
    )
    assert strategy(
        IronCondorConfiguration(minimum_premium=Decimal("1000"))
    ).evaluate_recommendation(context()).reasons == ("ICS.PREMIUM.BELOW_MINIMUM",)
    assert strategy().evaluate_recommendation(
        context(snapshot=snapshot(deltas=False))
    ).reasons == ("ICS.GREEKS.MISSING",)


def test_typed_context_and_serialization_round_trip() -> None:
    """Typed evidence works and entry recommendation JSON is canonical."""
    base = context(tags={})
    typed = IronCondorContext(
        base,
        MarketRegimeEvidence("RANGE_BOUND", AS_OF),
        EventRiskEvidence(False, AS_OF),
        TrendStrengthEvidence(Decimal("0.25"), AS_OF),
        Decimal("70"),
    )
    recommendation = strategy().evaluate_iron_condor(typed)
    restored = from_json(to_json(recommendation))
    assert restored.state is EntryRecommendationState.ENTER
    assert restored.strike_selection == recommendation.strike_selection
    assert restored.risk_metrics == recommendation.risk_metrics
    assert restored.schema_version == "1.0"


def test_configuration_validation_and_concurrent_calls() -> None:
    """Configuration fails closed and simultaneous evaluation is identical."""
    with pytest.raises(ValueError, match="CFG-ICS"):
        IronCondorConfiguration(short_target_delta=Decimal("0.50"))
    with pytest.raises(ValueError, match="CFG-ICS"):
        IronCondorConfiguration(long_target_delta=Decimal("0.20"))
    with pytest.raises(ValueError, match="CFG-ICS"):
        IronCondorConfiguration(supported_underlyings=frozenset())
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
        {"maximum_trend_strength": Decimal("1.5")},
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
    ],
)
def test_all_configuration_invariants_fail_closed(kwargs: dict[str, object]) -> None:
    """Every policy-boundary invariant rejects invalid static configuration."""
    with pytest.raises(ValueError, match="CFG-ICS"):
        IronCondorConfiguration(**kwargs)  # type: ignore[arg-type]


def test_remaining_recommendation_gates_and_public_aliases() -> None:
    """Event, IV, stale, trend-missing and unsupported paths retain stable outcomes."""
    instance = strategy()
    assert instance.evaluate(context()).state is EntryRecommendationState.ENTER
    assert instance.evaluate_recommendation(
        context(
            tags={
                "regime_tag": "RANGE_BOUND",
                "iv_rank": "70",
                "event_adverse": "true",
                "trend_strength": "0.25",
            }
        )
    ).reasons == ("ICS.EVENT.ADVERSE",)
    assert instance.evaluate_recommendation(
        context(tags={"regime_tag": "RANGE_BOUND", "trend_strength": "0.25"})
    ).reasons == ("ICS.IV_RANK.MISSING",)
    assert instance.evaluate_recommendation(
        context(
            tags={
                "regime_tag": "RANGE_BOUND",
                "iv_rank": "nan",
                "trend_strength": "0.25",
            }
        )
    ).reasons == ("ICS.METRIC.NON_FINITE",)
    assert instance.evaluate_recommendation(
        context(tags={"regime_tag": "RANGE_BOUND", "iv_rank": "70"})
    ).reasons == ("ICS.TREND.MISSING",)
    assert strategy(
        IronCondorConfiguration(supported_underlyings=frozenset({"SENSEX"}))
    ).evaluate_recommendation(context()).reasons == ("ICS.UNDERLYING.UNSUPPORTED",)
    stale = replace(context().snapshot.freshness, age_seconds=6.0)
    assert instance.evaluate_recommendation(
        context(snapshot=replace(context().snapshot, freshness=stale))
    ).reasons == ("ICS.SNAPSHOT.STALE",)


def test_reversed_chain_is_deterministic() -> None:
    """Input option-chain order must not influence selection."""
    forward = strategy().evaluate_recommendation(context())
    reversed_result = strategy().evaluate_recommendation(
        context(snapshot=snapshot(reverse=True))
    )
    assert forward.state is EntryRecommendationState.ENTER
    assert reversed_result.strike_selection == forward.strike_selection
    assert reversed_result.risk_metrics == forward.risk_metrics


def test_wing_policies_and_symmetric_requirement() -> None:
    """Wing policies and symmetry constraints remain fail-closed and deterministic."""
    fixed = IronCondorConfiguration(
        wing_selection_policy=WingSelectionPolicy.FIXED_WIDTH,
        target_wing_width=Decimal("200.0"),
        long_delta_selection_tolerance=Decimal("0.10"),
    )
    result = strategy(fixed).evaluate_recommendation(context())
    assert result.state is EntryRecommendationState.ENTER
    assert result.strike_selection is not None
    assert result.strike_selection.put_wing_width == Decimal("200.0")
    assert result.strike_selection.call_wing_width == Decimal("200.0")
    delta_policy = IronCondorConfiguration(
        wing_selection_policy=WingSelectionPolicy.DELTA_TARGET
    )
    assert strategy(delta_policy).evaluate_recommendation(context()).state is (
        EntryRecommendationState.ENTER
    )
    symmetric = IronCondorConfiguration(require_symmetric_wings=True)
    assert symmetric.allow_asymmetric_wings is False
    assert strategy(symmetric).evaluate_recommendation(context()).state is (
        EntryRecommendationState.ENTER
    )


def test_conservative_pricing_and_multiplier() -> None:
    """Conservative quote policy and multiplier affect defined-risk metrics."""
    configured = IronCondorConfiguration(
        premium_price_policy=PremiumPricePolicy.CONSERVATIVE,
        contract_multiplier=Decimal("75"),
    )
    result = strategy(configured).evaluate_recommendation(context())
    assert result.state is EntryRecommendationState.ENTER
    assert result.risk_metrics is not None
    assert result.risk_metrics.contract_multiplier == Decimal("75")
    assert result.risk_metrics.max_profit == result.risk_metrics.net_credit * Decimal("75")
    assert result.risk_metrics.max_loss == (
        max(
            result.strike_selection.put_wing_width,  # type: ignore[union-attr]
            result.strike_selection.call_wing_width,  # type: ignore[union-attr]
        )
        - result.risk_metrics.net_credit
    ) * Decimal("75")


def test_window_and_serialization_error_boundaries() -> None:
    """Window validation and malformed recommendation JSON fail closed."""
    with pytest.raises(ValueError, match="CFG-ICS"):
        TimeWindow(time(12), time(12))
    with pytest.raises(ValueError, match="CFG-ICS"):
        TimeWindow(time(9), time(10), "Not/A_Timezone")
    recommendation = strategy().evaluate_recommendation(context())
    assert recommendation.to_json() == to_json(recommendation)
    assert recommendation.from_json(recommendation.to_json()).state is (
        EntryRecommendationState.ENTER
    )
    with pytest.raises(ValueError, match="ICS.SERIALIZATION.INVALID"):
        from_json('{"schema_version":"2.0"}')


def test_numeric_and_expiry_boundary_helpers_reject_invalid_input() -> None:
    """Malformed snapshot-boundary values cannot enter Decimal calculations."""
    with pytest.raises(ValueError):
        iron_condor_module._decimal(object())
    assert iron_condor_module._parse_expiry("not-an-expiry") is None


def test_event_sink_failure_does_not_break_recommendation() -> None:
    """Optional event sink exceptions remain isolated from sealed results."""

    class BrokenSink:
        def publish(self, topic: str, payload: object) -> None:
            raise RuntimeError("sink failed")

    framework = StrategyScoringFramework(
        ScoringFrameworkConfig(enable_statistics=False),
        clock=lambda: AS_OF,
    )
    instance = IronCondorStrategy(
        default_iron_condor_configuration(), framework, event_sink=BrokenSink()
    )
    result = instance.evaluate_recommendation(context())
    assert result.state is EntryRecommendationState.ENTER


def test_module_has_no_broker_execution_dependency() -> None:
    """The strategy remains an analysis-only module."""
    source = Path(__file__).parents[1].joinpath("strategy/iron_condor_strategy.py").read_text()
    assert "kiteconnect" not in source.lower()
    assert "place_order" not in source.lower()
    assert "cancel_order" not in source.lower()
    assert "from broker" not in source.lower()
    assert "from risk" not in source.lower()
    assert "from execution" not in source.lower()


def test_context_validation_and_engine_dispatch_boundaries() -> None:
    """Missing/invalid context and generic EngineContext dispatch fail closed."""
    from core.engine_context import EngineContext
    from core.exceptions import EngineExecutionError

    instance = strategy()
    with pytest.raises(EngineExecutionError):
        instance.evaluate(EngineContext(correlation_id="x", as_of=AS_OF, payload={}))
    assert instance.evaluate_recommendation(context(snapshot=None)).reasons == (
        "ICS.SNAPSHOT.MISSING",
    )
    naive = AS_OF.replace(tzinfo=None)
    assert instance.evaluate_recommendation(context(as_of=naive)).reasons == (
        "ICS.CONTEXT.INVALID",
    )
    bad_spot = replace(
        context().snapshot,
        underlying=replace(context().snapshot.underlying, last_price=0.0),
    )
    assert instance.evaluate_recommendation(context(snapshot=bad_spot)).reasons == (
        "ICS.CONTEXT.INVALID",
    )
    assert instance._validate_context(context(snapshot="bad")) == "ICS.CONTEXT.INVALID"  # type: ignore[arg-type]


def test_liquidity_wing_filters_and_ask_credit_policy() -> None:
    """Liquidity floors, wing bounds, and ASK_CREDIT pricing remain deterministic."""
    tight_spread = IronCondorConfiguration(maximum_spread_width=Decimal("0.10"))
    assert strategy(tight_spread).evaluate_recommendation(context()).reasons == (
        "ICS.STRIKE.NO_ELIGIBLE_SHORT",
    )
    wide_relative = IronCondorConfiguration(
        maximum_relative_spread_width=Decimal("0.001")
    )
    assert strategy(wide_relative).evaluate_recommendation(context()).reasons == (
        "ICS.STRIKE.NO_ELIGIBLE_SHORT",
    )
    oi_floor = IronCondorConfiguration(minimum_open_interest=50000)
    assert strategy(oi_floor).evaluate_recommendation(context()).reasons == (
        "ICS.STRIKE.NO_ELIGIBLE_SHORT",
    )
    volume_floor = IronCondorConfiguration(minimum_volume=50000)
    assert strategy(volume_floor).evaluate_recommendation(context()).reasons == (
        "ICS.STRIKE.NO_ELIGIBLE_SHORT",
    )
    min_wing = IronCondorConfiguration(minimum_wing_width=Decimal("500"))
    assert strategy(min_wing).evaluate_recommendation(context()).reasons == (
        "ICS.STRIKE.NO_ELIGIBLE_SHORT",
    )
    max_wing = IronCondorConfiguration(maximum_wing_width=Decimal("50"))
    assert strategy(max_wing).evaluate_recommendation(context()).reasons == (
        "ICS.STRIKE.NO_ELIGIBLE_SHORT",
    )
    ask_credit = IronCondorConfiguration(
        premium_price_policy=PremiumPricePolicy.ASK_CREDIT
    )
    result = strategy(ask_credit).evaluate_recommendation(context())
    assert result.state is EntryRecommendationState.ENTER
    assert result.risk_metrics is not None
    assert result.risk_metrics.net_credit > Decimal("0")


def test_malformed_trend_tag_and_extra_config_invariants() -> None:
    """Malformed trend tags and remaining config invariants fail closed."""
    assert strategy().evaluate_recommendation(
        context(
            tags={
                "regime_tag": "RANGE_BOUND",
                "iv_rank": "70",
                "trend_strength": "not-a-number",
            }
        )
    ).reasons == ("ICS.METRIC.NON_FINITE",)
    assert strategy().evaluate_recommendation(
        context(
            tags={
                "regime_tag": "RANGE_BOUND",
                "iv_rank": "70",
                "trend_strength": "1.5",
            }
        )
    ).reasons == ("ICS.METRIC.NON_FINITE",)
    with pytest.raises(ValueError, match="CFG-ICS"):
        IronCondorConfiguration(
            short_call_target_delta=Decimal("0.10"),
            long_call_target_delta=Decimal("0.12"),
        )
    with pytest.raises(ValueError, match="CFG-ICS"):
        IronCondorConfiguration(short_delta_selection_tolerance=Decimal("-0.01"))
    with pytest.raises(ValueError, match="CFG-ICS"):
        IronCondorConfiguration(
            minimum_wing_width=Decimal("300"),
            maximum_wing_width=Decimal("100"),
        )
    with pytest.raises(ValueError, match="CFG-ICS"):
        IronCondorConfiguration(minimum_volume=-1)
    with pytest.raises(ValueError, match="CFG-ICS"):
        IronCondorConfiguration(iv_rank_lookback_observations=0)


def test_fixed_width_without_target_and_empty_chain() -> None:
    """FIXED_WIDTH without a target and empty chains abstain or reject."""
    fixed = IronCondorConfiguration(wing_selection_policy=WingSelectionPolicy.FIXED_WIDTH)
    assert strategy(fixed).evaluate_recommendation(context()).reasons == (
        "ICS.STRIKE.NO_ELIGIBLE_SHORT",
    )
    snap = snapshot()
    empty = replace(snap, option_chain=replace(snap.option_chain, contracts=()))
    assert strategy()._select(empty, AS_OF.date()) == "ICS.CHAIN.MISSING"
    bad_expiry = replace(
        snap,
        option_chain=replace(
            snap.option_chain,
            contracts=tuple(
                replace(item, expiry="not-a-date") for item in snap.option_chain.contracts
            ),
        ),
    )
    assert strategy()._select(bad_expiry, AS_OF.date()) == "ICS.CHAIN.INCOMPLETE"
    tokenized = replace(
        contract(24200, OptionType.PE, -0.16, bid=38.0, ask=40.0),
        instrument_token=12345,
    )
    assert iron_condor_module._instrument_id(tokenized) == "12345"


def test_non_positive_max_loss_and_working_event_sink() -> None:
    """Degenerate credit/wing geometry rejects and sinks receive ENTER events."""
    instance = strategy()
    entered = instance.evaluate_recommendation(context())
    assert entered.strike_selection is not None
    selection = replace(
        entered.strike_selection,
        put_wing_width=Decimal("10"),
        call_wing_width=Decimal("10"),
    )
    expensive_short = contract(24200, OptionType.PE, -0.16, bid=90.0, ask=100.0)
    cheap_long = contract(24000, OptionType.PE, -0.05, bid=1.0, ask=2.0)
    expensive_call = contract(24800, OptionType.CE, 0.16, bid=90.0, ask=100.0)
    cheap_call = contract(25000, OptionType.CE, 0.05, bid=1.0, ask=2.0)
    assert instance._risk_metrics(
        selection, cheap_long, expensive_short, expensive_call, cheap_call
    ) == "ICS.RISK.NON_POSITIVE_MAX_LOSS"
    zero_credit_short_put = contract(24200, OptionType.PE, -0.16, bid=1.0, ask=2.0)
    zero_credit_long_put = contract(24000, OptionType.PE, -0.05, bid=90.0, ask=100.0)
    zero_credit_short_call = contract(24800, OptionType.CE, 0.16, bid=1.0, ask=2.0)
    zero_credit_long_call = contract(25000, OptionType.CE, 0.05, bid=90.0, ask=100.0)
    assert instance._risk_metrics(
        entered.strike_selection,
        zero_credit_long_put,
        zero_credit_short_put,
        zero_credit_short_call,
        zero_credit_long_call,
    ) == "ICS.PREMIUM.BELOW_MINIMUM"

    class RecordingSink:
        def __init__(self) -> None:
            self.topics: list[str] = []

        def publish(self, topic: str, payload: object) -> None:
            self.topics.append(topic)

    sink = RecordingSink()
    framework = StrategyScoringFramework(
        ScoringFrameworkConfig(enable_statistics=False),
        clock=lambda: AS_OF,
    )
    publishing = IronCondorStrategy(
        default_iron_condor_configuration(), framework, event_sink=sink
    )
    sealed = publishing.evaluate_recommendation(context())
    assert sealed.state is EntryRecommendationState.ENTER
    assert "strategy.iron_condor.entered_candidate" in sink.topics


def test_illiquid_quotes_and_require_trend_disabled() -> None:
    """Crossed quotes are rejected and optional trend strength may be disabled."""
    crossed = contract(24200, OptionType.PE, -0.16, bid=40.0, ask=38.0)
    assert strategy()._liquid(crossed) is False
    invalid_quote = replace(
        contract(24200, OptionType.PE, -0.16, bid=38.0, ask=40.0),
        bid="bad",  # type: ignore[arg-type]
    )
    assert strategy()._liquid(invalid_quote) is False
    optional_trend = IronCondorConfiguration(require_trend_strength=False)
    result = strategy(optional_trend).evaluate_recommendation(
        context(tags={"regime_tag": "MEAN_REVERTING", "iv_rank": "70"})
    )
    assert result.state is EntryRecommendationState.ENTER


def test_selection_edge_helpers_cover_remaining_branches() -> None:
    """Direct helper calls cover rare selection and validation branches."""
    instance = strategy()
    snap = snapshot()
    assert instance._select(
        replace(snap, underlying=replace(snap.underlying, last_price="bad")),  # type: ignore[arg-type]
        AS_OF.date(),
    ) == "ICS.CHAIN.INCOMPLETE"
    dte_blocked = IronCondorConfiguration(minimum_dte=30, maximum_dte=30)
    assert strategy(dte_blocked)._select(snap, AS_OF.date()) == "ICS.CHAIN.INCOMPLETE"
    assert instance._validate_context(
        context(as_of=AS_OF.replace(tzinfo=None))
    ) == "ICS.CONTEXT.INVALID"
    assert iron_condor_module._clamp(Decimal("-1"), Decimal("0"), Decimal("1")) == Decimal("0")
    assert iron_condor_module._clamp(Decimal("2"), Decimal("0"), Decimal("1")) == Decimal("1")
    long_puts = instance._long_candidates(
        list(snap.option_chain.contracts),
        OptionType.PE,
        Decimal("24500"),
        contract(24200, OptionType.PE, -0.16, bid=38.0, ask=40.0),
        Decimal("0.05"),
    )
    assert long_puts
    delta_only = IronCondorConfiguration(
        wing_selection_policy=WingSelectionPolicy.DELTA_TARGET,
        long_delta_selection_tolerance=Decimal("0.001"),
    )
    assert (
        strategy(delta_only)._long_candidates(
            list(snap.option_chain.contracts),
            OptionType.CE,
            Decimal("24500"),
            contract(24800, OptionType.CE, 0.16, bid=38.0, ask=40.0),
            Decimal("0.20"),
        )
        == []
    )
    fixed_mismatch = IronCondorConfiguration(
        wing_selection_policy=WingSelectionPolicy.FIXED_WIDTH,
        target_wing_width=Decimal("350"),
    )
    assert (
        strategy(fixed_mismatch)._long_candidates(
            list(snap.option_chain.contracts),
            OptionType.CE,
            Decimal("24500"),
            contract(24800, OptionType.CE, 0.16, bid=38.0, ask=40.0),
            Decimal("0.05"),
        )
        == []
    )
