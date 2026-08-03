"""Centralized immutable application configuration for THETA AI TRADER v1.0.

This module is the single authoritative bootstrap layer that loads, validates,
merges, and exposes frozen configuration for every institutional pipeline engine,
the System Orchestrator, operational surfaces, and platform infrastructure.

Merge order (later layers override earlier layers):

1. BASE_DEFAULTS
2. PROFILE_DEFAULTS (development | paper | production)
3. CONFIG_FILE (optional YAML/JSON)
4. LEGACY_USER_CONFIG (optional user_config.json)
5. ENVIRONMENT (THETA_* variables)
6. CLI_OVERRIDE (LoadOptions.cli_overrides)
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import os
import stat
import uuid
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Mapping, Protocol, TypeVar, runtime_checkable

from apme.adaptive_position_management_engine import APMEConfig
from core.event_bus import DispatchMode, EventBusPolicy
from decision.trade_decision_engine import (
    DecisionFilterPolicy,
    TradeDecisionEngineConfig,
)
from execution.execution_engine import (
    ExecutionEngineConfig,
    SlippagePolicy,
    default_execution_engine_config,
)
from execution.order_manager import OrderManagerConfig
from market_data.market_data_engine import (
    MarketDataEngineConfig,
    ReconnectPolicy,
    UniverseConfig,
)
from portfolio.portfolio_manager import PortfolioManagerConfig
from portfolio.position_manager import PositionManagerConfig
from risk.risk_engine import (
    RiskEngineConfig,
    RiskProfileTier,
    UserRiskProfile,
    default_risk_engine_config,
)
from strategy.registry import StrategyRegistryConfig
from strategy.signals import StrategyExecutionMode
from strategy.strategy_evaluation_engine import (
    StrategyEvaluationEngineConfig,
)
from system.system_orchestrator import (
    SystemOrchestratorConfig,
    validate_orchestrator_config,
)

APPLICATION_CONFIG_VERSION: Final[str] = "1.0.0"
APPLICATION_CONFIG_SCHEMA_VERSION: Final[str] = "1.0.0"
PRODUCER_NAME: Final[str] = "application_configuration"
DEFAULT_CONFIG_PATH: Final[str] = "config/application.yaml"
DEFAULT_LEGACY_USER_CONFIG_PATH: Final[str] = "config/user_config.json"

_DEFAULT_SUBSCRIPTION_PATTERNS: Final[tuple[str, ...]] = (
    "market.snapshot.published",
    "order.plan.completed",
    "portfolio.snapshot.published",
    "apme.risk.escalated",
)

_KNOWN_ENV_VARS: Final[frozenset[str]] = frozenset(
    {
        "THETA_PROFILE",
        "THETA_CONFIG_FILE",
        "THETA_USER_CONFIG_PATH",
        "THETA_ACCOUNT_ID",
        "THETA_USER_ID",
        "THETA_TIMEZONE",
        "THETA_LOG_LEVEL",
        "THETA_LOG_FORMAT",
        "THETA_LOG_FILE",
        "THETA_BROKER_TYPE",
        "THETA_BROKER_API_KEY",
        "THETA_BROKER_API_SECRET",
        "THETA_BROKER_ACCESS_TOKEN",
        "THETA_MARKET_UNDERLYING",
        "THETA_MARKET_STRIKES_EACH_SIDE",
        "THETA_TRADING_ENABLED",
        "THETA_NEW_ENTRIES_ENABLED",
        "THETA_RISK_MAX_PER_TRADE_PCT",
        "THETA_RISK_MAX_DAILY_LOSS_PCT",
        "THETA_RISK_MAX_DRAWDOWN_PCT",
        "THETA_DASHBOARD_ENABLED",
        "THETA_DASHBOARD_PORT",
        "THETA_DASHBOARD_AUTH_TOKEN",
        "THETA_EXECUTION_MODE",
        "THETA_ALLOW_MOCK_BROKER_IN_PRODUCTION",
        "THETA_ALLOW_ANALYSIS_IN_PRODUCTION",
        "THETA_STRATEGY_PLUGIN_DIR",
        "THETA_DETERMINISTIC_FINGERPRINT",
    }
)

_VALID_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
)

_CONFIDENCE_BAND_MIN_SCORE: Final[Mapping[str, float]] = {
    "LOW": 20.0,
    "MEDIUM": 40.0,
    "HIGH": 60.0,
    "VERY_HIGH": 80.0,
}

_LOGGER = logging.getLogger("config.application_configuration")

_T = TypeVar("_T")


class EnvironmentProfile(str, Enum):
    """Named deployment environment profile."""

    DEVELOPMENT = "development"
    PAPER = "paper"
    PRODUCTION = "production"


class BrokerType(str, Enum):
    """Broker implementation selector."""

    ZERODHA_KITE = "zerodha_kite"
    MOCK = "mock"
    RECORDING = "recording"


class DashboardAuthMode(str, Enum):
    """Dashboard authentication policy."""

    NONE = "none"
    TOKEN = "token"
    OIDC = "oidc"


class LogFormat(str, Enum):
    """Log output format."""

    TEXT = "text"
    JSON = "json"


class SecretSource(str, Enum):
    """Secret resolution source type."""

    ENVIRONMENT = "environment"
    FILE = "file"
    VAULT = "vault"
    INLINE_FOR_TESTS = "inline_for_tests"


class ConfigurationLayer(str, Enum):
    """Configuration merge layer identifiers."""

    BASE_DEFAULTS = "base_defaults"
    PROFILE_DEFAULTS = "profile_defaults"
    CONFIG_FILE = "config_file"
    LEGACY_USER_CONFIG = "legacy_user_config"
    ENVIRONMENT = "environment"
    CLI_OVERRIDE = "cli_override"


class ApplicationConfigurationError(Exception):
    """Raised when configuration load or validation fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


class SecretResolutionError(ApplicationConfigurationError):
    """Raised when a required secret cannot be resolved."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "CONFIG.SECRET.RESOLUTION_FAILED",
        field: str | None = None,
    ) -> None:
        super().__init__(message, code=code, field=field)


@dataclass(frozen=True)
class ConfigurationValidationIssue:
    """Single validation issue."""

    code: str
    message: str
    field: str | None = None
    section: str | None = None
    severity: str = "ERROR"


@dataclass(frozen=True)
class ApplicationConfigurationValidationResult:
    """Outcome of configuration validation."""

    errors: tuple[ConfigurationValidationIssue, ...] = ()
    warnings: tuple[ConfigurationValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return True when no errors are present."""
        return not self.errors


@dataclass(frozen=True)
class SecretReference:
    """Logical pointer to a secret value — never the value itself."""

    ref_id: str
    source: SecretSource
    locator: str
    required: bool = False
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class SecretReferences:
    """Collection of secret references keyed by logical ref name."""

    refs: Mapping[str, SecretReference] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class AccountConfiguration:
    """Account and user identity hints."""

    account_id: str = ""
    user_id: str = ""
    display_name: str = ""
    currency: str = "INR"
    timezone: str = "Asia/Kolkata"


@dataclass(frozen=True)
class LoggingConfiguration:
    """Platform logging setup."""

    root_level: str = "INFO"
    platform_level: str = "INFO"
    engine_level: str = "INFO"
    broker_level: str = "WARNING"
    format: LogFormat = LogFormat.TEXT
    log_file_path: str | None = None
    max_file_bytes: int = 10_485_760
    backup_count: int = 5
    correlation_id_injection: bool = True


@dataclass(frozen=True)
class BrokerConfiguration:
    """Broker connection metadata — secret values never stored here."""

    broker_id: str = "default"
    broker_type: BrokerType = BrokerType.MOCK
    api_base_url: str = "https://api.kite.trade"
    websocket_url: str | None = "wss://ws.kite.trade"
    connect_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 30.0
    max_retries: int = 3
    paper_trading: bool = False
    api_key_secret_ref: str = "broker.api_key"
    api_secret_secret_ref: str = "broker.api_secret"
    access_token_secret_ref: str | None = "broker.access_token"
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class MarketDataConfiguration:
    """Market data engine bootstrap settings."""

    underlying: str = "NIFTY"
    exchange: str = "NFO"
    spot_exchange: str = "NSE"
    spot_symbol: str = "NIFTY 50"
    strikes_each_side: int = 10
    include_vix: bool = True
    publish_interval_seconds: float = 1.0
    instrument_cache_ttl_seconds: float = 3600.0
    minimum_publish_coverage_ratio: float = 0.8
    connect_timeout_seconds: float = 10.0
    max_subscriptions: int = 500
    reconnect_max_attempts: int = 10
    reconnect_base_delay_seconds: float = 1.0
    timezone: str = "Asia/Kolkata"


@dataclass(frozen=True)
class StrategyConfiguration:
    """Strategy registry and evaluation bootstrap settings."""

    registry_plugin_dir: str = "strategy/plugins/"
    enabled_strategy_ids: frozenset[str] = frozenset()
    disabled_strategy_ids: frozenset[str] = frozenset()
    evaluation_timeout_seconds: float = 30.0
    min_suitability_score: float = 30.0
    min_ranking_score: float = 30.0
    allow_manual_strategy_selection: bool = False
    registry_strict_mode: bool = False


@dataclass(frozen=True)
class UserRiskLimits:
    """User-facing risk limits (maps from legacy user config)."""

    max_risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_drawdown_pct: float = 10.0
    max_consecutive_losses: int = 3
    max_open_positions: int = 3
    caution_risk_multiplier: float = 0.5
    expiry_risk_multiplier: float = 0.5
    medium_confidence_multiplier: float = 0.75
    minimum_risk_multiplier: float = 0.25


@dataclass(frozen=True)
class RiskBudgetConfiguration:
    """Daily risk budget allocation settings."""

    allocation_mode: str = "FIXED"
    max_trades_per_day: int = 3
    confidence_scaling_enabled: bool = True
    minimum_setup_score: float = 60.0
    max_single_trade_daily_risk_pct: float = 40.0


@dataclass(frozen=True)
class RiskEnginePolicyOverrides:
    """Overrides applied when projecting into :class:`RiskEngineConfig`."""

    short_circuit_on_failure: bool = True
    strict_correlation_match: bool = True
    strict_decision_integrity: bool = True
    strict_portfolio_fingerprint: bool = False
    require_sizing_hint_in_live: bool = True
    reject_unknown_margin: bool = False
    reject_unknown_capital: bool = False
    skip_review_in_analysis: bool = False
    allow_invalid_signal_in_analysis: bool = False
    deterministic_fingerprint: bool = True
    apply_confidence_risk_multiplier: bool = True
    analysis_mode_limit_multiplier: float = 1.0


@dataclass(frozen=True)
class RiskConfiguration:
    """Risk limits and engine policy projection."""

    user_limits: UserRiskLimits = field(default_factory=UserRiskLimits)
    engine: RiskEnginePolicyOverrides = field(default_factory=RiskEnginePolicyOverrides)
    budget: RiskBudgetConfiguration = field(default_factory=RiskBudgetConfiguration)
    kill_switch_default_active: bool = False
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ExecutionEnginePolicyOverrides:
    """Overrides applied when projecting into :class:`ExecutionEngineConfig`."""

    require_contract_selection_in_live: bool = True
    allow_structure_hint_heuristics: bool = False
    require_sizing_hint_in_live: bool = True
    allow_market_orders_live: bool = False
    split_quantity_equally_across_legs: bool = True
    short_circuit_on_failure: bool = True
    strict_correlation: bool = True
    strict_output_validation: bool = True
    deterministic_fingerprint: bool = True
    skip_planning_in_analysis: bool = False
    allow_invalid_signal_in_analysis: bool = False
    abort_on_leg_failure: bool = True


@dataclass(frozen=True)
class OrderManagerPolicyOverrides:
    """Overrides applied when projecting into :class:`OrderManagerConfig`."""

    strict_output_validation: bool = True
    publish_lifecycle_events: bool = True
    max_poll_attempts: int = 30
    poll_interval_seconds: float = 1.0
    submission_timeout_seconds: float = 120.0


@dataclass(frozen=True)
class ExecutionConfiguration:
    """Execution and order manager policy projection."""

    engine: ExecutionEnginePolicyOverrides = field(
        default_factory=ExecutionEnginePolicyOverrides
    )
    order_manager: OrderManagerPolicyOverrides = field(
        default_factory=OrderManagerPolicyOverrides
    )
    slippage_bps_default: float = 50.0
    max_legs_per_plan: int = 8
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class OrchestratorConfiguration:
    """System orchestrator bootstrap settings."""

    enable_pre_trade_cycle: bool = True
    enable_post_fill_cycle: bool = True
    enable_event_driven_cycles: bool = True
    serial_cycle_execution: bool = True
    cycle_timeout_seconds: int = 120
    shutdown_drain_timeout_seconds: int = 60
    health_probe_interval_seconds: int = 30
    stale_snapshot_max_age_seconds: int = 60
    strict_correlation: bool = True
    deterministic_fingerprint: bool = True
    publish_system_events: bool = True
    fail_fast_on_engine_error: bool = False
    block_pre_trade_in_degraded: bool = True
    subscription_patterns: tuple[str, ...] = _DEFAULT_SUBSCRIPTION_PATTERNS


@dataclass(frozen=True)
class EventBusConfiguration:
    """Event bus policy bootstrap settings."""

    dispatch_mode: str = "sync"
    max_handler_exceptions_before_unsubscribe: int = 0
    allow_clear: bool = False
    publish_system_events: bool = True


@dataclass(frozen=True)
class PositionConfiguration:
    """Position manager bootstrap settings."""

    strict_correlation: bool = True
    publish_lifecycle_events: bool = True
    price_hint_max_age_seconds: int = 300
    deterministic_fingerprint: bool = True
    strict_output_validation: bool = True


@dataclass(frozen=True)
class PortfolioConfiguration:
    """Portfolio manager bootstrap settings."""

    require_account_hints: bool = True
    track_peak_equity: bool = True
    margin_hint_max_age_seconds: int = 300
    strict_correlation: bool = True
    deterministic_fingerprint: bool = True
    publish_lifecycle_events: bool = True


@dataclass(frozen=True)
class APMEConfiguration:
    """APME bootstrap settings."""

    decision_cooldown_seconds: int = 60
    enable_portfolio_protection: bool = True
    drawdown_halt_threshold_pct: float = 10.0
    drawdown_reduce_threshold_pct: float = 5.0
    strict_correlation: bool = True
    deterministic_fingerprint: bool = True
    publish_lifecycle_events: bool = True


@dataclass(frozen=True)
class DashboardConfiguration:
    """Dashboard server bootstrap settings."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    auth_mode: DashboardAuthMode = DashboardAuthMode.NONE
    auth_token_secret_ref: str | None = None
    refresh_interval_seconds: float = 2.0
    cors_allowed_origins: tuple[str, ...] = ()
    expose_redacted_config: bool = True
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class FeatureFlags:
    """Boolean toggles for optional platform behaviour."""

    trading_enabled: bool = True
    new_entries_enabled: bool = True
    expiry_trading_enabled: bool = True
    post_fill_apme_enabled: bool = True
    event_driven_cycles_enabled: bool = True
    dashboard_enabled: bool = True
    paper_broker_simulation: bool = False
    allow_caution_signals: bool = True
    minimum_confidence_band: str = "MEDIUM"
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class PathConfiguration:
    """Configurable filesystem paths."""

    config_dir: str = "config/"
    log_dir: str = "logs/"
    data_dir: str = "data/"
    strategy_plugin_dir: str = "strategy/plugins/"
    instrument_cache_path: str | None = None


@dataclass(frozen=True)
class LoadOptions:
    """Options controlling configuration load behaviour."""

    profile: EnvironmentProfile | None = None
    config_file_path: str | None = None
    user_config_path: str | None = None
    cli_overrides: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    allow_missing_secrets: bool = False
    allow_missing_config_file: bool = False
    strict_unknown_env_vars: bool | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@runtime_checkable
class SecretProvider(Protocol):
    """Resolve secret references without storing values in ApplicationConfiguration."""

    def get_secret(self, ref: SecretReference) -> str:
        """Return secret value for reference."""

    def is_available(self, ref: SecretReference) -> bool:
        """Return True when secret source is reachable."""


@dataclass(frozen=True)
class ApplicationConfiguration:
    """Immutable sealed application configuration bundle."""

    schema_version: str
    config_id: str
    config_fingerprint: str
    loaded_at: datetime
    profile: EnvironmentProfile
    execution_mode: StrategyExecutionMode
    account: AccountConfiguration
    logging: LoggingConfiguration
    broker: BrokerConfiguration
    market_data: MarketDataConfiguration
    strategy: StrategyConfiguration
    risk: RiskConfiguration
    execution: ExecutionConfiguration
    orchestrator: OrchestratorConfiguration
    event_bus: EventBusConfiguration
    position: PositionConfiguration
    portfolio: PortfolioConfiguration
    apme: APMEConfiguration
    dashboard: DashboardConfiguration
    features: FeatureFlags
    secrets: SecretReferences
    paths: PathConfiguration
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not _is_timezone_aware(self.loaded_at):
            raise ApplicationConfigurationError(
                "loaded_at must be timezone-aware.",
                code="CONFIG.VALIDATION.FIELD_INVALID",
                field="loaded_at",
            )
        expected = compute_config_fingerprint(self)
        if self.config_fingerprint != expected:
            raise ApplicationConfigurationError(
                "config_fingerprint does not match computed fingerprint.",
                code="CONFIG.VALIDATION.FIELD_INVALID",
                field="config_fingerprint",
            )

    def to_orchestrator_config(self) -> SystemOrchestratorConfig:
        """Project orchestrator settings into SystemOrchestratorConfig."""
        return SystemOrchestratorConfig(
            execution_mode=self.execution_mode,
            account_id=self.account.account_id,
            enable_pre_trade_cycle=self.orchestrator.enable_pre_trade_cycle,
            enable_post_fill_cycle=self.orchestrator.enable_post_fill_cycle,
            enable_event_driven_cycles=self.orchestrator.enable_event_driven_cycles,
            serial_cycle_execution=self.orchestrator.serial_cycle_execution,
            cycle_timeout_seconds=self.orchestrator.cycle_timeout_seconds,
            shutdown_drain_timeout_seconds=self.orchestrator.shutdown_drain_timeout_seconds,
            health_probe_interval_seconds=self.orchestrator.health_probe_interval_seconds,
            stale_snapshot_max_age_seconds=self.orchestrator.stale_snapshot_max_age_seconds,
            strict_correlation=self.orchestrator.strict_correlation,
            deterministic_fingerprint=self.orchestrator.deterministic_fingerprint,
            publish_system_events=self.orchestrator.publish_system_events,
            fail_fast_on_engine_error=self.orchestrator.fail_fast_on_engine_error,
            block_pre_trade_in_degraded=self.orchestrator.block_pre_trade_in_degraded,
            subscription_patterns=self.orchestrator.subscription_patterns,
            metadata=MappingProxyType(dict(self.metadata)),
        )

    def to_event_bus_policy(self) -> EventBusPolicy:
        """Project event bus settings into EventBusPolicy."""
        return EventBusPolicy(
            dispatch_mode=DispatchMode.SYNC,
            allow_clear=self.event_bus.allow_clear,
            emit_subscriber_failure_events=self.event_bus.publish_system_events,
        )

    def to_market_data_engine_config(self) -> MarketDataEngineConfig:
        """Project market data settings into MarketDataEngineConfig."""
        md = self.market_data
        return MarketDataEngineConfig(
            universe=UniverseConfig(
                underlying=md.underlying,
                exchange=md.exchange,
                strikes_each_side=md.strikes_each_side,
                include_vix=md.include_vix,
                spot_symbol=md.spot_symbol,
                spot_exchange=md.spot_exchange,
            ),
            publish_interval_seconds=md.publish_interval_seconds,
            instrument_cache_ttl_seconds=md.instrument_cache_ttl_seconds,
            minimum_publish_coverage_ratio=md.minimum_publish_coverage_ratio,
            connect_timeout_seconds=md.connect_timeout_seconds,
            max_subscriptions=md.max_subscriptions,
            reconnect_policy=ReconnectPolicy(
                max_attempts=md.reconnect_max_attempts,
                initial_delay_seconds=md.reconnect_base_delay_seconds,
            ),
            timezone=md.timezone,
        )

    def to_strategy_evaluation_engine_config(self) -> StrategyEvaluationEngineConfig:
        """Project strategy settings into StrategyEvaluationEngineConfig."""
        timeout_ms = max(1, int(self.strategy.evaluation_timeout_seconds * 1000))
        return StrategyEvaluationEngineConfig(
            plugin_timeout_ms=timeout_ms,
            strict_registry_match=self.strategy.registry_strict_mode,
        )

    def to_strategy_registry_config(self) -> StrategyRegistryConfig:
        """Project strategy settings into StrategyRegistryConfig."""
        return StrategyRegistryConfig(
            strict_batch=self.strategy.registry_strict_mode,
        )

    def to_trade_decision_engine_config(self) -> TradeDecisionEngineConfig:
        """Project decision-related settings into TradeDecisionEngineConfig."""
        band = self.features.minimum_confidence_band.upper()
        min_confidence = _CONFIDENCE_BAND_MIN_SCORE.get(
            band,
            _CONFIDENCE_BAND_MIN_SCORE["MEDIUM"],
        )
        return TradeDecisionEngineConfig(
            filter_policy=DecisionFilterPolicy(
                default_min_confidence=min_confidence,
                default_min_suitability=self.strategy.min_suitability_score,
                default_min_ranking=self.strategy.min_ranking_score,
            ),
            strict_correlation=self.orchestrator.strict_correlation,
            deterministic_fingerprint=self.orchestrator.deterministic_fingerprint,
        )

    def to_risk_engine_config(self) -> RiskEngineConfig:
        """Project risk settings into RiskEngineConfig."""
        base = default_risk_engine_config()
        engine = self.risk.engine
        limits = self.risk.user_limits
        return replace(
            base,
            kill_switch_active=self.risk.kill_switch_default_active,
            short_circuit_on_failure=engine.short_circuit_on_failure,
            strict_correlation_match=engine.strict_correlation_match,
            strict_decision_integrity=engine.strict_decision_integrity,
            strict_portfolio_fingerprint=engine.strict_portfolio_fingerprint,
            require_sizing_hint_in_live=engine.require_sizing_hint_in_live,
            reject_unknown_margin=engine.reject_unknown_margin,
            reject_unknown_capital=engine.reject_unknown_capital,
            skip_review_in_analysis=engine.skip_review_in_analysis,
            allow_invalid_signal_in_analysis=engine.allow_invalid_signal_in_analysis,
            deterministic_fingerprint=engine.deterministic_fingerprint,
            apply_confidence_risk_multiplier=engine.apply_confidence_risk_multiplier,
            medium_confidence_multiplier=limits.medium_confidence_multiplier,
            analysis_mode_limit_multiplier=engine.analysis_mode_limit_multiplier,
        )

    def to_default_user_risk_profile(self) -> UserRiskProfile:
        """Project user limits into UserRiskProfile."""
        limits = self.risk.user_limits
        profile_id = self.account.user_id or "profile-moderate-default"
        return UserRiskProfile(
            profile_id=profile_id,
            profile_tier=RiskProfileTier.MODERATE,
            max_risk_per_trade_pct=limits.max_risk_per_trade_pct,
            max_daily_loss_pct=limits.max_daily_loss_pct,
            max_drawdown_pct=limits.max_drawdown_pct,
            max_open_positions=limits.max_open_positions,
            max_consecutive_losses=limits.max_consecutive_losses,
            expiry_day_multiplier=limits.expiry_risk_multiplier,
            caution_multiplier=limits.caution_risk_multiplier,
            metadata=MappingProxyType(dict(self.risk.metadata)),
        )

    def to_execution_engine_config(self) -> ExecutionEngineConfig:
        """Project execution settings into ExecutionEngineConfig."""
        base = default_execution_engine_config()
        engine = self.execution.engine
        slippage = SlippagePolicy(max_slippage_bps=self.execution.slippage_bps_default)
        return replace(
            base,
            default_slippage_policy=slippage,
            require_contract_selection_in_live=engine.require_contract_selection_in_live,
            allow_structure_hint_heuristics=engine.allow_structure_hint_heuristics,
            require_sizing_hint_in_live=engine.require_sizing_hint_in_live,
            allow_market_orders_live=engine.allow_market_orders_live,
            split_quantity_equally_across_legs=engine.split_quantity_equally_across_legs,
            short_circuit_on_failure=engine.short_circuit_on_failure,
            strict_correlation=engine.strict_correlation,
            strict_output_validation=engine.strict_output_validation,
            deterministic_fingerprint=engine.deterministic_fingerprint,
            skip_planning_in_analysis=engine.skip_planning_in_analysis,
            allow_invalid_signal_in_analysis=engine.allow_invalid_signal_in_analysis,
            abort_on_leg_failure=engine.abort_on_leg_failure,
            metadata=MappingProxyType(dict(self.execution.metadata)),
        )

    def to_order_manager_config(self) -> OrderManagerConfig:
        """Project order manager settings into OrderManagerConfig."""
        om = self.execution.order_manager
        poll_ms = max(1, int(om.poll_interval_seconds * 1000))
        return OrderManagerConfig(
            strict_output_validation=om.strict_output_validation,
            publish_lifecycle_events=om.publish_lifecycle_events,
            max_poll_attempts=om.max_poll_attempts,
            poll_interval_ms=poll_ms,
            strict_correlation=self.orchestrator.strict_correlation,
            deterministic_fingerprint=self.orchestrator.deterministic_fingerprint,
        )

    def to_position_manager_config(self) -> PositionManagerConfig:
        """Project position settings into PositionManagerConfig."""
        return PositionManagerConfig(
            strict_correlation=self.position.strict_correlation,
            publish_lifecycle_events=self.position.publish_lifecycle_events,
            price_hint_max_age_seconds=self.position.price_hint_max_age_seconds,
            deterministic_fingerprint=self.position.deterministic_fingerprint,
            strict_output_validation=self.position.strict_output_validation,
        )

    def to_portfolio_manager_config(self) -> PortfolioManagerConfig:
        """Project portfolio settings into PortfolioManagerConfig."""
        return PortfolioManagerConfig(
            require_account_hints=self.portfolio.require_account_hints,
            track_peak_equity=self.portfolio.track_peak_equity,
            margin_hint_max_age_seconds=self.portfolio.margin_hint_max_age_seconds,
            strict_correlation=self.portfolio.strict_correlation,
            deterministic_fingerprint=self.portfolio.deterministic_fingerprint,
            publish_lifecycle_events=self.portfolio.publish_lifecycle_events,
            max_open_positions=self.risk.user_limits.max_open_positions,
        )

    def to_apme_config(self) -> APMEConfig:
        """Project APME settings into APMEConfig."""
        return APMEConfig(
            decision_cooldown_seconds=self.apme.decision_cooldown_seconds,
            enable_portfolio_protection=self.apme.enable_portfolio_protection,
            drawdown_halt_threshold_pct=self.apme.drawdown_halt_threshold_pct,
            drawdown_reduce_threshold_pct=self.apme.drawdown_reduce_threshold_pct,
            strict_correlation=self.apme.strict_correlation,
            deterministic_fingerprint=self.apme.deterministic_fingerprint,
            publish_lifecycle_events=self.apme.publish_lifecycle_events,
        )

    def redact_for_export(self) -> dict[str, object]:
        """Return redacted configuration suitable for export/logging."""
        return redact_for_logging(self)


@dataclass
class ConfigurationDraft:
    """Mutable intermediate configuration state during load pipeline."""

    profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT
    execution_mode: StrategyExecutionMode = StrategyExecutionMode.ANALYSIS
    account: AccountConfiguration = field(default_factory=AccountConfiguration)
    logging: LoggingConfiguration = field(default_factory=LoggingConfiguration)
    broker: BrokerConfiguration = field(default_factory=BrokerConfiguration)
    market_data: MarketDataConfiguration = field(default_factory=MarketDataConfiguration)
    strategy: StrategyConfiguration = field(default_factory=StrategyConfiguration)
    risk: RiskConfiguration = field(default_factory=RiskConfiguration)
    execution: ExecutionConfiguration = field(default_factory=ExecutionConfiguration)
    orchestrator: OrchestratorConfiguration = field(default_factory=OrchestratorConfiguration)
    event_bus: EventBusConfiguration = field(default_factory=EventBusConfiguration)
    position: PositionConfiguration = field(default_factory=PositionConfiguration)
    portfolio: PortfolioConfiguration = field(default_factory=PortfolioConfiguration)
    apme: APMEConfiguration = field(default_factory=APMEConfiguration)
    dashboard: DashboardConfiguration = field(default_factory=DashboardConfiguration)
    features: FeatureFlags = field(default_factory=FeatureFlags)
    secrets: SecretReferences = field(default_factory=SecretReferences)
    paths: PathConfiguration = field(default_factory=PathConfiguration)
    metadata: dict[str, str] = field(default_factory=dict)
    warnings: list[ConfigurationValidationIssue] = field(default_factory=list)


class EnvironmentSecretProvider:
    """Read secrets from environment variable locators."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env if env is not None else os.environ

    def is_available(self, ref: SecretReference) -> bool:
        """Return True when the environment variable is set."""
        if ref.source is not SecretSource.ENVIRONMENT:
            return False
        return bool(self._env.get(ref.locator, "").strip())

    def get_secret(self, ref: SecretReference) -> str:
        """Return secret value from environment."""
        if ref.source is not SecretSource.ENVIRONMENT:
            raise SecretResolutionError(
                f"EnvironmentSecretProvider cannot resolve source {ref.source.value}.",
                code="CONFIG.SECRET.RESOLUTION_FAILED",
                field=ref.ref_id,
            )
        value = self._env.get(ref.locator, "").strip()
        if not value:
            raise SecretResolutionError(
                f"Secret {ref.ref_id} not found in environment variable {ref.locator}.",
                code="CONFIG.SECRET.NOT_FOUND",
                field=ref.ref_id,
            )
        return value


class FileSecretProvider:
    """Read secrets from file paths."""

    def __init__(
        self,
        *,
        profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT,
    ) -> None:
        self._profile = profile

    def is_available(self, ref: SecretReference) -> bool:
        """Return True when the secret file exists."""
        if ref.source is not SecretSource.FILE:
            return False
        return Path(ref.locator).is_file()

    def get_secret(self, ref: SecretReference) -> str:
        """Return secret value read from file."""
        if ref.source is not SecretSource.FILE:
            raise SecretResolutionError(
                f"FileSecretProvider cannot resolve source {ref.source.value}.",
                code="CONFIG.SECRET.RESOLUTION_FAILED",
                field=ref.ref_id,
            )
        path = Path(ref.locator)
        if not path.is_file():
            raise SecretResolutionError(
                f"Secret file for {ref.ref_id} not found: {ref.locator}.",
                code="CONFIG.SECRET.NOT_FOUND",
                field=ref.ref_id,
            )
        if self._profile is EnvironmentProfile.PRODUCTION:
            mode = path.stat().st_mode
            if mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
                raise SecretResolutionError(
                    f"Secret file {ref.locator} must have mode 0600 in production.",
                    code="CONFIG.SECRET.PERMISSION_DENIED",
                    field=ref.ref_id,
                )
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise SecretResolutionError(
                f"Secret file for {ref.ref_id} is empty.",
                code="CONFIG.SECRET.NOT_FOUND",
                field=ref.ref_id,
            )
        return value


class InlineSecretProvider:
    """Test-only dict-backed secret provider."""

    def __init__(self, secrets: Mapping[str, str]) -> None:
        self._secrets = dict(secrets)

    def is_available(self, ref: SecretReference) -> bool:
        """Return True when ref_id is present in inline store."""
        return ref.ref_id in self._secrets and bool(self._secrets[ref.ref_id])

    def get_secret(self, ref: SecretReference) -> str:
        """Return inline secret value."""
        if ref.ref_id not in self._secrets:
            raise SecretResolutionError(
                f"Inline secret {ref.ref_id} not found.",
                code="CONFIG.SECRET.NOT_FOUND",
                field=ref.ref_id,
            )
        value = self._secrets[ref.ref_id].strip()
        if not value:
            raise SecretResolutionError(
                f"Inline secret {ref.ref_id} is empty.",
                code="CONFIG.SECRET.NOT_FOUND",
                field=ref.ref_id,
            )
        return value


class CompositeSecretProvider:
    """Chain secret providers by SecretSource type."""

    def __init__(self, providers: Mapping[SecretSource, SecretProvider]) -> None:
        self._providers = dict(providers)

    def is_available(self, ref: SecretReference) -> bool:
        """Return True when any matching provider reports availability."""
        provider = self._providers.get(ref.source)
        if provider is None:
            return False
        return provider.is_available(ref)

    def get_secret(self, ref: SecretReference) -> str:
        """Resolve secret using the provider matching ref.source."""
        provider = self._providers.get(ref.source)
        if provider is None:
            raise SecretResolutionError(
                f"No provider registered for source {ref.source.value}.",
                code="CONFIG.SECRET.RESOLUTION_FAILED",
                field=ref.ref_id,
            )
        return provider.get_secret(ref)


def _utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def _is_timezone_aware(value: datetime) -> bool:
    """Return True when datetime carries timezone information."""
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _canonical_json(payload: Mapping[str, object]) -> str:
    """Serialize mapping to canonical JSON."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _isoformat_utc(value: datetime) -> str:
    """Serialize datetime as ISO-8601 UTC with Z suffix."""
    if not _is_timezone_aware(value):
        raise ApplicationConfigurationError(
            "datetime must be timezone-aware.",
            code="CONFIG.VALIDATION.FIELD_INVALID",
            field="datetime",
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_datetime(value: str) -> datetime:
    """Parse ISO datetime string."""
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _parse_bool(value: str, *, field: str) -> bool:
    """Parse boolean environment variable value."""
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ApplicationConfigurationError(
        f"Invalid boolean value for {field}: {value!r}.",
        code="CONFIG.ENV.INVALID_BOOLEAN",
        field=field,
    )


def _parse_float(value: str, *, field: str) -> float:
    """Parse float environment variable value."""
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ApplicationConfigurationError(
            f"Invalid numeric value for {field}: {value!r}.",
            code="CONFIG.ENV.INVALID_NUMBER",
            field=field,
        ) from exc


def _parse_int(value: str, *, field: str) -> int:
    """Parse integer environment variable value."""
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ApplicationConfigurationError(
            f"Invalid numeric value for {field}: {value!r}.",
            code="CONFIG.ENV.INVALID_NUMBER",
            field=field,
        ) from exc


def _validate_log_level(level: str, *, field: str) -> None:
    """Validate Python log level name."""
    if level.upper() not in _VALID_LOG_LEVELS:
        raise ApplicationConfigurationError(
            f"Invalid log level {level!r}.",
            code="CONFIG.LOGGING.INVALID_LEVEL",
            field=field,
        )


def resolve_environment_profile(
    *,
    explicit: EnvironmentProfile | None = None,
    env: Mapping[str, str] | None = None,
) -> EnvironmentProfile:
    """Resolve profile from explicit argument or THETA_PROFILE env var."""
    if explicit is not None:
        return explicit
    mapping = env if env is not None else os.environ
    raw = mapping.get("THETA_PROFILE", "").strip().lower()
    if not raw:
        return EnvironmentProfile.DEVELOPMENT
    try:
        return EnvironmentProfile(raw)
    except ValueError as exc:
        raise ApplicationConfigurationError(
            f"Unknown environment profile: {raw!r}.",
            code="CONFIG.PROFILE.INVALID",
            field="profile",
        ) from exc


def default_load_options_for_profile(profile: EnvironmentProfile) -> LoadOptions:
    """Return default LoadOptions for a profile."""
    return LoadOptions(
        profile=profile,
        allow_missing_secrets=profile is EnvironmentProfile.DEVELOPMENT,
        allow_missing_config_file=profile is not EnvironmentProfile.PRODUCTION,
        strict_unknown_env_vars=profile is EnvironmentProfile.PRODUCTION,
    )


def _default_secret_refs(profile: EnvironmentProfile) -> dict[str, SecretReference]:
    """Build default secret reference map."""
    required = profile is EnvironmentProfile.PRODUCTION
    return {
        "broker.api_key": SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.ENVIRONMENT,
            locator="THETA_BROKER_API_KEY",
            required=required,
        ),
        "broker.api_secret": SecretReference(
            ref_id="broker.api_secret",
            source=SecretSource.ENVIRONMENT,
            locator="THETA_BROKER_API_SECRET",
            required=required,
        ),
        "broker.access_token": SecretReference(
            ref_id="broker.access_token",
            source=SecretSource.ENVIRONMENT,
            locator="THETA_BROKER_ACCESS_TOKEN",
            required=False,
        ),
        "dashboard.auth_token": SecretReference(
            ref_id="dashboard.auth_token",
            source=SecretSource.ENVIRONMENT,
            locator="THETA_DASHBOARD_AUTH_TOKEN",
            required=False,
        ),
    }


def _apply_base_defaults(draft: ConfigurationDraft) -> None:
    """Apply platform base defaults."""
    draft.secrets = SecretReferences(refs=MappingProxyType(_default_secret_refs(draft.profile)))


def _apply_profile_defaults(draft: ConfigurationDraft, profile: EnvironmentProfile) -> None:
    """Apply profile-specific default overrides."""
    draft.profile = profile
    draft.secrets = SecretReferences(refs=MappingProxyType(_default_secret_refs(profile)))

    if profile is EnvironmentProfile.DEVELOPMENT:
        draft.execution_mode = StrategyExecutionMode.ANALYSIS
        draft.logging = LoggingConfiguration(
            root_level="DEBUG",
            platform_level="DEBUG",
            engine_level="INFO",
            broker_level="WARNING",
            format=LogFormat.TEXT,
        )
        draft.broker = BrokerConfiguration(
            broker_type=BrokerType.MOCK,
            paper_trading=False,
        )
        draft.orchestrator = OrchestratorConfiguration(
            enable_event_driven_cycles=False,
            strict_correlation=False,
            fail_fast_on_engine_error=True,
            deterministic_fingerprint=True,
        )
        draft.portfolio = PortfolioConfiguration(require_account_hints=False)
        draft.position = PositionConfiguration(strict_correlation=False)
        draft.dashboard = DashboardConfiguration(
            enabled=True,
            auth_mode=DashboardAuthMode.NONE,
        )
        draft.features = FeatureFlags(
            paper_broker_simulation=False,
            event_driven_cycles_enabled=False,
            dashboard_enabled=True,
        )
        draft.event_bus = EventBusConfiguration(allow_clear=True)
        draft.risk = RiskConfiguration(
            engine=RiskEnginePolicyOverrides(
                reject_unknown_margin=False,
                reject_unknown_capital=False,
                skip_review_in_analysis=True,
                allow_invalid_signal_in_analysis=True,
            )
        )
        draft.execution = ExecutionConfiguration(
            engine=ExecutionEnginePolicyOverrides(
                skip_planning_in_analysis=True,
                allow_invalid_signal_in_analysis=True,
            )
        )
        return

    if profile is EnvironmentProfile.PAPER:
        draft.execution_mode = StrategyExecutionMode.LIVE
        draft.logging = LoggingConfiguration(
            root_level="INFO",
            platform_level="INFO",
            engine_level="INFO",
            broker_level="INFO",
            format=LogFormat.JSON,
            log_file_path="logs/theta.log",
        )
        draft.broker = BrokerConfiguration(
            broker_type=BrokerType.MOCK,
            paper_trading=True,
        )
        draft.orchestrator = OrchestratorConfiguration(
            enable_event_driven_cycles=True,
            strict_correlation=True,
            fail_fast_on_engine_error=False,
            deterministic_fingerprint=True,
        )
        draft.portfolio = PortfolioConfiguration(require_account_hints=True)
        draft.dashboard = DashboardConfiguration(
            enabled=True,
            auth_mode=DashboardAuthMode.TOKEN,
            auth_token_secret_ref="dashboard.auth_token",
        )
        draft.features = FeatureFlags(
            paper_broker_simulation=True,
            event_driven_cycles_enabled=True,
            dashboard_enabled=True,
        )
        draft.event_bus = EventBusConfiguration(allow_clear=False)
        return

    # PRODUCTION
    draft.execution_mode = StrategyExecutionMode.LIVE
    draft.logging = LoggingConfiguration(
        root_level="INFO",
        platform_level="WARNING",
        engine_level="INFO",
        broker_level="WARNING",
        format=LogFormat.JSON,
        log_file_path="logs/theta.log",
    )
    draft.broker = BrokerConfiguration(
        broker_type=BrokerType.ZERODHA_KITE,
        paper_trading=False,
    )
    draft.orchestrator = OrchestratorConfiguration(
        enable_event_driven_cycles=True,
        strict_correlation=True,
        fail_fast_on_engine_error=False,
        block_pre_trade_in_degraded=True,
        deterministic_fingerprint=True,
    )
    draft.portfolio = PortfolioConfiguration(require_account_hints=True)
    draft.dashboard = DashboardConfiguration(
        enabled=False,
        auth_mode=DashboardAuthMode.TOKEN,
        auth_token_secret_ref="dashboard.auth_token",
    )
    draft.features = FeatureFlags(
        paper_broker_simulation=False,
        event_driven_cycles_enabled=True,
        dashboard_enabled=False,
    )
    draft.event_bus = EventBusConfiguration(allow_clear=False)
    draft.risk = RiskConfiguration(
        engine=RiskEnginePolicyOverrides(
            reject_unknown_margin=True,
            reject_unknown_capital=True,
        )
    )


def _deep_merge_dict(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-merge overlay mapping into base dict."""
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _replace_dataclass(current: _T, updates: Mapping[str, Any]) -> _T:
    """Return copy of frozen dataclass with field updates."""
    valid = {f.name for f in fields(current)}
    filtered = {k: v for k, v in updates.items() if k in valid}
    return replace(current, **filtered)


def _apply_section_updates(draft: ConfigurationDraft, data: Mapping[str, Any]) -> None:
    """Apply nested mapping updates onto configuration draft sections."""
    if "profile" in data:
        raw = str(data["profile"]).strip().lower()
        draft.profile = EnvironmentProfile(raw)

    if "execution_mode" in data:
        draft.execution_mode = StrategyExecutionMode(str(data["execution_mode"]).lower())

    section_map: dict[str, tuple[Any, type]] = {
        "account": (draft.account, AccountConfiguration),
        "logging": (draft.logging, LoggingConfiguration),
        "broker": (draft.broker, BrokerConfiguration),
        "market_data": (draft.market_data, MarketDataConfiguration),
        "strategy": (draft.strategy, StrategyConfiguration),
        "risk": (draft.risk, RiskConfiguration),
        "execution": (draft.execution, ExecutionConfiguration),
        "orchestrator": (draft.orchestrator, OrchestratorConfiguration),
        "event_bus": (draft.event_bus, EventBusConfiguration),
        "position": (draft.position, PositionConfiguration),
        "portfolio": (draft.portfolio, PortfolioConfiguration),
        "apme": (draft.apme, APMEConfiguration),
        "dashboard": (draft.dashboard, DashboardConfiguration),
        "features": (draft.features, FeatureFlags),
        "paths": (draft.paths, PathConfiguration),
    }

    for section_name, (current, cls) in section_map.items():
        if section_name not in data:
            continue
        section_data = data[section_name]
        if not isinstance(section_data, Mapping):
            continue
        updates = dict(section_data)
        if section_name == "logging" and "format" in updates:
            updates["format"] = LogFormat(str(updates["format"]).lower())
        if section_name == "broker":
            if "broker_type" in updates:
                updates["broker_type"] = BrokerType(str(updates["broker_type"]).lower())
        if section_name == "dashboard" and "auth_mode" in updates:
            updates["auth_mode"] = DashboardAuthMode(str(updates["auth_mode"]).lower())
        if section_name == "strategy":
            if "enabled_strategy_ids" in updates:
                updates["enabled_strategy_ids"] = frozenset(updates["enabled_strategy_ids"])
            if "disabled_strategy_ids" in updates:
                updates["disabled_strategy_ids"] = frozenset(updates["disabled_strategy_ids"])
        if section_name == "risk" and isinstance(section_data, Mapping):
            risk_updates: dict[str, Any] = {}
            if "user_limits" in section_data:
                risk_updates["user_limits"] = _replace_dataclass(
                    draft.risk.user_limits,
                    section_data["user_limits"],
                )
            if "engine" in section_data:
                risk_updates["engine"] = _replace_dataclass(
                    draft.risk.engine,
                    section_data["engine"],
                )
            if "budget" in section_data:
                risk_updates["budget"] = _replace_dataclass(
                    draft.risk.budget,
                    section_data["budget"],
                )
            for key, value in section_data.items():
                if key not in {"user_limits", "engine", "budget"}:
                    risk_updates[key] = value
            draft.risk = _replace_dataclass(draft.risk, risk_updates)
            continue
        if section_name == "execution" and isinstance(section_data, Mapping):
            exec_updates: dict[str, Any] = {}
            if "engine" in section_data:
                exec_updates["engine"] = _replace_dataclass(
                    draft.execution.engine,
                    section_data["engine"],
                )
            if "order_manager" in section_data:
                exec_updates["order_manager"] = _replace_dataclass(
                    draft.execution.order_manager,
                    section_data["order_manager"],
                )
            for key, value in section_data.items():
                if key not in {"engine", "order_manager"}:
                    exec_updates[key] = value
            draft.execution = _replace_dataclass(draft.execution, exec_updates)
            continue
        setattr(draft, section_name, _replace_dataclass(current, updates))

    if "metadata" in data and isinstance(data["metadata"], Mapping):
        draft.metadata.update({str(k): str(v) for k, v in data["metadata"].items()})


def _load_config_file(path: str) -> dict[str, Any]:
    """Load YAML or JSON configuration file."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ApplicationConfigurationError(
            f"Configuration file not found: {path}.",
            code="CONFIG.FILE.NOT_FOUND",
            field="config_file_path",
        )
    extension = file_path.suffix.lower()
    content = file_path.read_text(encoding="utf-8")
    if extension == ".json":
        try:
            loaded = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ApplicationConfigurationError(
                f"Failed to parse JSON config file: {path}.",
                code="CONFIG.FILE.PARSE_ERROR",
                field="config_file_path",
            ) from exc
        return loaded if isinstance(loaded, dict) else {}
    if extension in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ApplicationConfigurationError(
                "PyYAML is required to load YAML configuration files.",
                code="CONFIG.FILE.PARSE_ERROR",
                field="config_file_path",
            ) from exc
        try:
            loaded = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ApplicationConfigurationError(
                f"Failed to parse YAML config file: {path}.",
                code="CONFIG.FILE.PARSE_ERROR",
                field="config_file_path",
            ) from exc
        return loaded if isinstance(loaded, dict) else {}
    raise ApplicationConfigurationError(
        f"Unsupported configuration file format: {extension}.",
        code="CONFIG.FILE.UNSUPPORTED_FORMAT",
        field="config_file_path",
    )


def _merge_legacy_user_config(draft: ConfigurationDraft, path: str) -> None:
    """Merge legacy user_config.json into draft."""
    file_path = Path(path)
    if not file_path.is_file():
        return
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        draft.warnings.append(
            ConfigurationValidationIssue(
                code="CONFIG.FILE.PARSE_ERROR",
                message=f"Failed to parse legacy user config: {path}.",
                field="user_config_path",
                section="legacy",
                severity="WARNING",
            )
        )
        return
    if not isinstance(data, dict):
        return

    _LOGGER.debug("config.legacy.merged", extra={"path": path})

    risk_section = data.get("risk")
    if isinstance(risk_section, dict):
        mapping = {
            "max_risk_per_trade_pct": risk_section.get("max_risk_per_trade_pct"),
            "max_daily_loss_pct": risk_section.get("max_daily_loss_pct"),
            "max_drawdown_pct": risk_section.get("max_account_drawdown_pct"),
            "max_consecutive_losses": risk_section.get("max_consecutive_losses"),
            "max_open_positions": risk_section.get("max_open_positions"),
            "caution_risk_multiplier": risk_section.get("caution_risk_multiplier"),
            "expiry_risk_multiplier": risk_section.get("expiry_risk_multiplier"),
            "medium_confidence_multiplier": risk_section.get("medium_confidence_multiplier"),
            "minimum_risk_multiplier": risk_section.get("minimum_risk_multiplier"),
        }
        filtered = {k: v for k, v in mapping.items() if v is not None}
        draft.risk = replace(
            draft.risk,
            user_limits=_replace_dataclass(draft.risk.user_limits, filtered),
        )

    budget_section = data.get("risk_budget")
    if isinstance(budget_section, dict):
        mapping = {
            "allocation_mode": budget_section.get("allocation_mode"),
            "max_trades_per_day": budget_section.get("max_trades_per_day"),
            "confidence_scaling_enabled": budget_section.get("confidence_scaling_enabled"),
            "minimum_setup_score": budget_section.get("minimum_setup_score"),
            "max_single_trade_daily_risk_pct": budget_section.get(
                "max_single_trade_daily_risk_pct"
            ),
        }
        filtered = {k: v for k, v in mapping.items() if v is not None}
        draft.risk = replace(
            draft.risk,
            budget=_replace_dataclass(draft.risk.budget, filtered),
        )

    trading_section = data.get("trading")
    if isinstance(trading_section, dict):
        feature_updates = {
            "trading_enabled": trading_section.get("trading_enabled"),
            "new_entries_enabled": trading_section.get("new_entries_enabled"),
            "expiry_trading_enabled": trading_section.get("expiry_trading_enabled"),
        }
        filtered = {k: v for k, v in feature_updates.items() if v is not None}
        draft.features = _replace_dataclass(draft.features, filtered)

    signal_section = data.get("signal")
    if isinstance(signal_section, dict):
        signal_updates = {
            "allow_caution_signals": signal_section.get("allow_caution_signals"),
            "minimum_confidence_band": signal_section.get("minimum_confidence"),
        }
        filtered = {k: v for k, v in signal_updates.items() if v is not None}
        draft.features = _replace_dataclass(draft.features, filtered)

    system_section = data.get("system")
    if isinstance(system_section, dict):
        env_hint = system_section.get("environment")
        if isinstance(env_hint, str) and env_hint.upper() == "PAPER":
            draft.warnings.append(
                ConfigurationValidationIssue(
                    code="CONFIG.LEGACY.PROFILE_HINT",
                    message="Legacy system.environment=PAPER profile hint ignored; use THETA_PROFILE.",
                    section="legacy",
                    severity="WARNING",
                )
            )


def _apply_environment_overrides(
    draft: ConfigurationDraft,
    env: Mapping[str, str],
    *,
    profile: EnvironmentProfile,
    strict_unknown: bool,
) -> None:
    """Apply THETA_* environment variables to configuration draft."""
    for key, value in env.items():
        if not key.startswith("THETA_"):
            continue
        if key not in _KNOWN_ENV_VARS:
            issue = ConfigurationValidationIssue(
                code="CONFIG.ENV.UNKNOWN_VARIABLE",
                message=f"Unknown THETA environment variable: {key}.",
                field=key,
                section="environment",
                severity="ERROR" if strict_unknown else "WARNING",
            )
            if strict_unknown:
                raise ApplicationConfigurationError(
                    issue.message,
                    code=issue.code,
                    field=issue.field,
                )
            draft.warnings.append(issue)

    if "THETA_ACCOUNT_ID" in env:
        draft.account = replace(draft.account, account_id=env["THETA_ACCOUNT_ID"].strip())
    if "THETA_USER_ID" in env:
        draft.account = replace(draft.account, user_id=env["THETA_USER_ID"].strip())
    if "THETA_TIMEZONE" in env:
        draft.account = replace(draft.account, timezone=env["THETA_TIMEZONE"].strip())

    if "THETA_LOG_LEVEL" in env:
        level = env["THETA_LOG_LEVEL"].strip().upper()
        _validate_log_level(level, field="logging.root_level")
        draft.logging = replace(draft.logging, root_level=level)
    if "THETA_LOG_FORMAT" in env:
        draft.logging = replace(
            draft.logging,
            format=LogFormat(env["THETA_LOG_FORMAT"].strip().lower()),
        )
    if "THETA_LOG_FILE" in env:
        draft.logging = replace(draft.logging, log_file_path=env["THETA_LOG_FILE"].strip())

    if "THETA_BROKER_TYPE" in env:
        draft.broker = replace(
            draft.broker,
            broker_type=BrokerType(env["THETA_BROKER_TYPE"].strip().lower()),
        )

    if "THETA_MARKET_UNDERLYING" in env:
        draft.market_data = replace(
            draft.market_data,
            underlying=env["THETA_MARKET_UNDERLYING"].strip(),
        )
    if "THETA_MARKET_STRIKES_EACH_SIDE" in env:
        draft.market_data = replace(
            draft.market_data,
            strikes_each_side=_parse_int(
                env["THETA_MARKET_STRIKES_EACH_SIDE"],
                field="market_data.strikes_each_side",
            ),
        )

    if "THETA_TRADING_ENABLED" in env:
        draft.features = replace(
            draft.features,
            trading_enabled=_parse_bool(env["THETA_TRADING_ENABLED"], field="THETA_TRADING_ENABLED"),
        )
    if "THETA_NEW_ENTRIES_ENABLED" in env:
        draft.features = replace(
            draft.features,
            new_entries_enabled=_parse_bool(
                env["THETA_NEW_ENTRIES_ENABLED"],
                field="THETA_NEW_ENTRIES_ENABLED",
            ),
        )

    if "THETA_RISK_MAX_PER_TRADE_PCT" in env:
        draft.risk = replace(
            draft.risk,
            user_limits=replace(
                draft.risk.user_limits,
                max_risk_per_trade_pct=_parse_float(
                    env["THETA_RISK_MAX_PER_TRADE_PCT"],
                    field="risk.user_limits.max_risk_per_trade_pct",
                ),
            ),
        )
    if "THETA_RISK_MAX_DAILY_LOSS_PCT" in env:
        draft.risk = replace(
            draft.risk,
            user_limits=replace(
                draft.risk.user_limits,
                max_daily_loss_pct=_parse_float(
                    env["THETA_RISK_MAX_DAILY_LOSS_PCT"],
                    field="risk.user_limits.max_daily_loss_pct",
                ),
            ),
        )
    if "THETA_RISK_MAX_DRAWDOWN_PCT" in env:
        draft.risk = replace(
            draft.risk,
            user_limits=replace(
                draft.risk.user_limits,
                max_drawdown_pct=_parse_float(
                    env["THETA_RISK_MAX_DRAWDOWN_PCT"],
                    field="risk.user_limits.max_drawdown_pct",
                ),
            ),
        )

    if "THETA_DASHBOARD_ENABLED" in env:
        enabled = _parse_bool(env["THETA_DASHBOARD_ENABLED"], field="THETA_DASHBOARD_ENABLED")
        draft.dashboard = replace(draft.dashboard, enabled=enabled)
        draft.features = replace(draft.features, dashboard_enabled=enabled)
    if "THETA_DASHBOARD_PORT" in env:
        draft.dashboard = replace(
            draft.dashboard,
            port=_parse_int(env["THETA_DASHBOARD_PORT"], field="dashboard.port"),
        )

    if "THETA_EXECUTION_MODE" in env:
        mode = StrategyExecutionMode(env["THETA_EXECUTION_MODE"].strip().lower())
        if profile is EnvironmentProfile.PRODUCTION and mode is not StrategyExecutionMode.LIVE:
            allow = _parse_bool(
                env.get("THETA_ALLOW_ANALYSIS_IN_PRODUCTION", "false"),
                field="THETA_ALLOW_ANALYSIS_IN_PRODUCTION",
            )
            if not allow:
                raise ApplicationConfigurationError(
                    "THETA_EXECUTION_MODE override forbidden in production without "
                    "THETA_ALLOW_ANALYSIS_IN_PRODUCTION=true.",
                    code="CONFIG.VALIDATION.PROFILE_GUARDRAIL",
                    field="execution_mode",
                )
        draft.execution_mode = mode

    if "THETA_STRATEGY_PLUGIN_DIR" in env:
        path = env["THETA_STRATEGY_PLUGIN_DIR"].strip()
        draft.strategy = replace(draft.strategy, registry_plugin_dir=path)
        draft.paths = replace(draft.paths, strategy_plugin_dir=path)

    if "THETA_DETERMINISTIC_FINGERPRINT" in env:
        deterministic = _parse_bool(
            env["THETA_DETERMINISTIC_FINGERPRINT"],
            field="THETA_DETERMINISTIC_FINGERPRINT",
        )
        draft.orchestrator = replace(
            draft.orchestrator,
            deterministic_fingerprint=deterministic,
        )

    if profile is EnvironmentProfile.PRODUCTION:
        allow_mock = _parse_bool(
            env.get("THETA_ALLOW_MOCK_BROKER_IN_PRODUCTION", "false"),
            field="THETA_ALLOW_MOCK_BROKER_IN_PRODUCTION",
        )
        if draft.broker.broker_type is BrokerType.MOCK and not allow_mock:
            raise ApplicationConfigurationError(
                "BrokerType.MOCK forbidden in production without "
                "THETA_ALLOW_MOCK_BROKER_IN_PRODUCTION=true.",
                code="CONFIG.VALIDATION.PROFILE_GUARDRAIL",
                field="broker.broker_type",
            )

    _LOGGER.debug("config.env.applied", extra={"profile": profile.value})


def _apply_cli_overrides(draft: ConfigurationDraft, overrides: Mapping[str, str]) -> None:
    """Apply dotted-path CLI overrides onto draft."""
    for path, raw_value in overrides.items():
        parts = path.split(".")
        if len(parts) < 2:
            continue
        section = parts[0]
        field_name = parts[1]
        payload = {section: {field_name: raw_value}}
        if raw_value.lower() in {"true", "false"}:
            payload[section][field_name] = _parse_bool(raw_value, field=path)
        else:
            try:
                if "." in raw_value:
                    payload[section][field_name] = float(raw_value)
                else:
                    payload[section][field_name] = int(raw_value)
            except ValueError:
                payload[section][field_name] = raw_value
        _apply_section_updates(draft, payload)


def _resolve_secret_references(
    draft: ConfigurationDraft,
    provider: SecretProvider,
    *,
    allow_missing: bool,
) -> None:
    """Validate secret references via provider without storing values."""
    for ref_name, ref in draft.secrets.refs.items():
        if provider.is_available(ref):
            try:
                provider.get_secret(ref)
                _LOGGER.debug(
                    "config.secret.resolved",
                    extra={"ref_id": ref.ref_id},
                )
            except SecretResolutionError:
                if ref.required and not allow_missing:
                    raise
                draft.warnings.append(
                    ConfigurationValidationIssue(
                        code="CONFIG.SECRET.RESOLUTION_FAILED",
                        message=f"Failed to resolve secret {ref_name}.",
                        field=ref_name,
                        section="secrets",
                        severity="WARNING",
                    )
                )
        elif ref.required and not allow_missing:
            _LOGGER.warning(
                "config.secret.missing",
                extra={"ref_id": ref.ref_id},
            )
            raise SecretResolutionError(
                f"Required secret {ref_name} is not available.",
                code="CONFIG.SECRET.NOT_FOUND",
                field=ref_name,
            )
        else:
            draft.warnings.append(
                ConfigurationValidationIssue(
                    code="CONFIG.SECRET.NOT_FOUND",
                    message=f"Optional secret {ref_name} is not available.",
                    field=ref_name,
                    section="secrets",
                    severity="WARNING",
                )
            )


def validate_application_configuration(
    draft: ConfigurationDraft,
    *,
    profile: EnvironmentProfile,
) -> ApplicationConfigurationValidationResult:
    """Validate draft configuration without loading secrets."""
    errors: list[ConfigurationValidationIssue] = []
    warnings: list[ConfigurationValidationIssue] = list(draft.warnings)

    for level_field, level_value in (
        ("logging.root_level", draft.logging.root_level),
        ("logging.platform_level", draft.logging.platform_level),
        ("logging.engine_level", draft.logging.engine_level),
        ("logging.broker_level", draft.logging.broker_level),
    ):
        if level_value.upper() not in _VALID_LOG_LEVELS:
            errors.append(
                ConfigurationValidationIssue(
                    code="CONFIG.LOGGING.INVALID_LEVEL",
                    message=f"Invalid log level: {level_value}.",
                    field=level_field,
                    section="logging",
                )
            )

    if draft.broker.connect_timeout_seconds <= 0 or draft.broker.request_timeout_seconds <= 0:
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.BROKER.INVALID_TIMEOUT",
                message="Broker timeouts must be positive.",
                field="broker.connect_timeout_seconds",
                section="broker",
            )
        )

    if not (1 <= draft.market_data.strikes_each_side <= 50):
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.MARKET_DATA.INVALID_STRIKES",
                message="strikes_each_side must be in [1, 50].",
                field="market_data.strikes_each_side",
                section="market_data",
            )
        )

    limits = draft.risk.user_limits
    if not (0.1 <= limits.max_risk_per_trade_pct <= 5.0):
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.RISK.INVALID_LIMIT",
                message="max_risk_per_trade_pct must be in [0.1, 5.0].",
                field="risk.user_limits.max_risk_per_trade_pct",
                section="risk",
            )
        )

    if draft.orchestrator.cycle_timeout_seconds < 1:
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.ORCHESTRATOR.INVALID_TIMEOUT",
                message="cycle_timeout_seconds must be >= 1.",
                field="orchestrator.cycle_timeout_seconds",
                section="orchestrator",
            )
        )

    if not (1 <= draft.dashboard.port <= 65535):
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.DASHBOARD.INVALID_PORT",
                message="dashboard.port must be in [1, 65535].",
                field="dashboard.port",
                section="dashboard",
            )
        )

    if draft.execution.slippage_bps_default < 0:
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.EXECUTION.INVALID_SLIPPAGE",
                message="slippage_bps_default must be >= 0.",
                field="execution.slippage_bps_default",
                section="execution",
            )
        )

    # Cross-section invariants
    if limits.max_daily_loss_pct > limits.max_drawdown_pct:
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.VALIDATION.CROSS_SECTION",
                message="max_daily_loss_pct must be <= max_drawdown_pct.",
                field="risk.user_limits.max_daily_loss_pct",
                section="risk",
            )
        )

    if limits.expiry_risk_multiplier < limits.minimum_risk_multiplier:
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.VALIDATION.CROSS_SECTION",
                message="expiry_risk_multiplier must be >= minimum_risk_multiplier.",
                field="risk.user_limits.expiry_risk_multiplier",
                section="risk",
            )
        )

    if draft.features.paper_broker_simulation and profile is not EnvironmentProfile.PAPER:
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.VALIDATION.CROSS_SECTION",
                message="paper_broker_simulation is only valid in PAPER profile.",
                field="features.paper_broker_simulation",
                section="features",
            )
        )

    if (
        draft.broker.paper_trading
        and profile is EnvironmentProfile.PRODUCTION
        and draft.broker.broker_type is not BrokerType.MOCK
    ):
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.VALIDATION.CROSS_SECTION",
                message="paper_trading inconsistent with production live broker.",
                field="broker.paper_trading",
                section="broker",
            )
        )

    if draft.orchestrator.enable_post_fill_cycle and not draft.features.post_fill_apme_enabled:
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.VALIDATION.CROSS_SECTION",
                message="post_fill cycle requires post_fill_apme_enabled.",
                field="features.post_fill_apme_enabled",
                section="features",
            )
        )

    coverage = draft.market_data.minimum_publish_coverage_ratio
    if not (0.0 < coverage <= 1.0):
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.VALIDATION.CROSS_SECTION",
                message="minimum_publish_coverage_ratio must be in (0, 1].",
                field="market_data.minimum_publish_coverage_ratio",
                section="market_data",
            )
        )

    enabled = draft.strategy.enabled_strategy_ids
    disabled = draft.strategy.disabled_strategy_ids
    if enabled & disabled:
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.VALIDATION.CROSS_SECTION",
                message="enabled_strategy_ids and disabled_strategy_ids must be disjoint.",
                field="strategy.enabled_strategy_ids",
                section="strategy",
            )
        )

    if not draft.features.trading_enabled and draft.features.new_entries_enabled:
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.VALIDATION.CROSS_SECTION",
                message="new_entries_enabled requires trading_enabled.",
                field="features.new_entries_enabled",
                section="features",
            )
        )

    if draft.dashboard.enabled != draft.features.dashboard_enabled:
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.VALIDATION.CROSS_SECTION",
                message="dashboard.enabled must agree with features.dashboard_enabled.",
                field="dashboard.enabled",
                section="dashboard",
            )
        )

    if draft.execution.max_legs_per_plan < 1:
        errors.append(
            ConfigurationValidationIssue(
                code="CONFIG.VALIDATION.CROSS_SECTION",
                message="max_legs_per_plan must be >= 1.",
                field="execution.max_legs_per_plan",
                section="execution",
            )
        )

    # Profile guardrails
    if profile is EnvironmentProfile.PRODUCTION:
        if not draft.account.account_id.strip():
            errors.append(
                ConfigurationValidationIssue(
                    code="CONFIG.VALIDATION.PROFILE_GUARDRAIL",
                    message="account_id is required in production profile.",
                    field="account.account_id",
                    section="account",
                )
            )
        if (
            draft.dashboard.enabled
            and draft.dashboard.auth_mode is DashboardAuthMode.NONE
        ):
            errors.append(
                ConfigurationValidationIssue(
                    code="CONFIG.VALIDATION.PROFILE_GUARDRAIL",
                    message="Dashboard auth NONE forbidden when dashboard enabled in production.",
                    field="dashboard.auth_mode",
                    section="dashboard",
                )
            )
        if (
            draft.dashboard.enabled
            and draft.dashboard.auth_mode is DashboardAuthMode.TOKEN
            and not draft.dashboard.auth_token_secret_ref
        ):
            errors.append(
                ConfigurationValidationIssue(
                    code="CONFIG.VALIDATION.PROFILE_GUARDRAIL",
                    message="auth_token_secret_ref required for TOKEN auth in production.",
                    field="dashboard.auth_token_secret_ref",
                    section="dashboard",
                )
            )

    if profile is EnvironmentProfile.PAPER:
        if draft.broker.broker_type is BrokerType.ZERODHA_KITE and not draft.broker.paper_trading:
            errors.append(
                ConfigurationValidationIssue(
                    code="CONFIG.VALIDATION.PROFILE_GUARDRAIL",
                    message="Live broker forbidden in PAPER profile by default.",
                    field="broker.broker_type",
                    section="broker",
                )
            )

    return ApplicationConfigurationValidationResult(
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _validate_projections(config: ApplicationConfiguration) -> None:
    """Ensure engine config projections are valid."""
    orchestrator_result = validate_orchestrator_config(config.to_orchestrator_config())
    if not orchestrator_result.is_valid:
        raise ApplicationConfigurationError(
            "Orchestrator projection failed validation.",
            code="CONFIG.PROJECTION.FAILED",
            field="orchestrator",
        )
    try:
        config.to_event_bus_policy()
        config.to_market_data_engine_config()
        config.to_strategy_evaluation_engine_config()
        config.to_strategy_registry_config()
        config.to_trade_decision_engine_config()
        config.to_risk_engine_config()
        config.to_default_user_risk_profile()
        config.to_execution_engine_config()
        config.to_order_manager_config()
        config.to_position_manager_config()
        config.to_portfolio_manager_config()
        config.to_apme_config()
    except Exception as exc:
        raise ApplicationConfigurationError(
            f"Engine config projection failed: {exc}.",
            code="CONFIG.PROJECTION.FAILED",
        ) from exc


def _fingerprint_payload(
    *,
    schema_version: str,
    profile: EnvironmentProfile,
    execution_mode: StrategyExecutionMode,
    account_id: str,
    broker_type: BrokerType,
    user_limits: UserRiskLimits,
    features: FeatureFlags,
    orchestrator: OrchestratorConfiguration,
) -> dict[str, object]:
    """Build salient configuration payload for fingerprint hashing."""
    return {
        "schema_version": schema_version,
        "profile": profile.value,
        "execution_mode": execution_mode.value,
        "account_id": account_id,
        "broker_type": broker_type.value,
        "risk_limits": {
            "max_risk_per_trade_pct": user_limits.max_risk_per_trade_pct,
            "max_daily_loss_pct": user_limits.max_daily_loss_pct,
            "max_drawdown_pct": user_limits.max_drawdown_pct,
            "max_open_positions": user_limits.max_open_positions,
        },
        "feature_flags": {
            "trading_enabled": features.trading_enabled,
            "new_entries_enabled": features.new_entries_enabled,
            "post_fill_apme_enabled": features.post_fill_apme_enabled,
            "event_driven_cycles_enabled": features.event_driven_cycles_enabled,
            "dashboard_enabled": features.dashboard_enabled,
            "paper_broker_simulation": features.paper_broker_simulation,
        },
        "orchestrator": {
            "enable_pre_trade_cycle": orchestrator.enable_pre_trade_cycle,
            "enable_post_fill_cycle": orchestrator.enable_post_fill_cycle,
            "enable_event_driven_cycles": orchestrator.enable_event_driven_cycles,
            "strict_correlation": orchestrator.strict_correlation,
            "deterministic_fingerprint": orchestrator.deterministic_fingerprint,
        },
    }


def _fingerprint_from_draft(draft: ConfigurationDraft) -> str:
    """Compute configuration fingerprint from draft state."""
    payload = _fingerprint_payload(
        schema_version=APPLICATION_CONFIG_SCHEMA_VERSION,
        profile=draft.profile,
        execution_mode=draft.execution_mode,
        account_id=draft.account.account_id,
        broker_type=draft.broker.broker_type,
        user_limits=draft.risk.user_limits,
        features=draft.features,
        orchestrator=draft.orchestrator,
    )
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _freeze_configuration(
    draft: ConfigurationDraft,
    *,
    loaded_at: datetime | None = None,
    config_id: str | None = None,
) -> ApplicationConfiguration:
    """Convert validated draft into immutable ApplicationConfiguration."""
    ts = loaded_at or _utc_now()
    cid = config_id or str(uuid.uuid4())
    fingerprint = _fingerprint_from_draft(draft)
    return ApplicationConfiguration(
        schema_version=APPLICATION_CONFIG_SCHEMA_VERSION,
        config_id=cid,
        config_fingerprint=fingerprint,
        loaded_at=ts,
        profile=draft.profile,
        execution_mode=draft.execution_mode,
        account=draft.account,
        logging=draft.logging,
        broker=draft.broker,
        market_data=draft.market_data,
        strategy=draft.strategy,
        risk=draft.risk,
        execution=draft.execution,
        orchestrator=draft.orchestrator,
        event_bus=draft.event_bus,
        position=draft.position,
        portfolio=draft.portfolio,
        apme=draft.apme,
        dashboard=draft.dashboard,
        features=draft.features,
        secrets=draft.secrets,
        paths=draft.paths,
        metadata=MappingProxyType(dict(draft.metadata)),
    )


def load_application_configuration(
    options: LoadOptions | None = None,
    *,
    secret_provider: SecretProvider | None = None,
    env: Mapping[str, str] | None = None,
) -> ApplicationConfiguration:
    """Load, merge, validate, and freeze application configuration.

    Args:
        options: Optional load options (profile, paths, overrides).
        secret_provider: Provider for secret resolution. Defaults to a
            composite of environment and file providers.
        env: Environment mapping; defaults to ``os.environ``.

    Returns:
        Immutable validated ApplicationConfiguration.

    Raises:
        ApplicationConfigurationError: On validation or resolution failure.
    """
    opts = options or LoadOptions()
    environment = env if env is not None else os.environ
    profile = resolve_environment_profile(explicit=opts.profile, env=environment)
    strict_unknown = (
        opts.strict_unknown_env_vars
        if opts.strict_unknown_env_vars is not None
        else profile is EnvironmentProfile.PRODUCTION
    )

    _LOGGER.info("config.load.start", extra={"profile": profile.value})

    draft = ConfigurationDraft()
    _apply_base_defaults(draft)
    _apply_profile_defaults(draft, profile)

    config_path = opts.config_file_path or environment.get("THETA_CONFIG_FILE", "").strip()
    explicit_config_path = bool(opts.config_file_path or environment.get("THETA_CONFIG_FILE", "").strip())
    if not config_path:
        for candidate in (DEFAULT_CONFIG_PATH, "config/application.json"):
            if Path(candidate).is_file():
                config_path = candidate
                break

    if config_path:
        if Path(config_path).is_file():
            file_data = _load_config_file(config_path)
            _apply_section_updates(draft, file_data)
            _LOGGER.debug("config.file.merged", extra={"path": config_path})
        elif explicit_config_path or (
            profile is EnvironmentProfile.PRODUCTION and not opts.allow_missing_config_file
        ):
            raise ApplicationConfigurationError(
                f"Configuration file not found: {config_path}.",
                code="CONFIG.FILE.NOT_FOUND",
                field="config_file_path",
            )

    user_config_path = (
        opts.user_config_path
        or environment.get("THETA_USER_CONFIG_PATH", "").strip()
        or DEFAULT_LEGACY_USER_CONFIG_PATH
    )
    _merge_legacy_user_config(draft, user_config_path)

    _apply_environment_overrides(
        draft,
        environment,
        profile=profile,
        strict_unknown=strict_unknown,
    )
    if opts.cli_overrides:
        _apply_cli_overrides(draft, opts.cli_overrides)

    if secret_provider is None:
        secret_provider = CompositeSecretProvider(
            {
                SecretSource.ENVIRONMENT: EnvironmentSecretProvider(environment),
                SecretSource.FILE: FileSecretProvider(profile=profile),
                SecretSource.INLINE_FOR_TESTS: InlineSecretProvider({}),
            }
        )

    if (
        profile is EnvironmentProfile.PRODUCTION
        and draft.broker.broker_type is BrokerType.ZERODHA_KITE
        and not opts.allow_missing_secrets
    ):
        _resolve_secret_references(
            draft,
            secret_provider,
            allow_missing=False,
        )
    elif not opts.allow_missing_secrets:
        _resolve_secret_references(
            draft,
            secret_provider,
            allow_missing=False,
        )
    else:
        _resolve_secret_references(
            draft,
            secret_provider,
            allow_missing=True,
        )

    validation = validate_application_configuration(draft, profile=draft.profile)
    for warning in validation.warnings:
        _LOGGER.warning(
            "config.validation.warning",
            extra={"code": warning.code, "field": warning.field},
        )
    if not validation.is_valid:
        first = validation.errors[0]
        raise ApplicationConfigurationError(
            first.message,
            code=first.code,
            field=first.field,
        )

    config = _freeze_configuration(draft)
    _validate_projections(config)

    _LOGGER.info(
        "config.load.complete",
        extra={
            "profile": config.profile.value,
            "fingerprint": config.config_fingerprint,
        },
    )
    return config


def compute_config_fingerprint(config: ApplicationConfiguration) -> str:
    """Compute SHA-256 over canonical JSON of redacted salient configuration."""
    payload = _fingerprint_payload(
        schema_version=config.schema_version,
        profile=config.profile,
        execution_mode=config.execution_mode,
        account_id=config.account.account_id,
        broker_type=config.broker.broker_type,
        user_limits=config.risk.user_limits,
        features=config.features,
        orchestrator=config.orchestrator,
    )
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def redact_for_logging(config: ApplicationConfiguration) -> dict[str, object]:
    """Return redacted configuration dict safe for logs and export."""
    secret_refs = {
        name: {
            "ref_id": ref.ref_id,
            "source": ref.source.value,
            "locator": "[REDACTED]",
            "required": ref.required,
        }
        for name, ref in config.secrets.refs.items()
    }
    return {
        "schema_version": config.schema_version,
        "config_id": config.config_id,
        "profile": config.profile.value,
        "execution_mode": config.execution_mode.value,
        "loaded_at": _isoformat_utc(config.loaded_at),
        "account": {
            "account_id": config.account.account_id,
            "user_id": config.account.user_id,
            "timezone": config.account.timezone,
        },
        "logging": {
            "root_level": config.logging.root_level,
            "format": config.logging.format.value,
        },
        "broker": {
            "broker_id": config.broker.broker_id,
            "broker_type": config.broker.broker_type.value,
            "paper_trading": config.broker.paper_trading,
            "api_key_secret_ref": config.broker.api_key_secret_ref,
        },
        "features": {
            "trading_enabled": config.features.trading_enabled,
            "new_entries_enabled": config.features.new_entries_enabled,
            "dashboard_enabled": config.features.dashboard_enabled,
        },
        "secrets": secret_refs,
        "metadata": dict(sorted(config.metadata.items())),
    }


def serialize_application_configuration(config: ApplicationConfiguration) -> str:
    """Serialize redacted application configuration to JSON."""
    export = redact_for_logging(config)
    export["config_fingerprint"] = config.config_fingerprint
    return json.dumps(export, sort_keys=True, indent=2)


def deserialize_application_configuration(payload: str) -> ApplicationConfiguration:
    """Deserialize redacted application configuration JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ApplicationConfigurationError(
            "Malformed configuration JSON payload.",
            code="CONFIG.SERIALIZATION.MALFORMED",
        ) from exc
    if not isinstance(data, dict):
        raise ApplicationConfigurationError(
            "Configuration payload must be a JSON object.",
            code="CONFIG.SERIALIZATION.MALFORMED",
        )
    schema = data.get("schema_version", "")
    if schema != APPLICATION_CONFIG_SCHEMA_VERSION:
        raise ApplicationConfigurationError(
            f"Unsupported schema version: {schema!r}.",
            code="CONFIG.SERIALIZATION.UNSUPPORTED_VERSION",
            field="schema_version",
        )

    draft = ConfigurationDraft()
    draft.profile = EnvironmentProfile(str(data.get("profile", "development")).lower())
    draft.execution_mode = StrategyExecutionMode(
        str(data.get("execution_mode", draft.execution_mode.value)).lower()
    )
    account_data = data.get("account", {})
    if isinstance(account_data, dict):
        draft.account = AccountConfiguration(
            account_id=str(account_data.get("account_id", "")),
            user_id=str(account_data.get("user_id", "")),
            timezone=str(account_data.get("timezone", "Asia/Kolkata")),
        )
    logging_data = data.get("logging", {})
    if isinstance(logging_data, dict):
        draft.logging = LoggingConfiguration(
            root_level=str(logging_data.get("root_level", "INFO")),
            format=LogFormat(str(logging_data.get("format", "text")).lower()),
        )
    broker_data = data.get("broker", {})
    if isinstance(broker_data, dict):
        draft.broker = BrokerConfiguration(
            broker_id=str(broker_data.get("broker_id", "default")),
            broker_type=BrokerType(str(broker_data.get("broker_type", "mock")).lower()),
            paper_trading=bool(broker_data.get("paper_trading", False)),
            api_key_secret_ref=str(broker_data.get("api_key_secret_ref", "broker.api_key")),
        )
    features_data = data.get("features", {})
    if isinstance(features_data, dict):
        draft.features = FeatureFlags(
            trading_enabled=bool(features_data.get("trading_enabled", True)),
            new_entries_enabled=bool(features_data.get("new_entries_enabled", True)),
            dashboard_enabled=bool(features_data.get("dashboard_enabled", True)),
        )
    draft.dashboard = replace(
        draft.dashboard,
        enabled=draft.features.dashboard_enabled,
    )
    metadata = data.get("metadata", {})
    if isinstance(metadata, dict):
        draft.metadata = {str(k): str(v) for k, v in metadata.items()}

    loaded_at_raw = data.get("loaded_at")
    loaded_at = _parse_iso_datetime(str(loaded_at_raw)) if loaded_at_raw else _utc_now()
    config_id = str(data.get("config_id", str(uuid.uuid4())))
    fingerprint = str(data.get("config_fingerprint", ""))

    _apply_profile_defaults(draft, draft.profile)
    validation = validate_application_configuration(draft, profile=draft.profile)
    if not validation.is_valid:
        first = validation.errors[0]
        raise ApplicationConfigurationError(
            first.message,
            code=first.code,
            field=first.field,
        )

    config = _freeze_configuration(draft, loaded_at=loaded_at, config_id=config_id)
    return config


def apply_logging_configuration(config: LoggingConfiguration) -> None:
    """Configure platform logging from LoggingConfiguration."""
    _validate_log_level(config.root_level, field="logging.root_level")
    _validate_log_level(config.platform_level, field="logging.platform_level")
    _validate_log_level(config.engine_level, field="logging.engine_level")
    _validate_log_level(config.broker_level, field="logging.broker_level")

    root = logging.getLogger()
    root.setLevel(config.root_level.upper())

    formatter: logging.Formatter
    if config.format is LogFormat.JSON:
        formatter = logging.Formatter(
            '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
            '"message":"%(message)s"}'
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        )

    if not root.handlers:
        handler: logging.Handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setFormatter(formatter)

    if config.log_file_path:
        log_path = Path(config.log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            config.log_file_path,
            maxBytes=config.max_file_bytes,
            backupCount=config.backup_count,
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    logging.getLogger("theta").setLevel(config.platform_level.upper())
    logging.getLogger("theta.engines").setLevel(config.engine_level.upper())
    logging.getLogger("theta.broker").setLevel(config.broker_level.upper())


__all__ = [
    "APPLICATION_CONFIG_SCHEMA_VERSION",
    "APPLICATION_CONFIG_VERSION",
    "APMEConfiguration",
    "AccountConfiguration",
    "ApplicationConfiguration",
    "ApplicationConfigurationError",
    "ApplicationConfigurationValidationResult",
    "BrokerConfiguration",
    "BrokerType",
    "CompositeSecretProvider",
    "ConfigurationDraft",
    "ConfigurationLayer",
    "ConfigurationValidationIssue",
    "DashboardAuthMode",
    "DashboardConfiguration",
    "EnvironmentProfile",
    "EnvironmentSecretProvider",
    "EventBusConfiguration",
    "ExecutionConfiguration",
    "FeatureFlags",
    "FileSecretProvider",
    "InlineSecretProvider",
    "LoadOptions",
    "LogFormat",
    "LoggingConfiguration",
    "MarketDataConfiguration",
    "OrchestratorConfiguration",
    "PathConfiguration",
    "PortfolioConfiguration",
    "PositionConfiguration",
    "PRODUCER_NAME",
    "RiskConfiguration",
    "SecretProvider",
    "SecretReference",
    "SecretReferences",
    "SecretResolutionError",
    "SecretSource",
    "StrategyConfiguration",
    "apply_logging_configuration",
    "compute_config_fingerprint",
    "default_load_options_for_profile",
    "deserialize_application_configuration",
    "load_application_configuration",
    "redact_for_logging",
    "resolve_environment_profile",
    "serialize_application_configuration",
    "validate_application_configuration",
]
