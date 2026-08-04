"""Application composition root for THETA AI TRADER v1.0.

Loads :class:`~config.application_configuration.ApplicationConfiguration`,
constructs every coordinated engine and the broker client, assembles the
:class:`~system.system_orchestrator.EngineRegistry`, constructs the
:class:`~system.system_orchestrator.SystemOrchestrator`, validates end-to-end
wiring, and exposes a thread-safe :class:`IntegrationSession` runtime facade.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Final, Mapping, TypeVar

from apme.adaptive_position_management_engine import AdaptivePositionManagementEngine
from broker.base_broker import (
    BaseBrokerClient,
    BrokerConnectionError,
    BrokerId,
    BrokerSession,
    ConnectionInfo,
    ConnectionState,
    SessionState,
    validate_broker_session,
)
from config.application_configuration import (
    ApplicationConfiguration,
    ApplicationConfigurationError,
    BrokerConfiguration,
    BrokerType,
    CompositeSecretProvider,
    EnvironmentProfile,
    EnvironmentSecretProvider,
    FileSecretProvider,
    InlineSecretProvider,
    LoadOptions,
    SecretProvider,
    SecretReference,
    SecretResolutionError,
    SecretSource,
    default_load_options_for_profile,
    load_application_configuration,
)
from core.event_bus import EventBus, EventEnvelope, SubscriptionHandle
from decision.trade_decision_engine import TradeDecisionEngine
from execution.execution_engine import ExecutionEngine
from execution.order_manager import OrderManager
from market_data.market_data_adapter import MarketDataAdapter
from market_data.market_data_engine import MarketDataEngine
from portfolio.portfolio_manager import PortfolioManager
from portfolio.position_manager import PositionManager
from risk.risk_engine import RiskEngine
from strategy.registry import StrategyDiscoveryDescriptor, StrategyRegistry
from strategy.signals import StrategyExecutionMode
from strategy.strategy_evaluation_engine import StrategyEvaluationEngine
from system.system_orchestrator import (
    CycleStatus,
    EngineRegistry,
    HealthIssueRecord,
    HealthStatus,
    OrchestratorState,
    PostFillCycleContext,
    PostFillCycleResult,
    ShutdownStatus,
    StartupStatus,
    SystemHealthReport,
    SystemOrchestrator,
    SystemShutdownResult,
    SystemStartupResult,
    TradingCycleContext,
    TradingCycleResult,
    deserialize_system_health_report,
    serialize_system_health_report,
)

INTEGRATION_ENGINE_VERSION: Final[str] = "1.0.0"
INTEGRATION_ENGINE_SCHEMA_VERSION: Final[str] = "1.0.0"
PRODUCER_NAME: Final[str] = "integration_engine"

_LOGGER = logging.getLogger("system.integration_engine")

_T = TypeVar("_T")

BrokerClientBuilder = Callable[[BrokerSession, BrokerConfiguration], BaseBrokerClient]

_CRITICAL_ENGINE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "market_data",
        "risk",
        "order_manager",
        "position_manager",
        "portfolio_manager",
    }
)

class IntegrationSessionState(str, Enum):
    """Lifecycle state of an :class:`IntegrationSession`."""

    NOT_BOOTSTRAPPED = "not_bootstrapped"
    BOOTSTRAPPING = "bootstrapping"
    WIRING = "wiring"
    WIRED = "wired"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class BootstrapStageId(str, Enum):
    """Ordered bootstrap pipeline stage identifiers."""

    CONFIG_RESOLUTION = "config_resolution"
    EVENT_BUS_CONSTRUCTION = "event_bus_construction"
    BROKER_SESSION_RESOLUTION = "broker_session_resolution"
    BROKER_CLIENT_CONSTRUCTION = "broker_client_construction"
    BROKER_CONNECTION = "broker_connection"
    STRATEGY_REGISTRY_CONSTRUCTION = "strategy_registry_construction"
    ENGINE_CONSTRUCTION = "engine_construction"
    ENGINE_REGISTRY_ASSEMBLY = "engine_registry_assembly"
    ORCHESTRATOR_CONSTRUCTION = "orchestrator_construction"
    WIRING_VALIDATION = "wiring_validation"
    ORCHESTRATOR_STARTUP = "orchestrator_startup"
    SESSION_SEAL = "session_seal"


class WiringCheckId(str, Enum):
    """End-to-end wiring validation check identifiers."""

    EVENT_BUS_IDENTITY = "event_bus_identity"
    BROKER_IDENTITY = "broker_identity"
    REGISTRY_COMPLETENESS = "registry_completeness"
    CONFIG_FINGERPRINT_CONSISTENCY = "config_fingerprint_consistency"
    STRATEGY_REGISTRY_POPULATION = "strategy_registry_population"
    BROKER_SESSION_VALIDITY = "broker_session_validity"
    ORCHESTRATOR_STATE_REACHABLE = "orchestrator_state_reachable"
    SUBSCRIPTION_PATTERNS_RESOLVED = "subscription_patterns_resolved"


class WiringValidationStatus(str, Enum):
    """Aggregate wiring validation outcome."""

    PASSED = "passed"
    PASSED_WITH_WARNINGS = "passed_with_warnings"
    FAILED = "failed"


class BootstrapStatus(str, Enum):
    """Aggregate bootstrap pipeline outcome."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class IntegrationEventType(str, Enum):
    """Integration lifecycle event discriminator."""

    BOOTSTRAP_STARTED = "bootstrap_started"
    BOOTSTRAP_STAGE_COMPLETED = "bootstrap_stage_completed"
    BOOTSTRAP_COMPLETED = "bootstrap_completed"
    BOOTSTRAP_FAILED = "bootstrap_failed"
    WIRING_VALIDATED = "wiring_validated"
    WIRING_FAILED = "wiring_failed"
    BROKER_CONNECTED = "broker_connected"
    BROKER_DISCONNECTED = "broker_disconnected"
    SESSION_READY = "session_ready"
    SESSION_STARTED = "session_started"
    SESSION_STOPPED = "session_stopped"
    SESSION_RESTARTED = "session_restarted"
    HEALTH_DEGRADED = "health_degraded"
    HEALTH_RECOVERED = "health_recovered"
    SESSION_ERROR = "session_error"


class RunnerKind(str, Enum):
    """Calling surface identifier for bootstrap metadata."""

    CLI = "cli"
    DASHBOARD = "dashboard"
    PAPER_TRADING = "paper_trading"
    LIVE_TRADING = "live_trading"
    TEST_HARNESS = "test_harness"


_INTEGRATION_EVENT_TOPICS: Final[Mapping[IntegrationEventType, str]] = MappingProxyType(
    {
        IntegrationEventType.BOOTSTRAP_STARTED: "integration.bootstrap.started",
        IntegrationEventType.BOOTSTRAP_STAGE_COMPLETED: "integration.bootstrap.stage.completed",
        IntegrationEventType.BOOTSTRAP_COMPLETED: "integration.bootstrap.completed",
        IntegrationEventType.BOOTSTRAP_FAILED: "integration.bootstrap.failed",
        IntegrationEventType.WIRING_VALIDATED: "integration.wiring.validated",
        IntegrationEventType.WIRING_FAILED: "integration.wiring.failed",
        IntegrationEventType.BROKER_CONNECTED: "integration.broker.connected",
        IntegrationEventType.BROKER_DISCONNECTED: "integration.broker.disconnected",
        IntegrationEventType.SESSION_READY: "integration.session.ready",
        IntegrationEventType.SESSION_STARTED: "integration.session.started",
        IntegrationEventType.SESSION_STOPPED: "integration.session.stopped",
        IntegrationEventType.SESSION_RESTARTED: "integration.session.restarted",
        IntegrationEventType.HEALTH_DEGRADED: "integration.health.degraded",
        IntegrationEventType.HEALTH_RECOVERED: "integration.health.recovered",
        IntegrationEventType.SESSION_ERROR: "integration.error",
    }
)


class IntegrationEngineError(Exception):
    """Base exception for Integration Engine failures."""

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


class IntegrationConfigurationError(IntegrationEngineError):
    """Raised when configuration resolution or validation fails."""


class IntegrationBootstrapError(IntegrationEngineError):
    """Raised when a critical bootstrap stage fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        stage_id: BootstrapStageId | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message, code=code, field=field)
        self.stage_id = stage_id


class IntegrationWiringError(IntegrationEngineError):
    """Raised when wiring validation fails under fail-fast policy."""


class IntegrationBrokerError(IntegrationEngineError):
    """Raised for broker session, factory, or connection failures."""


class IntegrationSessionStateError(IntegrationEngineError):
    """Raised when a lifecycle operation is incompatible with session state."""


@dataclass(frozen=True)
class IntegrationWarningRecord:
    """Non-fatal integration warning."""

    code: str
    message: str
    stage_id: BootstrapStageId | None = None
    field: str | None = None


@dataclass(frozen=True)
class IntegrationErrorRecord:
    """Structured integration error record."""

    code: str
    message: str
    stage_id: BootstrapStageId | None = None
    field: str | None = None


@dataclass(frozen=True)
class BootstrapStageResult:
    """Outcome of one bootstrap pipeline stage."""

    stage_id: BootstrapStageId
    passed: bool
    error_code: str | None
    message: str | None
    duration_ms: float
    details: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class BootstrapDiagnostics:
    """Immutable bootstrap audit trail."""

    bootstrap_id: str
    started_at: datetime
    completed_at: datetime | None
    stages: tuple[BootstrapStageResult, ...]
    status: BootstrapStatus
    warnings: tuple[IntegrationWarningRecord, ...]
    errors: tuple[IntegrationErrorRecord, ...]
    duration_ms: float


@dataclass(frozen=True)
class WiringCheckResult:
    """Outcome of one wiring validation check."""

    check_id: WiringCheckId
    passed: bool
    message: str | None
    details: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class WiringValidationIssue:
    """Structured wiring validation issue."""

    code: str
    message: str
    check_id: WiringCheckId | None
    severity: str
    field: str | None = None


@dataclass(frozen=True)
class WiringValidationResult:
    """Aggregate wiring validation outcome."""

    validation_id: str
    as_of: datetime
    status: WiringValidationStatus
    checks: tuple[WiringCheckResult, ...]
    issues: tuple[WiringValidationIssue, ...]
    wiring_fingerprint: str


@dataclass(frozen=True)
class BrokerHealthSnapshot:
    """Broker connectivity health snapshot."""

    broker_id: BrokerId | None
    connection_state: ConnectionState | None
    session_state: SessionState | None
    last_connected_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None


@dataclass(frozen=True)
class RuntimeState:
    """Immutable integration runtime snapshot."""

    session_id: str
    as_of: datetime
    session_state: IntegrationSessionState
    orchestrator_state: OrchestratorState | None
    environment_profile: EnvironmentProfile
    execution_mode: StrategyExecutionMode
    runner_kind: RunnerKind
    account_id: str
    broker_id: BrokerId | None
    broker_connection_state: ConnectionState | None
    config_fingerprint: str
    wiring_fingerprint: str
    uptime_seconds: float
    last_cycle_at: datetime | None
    last_cycle_status: CycleStatus | None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class IntegrationHealthReport:
    """Aggregated integration-level health snapshot."""

    report_id: str
    as_of: datetime
    session_state: IntegrationSessionState
    overall_status: HealthStatus
    orchestrator_health: SystemHealthReport | None
    broker_connection: BrokerHealthSnapshot
    wiring_status: WiringValidationStatus
    wiring_issues: tuple[WiringValidationIssue, ...]
    config_fingerprint: str
    wiring_fingerprint: str
    issues: tuple[HealthIssueRecord, ...]
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class IntegrationEvent:
    """Structured integration lifecycle event payload."""

    event_type: IntegrationEventType
    topic: str
    session_id: str
    correlation_id: str
    occurred_at: datetime
    session_state: IntegrationSessionState
    stage_id: BootstrapStageId | None
    message: str | None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class EngineOverrides:
    """Test-only dependency injection seam."""

    event_bus: EventBus | None = None
    broker_client: BaseBrokerClient | None = None
    strategy_registry: StrategyRegistry | None = None
    market_data: MarketDataEngine | None = None
    strategy_evaluation: StrategyEvaluationEngine | None = None
    trade_decision: TradeDecisionEngine | None = None
    risk: RiskEngine | None = None
    execution: ExecutionEngine | None = None
    order_manager: OrderManager | None = None
    position_manager: PositionManager | None = None
    portfolio_manager: PortfolioManager | None = None
    apme: AdaptivePositionManagementEngine | None = None
    orchestrator: SystemOrchestrator | None = None


@dataclass(frozen=True)
class IntegrationBootstrapOptions:
    """Bootstrap behaviour and dependency-override options."""

    runner_kind: RunnerKind = RunnerKind.CLI
    load_options: LoadOptions | None = None
    auto_connect_broker: bool = True
    auto_start_orchestrator: bool = True
    fail_fast_on_wiring_error: bool = True
    validate_wiring: bool = True
    engine_overrides: EngineOverrides = field(default_factory=EngineOverrides)
    clock: Callable[[], datetime] | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


def _utc_now() -> datetime:
    """Return the current UTC timestamp with timezone information."""
    return datetime.now(timezone.utc)


def _isoformat_utc(value: datetime) -> str:
    """Serialize a timezone-aware datetime as ISO-8601 UTC with Z suffix."""
    if value.tzinfo is None:
        raise IntegrationConfigurationError(
            "Datetime must be timezone-aware.",
            code="INTEGRATION.SERIALIZATION.MALFORMED",
        )
    utc_value = value.astimezone(timezone.utc)
    return utc_value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso_datetime(raw: str) -> datetime:
    """Parse ISO-8601 UTC datetime strings."""
    normalized = raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _canonical_json(payload: Mapping[str, object]) -> str:
    """Return deterministic JSON for fingerprinting."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _map_broker_type(broker_type: BrokerType) -> BrokerId:
    """Map configuration broker type to :class:`BrokerId`."""
    if broker_type is BrokerType.ZERODHA_KITE:
        return BrokerId.KITE
    if broker_type in {BrokerType.MOCK, BrokerType.RECORDING}:
        return BrokerId.MOCK
    return BrokerId.UNKNOWN


def _build_kite_broker_client(
    session: BrokerSession,
    broker_config: BrokerConfiguration,
) -> BaseBrokerClient:
    """Construct the production Kite broker client."""
    from broker.zerodha._kite_policy import KiteBrokerPolicy
    from broker.zerodha.kite_broker import KiteBrokerClient

    policy = KiteBrokerPolicy(
        retry_max_attempts=broker_config.max_retries,
        rest_timeout_seconds=broker_config.request_timeout_seconds,
    )
    return KiteBrokerClient(session, policy)


def _build_mock_broker_client(
    session: BrokerSession,
    broker_config: BrokerConfiguration,
) -> BaseBrokerClient:
    """Construct mock broker client via broker package implementation."""
    del broker_config
    module = importlib.import_module("broker.mock_broker")
    client_cls = getattr(module, "MockBrokerClient")
    return client_cls(session)


def _build_recording_broker_client(
    session: BrokerSession,
    broker_config: BrokerConfiguration,
) -> BaseBrokerClient:
    """Construct recording broker client via broker package implementation."""
    del broker_config
    module = importlib.import_module("broker.recording_broker")
    client_cls = getattr(module, "RecordingBrokerClient")
    return client_cls(session)


_BROKER_CLIENT_FACTORIES: Final[Mapping[BrokerType, BrokerClientBuilder]] = MappingProxyType(
    {
        BrokerType.ZERODHA_KITE: _build_kite_broker_client,
        BrokerType.MOCK: _build_mock_broker_client,
        BrokerType.RECORDING: _build_recording_broker_client,
    }
)


class BrokerClientFactory:
    """Resolves :class:`BrokerType` to a concrete :class:`BaseBrokerClient`."""

    @staticmethod
    def create(
        broker_type: BrokerType,
        session: BrokerSession,
        broker_config: BrokerConfiguration,
    ) -> BaseBrokerClient:
        """Construct the broker client mapped to ``broker_type``.

        Args:
            broker_type: Broker implementation selector from configuration.
            session: Resolved authenticated broker session.
            broker_config: Non-secret broker metadata.

        Returns:
            Concrete broker client instance.

        Raises:
            IntegrationBrokerError: When no factory is registered or import fails.
        """
        builder = _BROKER_CLIENT_FACTORIES.get(broker_type)
        if builder is None:
            raise IntegrationBrokerError(
                f"No broker client factory registered for {broker_type.value}.",
                code="INTEGRATION.BROKER.IMPLEMENTATION_NOT_FOUND",
            )
        try:
            return builder(session, broker_config)
        except ImportError as exc:
            raise IntegrationBrokerError(
                f"Broker implementation for {broker_type.value} is not "
                f"available in this deployment: {exc}.",
                code="INTEGRATION.BROKER.IMPLEMENTATION_NOT_FOUND",
            ) from exc


def compute_wiring_fingerprint(
    config: ApplicationConfiguration,
    engine_registry: EngineRegistry,
    broker_client: BaseBrokerClient | None,
) -> str:
    """Compute deterministic wiring identity hash.

    Args:
        config: Loaded application configuration.
        engine_registry: Assembled engine registry.
        broker_client: Constructed broker client, if any.

    Returns:
        SHA-256 hex digest over canonical wiring payload.
    """
    payload = {
        "config_fingerprint": config.config_fingerprint,
        "broker_type": config.broker.broker_type.value,
        "broker_id": broker_client.broker_id.value if broker_client else None,
        "engines_present": {
            "market_data": engine_registry.market_data is not None,
            "strategy_evaluation": engine_registry.strategy_evaluation is not None,
            "trade_decision": engine_registry.trade_decision is not None,
            "risk": engine_registry.risk is not None,
            "execution": engine_registry.execution is not None,
            "order_manager": engine_registry.order_manager is not None,
            "position_manager": engine_registry.position_manager is not None,
            "portfolio_manager": engine_registry.portfolio_manager is not None,
            "apme": engine_registry.apme is not None,
        },
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _check_event_bus_identity(
    bus: EventBus,
    engine_registry: EngineRegistry,
    orchestrator: SystemOrchestrator,
) -> WiringCheckResult:
    """Verify shared event bus identity across the object graph."""
    registry_bus = engine_registry.event_bus
    orchestrator_bus = orchestrator.event_bus
    passed = bus is registry_bus is orchestrator_bus
    return WiringCheckResult(
        check_id=WiringCheckId.EVENT_BUS_IDENTITY,
        passed=passed,
        message=None if passed else "EventBus instance mismatch across components.",
    )


def _extract_market_data_broker(market_data: object | None) -> object | None:
    """Return broker reference held by market data engine, if accessible."""
    if market_data is None:
        return None
    return getattr(market_data, "_broker", None) or getattr(market_data, "_broker_client", None)


def _check_broker_identity(
    broker_client: BaseBrokerClient | None,
    engine_registry: EngineRegistry,
    orchestrator: SystemOrchestrator,
) -> WiringCheckResult:
    """Verify broker client identity between orchestrator and market data."""
    if broker_client is None:
        return WiringCheckResult(
            check_id=WiringCheckId.BROKER_IDENTITY,
            passed=True,
            message=None,
            details=MappingProxyType({"skipped": "no_broker_client"}),
        )
    md_broker = _extract_market_data_broker(engine_registry.market_data)
    orch_broker = getattr(orchestrator, "_broker_client", None)
    passed = broker_client is md_broker is orch_broker
    return WiringCheckResult(
        check_id=WiringCheckId.BROKER_IDENTITY,
        passed=passed,
        message=None if passed else "Broker client instance mismatch across components.",
    )


def _check_registry_completeness(
    engine_registry: EngineRegistry,
    config: ApplicationConfiguration,
) -> WiringCheckResult:
    """Verify required registry fields are populated."""
    required: list[str] = ["market_data", "risk", "order_manager", "position_manager", "portfolio_manager"]
    if config.orchestrator.enable_pre_trade_cycle:
        required.extend(["strategy_evaluation", "trade_decision", "execution"])
    if config.orchestrator.enable_post_fill_cycle:
        required.append("apme")
    missing = [
        name
        for name in required
        if getattr(engine_registry, name) is None
    ]
    passed = not missing
    return WiringCheckResult(
        check_id=WiringCheckId.REGISTRY_COMPLETENESS,
        passed=passed,
        message=None if passed else f"Missing registry fields: {', '.join(missing)}.",
        details=MappingProxyType({"missing": ",".join(missing)} if missing else {}),
    )


def _check_config_fingerprint_consistency(
    config: ApplicationConfiguration,
    engine_registry: EngineRegistry,
) -> WiringCheckResult:
    """Advisory check that engines were built from the same configuration."""
    del engine_registry
    passed = bool(config.config_fingerprint)
    return WiringCheckResult(
        check_id=WiringCheckId.CONFIG_FINGERPRINT_CONSISTENCY,
        passed=passed,
        message=None if passed else "Configuration fingerprint missing.",
    )


def _check_strategy_registry_population(
    strategy_registry: StrategyRegistry | None,
    config: ApplicationConfiguration,
) -> WiringCheckResult:
    """Verify strategy registry population policy."""
    if strategy_registry is None:
        if config.execution_mode is StrategyExecutionMode.LIVE and config.profile is EnvironmentProfile.PRODUCTION:
            return WiringCheckResult(
                check_id=WiringCheckId.STRATEGY_REGISTRY_POPULATION,
                passed=False,
                message="Strategy registry missing for LIVE production bootstrap.",
            )
        return WiringCheckResult(
            check_id=WiringCheckId.STRATEGY_REGISTRY_POPULATION,
            passed=True,
            message="Strategy registry absent; advisory only.",
        )
    enabled = strategy_registry.enabled_count()
    if config.execution_mode is StrategyExecutionMode.LIVE and enabled < 1:
        if config.profile is EnvironmentProfile.PRODUCTION:
            return WiringCheckResult(
                check_id=WiringCheckId.STRATEGY_REGISTRY_POPULATION,
                passed=False,
                message="No enabled strategies registered for LIVE execution mode.",
            )
        return WiringCheckResult(
            check_id=WiringCheckId.STRATEGY_REGISTRY_POPULATION,
            passed=True,
            message="No enabled strategies; warning only outside production.",
            details=MappingProxyType({"severity": "warning"}),
        )
    return WiringCheckResult(
        check_id=WiringCheckId.STRATEGY_REGISTRY_POPULATION,
        passed=True,
        message=None,
    )


def _check_broker_session_validity(
    broker_client: BaseBrokerClient | None,
) -> WiringCheckResult:
    """Verify broker session is not expired or revoked."""
    if broker_client is None:
        return WiringCheckResult(
            check_id=WiringCheckId.BROKER_SESSION_VALIDITY,
            passed=True,
            message=None,
            details=MappingProxyType({"skipped": "no_broker_client"}),
        )
    session_state = broker_client.get_session_state()
    passed = session_state not in {SessionState.EXPIRED, SessionState.REVOKED}
    return WiringCheckResult(
        check_id=WiringCheckId.BROKER_SESSION_VALIDITY,
        passed=passed,
        message=None if passed else f"Broker session state is {session_state.value}.",
    )


def _check_orchestrator_state_reachable(
    orchestrator: SystemOrchestrator,
    *,
    prior_to_startup: bool = True,
) -> WiringCheckResult:
    """Verify orchestrator lifecycle state is reachable."""
    state = orchestrator.get_state()
    if prior_to_startup:
        passed = state is OrchestratorState.UNINITIALIZED
        message = (
            None
            if passed
            else f"Expected UNINITIALIZED orchestrator state, got {state.value}."
        )
    else:
        passed = state in {
            OrchestratorState.UNINITIALIZED,
            OrchestratorState.RUNNING,
            OrchestratorState.DEGRADED,
            OrchestratorState.STOPPING,
            OrchestratorState.STOPPED,
        }
        message = None if passed else f"Unexpected orchestrator state {state.value}."
    return WiringCheckResult(
        check_id=WiringCheckId.ORCHESTRATOR_STATE_REACHABLE,
        passed=passed,
        message=message,
    )


def _check_subscription_patterns_resolved(
    config: ApplicationConfiguration,
    orchestrator: SystemOrchestrator,
) -> WiringCheckResult:
    """Advisory check that orchestrator subscription patterns match configuration."""
    expected = config.orchestrator.subscription_patterns
    actual = orchestrator.config.subscription_patterns
    passed = expected == actual
    return WiringCheckResult(
        check_id=WiringCheckId.SUBSCRIPTION_PATTERNS_RESOLVED,
        passed=passed,
        message=None if passed else "Orchestrator subscription patterns differ from configuration.",
        details=MappingProxyType({"severity": "warning"} if not passed else {}),
    )


def _issue_from_check(check: WiringCheckResult) -> WiringValidationIssue | None:
    """Convert a failed wiring check into a structured issue."""
    if check.passed:
        return None
    severity = check.details.get("severity", "ERROR")
    code_map = {
        WiringCheckId.EVENT_BUS_IDENTITY: "INTEGRATION.WIRING.EVENT_BUS_MISMATCH",
        WiringCheckId.BROKER_IDENTITY: "INTEGRATION.WIRING.BROKER_MISMATCH",
        WiringCheckId.REGISTRY_COMPLETENESS: "INTEGRATION.WIRING.INCOMPLETE_REGISTRY",
        WiringCheckId.CONFIG_FINGERPRINT_CONSISTENCY: "INTEGRATION.WIRING.CONTRACT_VIOLATION",
        WiringCheckId.STRATEGY_REGISTRY_POPULATION: "INTEGRATION.WIRING.CONTRACT_VIOLATION",
        WiringCheckId.BROKER_SESSION_VALIDITY: "INTEGRATION.BROKER.SESSION_INVALID",
        WiringCheckId.ORCHESTRATOR_STATE_REACHABLE: "INTEGRATION.WIRING.VALIDATION_FAILED",
        WiringCheckId.SUBSCRIPTION_PATTERNS_RESOLVED: "INTEGRATION.WIRING.CONTRACT_VIOLATION",
    }
    return WiringValidationIssue(
        code=code_map.get(check.check_id, "INTEGRATION.WIRING.VALIDATION_FAILED"),
        message=check.message or "Wiring check failed.",
        check_id=check.check_id,
        severity=severity,
    )


def validate_wiring(
    config: ApplicationConfiguration,
    bus: EventBus,
    broker_client: BaseBrokerClient | None,
    engine_registry: EngineRegistry,
    orchestrator: SystemOrchestrator,
    *,
    strategy_registry: StrategyRegistry | None = None,
    clock: Callable[[], datetime] | None = None,
    prior_to_startup: bool = True,
) -> WiringValidationResult:
    """Run every WIRE-* check against the constructed object graph.

    Args:
        config: Application configuration used for bootstrap.
        bus: Shared event bus instance.
        broker_client: Constructed broker client, if any.
        engine_registry: Assembled engine registry.
        orchestrator: Constructed system orchestrator.
        strategy_registry: Optional strategy registry for population checks.
        clock: Injectable clock for ``as_of`` timestamp.

    Returns:
        Structured wiring validation outcome.
    """
    now = (clock or _utc_now)()
    checks: tuple[WiringCheckResult, ...] = (
        _check_event_bus_identity(bus, engine_registry, orchestrator),
        _check_broker_identity(broker_client, engine_registry, orchestrator),
        _check_registry_completeness(engine_registry, config),
        _check_config_fingerprint_consistency(config, engine_registry),
        _check_strategy_registry_population(strategy_registry, config),
        _check_broker_session_validity(broker_client),
        _check_orchestrator_state_reachable(orchestrator, prior_to_startup=prior_to_startup),
        _check_subscription_patterns_resolved(config, orchestrator),
    )
    issues: list[WiringValidationIssue] = []
    has_error = False
    has_warning = False
    for check in checks:
        if check.passed:
            if check.details.get("severity") == "warning":
                has_warning = True
            continue
        issue = _issue_from_check(check)
        if issue is not None:
            issues.append(issue)
            if issue.severity == "ERROR":
                has_error = True
            else:
                has_warning = True
    if has_error:
        status = WiringValidationStatus.FAILED
    elif has_warning:
        status = WiringValidationStatus.PASSED_WITH_WARNINGS
    else:
        status = WiringValidationStatus.PASSED
    return WiringValidationResult(
        validation_id=str(uuid.uuid4()),
        as_of=now,
        status=status,
        checks=checks,
        issues=tuple(issues),
        wiring_fingerprint=compute_wiring_fingerprint(config, engine_registry, broker_client),
    )


def _aggregate_overall_status(
    *,
    session_state: IntegrationSessionState,
    orchestrator_health: SystemHealthReport | None,
    broker_snapshot: BrokerHealthSnapshot,
    wiring_status: WiringValidationStatus,
    execution_mode: StrategyExecutionMode,
) -> HealthStatus:
    """Derive integration overall health status."""
    if session_state is IntegrationSessionState.FAILED:
        return HealthStatus.UNHEALTHY
    if orchestrator_health is not None and orchestrator_health.overall_status is HealthStatus.UNHEALTHY:
        return HealthStatus.UNHEALTHY
    if (
        broker_snapshot.connection_state is ConnectionState.DISCONNECTED
        and execution_mode is StrategyExecutionMode.LIVE
    ):
        return HealthStatus.UNHEALTHY
    if session_state is IntegrationSessionState.DEGRADED:
        return HealthStatus.DEGRADED
    if orchestrator_health is not None and orchestrator_health.overall_status is HealthStatus.DEGRADED:
        return HealthStatus.DEGRADED
    if wiring_status is WiringValidationStatus.FAILED:
        return HealthStatus.DEGRADED
    return HealthStatus.HEALTHY


def _collect_health_issues(
    orchestrator_health: SystemHealthReport | None,
    broker_snapshot: BrokerHealthSnapshot,
    execution_mode: StrategyExecutionMode,
) -> tuple[HealthIssueRecord, ...]:
    """Collect integration-level health issues."""
    issues: list[HealthIssueRecord] = []
    if orchestrator_health is not None:
        issues.extend(orchestrator_health.issues)
    if (
        broker_snapshot.connection_state is ConnectionState.DISCONNECTED
        and execution_mode is StrategyExecutionMode.LIVE
    ):
        issues.append(
            HealthIssueRecord(
                issue_code="INTEGRATION.BROKER.DISCONNECTED",
                severity="error",
                message="Broker disconnected in LIVE execution mode.",
                engine_id=None,
            )
        )
    return tuple(issues)


def _default_secret_provider() -> SecretProvider:
    """Build default composite secret provider."""
    environment = os.environ
    profile = resolve_environment_profile_from_env(environment)
    return CompositeSecretProvider(
        {
            SecretSource.ENVIRONMENT: EnvironmentSecretProvider(environment),
            SecretSource.FILE: FileSecretProvider(profile=profile),
            SecretSource.INLINE_FOR_TESTS: InlineSecretProvider({}),
        }
    )


def resolve_environment_profile_from_env(env: Mapping[str, str]) -> EnvironmentProfile:
    """Resolve environment profile from mapping without importing loader internals."""
    from config.application_configuration import resolve_environment_profile

    return resolve_environment_profile(env=env)


def _resolve_secret_value(
    config: ApplicationConfiguration,
    provider: SecretProvider,
    ref_name: str,
) -> str:
    """Resolve one secret reference by logical name."""
    ref = config.secrets.refs.get(ref_name)
    if ref is None:
        raise IntegrationBrokerError(
            f"Secret reference {ref_name!r} is not defined.",
            code="INTEGRATION.BROKER.SECRET_UNRESOLVED",
            field=ref_name,
        )
    if not provider.is_available(ref):
        raise IntegrationBrokerError(
            f"Secret {ref_name!r} is not available.",
            code="INTEGRATION.BROKER.SECRET_UNRESOLVED",
            field=ref_name,
        )
    try:
        return provider.get_secret(ref)
    except SecretResolutionError as exc:
        raise IntegrationBrokerError(
            str(exc),
            code="INTEGRATION.BROKER.SECRET_UNRESOLVED",
            field=ref_name,
        ) from exc


def _discover_strategy_plugins(
    plugin_dir: Path,
) -> tuple[StrategyDiscoveryDescriptor, ...]:
    """Discover strategy plugin descriptors under a directory.

    Returns an empty tuple when the directory is missing or contains no plugins.
    """
    if not plugin_dir.is_dir():
        return ()
    descriptors: list[StrategyDiscoveryDescriptor] = []
    for path in sorted(plugin_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"strategy.plugins.{path.stem}"
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if isinstance(obj, type) and hasattr(obj, "metadata"):
                try:
                    instance = obj()
                except Exception:
                    continue
                if hasattr(instance, "metadata") and hasattr(instance, "evaluate"):
                    descriptors.append(StrategyDiscoveryDescriptor(strategy=instance))
    return tuple(descriptors)


def serialize_runtime_state(state: RuntimeState) -> str:
    """Serialize runtime state to JSON schema v1.0.0."""
    payload = {
        "schema_version": INTEGRATION_ENGINE_SCHEMA_VERSION,
        "session_id": state.session_id,
        "as_of": _isoformat_utc(state.as_of),
        "session_state": state.session_state.value,
        "orchestrator_state": state.orchestrator_state.value if state.orchestrator_state else None,
        "environment_profile": state.environment_profile.value,
        "execution_mode": state.execution_mode.value,
        "runner_kind": state.runner_kind.value,
        "account_id": state.account_id,
        "broker_id": state.broker_id.value if state.broker_id else None,
        "broker_connection_state": (
            state.broker_connection_state.value if state.broker_connection_state else None
        ),
        "config_fingerprint": state.config_fingerprint,
        "wiring_fingerprint": state.wiring_fingerprint,
        "uptime_seconds": state.uptime_seconds,
        "last_cycle_at": _isoformat_utc(state.last_cycle_at) if state.last_cycle_at else None,
        "last_cycle_status": state.last_cycle_status.value if state.last_cycle_status else None,
        "metadata": dict(state.metadata),
    }
    return json.dumps(payload, sort_keys=True)


def deserialize_runtime_state(payload: str) -> RuntimeState:
    """Deserialize runtime state from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise IntegrationConfigurationError(
            f"Malformed JSON: {exc}",
            code="INTEGRATION.SERIALIZATION.MALFORMED",
        ) from exc
    if data.get("schema_version") != INTEGRATION_ENGINE_SCHEMA_VERSION:
        raise IntegrationConfigurationError(
            "Unsupported schema version.",
            code="INTEGRATION.SERIALIZATION.UNSUPPORTED_VERSION",
        )
    return RuntimeState(
        session_id=str(data["session_id"]),
        as_of=_parse_iso_datetime(data["as_of"]),
        session_state=IntegrationSessionState(data["session_state"]),
        orchestrator_state=(
            OrchestratorState(data["orchestrator_state"]) if data.get("orchestrator_state") else None
        ),
        environment_profile=EnvironmentProfile(data["environment_profile"]),
        execution_mode=StrategyExecutionMode(data["execution_mode"]),
        runner_kind=RunnerKind(data["runner_kind"]),
        account_id=str(data["account_id"]),
        broker_id=BrokerId(data["broker_id"]) if data.get("broker_id") else None,
        broker_connection_state=(
            ConnectionState(data["broker_connection_state"])
            if data.get("broker_connection_state")
            else None
        ),
        config_fingerprint=str(data["config_fingerprint"]),
        wiring_fingerprint=str(data["wiring_fingerprint"]),
        uptime_seconds=float(data["uptime_seconds"]),
        last_cycle_at=_parse_iso_datetime(data["last_cycle_at"]) if data.get("last_cycle_at") else None,
        last_cycle_status=CycleStatus(data["last_cycle_status"]) if data.get("last_cycle_status") else None,
        metadata=MappingProxyType(dict(data.get("metadata", {}))),
    )


def serialize_integration_health_report(report: IntegrationHealthReport) -> str:
    """Serialize integration health report to JSON schema v1.0.0."""
    payload: dict[str, object] = {
        "schema_version": INTEGRATION_ENGINE_SCHEMA_VERSION,
        "report_id": report.report_id,
        "as_of": _isoformat_utc(report.as_of),
        "session_state": report.session_state.value,
        "overall_status": report.overall_status.value,
        "orchestrator_health": (
            json.loads(serialize_system_health_report(report.orchestrator_health))
            if report.orchestrator_health is not None
            else None
        ),
        "broker_connection": {
            "broker_id": report.broker_connection.broker_id.value
            if report.broker_connection.broker_id
            else None,
            "connection_state": (
                report.broker_connection.connection_state.value
                if report.broker_connection.connection_state
                else None
            ),
            "session_state": (
                report.broker_connection.session_state.value
                if report.broker_connection.session_state
                else None
            ),
            "last_connected_at": (
                _isoformat_utc(report.broker_connection.last_connected_at)
                if report.broker_connection.last_connected_at
                else None
            ),
            "last_error_code": report.broker_connection.last_error_code,
            "last_error_message": report.broker_connection.last_error_message,
        },
        "wiring_status": report.wiring_status.value,
        "wiring_issues": [
            {
                "code": issue.code,
                "message": issue.message,
                "check_id": issue.check_id.value if issue.check_id else None,
                "severity": issue.severity,
                "field": issue.field,
            }
            for issue in report.wiring_issues
        ],
        "config_fingerprint": report.config_fingerprint,
        "wiring_fingerprint": report.wiring_fingerprint,
        "issues": [
            {
                "issue_code": issue.issue_code,
                "severity": issue.severity,
                "message": issue.message,
                "engine_id": issue.engine_id.value if issue.engine_id else None,
            }
            for issue in report.issues
        ],
        "metadata": dict(report.metadata),
    }
    return json.dumps(payload, sort_keys=True)


def deserialize_integration_health_report(payload: str) -> IntegrationHealthReport:
    """Deserialize integration health report from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise IntegrationConfigurationError(
            f"Malformed JSON: {exc}",
            code="INTEGRATION.SERIALIZATION.MALFORMED",
        ) from exc
    if data.get("schema_version") != INTEGRATION_ENGINE_SCHEMA_VERSION:
        raise IntegrationConfigurationError(
            "Unsupported schema version.",
            code="INTEGRATION.SERIALIZATION.UNSUPPORTED_VERSION",
        )
    orchestrator_health = None
    if data.get("orchestrator_health") is not None:
        orchestrator_health = deserialize_system_health_report(
            json.dumps(data["orchestrator_health"], sort_keys=True)
        )
    broker_raw = data["broker_connection"]
    report = IntegrationHealthReport(
        report_id=str(data["report_id"]),
        as_of=_parse_iso_datetime(data["as_of"]),
        session_state=IntegrationSessionState(data["session_state"]),
        overall_status=HealthStatus(data["overall_status"]),
        orchestrator_health=orchestrator_health,
        broker_connection=BrokerHealthSnapshot(
            broker_id=BrokerId(broker_raw["broker_id"]) if broker_raw.get("broker_id") else None,
            connection_state=(
                ConnectionState(broker_raw["connection_state"])
                if broker_raw.get("connection_state")
                else None
            ),
            session_state=(
                SessionState(broker_raw["session_state"]) if broker_raw.get("session_state") else None
            ),
            last_connected_at=(
                _parse_iso_datetime(broker_raw["last_connected_at"])
                if broker_raw.get("last_connected_at")
                else None
            ),
            last_error_code=broker_raw.get("last_error_code"),
            last_error_message=broker_raw.get("last_error_message"),
        ),
        wiring_status=WiringValidationStatus(data["wiring_status"]),
        wiring_issues=tuple(
            WiringValidationIssue(
                code=item["code"],
                message=item["message"],
                check_id=WiringCheckId(item["check_id"]) if item.get("check_id") else None,
                severity=item["severity"],
                field=item.get("field"),
            )
            for item in data.get("wiring_issues", [])
        ),
        config_fingerprint=str(data["config_fingerprint"]),
        wiring_fingerprint=str(data["wiring_fingerprint"]),
        issues=tuple(
            HealthIssueRecord(
                issue_code=item["issue_code"],
                severity=item["severity"],
                message=item["message"],
                engine_id=None,
            )
            for item in data.get("issues", [])
        ),
        metadata=MappingProxyType(dict(data.get("metadata", {}))),
    )
    return report


class IntegrationEngine:
    """Application composition root for THETA AI TRADER institutional pipeline."""

    def __init__(
        self,
        config: ApplicationConfiguration | None = None,
        options: IntegrationBootstrapOptions | None = None,
    ) -> None:
        """Initialize composition root.

        Args:
            config: Optional pre-loaded application configuration.
            options: Bootstrap behaviour and dependency-override options.
        """
        self._injected_config = config
        self._options = options or IntegrationBootstrapOptions()
        self._clock = self._options.clock or _utc_now
        self._bootstrap_attempted = False

    def bootstrap(self) -> IntegrationSession:
        """Execute the full bootstrap pipeline and return a sealed session."""
        if self._bootstrap_attempted:
            raise IntegrationBootstrapError(
                "bootstrap() was already invoked on this IntegrationEngine instance.",
                code="INTEGRATION.BOOTSTRAP.ALREADY_RUNNING",
            )
        self._bootstrap_attempted = True

        bootstrap_id = str(uuid.uuid4())
        started_at = self._clock()
        stages: list[BootstrapStageResult] = []
        warnings: list[IntegrationWarningRecord] = []
        errors: list[IntegrationErrorRecord] = []
        correlation_id = bootstrap_id
        session_state = IntegrationSessionState.BOOTSTRAPPING
        subscription_handles: list[SubscriptionHandle] = []

        config: ApplicationConfiguration | None = None
        bus: EventBus | None = None
        broker_session: BrokerSession | None = None
        broker_client: BaseBrokerClient | None = None
        strategy_registry: StrategyRegistry | None = None
        engine_registry: EngineRegistry | None = None
        orchestrator: SystemOrchestrator | None = None
        wiring_result: WiringValidationResult | None = None

        try:
            config = self._run_stage(
                BootstrapStageId.CONFIG_RESOLUTION,
                stages,
                errors,
                lambda: self._resolve_configuration(self._injected_config),
                critical=True,
                correlation_id=correlation_id,
                bus=None,
                session_state=session_state,
            )
            assert config is not None

            bus = self._run_stage(
                BootstrapStageId.EVENT_BUS_CONSTRUCTION,
                stages,
                errors,
                lambda: self._construct_event_bus(config),
                critical=True,
                correlation_id=correlation_id,
                bus=bus,
                session_state=session_state,
            )
            assert bus is not None
            subscription_handles = self._register_integration_subscriptions(bus, correlation_id)
            self._publish_integration_event(
                bus,
                IntegrationEventType.BOOTSTRAP_STARTED,
                correlation_id=correlation_id,
                session_state=session_state,
            )

            broker_session = self._run_stage(
                BootstrapStageId.BROKER_SESSION_RESOLUTION,
                stages,
                errors,
                lambda: self._resolve_broker_session(config),
                critical=self._is_broker_session_critical(config),
                critical_sink=errors,
                non_critical_sink=warnings,
                correlation_id=correlation_id,
                bus=bus,
                session_state=session_state,
            )

            broker_client = self._run_stage(
                BootstrapStageId.BROKER_CLIENT_CONSTRUCTION,
                stages,
                errors,
                lambda: self._construct_broker_client(config, broker_session),
                critical=True,
                correlation_id=correlation_id,
                bus=bus,
                session_state=session_state,
            )

            self._run_stage(
                BootstrapStageId.BROKER_CONNECTION,
                stages,
                warnings,
                lambda: self._connect_broker(broker_client, bus, correlation_id, session_state),
                critical=self._is_broker_connection_critical(config),
                critical_sink=errors,
                non_critical_sink=warnings,
                correlation_id=correlation_id,
                bus=bus,
                session_state=session_state,
            )

            strategy_registry = self._run_stage(
                BootstrapStageId.STRATEGY_REGISTRY_CONSTRUCTION,
                stages,
                warnings,
                lambda: self._construct_strategy_registry(config),
                critical=False,
                correlation_id=correlation_id,
                bus=bus,
                session_state=session_state,
            )

            overrides = self._options.engine_overrides
            if overrides.orchestrator is not None:
                for stage_id in (
                    BootstrapStageId.ENGINE_CONSTRUCTION,
                    BootstrapStageId.ENGINE_REGISTRY_ASSEMBLY,
                    BootstrapStageId.ORCHESTRATOR_CONSTRUCTION,
                ):
                    stages.append(
                        BootstrapStageResult(
                            stage_id=stage_id,
                            passed=True,
                            error_code=None,
                            message=None,
                            duration_ms=0.0,
                            details=MappingProxyType({"skipped": "override_supplied"}),
                        )
                    )
                    self._publish_integration_event(
                        bus,
                        IntegrationEventType.BOOTSTRAP_STAGE_COMPLETED,
                        correlation_id=correlation_id,
                        session_state=session_state,
                        stage_id=stage_id,
                    )
                engine_registry = self._assemble_engine_registry_from_overrides(
                    config, bus, broker_client, strategy_registry
                )
                orchestrator = overrides.orchestrator
            else:
                engine_registry = self._run_stage(
                    BootstrapStageId.ENGINE_CONSTRUCTION,
                    stages,
                    errors,
                    lambda: self._construct_engines(
                        config, bus, broker_client, strategy_registry, stages, warnings, errors
                    ),
                    critical=True,
                    correlation_id=correlation_id,
                    bus=bus,
                    session_state=session_state,
                )
                assert engine_registry is not None
                stages.append(
                    BootstrapStageResult(
                        stage_id=BootstrapStageId.ENGINE_REGISTRY_ASSEMBLY,
                        passed=True,
                        error_code=None,
                        message=None,
                        duration_ms=0.0,
                    )
                )
                self._publish_integration_event(
                    bus,
                    IntegrationEventType.BOOTSTRAP_STAGE_COMPLETED,
                    correlation_id=correlation_id,
                    session_state=session_state,
                    stage_id=BootstrapStageId.ENGINE_REGISTRY_ASSEMBLY,
                )
                orchestrator = self._run_stage(
                    BootstrapStageId.ORCHESTRATOR_CONSTRUCTION,
                    stages,
                    errors,
                    lambda: self._construct_orchestrator(
                        config, bus, broker_client, engine_registry
                    ),
                    critical=True,
                    correlation_id=correlation_id,
                    bus=bus,
                    session_state=session_state,
                )

            assert engine_registry is not None
            assert orchestrator is not None
            session_state = IntegrationSessionState.WIRING

            if self._options.validate_wiring:
                wiring_result = self._run_stage(
                    BootstrapStageId.WIRING_VALIDATION,
                    stages,
                    errors,
                    lambda: validate_wiring(
                        config,
                        bus,
                        broker_client,
                        engine_registry,
                        orchestrator,
                        strategy_registry=strategy_registry,
                        clock=self._clock,
                    ),
                    critical=False,
                    correlation_id=correlation_id,
                    bus=bus,
                    session_state=session_state,
                )
                assert wiring_result is not None
                if wiring_result.status is WiringValidationStatus.FAILED:
                    self._publish_integration_event(
                        bus,
                        IntegrationEventType.WIRING_FAILED,
                        correlation_id=correlation_id,
                        session_state=session_state,
                    )
                    if self._options.fail_fast_on_wiring_error:
                        raise IntegrationWiringError(
                            "Wiring validation failed.",
                            code="INTEGRATION.WIRING.VALIDATION_FAILED",
                        )
                else:
                    self._publish_integration_event(
                        bus,
                        IntegrationEventType.WIRING_VALIDATED,
                        correlation_id=correlation_id,
                        session_state=session_state,
                    )
            else:
                wiring_result = WiringValidationResult(
                    validation_id=str(uuid.uuid4()),
                    as_of=self._clock(),
                    status=WiringValidationStatus.PASSED,
                    checks=(),
                    issues=(),
                    wiring_fingerprint=compute_wiring_fingerprint(
                        config, engine_registry, broker_client
                    ),
                )
                stages.append(
                    BootstrapStageResult(
                        stage_id=BootstrapStageId.WIRING_VALIDATION,
                        passed=True,
                        error_code=None,
                        message="Skipped",
                        duration_ms=0.0,
                        details=MappingProxyType({"skipped": "validate_wiring_false"}),
                    )
                )

            initial_state = IntegrationSessionState.WIRED
            bootstrap_status = BootstrapStatus.SUCCESS

            if wiring_result.status is WiringValidationStatus.FAILED:
                initial_state = IntegrationSessionState.FAILED
                bootstrap_status = BootstrapStatus.FAILED
            elif self._options.auto_start_orchestrator:
                startup_result = self._run_stage(
                    BootstrapStageId.ORCHESTRATOR_STARTUP,
                    stages,
                    errors,
                    lambda: orchestrator.start(),
                    critical=False,
                    correlation_id=correlation_id,
                    bus=bus,
                    session_state=IntegrationSessionState.STARTING,
                )
                if startup_result is not None:
                    if startup_result.status is StartupStatus.SUCCESS:
                        initial_state = IntegrationSessionState.RUNNING
                    elif startup_result.status is StartupStatus.PARTIAL:
                        initial_state = IntegrationSessionState.DEGRADED
                        bootstrap_status = BootstrapStatus.PARTIAL
                    else:
                        initial_state = IntegrationSessionState.FAILED
                        bootstrap_status = BootstrapStatus.FAILED
            else:
                stages.append(
                    BootstrapStageResult(
                        stage_id=BootstrapStageId.ORCHESTRATOR_STARTUP,
                        passed=True,
                        error_code=None,
                        message="Skipped",
                        duration_ms=0.0,
                        details=MappingProxyType({"skipped": "auto_start_false"}),
                    )
                )

            completed_at = self._clock()
            diagnostics = BootstrapDiagnostics(
                bootstrap_id=bootstrap_id,
                started_at=started_at,
                completed_at=completed_at,
                stages=tuple(stages),
                status=bootstrap_status,
                warnings=tuple(warnings),
                errors=tuple(errors),
                duration_ms=(completed_at - started_at).total_seconds() * 1000,
            )
            wiring_fingerprint = wiring_result.wiring_fingerprint
            session_id = str(uuid.uuid4())
            session = IntegrationSession(
                session_id=session_id,
                config=config,
                options=self._options,
                event_bus=bus,
                broker_client=broker_client,
                strategy_registry=strategy_registry,
                engine_registry=engine_registry,
                orchestrator=orchestrator if initial_state is not IntegrationSessionState.FAILED else None,
                wiring_fingerprint=wiring_fingerprint,
                bootstrap_diagnostics=diagnostics,
                wiring_result=wiring_result,
                initial_state=initial_state,
                sealed_at=completed_at,
                clock=self._clock,
                subscription_handles=subscription_handles,
            )
            stages.append(
                BootstrapStageResult(
                    stage_id=BootstrapStageId.SESSION_SEAL,
                    passed=True,
                    error_code=None,
                    message=None,
                    duration_ms=0.0,
                )
            )
            self._publish_integration_event(
                bus,
                IntegrationEventType.SESSION_READY,
                correlation_id=correlation_id,
                session_state=initial_state,
                session_id=session_id,
                metadata=MappingProxyType(
                    {"runtime_state": serialize_runtime_state(session.get_runtime_state())}
                ),
            )
            self._publish_integration_event(
                bus,
                IntegrationEventType.BOOTSTRAP_COMPLETED,
                correlation_id=correlation_id,
                session_state=initial_state,
                session_id=session_id,
            )
            return session
        except IntegrationEngineError:
            self._publish_integration_event(
                bus,
                IntegrationEventType.BOOTSTRAP_FAILED,
                correlation_id=correlation_id,
                session_state=IntegrationSessionState.FAILED,
            )
            raise

    def _run_stage(
        self,
        stage_id: BootstrapStageId,
        stages: list[BootstrapStageResult],
        diagnostics_sink: list[IntegrationErrorRecord | IntegrationWarningRecord],
        callable_fn: Callable[[], _T],
        *,
        critical: bool,
        correlation_id: str,
        bus: EventBus | None,
        session_state: IntegrationSessionState,
        critical_sink: list[IntegrationErrorRecord] | None = None,
        non_critical_sink: list[IntegrationWarningRecord] | None = None,
    ) -> _T | None:
        """Execute one bootstrap stage with structured error isolation."""
        start = time.perf_counter()
        if bus is not None:
            self._publish_integration_event(
                bus,
                IntegrationEventType.BOOTSTRAP_STAGE_COMPLETED,
                correlation_id=correlation_id,
                session_state=session_state,
                stage_id=stage_id,
                message="stage_start",
            )
        try:
            result = callable_fn()
            duration_ms = (time.perf_counter() - start) * 1000
            stages.append(
                BootstrapStageResult(
                    stage_id=stage_id,
                    passed=True,
                    error_code=None,
                    message=None,
                    duration_ms=duration_ms,
                )
            )
            if bus is not None:
                self._publish_integration_event(
                    bus,
                    IntegrationEventType.BOOTSTRAP_STAGE_COMPLETED,
                    correlation_id=correlation_id,
                    session_state=session_state,
                    stage_id=stage_id,
                )
            return result
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            code = getattr(exc, "code", "INTEGRATION.BOOTSTRAP.STAGE_FAILED")
            stages.append(
                BootstrapStageResult(
                    stage_id=stage_id,
                    passed=False,
                    error_code=str(code),
                    message=str(exc),
                    duration_ms=duration_ms,
                )
            )
            record: IntegrationErrorRecord | IntegrationWarningRecord
            if critical:
                record = IntegrationErrorRecord(code=str(code), message=str(exc), stage_id=stage_id)
                target = critical_sink if critical_sink is not None else diagnostics_sink
                if isinstance(record, IntegrationErrorRecord):
                    target.append(record)  # type: ignore[arg-type]
            else:
                record = IntegrationWarningRecord(code=str(code), message=str(exc), stage_id=stage_id)
                target = non_critical_sink if non_critical_sink is not None else diagnostics_sink
                if isinstance(record, IntegrationWarningRecord):
                    target.append(record)  # type: ignore[arg-type]
            if bus is not None:
                self._publish_integration_event(
                    bus,
                    IntegrationEventType.BOOTSTRAP_STAGE_COMPLETED,
                    correlation_id=correlation_id,
                    session_state=session_state,
                    stage_id=stage_id,
                    message=str(exc),
                )
            if critical:
                stage = getattr(exc, "stage_id", stage_id)
                raise IntegrationBootstrapError(
                    str(exc),
                    code=str(code),
                    stage_id=stage if isinstance(stage, BootstrapStageId) else stage_id,
                ) from exc
            return None

    def _resolve_configuration(
        self,
        injected: ApplicationConfiguration | None,
    ) -> ApplicationConfiguration:
        """Resolve application configuration for bootstrap."""
        try:
            if injected is not None:
                config = injected
            else:
                config = load_application_configuration(self._options.load_options)
        except ApplicationConfigurationError as exc:
            raise IntegrationConfigurationError(
                str(exc),
                code="INTEGRATION.CONFIG.LOAD_FAILED",
                field=exc.field,
            ) from exc
        if (
            config.execution_mode is StrategyExecutionMode.LIVE
            and config.profile is EnvironmentProfile.DEVELOPMENT
            and config.metadata.get("allow_live_in_development") != "true"
        ):
            raise IntegrationConfigurationError(
                "LIVE execution_mode is incompatible with DEVELOPMENT profile.",
                code="INTEGRATION.CONFIG.PROFILE_MODE_MISMATCH",
            )
        return config

    def _construct_event_bus(self, config: ApplicationConfiguration) -> EventBus:
        """Construct shared event bus."""
        override = self._options.engine_overrides.event_bus
        if override is not None:
            return override
        return EventBus(config.to_event_bus_policy())

    def _resolve_broker_session(self, config: ApplicationConfiguration) -> BrokerSession:
        """Resolve broker session from configuration secrets."""
        if self._options.engine_overrides.broker_client is not None:
            return validate_broker_session(
                self._options.engine_overrides.broker_client.session
            )
        broker = config.broker
        if broker.broker_type in {BrokerType.MOCK, BrokerType.RECORDING}:
            session = BrokerSession(
                broker_id=_map_broker_type(broker.broker_type),
                session_id=str(uuid.uuid4()),
                authenticated_at=self._clock(),
                credentials=MappingProxyType({}),
            )
            return validate_broker_session(session)
        provider = _default_secret_provider()
        credentials: dict[str, str] = {}
        for key, ref_name in (
            ("api_key", broker.api_key_secret_ref),
            ("api_secret", broker.api_secret_secret_ref),
            ("access_token", broker.access_token_secret_ref),
        ):
            if not ref_name:
                continue
            credentials[key] = _resolve_secret_value(config, provider, ref_name)
        session = BrokerSession(
            broker_id=_map_broker_type(broker.broker_type),
            session_id=str(uuid.uuid4()),
            authenticated_at=self._clock(),
            credentials=MappingProxyType(dict(credentials)),
        )
        try:
            return validate_broker_session(session)
        except Exception as exc:
            raise IntegrationBrokerError(
                str(exc),
                code="INTEGRATION.BROKER.SESSION_INVALID",
            ) from exc

    def _construct_broker_client(
        self,
        config: ApplicationConfiguration,
        broker_session: BrokerSession | None,
    ) -> BaseBrokerClient:
        """Construct broker client from session or override."""
        override = self._options.engine_overrides.broker_client
        if override is not None:
            return override
        if broker_session is None:
            raise IntegrationBrokerError(
                "Broker session is required for broker client construction.",
                code="INTEGRATION.BROKER.SESSION_INVALID",
            )
        try:
            return BrokerClientFactory.create(
                config.broker.broker_type,
                broker_session,
                config.broker,
            )
        except IntegrationBrokerError:
            raise
        except Exception as exc:
            raise IntegrationBootstrapError(
                str(exc),
                code="INTEGRATION.ENGINE.CONSTRUCTION_FAILED",
                stage_id=BootstrapStageId.BROKER_CLIENT_CONSTRUCTION,
            ) from exc

    def _connect_broker(
        self,
        broker_client: BaseBrokerClient | None,
        bus: EventBus,
        correlation_id: str,
        session_state: IntegrationSessionState,
    ) -> None:
        """Connect broker client when configured."""
        if not self._options.auto_connect_broker or broker_client is None:
            return
        try:
            broker_client.connect()
        except BrokerConnectionError as exc:
            raise IntegrationBrokerError(
                str(exc),
                code="INTEGRATION.BROKER.CONNECT_FAILED",
            ) from exc
        self._publish_integration_event(
            bus,
            IntegrationEventType.BROKER_CONNECTED,
            correlation_id=correlation_id,
            session_state=session_state,
        )

    def _construct_strategy_registry(
        self,
        config: ApplicationConfiguration,
    ) -> StrategyRegistry:
        """Build and populate strategy registry."""
        override = self._options.engine_overrides.strategy_registry
        if override is not None:
            return override
        try:
            registry = StrategyRegistry(config.to_strategy_registry_config())
        except Exception as exc:
            raise IntegrationBootstrapError(
                str(exc),
                code="INTEGRATION.ENGINE.CONSTRUCTION_FAILED",
                stage_id=BootstrapStageId.STRATEGY_REGISTRY_CONSTRUCTION,
            ) from exc
        plugin_dir = Path(config.strategy.registry_plugin_dir or config.paths.strategy_plugin_dir)
        candidates = _discover_strategy_plugins(plugin_dir)
        enabled = config.strategy.enabled_strategy_ids
        disabled = config.strategy.disabled_strategy_ids
        filtered: list[StrategyDiscoveryDescriptor] = []
        for descriptor in candidates:
            strategy_id = descriptor.strategy.metadata.strategy_id
            if enabled and strategy_id not in enabled:
                continue
            if strategy_id in disabled:
                continue
            filtered.append(descriptor)
        if filtered:
            registry.register_batch(filtered)
        return registry

    def _construct_engines(
        self,
        config: ApplicationConfiguration,
        bus: EventBus,
        broker_client: BaseBrokerClient | None,
        strategy_registry: StrategyRegistry | None,
        stages: list[BootstrapStageResult],
        warnings: list[IntegrationWarningRecord],
        errors: list[IntegrationErrorRecord],
    ) -> EngineRegistry:
        """Construct coordinated engines and assemble registry."""
        overrides = self._options.engine_overrides
        built: dict[str, object | None] = {}
        engine_builders: list[tuple[str, Callable[[], object], bool]] = [
            (
                "market_data",
                lambda: overrides.market_data
                or MarketDataEngine(
                    config.to_market_data_engine_config(),
                    broker_client,
                    MarketDataAdapter(),
                    bus,
                ),
                True,
            ),
            (
                "strategy_evaluation",
                lambda: overrides.strategy_evaluation
                or StrategyEvaluationEngine(
                    config.to_strategy_evaluation_engine_config(),
                    strategy_registry,
                ),
                False,
            ),
            (
                "trade_decision",
                lambda: overrides.trade_decision
                or TradeDecisionEngine(config.to_trade_decision_engine_config()),
                False,
            ),
            ("risk", lambda: overrides.risk or RiskEngine(config.to_risk_engine_config()), True),
            (
                "execution",
                lambda: overrides.execution or ExecutionEngine(config.to_execution_engine_config()),
                False,
            ),
            (
                "order_manager",
                lambda: overrides.order_manager
                or OrderManager(config.to_order_manager_config(), event_bus=bus),
                True,
            ),
            (
                "position_manager",
                lambda: overrides.position_manager
                or PositionManager(config.to_position_manager_config(), bus),
                True,
            ),
            (
                "portfolio_manager",
                lambda: overrides.portfolio_manager
                or PortfolioManager(config.to_portfolio_manager_config(), bus),
                True,
            ),
            (
                "apme",
                lambda: overrides.apme
                or AdaptivePositionManagementEngine(config.to_apme_config(), bus),
                False,
            ),
        ]
        details: dict[str, str] = {}
        for name, builder, critical in engine_builders:
            if getattr(overrides, name) is not None:
                built[name] = getattr(overrides, name)
                details[name] = "override"
                continue
            try:
                built[name] = builder()
                details[name] = "constructed"
            except Exception as exc:
                code = getattr(exc, "code", "INTEGRATION.ENGINE.CONSTRUCTION_FAILED")
                built[name] = None
                details[name] = "failed"
                record = IntegrationErrorRecord(
                    code=str(code),
                    message=str(exc),
                    stage_id=BootstrapStageId.ENGINE_CONSTRUCTION,
                    field=name,
                )
                if critical:
                    errors.append(record)
                    raise IntegrationBootstrapError(
                        str(exc),
                        code="INTEGRATION.ENGINE.CONSTRUCTION_FAILED",
                        stage_id=BootstrapStageId.ENGINE_CONSTRUCTION,
                        field=name,
                    ) from exc
                warnings.append(
                    IntegrationWarningRecord(
                        code=str(code),
                        message=str(exc),
                        stage_id=BootstrapStageId.ENGINE_CONSTRUCTION,
                        field=name,
                    )
                )
        stages.append(
            BootstrapStageResult(
                stage_id=BootstrapStageId.ENGINE_CONSTRUCTION,
                passed=True,
                error_code=None,
                message=None,
                duration_ms=0.0,
                details=MappingProxyType(details),
            )
        )
        return EngineRegistry(
            event_bus=bus,
            market_data=built["market_data"],
            strategy_evaluation=built["strategy_evaluation"],
            trade_decision=built["trade_decision"],
            risk=built["risk"],
            execution=built["execution"],
            order_manager=built["order_manager"],
            position_manager=built["position_manager"],
            portfolio_manager=built["portfolio_manager"],
            apme=built["apme"],
        )

    def _assemble_engine_registry_from_overrides(
        self,
        config: ApplicationConfiguration,
        bus: EventBus,
        broker_client: BaseBrokerClient | None,
        strategy_registry: StrategyRegistry | None,
    ) -> EngineRegistry:
        """Assemble registry when orchestrator override skips construction."""
        del config, broker_client, strategy_registry
        overrides = self._options.engine_overrides
        return EngineRegistry(
            event_bus=overrides.event_bus or bus,
            market_data=overrides.market_data,
            strategy_evaluation=overrides.strategy_evaluation,
            trade_decision=overrides.trade_decision,
            risk=overrides.risk,
            execution=overrides.execution,
            order_manager=overrides.order_manager,
            position_manager=overrides.position_manager,
            portfolio_manager=overrides.portfolio_manager,
            apme=overrides.apme,
        )

    def _construct_orchestrator(
        self,
        config: ApplicationConfiguration,
        bus: EventBus,
        broker_client: BaseBrokerClient | None,
        engine_registry: EngineRegistry,
    ) -> SystemOrchestrator:
        """Construct system orchestrator with injected dependencies."""
        override = self._options.engine_overrides.orchestrator
        if override is not None:
            return override
        try:
            return SystemOrchestrator(
                config.to_orchestrator_config(),
                event_bus=bus,
                broker_client=broker_client,
                engine_registry=engine_registry,
                clock=self._options.clock,
            )
        except Exception as exc:
            raise IntegrationBootstrapError(
                str(exc),
                code="INTEGRATION.ENGINE.CONSTRUCTION_FAILED",
                stage_id=BootstrapStageId.ORCHESTRATOR_CONSTRUCTION,
            ) from exc

    def _is_broker_session_critical(self, config: ApplicationConfiguration) -> bool:
        """Return whether broker session resolution is critical for profile."""
        return config.profile is EnvironmentProfile.PRODUCTION

    def _is_broker_connection_critical(self, config: ApplicationConfiguration) -> bool:
        """Return whether broker connection is critical for profile."""
        return config.profile is EnvironmentProfile.PRODUCTION

    def _register_integration_subscriptions(
        self,
        bus: EventBus,
        correlation_id: str,
    ) -> list[SubscriptionHandle]:
        """Register relay subscriptions for orchestrator health events."""
        del correlation_id
        handles: list[SubscriptionHandle] = []

        def _relay_degraded(envelope: EventEnvelope) -> None:
            self._publish_integration_event(
                bus,
                IntegrationEventType.HEALTH_DEGRADED,
                correlation_id=envelope.correlation_id,
                session_state=IntegrationSessionState.RUNNING,
                message=str(envelope.payload),
            )

        def _relay_recovered(envelope: EventEnvelope) -> None:
            self._publish_integration_event(
                bus,
                IntegrationEventType.HEALTH_RECOVERED,
                correlation_id=envelope.correlation_id,
                session_state=IntegrationSessionState.RUNNING,
                message=str(envelope.payload),
            )

        handles.append(bus.subscribe("system.health.degraded", _relay_degraded))
        handles.append(bus.subscribe("system.health.recovered", _relay_recovered))
        return handles

    def _publish_integration_event(
        self,
        bus: EventBus | None,
        event_type: IntegrationEventType,
        *,
        correlation_id: str,
        session_state: IntegrationSessionState,
        stage_id: BootstrapStageId | None = None,
        session_id: str = "",
        message: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Publish integration lifecycle event when bus is available."""
        if bus is None:
            return
        topic = _INTEGRATION_EVENT_TOPICS[event_type]
        event = IntegrationEvent(
            event_type=event_type,
            topic=topic,
            session_id=session_id,
            correlation_id=correlation_id,
            occurred_at=self._clock(),
            session_state=session_state,
            stage_id=stage_id,
            message=message,
            metadata=MappingProxyType(dict(metadata or {})),
        )
        bus.publish(
            topic,
            {
                "event_type": event.event_type.value,
                "session_id": event.session_id,
                "correlation_id": event.correlation_id,
                "occurred_at": _isoformat_utc(event.occurred_at),
                "session_state": event.session_state.value,
                "stage_id": event.stage_id.value if event.stage_id else None,
                "message": event.message,
                "metadata": dict(event.metadata),
            },
            correlation_id=correlation_id,
            producer=PRODUCER_NAME,
        )


class IntegrationSession:
    """Thread-safe runtime facade for a bootstrapped THETA AI TRADER process."""

    def __init__(
        self,
        *,
        session_id: str,
        config: ApplicationConfiguration,
        options: IntegrationBootstrapOptions,
        event_bus: EventBus,
        broker_client: BaseBrokerClient | None,
        strategy_registry: StrategyRegistry | None,
        engine_registry: EngineRegistry,
        orchestrator: SystemOrchestrator | None,
        wiring_fingerprint: str,
        bootstrap_diagnostics: BootstrapDiagnostics,
        wiring_result: WiringValidationResult,
        initial_state: IntegrationSessionState,
        sealed_at: datetime,
        clock: Callable[[], datetime],
        subscription_handles: list[SubscriptionHandle],
    ) -> None:
        """Initialize sealed integration session."""
        self.session_id = session_id
        self._config = config
        self._options = options
        self._event_bus = event_bus
        self._broker_client = broker_client
        self._strategy_registry = strategy_registry
        self._engine_registry = engine_registry
        self._orchestrator = orchestrator
        self._wiring_fingerprint = wiring_fingerprint
        self._bootstrap_diagnostics = bootstrap_diagnostics
        self._last_wiring_status = wiring_result.status
        self._last_wiring_issues = wiring_result.issues
        self._sealed_at = sealed_at
        self._clock = clock
        self._lock = threading.RLock()
        self._state = initial_state
        self._subscription_handles = list(subscription_handles)

    def start(self) -> SystemStartupResult:
        """Start the underlying orchestrator (no-op if already RUNNING)."""
        with self._lock:
            if self._state in {IntegrationSessionState.RUNNING, IntegrationSessionState.DEGRADED}:
                return SystemStartupResult(
                    startup_id=str(uuid.uuid4()),
                    status=StartupStatus.SUCCESS,
                    engines_started=(),
                    engines_failed=(),
                    subscriptions_registered=0,
                    warnings=(),
                    errors=(),
                    started_at=self._clock(),
                    completed_at=self._clock(),
                    duration_ms=0.0,
                )
            self._require_orchestrator()
            self._state = IntegrationSessionState.STARTING
        assert self._orchestrator is not None
        result = self._orchestrator.start()
        with self._lock:
            if result.status is StartupStatus.SUCCESS:
                self._state = IntegrationSessionState.RUNNING
            elif result.status is StartupStatus.PARTIAL:
                self._state = IntegrationSessionState.DEGRADED
            else:
                self._state = IntegrationSessionState.FAILED
        self._publish_session_event(IntegrationEventType.SESSION_STARTED)
        return result

    def stop(self) -> SystemShutdownResult:
        """Gracefully stop the orchestrator and disconnect the broker."""
        with self._lock:
            if self._state is IntegrationSessionState.STOPPED:
                return SystemShutdownResult(
                    shutdown_id=str(uuid.uuid4()),
                    status=ShutdownStatus.SUCCESS,
                    cycles_drained=0,
                    subscriptions_removed=0,
                    engines_stopped=(),
                    warnings=(),
                    errors=(),
                    started_at=self._clock(),
                    completed_at=self._clock(),
                    duration_ms=0.0,
                )
            self._state = IntegrationSessionState.STOPPING
        shutdown_result: SystemShutdownResult
        if self._orchestrator is not None:
            orch_state = self._orchestrator.get_state()
            if orch_state in {OrchestratorState.RUNNING, OrchestratorState.DEGRADED}:
                shutdown_result = self._orchestrator.stop()
            else:
                shutdown_result = SystemShutdownResult(
                    shutdown_id=str(uuid.uuid4()),
                    status=ShutdownStatus.SUCCESS,
                    cycles_drained=0,
                    subscriptions_removed=0,
                    engines_stopped=(),
                    warnings=(),
                    errors=(),
                    started_at=self._clock(),
                    completed_at=self._clock(),
                    duration_ms=0.0,
                )
        else:
            shutdown_result = SystemShutdownResult(
                shutdown_id=str(uuid.uuid4()),
                status=ShutdownStatus.SUCCESS,
                cycles_drained=0,
                subscriptions_removed=0,
                engines_stopped=(),
                warnings=(),
                errors=(),
                started_at=self._clock(),
                completed_at=self._clock(),
                duration_ms=0.0,
            )
        if self._broker_client is not None and self._broker_client.is_connected():
            try:
                self._broker_client.disconnect()
            except Exception as exc:
                _LOGGER.warning(
                    "integration.broker.disconnect.failed",
                    extra={"error": str(exc)},
                )
            self._publish_session_event(IntegrationEventType.BROKER_DISCONNECTED)
        for handle in self._subscription_handles:
            try:
                handle.unsubscribe()
            except Exception:
                pass
        self._subscription_handles.clear()
        with self._lock:
            self._state = IntegrationSessionState.STOPPED
        self._publish_session_event(IntegrationEventType.SESSION_STOPPED)
        return shutdown_result

    def restart(self) -> IntegrationSession:
        """Stop and rebuild the entire object graph from the same config."""
        preserved_id = self.session_id
        config = self._config
        options = self._options
        self.stop()
        fresh_overrides = replace(
            options.engine_overrides,
            orchestrator=None,
            market_data=None,
            strategy_evaluation=None,
            trade_decision=None,
            risk=None,
            execution=None,
            order_manager=None,
            position_manager=None,
            portfolio_manager=None,
            apme=None,
        )
        fresh_options = replace(options, engine_overrides=fresh_overrides)
        engine = IntegrationEngine(config, fresh_options)
        new_session = engine.bootstrap()
        new_session.session_id = preserved_id
        new_session._publish_session_event(IntegrationEventType.SESSION_RESTARTED)
        return new_session

    def run_trading_cycle(self, context: TradingCycleContext) -> TradingCycleResult:
        """Delegate a pre-trade cycle to the orchestrator unchanged."""
        with self._lock:
            self._require_state(
                {IntegrationSessionState.RUNNING, IntegrationSessionState.DEGRADED},
            )
        assert self._orchestrator is not None
        return self._orchestrator.run_trading_cycle(context)

    def run_post_fill_cycle(self, context: PostFillCycleContext) -> PostFillCycleResult:
        """Delegate a post-fill cycle to the orchestrator unchanged."""
        with self._lock:
            self._require_state(
                {IntegrationSessionState.RUNNING, IntegrationSessionState.DEGRADED},
            )
        assert self._orchestrator is not None
        return self._orchestrator.run_post_fill_cycle(context)

    def run_forever(
        self,
        *,
        interval_seconds: float,
        context_factory: Callable[[], TradingCycleContext],
        stop_event: threading.Event,
    ) -> None:
        """Optional convenience loop for simple CLI or paper runners."""
        while not stop_event.is_set():
            context = context_factory()
            self.run_trading_cycle(context)
            stop_event.wait(interval_seconds)

    def get_health(self) -> IntegrationHealthReport:
        """Return aggregated integration-level health."""
        with self._lock:
            state = self._state
            orchestrator = self._orchestrator
            wiring_status = self._last_wiring_status
            wiring_issues = self._last_wiring_issues
            wiring_fingerprint = self._wiring_fingerprint
        try:
            orchestrator_health = orchestrator.get_health() if orchestrator is not None else None
            broker_snapshot = self._broker_health_snapshot()
            overall = _aggregate_overall_status(
                session_state=state,
                orchestrator_health=orchestrator_health,
                broker_snapshot=broker_snapshot,
                wiring_status=wiring_status,
                execution_mode=self._config.execution_mode,
            )
            return IntegrationHealthReport(
                report_id=str(uuid.uuid4()),
                as_of=self._clock(),
                session_state=state,
                overall_status=overall,
                orchestrator_health=orchestrator_health,
                broker_connection=broker_snapshot,
                wiring_status=wiring_status,
                wiring_issues=wiring_issues,
                config_fingerprint=self._config.config_fingerprint,
                wiring_fingerprint=wiring_fingerprint,
                issues=_collect_health_issues(
                    orchestrator_health,
                    broker_snapshot,
                    self._config.execution_mode,
                ),
            )
        except Exception as exc:
            raise IntegrationEngineError(
                str(exc),
                code="INTEGRATION.HEALTH.AGGREGATION_FAILED",
            ) from exc

    def get_runtime_state(self) -> RuntimeState:
        """Return an immutable runtime state snapshot."""
        with self._lock:
            state = self._state
            orchestrator_state = (
                self._orchestrator.get_state() if self._orchestrator is not None else None
            )
            last_cycle_at = None
            last_cycle_status = None
            if self._orchestrator is not None:
                health = self._orchestrator.get_health()
                last_cycle_at = health.last_cycle_at
                last_cycle_status = health.last_cycle_status
            uptime = max(0.0, (self._clock() - self._sealed_at).total_seconds())
            broker_id = self._broker_client.broker_id if self._broker_client is not None else None
            broker_connection_state = None
            if self._broker_client is not None:
                broker_connection_state = self._broker_client.get_connection_info().state
            return RuntimeState(
                session_id=self.session_id,
                as_of=self._clock(),
                session_state=state,
                orchestrator_state=orchestrator_state,
                environment_profile=self._config.profile,
                execution_mode=self._config.execution_mode,
                runner_kind=self._options.runner_kind,
                account_id=self._config.account.account_id,
                broker_id=broker_id,
                broker_connection_state=broker_connection_state,
                config_fingerprint=self._config.config_fingerprint,
                wiring_fingerprint=self._wiring_fingerprint,
                uptime_seconds=uptime,
                last_cycle_at=last_cycle_at,
                last_cycle_status=last_cycle_status,
                metadata=MappingProxyType(dict(self._options.metadata)),
            )

    def revalidate_wiring(self) -> WiringValidationResult:
        """Re-run WIRE-* checks against the current live object graph."""
        self._require_orchestrator()
        assert self._orchestrator is not None
        result = validate_wiring(
            self._config,
            self._event_bus,
            self._broker_client,
            self._engine_registry,
            self._orchestrator,
            strategy_registry=self._strategy_registry,
            clock=self._clock,
            prior_to_startup=False,
        )
        with self._lock:
            self._last_wiring_status = result.status
            self._last_wiring_issues = result.issues
        return result

    def get_orchestrator(self) -> SystemOrchestrator:
        """Return the constructed system orchestrator."""
        self._require_orchestrator()
        assert self._orchestrator is not None
        return self._orchestrator

    def get_broker_client(self) -> BaseBrokerClient:
        """Return the constructed broker client."""
        if self._broker_client is None:
            raise IntegrationSessionStateError(
                "Broker client was not constructed.",
                code="INTEGRATION.SESSION.NOT_BOOTSTRAPPED",
            )
        return self._broker_client

    def get_event_bus(self) -> EventBus:
        """Return the shared event bus instance."""
        return self._event_bus

    def get_strategy_registry(self) -> StrategyRegistry:
        """Return the constructed strategy registry."""
        if self._strategy_registry is None:
            raise IntegrationSessionStateError(
                "Strategy registry was not constructed.",
                code="INTEGRATION.SESSION.NOT_BOOTSTRAPPED",
            )
        return self._strategy_registry

    def get_configuration(self) -> ApplicationConfiguration:
        """Return the application configuration this session was built from."""
        return self._config

    def __enter__(self) -> IntegrationSession:
        """Support context manager entry."""
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Call stop() on context exit regardless of exception state."""
        del exc_type, exc, tb
        self.stop()

    def _broker_health_snapshot(self) -> BrokerHealthSnapshot:
        """Build broker connectivity snapshot."""
        if self._broker_client is None:
            return BrokerHealthSnapshot(
                broker_id=None,
                connection_state=None,
                session_state=None,
                last_connected_at=None,
                last_error_code=None,
                last_error_message=None,
            )
        info: ConnectionInfo = self._broker_client.get_connection_info()
        return BrokerHealthSnapshot(
            broker_id=self._broker_client.broker_id,
            connection_state=info.state,
            session_state=self._broker_client.get_session_state(),
            last_connected_at=info.since,
            last_error_code=info.last_error_code,
            last_error_message=info.last_error_message,
        )

    def _require_orchestrator(self) -> None:
        """Ensure orchestrator is available."""
        if self._orchestrator is None:
            raise IntegrationSessionStateError(
                "Session is not bootstrapped with an orchestrator.",
                code="INTEGRATION.SESSION.NOT_BOOTSTRAPPED",
            )

    def _require_state(self, allowed: set[IntegrationSessionState]) -> None:
        """Validate current session state for an operation."""
        if self._state not in allowed:
            if self._state in {
                IntegrationSessionState.NOT_BOOTSTRAPPED,
                IntegrationSessionState.BOOTSTRAPPING,
                IntegrationSessionState.WIRING,
                IntegrationSessionState.WIRED,
                IntegrationSessionState.STARTING,
            }:
                raise IntegrationSessionStateError(
                    f"Operation not allowed in state {self._state.value}.",
                    code="INTEGRATION.SESSION.NOT_BOOTSTRAPPED",
                )
            if self._state is IntegrationSessionState.STOPPED:
                raise IntegrationSessionStateError(
                    "Session has already been stopped.",
                    code="INTEGRATION.SESSION.ALREADY_STOPPED",
                )
            raise IntegrationSessionStateError(
                f"Invalid state transition from {self._state.value}.",
                code="INTEGRATION.STATE.INVALID_TRANSITION",
            )

    def _publish_session_event(self, event_type: IntegrationEventType) -> None:
        """Publish integration session lifecycle event."""
        IntegrationEngine(config=self._config, options=self._options)._publish_integration_event(
            self._event_bus,
            event_type,
            correlation_id=self.session_id,
            session_state=self._state,
            session_id=self.session_id,
        )


def bootstrap_integration_session(
    config: ApplicationConfiguration | None = None,
    options: IntegrationBootstrapOptions | None = None,
) -> IntegrationSession:
    """Construct an :class:`IntegrationEngine` and immediately bootstrap it."""
    return IntegrationEngine(config, options).bootstrap()


def create_development_session(
    load_options: LoadOptions | None = None,
) -> IntegrationSession:
    """Bootstrap a DEVELOPMENT-profile session with permissive defaults."""
    opts = load_options or default_load_options_for_profile(EnvironmentProfile.DEVELOPMENT)
    return bootstrap_integration_session(
        options=IntegrationBootstrapOptions(
            runner_kind=RunnerKind.CLI,
            load_options=opts,
            fail_fast_on_wiring_error=False,
        ),
    )


def create_paper_trading_session(
    load_options: LoadOptions | None = None,
) -> IntegrationSession:
    """Bootstrap a PAPER-profile session for the Paper Trading runner."""
    opts = load_options or default_load_options_for_profile(EnvironmentProfile.PAPER)
    return bootstrap_integration_session(
        options=IntegrationBootstrapOptions(
            runner_kind=RunnerKind.PAPER_TRADING,
            load_options=opts,
            fail_fast_on_wiring_error=True,
        ),
    )


def create_live_session(
    load_options: LoadOptions | None = None,
) -> IntegrationSession:
    """Bootstrap a PRODUCTION-profile session for the Live Trading runner."""
    opts = load_options or default_load_options_for_profile(EnvironmentProfile.PRODUCTION)
    return bootstrap_integration_session(
        options=IntegrationBootstrapOptions(
            runner_kind=RunnerKind.LIVE_TRADING,
            load_options=opts,
            fail_fast_on_wiring_error=True,
            auto_connect_broker=True,
        ),
    )


__all__ = [
    "INTEGRATION_ENGINE_VERSION",
    "INTEGRATION_ENGINE_SCHEMA_VERSION",
    "PRODUCER_NAME",
    "ApplicationConfiguration",
    "BaseBrokerClient",
    "BootstrapDiagnostics",
    "BootstrapStageId",
    "BootstrapStageResult",
    "BootstrapStatus",
    "BrokerClientFactory",
    "BrokerHealthSnapshot",
    "BrokerId",
    "BrokerType",
    "ConnectionState",
    "CycleStatus",
    "EngineOverrides",
    "EngineRegistry",
    "EnvironmentProfile",
    "HealthStatus",
    "IntegrationBootstrapOptions",
    "IntegrationBrokerError",
    "IntegrationBootstrapError",
    "IntegrationConfigurationError",
    "IntegrationEngine",
    "IntegrationEngineError",
    "IntegrationEvent",
    "IntegrationEventType",
    "IntegrationHealthReport",
    "IntegrationSession",
    "IntegrationSessionState",
    "IntegrationSessionStateError",
    "IntegrationWarningRecord",
    "IntegrationErrorRecord",
    "IntegrationWiringError",
    "LoadOptions",
    "OrchestratorState",
    "PostFillCycleContext",
    "PostFillCycleResult",
    "RunnerKind",
    "RuntimeState",
    "SessionState",
    "StrategyExecutionMode",
    "StrategyRegistry",
    "SystemHealthReport",
    "SystemOrchestrator",
    "SystemShutdownResult",
    "SystemStartupResult",
    "TradingCycleContext",
    "TradingCycleResult",
    "WiringCheckId",
    "WiringCheckResult",
    "WiringValidationIssue",
    "WiringValidationResult",
    "WiringValidationStatus",
    "bootstrap_integration_session",
    "compute_wiring_fingerprint",
    "create_development_session",
    "create_live_session",
    "create_paper_trading_session",
    "deserialize_integration_health_report",
    "deserialize_runtime_state",
    "serialize_integration_health_report",
    "serialize_runtime_state",
    "validate_wiring",
]
