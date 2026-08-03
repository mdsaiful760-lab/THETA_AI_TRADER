"""Thread-safe strategy plugin registry for THETA AI TRADER.

This module manages the in-process catalog of :class:`BaseStrategy` plugins.
It handles registration, enablement, immutable snapshots, and duplicate
detection — never strategy evaluation, market data, broker calls, or execution.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Final, Mapping

from strategy.base_strategy import (
    BaseStrategy,
    StrategyEngineConfigurationError,
    StrategyMetadata,
    StrategyPluginConfig,
    StrategyRiskProfileHint,
    validate_strategy_metadata,
    validate_strategy_plugin_config,
)
from strategy.signals import StrategyFamily

STRATEGY_REGISTRY_VERSION: Final[str] = "1.0.0"
STRATEGY_REGISTRY_SCHEMA_VERSION: Final[str] = "1.0.0"
DEFAULT_MAX_PLUGINS: Final[int] = 256
MIN_PRIORITY: Final[int] = 0
MAX_PRIORITY: Final[int] = 1000

ERROR_CONFIG_INVALID: Final[str] = "STRATEGY_REGISTRY.CONFIG.INVALID"
ERROR_DUPLICATE_ID: Final[str] = "STRATEGY_REGISTRY.DUPLICATE_ID"
ERROR_DUPLICATE_FINGERPRINT: Final[str] = "STRATEGY_REGISTRY.DUPLICATE_FINGERPRINT"
ERROR_NOT_FOUND: Final[str] = "STRATEGY_REGISTRY.NOT_FOUND"
ERROR_FROZEN: Final[str] = "STRATEGY_REGISTRY.FROZEN"
ERROR_LIMIT_EXCEEDED: Final[str] = "STRATEGY_REGISTRY.LIMIT_EXCEEDED"
ERROR_INVALID_STATE: Final[str] = "STRATEGY_REGISTRY.INVALID_STATE"
ERROR_VALIDATION_FAILED: Final[str] = "STRATEGY_REGISTRY.VALIDATION.FAILED"
ERROR_TYPE_INVALID: Final[str] = "STRATEGY_REGISTRY.TYPE.INVALID"
ERROR_BATCH_PARTIAL_FAILURE: Final[str] = "STRATEGY_REGISTRY.BATCH.PARTIAL_FAILURE"
ERROR_EMPTY_ENABLED_SET: Final[str] = "STRATEGY_REGISTRY.EMPTY_ENABLED_SET"
ERROR_SERIALIZATION_UNSUPPORTED_VERSION: Final[str] = "STRATEGY_REGISTRY.SERIALIZATION.UNSUPPORTED_VERSION"
ERROR_SERIALIZATION_MALFORMED: Final[str] = "STRATEGY_REGISTRY.SERIALIZATION.MALFORMED"

_logger = logging.getLogger(__name__)


class RegistrationState(str, Enum):
    """Lifecycle state of a registered strategy plugin."""

    REGISTERED = "registered"
    PENDING_REMOVAL = "pending_removal"
    FAILED_INIT = "failed_init"


class RegistryFreezeState(str, Enum):
    """Whether structural registry mutations are permitted."""

    MUTABLE = "mutable"
    FROZEN = "frozen"


class DuplicateRegistrationPolicy(str, Enum):
    """Policy applied when registering a duplicate ``strategy_id``."""

    REJECT = "reject"
    REPLACE = "replace"
    IGNORE = "ignore"


class StrategyRegistryError(Exception):
    """Base exception for strategy registry failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        strategy_id: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.strategy_id = strategy_id
        self.field = field


class StrategyRegistryConfigurationError(StrategyRegistryError):
    """Raised when plugin configuration or metadata is invalid."""


class StrategyRegistryDuplicateError(StrategyRegistryError):
    """Raised when duplicate registration is rejected."""


class StrategyRegistryNotFoundError(StrategyRegistryError):
    """Raised when a strategy identifier is not registered."""


class StrategyRegistryFrozenError(StrategyRegistryError):
    """Raised when a mutation is rejected because the registry is frozen."""


class StrategyRegistryLimitError(StrategyRegistryError):
    """Raised when the maximum plugin count is exceeded."""


class StrategyRegistryValidationError(StrategyRegistryError):
    """Raised when registry validation fails in :meth:`StrategyRegistry.assert_valid`."""


class StrategyRegistryInvalidStateError(StrategyRegistryError):
    """Raised when an operation conflicts with entry lifecycle state."""


@dataclass(frozen=True)
class StrategyRegistryConfig:
    """Immutable configuration for :class:`StrategyRegistry` behaviour.

    Attributes:
        duplicate_policy: Policy for duplicate ``strategy_id`` registration.
        max_plugins: Maximum number of plugins allowed in the registry.
        defer_unregistration: Defer physical removal during active engine runs.
        allow_enable_disable_while_frozen: Permit enable/disable when frozen.
        strict_batch: Abort batch registration on first failure when ``True``.
    """

    duplicate_policy: DuplicateRegistrationPolicy = DuplicateRegistrationPolicy.REJECT
    max_plugins: int = DEFAULT_MAX_PLUGINS
    defer_unregistration: bool = False
    allow_enable_disable_while_frozen: bool = True
    strict_batch: bool = False

    def __post_init__(self) -> None:
        if self.max_plugins <= 0:
            raise StrategyRegistryConfigurationError(
                "max_plugins must be positive.",
                code=ERROR_CONFIG_INVALID,
                field="max_plugins",
            )


@dataclass(frozen=True)
class StrategyRegistrationRecord:
    """Immutable descriptor for one registered strategy plugin.

    Attributes:
        strategy_id: Stable unique identifier.
        display_name: Human-readable name from metadata.
        strategy_version: Semantic version of the plugin implementation.
        strategy_family: Canonical strategy family.
        priority: Scheduling priority in ``0..1000``.
        enabled: Whether the plugin is eligible for engine scheduling.
        registered_at: First registration timestamp (timezone-aware).
        updated_at: Last metadata or state mutation timestamp.
        state: Registration lifecycle state.
        metadata: Immutable metadata snapshot captured at registration.
        metadata_fingerprint: SHA-256 hex digest of plugin metadata.
        tags: Immutable copy of metadata tags for fast filtering.
    """

    strategy_id: str
    display_name: str
    strategy_version: str
    strategy_family: StrategyFamily
    priority: int
    enabled: bool
    registered_at: datetime
    updated_at: datetime
    state: RegistrationState
    metadata: StrategyMetadata
    metadata_fingerprint: str
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class RegistrySnapshot:
    """Immutable point-in-time view of registry catalog state.

    Attributes:
        snapshot_id: Unique snapshot identifier.
        created_at: Snapshot materialization timestamp.
        registry_fingerprint: Deterministic hash over sorted registration records.
        freeze_state: Whether the source registry was frozen when captured.
        records: All records sorted by priority desc, strategy_id asc.
        enabled_records: Enabled subset with the same sort order.
        plugin_count: Denormalized total record count.
        enabled_count: Denormalized enabled record count.
    """

    snapshot_id: str
    created_at: datetime
    registry_fingerprint: str
    freeze_state: RegistryFreezeState
    records: tuple[StrategyRegistrationRecord, ...]
    enabled_records: tuple[StrategyRegistrationRecord, ...]
    plugin_count: int
    enabled_count: int


@dataclass(frozen=True)
class RegistryValidationRecord:
    """Single validation finding from :meth:`StrategyRegistry.validate`.

    Attributes:
        code: Stable error or warning code.
        message: Human-readable description.
        strategy_id: Related plugin identifier, if applicable.
        field: Related field path, if applicable.
    """

    code: str
    message: str
    strategy_id: str | None = None
    field: str | None = None


@dataclass(frozen=True)
class RegistryValidationResult:
    """Structured outcome of :meth:`StrategyRegistry.validate`.

    Attributes:
        errors: Validation errors that invalidate the registry catalog.
        warnings: Non-fatal validation warnings.
    """

    errors: tuple[RegistryValidationRecord, ...] = ()
    warnings: tuple[RegistryValidationRecord, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return ``True`` when no validation errors were found."""
        return not self.errors


@dataclass(frozen=True)
class StrategyDiscoveryDescriptor:
    """Immutable descriptor for batch strategy registration.

    Attributes:
        strategy: Pre-constructed plugin instance.
        enabled: Whether the plugin should be enabled after registration.
        priority_override: Optional priority override for the registration record.
    """

    strategy: BaseStrategy
    enabled: bool = True
    priority_override: int | None = None


@dataclass(frozen=True)
class RegistryBatchResult:
    """Outcome of :meth:`StrategyRegistry.register_batch`.

    Attributes:
        registered_ids: Successfully registered strategy identifiers.
        failed: Failure records for plugins that could not be registered.
        skipped: Identifiers skipped due to IGNORE policy idempotency.
    """

    registered_ids: tuple[str, ...] = ()
    failed: tuple[RegistryValidationRecord, ...] = ()
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegistryDuplicateReport:
    """Duplicate scan report from :meth:`StrategyRegistry.detect_duplicates`.

    Attributes:
        fingerprint_duplicates: Groups of strategy IDs sharing a metadata fingerprint.
        display_name_collisions: Groups of strategy IDs with case-insensitive name clash.
    """

    fingerprint_duplicates: tuple[tuple[str, ...], ...] = ()
    display_name_collisions: tuple[tuple[str, ...], ...] = ()


@dataclass
class _StrategyRegistrationEntry:
    """Internal mutable registration entry."""

    strategy: BaseStrategy
    record: StrategyRegistrationRecord


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def _record_sort_key(record: StrategyRegistrationRecord) -> tuple[int, str]:
    """Return deterministic sort key: higher priority first, then strategy_id."""
    return (-record.priority, record.strategy_id)


def _tags_from_metadata(metadata: StrategyMetadata) -> Mapping[str, str]:
    """Return an immutable copy of metadata tags."""
    if not metadata.tags:
        return MappingProxyType({})
    return MappingProxyType(dict(metadata.tags))


def _ensure_timezone_aware(value: datetime, field_name: str) -> None:
    """Raise if ``value`` is not timezone-aware."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise StrategyRegistryConfigurationError(
            f"{field_name} must be a timezone-aware datetime.",
            code=ERROR_CONFIG_INVALID,
            field=field_name,
        )


def _validate_plugin(strategy: BaseStrategy) -> StrategyPluginConfig:
    """Validate a strategy plugin and return its configuration.

    Args:
        strategy: Candidate plugin instance.

    Returns:
        Validated plugin configuration.

    Raises:
        StrategyRegistryConfigurationError: If validation fails.
        StrategyRegistryError: If the object is not a BaseStrategy.
    """
    if not isinstance(strategy, BaseStrategy):
        raise StrategyRegistryError(
            "Registered object must be an instance of BaseStrategy.",
            code=ERROR_TYPE_INVALID,
            field="strategy",
        )

    plugin_config = strategy.plugin_config
    try:
        validate_strategy_plugin_config(plugin_config)
        validate_strategy_metadata(plugin_config.metadata)
    except StrategyEngineConfigurationError as exc:
        raise StrategyRegistryConfigurationError(
            str(exc),
            code=ERROR_CONFIG_INVALID,
            strategy_id=getattr(plugin_config.metadata, "strategy_id", None),
            field=getattr(exc, "field", None),
        ) from exc

    if strategy.engine_name != plugin_config.metadata.strategy_id:
        raise StrategyRegistryConfigurationError(
            "strategy.engine_name must match metadata.strategy_id.",
            code=ERROR_CONFIG_INVALID,
            strategy_id=plugin_config.metadata.strategy_id,
            field="engine_name",
        )

    return plugin_config


def _build_registration_record(
    strategy: BaseStrategy,
    *,
    enabled: bool,
    priority: int,
    now: datetime,
    registered_at: datetime | None = None,
    state: RegistrationState = RegistrationState.REGISTERED,
) -> StrategyRegistrationRecord:
    """Build an immutable registration record from a validated plugin."""
    metadata = strategy.metadata_snapshot()
    reg_at = registered_at or now
    _ensure_timezone_aware(reg_at, "registered_at")
    _ensure_timezone_aware(now, "updated_at")

    return StrategyRegistrationRecord(
        strategy_id=metadata.strategy_id,
        display_name=metadata.display_name,
        strategy_version=metadata.version,
        strategy_family=metadata.strategy_family,
        priority=priority,
        enabled=enabled,
        registered_at=reg_at,
        updated_at=now,
        state=state,
        metadata=metadata,
        metadata_fingerprint=strategy.metadata_fingerprint(),
        tags=_tags_from_metadata(metadata),
    )


def _record_fingerprint_payload(record: StrategyRegistrationRecord) -> dict[str, Any]:
    """Build canonical fingerprint payload for one registration record."""
    return {
        "strategy_id": record.strategy_id,
        "display_name": record.display_name,
        "strategy_version": record.strategy_version,
        "strategy_family": record.strategy_family.value,
        "priority": record.priority,
        "enabled": record.enabled,
        "state": record.state.value,
        "metadata_fingerprint": record.metadata_fingerprint,
        "registered_at": record.registered_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def registry_fingerprint(records: tuple[StrategyRegistrationRecord, ...]) -> str:
    """Compute deterministic SHA-256 fingerprint over registration records.

    Records are sorted by ``strategy_id`` before hashing, independent of
    execution priority ordering.

    Args:
        records: Registration records to fingerprint.

    Returns:
        Lowercase hex SHA-256 digest.
    """
    sorted_records = sorted(records, key=lambda item: item.strategy_id)
    payload = [_record_fingerprint_payload(record) for record in sorted_records]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _snapshot_id_from(fingerprint: str, created_at: datetime) -> str:
    """Build a deterministic snapshot identifier."""
    material = f"{fingerprint}|{created_at.isoformat()}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"reg-snap-{digest}"


def _build_snapshot(
    records: tuple[StrategyRegistrationRecord, ...],
    *,
    freeze_state: RegistryFreezeState,
    created_at: datetime,
) -> RegistrySnapshot:
    """Materialize an immutable registry snapshot from records."""
    sorted_records = tuple(sorted(records, key=_record_sort_key))
    enabled_records = tuple(
        record
        for record in sorted_records
        if record.enabled and record.state is RegistrationState.REGISTERED
    )
    fingerprint = registry_fingerprint(sorted_records)
    return RegistrySnapshot(
        snapshot_id=_snapshot_id_from(fingerprint, created_at),
        created_at=created_at,
        registry_fingerprint=fingerprint,
        freeze_state=freeze_state,
        records=sorted_records,
        enabled_records=enabled_records,
        plugin_count=len(sorted_records),
        enabled_count=len(enabled_records),
    )


def _metadata_to_dict(metadata: StrategyMetadata) -> dict[str, Any]:
    """Serialize strategy metadata to a dictionary."""
    return {
        "strategy_id": metadata.strategy_id,
        "display_name": metadata.display_name,
        "version": metadata.version,
        "strategy_family": metadata.strategy_family.value,
        "category": metadata.category,
        "supported_underlyings": list(metadata.supported_underlyings),
        "requires_volatility_snapshot": metadata.requires_volatility_snapshot,
        "min_contracts_required": metadata.min_contracts_required,
        "risk_profile_hint": metadata.risk_profile_hint.value if metadata.risk_profile_hint else None,
        "tags": dict(metadata.tags),
    }


def _metadata_from_dict(data: Mapping[str, Any]) -> StrategyMetadata:
    """Deserialize strategy metadata from a dictionary."""
    risk_hint = data.get("risk_profile_hint")
    return StrategyMetadata(
        strategy_id=str(data["strategy_id"]),
        display_name=str(data["display_name"]),
        version=str(data["version"]),
        strategy_family=StrategyFamily(str(data["strategy_family"])),
        category=data.get("category"),
        supported_underlyings=tuple(str(item) for item in data.get("supported_underlyings", ())),
        requires_volatility_snapshot=bool(data.get("requires_volatility_snapshot", False)),
        min_contracts_required=int(data.get("min_contracts_required", 1)),
        risk_profile_hint=StrategyRiskProfileHint(str(risk_hint)) if risk_hint else None,
        tags=MappingProxyType(dict(data.get("tags") or {})),
    )


def record_to_dict(record: StrategyRegistrationRecord, *, omit_nulls: bool = False) -> dict[str, Any]:
    """Serialize a registration record to a dictionary.

    Args:
        record: Registration record to serialize.
        omit_nulls: When ``True``, omit keys whose values are ``None``.

    Returns:
        JSON-compatible dictionary representation.
    """
    payload: dict[str, Any] = {
        "strategy_id": record.strategy_id,
        "display_name": record.display_name,
        "strategy_version": record.strategy_version,
        "strategy_family": record.strategy_family.value,
        "priority": record.priority,
        "enabled": record.enabled,
        "registered_at": record.registered_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "state": record.state.value,
        "metadata_fingerprint": record.metadata_fingerprint,
        "metadata": _metadata_to_dict(record.metadata),
        "tags": dict(record.tags),
    }
    if omit_nulls:
        return {key: value for key, value in payload.items() if value is not None}
    return payload


def record_from_dict(data: Mapping[str, Any]) -> StrategyRegistrationRecord:
    """Deserialize a registration record from a dictionary."""
    metadata = _metadata_from_dict(data["metadata"])
    return StrategyRegistrationRecord(
        strategy_id=str(data["strategy_id"]),
        display_name=str(data["display_name"]),
        strategy_version=str(data["strategy_version"]),
        strategy_family=StrategyFamily(str(data["strategy_family"])),
        priority=int(data["priority"]),
        enabled=bool(data["enabled"]),
        registered_at=datetime.fromisoformat(str(data["registered_at"])),
        updated_at=datetime.fromisoformat(str(data["updated_at"])),
        state=RegistrationState(str(data["state"])),
        metadata=metadata,
        metadata_fingerprint=str(data["metadata_fingerprint"]),
        tags=MappingProxyType(dict(data.get("tags") or {})),
    )


def snapshot_to_dict(snapshot: RegistrySnapshot, *, omit_nulls: bool = False) -> dict[str, Any]:
    """Serialize a registry snapshot to a dictionary."""
    payload: dict[str, Any] = {
        "schema_version": STRATEGY_REGISTRY_SCHEMA_VERSION,
        "snapshot_id": snapshot.snapshot_id,
        "created_at": snapshot.created_at.isoformat(),
        "registry_fingerprint": snapshot.registry_fingerprint,
        "freeze_state": snapshot.freeze_state.value,
        "plugin_count": snapshot.plugin_count,
        "enabled_count": snapshot.enabled_count,
        "records": [record_to_dict(record, omit_nulls=omit_nulls) for record in snapshot.records],
    }
    if omit_nulls:
        return {key: value for key, value in payload.items() if value is not None}
    return payload


def snapshot_from_dict(data: Mapping[str, Any]) -> RegistrySnapshot:
    """Deserialize a registry snapshot from a dictionary."""
    schema_version = str(data.get("schema_version", ""))
    if schema_version != STRATEGY_REGISTRY_SCHEMA_VERSION:
        raise StrategyRegistryConfigurationError(
            f"Unsupported schema version: {schema_version!r}.",
            code=ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
            field="schema_version",
        )

    records = tuple(record_from_dict(item) for item in data.get("records", ()))
    created_at = datetime.fromisoformat(str(data["created_at"]))
    fingerprint = str(data["registry_fingerprint"])
    enabled_records = tuple(
        record
        for record in sorted(records, key=_record_sort_key)
        if record.enabled and record.state is RegistrationState.REGISTERED
    )
    return RegistrySnapshot(
        snapshot_id=str(data["snapshot_id"]),
        created_at=created_at,
        registry_fingerprint=fingerprint,
        freeze_state=RegistryFreezeState(str(data["freeze_state"])),
        records=tuple(sorted(records, key=_record_sort_key)),
        enabled_records=enabled_records,
        plugin_count=int(data.get("plugin_count", len(records))),
        enabled_count=int(data.get("enabled_count", len(enabled_records))),
    )


def snapshot_to_json(snapshot: RegistrySnapshot, *, omit_nulls: bool = False) -> str:
    """Serialize a registry snapshot to a JSON string."""
    return json.dumps(snapshot_to_dict(snapshot, omit_nulls=omit_nulls), sort_keys=True)


def snapshot_from_json(payload: str) -> RegistrySnapshot:
    """Deserialize a registry snapshot from a JSON string."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise StrategyRegistryConfigurationError(
            "Malformed registry snapshot JSON.",
            code=ERROR_SERIALIZATION_MALFORMED,
        ) from exc
    if not isinstance(data, dict):
        raise StrategyRegistryConfigurationError(
            "Registry snapshot JSON root must be an object.",
            code=ERROR_SERIALIZATION_MALFORMED,
        )
    return snapshot_from_dict(data)


class StrategyRegistry:
    """Thread-safe catalog of hot-pluggable strategy plugins.

    The registry manages plugin registration, enablement, validation, and
    immutable snapshots. It never evaluates strategies or interacts with brokers.

    Args:
        config: Registry policy configuration.
        clock: Callable returning timezone-aware timestamps for tests.
        engine_run_active: Callable indicating whether an engine run is active.
    """

    def __init__(
        self,
        config: StrategyRegistryConfig | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        engine_run_active: Callable[[], bool] | None = None,
    ) -> None:
        self._config = config or StrategyRegistryConfig()
        self._clock = clock or _utc_now
        self._engine_run_active = engine_run_active or (lambda: False)
        self._lock = threading.RLock()
        self._entries: dict[str, _StrategyRegistrationEntry] = {}
        self._freeze_state = RegistryFreezeState.MUTABLE

    @property
    def config(self) -> StrategyRegistryConfig:
        """Return immutable registry configuration."""
        return self._config

    def is_frozen(self) -> bool:
        """Return ``True`` when structural mutations are blocked."""
        with self._lock:
            return self._freeze_state is RegistryFreezeState.FROZEN

    def count(self) -> int:
        """Return the total number of registered plugins."""
        with self._lock:
            return len(self._entries)

    def enabled_count(self) -> int:
        """Return the number of enabled, non-pending plugins."""
        with self._lock:
            return sum(
                1
                for entry in self._entries.values()
                if entry.record.enabled and entry.record.state is RegistrationState.REGISTERED
            )

    def register(
        self,
        strategy: BaseStrategy,
        *,
        enabled: bool | None = None,
        priority: int | None = None,
    ) -> None:
        """Register a strategy plugin in the catalog.

        Args:
            strategy: Pre-constructed strategy plugin instance.
            enabled: Optional enablement override for the registration record.
            priority: Optional priority override for the registration record.

        Raises:
            StrategyRegistryFrozenError: If the registry is frozen.
            StrategyRegistryConfigurationError: If plugin validation fails.
            StrategyRegistryDuplicateError: If duplicate ID is rejected.
            StrategyRegistryLimitError: If max plugin count is exceeded.
        """
        plugin_config = _validate_plugin(strategy)
        strategy_id = plugin_config.metadata.strategy_id
        effective_enabled = plugin_config.enabled if enabled is None else enabled
        effective_priority = plugin_config.priority if priority is None else priority

        if not (MIN_PRIORITY <= effective_priority <= MAX_PRIORITY):
            raise StrategyRegistryConfigurationError(
                f"priority must be within [{MIN_PRIORITY}, {MAX_PRIORITY}].",
                code=ERROR_CONFIG_INVALID,
                strategy_id=strategy_id,
                field="priority",
            )

        with self._lock:
            self._assert_not_frozen_for_structural("register")
            existing = self._entries.get(strategy_id)
            if existing is not None:
                self._handle_duplicate_registration(
                    strategy_id=strategy_id,
                    strategy=strategy,
                    existing=existing,
                    enabled=effective_enabled,
                    priority=effective_priority,
                )
                return

            if len(self._entries) >= self._config.max_plugins:
                raise StrategyRegistryLimitError(
                    f"Registry plugin limit exceeded: max_plugins={self._config.max_plugins}.",
                    code=ERROR_LIMIT_EXCEEDED,
                    strategy_id=strategy_id,
                )

            now = self._clock()
            record = _build_registration_record(
                strategy,
                enabled=effective_enabled,
                priority=effective_priority,
                now=now,
            )
            self._entries[strategy_id] = _StrategyRegistrationEntry(strategy=strategy, record=record)
            _logger.info(
                "strategy.registry.register",
                extra={"strategy_id": strategy_id, "priority": effective_priority, "enabled": effective_enabled},
            )

    def replace(self, strategy_id: str, strategy: BaseStrategy) -> None:
        """Replace an existing plugin instance under the same ``strategy_id``.

        Args:
            strategy_id: Identifier of the plugin to replace.
            strategy: New plugin instance.

        Raises:
            StrategyRegistryFrozenError: If the registry is frozen.
            StrategyRegistryNotFoundError: If the identifier is not registered.
            StrategyRegistryConfigurationError: If validation fails or IDs mismatch.
        """
        plugin_config = _validate_plugin(strategy)
        if plugin_config.metadata.strategy_id != strategy_id:
            raise StrategyRegistryConfigurationError(
                "Replacement strategy metadata.strategy_id must match strategy_id argument.",
                code=ERROR_CONFIG_INVALID,
                strategy_id=strategy_id,
                field="strategy_id",
            )

        with self._lock:
            self._assert_not_frozen_for_structural("replace")
            entry = self._entries.get(strategy_id)
            if entry is None:
                raise StrategyRegistryNotFoundError(
                    f"Strategy '{strategy_id}' is not registered.",
                    code=ERROR_NOT_FOUND,
                    strategy_id=strategy_id,
                )

            now = self._clock()
            preserved_registered_at = entry.record.registered_at
            record = _build_registration_record(
                strategy,
                enabled=entry.record.enabled,
                priority=entry.record.priority,
                now=now,
                registered_at=preserved_registered_at,
            )
            self._entries[strategy_id] = _StrategyRegistrationEntry(strategy=strategy, record=record)
            _logger.info("strategy.registry.replace", extra={"strategy_id": strategy_id})

    def unregister(self, strategy_id: str) -> bool:
        """Remove a strategy plugin from the catalog.

        Args:
            strategy_id: Identifier of the plugin to remove.

        Returns:
            ``True`` if the plugin was removed or marked pending removal.

        Raises:
            StrategyRegistryFrozenError: If the registry is frozen.
        """
        with self._lock:
            self._assert_not_frozen_for_structural("unregister")
            entry = self._entries.get(strategy_id)
            if entry is None:
                _logger.warning(
                    "strategy.registry.unregister.not_found",
                    extra={"strategy_id": strategy_id},
                )
                return False

            if self._config.defer_unregistration and self._engine_run_active():
                now = self._clock()
                pending_record = replace(
                    entry.record,
                    enabled=False,
                    updated_at=now,
                    state=RegistrationState.PENDING_REMOVAL,
                )
                self._entries[strategy_id] = _StrategyRegistrationEntry(
                    strategy=entry.strategy,
                    record=pending_record,
                )
                _logger.info(
                    "strategy.registry.unregister.deferred",
                    extra={"strategy_id": strategy_id},
                )
                return True

            del self._entries[strategy_id]
            _logger.info("strategy.registry.unregister", extra={"strategy_id": strategy_id})
            return True

    def get(self, strategy_id: str) -> BaseStrategy:
        """Return the registered plugin instance for ``strategy_id``.

        Args:
            strategy_id: Plugin identifier.

        Returns:
            Registered :class:`BaseStrategy` instance.

        Raises:
            StrategyRegistryNotFoundError: If the identifier is not registered.
        """
        with self._lock:
            entry = self._entries.get(strategy_id)
            if entry is None:
                raise StrategyRegistryNotFoundError(
                    f"Strategy '{strategy_id}' is not registered.",
                    code=ERROR_NOT_FOUND,
                    strategy_id=strategy_id,
                )
            return entry.strategy

    def get_record(self, strategy_id: str) -> StrategyRegistrationRecord:
        """Return the immutable registration record for ``strategy_id``.

        Args:
            strategy_id: Plugin identifier.

        Returns:
            Immutable registration record.

        Raises:
            StrategyRegistryNotFoundError: If the identifier is not registered.
        """
        with self._lock:
            entry = self._entries.get(strategy_id)
            if entry is None:
                raise StrategyRegistryNotFoundError(
                    f"Strategy '{strategy_id}' is not registered.",
                    code=ERROR_NOT_FOUND,
                    strategy_id=strategy_id,
                )
            return entry.record

    def exists(self, strategy_id: str) -> bool:
        """Return ``True`` if ``strategy_id`` is registered."""
        with self._lock:
            return strategy_id in self._entries

    def get_all(self) -> tuple[StrategyRegistrationRecord, ...]:
        """Return all registration records in priority order."""
        with self._lock:
            records = tuple(entry.record for entry in self._entries.values())
            return tuple(sorted(records, key=_record_sort_key))

    def enabled(self) -> tuple[StrategyRegistrationRecord, ...]:
        """Return enabled, registered records in priority order."""
        with self._lock:
            records = tuple(
                entry.record
                for entry in self._entries.values()
                if entry.record.enabled and entry.record.state is RegistrationState.REGISTERED
            )
            return tuple(sorted(records, key=_record_sort_key))

    def disabled(self) -> tuple[StrategyRegistrationRecord, ...]:
        """Return disabled or pending-removal records in priority order."""
        with self._lock:
            records = tuple(
                entry.record
                for entry in self._entries.values()
                if not entry.record.enabled or entry.record.state is not RegistrationState.REGISTERED
            )
            return tuple(sorted(records, key=_record_sort_key))

    def enable(self, strategy_id: str) -> None:
        """Enable a registered strategy plugin.

        Args:
            strategy_id: Plugin identifier.

        Raises:
            StrategyRegistryFrozenError: If frozen and policy disallows toggling.
            StrategyRegistryNotFoundError: If the identifier is not registered.
            StrategyRegistryInvalidStateError: If the entry is pending removal.
        """
        with self._lock:
            self._assert_enable_disable_allowed()
            entry = self._require_mutable_entry(strategy_id, operation="enable")
            if entry.record.state is RegistrationState.PENDING_REMOVAL:
                raise StrategyRegistryInvalidStateError(
                    f"Cannot enable strategy '{strategy_id}' while pending removal.",
                    code=ERROR_INVALID_STATE,
                    strategy_id=strategy_id,
                )
            if entry.record.enabled:
                _logger.debug("strategy.registry.enable.noop", extra={"strategy_id": strategy_id})
                return

            now = self._clock()
            entry.record = replace(entry.record, enabled=True, updated_at=now)
            _logger.info("strategy.registry.enable", extra={"strategy_id": strategy_id})

    def disable(self, strategy_id: str) -> None:
        """Disable a registered strategy plugin.

        Args:
            strategy_id: Plugin identifier.

        Raises:
            StrategyRegistryFrozenError: If frozen and policy disallows toggling.
            StrategyRegistryNotFoundError: If the identifier is not registered.
            StrategyRegistryInvalidStateError: If the entry is pending removal.
        """
        with self._lock:
            self._assert_enable_disable_allowed()
            entry = self._require_mutable_entry(strategy_id, operation="disable")
            if entry.record.state is RegistrationState.PENDING_REMOVAL:
                raise StrategyRegistryInvalidStateError(
                    f"Cannot disable strategy '{strategy_id}' while pending removal.",
                    code=ERROR_INVALID_STATE,
                    strategy_id=strategy_id,
                )
            if not entry.record.enabled:
                _logger.debug("strategy.registry.disable.noop", extra={"strategy_id": strategy_id})
                return

            now = self._clock()
            entry.record = replace(entry.record, enabled=False, updated_at=now)
            _logger.info("strategy.registry.disable", extra={"strategy_id": strategy_id})

    def validate(self) -> RegistryValidationResult:
        """Validate registry consistency without mutating state."""
        with self._lock:
            errors: list[RegistryValidationRecord] = []
            warnings: list[RegistryValidationRecord] = []

            records = list(self._entries.values())
            if not records:
                warnings.append(
                    RegistryValidationRecord(
                        code=ERROR_EMPTY_ENABLED_SET,
                        message="Registry has zero registered plugins.",
                    )
                )
                return RegistryValidationResult(errors=tuple(errors), warnings=tuple(warnings))

            if len(records) > self._config.max_plugins:
                errors.append(
                    RegistryValidationRecord(
                        code=ERROR_LIMIT_EXCEEDED,
                        message=f"Registry count exceeds max_plugins={self._config.max_plugins}.",
                    )
                )

            enabled_count = 0
            fingerprint_map: dict[str, list[str]] = {}
            family_map: dict[StrategyFamily, list[str]] = {}
            display_name_map: dict[str, list[str]] = {}

            for entry in records:
                record = entry.record
                strategy = entry.strategy
                strategy_id = record.strategy_id

                if not (MIN_PRIORITY <= record.priority <= MAX_PRIORITY):
                    errors.append(
                        RegistryValidationRecord(
                            code=ERROR_CONFIG_INVALID,
                            message="priority must be within configured bounds.",
                            strategy_id=strategy_id,
                            field="priority",
                        )
                    )

                if record.strategy_id != record.metadata.strategy_id:
                    errors.append(
                        RegistryValidationRecord(
                            code=ERROR_CONFIG_INVALID,
                            message="record.strategy_id must match metadata.strategy_id.",
                            strategy_id=strategy_id,
                            field="strategy_id",
                        )
                    )

                if strategy.engine_name != record.strategy_id:
                    errors.append(
                        RegistryValidationRecord(
                            code=ERROR_CONFIG_INVALID,
                            message="plugin engine_name must match record.strategy_id.",
                            strategy_id=strategy_id,
                            field="engine_name",
                        )
                    )

                if record.state is RegistrationState.PENDING_REMOVAL and record.enabled:
                    errors.append(
                        RegistryValidationRecord(
                            code=ERROR_INVALID_STATE,
                            message="Pending removal entry must not remain enabled.",
                            strategy_id=strategy_id,
                            field="enabled",
                        )
                    )

                for field_name in ("registered_at", "updated_at"):
                    timestamp = getattr(record, field_name)
                    if timestamp.tzinfo is None or timestamp.tzinfo.utcoffset(timestamp) is None:
                        errors.append(
                            RegistryValidationRecord(
                                code=ERROR_CONFIG_INVALID,
                                message=f"{field_name} must be timezone-aware.",
                                strategy_id=strategy_id,
                                field=field_name,
                            )
                        )

                if (
                    record.registered_at.tzinfo is not None
                    and record.registered_at.tzinfo.utcoffset(record.registered_at) is not None
                    and record.updated_at.tzinfo is not None
                    and record.updated_at.tzinfo.utcoffset(record.updated_at) is not None
                    and record.registered_at > record.updated_at
                ):
                    errors.append(
                        RegistryValidationRecord(
                            code=ERROR_CONFIG_INVALID,
                            message="registered_at must not exceed updated_at.",
                            strategy_id=strategy_id,
                            field="registered_at",
                        )
                    )

                if record.enabled and record.state is RegistrationState.REGISTERED:
                    enabled_count += 1
                    family_map.setdefault(record.strategy_family, []).append(strategy_id)

                fingerprint_map.setdefault(record.metadata_fingerprint, []).append(strategy_id)
                display_name_map.setdefault(record.display_name.strip().lower(), []).append(strategy_id)

            if enabled_count == 0:
                warnings.append(
                    RegistryValidationRecord(
                        code=ERROR_EMPTY_ENABLED_SET,
                        message="Registry has zero enabled plugins.",
                    )
                )

            for ids in fingerprint_map.values():
                if len(ids) > 1:
                    warnings.append(
                        RegistryValidationRecord(
                            code=ERROR_DUPLICATE_FINGERPRINT,
                            message=f"Duplicate metadata fingerprint across strategies: {sorted(ids)}.",
                            strategy_id=ids[0],
                        )
                    )

            for ids in family_map.values():
                if len(ids) > 1:
                    warnings.append(
                        RegistryValidationRecord(
                            code="STRATEGY_REGISTRY.DUPLICATE_FAMILY_ENABLED",
                            message=f"Multiple enabled plugins share strategy_family: {sorted(ids)}.",
                            strategy_id=ids[0],
                        )
                    )

            for ids in display_name_map.values():
                if len(ids) > 1:
                    warnings.append(
                        RegistryValidationRecord(
                            code="STRATEGY_REGISTRY.DISPLAY_NAME_COLLISION",
                            message=f"Display name collision across strategies: {sorted(ids)}.",
                            strategy_id=ids[0],
                        )
                    )

            if errors:
                _logger.error(
                    "strategy.registry.validate.failed",
                    extra={"error_count": len(errors), "warning_count": len(warnings)},
                )

            return RegistryValidationResult(errors=tuple(errors), warnings=tuple(warnings))

    def assert_valid(self) -> None:
        """Validate the registry and raise if errors are present.

        Raises:
            StrategyRegistryValidationError: If validation errors exist.
        """
        result = self.validate()
        if not result.is_valid:
            first = result.errors[0]
            raise StrategyRegistryValidationError(
                first.message,
                code=ERROR_VALIDATION_FAILED,
                strategy_id=first.strategy_id,
                field=first.field,
            )

    def snapshot(self) -> RegistrySnapshot:
        """Return an immutable snapshot without changing freeze state."""
        with self._lock:
            records = tuple(entry.record for entry in self._entries.values())
            return _build_snapshot(
                records,
                freeze_state=self._freeze_state,
                created_at=self._clock(),
            )

    def freeze(self) -> RegistrySnapshot:
        """Freeze the registry and return an immutable snapshot.

        Structural mutations are rejected after freezing until the registry
        instance is discarded or a future thaw API is introduced.

        Returns:
            Immutable snapshot captured at freeze time.
        """
        with self._lock:
            self._freeze_state = RegistryFreezeState.FROZEN
            records = tuple(entry.record for entry in self._entries.values())
            snapshot = _build_snapshot(
                records,
                freeze_state=RegistryFreezeState.FROZEN,
                created_at=self._clock(),
            )
            _logger.info(
                "strategy.registry.freeze",
                extra={
                    "snapshot_id": snapshot.snapshot_id,
                    "plugin_count": snapshot.plugin_count,
                    "enabled_count": snapshot.enabled_count,
                },
            )
            return snapshot

    def clear(self) -> None:
        """Remove all plugins from the registry.

        Intended for tests. Raises when the registry is frozen.
        """
        with self._lock:
            self._assert_not_frozen_for_structural("clear")
            self._entries.clear()

    def register_batch(
        self,
        descriptors: tuple[StrategyDiscoveryDescriptor, ...] | list[StrategyDiscoveryDescriptor],
    ) -> RegistryBatchResult:
        """Register multiple strategy plugins from discovery descriptors.

        Args:
            descriptors: Batch of discovery descriptors.

        Returns:
            Structured batch registration outcome.
        """
        registered: list[str] = []
        failed: list[RegistryValidationRecord] = []
        skipped: list[str] = []

        for descriptor in descriptors:
            strategy = descriptor.strategy
            try:
                plugin_config = _validate_plugin(strategy)
                strategy_id = plugin_config.metadata.strategy_id
                if (
                    self.exists(strategy_id)
                    and self._config.duplicate_policy is DuplicateRegistrationPolicy.IGNORE
                    and self.get_record(strategy_id).metadata_fingerprint
                    == strategy.metadata_fingerprint()
                ):
                    skipped.append(strategy_id)
                    continue

                priority = (
                    descriptor.priority_override
                    if descriptor.priority_override is not None
                    else plugin_config.priority
                )
                self.register(
                    strategy,
                    enabled=descriptor.enabled,
                    priority=priority,
                )
                registered.append(strategy_id)
            except StrategyRegistryDuplicateError as exc:
                failed.append(
                    RegistryValidationRecord(
                        code=exc.code,
                        message=str(exc),
                        strategy_id=exc.strategy_id,
                        field=exc.field,
                    )
                )
                if self._config.strict_batch:
                    break
            except StrategyRegistryError as exc:
                failed.append(
                    RegistryValidationRecord(
                        code=exc.code,
                        message=str(exc),
                        strategy_id=exc.strategy_id,
                        field=exc.field,
                    )
                )
                if self._config.strict_batch:
                    break

        if failed and not self._config.strict_batch:
            _logger.warning(
                "strategy.registry.register_batch.partial_failure",
                extra={"failed_count": len(failed)},
            )

        return RegistryBatchResult(
            registered_ids=tuple(registered),
            failed=tuple(failed),
            skipped=tuple(skipped),
        )

    def commit_pending_removals(self) -> tuple[str, ...]:
        """Finalize deferred unregistrations marked as pending removal.

        Returns:
            Tuple of strategy IDs physically removed from the registry.
        """
        removed: list[str] = []
        with self._lock:
            pending_ids = [
                strategy_id
                for strategy_id, entry in self._entries.items()
                if entry.record.state is RegistrationState.PENDING_REMOVAL
            ]
            for strategy_id in pending_ids:
                del self._entries[strategy_id]
                removed.append(strategy_id)
                _logger.info(
                    "strategy.registry.commit_pending_removals",
                    extra={"strategy_id": strategy_id},
                )
        return tuple(removed)

    def detect_duplicates(self) -> RegistryDuplicateReport:
        """Scan the registry for duplicate fingerprints and display names."""
        with self._lock:
            fingerprint_groups: dict[str, list[str]] = {}
            display_name_groups: dict[str, list[str]] = {}

            for strategy_id, entry in self._entries.items():
                fingerprint_groups.setdefault(entry.record.metadata_fingerprint, []).append(strategy_id)
                key = entry.record.display_name.strip().lower()
                display_name_groups.setdefault(key, []).append(strategy_id)

            fingerprint_duplicates = tuple(
                tuple(sorted(ids))
                for ids in fingerprint_groups.values()
                if len(ids) > 1
            )
            display_name_collisions = tuple(
                tuple(sorted(ids))
                for ids in display_name_groups.values()
                if len(ids) > 1
            )
            return RegistryDuplicateReport(
                fingerprint_duplicates=fingerprint_duplicates,
                display_name_collisions=display_name_collisions,
            )

    def find_by_fingerprint(self, fingerprint: str) -> tuple[str, ...]:
        """Return strategy IDs whose metadata fingerprint matches ``fingerprint``."""
        with self._lock:
            matches = [
                strategy_id
                for strategy_id, entry in self._entries.items()
                if entry.record.metadata_fingerprint == fingerprint
            ]
            return tuple(sorted(matches))

    def _assert_not_frozen_for_structural(self, operation: str) -> None:
        if self._freeze_state is RegistryFreezeState.FROZEN:
            raise StrategyRegistryFrozenError(
                f"Cannot {operation} while registry is frozen.",
                code=ERROR_FROZEN,
            )

    def _assert_enable_disable_allowed(self) -> None:
        if (
            self._freeze_state is RegistryFreezeState.FROZEN
            and not self._config.allow_enable_disable_while_frozen
        ):
            raise StrategyRegistryFrozenError(
                "Cannot enable/disable while registry is frozen.",
                code=ERROR_FROZEN,
            )

    def _require_mutable_entry(self, strategy_id: str, *, operation: str) -> _StrategyRegistrationEntry:
        entry = self._entries.get(strategy_id)
        if entry is None:
            raise StrategyRegistryNotFoundError(
                f"Strategy '{strategy_id}' is not registered.",
                code=ERROR_NOT_FOUND,
                strategy_id=strategy_id,
            )
        return entry

    def _handle_duplicate_registration(
        self,
        *,
        strategy_id: str,
        strategy: BaseStrategy,
        existing: _StrategyRegistrationEntry,
        enabled: bool,
        priority: int,
    ) -> None:
        policy = self._config.duplicate_policy
        new_fingerprint = strategy.metadata_fingerprint()

        if policy is DuplicateRegistrationPolicy.REJECT:
            _logger.warning(
                "strategy.registry.register.rejected",
                extra={"strategy_id": strategy_id, "policy": policy.value},
            )
            raise StrategyRegistryDuplicateError(
                f"Strategy '{strategy_id}' is already registered.",
                code=ERROR_DUPLICATE_ID,
                strategy_id=strategy_id,
            )

        if policy is DuplicateRegistrationPolicy.IGNORE:
            if existing.record.metadata_fingerprint == new_fingerprint:
                _logger.debug(
                    "strategy.registry.register.ignored",
                    extra={"strategy_id": strategy_id},
                )
                return
            _logger.warning(
                "strategy.registry.register.rejected",
                extra={"strategy_id": strategy_id, "policy": policy.value},
            )
            raise StrategyRegistryDuplicateError(
                f"Strategy '{strategy_id}' is already registered with different metadata.",
                code=ERROR_DUPLICATE_ID,
                strategy_id=strategy_id,
            )

        now = self._clock()
        preserved_registered_at = existing.record.registered_at
        record = _build_registration_record(
            strategy,
            enabled=enabled,
            priority=priority,
            now=now,
            registered_at=preserved_registered_at,
        )
        self._entries[strategy_id] = _StrategyRegistrationEntry(strategy=strategy, record=record)
        _logger.info(
            "strategy.registry.replace",
            extra={"strategy_id": strategy_id, "via": "duplicate_policy_replace"},
        )


__all__ = [
    "DEFAULT_MAX_PLUGINS",
    "DuplicateRegistrationPolicy",
    "ERROR_BATCH_PARTIAL_FAILURE",
    "ERROR_CONFIG_INVALID",
    "ERROR_DUPLICATE_FINGERPRINT",
    "ERROR_DUPLICATE_ID",
    "ERROR_EMPTY_ENABLED_SET",
    "ERROR_FROZEN",
    "ERROR_INVALID_STATE",
    "ERROR_LIMIT_EXCEEDED",
    "ERROR_NOT_FOUND",
    "ERROR_SERIALIZATION_MALFORMED",
    "ERROR_SERIALIZATION_UNSUPPORTED_VERSION",
    "ERROR_TYPE_INVALID",
    "ERROR_VALIDATION_FAILED",
    "MAX_PRIORITY",
    "MIN_PRIORITY",
    "RegistrationState",
    "RegistryBatchResult",
    "RegistryDuplicateReport",
    "RegistryFreezeState",
    "RegistrySnapshot",
    "RegistryValidationRecord",
    "RegistryValidationResult",
    "STRATEGY_REGISTRY_SCHEMA_VERSION",
    "STRATEGY_REGISTRY_VERSION",
    "StrategyDiscoveryDescriptor",
    "StrategyRegistrationRecord",
    "StrategyRegistry",
    "StrategyRegistryConfig",
    "StrategyRegistryConfigurationError",
    "StrategyRegistryDuplicateError",
    "StrategyRegistryError",
    "StrategyRegistryFrozenError",
    "StrategyRegistryInvalidStateError",
    "StrategyRegistryLimitError",
    "StrategyRegistryNotFoundError",
    "StrategyRegistryValidationError",
    "record_from_dict",
    "record_to_dict",
    "registry_fingerprint",
    "snapshot_from_dict",
    "snapshot_from_json",
    "snapshot_to_dict",
    "snapshot_to_json",
]
