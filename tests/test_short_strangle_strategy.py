"""Tests for the deterministic short-strangle strategy plugin."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, time, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import strategy.short_strangle_strategy as short_strangle_module
from market_data.market_snapshot import (
    OptionContractSnapshot,
    OptionType,
    UnderlyingSnapshot,
    VolatilitySnapshot,
    build_market_snapshot,
)
from strategy.base_strategy import StrategyContext
from strategy.short_strangle_strategy import (
    EntryRecommendationState,
    EventRiskEvidence,
    MarketRegimeEvidence,
    ShortStrangleConfiguration,
    ShortStrangleContext,
    ShortStrangleStrategy,
    TimeWindow,
    default_short_strangle_configuration,
    from_json,
    to_json,
)
from strategy.signals import SignalAction
from strategy.strategy_scoring_framework import (
    ScoringFrameworkConfig,
    StrategyScoringFramework,
)

AS_OF = datetime(2026, 8, 5, 5, 30, tzinfo=timezone.utc)


def contract(strike: float, option_type: OptionType, delta: float) -> OptionContractSnapshot:
    """Build one liquid option fixture contract."""
    return OptionContractSnapshot(
        underlying="NIFTY", exchange="NFO", tradingsymbol=f"NIFTY26813{strike:.0f}{option_type.value}",
        expiry="2026-08-13", strike=strike, option_type=option_type, lot_size=75,
        ltp=30.0, bid=29.5, ask=30.5, volume=1000, open_interest=10000,
        delta=delta, quote_timestamp=AS_OF,
    )


def snapshot(*, call_ask: float = 30.5, deltas: bool = True):
    """Build a complete, sorted and fresh NIFTY option-chain fixture."""
    contracts = (
        contract(24300, OptionType.CE, 0.40),
        contract(24300, OptionType.PE, -0.15),
        contract(24400, OptionType.CE, 0.30),
        contract(24400, OptionType.PE, -0.20),
        contract(24600, OptionType.CE, 0.20),
        contract(24600, OptionType.PE, -0.30),
        replace(contract(24700, OptionType.CE, 0.15), ask=call_ask),
        contract(24700, OptionType.PE, -0.40),
    )
    if not deltas:
        contracts = tuple(replace(item, delta=None) for item in contracts)
    return build_market_snapshot(
        underlying=UnderlyingSnapshot("NIFTY", "NSE", "NSE:NIFTY 50", 24500.0, quote_timestamp=AS_OF),
        contracts=contracts, underlying_symbol="NIFTY", exchange="NFO", expiry="2026-08-13",
        atm_strike=24500.0, strike_step=100.0, strike_window_strikes=4,
        minimum_strike=24300.0, maximum_strike=24700.0, lot_size=75,
        as_of=AS_OF, captured_at=AS_OF, reference_time=AS_OF, snapshot_id="short-strangle-fixture",
        volatility=VolatilitySnapshot("INDIA VIX", "NSE", "NSE:INDIA VIX", 15.0, AS_OF),
    )


def context(**overrides: object) -> StrategyContext:
    """Build a conventional tags-backed strategy context."""
    values: dict[str, object] = {
        "correlation_id": "short-strangle-correlation",
        "as_of": AS_OF,
        "snapshot": snapshot(),
        "tags": {"regime_tag": "RANGE_BOUND", "iv_rank": "70", "event_adverse": "false"},
    }
    values.update(overrides)
    return StrategyContext(**values)  # type: ignore[arg-type]


def strategy(configuration: ShortStrangleConfiguration | None = None) -> ShortStrangleStrategy:
    """Build a test strategy with deterministic framework configuration."""
    framework = StrategyScoringFramework(
        ScoringFrameworkConfig(enable_statistics=False),
        clock=lambda: AS_OF,
    )
    return ShortStrangleStrategy(configuration or default_short_strangle_configuration(), framework)


def test_enter_happy_path_and_signal_mapping() -> None:
    """A liquid, range-bound, elevated-IV setup enters deterministically."""
    result = strategy().evaluate_recommendation(context())
    signal = strategy().run(context())
    assert result.state is EntryRecommendationState.ENTER
    assert result.selection is not None and result.selection.call_strike == Decimal("24700.0")
    assert result.risk_metrics is not None
    assert result.risk_metrics.max_loss is None
    assert result.risk_metrics.max_loss_label == "UNDEFINED_UNLIMITED"
    assert signal.action is SignalAction.EVALUATE
    assert signal.metadata["risk_warning"] == "UNDEFINED_UNLIMITED"


@pytest.mark.parametrize(
    ("tags", "state", "code"),
    [
        ({"iv_rank": "70"}, EntryRecommendationState.REJECT, "SSS.REGIME.MISSING"),
        ({"regime_tag": "HIGH_VOLATILITY_CRISIS", "iv_rank": "70"}, EntryRecommendationState.REJECT, "SSS.REGIME.CRISIS"),
        ({"regime_tag": "TRENDING_UP", "iv_rank": "70"}, EntryRecommendationState.ABSTAIN, "SSS.REGIME.UNSUITABLE"),
        ({"regime_tag": "RANGE_BOUND", "iv_rank": "10"}, EntryRecommendationState.ABSTAIN, "SSS.IV_RANK.LOW"),
    ],
)
def test_regime_and_iv_gates(tags: dict[str, str], state: EntryRecommendationState, code: str) -> None:
    """Regime and IV gates preserve their specified classifications."""
    result = strategy().evaluate_recommendation(context(tags=tags))
    assert result.state is state
    assert result.reasons == (code,)


def test_time_premium_and_delta_abstentions() -> None:
    """Valid but unsuitable time, premium and strike inputs abstain."""
    bounded = ShortStrangleConfiguration(entry_time_window=TimeWindow(time(9), time(11)))
    assert strategy(bounded).evaluate_recommendation(context()).reasons == ("SSS.TIME.OUTSIDE_ENTRY_WINDOW",)
    assert strategy(ShortStrangleConfiguration(minimum_premium=Decimal("100"))).evaluate_recommendation(context()).reasons == ("SSS.PREMIUM.BELOW_MINIMUM",)
    assert strategy().evaluate_recommendation(context(snapshot=snapshot(deltas=False))).reasons == ("SSS.GREEKS.MISSING",)


def test_typed_context_and_serialization_round_trip() -> None:
    """Typed evidence works and entry recommendation JSON is canonical."""
    base = context(tags={})
    typed = ShortStrangleContext(
        base, MarketRegimeEvidence("RANGE_BOUND", AS_OF),
        EventRiskEvidence(False, AS_OF), Decimal("70"),
    )
    recommendation = strategy().evaluate_short_strangle(typed)
    restored = from_json(to_json(recommendation))
    assert restored.state is EntryRecommendationState.ENTER
    assert restored.selection == recommendation.selection
    assert restored.risk_metrics == recommendation.risk_metrics


def test_configuration_validation_and_concurrent_calls() -> None:
    """Configuration fails closed and simultaneous evaluation is identical."""
    with pytest.raises(ValueError, match="CFG-SSS"):
        ShortStrangleConfiguration(target_delta=Decimal("0.50"))
    with pytest.raises(ValueError, match="CFG-SSS"):
        ShortStrangleConfiguration(supported_underlyings=frozenset())
    instance = strategy()
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(instance.evaluate_recommendation, [context()] * 16))
    assert len({item.recommendation_id for item in results}) == 1
    assert len({to_json(item) for item in results}) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_delta": Decimal("NaN")},
        {"minimum_iv_rank": Decimal("101")},
        {"maximum_spread_width": Decimal("0")},
        {"maximum_relative_spread_width": Decimal("0")},
        {"minimum_premium": Decimal("-1")},
        {"delta_selection_tolerance": Decimal("0.50")},
        {"minimum_open_interest": -1},
        {"max_snapshot_age_seconds": 0},
        {"minimum_dte": 2, "maximum_dte": 1},
        {"scoring_profile_name": "BALANCED"},
        {"premium_price_policy": "bad"},
    ],
)
def test_all_configuration_invariants_fail_closed(kwargs: dict[str, object]) -> None:
    """Every policy-boundary invariant rejects invalid static configuration."""
    with pytest.raises(ValueError, match="CFG-SSS"):
        ShortStrangleConfiguration(**kwargs)  # type: ignore[arg-type]


def test_remaining_recommendation_gates_and_public_aliases() -> None:
    """Event, IV, stale and unsupported paths retain stable outcomes."""
    instance = strategy()
    assert instance.evaluate(context()).state is EntryRecommendationState.ENTER
    assert instance.evaluate_recommendation(
        context(tags={"regime_tag": "RANGE_BOUND", "iv_rank": "70", "event_adverse": "true"})
    ).reasons == ("SSS.EVENT.ADVERSE",)
    assert instance.evaluate_recommendation(
        context(tags={"regime_tag": "RANGE_BOUND"})
    ).reasons == ("SSS.IV_RANK.MISSING",)
    assert instance.evaluate_recommendation(
        context(tags={"regime_tag": "RANGE_BOUND", "iv_rank": "nan"})
    ).reasons == ("SSS.METRIC.NON_FINITE",)
    assert strategy(ShortStrangleConfiguration(supported_underlyings=frozenset({"SENSEX"}))).evaluate_recommendation(
        context()
    ).reasons == ("SSS.UNDERLYING.UNSUPPORTED",)
    stale = replace(context().snapshot.freshness, age_seconds=6.0)
    assert instance.evaluate_recommendation(context(snapshot=replace(context().snapshot, freshness=stale))).reasons == (
        "SSS.SNAPSHOT.STALE",
    )


def test_window_and_serialization_error_boundaries() -> None:
    """Window validation and malformed recommendation JSON fail closed."""
    with pytest.raises(ValueError, match="CFG-SSS"):
        TimeWindow(time(12), time(12))
    with pytest.raises(ValueError, match="CFG-SSS"):
        TimeWindow(time(9), time(10), "Not/A_Timezone")
    recommendation = strategy().evaluate_recommendation(context())
    assert recommendation.to_json() == to_json(recommendation)
    assert recommendation.from_json(recommendation.to_json()).state is EntryRecommendationState.ENTER
    with pytest.raises(ValueError, match="SSS.SERIALIZATION.INVALID"):
        from_json('{"schema_version":"2.0"}')


def test_numeric_and_expiry_boundary_helpers_reject_invalid_input() -> None:
    """Malformed snapshot-boundary values cannot enter Decimal calculations."""
    with pytest.raises(ValueError):
        short_strangle_module._decimal(object())
    assert short_strangle_module._parse_expiry("not-an-expiry") is None


def test_module_has_no_broker_execution_dependency() -> None:
    """The strategy remains an analysis-only module."""
    source = Path(__file__).parents[1].joinpath("strategy/short_strangle_strategy.py").read_text()
    assert "kiteconnect" not in source.lower()
    assert "place_order" not in source.lower()
