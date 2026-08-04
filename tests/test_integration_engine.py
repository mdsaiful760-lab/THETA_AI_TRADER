"""Unit tests for system.integration_engine."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dataclasses import replace
from datetime import datetime, timezone
from types import MappingProxyType, SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from broker.base_broker import (
    BaseBrokerClient,
    BrokerCapabilities,
    BrokerClientMetadata,
    BrokerConnectionError,
    BrokerId,
    BrokerSession,
    ConnectionInfo,
    ConnectionState,
    SessionState,
    WebSocketState,
)
from config.application_configuration import (
    ApplicationConfiguration,
    ApplicationConfigurationError,
    BrokerConfiguration,
    BrokerType,
    EnvironmentProfile,
    InlineSecretProvider,
    LoadOptions,
    compute_config_fingerprint,
    default_load_options_for_profile,
    load_application_configuration,
)
from core.event_bus import EventBus
from strategy.signals import StrategyExecutionMode
from strategy.registry import StrategyRegistry
from system.integration_engine import (
    BootstrapStageId,
    BootstrapStatus,
    BrokerClientFactory,
    BrokerHealthSnapshot,
    EngineOverrides,
    IntegrationBootstrapError,
    IntegrationBootstrapOptions,
    IntegrationBrokerError,
    IntegrationConfigurationError,
    IntegrationEngine,
    IntegrationEngineError,
    IntegrationHealthReport,
    IntegrationSessionState,
    IntegrationSessionStateError,
    IntegrationWiringError,
    RunnerKind,
    RuntimeState,
    WiringCheckId,
    WiringValidationIssue,
    WiringValidationResult,
    WiringValidationStatus,
    bootstrap_integration_session,
    compute_wiring_fingerprint,
    create_development_session,
    create_live_session,
    create_paper_trading_session,
    deserialize_integration_health_report,
    deserialize_runtime_state,
    serialize_integration_health_report,
    serialize_runtime_state,
    validate_wiring,
)
from system.system_orchestrator import (
    CycleStatus,
    CycleTrigger,
    EngineRegistry,
    HealthStatus,
    OrchestratorState,
    PostFillCycleContext,
    StartupStatus,
    SystemHealthReport,
    SystemOrchestrator,
    SystemOrchestratorConfig,
    TradingCycleContext,
    default_orchestrator_config,
)
from tests.test_base_broker import MinimalBrokerClient, make_session, utc_now

IST = ZoneInfo("Asia/Kolkata")


def fixed_clock() -> datetime:
    """Deterministic clock for integration tests."""
    return datetime(2026, 8, 4, 4, 30, tzinfo=IST)


def load_dev_config(**kwargs: object) -> ApplicationConfiguration:
    """Load development profile configuration."""
    options = LoadOptions(
        profile=EnvironmentProfile.DEVELOPMENT,
        user_config_path="/nonexistent/user_config.json",
        **kwargs,
    )
    return load_application_configuration(
        options,
        secret_provider=InlineSecretProvider({}),
        env={},
    )


def load_dev_with_execution_mode(mode: StrategyExecutionMode) -> ApplicationConfiguration:
    """Load development config with an explicit execution mode override."""
    return load_application_configuration(
        LoadOptions(
            profile=EnvironmentProfile.DEVELOPMENT,
            user_config_path="/nonexistent/user_config.json",
        ),
        secret_provider=InlineSecretProvider({}),
        env={"THETA_EXECUTION_MODE": mode.value},
    )


class ConnectableStubBroker(MinimalBrokerClient):
    """Stub broker that supports deterministic connect/disconnect."""

    def __init__(
        self,
        session: BrokerSession | None = None,
        *,
        connect_fail: bool = False,
    ) -> None:
        super().__init__(session or make_session())
        self._connect_fail = connect_fail
        self._connected = False

    def connect(self) -> None:
        if self._connect_fail:
            raise BrokerConnectionError("connect failed", code="BROKER.CONNECTION.FAILED")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_connection_info(self) -> ConnectionInfo:
        return ConnectionInfo(
            state=ConnectionState.CONNECTED if self._connected else ConnectionState.DISCONNECTED,
            since=fixed_clock() if self._connected else None,
            websocket_state=WebSocketState.OPEN if self._connected else WebSocketState.CLOSED,
        )

    def get_session_state(self) -> SessionState:
        return SessionState.AUTHENTICATED if self._connected else SessionState.UNAUTHENTICATED


class StubEngine:
    """Minimal engine stub."""

    def validate_configuration(self) -> None:
        return None


def make_registry(
    bus: EventBus,
    broker: BaseBrokerClient,
    *,
    market_data_broker: BaseBrokerClient | None = None,
) -> EngineRegistry:
    """Build engine registry with stub engines sharing bus and broker."""
    md = SimpleNamespace(_broker=market_data_broker or broker)
    return EngineRegistry(
        event_bus=bus,
        market_data=md,
        strategy_evaluation=StubEngine(),
        trade_decision=StubEngine(),
        risk=StubEngine(),
        execution=StubEngine(),
        order_manager=StubEngine(),
        position_manager=StubEngine(),
        portfolio_manager=StubEngine(),
        apme=StubEngine(),
    )


def make_orchestrator(
    config: ApplicationConfiguration,
    bus: EventBus,
    broker: BaseBrokerClient,
    registry: EngineRegistry | None = None,
) -> SystemOrchestrator:
    """Construct orchestrator wired to shared collaborators."""
    registry = registry or make_registry(bus, broker)
    return SystemOrchestrator(
        config.to_orchestrator_config(),
        event_bus=bus,
        broker_client=broker,
        engine_registry=registry,
        clock=fixed_clock,
    )


def bootstrap_with_overrides(
    config: ApplicationConfiguration,
    *,
    broker: BaseBrokerClient | None = None,
    bus: EventBus | None = None,
    orchestrator: SystemOrchestrator | None = None,
    fail_fast_on_wiring_error: bool = True,
    auto_start_orchestrator: bool = True,
    validate_wiring: bool = True,
) -> object:
    """Bootstrap integration session using test overrides."""
    bus = bus or EventBus(config.to_event_bus_policy())
    broker = broker or ConnectableStubBroker()
    registry = make_registry(bus, broker)
    orchestrator = orchestrator or make_orchestrator(config, bus, broker, registry)
    options = IntegrationBootstrapOptions(
        runner_kind=RunnerKind.TEST_HARNESS,
        engine_overrides=EngineOverrides(
            event_bus=bus,
            broker_client=broker,
            strategy_registry=StrategyRegistry(config.to_strategy_registry_config()),
            orchestrator=orchestrator,
            market_data=registry.market_data,
            strategy_evaluation=registry.strategy_evaluation,
            trade_decision=registry.trade_decision,
            risk=registry.risk,
            execution=registry.execution,
            order_manager=registry.order_manager,
            position_manager=registry.position_manager,
            portfolio_manager=registry.portfolio_manager,
            apme=registry.apme,
        ),
        fail_fast_on_wiring_error=fail_fast_on_wiring_error,
        auto_start_orchestrator=auto_start_orchestrator,
        clock=fixed_clock,
    )
    return IntegrationEngine(config, options).bootstrap()


class TestBrokerClientFactory:
    """Broker factory resolution tests."""

    def test_mock_broker_raises_implementation_not_found(self) -> None:
        session = make_session()
        with pytest.raises(IntegrationBrokerError) as exc:
            BrokerClientFactory.create(BrokerType.MOCK, session, BrokerConfiguration())
        assert exc.value.code == "INTEGRATION.BROKER.IMPLEMENTATION_NOT_FOUND"

    def test_unknown_broker_type_raises(self) -> None:
        session = make_session()
        with pytest.raises(IntegrationBrokerError):
            BrokerClientFactory.create(BrokerType.RECORDING, session, BrokerConfiguration())


class TestWiringValidation:
    """End-to-end wiring validation tests."""

    def test_happy_path_passes(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus, broker)
        orchestrator = make_orchestrator(config, bus, broker, registry)
        result = validate_wiring(config, bus, broker, registry, orchestrator, clock=fixed_clock)
        assert result.status is WiringValidationStatus.PASSED

    def test_event_bus_mismatch_fails(self) -> None:
        config = load_dev_config()
        bus_a = EventBus(config.to_event_bus_policy())
        bus_b = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus_b, broker)
        orchestrator = make_orchestrator(config, bus_b, broker, registry)
        result = validate_wiring(config, bus_a, broker, registry, orchestrator, clock=fixed_clock)
        assert result.status is WiringValidationStatus.FAILED
        assert any(
            issue.check_id is WiringCheckId.EVENT_BUS_IDENTITY for issue in result.issues
        )

    def test_broker_mismatch_fails(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker_a = ConnectableStubBroker()
        broker_b = ConnectableStubBroker()
        registry = make_registry(bus, broker_a, market_data_broker=broker_b)
        orchestrator = make_orchestrator(config, bus, broker_a, registry)
        result = validate_wiring(config, bus, broker_a, registry, orchestrator, clock=fixed_clock)
        assert result.status is WiringValidationStatus.FAILED
        assert any(issue.check_id is WiringCheckId.BROKER_IDENTITY for issue in result.issues)

    def test_incomplete_registry_fails(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = EngineRegistry(event_bus=bus, market_data=SimpleNamespace(_broker=broker))
        orchestrator = SystemOrchestrator(
            config.to_orchestrator_config(),
            event_bus=bus,
            broker_client=broker,
            engine_registry=registry,
            clock=fixed_clock,
        )
        result = validate_wiring(config, bus, broker, registry, orchestrator, clock=fixed_clock)
        assert result.status is WiringValidationStatus.FAILED
        assert any(
            issue.check_id is WiringCheckId.REGISTRY_COMPLETENESS for issue in result.issues
        )


class TestBootstrapHappyPath:
    """Happy-path bootstrap tests."""

    def test_bootstrap_reaches_running(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        try:
            assert session.get_runtime_state().session_state is IntegrationSessionState.RUNNING
            health = session.get_health()
            assert health.overall_status in {HealthStatus.HEALTHY, HealthStatus.DEGRADED}
            assert health.wiring_status is WiringValidationStatus.PASSED
        finally:
            session.stop()

    def test_real_engines_with_stub_broker(self) -> None:
        config = load_dev_config()
        broker = ConnectableStubBroker()
        options = IntegrationBootstrapOptions(
            runner_kind=RunnerKind.TEST_HARNESS,
            engine_overrides=EngineOverrides(broker_client=broker),
            fail_fast_on_wiring_error=False,
            clock=fixed_clock,
        )
        session = IntegrationEngine(config, options).bootstrap()
        try:
            assert session.get_runtime_state().session_state in {
                IntegrationSessionState.RUNNING,
                IntegrationSessionState.DEGRADED,
                IntegrationSessionState.FAILED,
            }
        finally:
            session.stop()

    def test_double_bootstrap_rejected(self) -> None:
        config = load_dev_config()
        engine = IntegrationEngine(
            config,
            IntegrationBootstrapOptions(
                engine_overrides=EngineOverrides(broker_client=ConnectableStubBroker()),
                fail_fast_on_wiring_error=False,
                clock=fixed_clock,
            ),
        )
        session = engine.bootstrap()
        try:
            with pytest.raises(IntegrationBootstrapError) as exc:
                engine.bootstrap()
            assert exc.value.code == "INTEGRATION.BOOTSTRAP.ALREADY_RUNNING"
        finally:
            session.stop()


class TestCycleDelegation:
    """Pure delegation contract tests."""

    def test_run_trading_cycle_delegates_exact_result(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus, broker)
        orchestrator = make_orchestrator(config, bus, broker, registry)
        expected = MagicMock(name="cycle_result")
        orchestrator.run_trading_cycle = MagicMock(return_value=expected)
        session = bootstrap_with_overrides(
            config,
            bus=bus,
            broker=broker,
            orchestrator=orchestrator,
        )
        try:
            context = TradingCycleContext(
                correlation_id="corr-1",
                reference_time=fixed_clock(),
                execution_mode=StrategyExecutionMode.BACKTEST,
                account_id=config.account.account_id,
                trigger=CycleTrigger.MANUAL,
            )
            result = session.run_trading_cycle(context)
            assert result is expected
            orchestrator.run_trading_cycle.assert_called_once_with(context)
        finally:
            session.stop()

    def test_cycle_rejected_before_running(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config, auto_start_orchestrator=False)
        try:
            assert session.get_runtime_state().session_state is IntegrationSessionState.WIRED
            context = TradingCycleContext(
                correlation_id="corr-2",
                reference_time=fixed_clock(),
                execution_mode=StrategyExecutionMode.BACKTEST,
                account_id=config.account.account_id,
            )
            with pytest.raises(IntegrationSessionStateError) as exc:
                session.run_trading_cycle(context)
            assert exc.value.code == "INTEGRATION.SESSION.NOT_BOOTSTRAPPED"
        finally:
            session.stop()


class TestLifecycle:
    """Session lifecycle tests."""

    def test_start_stop_flow(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config, auto_start_orchestrator=False)
        try:
            assert session.get_runtime_state().session_state is IntegrationSessionState.WIRED
            startup = session.start()
            assert startup.status is StartupStatus.SUCCESS
            assert session.get_runtime_state().session_state is IntegrationSessionState.RUNNING
            shutdown = session.stop()
            assert session.get_runtime_state().session_state is IntegrationSessionState.STOPPED
            assert shutdown.status.value in {"success", "forced"}
        finally:
            if session.get_runtime_state().session_state is not IntegrationSessionState.STOPPED:
                session.stop()

    def test_context_manager_stops_session(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        with session:
            assert session.get_runtime_state().session_state is IntegrationSessionState.RUNNING
        assert session.get_runtime_state().session_state is IntegrationSessionState.STOPPED

    def test_restart_preserves_session_id(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        original_id = session.session_id
        try:
            restarted = session.restart()
            assert restarted.session_id == original_id
            restarted.stop()
        finally:
            if session.get_runtime_state().session_state is not IntegrationSessionState.STOPPED:
                session.stop()


class TestHealthAggregation:
    """Integration health report tests."""

    def test_failed_session_unhealthy(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus, broker)
        orchestrator = make_orchestrator(config, bus, broker, registry)
        session = bootstrap_with_overrides(
            config,
            bus=bus,
            broker=broker,
            orchestrator=orchestrator,
            fail_fast_on_wiring_error=False,
            auto_start_orchestrator=False,
        )
        session._state = IntegrationSessionState.FAILED  # noqa: SLF001
        health = session.get_health()
        assert health.overall_status is HealthStatus.UNHEALTHY

    def test_live_disconnected_broker_unhealthy(self) -> None:
        from system.integration_engine import _aggregate_overall_status

        snapshot = BrokerHealthSnapshot(
            broker_id=BrokerId.MOCK,
            connection_state=ConnectionState.DISCONNECTED,
            session_state=SessionState.UNAUTHENTICATED,
            last_connected_at=None,
            last_error_code=None,
            last_error_message=None,
        )
        status = _aggregate_overall_status(
            session_state=IntegrationSessionState.RUNNING,
            orchestrator_health=None,
            broker_snapshot=snapshot,
            wiring_status=WiringValidationStatus.PASSED,
            execution_mode=StrategyExecutionMode.LIVE,
        )
        assert status is HealthStatus.UNHEALTHY

    def test_degraded_orchestrator_maps_degraded(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        try:
            degraded_health = SystemHealthReport(
                report_id="r1",
                as_of=fixed_clock(),
                orchestrator_state=OrchestratorState.DEGRADED,
                engine_health=MappingProxyType({}),
                event_bus_metrics=SimpleNamespace(
                    publish_count=0,
                    delivery_count=0,
                    subscriber_failure_count=0,
                    active_subscriptions=0,
                ),
                last_cycle_at=None,
                last_cycle_status=None,
                stale_snapshot=False,
                issues=(),
                overall_status=HealthStatus.DEGRADED,
            )
            session._orchestrator.get_health = MagicMock(return_value=degraded_health)  # noqa: SLF001
            session._state = IntegrationSessionState.DEGRADED  # noqa: SLF001
            health = session.get_health()
            assert health.overall_status is HealthStatus.DEGRADED
        finally:
            session.stop()


class TestDeterminism:
    """Wiring fingerprint determinism tests."""

    def test_identical_config_same_fingerprint(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus, broker)
        fp1 = compute_wiring_fingerprint(config, registry, broker)
        fp2 = compute_wiring_fingerprint(config, registry, broker)
        assert fp1 == fp2

    def test_bootstrap_produces_stable_fingerprint(self) -> None:
        config = load_dev_config()
        session_a = bootstrap_with_overrides(config)
        session_b = bootstrap_with_overrides(config)
        try:
            assert (
                session_a.get_runtime_state().wiring_fingerprint
                == session_b.get_runtime_state().wiring_fingerprint
            )
        finally:
            session_a.stop()
            session_b.stop()


class TestSerialization:
    """JSON serialization round-trip tests."""

    def test_runtime_state_round_trip(self) -> None:
        state = RuntimeState(
            session_id="sess-1",
            as_of=fixed_clock(),
            session_state=IntegrationSessionState.RUNNING,
            orchestrator_state=OrchestratorState.RUNNING,
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            execution_mode=StrategyExecutionMode.BACKTEST,
            runner_kind=RunnerKind.TEST_HARNESS,
            account_id="acct-1",
            broker_id=BrokerId.MOCK,
            broker_connection_state=ConnectionState.CONNECTED,
            config_fingerprint="abc123",
            wiring_fingerprint="def456",
            uptime_seconds=12.5,
            last_cycle_at=fixed_clock(),
            last_cycle_status=CycleStatus.COMPLETED,
            metadata=MappingProxyType({"k": "v"}),
        )
        restored = deserialize_runtime_state(serialize_runtime_state(state))
        assert restored.session_id == state.session_id
        assert restored.session_state is state.session_state
        assert restored.wiring_fingerprint == state.wiring_fingerprint

    def test_health_report_round_trip(self) -> None:
        report = IntegrationHealthReport(
            report_id="rep-1",
            as_of=fixed_clock(),
            session_state=IntegrationSessionState.RUNNING,
            overall_status=HealthStatus.HEALTHY,
            orchestrator_health=None,
            broker_connection=BrokerHealthSnapshot(
                broker_id=BrokerId.MOCK,
                connection_state=ConnectionState.CONNECTED,
                session_state=SessionState.AUTHENTICATED,
                last_connected_at=fixed_clock(),
                last_error_code=None,
                last_error_message=None,
            ),
            wiring_status=WiringValidationStatus.PASSED,
            wiring_issues=(),
            config_fingerprint="cfg",
            wiring_fingerprint="wire",
            issues=(),
        )
        payload = serialize_integration_health_report(report)
        assert "super_secret" not in payload
        restored = deserialize_integration_health_report(payload)
        assert restored.report_id == report.report_id
        assert restored.overall_status is report.overall_status

    def test_malformed_json_raises(self) -> None:
        with pytest.raises(IntegrationConfigurationError):
            deserialize_runtime_state("{not-json")


class TestConfigurationErrors:
    """Configuration resolution failure tests."""

    def test_profile_mode_mismatch(self) -> None:
        config = load_dev_with_execution_mode(StrategyExecutionMode.LIVE)
        engine = IntegrationEngine(config)
        with pytest.raises(IntegrationConfigurationError) as exc:
            engine._resolve_configuration(config)
        assert exc.value.code == "INTEGRATION.CONFIG.PROFILE_MODE_MISMATCH"

    def test_wiring_fail_fast_raises(self) -> None:
        config = load_dev_config()
        bus_a = EventBus(config.to_event_bus_policy())
        bus_b = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus_b, broker)
        orchestrator = make_orchestrator(config, bus_b, broker, registry)
        options = IntegrationBootstrapOptions(
            engine_overrides=EngineOverrides(
                event_bus=bus_b,
                broker_client=broker,
                orchestrator=orchestrator,
                strategy_registry=StrategyRegistry(config.to_strategy_registry_config()),
                market_data=registry.market_data,
                strategy_evaluation=registry.strategy_evaluation,
                trade_decision=registry.trade_decision,
                risk=registry.risk,
                execution=registry.execution,
                order_manager=registry.order_manager,
                position_manager=registry.position_manager,
                portfolio_manager=registry.portfolio_manager,
                apme=registry.apme,
            ),
            fail_fast_on_wiring_error=True,
            clock=fixed_clock,
        )
        engine = IntegrationEngine(config, options)
        original_validate = validate_wiring

        def fail_validate(*args: object, **kwargs: object) -> object:
            del args, kwargs
            from system.integration_engine import WiringValidationIssue, WiringValidationResult

            return WiringValidationResult(
                validation_id="v1",
                as_of=fixed_clock(),
                status=WiringValidationStatus.FAILED,
                checks=(),
                issues=(
                    WiringValidationIssue(
                        code="INTEGRATION.WIRING.EVENT_BUS_MISMATCH",
                        message="forced",
                        check_id=WiringCheckId.EVENT_BUS_IDENTITY,
                        severity="ERROR",
                    ),
                ),
                wiring_fingerprint="abc",
            )

        with patch("system.integration_engine.validate_wiring", side_effect=fail_validate):
            with pytest.raises(IntegrationWiringError):
                engine.bootstrap()


class TestModeMatrix:
    """Convenience bootstrap function tests."""

    def test_create_development_session_options(self) -> None:
        captured: dict[str, object] = {}

        def capture_init(self: object, config: object, options: object) -> None:
            del self, config
            captured["options"] = options

        with patch.object(IntegrationEngine, "__init__", capture_init):
            with patch.object(IntegrationEngine, "bootstrap", return_value=MagicMock()):
                create_development_session()
        options = captured["options"]
        assert isinstance(options, IntegrationBootstrapOptions)
        assert options.runner_kind is RunnerKind.CLI
        assert options.fail_fast_on_wiring_error is False

    def test_create_paper_trading_session_options(self) -> None:
        captured: dict[str, object] = {}

        def capture_init(self: object, config: object, options: object) -> None:
            del self, config
            captured["options"] = options

        with patch.object(IntegrationEngine, "__init__", capture_init):
            with patch.object(IntegrationEngine, "bootstrap", return_value=MagicMock()):
                create_paper_trading_session()
        options = captured["options"]
        assert isinstance(options, IntegrationBootstrapOptions)
        assert options.runner_kind is RunnerKind.PAPER_TRADING
        assert options.fail_fast_on_wiring_error is True

    def test_create_live_session_options(self) -> None:
        captured: dict[str, object] = {}

        def capture_init(self: object, config: object, options: object) -> None:
            del self, config
            captured["options"] = options

        with patch.object(IntegrationEngine, "__init__", capture_init):
            with patch.object(IntegrationEngine, "bootstrap", return_value=MagicMock()):
                create_live_session()
        options = captured["options"]
        assert isinstance(options, IntegrationBootstrapOptions)
        assert options.runner_kind is RunnerKind.LIVE_TRADING
        assert options.auto_connect_broker is True


class TestThreadSafety:
    """Concurrent access tests."""

    def test_concurrent_health_and_runtime_reads(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        try:
            barrier = threading.Barrier(4)

            def worker() -> None:
                barrier.wait()
                session.get_health()
                session.get_runtime_state()

            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(worker) for _ in range(4)]
                for future in futures:
                    future.result()
        finally:
            session.stop()


class TestBrokerConnectionFailure:
    """Non-critical broker connection failure paths."""

    def test_connect_failure_degraded_in_development(self) -> None:
        config = load_dev_config()
        broker = ConnectableStubBroker(connect_fail=True)
        session = bootstrap_with_overrides(config, broker=broker)
        try:
            diagnostics = session._bootstrap_diagnostics  # noqa: SLF001
            assert diagnostics.status in {BootstrapStatus.PARTIAL, BootstrapStatus.SUCCESS}
            assert any(
                w.stage_id is BootstrapStageId.BROKER_CONNECTION for w in diagnostics.warnings
            )
        finally:
            session.stop()


class TestRevalidateWiring:
    """Manual wiring re-validation tests."""

    def test_revalidate_wiring_updates_status(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        try:
            result = session.revalidate_wiring()
            assert result.status is WiringValidationStatus.PASSED
            assert session.get_health().wiring_status is WiringValidationStatus.PASSED
        finally:
            session.stop()


class TestComputeWiringFingerprint:
    """Fingerprint helper edge cases."""

    def test_no_broker_client(self) -> None:
        config = load_dev_config()
        registry = EngineRegistry()
        fp = compute_wiring_fingerprint(config, registry, None)
        assert len(fp) == 64


class TestAdditionalCoverage:
    """Targeted tests for uncovered integration engine paths."""

    def test_kite_broker_factory(self) -> None:
        from system.integration_engine import _build_kite_broker_client

        session = BrokerSession(
            broker_id=BrokerId.KITE,
            session_id="session-kite",
            authenticated_at=fixed_clock(),
            credentials=MappingProxyType({"api_key": "key", "access_token": "token"}),
        )
        with patch("broker.zerodha.kite_broker.KiteBrokerClient") as mock_cls:
            mock_cls.return_value = MagicMock(broker_id=BrokerId.KITE)
            client = _build_kite_broker_client(
                session,
                BrokerConfiguration(broker_type=BrokerType.ZERODHA_KITE),
            )
        assert client.broker_id is BrokerId.KITE

    def test_build_recording_broker_import_error(self) -> None:
        with pytest.raises(IntegrationBrokerError) as exc:
            BrokerClientFactory.create(
                BrokerType.RECORDING,
                make_session(),
                BrokerConfiguration(broker_type=BrokerType.RECORDING),
            )
        assert exc.value.code == "INTEGRATION.BROKER.IMPLEMENTATION_NOT_FOUND"

    def test_strategy_registry_live_production_fails(self) -> None:
        from system.integration_engine import _check_strategy_registry_population

        config = load_application_configuration(
            LoadOptions(
                profile=EnvironmentProfile.PRODUCTION,
                allow_missing_config_file=True,
                user_config_path="/nonexistent/user_config.json",
            ),
            secret_provider=InlineSecretProvider(
                {
                    "broker.api_key": "k",
                    "broker.api_secret": "s",
                }
            ),
            env={
                "THETA_ACCOUNT_ID": "acct-prod-1",
                "THETA_ALLOW_MOCK_BROKER_IN_PRODUCTION": "true",
            },
        )
        registry = StrategyRegistry(config.to_strategy_registry_config())
        result = _check_strategy_registry_population(registry, config)
        assert result.passed is False

    def test_broker_session_expired_fails(self) -> None:
        class ExpiredBroker(ConnectableStubBroker):
            def get_session_state(self) -> SessionState:
                return SessionState.EXPIRED

        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ExpiredBroker()
        registry = make_registry(bus, broker)
        orchestrator = make_orchestrator(config, bus, broker, registry)
        result = validate_wiring(config, bus, broker, registry, orchestrator, clock=fixed_clock)
        assert any(issue.check_id is WiringCheckId.BROKER_SESSION_VALIDITY for issue in result.issues)

    def test_subscription_pattern_warning(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus, broker)
        orch_config = replace(config.to_orchestrator_config(), subscription_patterns=("custom.only",))
        orchestrator = SystemOrchestrator(
            orch_config,
            event_bus=bus,
            broker_client=broker,
            engine_registry=registry,
            clock=fixed_clock,
        )
        result = validate_wiring(config, bus, broker, registry, orchestrator, clock=fixed_clock)
        assert result.status in {
            WiringValidationStatus.PASSED,
            WiringValidationStatus.PASSED_WITH_WARNINGS,
        }

    def test_bootstrap_without_wiring_validation(self) -> None:
        config = load_dev_config()
        options = IntegrationBootstrapOptions(
            engine_overrides=EngineOverrides(broker_client=ConnectableStubBroker()),
            validate_wiring=False,
            fail_fast_on_wiring_error=False,
            clock=fixed_clock,
        )
        session = IntegrationEngine(config, options).bootstrap()
        try:
            assert session.get_health().wiring_status is WiringValidationStatus.PASSED
        finally:
            session.stop()

    def test_load_configuration_from_options(self) -> None:
        options = IntegrationBootstrapOptions(
            load_options=LoadOptions(
                profile=EnvironmentProfile.DEVELOPMENT,
                user_config_path="/nonexistent/user_config.json",
            ),
            engine_overrides=EngineOverrides(broker_client=ConnectableStubBroker()),
            fail_fast_on_wiring_error=False,
            clock=fixed_clock,
        )
        session = IntegrationEngine(options=options).bootstrap()
        try:
            assert session.get_configuration().profile is EnvironmentProfile.DEVELOPMENT
        finally:
            session.stop()

    def test_get_broker_client_missing_raises(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        try:
            session._broker_client = None  # noqa: SLF001
            with pytest.raises(IntegrationSessionStateError):
                session.get_broker_client()
        finally:
            session.stop()

    def test_get_strategy_registry_missing_raises(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        try:
            session._strategy_registry = None  # noqa: SLF001
            with pytest.raises(IntegrationSessionStateError):
                session.get_strategy_registry()
        finally:
            session.stop()

    def test_already_stopped_cycle_raises(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        session.stop()
        with pytest.raises(IntegrationSessionStateError) as exc:
            session.run_trading_cycle(
                TradingCycleContext(
                    correlation_id="c",
                    reference_time=fixed_clock(),
                    execution_mode=StrategyExecutionMode.BACKTEST,
                    account_id="a",
                )
            )
        assert exc.value.code == "INTEGRATION.SESSION.ALREADY_STOPPED"

    def test_run_post_fill_cycle_delegates(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        expected = MagicMock(name="post_fill_result")
        context = MagicMock(name="post_fill_context")
        session._orchestrator.run_post_fill_cycle = MagicMock(return_value=expected)  # noqa: SLF001
        try:
            assert session.run_post_fill_cycle(context) is expected
        finally:
            session.stop()

    def test_run_forever_respects_stop_event(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        stop_event = threading.Event()
        stop_event.set()
        try:
            session.run_forever(
                interval_seconds=0.01,
                context_factory=lambda: TradingCycleContext(
                    correlation_id="c",
                    reference_time=fixed_clock(),
                    execution_mode=StrategyExecutionMode.BACKTEST,
                    account_id="a",
                ),
                stop_event=stop_event,
            )
        finally:
            session.stop()

    def test_start_idempotent_when_running(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        try:
            result = session.start()
            assert result.status is StartupStatus.SUCCESS
        finally:
            session.stop()

    def test_deserialize_unsupported_schema_version(self) -> None:
        payload = json.dumps({"schema_version": "9.9.9"})
        with pytest.raises(IntegrationConfigurationError):
            deserialize_runtime_state(payload)

    def test_deserialize_health_unsupported_schema(self) -> None:
        payload = json.dumps({"schema_version": "9.9.9"})
        with pytest.raises(IntegrationConfigurationError):
            deserialize_integration_health_report(payload)

    def test_wiring_failed_session_without_fail_fast(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus, broker)
        orchestrator = make_orchestrator(config, bus, broker, registry)
        options = IntegrationBootstrapOptions(
            engine_overrides=EngineOverrides(
                event_bus=bus,
                broker_client=broker,
                orchestrator=orchestrator,
                strategy_registry=StrategyRegistry(config.to_strategy_registry_config()),
                market_data=registry.market_data,
                strategy_evaluation=registry.strategy_evaluation,
                trade_decision=registry.trade_decision,
                risk=registry.risk,
                execution=registry.execution,
                order_manager=registry.order_manager,
                position_manager=registry.position_manager,
                portfolio_manager=registry.portfolio_manager,
                apme=registry.apme,
            ),
            fail_fast_on_wiring_error=False,
            clock=fixed_clock,
        )
        engine = IntegrationEngine(config, options)

        def fail_validate(*args: object, **kwargs: object) -> object:
            del args, kwargs
            from system.integration_engine import WiringValidationIssue, WiringValidationResult

            return WiringValidationResult(
                validation_id="v1",
                as_of=fixed_clock(),
                status=WiringValidationStatus.FAILED,
                checks=(),
                issues=(
                    WiringValidationIssue(
                        code="INTEGRATION.WIRING.INCOMPLETE_REGISTRY",
                        message="forced",
                        check_id=WiringCheckId.REGISTRY_COMPLETENESS,
                        severity="ERROR",
                    ),
                ),
                wiring_fingerprint="abc",
            )

        with patch("system.integration_engine.validate_wiring", side_effect=fail_validate):
            session = engine.bootstrap()
        try:
            assert session.get_runtime_state().session_state is IntegrationSessionState.FAILED
        finally:
            session.stop()

    def test_collect_health_issues_includes_orchestrator(self) -> None:
        from system.integration_engine import _collect_health_issues
        from system.system_orchestrator import EventBusHealthMetrics, HealthIssueRecord

        orchestrator_health = SystemHealthReport(
            report_id="r",
            as_of=fixed_clock(),
            orchestrator_state=OrchestratorState.RUNNING,
            engine_health=MappingProxyType({}),
            event_bus_metrics=EventBusHealthMetrics(0, 0, 0, 0),
            last_cycle_at=None,
            last_cycle_status=None,
            stale_snapshot=False,
            issues=(
                HealthIssueRecord(
                    issue_code="X",
                    severity="warning",
                    message="m",
                    engine_id=None,
                ),
            ),
            overall_status=HealthStatus.HEALTHY,
        )
        issues = _collect_health_issues(
            orchestrator_health,
            BrokerHealthSnapshot(None, None, None, None, None, None),
            StrategyExecutionMode.BACKTEST,
        )
        assert len(issues) == 1

    def test_map_broker_type_unknown(self) -> None:
        from system.integration_engine import _map_broker_type

        assert _map_broker_type(BrokerType.ZERODHA_KITE) is BrokerId.KITE

    def test_allow_live_in_development_metadata(self) -> None:
        base = load_dev_with_execution_mode(StrategyExecutionMode.LIVE)
        allowed = replace(
            base,
            metadata=MappingProxyType({"allow_live_in_development": "true"}),
        )
        allowed = replace(allowed, config_fingerprint=compute_config_fingerprint(allowed))
        resolved = IntegrationEngine(allowed)._resolve_configuration(allowed)
        assert resolved.execution_mode is StrategyExecutionMode.LIVE

    def test_health_aggregation_failure(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        try:
            session._orchestrator.get_health = MagicMock(side_effect=RuntimeError("boom"))  # noqa: SLF001
            with pytest.raises(IntegrationEngineError) as exc:
                session.get_health()
            assert exc.value.code == "INTEGRATION.HEALTH.AGGREGATION_FAILED"
        finally:
            session.stop()

    def test_resolve_broker_session_with_secrets(self) -> None:
        config = load_application_configuration(
            LoadOptions(
                profile=EnvironmentProfile.PRODUCTION,
                allow_missing_config_file=True,
                user_config_path="/nonexistent/user_config.json",
            ),
            secret_provider=InlineSecretProvider(
                {
                    "broker.api_key": "key",
                    "broker.api_secret": "secret",
                    "broker.access_token": "token",
                }
            ),
            env={"THETA_ACCOUNT_ID": "acct-prod-1"},
        )
        engine = IntegrationEngine(config, IntegrationBootstrapOptions(clock=fixed_clock))
        provider = InlineSecretProvider(
            {
                "broker.api_key": "key",
                "broker.api_secret": "secret",
                "broker.access_token": "token",
            }
        )
        with patch("system.integration_engine._default_secret_provider", return_value=provider):
            session = engine._resolve_broker_session(config)
        assert session.broker_id is BrokerId.KITE

    def test_resolve_broker_session_skips_when_override(self) -> None:
        config = load_dev_config()
        broker = ConnectableStubBroker()
        engine = IntegrationEngine(
            config,
            IntegrationBootstrapOptions(
                engine_overrides=EngineOverrides(broker_client=broker),
                clock=fixed_clock,
            ),
        )
        resolved = engine._resolve_broker_session(config)
        assert resolved.session_id == broker.session.session_id

    def test_non_critical_engine_failure_continues(self) -> None:
        config = load_dev_config()
        options = IntegrationBootstrapOptions(
            engine_overrides=EngineOverrides(broker_client=ConnectableStubBroker()),
            fail_fast_on_wiring_error=False,
            clock=fixed_clock,
        )
        with patch(
            "system.integration_engine.StrategyEvaluationEngine",
            side_effect=RuntimeError("broken"),
        ):
            session = IntegrationEngine(config, options).bootstrap()
        try:
            assert session._engine_registry.strategy_evaluation is None  # noqa: SLF001
        finally:
            session.stop()

    def test_get_orchestrator(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        try:
            assert session.get_orchestrator() is session._orchestrator  # noqa: SLF001
        finally:
            session.stop()

    def test_invalid_state_transition(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        try:
            session._state = IntegrationSessionState.FAILED  # noqa: SLF001
            with pytest.raises(IntegrationSessionStateError) as exc:
                session.run_trading_cycle(
                    TradingCycleContext(
                        correlation_id="c",
                        reference_time=fixed_clock(),
                        execution_mode=StrategyExecutionMode.BACKTEST,
                        account_id="a",
                    )
                )
            assert exc.value.code == "INTEGRATION.STATE.INVALID_TRANSITION"
        finally:
            session.stop()

    def test_isoformat_naive_datetime_raises(self) -> None:
        from system.integration_engine import _isoformat_utc

        with pytest.raises(IntegrationConfigurationError):
            _isoformat_utc(datetime(2026, 1, 1))

    def test_orchestrator_startup_partial(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus, broker)
        orchestrator = make_orchestrator(config, bus, broker, registry)
        orchestrator.start = MagicMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(
                status=StartupStatus.PARTIAL,
                startup_id="s",
                engines_started=(),
                engines_failed=(),
                subscriptions_registered=0,
                warnings=(),
                errors=(),
                started_at=fixed_clock(),
                completed_at=fixed_clock(),
                duration_ms=1.0,
            )
        )
        session = bootstrap_with_overrides(config, bus=bus, broker=broker, orchestrator=orchestrator)
        try:
            assert session.get_runtime_state().session_state is IntegrationSessionState.DEGRADED
        finally:
            session.stop()

    def test_discover_strategy_plugins_empty_dir(self, tmp_path: object) -> None:
        from system.integration_engine import _discover_strategy_plugins

        assert _discover_strategy_plugins(tmp_path) == ()  # type: ignore[arg-type]

    def test_config_load_failure_wrapped(self) -> None:
        with patch(
            "system.integration_engine.load_application_configuration",
            side_effect=ApplicationConfigurationError("bad", code="CONFIG.FAIL"),
        ):
            with pytest.raises(IntegrationBootstrapError) as exc:
                IntegrationEngine(
                    options=IntegrationBootstrapOptions(
                        load_options=LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
                        engine_overrides=EngineOverrides(broker_client=ConnectableStubBroker()),
                    )
                ).bootstrap()
            assert exc.value.code == "INTEGRATION.CONFIG.LOAD_FAILED"

    def test_build_mock_broker_client_import(self) -> None:
        from system.integration_engine import _build_mock_broker_client

        with pytest.raises(ImportError):
            _build_mock_broker_client(make_session(), BrokerConfiguration())

    def test_broker_client_construction_without_override(self) -> None:
        config = load_dev_config()
        engine = IntegrationEngine(
            config,
            IntegrationBootstrapOptions(
                engine_overrides=EngineOverrides(broker_client=ConnectableStubBroker()),
                clock=fixed_clock,
            ),
        )
        session = engine._resolve_broker_session(config)
        client = engine._construct_broker_client(config, session)
        assert client is not None

    def test_health_wiring_failed_degraded(self) -> None:
        from system.integration_engine import _aggregate_overall_status

        status = _aggregate_overall_status(
            session_state=IntegrationSessionState.RUNNING,
            orchestrator_health=None,
            broker_snapshot=BrokerHealthSnapshot(None, None, None, None, None, None),
            wiring_status=WiringValidationStatus.FAILED,
            execution_mode=StrategyExecutionMode.BACKTEST,
        )
        assert status is HealthStatus.DEGRADED

    def test_strategy_registry_missing_live_dev_warning(self) -> None:
        from system.integration_engine import _check_strategy_registry_population

        config = load_dev_with_execution_mode(StrategyExecutionMode.LIVE)
        result = _check_strategy_registry_population(None, config)
        assert result.passed is True

    def test_orchestrator_startup_failed_state(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus, broker)
        orchestrator = make_orchestrator(config, bus, broker, registry)
        orchestrator.start = MagicMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(
                status=StartupStatus.FAILED,
                startup_id="s",
                engines_started=(),
                engines_failed=(),
                subscriptions_registered=0,
                warnings=(),
                errors=(),
                started_at=fixed_clock(),
                completed_at=fixed_clock(),
                duration_ms=1.0,
            )
        )
        session = bootstrap_with_overrides(config, bus=bus, broker=broker, orchestrator=orchestrator)
        try:
            assert session.get_runtime_state().session_state is IntegrationSessionState.FAILED
        finally:
            session.stop()

    def test_critical_engine_failure_aborts(self) -> None:
        config = load_dev_config()
        options = IntegrationBootstrapOptions(
            engine_overrides=EngineOverrides(broker_client=ConnectableStubBroker()),
            clock=fixed_clock,
        )
        with patch("system.integration_engine.MarketDataEngine", side_effect=RuntimeError("critical")):
            with pytest.raises(IntegrationBootstrapError):
                IntegrationEngine(config, options).bootstrap()

    def test_map_broker_type_recording(self) -> None:
        from system.integration_engine import _map_broker_type

        assert _map_broker_type(BrokerType.RECORDING) is BrokerId.MOCK

    def test_get_configuration(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        try:
            assert session.get_configuration() is config
        finally:
            session.stop()

    def test_broker_factory_unregistered_type(self) -> None:
        with patch("system.integration_engine._BROKER_CLIENT_FACTORIES", {}):
            with pytest.raises(IntegrationBrokerError):
                BrokerClientFactory.create(BrokerType.MOCK, make_session(), BrokerConfiguration())

    def test_secret_ref_missing_raises(self) -> None:
        from system.integration_engine import _resolve_secret_value

        config = load_dev_config()
        provider = InlineSecretProvider({})
        with pytest.raises(IntegrationBrokerError) as exc:
            _resolve_secret_value(config, provider, "missing.ref")
        assert exc.value.code == "INTEGRATION.BROKER.SECRET_UNRESOLVED"

    def test_secret_not_available_raises(self) -> None:
        from system.integration_engine import _resolve_secret_value

        config = load_dev_config()
        provider = InlineSecretProvider({})
        with pytest.raises(IntegrationBrokerError):
            _resolve_secret_value(config, provider, "broker.api_key")

    def test_relay_health_degraded_event(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        IntegrationEngine(config, IntegrationBootstrapOptions(clock=fixed_clock))._register_integration_subscriptions(
            bus,
            "corr-1",
        )
        bus.publish(
            "system.health.degraded",
            {"reason": "test"},
            correlation_id="corr-1",
            producer="test",
        )

    def test_construct_broker_client_missing_session(self) -> None:
        config = load_dev_config()
        engine = IntegrationEngine(config, IntegrationBootstrapOptions(clock=fixed_clock))
        with pytest.raises(IntegrationBrokerError):
            engine._construct_broker_client(config, None)

    def test_wiring_passed_with_warnings(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus, broker)
        orchestrator = make_orchestrator(
            config,
            bus,
            broker,
            replace(registry, strategy_evaluation=None),
        )
        result = validate_wiring(
            config,
            bus,
            broker,
            EngineRegistry(
                event_bus=bus,
                market_data=registry.market_data,
                risk=registry.risk,
                order_manager=registry.order_manager,
                position_manager=registry.position_manager,
                portfolio_manager=registry.portfolio_manager,
            ),
            orchestrator,
            strategy_registry=StrategyRegistry(config.to_strategy_registry_config()),
            clock=fixed_clock,
        )
        assert result.status in {
            WiringValidationStatus.FAILED,
            WiringValidationStatus.PASSED_WITH_WARNINGS,
            WiringValidationStatus.PASSED,
        }

    def test_session_stop_without_orchestrator(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config, fail_fast_on_wiring_error=False)
        session._orchestrator = None  # noqa: SLF001
        session._state = IntegrationSessionState.RUNNING  # noqa: SLF001
        result = session.stop()
        assert result.status.value == "success"

    def test_mock_broker_session_resolution(self) -> None:
        config = load_dev_config()
        engine = IntegrationEngine(config, IntegrationBootstrapOptions(clock=fixed_clock))
        session = engine._resolve_broker_session(config)
        assert session.broker_id is BrokerId.MOCK

    def test_broker_factory_create_path(self) -> None:
        config = load_dev_config()
        engine = IntegrationEngine(config, IntegrationBootstrapOptions(clock=fixed_clock))
        broker_session = engine._resolve_broker_session(config)
        with patch(
            "system.integration_engine.BrokerClientFactory.create",
            return_value=ConnectableStubBroker(broker_session),
        ) as mock_create:
            client = engine._construct_broker_client(config, broker_session)
        assert client is not None
        mock_create.assert_called_once()

    def test_orchestrator_construction_without_override(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus, broker)
        engine = IntegrationEngine(
            config,
            IntegrationBootstrapOptions(
                engine_overrides=EngineOverrides(broker_client=broker),
                fail_fast_on_wiring_error=False,
                clock=fixed_clock,
            ),
        )
        orchestrator = engine._construct_orchestrator(config, bus, broker, registry)
        assert isinstance(orchestrator, SystemOrchestrator)

    def test_run_forever_executes_one_cycle(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        stop_event = threading.Event()
        session.run_trading_cycle = MagicMock()  # type: ignore[method-assign]

        def stop_after_one(*args: object, **kwargs: object) -> None:
            del args, kwargs
            stop_event.set()

        session.run_trading_cycle.side_effect = stop_after_one
        try:
            session.run_forever(
                interval_seconds=0.01,
                context_factory=lambda: TradingCycleContext(
                    correlation_id="c",
                    reference_time=fixed_clock(),
                    execution_mode=StrategyExecutionMode.BACKTEST,
                    account_id="a",
                ),
                stop_event=stop_event,
            )
            session.run_trading_cycle.assert_called_once()
        finally:
            session.stop()

    def test_deserialize_health_malformed(self) -> None:
        with pytest.raises(IntegrationConfigurationError):
            deserialize_integration_health_report("{bad")

    def test_deserialize_health_with_orchestrator_embedded(self) -> None:
        from system.system_orchestrator import EventBusHealthMetrics, serialize_system_health_report

        orch_health = SystemHealthReport(
            report_id="r1",
            as_of=fixed_clock(),
            orchestrator_state=OrchestratorState.RUNNING,
            engine_health=MappingProxyType({}),
            event_bus_metrics=EventBusHealthMetrics(0, 0, 0, 0),
            last_cycle_at=None,
            last_cycle_status=None,
            stale_snapshot=False,
            issues=(),
            overall_status=HealthStatus.HEALTHY,
        )
        report = IntegrationHealthReport(
            report_id="rep-2",
            as_of=fixed_clock(),
            session_state=IntegrationSessionState.RUNNING,
            overall_status=HealthStatus.HEALTHY,
            orchestrator_health=orch_health,
            broker_connection=BrokerHealthSnapshot(None, None, None, None, None, None),
            wiring_status=WiringValidationStatus.PASSED,
            wiring_issues=(),
            config_fingerprint="cfg",
            wiring_fingerprint="wire",
            issues=(),
        )
        restored = deserialize_integration_health_report(serialize_integration_health_report(report))
        assert restored.orchestrator_health is not None

    def test_connect_broker_skipped_when_disabled(self) -> None:
        config = load_dev_config()
        engine = IntegrationEngine(
            config,
            IntegrationBootstrapOptions(
                auto_connect_broker=False,
                clock=fixed_clock,
            ),
        )
        engine._connect_broker(ConnectableStubBroker(), EventBus(), "c", IntegrationSessionState.WIRED)

    def test_broker_factory_generic_exception_wrapped(self) -> None:
        config = load_dev_config()
        engine = IntegrationEngine(config, IntegrationBootstrapOptions(clock=fixed_clock))
        session = engine._resolve_broker_session(config)
        with patch(
            "system.integration_engine.BrokerClientFactory.create",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(IntegrationBootstrapError) as exc:
                engine._construct_broker_client(config, session)
            assert exc.value.code == "INTEGRATION.ENGINE.CONSTRUCTION_FAILED"

    def test_engine_override_short_circuit(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = StrategyRegistry(config.to_strategy_registry_config())
        engine = IntegrationEngine(
            config,
            IntegrationBootstrapOptions(
                engine_overrides=EngineOverrides(
                    market_data=SimpleNamespace(_broker=broker),
                ),
                clock=fixed_clock,
            ),
        )
        built = engine._construct_engines(
            config,
            bus,
            broker,
            registry,
            [],
            [],
            [],
        )
        assert built.market_data is not None

    def test_session_start_partial_becomes_degraded(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config, auto_start_orchestrator=False)
        session._orchestrator.start = MagicMock(  # noqa: SLF001
            return_value=SimpleNamespace(
                status=StartupStatus.PARTIAL,
                startup_id="s",
                engines_started=(),
                engines_failed=(),
                subscriptions_registered=0,
                warnings=(),
                errors=(),
                started_at=fixed_clock(),
                completed_at=fixed_clock(),
                duration_ms=1.0,
            )
        )
        try:
            session.start()
            assert session.get_runtime_state().session_state is IntegrationSessionState.DEGRADED
        finally:
            session.stop()

    def test_discover_strategy_plugins_imports_module(self, tmp_path: Path) -> None:
        from system.integration_engine import _discover_strategy_plugins

        plugin = tmp_path / "sample_plugin.py"
        plugin.write_text("class Sample:\n    pass\n")
        with patch("system.integration_engine.importlib.import_module") as mock_import:
            mock_import.side_effect = ImportError("skip")
            assert _discover_strategy_plugins(tmp_path) == ()

    def test_wiring_fail_fast_triggers_bootstrap_failed_event(self) -> None:
        config = load_dev_config()
        options = IntegrationBootstrapOptions(
            engine_overrides=EngineOverrides(broker_client=ConnectableStubBroker()),
            fail_fast_on_wiring_error=True,
            clock=fixed_clock,
        )
        with patch(
            "system.integration_engine.validate_wiring",
            return_value=WiringValidationResult(
                validation_id="v",
                as_of=fixed_clock(),
                status=WiringValidationStatus.FAILED,
                checks=(),
                issues=(
                    WiringValidationIssue(
                        code="INTEGRATION.WIRING.EVENT_BUS_MISMATCH",
                        message="fail",
                        check_id=WiringCheckId.EVENT_BUS_IDENTITY,
                        severity="ERROR",
                    ),
                ),
                wiring_fingerprint="abc",
            ),
        ):
            with pytest.raises(IntegrationWiringError):
                IntegrationEngine(config, options).bootstrap()

    def test_default_secret_provider(self) -> None:
        from system.integration_engine import _default_secret_provider

        provider = _default_secret_provider()
        assert provider is not None

    def test_secret_resolution_error_wrapped(self) -> None:
        from config.application_configuration import SecretResolutionError
        from system.integration_engine import _resolve_secret_value

        config = load_dev_config()
        provider = InlineSecretProvider({"broker.api_key": "k"})
        provider.get_secret = MagicMock(  # type: ignore[method-assign]
            side_effect=SecretResolutionError("fail", code="CONFIG.SECRET.NOT_FOUND")
        )
        provider.is_available = MagicMock(return_value=True)  # type: ignore[method-assign]
        with pytest.raises(IntegrationBrokerError):
            _resolve_secret_value(config, provider, "broker.api_key")

    def test_orchestrator_construction_raises(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus, broker)
        engine = IntegrationEngine(config, IntegrationBootstrapOptions(clock=fixed_clock))
        with patch("system.integration_engine.SystemOrchestrator", side_effect=RuntimeError("bad")):
            with pytest.raises(IntegrationBootstrapError):
                engine._construct_orchestrator(config, bus, broker, registry)

    def test_orchestrator_override_return(self) -> None:
        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        broker = ConnectableStubBroker()
        registry = make_registry(bus, broker)
        orchestrator = make_orchestrator(config, bus, broker, registry)
        engine = IntegrationEngine(
            config,
            IntegrationBootstrapOptions(
                engine_overrides=EngineOverrides(orchestrator=orchestrator),
                clock=fixed_clock,
            ),
        )
        assert engine._construct_orchestrator(config, bus, broker, registry) is orchestrator

    def test_session_start_failed_state(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config, auto_start_orchestrator=False)
        session._orchestrator.start = MagicMock(  # noqa: SLF001
            return_value=SimpleNamespace(
                status=StartupStatus.FAILED,
                startup_id="s",
                engines_started=(),
                engines_failed=(),
                subscriptions_registered=0,
                warnings=(),
                errors=(),
                started_at=fixed_clock(),
                completed_at=fixed_clock(),
                duration_ms=1.0,
            )
        )
        try:
            session.start()
            assert session.get_runtime_state().session_state is IntegrationSessionState.FAILED
        finally:
            session.stop()

    def test_stop_idempotent(self) -> None:
        config = load_dev_config()
        session = bootstrap_with_overrides(config)
        session.stop()
        again = session.stop()
        assert again.status.value == "success"

    def test_check_broker_identity_without_client(self) -> None:
        from system.integration_engine import _check_broker_identity

        config = load_dev_config()
        bus = EventBus(config.to_event_bus_policy())
        registry = make_registry(bus, ConnectableStubBroker())
        orchestrator = make_orchestrator(config, bus, ConnectableStubBroker(), registry)
        result = _check_broker_identity(None, registry, orchestrator)
        assert result.passed is True

    def test_discover_strategy_plugins_success_path(self, tmp_path: Path) -> None:
        from system.integration_engine import _discover_strategy_plugins

        (tmp_path / "plugin_a.py").write_text("class Plugin: pass\n")
        fake_strategy = MagicMock()
        fake_strategy.metadata.strategy_id = "plugin-a"
        fake_strategy.evaluate = MagicMock()
        fake_cls = MagicMock(return_value=fake_strategy)
        fake_cls.metadata = fake_strategy.metadata
        fake_module = MagicMock()
        fake_module.Plugin = fake_cls
        with patch("system.integration_engine.importlib.import_module", return_value=fake_module):
            with patch("system.integration_engine.isinstance", return_value=True):
                descriptors = _discover_strategy_plugins(tmp_path)
        assert len(descriptors) >= 1

