"""Unit tests for strategy.registry."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from strategy.base_strategy import (
    BaseStrategy,
    StrategyContext,
    StrategyMetadata,
    StrategyPluginConfig,
)
from strategy.registry import (
    DEFAULT_MAX_PLUGINS,
    ERROR_CONFIG_INVALID,
    ERROR_DUPLICATE_FINGERPRINT,
    ERROR_DUPLICATE_ID,
    ERROR_EMPTY_ENABLED_SET,
    ERROR_FROZEN,
    ERROR_INVALID_STATE,
    ERROR_LIMIT_EXCEEDED,
    ERROR_NOT_FOUND,
    ERROR_SERIALIZATION_MALFORMED,
    ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
    ERROR_TYPE_INVALID,
    ERROR_VALIDATION_FAILED,
    MAX_PRIORITY,
    MIN_PRIORITY,
    DuplicateRegistrationPolicy,
    RegistrationState,
    RegistryFreezeState,
    StrategyDiscoveryDescriptor,
    StrategyRegistry,
    StrategyRegistryConfig,
    StrategyRegistryConfigurationError,
    StrategyRegistryDuplicateError,
    StrategyRegistryError,
    StrategyRegistryFrozenError,
    StrategyRegistryInvalidStateError,
    StrategyRegistryLimitError,
    StrategyRegistryNotFoundError,
    StrategyRegistryValidationError,
    record_from_dict,
    record_to_dict,
    registry_fingerprint,
    snapshot_from_dict,
    snapshot_from_json,
    snapshot_to_dict,
    snapshot_to_json,
)
from strategy.signals import StrategyFamily


class FixedClock:
    """Deterministic clock for registry timestamp tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self._current = start or datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc)
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._current

    def advance(self, seconds: float = 1.0) -> None:
        with self._lock:
            self._current = self._current.replace(
                microsecond=min(self._current.microsecond + int(seconds * 1_000_000), 999_999)
            )


def valid_metadata(**overrides: object) -> StrategyMetadata:
    """Build valid strategy metadata."""
    defaults: dict[str, object] = {
        "strategy_id": "short_strangle",
        "display_name": "Short Strangle",
        "version": "1.0.0",
        "strategy_family": StrategyFamily.SHORT_STRANGLE,
    }
    defaults.update(overrides)
    return StrategyMetadata(**defaults)  # type: ignore[arg-type]


def make_strategy(
    *,
    strategy_id: str = "short_strangle",
    display_name: str = "Short Strangle",
    family: StrategyFamily = StrategyFamily.SHORT_STRANGLE,
    priority: int = 650,
    enabled: bool = True,
    version: str = "1.0.0",
) -> BaseStrategy:
    """Build a minimal valid strategy plugin for registry tests."""

    class _RegistryTestStrategy(BaseStrategy):
        def _execute(self, context: StrategyContext) -> object:
            return self.build_abstain_signal(context)

    metadata = valid_metadata(
        strategy_id=strategy_id,
        display_name=display_name,
        strategy_family=family,
        version=version,
    )
    config = StrategyPluginConfig(metadata=metadata, enabled=enabled, priority=priority)
    return _RegistryTestStrategy(config)


class MismatchedEngineNameStrategy(BaseStrategy):
    """Strategy whose engine_name intentionally diverges from metadata."""

    def __init__(self) -> None:
        metadata = valid_metadata(strategy_id="mismatch_test")
        super().__init__(StrategyPluginConfig(metadata=metadata))

    @property
    def engine_name(self) -> str:
        return "wrong_engine_name"

    def _execute(self, context: StrategyContext) -> object:
        return self.build_abstain_signal(context)


@pytest.fixture
def clock() -> FixedClock:
    """Provide a fixed UTC clock."""
    return FixedClock()


@pytest.fixture
def registry(clock: FixedClock) -> StrategyRegistry:
    """Provide an empty registry with deterministic clock."""
    return StrategyRegistry(clock=clock)


class TestStrategyRegistryConfig:
    def test_default_config(self) -> None:
        config = StrategyRegistryConfig()
        assert config.duplicate_policy is DuplicateRegistrationPolicy.REJECT
        assert config.max_plugins == DEFAULT_MAX_PLUGINS
        assert config.defer_unregistration is False
        assert config.allow_enable_disable_while_frozen is True

    def test_invalid_max_plugins_raises(self) -> None:
        with pytest.raises(StrategyRegistryConfigurationError) as exc_info:
            StrategyRegistryConfig(max_plugins=0)
        assert exc_info.value.code == ERROR_CONFIG_INVALID


class TestRegistrationHappyPath:
    def test_register_and_get(self, registry: StrategyRegistry) -> None:
        strategy = make_strategy()
        registry.register(strategy)
        assert registry.exists("short_strangle")
        assert registry.get("short_strangle") is strategy

    def test_get_record_fields(self, registry: StrategyRegistry) -> None:
        strategy = make_strategy(priority=700)
        registry.register(strategy, enabled=True)
        record = registry.get_record("short_strangle")
        assert record.strategy_id == "short_strangle"
        assert record.display_name == "Short Strangle"
        assert record.strategy_version == "1.0.0"
        assert record.strategy_family is StrategyFamily.SHORT_STRANGLE
        assert record.priority == 700
        assert record.enabled is True
        assert record.state is RegistrationState.REGISTERED
        assert record.metadata_fingerprint == strategy.metadata_fingerprint()

    def test_register_with_overrides(self, registry: StrategyRegistry) -> None:
        strategy = make_strategy(priority=500, enabled=True)
        registry.register(strategy, enabled=False, priority=800)
        record = registry.get_record("short_strangle")
        assert record.enabled is False
        assert record.priority == 800

    def test_count_and_enabled_count(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(strategy_id="alpha", priority=700))
        registry.register(make_strategy(strategy_id="beta", priority=600), enabled=False)
        assert registry.count() == 2
        assert registry.enabled_count() == 1


class TestDuplicatePolicies:
    def test_reject_duplicate_id(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy())
        duplicate = make_strategy()
        with pytest.raises(StrategyRegistryDuplicateError) as exc_info:
            registry.register(duplicate)
        assert exc_info.value.code == ERROR_DUPLICATE_ID

    def test_replace_duplicate_id(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(
            StrategyRegistryConfig(duplicate_policy=DuplicateRegistrationPolicy.REPLACE),
            clock=clock,
        )
        first = make_strategy(version="1.0.0")
        reg.register(first)
        registered_at = reg.get_record("short_strangle").registered_at
        clock.advance(5.0)
        second = make_strategy(version="1.0.1")
        reg.register(second)
        record = reg.get_record("short_strangle")
        assert record.strategy_version == "1.0.1"
        assert record.registered_at == registered_at
        assert record.updated_at > registered_at
        assert reg.get("short_strangle") is second

    def test_ignore_identical_fingerprint(self) -> None:
        reg = StrategyRegistry(StrategyRegistryConfig(duplicate_policy=DuplicateRegistrationPolicy.IGNORE))
        first = make_strategy()
        reg.register(first)
        duplicate = make_strategy()
        reg.register(duplicate)
        assert reg.count() == 1
        assert reg.get("short_strangle") is first

    def test_ignore_different_metadata_rejects(self) -> None:
        reg = StrategyRegistry(StrategyRegistryConfig(duplicate_policy=DuplicateRegistrationPolicy.IGNORE))
        reg.register(make_strategy(version="1.0.0"))
        with pytest.raises(StrategyRegistryDuplicateError):
            reg.register(make_strategy(version="1.0.1"))

    def test_replace_explicit(self, registry: StrategyRegistry, clock: FixedClock) -> None:
        first = make_strategy(version="1.0.0")
        registry.register(first)
        registered_at = registry.get_record("short_strangle").registered_at
        clock.advance(2.0)
        second = make_strategy(version="2.0.0")
        registry.replace("short_strangle", second)
        record = registry.get_record("short_strangle")
        assert record.strategy_version == "2.0.0"
        assert record.registered_at == registered_at
        assert registry.get("short_strangle") is second

    def test_replace_unknown_raises(self, registry: StrategyRegistry) -> None:
        with pytest.raises(StrategyRegistryNotFoundError):
            registry.replace("missing", make_strategy(strategy_id="missing"))


class TestUnregisterAndDeferredRemoval:
    def test_unregister_existing(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy())
        assert registry.unregister("short_strangle") is True
        assert not registry.exists("short_strangle")

    def test_unregister_unknown_returns_false(self, registry: StrategyRegistry) -> None:
        assert registry.unregister("missing") is False

    def test_deferred_unregistration(self, clock: FixedClock) -> None:
        active = threading.Event()
        active.set()
        reg = StrategyRegistry(
            StrategyRegistryConfig(defer_unregistration=True),
            clock=clock,
            engine_run_active=active.is_set,
        )
        reg.register(make_strategy())
        assert reg.unregister("short_strangle") is True
        assert reg.exists("short_strangle")
        record = reg.get_record("short_strangle")
        assert record.state is RegistrationState.PENDING_REMOVAL
        assert record.enabled is False
        assert reg.enabled_count() == 0

        removed = reg.commit_pending_removals()
        assert removed == ("short_strangle",)
        assert not reg.exists("short_strangle")


class TestLookupAndListing:
    def test_get_not_found(self, registry: StrategyRegistry) -> None:
        with pytest.raises(StrategyRegistryNotFoundError) as exc_info:
            registry.get("missing")
        assert exc_info.value.code == ERROR_NOT_FOUND

    def test_priority_ordering(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(strategy_id="long_volatility", family=StrategyFamily.LONG_VOLATILITY, priority=400))
        registry.register(make_strategy(strategy_id="iron_condor", family=StrategyFamily.IRON_CONDOR, priority=700))
        registry.register(make_strategy(strategy_id="bull_put_spread", family=StrategyFamily.BULL_PUT_SPREAD, priority=650))
        registry.register(make_strategy(strategy_id="short_strangle", priority=650))

        ordered_ids = [record.strategy_id for record in registry.get_all()]
        assert ordered_ids == [
            "iron_condor",
            "bull_put_spread",
            "short_strangle",
            "long_volatility",
        ]

    def test_enabled_and_disabled_views(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(strategy_id="enabled_one", priority=700))
        registry.register(make_strategy(strategy_id="disabled_one", priority=600), enabled=False)
        enabled_ids = [record.strategy_id for record in registry.enabled()]
        disabled_ids = [record.strategy_id for record in registry.disabled()]
        assert enabled_ids == ["enabled_one"]
        assert disabled_ids == ["disabled_one"]


class TestEnableDisable:
    def test_enable_disable_toggle(self, registry: StrategyRegistry, clock: FixedClock) -> None:
        registry.register(make_strategy(), enabled=False)
        registry.enable("short_strangle")
        assert registry.get_record("short_strangle").enabled is True
        clock.advance(1.0)
        registry.disable("short_strangle")
        assert registry.get_record("short_strangle").enabled is False

    def test_enable_unknown_raises(self, registry: StrategyRegistry) -> None:
        with pytest.raises(StrategyRegistryNotFoundError):
            registry.enable("missing")

    def test_enable_pending_removal_raises(self, clock: FixedClock) -> None:
        active = threading.Event()
        active.set()
        reg = StrategyRegistry(
            StrategyRegistryConfig(defer_unregistration=True),
            clock=clock,
            engine_run_active=active.is_set,
        )
        reg.register(make_strategy())
        reg.unregister("short_strangle")
        with pytest.raises(StrategyRegistryInvalidStateError) as exc_info:
            reg.enable("short_strangle")
        assert exc_info.value.code == ERROR_INVALID_STATE

    def test_enable_noop_when_already_enabled(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(), enabled=True)
        registry.enable("short_strangle")
        assert registry.get_record("short_strangle").enabled is True

    def test_disable_noop_when_already_disabled(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(), enabled=False)
        registry.disable("short_strangle")
        assert registry.get_record("short_strangle").enabled is False


class TestFreeze:
    def test_freeze_blocks_structural_mutations(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy())
        snapshot = registry.freeze()
        assert registry.is_frozen()
        assert snapshot.freeze_state is RegistryFreezeState.FROZEN

        with pytest.raises(StrategyRegistryFrozenError) as exc_info:
            registry.register(make_strategy(strategy_id="iron_condor", family=StrategyFamily.IRON_CONDOR))
        assert exc_info.value.code == ERROR_FROZEN

        with pytest.raises(StrategyRegistryFrozenError):
            registry.unregister("short_strangle")

        with pytest.raises(StrategyRegistryFrozenError):
            registry.replace("short_strangle", make_strategy())

        with pytest.raises(StrategyRegistryFrozenError):
            registry.clear()

    def test_enable_disable_allowed_while_frozen_by_default(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(), enabled=True)
        registry.freeze()
        registry.disable("short_strangle")
        assert registry.get_record("short_strangle").enabled is False

    def test_enable_disable_blocked_when_configured(self, registry: StrategyRegistry) -> None:
        reg = StrategyRegistry(
            StrategyRegistryConfig(allow_enable_disable_while_frozen=False),
            clock=registry._clock,  # type: ignore[attr-defined]
        )
        reg.register(make_strategy())
        reg.freeze()
        with pytest.raises(StrategyRegistryFrozenError):
            reg.disable("short_strangle")

    def test_snapshot_without_freeze(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy())
        snap = registry.snapshot()
        assert snap.freeze_state is RegistryFreezeState.MUTABLE
        registry.register(make_strategy(strategy_id="iron_condor", family=StrategyFamily.IRON_CONDOR))
        assert registry.snapshot().plugin_count == 2
        assert snap.plugin_count == 1


class TestValidation:
    def test_empty_registry_warning(self, registry: StrategyRegistry) -> None:
        result = registry.validate()
        assert result.is_valid
        assert any(item.code == ERROR_EMPTY_ENABLED_SET for item in result.warnings)

    def test_zero_enabled_warning(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(), enabled=False)
        result = registry.validate()
        assert result.is_valid
        assert any(item.code == ERROR_EMPTY_ENABLED_SET for item in result.warnings)

    def test_duplicate_fingerprint_warning(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy(strategy_id="alpha"))
        reg.register(make_strategy(strategy_id="beta", display_name="Beta"))
        alpha_fp = reg.get_record("alpha").metadata_fingerprint
        entry = reg._entries["beta"]  # type: ignore[attr-defined]
        entry.record = replace(entry.record, metadata_fingerprint=alpha_fp)
        result = reg.validate()
        assert result.is_valid
        assert any(item.code == ERROR_DUPLICATE_FINGERPRINT for item in result.warnings)

    def test_assert_valid_raises(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy())
        registry.assert_valid()

    def test_assert_valid_raises_on_error(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        strategy = make_strategy()
        reg.register(strategy)
        entry = reg._entries["short_strangle"]  # type: ignore[attr-defined]
        bad_record = replace(entry.record, priority=MAX_PRIORITY + 1)
        entry.record = bad_record
        with pytest.raises(StrategyRegistryValidationError) as exc_info:
            reg.assert_valid()
        assert exc_info.value.code == ERROR_VALIDATION_FAILED


class TestLimitsAndInvalidRegistration:
    def test_engine_name_mismatch_on_register(self, registry: StrategyRegistry) -> None:
        with pytest.raises(StrategyRegistryConfigurationError) as exc_info:
            registry.register(MismatchedEngineNameStrategy())
        assert exc_info.value.field == "engine_name"

    def test_max_plugins_limit(self) -> None:
        reg = StrategyRegistry(StrategyRegistryConfig(max_plugins=2))
        reg.register(make_strategy(strategy_id="one"))
        reg.register(make_strategy(strategy_id="two"))
        with pytest.raises(StrategyRegistryLimitError) as exc_info:
            reg.register(make_strategy(strategy_id="three"))
        assert exc_info.value.code == ERROR_LIMIT_EXCEEDED

    def test_invalid_priority_on_register(self, registry: StrategyRegistry) -> None:
        with pytest.raises(StrategyRegistryConfigurationError):
            registry.register(make_strategy(), priority=MAX_PRIORITY + 1)

    def test_not_base_strategy_raises(self, registry: StrategyRegistry) -> None:
        with pytest.raises(StrategyRegistryError) as exc_info:
            registry.register(object())  # type: ignore[arg-type]
        assert exc_info.value.code == ERROR_TYPE_INVALID

    def test_bad_config_strategy_raises(self, registry: StrategyRegistry) -> None:
        strategy = make_strategy()
        invalid_config = replace(strategy.plugin_config, priority=MAX_PRIORITY + 1)
        object.__setattr__(strategy, "_plugin_config", invalid_config)
        with pytest.raises(StrategyRegistryConfigurationError):
            registry.register(strategy)

    def test_replace_id_mismatch_raises(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy())
        with pytest.raises(StrategyRegistryConfigurationError):
            registry.replace("short_strangle", make_strategy(strategy_id="other"))


class TestBatchRegistration:
    def test_register_batch_success(self, registry: StrategyRegistry) -> None:
        descriptors = (
            StrategyDiscoveryDescriptor(make_strategy(strategy_id="alpha")),
            StrategyDiscoveryDescriptor(
                make_strategy(strategy_id="beta", priority=500),
                enabled=False,
                priority_override=720,
            ),
        )
        result = registry.register_batch(descriptors)
        assert result.registered_ids == ("alpha", "beta")
        assert not result.failed
        assert registry.get_record("beta").priority == 720
        assert registry.get_record("beta").enabled is False

    def test_register_batch_partial_failure(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(strategy_id="alpha"))
        result = registry.register_batch(
            (
                StrategyDiscoveryDescriptor(make_strategy(strategy_id="alpha")),
                StrategyDiscoveryDescriptor(make_strategy(strategy_id="beta")),
            )
        )
        assert result.registered_ids == ("beta",)
        assert len(result.failed) == 1
        assert result.failed[0].code == ERROR_DUPLICATE_ID

    def test_register_batch_strict_stops_early(self, registry: StrategyRegistry) -> None:
        reg = StrategyRegistry(
            StrategyRegistryConfig(strict_batch=True),
            clock=registry._clock,  # type: ignore[attr-defined]
        )
        reg.register(make_strategy(strategy_id="alpha"))
        result = reg.register_batch(
            (
                StrategyDiscoveryDescriptor(make_strategy(strategy_id="alpha")),
                StrategyDiscoveryDescriptor(make_strategy(strategy_id="beta")),
            )
        )
        assert result.registered_ids == ()
        assert len(result.failed) == 1

    def test_register_batch_ignore_skips(self) -> None:
        reg = StrategyRegistry(StrategyRegistryConfig(duplicate_policy=DuplicateRegistrationPolicy.IGNORE))
        reg.register(make_strategy(strategy_id="alpha"))
        result = reg.register_batch((StrategyDiscoveryDescriptor(make_strategy(strategy_id="alpha")),))
        assert result.skipped == ("alpha",)
        assert result.registered_ids == ()


class TestDuplicateDetection:
    def test_find_by_fingerprint(self, registry: StrategyRegistry) -> None:
        strategy = make_strategy()
        registry.register(strategy)
        fingerprint = strategy.metadata_fingerprint()
        assert registry.find_by_fingerprint(fingerprint) == ("short_strangle",)
        assert registry.find_by_fingerprint("nonexistent") == ()

    def test_detect_duplicates(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(strategy_id="alpha", display_name="Same Name"))
        registry.register(make_strategy(strategy_id="beta", display_name="same name"))
        report = registry.detect_duplicates()
        assert report.display_name_collisions
        assert ("alpha", "beta") in report.display_name_collisions


class TestFingerprintAndSnapshot:
    def test_registry_fingerprint_stable(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(strategy_id="alpha", priority=700))
        registry.register(make_strategy(strategy_id="beta", priority=600))
        snap1 = registry.snapshot()
        snap2 = registry.snapshot()
        assert snap1.registry_fingerprint == snap2.registry_fingerprint

    def test_snapshot_immutability_after_mutation(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(strategy_id="alpha"))
        before = registry.snapshot()
        registry.register(make_strategy(strategy_id="beta", family=StrategyFamily.IRON_CONDOR))
        assert before.plugin_count == 1
        assert registry.count() == 2

    def test_enabled_records_excludes_pending_removal(self, clock: FixedClock) -> None:
        active = threading.Event()
        active.set()
        reg = StrategyRegistry(
            StrategyRegistryConfig(defer_unregistration=True),
            clock=clock,
            engine_run_active=active.is_set,
        )
        reg.register(make_strategy())
        reg.unregister("short_strangle")
        snap = reg.snapshot()
        assert snap.plugin_count == 1
        assert snap.enabled_count == 0


class TestSerialization:
    def test_record_round_trip(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy())
        record = registry.get_record("short_strangle")
        restored = record_from_dict(record_to_dict(record))
        assert restored == record

    def test_snapshot_json_round_trip(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy())
        registry.register(make_strategy(strategy_id="iron_condor", family=StrategyFamily.IRON_CONDOR))
        snap = registry.freeze()
        payload = snapshot_to_json(snap)
        restored = snapshot_from_json(payload)
        assert restored.snapshot_id == snap.snapshot_id
        assert restored.registry_fingerprint == snap.registry_fingerprint
        assert len(restored.records) == 2

    def test_snapshot_unsupported_schema_version(self) -> None:
        data = snapshot_to_dict(
            StrategyRegistry(clock=FixedClock()).snapshot()
        )
        data["schema_version"] = "9.9.9"
        with pytest.raises(StrategyRegistryConfigurationError) as exc_info:
            snapshot_from_dict(data)
        assert exc_info.value.code == ERROR_SERIALIZATION_UNSUPPORTED_VERSION

    def test_snapshot_malformed_json(self) -> None:
        with pytest.raises(StrategyRegistryConfigurationError) as exc_info:
            snapshot_from_json("{not-json")
        assert exc_info.value.code == ERROR_SERIALIZATION_MALFORMED

    def test_snapshot_json_root_not_object(self) -> None:
        with pytest.raises(StrategyRegistryConfigurationError):
            snapshot_from_json("[]")

    def test_record_to_dict_omit_nulls(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy())
        record = registry.get_record("short_strangle")
        payload = record_to_dict(record, omit_nulls=True)
        assert "strategy_id" in payload


class TestRegistryFingerprintFunction:
    def test_registry_fingerprint_sorted_by_strategy_id(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(strategy_id="beta", priority=900))
        registry.register(make_strategy(strategy_id="alpha", priority=100))
        records = registry.get_all()
        fp = registry_fingerprint(records)
        reversed_fp = registry_fingerprint(tuple(reversed(records)))
        assert fp == reversed_fp


class TestThreadSafety:
    def test_concurrent_register_and_read(self) -> None:
        reg = StrategyRegistry()
        errors: list[Exception] = []

        def register_worker(index: int) -> None:
            try:
                strategy = make_strategy(
                    strategy_id=f"strategy_{index:03d}",
                    display_name=f"Strategy {index}",
                    family=StrategyFamily.SHORT_STRANGLE,
                )
                reg.register(strategy)
            except Exception as exc:  # pragma: no cover - collected for assertion
                errors.append(exc)

        def read_worker() -> None:
            try:
                reg.get_all()
                reg.enabled()
                reg.snapshot()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(register_worker, index) for index in range(100)]
            futures.extend(executor.submit(read_worker) for _ in range(16))
            for future in as_completed(futures):
                future.result()

        assert not errors
        assert reg.count() == 100

    def test_concurrent_get_exists(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy())
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(1000):
                    registry.exists("short_strangle")
                    registry.get("short_strangle")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(worker) for _ in range(16)]
            for future in as_completed(futures):
                future.result()

        assert not errors


class TestPerformanceSmoke:
    def test_register_and_snapshot_32_plugins_under_threshold(self) -> None:
        reg = StrategyRegistry()
        for index in range(32):
            reg.register(
                make_strategy(
                    strategy_id=f"plugin_{index:02d}",
                    display_name=f"Plugin {index}",
                    priority=500 + index,
                )
            )

        start = time.perf_counter()
        reg.get_all()
        listing_elapsed = time.perf_counter() - start
        assert listing_elapsed < 0.05

        start = time.perf_counter()
        reg.snapshot()
        snapshot_elapsed = time.perf_counter() - start
        assert snapshot_elapsed < 0.05

        start = time.perf_counter()
        reg.validate()
        validate_elapsed = time.perf_counter() - start
        assert validate_elapsed < 0.1


class TestClear:
    def test_clear_removes_all(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy())
        registry.clear()
        assert registry.count() == 0


class TestConstants:
    def test_priority_bounds(self) -> None:
        assert MIN_PRIORITY == 0
        assert MAX_PRIORITY == 1000


class TestEnums:
    def test_registration_state_values(self) -> None:
        assert RegistrationState.REGISTERED.value == "registered"
        assert RegistrationState.PENDING_REMOVAL.value == "pending_removal"

    def test_duplicate_policy_values(self) -> None:
        assert DuplicateRegistrationPolicy.REJECT.value == "reject"
        assert DuplicateRegistrationPolicy.REPLACE.value == "replace"
        assert DuplicateRegistrationPolicy.IGNORE.value == "ignore"


class TestValidationEdgeCases:
    def test_pending_removal_enabled_is_error(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy())
        entry = reg._entries["short_strangle"]  # type: ignore[attr-defined]
        entry.record = replace(
            entry.record,
            enabled=True,
            state=RegistrationState.PENDING_REMOVAL,
        )
        result = reg.validate()
        assert not result.is_valid
        assert any(item.code == ERROR_INVALID_STATE for item in result.errors)

    def test_get_record_not_found(self, registry: StrategyRegistry) -> None:
        with pytest.raises(StrategyRegistryNotFoundError):
            registry.get_record("missing")

    def test_config_property(self, registry: StrategyRegistry) -> None:
        assert registry.config.max_plugins == DEFAULT_MAX_PLUGINS

    def test_snapshot_to_dict_omit_nulls(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy())
        snap = registry.snapshot()
        payload = snapshot_to_dict(snap, omit_nulls=True)
        assert "schema_version" in payload

    def test_disable_pending_removal_raises(self, clock: FixedClock) -> None:
        active = threading.Event()
        active.set()
        reg = StrategyRegistry(
            StrategyRegistryConfig(defer_unregistration=True),
            clock=clock,
            engine_run_active=active.is_set,
        )
        reg.register(make_strategy())
        reg.unregister("short_strangle")
        with pytest.raises(StrategyRegistryInvalidStateError):
            reg.disable("short_strangle")

    def test_strategy_id_metadata_mismatch_error(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy())
        entry = reg._entries["short_strangle"]  # type: ignore[attr-defined]
        bad_meta = replace(entry.record.metadata, strategy_id="other")
        entry.record = replace(entry.record, metadata=bad_meta)
        result = reg.validate()
        assert not result.is_valid

    def test_register_batch_generic_registry_error(self, registry: StrategyRegistry) -> None:
        frozen = StrategyRegistry(clock=registry._clock)  # type: ignore[attr-defined]
        frozen.register(make_strategy())
        frozen.freeze()
        result = frozen.register_batch(
            (StrategyDiscoveryDescriptor(make_strategy(strategy_id="iron_condor", family=StrategyFamily.IRON_CONDOR)),)
        )
        assert len(result.failed) == 1
        assert result.failed[0].code == ERROR_FROZEN

    def test_register_batch_invalid_plugin_config_error(self, registry: StrategyRegistry) -> None:
        strategy = make_strategy(strategy_id="broken")
        object.__setattr__(strategy, "_plugin_config", replace(strategy.plugin_config, priority=9999))
        result = registry.register_batch((StrategyDiscoveryDescriptor(strategy),))
        assert result.failed[0].code == ERROR_CONFIG_INVALID

    def test_register_batch_strict_generic_error_stops(self) -> None:
        reg = StrategyRegistry(StrategyRegistryConfig(strict_batch=True))
        broken = make_strategy(strategy_id="broken")
        object.__setattr__(broken, "_plugin_config", replace(broken.plugin_config, priority=9999))
        result = reg.register_batch(
            (
                StrategyDiscoveryDescriptor(broken),
                StrategyDiscoveryDescriptor(make_strategy(strategy_id="second")),
            )
        )
        assert len(result.failed) == 1
        assert result.registered_ids == ()
        assert not reg.exists("second")

    def test_engine_name_mismatch_in_validate(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        strategy = make_strategy()
        reg.register(strategy)
        other = make_strategy(strategy_id="other_id")
        entry = reg._entries["short_strangle"]  # type: ignore[attr-defined]
        entry.strategy = other
        result = reg.validate()
        assert not result.is_valid

    def test_duplicate_family_enabled_warning(self, registry: StrategyRegistry) -> None:
        registry.register(make_strategy(strategy_id="alpha", family=StrategyFamily.IRON_CONDOR, priority=700))
        registry.register(make_strategy(strategy_id="beta", family=StrategyFamily.IRON_CONDOR, priority=600))
        result = registry.validate()
        assert result.is_valid
        assert any("DUPLICATE_FAMILY" in item.code for item in result.warnings)

    def test_naive_datetime_error(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy())
        entry = reg._entries["short_strangle"]  # type: ignore[attr-defined]
        naive = datetime(2026, 8, 3, 10, 0, 0)
        entry.record = replace(entry.record, registered_at=naive)
        result = reg.validate()
        assert not result.is_valid

    def test_registered_after_updated_error(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(clock=clock)
        reg.register(make_strategy())
        entry = reg._entries["short_strangle"]  # type: ignore[attr-defined]
        now = clock()
        entry.record = replace(entry.record, registered_at=now, updated_at=now.replace(year=2020))
        result = reg.validate()
        assert not result.is_valid

    def test_count_exceeds_max_plugins_error(self, clock: FixedClock) -> None:
        reg = StrategyRegistry(StrategyRegistryConfig(max_plugins=2), clock=clock)
        reg.register(make_strategy(strategy_id="alpha"))
        reg.register(make_strategy(strategy_id="beta"))
        reg._config = StrategyRegistryConfig(max_plugins=1)  # type: ignore[misc]
        result = reg.validate()
        assert not result.is_valid
        assert any(item.code == ERROR_LIMIT_EXCEEDED for item in result.errors)


class TestHelperFunctions:
    def test_build_registration_record_timezone_guard(self) -> None:
        from strategy.registry import _build_registration_record, _ensure_timezone_aware

        strategy = make_strategy()
        naive = datetime(2026, 8, 3, 10, 0, 0)
        with pytest.raises(StrategyRegistryConfigurationError):
            _ensure_timezone_aware(naive, "registered_at")
        with pytest.raises(StrategyRegistryConfigurationError):
            _build_registration_record(strategy, enabled=True, priority=500, now=naive)

    def test_metadata_from_dict_round_trip(self, registry: StrategyRegistry) -> None:
        metadata = valid_metadata(tags=MappingProxyType({"env": "test"}))
        config = StrategyPluginConfig(metadata=metadata, priority=650)
        strategy = make_strategy()
        object.__setattr__(strategy, "_plugin_config", config)
        registry.register(strategy)
        record = registry.get_record("short_strangle")
        payload = record_to_dict(record)
        restored = record_from_dict(payload)
        assert restored.metadata.strategy_id == "short_strangle"
        assert dict(restored.metadata.tags) == {"env": "test"}
