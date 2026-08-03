"""Unit tests for strategy.base_strategy."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from core.engine_context import EngineContext
from core.exceptions import EngineExecutionError
from market_data.market_snapshot import (
    MarketSnapshot,
    OptionChainSnapshot,
    OptionType,
    SnapshotQuality,
    SnapshotValidationStatus,
    UnderlyingSnapshot,
    VolatilitySnapshot,
    build_market_snapshot,
)
from strategy.base_strategy import (
    ERROR_CAPABILITY_CONTRACTS_INSUFFICIENT,
    ERROR_CAPABILITY_UNDERLYING_UNSUPPORTED,
    ERROR_CAPABILITY_VOLATILITY_REQUIRED,
    ERROR_CONFIG_INVALID,
    ERROR_CONTEXT_INVALID,
    ERROR_CONTEXT_SNAPSHOT_INVALID,
    ERROR_SIGNAL_INVALID,
    ERROR_SIGNAL_SEMANTIC_REJECT,
    STRATEGY_VERSION,
    BaseStrategy,
    StrategyContext,
    StrategyContextError,
    StrategyEngineConfigurationError,
    StrategyMetadata,
    StrategyPluginConfig,
    StrategyRiskProfileHint,
    StrategySignalError,
    validate_strategy_metadata,
    validate_strategy_plugin_config,
)
from strategy.signals import (
    ConfidenceBand,
    SignalAction,
    SignalConfidence,
    SignalDirection,
    SignalMarketContext,
    StrategyExecutionMode,
    StrategyFamily,
    TradingSignal,
    confidence_band_for_score,
    market_context_from_snapshot,
)

IST = ZoneInfo("Asia/Kolkata")


def fixed_as_of() -> datetime:
    """Monday during regular NSE session."""
    return datetime(2026, 8, 3, 10, 15, 0, tzinfo=IST)


def fixed_captured_at() -> datetime:
    return datetime(2026, 8, 3, 10, 15, 1, 42000, tzinfo=IST)


def make_contract(
    *,
    strike: float = 24300.0,
    option_type: OptionType = OptionType.CE,
    tradingsymbol: str | None = None,
    bid: float = 109.65,
    ask: float = 109.9,
    ltp: float = 110.0,
) -> object:
    from market_data.market_snapshot import OptionContractSnapshot

    suffix = option_type.value
    return OptionContractSnapshot(
        underlying="NIFTY",
        exchange="NFO",
        tradingsymbol=tradingsymbol or f"NIFTY2680724300{suffix}",
        expiry="2026-08-07",
        strike=strike,
        option_type=option_type,
        lot_size=75,
        ltp=ltp,
        bid=bid,
        ask=ask,
        volume=1000,
        open_interest=10000,
        quote_timestamp=fixed_as_of(),
    )


def make_underlying(*, last_price: float = 24296.75) -> UnderlyingSnapshot:
    return UnderlyingSnapshot(
        symbol="NIFTY",
        exchange="NSE",
        quote_key="NSE:NIFTY 50",
        last_price=last_price,
        quote_timestamp=fixed_as_of(),
    )


def minimal_valid_snapshot(*, snapshot_id: str = "test-snapshot-001") -> MarketSnapshot:
    contracts = (
        make_contract(strike=24300.0, option_type=OptionType.CE),
        make_contract(
            strike=24300.0,
            option_type=OptionType.PE,
            tradingsymbol="NIFTY2680724300PE",
            ltp=115.0,
            bid=115.0,
            ask=115.15,
        ),
    )
    return build_market_snapshot(
        underlying=make_underlying(),
        contracts=contracts,
        underlying_symbol="NIFTY",
        exchange="NFO",
        expiry="2026-08-07",
        atm_strike=24300.0,
        strike_step=50.0,
        strike_window_strikes=1,
        minimum_strike=24300.0,
        maximum_strike=24300.0,
        lot_size=75,
        as_of=fixed_as_of(),
        captured_at=fixed_captured_at(),
        snapshot_id=snapshot_id,
        reference_time=fixed_captured_at(),
    )


def valid_metadata(**overrides: object) -> StrategyMetadata:
    """Build valid strategy metadata with optional field overrides."""
    defaults: dict[str, object] = {
        "strategy_id": "short_strangle",
        "display_name": "Short Strangle",
        "version": "1.0.0",
        "strategy_family": StrategyFamily.SHORT_STRANGLE,
    }
    defaults.update(overrides)
    return StrategyMetadata(**defaults)  # type: ignore[arg-type]


def valid_plugin_config(**overrides: object) -> StrategyPluginConfig:
    """Build valid plugin configuration with optional field overrides."""
    metadata = overrides.pop("metadata", None) if "metadata" in overrides else None
    meta = metadata if isinstance(metadata, StrategyMetadata) else valid_metadata()
    base = StrategyPluginConfig(metadata=meta)
    if not overrides:
        return base
    return replace(base, **overrides)  # type: ignore[arg-type]


def valid_context(**overrides: object) -> StrategyContext:
    """Build valid strategy context with optional field overrides."""
    base = StrategyContext(
        correlation_id="corr-001",
        as_of=fixed_as_of(),
        snapshot=minimal_valid_snapshot(),
    )
    if not overrides:
        return base
    return replace(base, **overrides)  # type: ignore[arg-type]


def build_evaluate_signal(strategy: BaseStrategy, context: StrategyContext) -> TradingSignal:
    """Build a semantically valid EVALUATE signal for a strategy plugin."""
    score = 72.5
    return TradingSignal(
        signal_id="signal-echo-001",
        strategy_id=strategy.metadata.strategy_id,
        strategy_version=strategy.metadata.version,
        strategy_family=strategy.metadata.strategy_family,
        action=SignalAction.EVALUATE,
        direction=SignalDirection.NEUTRAL,
        confidence=SignalConfidence(
            score=score,
            band=confidence_band_for_score(score),
            method="echo",
        ),
        market=market_context_from_snapshot(context.snapshot),
        as_of=context.as_of,
        reasons=("setup meets short strangle criteria",),
    )


class EchoEvaluateStrategy(BaseStrategy):
    """Fake strategy that returns a valid EVALUATE signal."""

    def _execute(self, context: StrategyContext) -> TradingSignal:
        return build_evaluate_signal(self, context)


class AbstainOnlyStrategy(BaseStrategy):
    """Fake strategy that abstains via build_abstain_signal."""

    def _execute(self, context: StrategyContext) -> TradingSignal:
        return self.build_abstain_signal(context)


class NullReturnStrategy(BaseStrategy):
    """Fake strategy that incorrectly returns None."""

    def _execute(self, context: StrategyContext) -> TradingSignal:
        return None  # type: ignore[return-value]


class BadSignalStrategy(BaseStrategy):
    """Fake strategy that returns a semantically invalid signal."""

    def _execute(self, context: StrategyContext) -> TradingSignal:
        score = 72.5
        return TradingSignal(
            signal_id="bad-signal",
            strategy_id="wrong_id",
            strategy_version=self.metadata.version,
            strategy_family=self.metadata.strategy_family,
            action=SignalAction.EVALUATE,
            direction=SignalDirection.NEUTRAL,
            confidence=SignalConfidence(
                score=score,
                band=confidence_band_for_score(score),
            ),
            market=market_context_from_snapshot(context.snapshot),
            as_of=context.as_of,
            reasons=("bad plugin output",),
        )


class TestStrategyVersion:
    def test_strategy_version_constant(self) -> None:
        assert STRATEGY_VERSION == "1.0.0"


class TestStrategyExecutionMode:
    def test_execution_mode_values(self) -> None:
        assert StrategyExecutionMode.LIVE.value == "live"
        assert StrategyExecutionMode.ANALYSIS.value == "analysis"
        assert StrategyExecutionMode.BACKTEST.value == "backtest"

    def test_execution_mode_from_string(self) -> None:
        assert StrategyExecutionMode("live") is StrategyExecutionMode.LIVE

    def test_execution_mode_is_str_enum(self) -> None:
        assert isinstance(StrategyExecutionMode.LIVE, str)


class TestStrategyFamily:
    def test_strategy_family_income_strategies(self) -> None:
        assert StrategyFamily.SHORT_STRANGLE.value == "short_strangle"
        assert StrategyFamily.IRON_CONDOR.value == "iron_condor"

    def test_strategy_family_custom_and_no_strategy(self) -> None:
        assert StrategyFamily.CUSTOM.value == "custom"
        assert StrategyFamily.NO_STRATEGY.value == "no_strategy"

    def test_strategy_family_long_volatility(self) -> None:
        assert StrategyFamily.LONG_VOLATILITY.value == "long_volatility"


class TestConfidenceBandForScore:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0.0, ConfidenceBand.LOW),
            (39.9, ConfidenceBand.LOW),
            (40.0, ConfidenceBand.MEDIUM),
            (59.9, ConfidenceBand.MEDIUM),
            (60.0, ConfidenceBand.HIGH),
            (79.9, ConfidenceBand.HIGH),
            (80.0, ConfidenceBand.VERY_HIGH),
            (100.0, ConfidenceBand.VERY_HIGH),
        ],
    )
    def test_confidence_band_boundaries(self, score: float, expected: ConfidenceBand) -> None:
        assert confidence_band_for_score(score) is expected


class TestValidateStrategyMetadata:
    def test_valid_metadata_passes(self) -> None:
        validate_strategy_metadata(valid_metadata())

    def test_invalid_strategy_id_uppercase(self) -> None:
        with pytest.raises(StrategyEngineConfigurationError) as exc_info:
            validate_strategy_metadata(valid_metadata(strategy_id="Short_Strangle"))
        assert exc_info.value.code == ERROR_CONFIG_INVALID

    def test_invalid_strategy_id_too_short(self) -> None:
        with pytest.raises(StrategyEngineConfigurationError):
            validate_strategy_metadata(valid_metadata(strategy_id="a"))

    def test_empty_display_name_rejected(self) -> None:
        with pytest.raises(StrategyEngineConfigurationError) as exc_info:
            validate_strategy_metadata(valid_metadata(display_name="   "))
        assert exc_info.value.code == ERROR_CONFIG_INVALID

    def test_invalid_semver_rejected(self) -> None:
        with pytest.raises(StrategyEngineConfigurationError):
            validate_strategy_metadata(valid_metadata(version="not-a-version"))

    def test_negative_min_contracts_rejected(self) -> None:
        with pytest.raises(StrategyEngineConfigurationError):
            validate_strategy_metadata(valid_metadata(min_contracts_required=-1))

    def test_custom_family_requires_custom_family_name_tag(self) -> None:
        with pytest.raises(StrategyEngineConfigurationError) as exc_info:
            validate_strategy_metadata(
                valid_metadata(strategy_family=StrategyFamily.CUSTOM, tags=MappingProxyType({}))
            )
        assert "custom_family_name" in str(exc_info.value)

    def test_custom_family_passes_with_tag(self) -> None:
        validate_strategy_metadata(
            valid_metadata(
                strategy_family=StrategyFamily.CUSTOM,
                tags=MappingProxyType({"custom_family_name": "my_custom"}),
            )
        )

    def test_empty_supported_underlying_entry_rejected(self) -> None:
        with pytest.raises(StrategyEngineConfigurationError):
            validate_strategy_metadata(valid_metadata(supported_underlyings=("NIFTY", "  ")))


class TestValidateStrategyPluginConfig:
    def test_valid_plugin_config_passes(self) -> None:
        validate_strategy_plugin_config(valid_plugin_config())

    def test_priority_below_minimum_rejected(self) -> None:
        with pytest.raises(StrategyEngineConfigurationError) as exc_info:
            validate_strategy_plugin_config(valid_plugin_config(priority=-1))
        assert exc_info.value.code == ERROR_CONFIG_INVALID

    def test_priority_above_maximum_rejected(self) -> None:
        with pytest.raises(StrategyEngineConfigurationError):
            validate_strategy_plugin_config(valid_plugin_config(priority=1001))

    def test_invalid_metadata_type_rejected(self) -> None:
        config = StrategyPluginConfig(metadata=valid_metadata())  # type: ignore[arg-type]
        bad = replace(config, metadata="not-metadata")  # type: ignore[arg-type]
        with pytest.raises(StrategyEngineConfigurationError):
            validate_strategy_plugin_config(bad)


class TestImmutableDataclasses:
    def test_strategy_metadata_is_frozen(self) -> None:
        meta = valid_metadata()
        with pytest.raises(Exception):
            meta.display_name = "changed"  # type: ignore[misc]

    def test_strategy_plugin_config_is_frozen(self) -> None:
        config = valid_plugin_config()
        with pytest.raises(Exception):
            config.priority = 100  # type: ignore[misc]

    def test_strategy_context_is_frozen(self) -> None:
        context = valid_context()
        with pytest.raises(Exception):
            context.correlation_id = "changed"  # type: ignore[misc]

    def test_trading_signal_is_frozen(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        signal = strategy.build_abstain_signal(valid_context())
        with pytest.raises(Exception):
            signal.action = SignalAction.WAIT  # type: ignore[misc]


class TestBaseStrategyContract:
    def test_cannot_instantiate_abstract_base_strategy(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            BaseStrategy(valid_plugin_config())  # type: ignore[abstract]

    def test_fake_strategy_exposes_metadata_properties(self) -> None:
        config = valid_plugin_config(priority=750)
        strategy = EchoEvaluateStrategy(config)
        assert strategy.engine_name == "short_strangle"
        assert strategy.engine_version == "1.0.0"
        assert strategy.strategy_version == "1.0.0"
        assert strategy.plugin_config is config
        assert strategy.metadata.strategy_family is StrategyFamily.SHORT_STRANGLE

    def test_evaluate_raises_engine_execution_error(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        context = EngineContext(
            correlation_id="corr-001",
            as_of=fixed_as_of(),
            payload={},
        )
        with pytest.raises(EngineExecutionError) as exc_info:
            strategy.evaluate(context)
        assert "run(StrategyContext)" in str(exc_info.value)

    def test_init_rejects_invalid_configuration(self) -> None:
        bad_config = valid_plugin_config(
            metadata=valid_metadata(strategy_id="INVALID-ID"),
        )
        with pytest.raises(StrategyEngineConfigurationError) as exc_info:
            EchoEvaluateStrategy(bad_config)
        assert exc_info.value.code == ERROR_CONFIG_INVALID


class TestMetadataSnapshot:
    def test_metadata_fingerprint_stable_across_tag_order(self) -> None:
        tags_a = MappingProxyType({"zeta": "1", "alpha": "2"})
        tags_b = MappingProxyType({"alpha": "2", "zeta": "1"})
        strategy_a = EchoEvaluateStrategy(valid_plugin_config(metadata=valid_metadata(tags=tags_a)))
        strategy_b = EchoEvaluateStrategy(valid_plugin_config(metadata=valid_metadata(tags=tags_b)))
        assert strategy_a.metadata_fingerprint() == strategy_b.metadata_fingerprint()

    def test_metadata_snapshot_returns_same_instance_when_tags_sorted(self) -> None:
        tags = MappingProxyType({"alpha": "1", "beta": "2"})
        config = valid_plugin_config(metadata=valid_metadata(tags=tags))
        strategy = EchoEvaluateStrategy(config)
        assert strategy.metadata_snapshot() is strategy.metadata

    def test_metadata_fingerprint_is_stable(self) -> None:
        config = valid_plugin_config(
            metadata=valid_metadata(
                tags=MappingProxyType({"b": "2", "a": "1"}),
                risk_profile_hint=StrategyRiskProfileHint.DEFINED,
            )
        )
        strategy = EchoEvaluateStrategy(config)
        first = strategy.metadata_fingerprint()
        second = strategy.metadata_fingerprint()
        assert first == second
        assert len(first) == 64


class TestValidateContext:
    def test_valid_context_passes(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        strategy.validate_context(valid_context())

    def test_rejects_non_strategy_context(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        with pytest.raises(StrategyContextError) as exc_info:
            strategy.validate_context("not-a-context")  # type: ignore[arg-type]
        assert exc_info.value.code == ERROR_CONTEXT_INVALID
        assert exc_info.value.field == "context"

    def test_rejects_empty_correlation_id(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        context = valid_context(correlation_id="   ")
        with pytest.raises(StrategyContextError) as exc_info:
            strategy.validate_context(context)
        assert exc_info.value.field == "correlation_id"

    def test_rejects_naive_as_of(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        context = valid_context(as_of=datetime(2026, 8, 3, 10, 15, 0))
        with pytest.raises(StrategyContextError) as exc_info:
            strategy.validate_context(context)
        assert exc_info.value.field == "as_of"

    def test_rejects_none_snapshot(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        context = valid_context(snapshot=None)  # type: ignore[arg-type]
        with pytest.raises(StrategyContextError) as exc_info:
            strategy.validate_context(context)
        assert exc_info.value.field == "snapshot"

    def test_rejects_wrong_snapshot_type(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        context = valid_context(snapshot={"bad": True})  # type: ignore[arg-type]
        with pytest.raises(StrategyContextError) as exc_info:
            strategy.validate_context(context)
        assert exc_info.value.code == ERROR_CONTEXT_SNAPSHOT_INVALID

    def test_rejects_invalid_market_snapshot(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        base = minimal_valid_snapshot()
        invalid_underlying = UnderlyingSnapshot(
            symbol="NIFTY 50",
            exchange="NSE",
            quote_key="NSE:NIFTY 50",
            last_price=float("nan"),
            quote_timestamp=fixed_as_of(),
        )
        invalid_snapshot = replace(base, underlying=invalid_underlying)
        context = valid_context(snapshot=invalid_snapshot)
        with pytest.raises(StrategyContextError) as exc_info:
            strategy.validate_context(context)
        assert exc_info.value.code == ERROR_CONTEXT_SNAPSHOT_INVALID

    def test_rejects_partial_snapshot_in_live_mode(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        base = minimal_valid_snapshot()
        partial_snapshot = replace(
            base,
            quality=SnapshotQuality(
                validation_status=SnapshotValidationStatus.PARTIAL,
                completeness_score=80.0,
                missing_quotes=0,
                inverted_markets=0,
                warnings=(),
                errors=(),
            ),
        )
        context = valid_context(snapshot=partial_snapshot, execution_mode=StrategyExecutionMode.LIVE)
        with pytest.raises(StrategyContextError) as exc_info:
            strategy.validate_context(context)
        assert exc_info.value.field == "snapshot.quality.validation_status"

    def test_allows_partial_snapshot_when_configured(self) -> None:
        config = valid_plugin_config(allow_partial_snapshot=True)
        strategy = EchoEvaluateStrategy(config)
        base = minimal_valid_snapshot()
        partial_snapshot = replace(
            base,
            quality=SnapshotQuality(
                validation_status=SnapshotValidationStatus.PARTIAL,
                completeness_score=80.0,
                missing_quotes=0,
                inverted_markets=0,
                warnings=(),
                errors=(),
            ),
        )
        context = valid_context(snapshot=partial_snapshot, execution_mode=StrategyExecutionMode.LIVE)
        strategy.validate_context(context)

    def test_rejects_underlying_symbol_mismatch(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        base = minimal_valid_snapshot()
        mismatched = replace(
            base,
            underlying=replace(base.underlying, symbol="BANKNIFTY"),
        )
        context = valid_context(snapshot=mismatched)
        with pytest.raises(StrategyContextError) as exc_info:
            strategy.validate_context(context)
        assert exc_info.value.field == "snapshot.underlying"

    def test_rejects_unsupported_underlying_capability(self) -> None:
        config = valid_plugin_config(
            metadata=valid_metadata(supported_underlyings=("BANKNIFTY",)),
        )
        strategy = EchoEvaluateStrategy(config)
        with pytest.raises(StrategyContextError) as exc_info:
            strategy.validate_context(valid_context())
        assert exc_info.value.code == ERROR_CAPABILITY_UNDERLYING_UNSUPPORTED

    def test_rejects_insufficient_contracts_capability(self) -> None:
        config = valid_plugin_config(metadata=valid_metadata(min_contracts_required=5))
        strategy = EchoEvaluateStrategy(config)
        with pytest.raises(StrategyContextError) as exc_info:
            strategy.validate_context(valid_context())
        assert exc_info.value.code == ERROR_CAPABILITY_CONTRACTS_INSUFFICIENT

    def test_rejects_missing_volatility_capability(self) -> None:
        config = valid_plugin_config(metadata=valid_metadata(requires_volatility_snapshot=True))
        strategy = EchoEvaluateStrategy(config)
        with pytest.raises(StrategyContextError) as exc_info:
            strategy.validate_context(valid_context())
        assert exc_info.value.code == ERROR_CAPABILITY_VOLATILITY_REQUIRED

    def test_passes_volatility_capability_when_present(self) -> None:
        config = valid_plugin_config(metadata=valid_metadata(requires_volatility_snapshot=True))
        strategy = EchoEvaluateStrategy(config)
        base = minimal_valid_snapshot()
        snapshot = replace(
            base,
            volatility=VolatilitySnapshot(
                symbol="INDIA VIX",
                exchange="NSE",
                quote_key="NSE:INDIA VIX",
                last_price=13.24,
                quote_timestamp=fixed_as_of(),
            ),
        )
        strategy.validate_context(valid_context(snapshot=snapshot))

    def test_rejects_invalid_prior_signal_entry(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        context = valid_context(prior_signals=("not-a-signal",))  # type: ignore[arg-type]
        with pytest.raises(StrategyContextError) as exc_info:
            strategy.validate_context(context)
        assert exc_info.value.field == "prior_signals[0]"

    def test_skips_capability_checks_when_disabled(self) -> None:
        config = valid_plugin_config(
            metadata=valid_metadata(
                supported_underlyings=("BANKNIFTY",),
                requires_volatility_snapshot=True,
                min_contracts_required=100,
            ),
            enforce_capability_checks=False,
        )
        strategy = EchoEvaluateStrategy(config)
        strategy.validate_context(valid_context())


class TestRunAndExecute:
    def test_run_returns_valid_evaluate_signal(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        signal = strategy.run(valid_context())
        assert signal.action is SignalAction.EVALUATE
        assert signal.strategy_id == "short_strangle"
        assert signal.snapshot_id == "test-snapshot-001"

    def test_run_abstain_strategy(self) -> None:
        strategy = AbstainOnlyStrategy(valid_plugin_config())
        signal = strategy.run(valid_context())
        assert signal.action is SignalAction.ABSTAIN
        assert signal.confidence.score == 0.0
        assert signal.reasons

    def test_run_rejects_none_from_execute(self) -> None:
        strategy = NullReturnStrategy(valid_plugin_config())
        with pytest.raises(StrategySignalError) as exc_info:
            strategy.run(valid_context())
        assert exc_info.value.code == ERROR_SIGNAL_INVALID

    def test_run_rejects_semantically_invalid_signal(self) -> None:
        strategy = BadSignalStrategy(valid_plugin_config())
        with pytest.raises(StrategySignalError) as exc_info:
            strategy.run(valid_context())
        assert exc_info.value.code == ERROR_SIGNAL_SEMANTIC_REJECT
        assert exc_info.value.field == "strategy_id"

    def test_validate_configuration_can_be_called_directly(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        strategy.validate_configuration()


class TestBuildAbstainSignal:
    def test_build_abstain_signal_defaults(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        context = valid_context()
        signal = strategy.build_abstain_signal(context)
        assert signal.action is SignalAction.ABSTAIN
        assert signal.direction is SignalDirection.UNKNOWN
        assert signal.underlying == "NIFTY"
        assert signal.confidence.band is ConfidenceBand.LOW

    def test_build_abstain_signal_is_deterministic(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        context = valid_context()
        first = strategy.build_abstain_signal(context)
        second = strategy.build_abstain_signal(context)
        assert first.signal_id == second.signal_id


class TestValidateTradingSignal:
    def test_rejects_empty_reasons(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        context = valid_context()
        bad = replace(
            build_evaluate_signal(strategy, context),
            reasons=(),
        )
        with pytest.raises(StrategySignalError) as exc_info:
            strategy.validate_trading_signal(bad, context)
        assert exc_info.value.field == "reasons"

    def test_rejects_mismatched_confidence_band(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        context = valid_context()
        signal = build_evaluate_signal(strategy, context)
        bad = replace(
            signal,
            confidence=replace(signal.confidence, band=ConfidenceBand.LOW),
        )
        with pytest.raises(StrategySignalError) as exc_info:
            strategy.validate_trading_signal(bad, context)
        assert exc_info.value.field == "confidence.band"

    def test_rejects_evaluate_with_no_strategy_family(self) -> None:
        config = valid_plugin_config(
            metadata=valid_metadata(strategy_family=StrategyFamily.NO_STRATEGY),
        )
        strategy = EchoEvaluateStrategy(config)
        context = valid_context()
        score = 50.0
        signal = TradingSignal(
            signal_id="no-strategy-signal",
            strategy_id=strategy.metadata.strategy_id,
            strategy_version=strategy.metadata.version,
            strategy_family=StrategyFamily.NO_STRATEGY,
            action=SignalAction.EVALUATE,
            direction=SignalDirection.UNKNOWN,
            confidence=SignalConfidence(score=score, band=confidence_band_for_score(score)),
            market=market_context_from_snapshot(context.snapshot),
            as_of=context.as_of,
            reasons=("should not evaluate",),
        )
        with pytest.raises(StrategySignalError) as exc_info:
            strategy.validate_trading_signal(signal, context)
        assert exc_info.value.code == ERROR_SIGNAL_SEMANTIC_REJECT

    def test_rejects_wrong_underlying_symbol(self) -> None:
        strategy = EchoEvaluateStrategy(valid_plugin_config())
        context = valid_context()
        base_signal = build_evaluate_signal(strategy, context)
        bad = replace(
            base_signal,
            market=replace(base_signal.market, underlying="BANKNIFTY"),
        )
        with pytest.raises(StrategySignalError) as exc_info:
            strategy.validate_trading_signal(bad, context)
        assert exc_info.value.field == "underlying"


class TestStrategyErrors:
    def test_configuration_error_carries_code(self) -> None:
        error = StrategyEngineConfigurationError("bad config", code=ERROR_CONFIG_INVALID)
        assert error.code == ERROR_CONFIG_INVALID
        assert str(error) == "bad config"

    def test_context_error_carries_code_and_field(self) -> None:
        error = StrategyContextError("bad context", code=ERROR_CONTEXT_INVALID, field="as_of")
        assert error.code == ERROR_CONTEXT_INVALID
        assert error.field == "as_of"


class TestThreadSafety:
    def test_concurrent_run_calls_are_safe(self) -> None:
        strategy = AbstainOnlyStrategy(valid_plugin_config())
        contexts = [
            valid_context(
                correlation_id=f"corr-{index}",
                snapshot=minimal_valid_snapshot(snapshot_id=f"snapshot-{index}"),
            )
            for index in range(12)
        ]
        results: list[TradingSignal] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker(ctx: StrategyContext) -> None:
            try:
                signal = strategy.run(ctx)
                with lock:
                    results.append(signal)
            except BaseException as exc:  # pragma: no cover - failure path
                with lock:
                    errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, ctx) for ctx in contexts]
            for future in as_completed(futures):
                future.result()

        assert not errors
        assert len(results) == len(contexts)
        signal_ids = {item.signal_id for item in results}
        assert len(signal_ids) == len(contexts)
