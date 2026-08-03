"""Unit tests for config.application_configuration."""

from __future__ import annotations

import json
import logging
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import pytest

from config.application_configuration import (
    APPLICATION_CONFIG_SCHEMA_VERSION,
    ApplicationConfiguration,
    ApplicationConfigurationError,
    BrokerConfiguration,
    BrokerType,
    CompositeSecretProvider,
    ConfigurationDraft,
    DashboardAuthMode,
    EnvironmentProfile,
    EnvironmentSecretProvider,
    FileSecretProvider,
    InlineSecretProvider,
    LoadOptions,
    LogFormat,
    LoggingConfiguration,
    RiskConfiguration,
    SecretReference,
    SecretReferences,
    SecretResolutionError,
    SecretSource,
    UserRiskLimits,
    apply_logging_configuration,
    compute_config_fingerprint,
    default_load_options_for_profile,
    deserialize_application_configuration,
    load_application_configuration,
    redact_for_logging,
    resolve_environment_profile,
    serialize_application_configuration,
    validate_application_configuration,
)
from strategy.signals import StrategyExecutionMode


@pytest.fixture
def empty_secrets() -> InlineSecretProvider:
    """Inline secret provider with no secrets."""
    return InlineSecretProvider({})


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """Temporary directory for config file fixtures."""
    return tmp_path


def load_dev(**kwargs: object) -> ApplicationConfiguration:
    """Load development profile configuration for tests."""
    options = LoadOptions(profile=EnvironmentProfile.DEVELOPMENT, **kwargs)
    return load_application_configuration(
        options,
        secret_provider=InlineSecretProvider({}),
        env={},
    )


class TestProfileResolution:
    """Profile resolution tests."""

    def test_explicit_profile(self) -> None:
        profile = resolve_environment_profile(explicit=EnvironmentProfile.PAPER)
        assert profile is EnvironmentProfile.PAPER

    def test_env_profile(self) -> None:
        profile = resolve_environment_profile(env={"THETA_PROFILE": "production"})
        assert profile is EnvironmentProfile.PRODUCTION

    def test_default_development(self) -> None:
        profile = resolve_environment_profile(env={})
        assert profile is EnvironmentProfile.DEVELOPMENT

    def test_invalid_profile_raises(self) -> None:
        with pytest.raises(ApplicationConfigurationError) as exc:
            resolve_environment_profile(env={"THETA_PROFILE": "staging"})
        assert exc.value.code == "CONFIG.PROFILE.INVALID"


class TestDevelopmentLoad:
    """Development profile load tests."""

    def test_load_development_defaults(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_application_configuration(
            LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
            secret_provider=empty_secrets,
            env={},
        )
        assert config.profile is EnvironmentProfile.DEVELOPMENT
        assert config.execution_mode is StrategyExecutionMode.ANALYSIS
        assert config.broker.broker_type is BrokerType.MOCK
        assert config.logging.format is LogFormat.TEXT
        assert config.orchestrator.strict_correlation is False

    def test_fingerprint_is_stable(self, empty_secrets: InlineSecretProvider) -> None:
        env = {"THETA_ACCOUNT_ID": "acct-test"}
        opts = LoadOptions(profile=EnvironmentProfile.DEVELOPMENT)
        first = load_application_configuration(opts, secret_provider=empty_secrets, env=env)
        second = load_application_configuration(opts, secret_provider=empty_secrets, env=env)
        assert first.config_fingerprint == second.config_fingerprint

    def test_config_id_differs_across_loads(self, empty_secrets: InlineSecretProvider) -> None:
        opts = LoadOptions(profile=EnvironmentProfile.DEVELOPMENT)
        first = load_application_configuration(opts, secret_provider=empty_secrets, env={})
        second = load_application_configuration(opts, secret_provider=empty_secrets, env={})
        assert first.config_id != second.config_id


class TestPaperLoad:
    """Paper profile load tests."""

    def test_load_paper_profile(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_application_configuration(
            LoadOptions(profile=EnvironmentProfile.PAPER),
            secret_provider=empty_secrets,
            env={},
        )
        assert config.profile is EnvironmentProfile.PAPER
        assert config.execution_mode is StrategyExecutionMode.LIVE
        assert config.broker.paper_trading is True
        assert config.features.paper_broker_simulation is True
        assert config.logging.format is LogFormat.JSON


class TestProductionGuardrails:
    """Production profile guardrail tests."""

    def test_production_requires_account_id(self, empty_secrets: InlineSecretProvider) -> None:
        with pytest.raises(ApplicationConfigurationError) as exc:
            load_application_configuration(
                LoadOptions(
                    profile=EnvironmentProfile.PRODUCTION,
                    allow_missing_config_file=True,
                    allow_missing_secrets=True,
                ),
                secret_provider=empty_secrets,
                env={},
            )
        assert exc.value.code == "CONFIG.VALIDATION.PROFILE_GUARDRAIL"

    def test_production_mock_broker_forbidden(self, empty_secrets: InlineSecretProvider) -> None:
        with pytest.raises(ApplicationConfigurationError) as exc:
            load_application_configuration(
                LoadOptions(
                    profile=EnvironmentProfile.PRODUCTION,
                    allow_missing_config_file=True,
                    allow_missing_secrets=True,
                ),
                secret_provider=empty_secrets,
                env={
                    "THETA_ACCOUNT_ID": "AB1234",
                    "THETA_BROKER_TYPE": "mock",
                },
            )
        assert exc.value.code == "CONFIG.VALIDATION.PROFILE_GUARDRAIL"

    def test_production_mock_broker_escape_hatch(
        self,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        config = load_application_configuration(
            LoadOptions(
                profile=EnvironmentProfile.PRODUCTION,
                allow_missing_config_file=True,
                allow_missing_secrets=True,
            ),
            secret_provider=empty_secrets,
            env={
                "THETA_ACCOUNT_ID": "AB1234",
                "THETA_BROKER_TYPE": "mock",
                "THETA_ALLOW_MOCK_BROKER_IN_PRODUCTION": "true",
            },
        )
        assert config.broker.broker_type is BrokerType.MOCK

    def test_production_requires_secrets(self) -> None:
        with pytest.raises(SecretResolutionError) as exc:
            load_application_configuration(
                LoadOptions(
                    profile=EnvironmentProfile.PRODUCTION,
                    allow_missing_config_file=True,
                ),
                secret_provider=InlineSecretProvider({}),
                env={"THETA_ACCOUNT_ID": "AB1234"},
            )
        assert exc.value.code == "CONFIG.SECRET.NOT_FOUND"

    def test_production_with_secrets(self) -> None:
        secrets = InlineSecretProvider(
            {
                "broker.api_key": "key",
                "broker.api_secret": "secret",
            }
        )
        config = load_application_configuration(
            LoadOptions(
                profile=EnvironmentProfile.PRODUCTION,
                allow_missing_config_file=True,
            ),
            secret_provider=secrets,
            env={"THETA_ACCOUNT_ID": "AB1234"},
        )
        assert config.broker.broker_type is BrokerType.ZERODHA_KITE


class TestEnvironmentOverrides:
    """Environment variable parsing tests."""

    def test_boolean_parsing(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_application_configuration(
            LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
            secret_provider=empty_secrets,
            env={
                "THETA_TRADING_ENABLED": "false",
                "THETA_NEW_ENTRIES_ENABLED": "no",
            },
        )
        assert config.features.trading_enabled is False
        assert config.features.new_entries_enabled is False

    def test_invalid_boolean_raises(self, empty_secrets: InlineSecretProvider) -> None:
        with pytest.raises(ApplicationConfigurationError) as exc:
            load_application_configuration(
                LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
                secret_provider=empty_secrets,
                env={"THETA_TRADING_ENABLED": "maybe"},
            )
        assert exc.value.code == "CONFIG.ENV.INVALID_BOOLEAN"

    def test_numeric_overrides(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_application_configuration(
            LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
            secret_provider=empty_secrets,
            env={
                "THETA_RISK_MAX_PER_TRADE_PCT": "2.5",
                "THETA_MARKET_STRIKES_EACH_SIDE": "15",
                "THETA_DASHBOARD_PORT": "9090",
            },
        )
        assert config.risk.user_limits.max_risk_per_trade_pct == 2.5
        assert config.market_data.strikes_each_side == 15
        assert config.dashboard.port == 9090

    def test_invalid_number_raises(self, empty_secrets: InlineSecretProvider) -> None:
        with pytest.raises(ApplicationConfigurationError) as exc:
            load_application_configuration(
                LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
                secret_provider=empty_secrets,
                env={"THETA_DASHBOARD_PORT": "not-a-number"},
            )
        assert exc.value.code == "CONFIG.ENV.INVALID_NUMBER"

    def test_unknown_env_var_warning_in_development(
        self,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        config = load_application_configuration(
            LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
            secret_provider=empty_secrets,
            env={"THETA_UNKNOWN_SETTING": "value"},
        )
        assert config.profile is EnvironmentProfile.DEVELOPMENT

    def test_unknown_env_var_error_in_production(self, empty_secrets: InlineSecretProvider) -> None:
        with pytest.raises(ApplicationConfigurationError) as exc:
            load_application_configuration(
                LoadOptions(
                    profile=EnvironmentProfile.PRODUCTION,
                    allow_missing_config_file=True,
                    allow_missing_secrets=True,
                ),
                secret_provider=empty_secrets,
                env={
                    "THETA_ACCOUNT_ID": "AB1234",
                    "THETA_UNKNOWN_SETTING": "value",
                },
            )
        assert exc.value.code == "CONFIG.ENV.UNKNOWN_VARIABLE"

    def test_execution_mode_override(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_application_configuration(
            LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
            secret_provider=empty_secrets,
            env={"THETA_EXECUTION_MODE": "backtest"},
        )
        assert config.execution_mode is StrategyExecutionMode.BACKTEST


class TestConfigFileMerge:
    """Configuration file merge tests."""

    def test_merge_json_config_file(
        self,
        tmp_config_dir: Path,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        config_path = tmp_config_dir / "application.json"
        config_path.write_text(
            json.dumps(
                {
                    "account": {"account_id": "FILE-ACCT"},
                    "market_data": {"underlying": "BANKNIFTY"},
                    "features": {"trading_enabled": False, "new_entries_enabled": False},
                }
            ),
            encoding="utf-8",
        )
        config = load_application_configuration(
            LoadOptions(
                profile=EnvironmentProfile.DEVELOPMENT,
                config_file_path=str(config_path),
                user_config_path=str(tmp_config_dir / "missing-user-config.json"),
            ),
            secret_provider=empty_secrets,
            env={},
        )
        assert config.account.account_id == "FILE-ACCT"
        assert config.market_data.underlying == "BANKNIFTY"
        assert config.features.trading_enabled is False

    def test_missing_config_file_raises(
        self,
        tmp_config_dir: Path,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        with pytest.raises(ApplicationConfigurationError) as exc:
            load_application_configuration(
                LoadOptions(
                    profile=EnvironmentProfile.DEVELOPMENT,
                    config_file_path=str(tmp_config_dir / "missing.json"),
                ),
                secret_provider=empty_secrets,
                env={},
            )
        assert exc.value.code == "CONFIG.FILE.NOT_FOUND"

    def test_unsupported_format_raises(
        self,
        tmp_config_dir: Path,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        bad_path = tmp_config_dir / "application.toml"
        bad_path.write_text("profile = 'paper'\n", encoding="utf-8")
        with pytest.raises(ApplicationConfigurationError) as exc:
            load_application_configuration(
                LoadOptions(
                    profile=EnvironmentProfile.DEVELOPMENT,
                    config_file_path=str(bad_path),
                ),
                secret_provider=empty_secrets,
                env={},
            )
        assert exc.value.code == "CONFIG.FILE.UNSUPPORTED_FORMAT"

    def test_invalid_json_raises(
        self,
        tmp_config_dir: Path,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        bad_path = tmp_config_dir / "application.json"
        bad_path.write_text("{invalid", encoding="utf-8")
        with pytest.raises(ApplicationConfigurationError) as exc:
            load_application_configuration(
                LoadOptions(
                    profile=EnvironmentProfile.DEVELOPMENT,
                    config_file_path=str(bad_path),
                ),
                secret_provider=empty_secrets,
                env={},
            )
        assert exc.value.code == "CONFIG.FILE.PARSE_ERROR"


class TestLegacyMerge:
    """Legacy user_config.json merge tests."""

    def test_legacy_user_config_merge(
        self,
        tmp_config_dir: Path,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        legacy_path = tmp_config_dir / "user_config.json"
        legacy_path.write_text(
            json.dumps(
                {
                    "risk": {
                        "max_risk_per_trade_pct": 0.75,
                        "max_daily_loss_pct": 2.0,
                        "max_account_drawdown_pct": 8.0,
                    },
                    "trading": {"trading_enabled": True, "new_entries_enabled": False},
                    "signal": {"minimum_confidence": "HIGH", "allow_caution_signals": False},
                    "system": {"environment": "PAPER"},
                }
            ),
            encoding="utf-8",
        )
        config = load_application_configuration(
            LoadOptions(
                profile=EnvironmentProfile.DEVELOPMENT,
                user_config_path=str(legacy_path),
            ),
            secret_provider=empty_secrets,
            env={},
        )
        assert config.risk.user_limits.max_risk_per_trade_pct == 0.75
        assert config.risk.user_limits.max_drawdown_pct == 8.0
        assert config.features.new_entries_enabled is False
        assert config.features.minimum_confidence_band == "HIGH"


class TestValidation:
    """Validation pipeline tests."""

    def test_cross_section_daily_loss_vs_drawdown(self) -> None:
        draft = ConfigurationDraft()
        draft.risk = RiskConfiguration(
            user_limits=UserRiskLimits(max_daily_loss_pct=15.0, max_drawdown_pct=10.0)
        )
        result = validate_application_configuration(
            draft,
            profile=EnvironmentProfile.DEVELOPMENT,
        )
        assert not result.is_valid
        assert any(err.code == "CONFIG.VALIDATION.CROSS_SECTION" for err in result.errors)

    def test_paper_simulation_only_in_paper_profile(self) -> None:
        draft = ConfigurationDraft()
        draft.features = draft.features.__class__(paper_broker_simulation=True)
        result = validate_application_configuration(
            draft,
            profile=EnvironmentProfile.DEVELOPMENT,
        )
        assert not result.is_valid

    def test_trading_disabled_implies_new_entries_disabled(self) -> None:
        draft = ConfigurationDraft()
        draft.features = draft.features.__class__(
            trading_enabled=False,
            new_entries_enabled=True,
        )
        result = validate_application_configuration(
            draft,
            profile=EnvironmentProfile.DEVELOPMENT,
        )
        assert not result.is_valid

    def test_invalid_log_level_in_validation(self) -> None:
        draft = ConfigurationDraft()
        draft.logging = LoggingConfiguration(root_level="VERBOSE")
        result = validate_application_configuration(
            draft,
            profile=EnvironmentProfile.DEVELOPMENT,
        )
        assert any(err.code == "CONFIG.LOGGING.INVALID_LEVEL" for err in result.errors)

    def test_invalid_strikes(self) -> None:
        draft = ConfigurationDraft()
        draft.market_data = draft.market_data.__class__(strikes_each_side=100)
        result = validate_application_configuration(
            draft,
            profile=EnvironmentProfile.DEVELOPMENT,
        )
        assert any(err.code == "CONFIG.MARKET_DATA.INVALID_STRIKES" for err in result.errors)


class TestSecretProviders:
    """Secret provider tests."""

    def test_environment_secret_provider(self) -> None:
        provider = EnvironmentSecretProvider({"THETA_BROKER_API_KEY": "secret-key"})
        ref = SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.ENVIRONMENT,
            locator="THETA_BROKER_API_KEY",
        )
        assert provider.is_available(ref)
        assert provider.get_secret(ref) == "secret-key"

    def test_environment_secret_missing(self) -> None:
        provider = EnvironmentSecretProvider({})
        ref = SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.ENVIRONMENT,
            locator="THETA_BROKER_API_KEY",
        )
        assert not provider.is_available(ref)
        with pytest.raises(SecretResolutionError):
            provider.get_secret(ref)

    def test_file_secret_provider(self, tmp_path: Path) -> None:
        secret_file = tmp_path / "api_key.txt"
        secret_file.write_text("file-secret\n", encoding="utf-8")
        provider = FileSecretProvider(profile=EnvironmentProfile.DEVELOPMENT)
        ref = SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.FILE,
            locator=str(secret_file),
        )
        assert provider.get_secret(ref) == "file-secret"

    def test_file_secret_production_permissions(self, tmp_path: Path) -> None:
        secret_file = tmp_path / "api_key.txt"
        secret_file.write_text("file-secret\n", encoding="utf-8")
        os.chmod(secret_file, stat.S_IRUSR | stat.S_IRGRP)
        provider = FileSecretProvider(profile=EnvironmentProfile.PRODUCTION)
        ref = SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.FILE,
            locator=str(secret_file),
        )
        with pytest.raises(SecretResolutionError) as exc:
            provider.get_secret(ref)
        assert exc.value.code == "CONFIG.SECRET.PERMISSION_DENIED"

    def test_composite_secret_provider(self) -> None:
        provider = CompositeSecretProvider(
            {
                SecretSource.ENVIRONMENT: EnvironmentSecretProvider(
                    {"THETA_BROKER_API_KEY": "from-env"}
                ),
                SecretSource.INLINE_FOR_TESTS: InlineSecretProvider(
                    {"broker.api_secret": "inline"}
                ),
            }
        )
        env_ref = SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.ENVIRONMENT,
            locator="THETA_BROKER_API_KEY",
        )
        inline_ref = SecretReference(
            ref_id="broker.api_secret",
            source=SecretSource.INLINE_FOR_TESTS,
            locator="ignored",
        )
        assert provider.get_secret(env_ref) == "from-env"
        assert provider.get_secret(inline_ref) == "inline"

    def test_inline_secret_empty_raises(self) -> None:
        provider = InlineSecretProvider({"broker.api_key": "  "})
        ref = SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.INLINE_FOR_TESTS,
            locator="broker.api_key",
        )
        with pytest.raises(SecretResolutionError):
            provider.get_secret(ref)


class TestProjections:
    """Engine config projection tests."""

    def test_all_projections_valid(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_application_configuration(
            LoadOptions(profile=EnvironmentProfile.PAPER),
            secret_provider=empty_secrets,
            env={"THETA_ACCOUNT_ID": "PAPER-1"},
        )
        orchestrator = config.to_orchestrator_config()
        assert orchestrator.account_id == "PAPER-1"
        assert config.to_event_bus_policy().dispatch_mode.value == "sync"
        assert config.to_market_data_engine_config().universe.underlying == "NIFTY"
        assert config.to_strategy_evaluation_engine_config().plugin_timeout_ms >= 1
        assert config.to_strategy_registry_config() is not None
        assert config.to_trade_decision_engine_config().strict_correlation is True
        assert config.to_risk_engine_config().deterministic_fingerprint is True
        profile = config.to_default_user_risk_profile()
        assert profile.max_risk_per_trade_pct == config.risk.user_limits.max_risk_per_trade_pct
        assert config.to_execution_engine_config().default_slippage_policy.max_slippage_bps >= 0
        assert config.to_order_manager_config().max_poll_attempts >= 1
        assert config.to_position_manager_config().strict_correlation is True
        assert config.to_portfolio_manager_config().require_account_hints is True
        assert config.to_apme_config().enable_portfolio_protection is True


class TestSerialization:
    """Serialization and fingerprint tests."""

    def test_serialize_deserialize_round_trip(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_application_configuration(
            LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
            secret_provider=empty_secrets,
            env={"THETA_ACCOUNT_ID": "SER-1"},
        )
        payload = serialize_application_configuration(config)
        restored = deserialize_application_configuration(payload)
        assert restored.schema_version == APPLICATION_CONFIG_SCHEMA_VERSION
        assert restored.account.account_id == "SER-1"
        assert restored.profile is EnvironmentProfile.DEVELOPMENT

    def test_redact_for_logging_hides_secret_locators(
        self,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        config = load_application_configuration(
            LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
            secret_provider=empty_secrets,
            env={},
        )
        redacted = redact_for_logging(config)
        secrets = redacted["secrets"]
        assert isinstance(secrets, dict)
        for entry in secrets.values():
            assert entry["locator"] == "[REDACTED]"

    def test_compute_config_fingerprint_matches(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_dev()
        assert compute_config_fingerprint(config) == config.config_fingerprint

    def test_deserialize_unsupported_schema(self) -> None:
        with pytest.raises(ApplicationConfigurationError) as exc:
            deserialize_application_configuration('{"schema_version":"9.9.9"}')
        assert exc.value.code == "CONFIG.SERIALIZATION.UNSUPPORTED_VERSION"

    def test_deserialize_malformed_json(self) -> None:
        with pytest.raises(ApplicationConfigurationError) as exc:
            deserialize_application_configuration("{bad json")
        assert exc.value.code == "CONFIG.SERIALIZATION.MALFORMED"


class TestLoggingApply:
    """Logging configuration application tests."""

    def test_apply_logging_configuration(self) -> None:
        apply_logging_configuration(
            LoggingConfiguration(
                root_level="WARNING",
                platform_level="INFO",
                engine_level="DEBUG",
                broker_level="ERROR",
                format=LogFormat.TEXT,
            )
        )
        assert logging.getLogger().level == logging.WARNING
        assert logging.getLogger("theta").level == logging.INFO

    def test_apply_logging_json_with_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "theta.log"
        apply_logging_configuration(
            LoggingConfiguration(
                root_level="INFO",
                format=LogFormat.JSON,
                log_file_path=str(log_file),
            )
        )
        assert log_file.parent.exists()


class TestThreadSafety:
    """Thread-safe read tests."""

    def test_concurrent_reads(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_dev()

        def read_config() -> str:
            return config.to_orchestrator_config().account_id

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: read_config(), range(32)))
        assert len(results) == 32


class TestLoadOptions:
    """LoadOptions helper tests."""

    def test_default_load_options_for_profile(self) -> None:
        prod_opts = default_load_options_for_profile(EnvironmentProfile.PRODUCTION)
        assert prod_opts.allow_missing_secrets is False
        assert prod_opts.allow_missing_config_file is False
        dev_opts = default_load_options_for_profile(EnvironmentProfile.DEVELOPMENT)
        assert dev_opts.allow_missing_secrets is True


class TestCliOverrides:
    """CLI override merge tests."""

    def test_cli_overrides_applied(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_application_configuration(
            LoadOptions(
                profile=EnvironmentProfile.DEVELOPMENT,
                cli_overrides=MappingProxyType({"dashboard.port": "7777"}),
            ),
            secret_provider=empty_secrets,
            env={},
        )
        assert config.dashboard.port == 7777


class TestErrorTaxonomy:
    """Stable error code tests."""

    def test_application_configuration_error_attributes(self) -> None:
        err = ApplicationConfigurationError("msg", code="CONFIG.TEST.CODE", field="test.field")
        assert err.code == "CONFIG.TEST.CODE"
        assert err.field == "test.field"


class TestImmutableConfiguration:
    """Immutability tests."""

    def test_frozen_dataclass(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_dev()
        with pytest.raises(Exception):
            config.profile = EnvironmentProfile.PRODUCTION  # type: ignore[misc]

    def test_loaded_at_timezone_aware(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_dev()
        assert config.loaded_at.tzinfo is not None


class TestStrategyDisjointSets:
    """Strategy enabled/disabled disjoint validation."""

    def test_overlapping_strategy_sets_invalid(self) -> None:
        draft = ConfigurationDraft()
        draft.strategy = draft.strategy.__class__(
            enabled_strategy_ids=frozenset({"s1"}),
            disabled_strategy_ids=frozenset({"s1"}),
        )
        result = validate_application_configuration(
            draft,
            profile=EnvironmentProfile.DEVELOPMENT,
        )
        assert not result.is_valid


class TestDashboardGuardrails:
    """Dashboard configuration guardrails."""

    def test_dashboard_enabled_mismatch(self) -> None:
        draft = ConfigurationDraft()
        draft.dashboard = draft.dashboard.__class__(enabled=True)
        draft.features = draft.features.__class__(dashboard_enabled=False)
        result = validate_application_configuration(
            draft,
            profile=EnvironmentProfile.DEVELOPMENT,
        )
        assert not result.is_valid


class TestProductionAnalysisMode:
    """Production execution mode guardrail."""

    def test_analysis_mode_forbidden_in_production(self, empty_secrets: InlineSecretProvider) -> None:
        with pytest.raises(ApplicationConfigurationError) as exc:
            load_application_configuration(
                LoadOptions(
                    profile=EnvironmentProfile.PRODUCTION,
                    allow_missing_config_file=True,
                    allow_missing_secrets=True,
                ),
                secret_provider=empty_secrets,
                env={
                    "THETA_ACCOUNT_ID": "AB1234",
                    "THETA_EXECUTION_MODE": "analysis",
                },
            )
        assert exc.value.code == "CONFIG.VALIDATION.PROFILE_GUARDRAIL"


class TestLegacyUserConfigParseWarning:
    """Legacy config parse warning path."""

    def test_invalid_legacy_json_adds_warning(
        self,
        tmp_path: Path,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        bad_legacy = tmp_path / "user_config.json"
        bad_legacy.write_text("{not valid", encoding="utf-8")
        config = load_application_configuration(
            LoadOptions(
                profile=EnvironmentProfile.DEVELOPMENT,
                user_config_path=str(bad_legacy),
            ),
            secret_provider=empty_secrets,
            env={},
        )
        assert config.profile is EnvironmentProfile.DEVELOPMENT


class TestSecretProviderWrongSource:
    """Secret provider source mismatch tests."""

    def test_environment_provider_rejects_file_source(self) -> None:
        provider = EnvironmentSecretProvider({})
        ref = SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.FILE,
            locator="/tmp/key",
        )
        with pytest.raises(SecretResolutionError):
            provider.get_secret(ref)

    def test_composite_missing_provider(self) -> None:
        provider = CompositeSecretProvider({})
        ref = SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.VAULT,
            locator="vault/path",
        )
        with pytest.raises(SecretResolutionError):
            provider.get_secret(ref)


class TestRedactExport:
    """ApplicationConfiguration.redact_for_export tests."""

    def test_redact_for_export(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_dev()
        exported = config.redact_for_export()
        assert exported["profile"] == EnvironmentProfile.DEVELOPMENT.value
        assert "secrets" in exported


class TestPaperLiveBrokerGuardrail:
    """Paper profile live broker guardrail."""

    def test_zerodha_without_paper_flag_invalid(self) -> None:
        draft = ConfigurationDraft(profile=EnvironmentProfile.PAPER)
        draft.broker = BrokerConfiguration(
            broker_type=BrokerType.ZERODHA_KITE,
            paper_trading=False,
        )
        draft.features = draft.features.__class__(paper_broker_simulation=True)
        result = validate_application_configuration(draft, profile=EnvironmentProfile.PAPER)
        assert not result.is_valid


class TestInvalidLogLevelOnApply:
    """apply_logging_configuration validation."""

    def test_invalid_log_level_raises(self) -> None:
        with pytest.raises(ApplicationConfigurationError) as exc:
            apply_logging_configuration(LoggingConfiguration(root_level="VERBOSE"))
        assert exc.value.code == "CONFIG.LOGGING.INVALID_LEVEL"


class TestConfigFileFromRepoLegacy:
    """Load using repository legacy user config when present."""

    def test_default_legacy_user_config_path(self, empty_secrets: InlineSecretProvider) -> None:
        legacy = Path("config/user_config.json")
        if not legacy.is_file():
            pytest.skip("Legacy user config not present")
        config = load_application_configuration(
            LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
            secret_provider=empty_secrets,
            env={},
        )
        assert config.risk.user_limits.max_risk_per_trade_pct == 1.0


class TestFingerprintDeterminismWithEnv:
    """Deterministic fingerprint with controlled environment."""

    def test_identical_env_produces_identical_fingerprint(
        self,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        env = {
            "THETA_ACCOUNT_ID": "deterministic-acct",
            "THETA_RISK_MAX_PER_TRADE_PCT": "1.5",
        }
        opts = LoadOptions(profile=EnvironmentProfile.PAPER)
        one = load_application_configuration(opts, secret_provider=empty_secrets, env=env)
        two = load_application_configuration(opts, secret_provider=empty_secrets, env=env)
        assert one.config_fingerprint == two.config_fingerprint


class TestRichConfigFileMerge:
    """Full configuration file section merge coverage."""

    def test_merge_all_sections(
        self,
        tmp_path: Path,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        config_path = tmp_path / "application.json"
        config_path.write_text(
            json.dumps(
                {
                    "profile": "paper",
                    "execution_mode": "live",
                    "account": {
                        "account_id": "RICH-1",
                        "user_id": "user-1",
                        "timezone": "Asia/Kolkata",
                    },
                    "logging": {"format": "json", "root_level": "INFO"},
                    "broker": {"broker_type": "mock", "paper_trading": True},
                    "market_data": {"underlying": "BANKNIFTY", "strikes_each_side": 8},
                    "strategy": {
                        "registry_plugin_dir": "/tmp/plugins",
                        "enabled_strategy_ids": ["s1"],
                        "disabled_strategy_ids": ["s2"],
                        "evaluation_timeout_seconds": 15.0,
                    },
                    "risk": {
                        "user_limits": {"max_risk_per_trade_pct": 1.2},
                        "engine": {"reject_unknown_margin": True},
                        "budget": {"max_trades_per_day": 5},
                        "kill_switch_default_active": True,
                    },
                    "execution": {
                        "slippage_bps_default": 25.0,
                        "engine": {"strict_correlation": False},
                        "order_manager": {"max_poll_attempts": 10},
                    },
                    "orchestrator": {"cycle_timeout_seconds": 90},
                    "event_bus": {"allow_clear": False},
                    "position": {"price_hint_max_age_seconds": 120},
                    "portfolio": {"require_account_hints": True},
                    "apme": {"decision_cooldown_seconds": 30},
                    "dashboard": {
                        "enabled": True,
                        "auth_mode": "token",
                        "port": 9001,
                    },
                    "features": {
                        "trading_enabled": True,
                        "new_entries_enabled": True,
                        "paper_broker_simulation": True,
                        "dashboard_enabled": True,
                    },
                    "paths": {"log_dir": "/tmp/logs"},
                    "metadata": {"source": "test-fixture"},
                }
            ),
            encoding="utf-8",
        )
        config = load_application_configuration(
            LoadOptions(
                config_file_path=str(config_path),
                user_config_path=str(tmp_path / "missing-user.json"),
            ),
            secret_provider=empty_secrets,
            env={},
        )
        assert config.profile is EnvironmentProfile.PAPER
        assert config.account.account_id == "RICH-1"
        assert config.strategy.enabled_strategy_ids == frozenset({"s1"})
        assert config.risk.engine.reject_unknown_margin is True
        assert config.execution.order_manager.max_poll_attempts == 10
        assert config.metadata["source"] == "test-fixture"


class TestFullEnvironmentCatalog:
    """Cover remaining THETA_* environment variable mappings."""

    def test_all_documented_env_vars(
        self,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        env = {
            "THETA_USER_ID": "uid-1",
            "THETA_TIMEZONE": "UTC",
            "THETA_LOG_LEVEL": "DEBUG",
            "THETA_LOG_FORMAT": "json",
            "THETA_LOG_FILE": "/tmp/theta.log",
            "THETA_BROKER_TYPE": "recording",
            "THETA_MARKET_UNDERLYING": "FINNIFTY",
            "THETA_MARKET_STRIKES_EACH_SIDE": "12",
            "THETA_RISK_MAX_DAILY_LOSS_PCT": "2.5",
            "THETA_RISK_MAX_DRAWDOWN_PCT": "9.0",
            "THETA_DASHBOARD_ENABLED": "true",
            "THETA_DASHBOARD_PORT": "8888",
            "THETA_STRATEGY_PLUGIN_DIR": "/custom/plugins",
            "THETA_DETERMINISTIC_FINGERPRINT": "false",
        }
        config = load_application_configuration(
            LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
            secret_provider=empty_secrets,
            env=env,
        )
        assert config.account.user_id == "uid-1"
        assert config.logging.format is LogFormat.JSON
        assert config.broker.broker_type is BrokerType.RECORDING
        assert config.market_data.underlying == "FINNIFTY"
        assert config.paths.strategy_plugin_dir == "/custom/plugins"
        assert config.orchestrator.deterministic_fingerprint is False


class TestAdditionalValidationPaths:
    """Additional validation error paths."""

    def test_invalid_broker_timeout(self) -> None:
        draft = ConfigurationDraft()
        draft.broker = BrokerConfiguration(connect_timeout_seconds=0.0)
        result = validate_application_configuration(draft, profile=EnvironmentProfile.DEVELOPMENT)
        assert any(err.code == "CONFIG.BROKER.INVALID_TIMEOUT" for err in result.errors)

    def test_invalid_orchestrator_timeout(self) -> None:
        draft = ConfigurationDraft()
        draft.orchestrator = draft.orchestrator.__class__(cycle_timeout_seconds=0)
        result = validate_application_configuration(draft, profile=EnvironmentProfile.DEVELOPMENT)
        assert any(err.code == "CONFIG.ORCHESTRATOR.INVALID_TIMEOUT" for err in result.errors)

    def test_invalid_slippage(self) -> None:
        draft = ConfigurationDraft()
        draft.execution = draft.execution.__class__(slippage_bps_default=-1.0)
        result = validate_application_configuration(draft, profile=EnvironmentProfile.DEVELOPMENT)
        assert any(err.code == "CONFIG.EXECUTION.INVALID_SLIPPAGE" for err in result.errors)

    def test_invalid_risk_limit(self) -> None:
        draft = ConfigurationDraft()
        draft.risk = RiskConfiguration(
            user_limits=UserRiskLimits(max_risk_per_trade_pct=10.0)
        )
        result = validate_application_configuration(draft, profile=EnvironmentProfile.DEVELOPMENT)
        assert any(err.code == "CONFIG.RISK.INVALID_LIMIT" for err in result.errors)

    def test_invalid_dashboard_port(self) -> None:
        draft = ConfigurationDraft()
        draft.dashboard = draft.dashboard.__class__(port=70000)
        result = validate_application_configuration(draft, profile=EnvironmentProfile.DEVELOPMENT)
        assert any(err.code == "CONFIG.DASHBOARD.INVALID_PORT" for err in result.errors)

    def test_post_fill_requires_apme(self) -> None:
        draft = ConfigurationDraft()
        draft.orchestrator = draft.orchestrator.__class__(enable_post_fill_cycle=True)
        draft.features = draft.features.__class__(post_fill_apme_enabled=False)
        result = validate_application_configuration(draft, profile=EnvironmentProfile.DEVELOPMENT)
        assert not result.is_valid

    def test_expiry_multiplier_below_minimum(self) -> None:
        draft = ConfigurationDraft()
        draft.risk = RiskConfiguration(
            user_limits=UserRiskLimits(
                expiry_risk_multiplier=0.1,
                minimum_risk_multiplier=0.25,
            )
        )
        result = validate_application_configuration(draft, profile=EnvironmentProfile.DEVELOPMENT)
        assert not result.is_valid

    def test_production_dashboard_none_auth(self) -> None:
        draft = ConfigurationDraft(profile=EnvironmentProfile.PRODUCTION)
        draft.account = draft.account.__class__(account_id="AB1234")
        draft.dashboard = draft.dashboard.__class__(
            enabled=True,
            auth_mode=DashboardAuthMode.NONE,
        )
        draft.features = draft.features.__class__(dashboard_enabled=True)
        result = validate_application_configuration(draft, profile=EnvironmentProfile.PRODUCTION)
        assert not result.is_valid

    def test_production_dashboard_token_missing_ref(self) -> None:
        draft = ConfigurationDraft(profile=EnvironmentProfile.PRODUCTION)
        draft.account = draft.account.__class__(account_id="AB1234")
        draft.dashboard = draft.dashboard.__class__(
            enabled=True,
            auth_mode=DashboardAuthMode.TOKEN,
            auth_token_secret_ref=None,
        )
        draft.features = draft.features.__class__(dashboard_enabled=True)
        result = validate_application_configuration(draft, profile=EnvironmentProfile.PRODUCTION)
        assert not result.is_valid

    def test_paper_trading_production_live_broker(self) -> None:
        draft = ConfigurationDraft(profile=EnvironmentProfile.PRODUCTION)
        draft.account = draft.account.__class__(account_id="AB1234")
        draft.broker = BrokerConfiguration(
            broker_type=BrokerType.ZERODHA_KITE,
            paper_trading=True,
        )
        result = validate_application_configuration(draft, profile=EnvironmentProfile.PRODUCTION)
        assert not result.is_valid


class TestSecretResolutionPaths:
    """Secret resolution edge cases."""

    def test_required_secret_resolution_failure(self) -> None:
        with pytest.raises(SecretResolutionError):
            load_application_configuration(
                LoadOptions(
                    profile=EnvironmentProfile.PRODUCTION,
                    allow_missing_config_file=True,
                    allow_missing_secrets=False,
                ),
                secret_provider=InlineSecretProvider({}),
                env={"THETA_ACCOUNT_ID": "AB1234"},
            )

    def test_file_secret_not_found(self, tmp_path: Path) -> None:
        provider = FileSecretProvider(profile=EnvironmentProfile.DEVELOPMENT)
        ref = SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.FILE,
            locator=str(tmp_path / "missing.txt"),
        )
        with pytest.raises(SecretResolutionError):
            provider.get_secret(ref)

    def test_file_secret_empty(self, tmp_path: Path) -> None:
        secret_file = tmp_path / "empty.txt"
        secret_file.write_text("   ", encoding="utf-8")
        provider = FileSecretProvider(profile=EnvironmentProfile.DEVELOPMENT)
        ref = SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.FILE,
            locator=str(secret_file),
        )
        with pytest.raises(SecretResolutionError):
            provider.get_secret(ref)

    def test_file_provider_rejects_environment_source(self) -> None:
        provider = FileSecretProvider(profile=EnvironmentProfile.DEVELOPMENT)
        ref = SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.ENVIRONMENT,
            locator="THETA_BROKER_API_KEY",
        )
        with pytest.raises(SecretResolutionError):
            provider.get_secret(ref)

    def test_inline_secret_missing_ref(self) -> None:
        provider = InlineSecretProvider({})
        ref = SecretReference(
            ref_id="broker.api_key",
            source=SecretSource.INLINE_FOR_TESTS,
            locator="broker.api_key",
        )
        with pytest.raises(SecretResolutionError):
            provider.get_secret(ref)


class TestProjectionFailure:
    """Projection validation failure path."""

    def test_projection_failure_raises(self, empty_secrets: InlineSecretProvider) -> None:
        with patch(
            "config.application_configuration.validate_orchestrator_config",
            return_value=type(
                "R",
                (),
                {"is_valid": False, "errors": ()},
            )(),
        ):
            with pytest.raises(ApplicationConfigurationError) as exc:
                load_application_configuration(
                    LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
                    secret_provider=empty_secrets,
                    env={},
                )
            assert exc.value.code == "CONFIG.PROJECTION.FAILED"


class TestApplicationConfigurationPostInit:
    """ApplicationConfiguration invariant tests."""

    def test_fingerprint_mismatch_raises(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_dev()
        with pytest.raises(ApplicationConfigurationError):
            ApplicationConfiguration(
                schema_version=config.schema_version,
                config_id=config.config_id,
                config_fingerprint="invalid-fingerprint",
                loaded_at=config.loaded_at,
                profile=config.profile,
                execution_mode=config.execution_mode,
                account=config.account,
                logging=config.logging,
                broker=config.broker,
                market_data=config.market_data,
                strategy=config.strategy,
                risk=config.risk,
                execution=config.execution,
                orchestrator=config.orchestrator,
                event_bus=config.event_bus,
                position=config.position,
                portfolio=config.portfolio,
                apme=config.apme,
                dashboard=config.dashboard,
                features=config.features,
                secrets=config.secrets,
                paths=config.paths,
                metadata=config.metadata,
            )

    def test_naive_loaded_at_raises(self, empty_secrets: InlineSecretProvider) -> None:
        config = load_dev()
        with pytest.raises(ApplicationConfigurationError):
            ApplicationConfiguration(
                schema_version=config.schema_version,
                config_id=config.config_id,
                config_fingerprint=config.config_fingerprint,
                loaded_at=datetime(2026, 1, 1),
                profile=config.profile,
                execution_mode=config.execution_mode,
                account=config.account,
                logging=config.logging,
                broker=config.broker,
                market_data=config.market_data,
                strategy=config.strategy,
                risk=config.risk,
                execution=config.execution,
                orchestrator=config.orchestrator,
                event_bus=config.event_bus,
                position=config.position,
                portfolio=config.portfolio,
                apme=config.apme,
                dashboard=config.dashboard,
                features=config.features,
                secrets=config.secrets,
                paths=config.paths,
                metadata=config.metadata,
            )


class TestYamlConfigLoading:
    """YAML configuration file loading."""

    def test_yaml_requires_pyyaml(
        self,
        tmp_path: Path,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        yaml_path = tmp_path / "application.yaml"
        yaml_path.write_text("profile: paper\n", encoding="utf-8")
        with patch.dict("sys.modules", {"yaml": None}):
            with pytest.raises(ApplicationConfigurationError) as exc:
                load_application_configuration(
                    LoadOptions(
                        profile=EnvironmentProfile.DEVELOPMENT,
                        config_file_path=str(yaml_path),
                        user_config_path=str(tmp_path / "missing.json"),
                    ),
                    secret_provider=empty_secrets,
                    env={},
                )
        assert exc.value.code == "CONFIG.FILE.PARSE_ERROR"


class TestDeserializeEdgeCases:
    """Deserialize edge cases."""

    def test_deserialize_non_object_payload(self) -> None:
        with pytest.raises(ApplicationConfigurationError) as exc:
            deserialize_application_configuration("[1, 2, 3]")
        assert exc.value.code == "CONFIG.SERIALIZATION.MALFORMED"

    def test_deserialize_round_trip_fingerprint(
        self,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        config = load_dev()
        payload = serialize_application_configuration(config)
        restored = deserialize_application_configuration(payload)
        assert restored.config_fingerprint == config.config_fingerprint


class TestDefaultSecretProvider:
    """Default composite secret provider wiring."""

    def test_default_provider_resolution(self) -> None:
        config = load_application_configuration(
            LoadOptions(profile=EnvironmentProfile.DEVELOPMENT, allow_missing_secrets=True),
            env={"THETA_BROKER_API_KEY": "from-default-provider"},
        )
        assert config.profile is EnvironmentProfile.DEVELOPMENT


class TestProductionMissingConfigFile:
    """Production config file requirement."""

    def test_production_missing_default_config_raises(
        self,
        empty_secrets: InlineSecretProvider,
    ) -> None:
        with patch.object(Path, "is_file", return_value=False):
            with pytest.raises(ApplicationConfigurationError) as exc:
                load_application_configuration(
                    LoadOptions(
                        profile=EnvironmentProfile.PRODUCTION,
                        allow_missing_secrets=True,
                    ),
                    secret_provider=empty_secrets,
                    env={"THETA_ACCOUNT_ID": "AB1234", "THETA_CONFIG_FILE": "config/app.json"},
                )
            assert exc.value.code == "CONFIG.FILE.NOT_FOUND"


class TestInternalHelpers:
    """Direct helper function tests."""

    def test_deep_merge_dict(self) -> None:
        from config.application_configuration import _deep_merge_dict

        base = {"a": {"b": 1}, "c": 2}
        merged = _deep_merge_dict(base, {"a": {"d": 3}, "c": 4})
        assert merged == {"a": {"b": 1, "d": 3}, "c": 4}

    def test_isoformat_utc_naive_raises(self) -> None:
        from config.application_configuration import _isoformat_utc

        with pytest.raises(ApplicationConfigurationError):
            _isoformat_utc(datetime(2026, 1, 1))
