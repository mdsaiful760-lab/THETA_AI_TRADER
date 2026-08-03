"""Base strategy plugin contract for THETA AI TRADER.

This module defines :class:`BaseStrategy`, the abstract contract every trading
strategy plugin must implement. Strategies consume immutable :class:`MarketSnapshot`
inputs via :class:`StrategyContext` and produce immutable :class:`TradingSignal`
outputs — never orders, never broker calls, never risk decisions.

Full signal aggregation, registry, and orchestration live in future modules;
only the plugin foundation and minimal signal contract types are defined here.
"""

from __future__ import annotations

import abc
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Final, Mapping

from core.base_engine import BaseEngine
from core.engine_context import EngineContext
from core.engine_result import EngineResult
from core.exceptions import EngineExecutionError
from market_data.market_snapshot import (
    MarketSnapshot,
    SnapshotValidationStatus,
    validate_market_snapshot,
)
from strategy.signals import (
    ConfidenceBand,
    SignalAction,
    SignalConfidence,
    SignalDirection,
    StrategyExecutionMode,
    StrategyFamily,
    TradingSignal,
    confidence_band_for_score,
    market_context_from_snapshot,
)

STRATEGY_VERSION: Final[str] = "1.0.0"

ERROR_CONFIG_INVALID: Final[str] = "STRATEGY_ENGINE.CONFIG.INVALID"
ERROR_CONTEXT_INVALID: Final[str] = "STRATEGY_ENGINE.CONTEXT.INVALID"
ERROR_CONTEXT_SNAPSHOT_MISSING: Final[str] = "STRATEGY_ENGINE.CONTEXT.SNAPSHOT_MISSING"
ERROR_CONTEXT_SNAPSHOT_INVALID: Final[str] = "STRATEGY_ENGINE.CONTEXT.SNAPSHOT_INVALID"
ERROR_SIGNAL_INVALID: Final[str] = "STRATEGY_ENGINE.SIGNAL.INVALID"
ERROR_SIGNAL_SEMANTIC_REJECT: Final[str] = "STRATEGY_ENGINE.SIGNAL.SEMANTIC_REJECT"
ERROR_CAPABILITY_VOLATILITY_REQUIRED: Final[str] = "STRATEGY_ENGINE.CAPABILITY.VOLATILITY_REQUIRED"
ERROR_CAPABILITY_UNDERLYING_UNSUPPORTED: Final[str] = "STRATEGY_ENGINE.CAPABILITY.UNDERLYING_UNSUPPORTED"
ERROR_CAPABILITY_CONTRACTS_INSUFFICIENT: Final[str] = "STRATEGY_ENGINE.CAPABILITY.CONTRACTS_INSUFFICIENT"

_MIN_PRIORITY: Final[int] = 0
_MAX_PRIORITY: Final[int] = 1000
_STRATEGY_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SEMVER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

_logger = logging.getLogger(__name__)


class StrategyEngineConfigurationError(Exception):
    """Raised when static strategy plugin configuration is invalid."""

    def __init__(self, message: str, *, code: str = ERROR_CONFIG_INVALID) -> None:
        super().__init__(message)
        self.code = code


class StrategyContextError(Exception):
    """Raised when a :class:`StrategyContext` fails validation."""

    def __init__(self, message: str, *, code: str = ERROR_CONTEXT_INVALID, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


class StrategySignalError(Exception):
    """Raised when a :class:`TradingSignal` fails validation."""

    def __init__(self, message: str, *, code: str = ERROR_SIGNAL_INVALID, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


class StrategyRiskProfileHint(str, Enum):
    """Informational risk profile hint — not risk enforcement."""

    DEFINED = "defined"
    UNDEFINED = "undefined"


@dataclass(frozen=True)
class StrategyMetadata:
    """Immutable descriptive metadata for a strategy plugin.

    Attributes:
        strategy_id: Stable identifier, e.g. ``"short_strangle"``.
        display_name: Human-readable label.
        version: Semantic version of the implementation.
        strategy_family: Canonical strategy family.
        category: Optional category label such as ``"income"``.
        supported_underlyings: Allowed underlying symbols; empty means all.
        requires_volatility_snapshot: Whether India VIX (or equivalent) is required.
        min_contracts_required: Minimum option contracts required in snapshot.
        risk_profile_hint: Informational defined/undefined risk hint.
        tags: Immutable extension labels.
    """

    strategy_id: str
    display_name: str
    version: str
    strategy_family: StrategyFamily
    category: str | None = None
    supported_underlyings: tuple[str, ...] = ()
    requires_volatility_snapshot: bool = False
    min_contracts_required: int = 1
    risk_profile_hint: StrategyRiskProfileHint | None = None
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class StrategyPluginConfig:
    """Immutable configuration injected at strategy plugin construction.

    Attributes:
        metadata: Validated strategy metadata snapshot.
        enabled: Whether the plugin is eligible for execution.
        priority: Registry priority hint in ``0..1000`` (higher is stronger).
        require_valid_snapshot: Reject INVALID snapshots during validation.
        allow_partial_snapshot: Allow PARTIAL snapshot quality when validating.
        enforce_capability_checks: Validate snapshot capabilities in context checks.
    """

    metadata: StrategyMetadata
    enabled: bool = True
    priority: int = 500
    require_valid_snapshot: bool = True
    allow_partial_snapshot: bool = False
    enforce_capability_checks: bool = True


@dataclass(frozen=True)
class StrategyContext:
    """Immutable input to one strategy plugin evaluation.

    Attributes:
        correlation_id: Pipeline correlation identifier.
        as_of: Timezone-aware decision timestamp.
        snapshot: Canonical market observation.
        execution_mode: LIVE, ANALYSIS, or BACKTEST execution mode.
        tags: Optional orchestrator hints (read-only).
        prior_signals: Optional prior-pass signals (reserved for multi-pass pipelines).
    """

    correlation_id: str
    as_of: datetime
    snapshot: MarketSnapshot
    execution_mode: StrategyExecutionMode = StrategyExecutionMode.LIVE
    tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    prior_signals: tuple[TradingSignal, ...] = ()


class BaseStrategy(BaseEngine):
    """Abstract base contract for a single hot-pluggable trading strategy plugin.

    Strategy plugins are stateless, deterministic, and thread-safe. They accept
    only :class:`StrategyContext` and return only :class:`TradingSignal` — never
    ``None``.

    Args:
        plugin_config: Immutable plugin configuration and metadata.
        re_raise_on_failure: When ``True``, unexpected failures propagate after logging.
    """

    def __init__(
        self,
        plugin_config: StrategyPluginConfig,
        *,
        re_raise_on_failure: bool = False,
    ) -> None:
        """Initialize the strategy plugin and validate static configuration.

        Args:
            plugin_config: Immutable plugin configuration.
            re_raise_on_failure: Whether to re-raise unexpected execution failures.

        Raises:
            StrategyEngineConfigurationError: If plugin configuration is invalid.
        """
        self._plugin_config = plugin_config
        super().__init__(
            config=MappingProxyType(
                {
                    "strategy_id": plugin_config.metadata.strategy_id,
                    "strategy_family": plugin_config.metadata.strategy_family.value,
                    "priority": plugin_config.priority,
                }
            ),
            re_raise_on_failure=re_raise_on_failure,
        )

    @property
    def plugin_config(self) -> StrategyPluginConfig:
        """Return the immutable plugin configuration."""
        return self._plugin_config

    @property
    def metadata(self) -> StrategyMetadata:
        """Return immutable strategy metadata."""
        return self._plugin_config.metadata

    @property
    def engine_name(self) -> str:
        """Return stable strategy identifier used in logs and results."""
        return self._plugin_config.metadata.strategy_id

    @property
    def engine_version(self) -> str:
        """Return semantic version of the strategy implementation."""
        return self._plugin_config.metadata.version

    @property
    def strategy_version(self) -> str:
        """Return semantic version alias for strategy plugins."""
        return self.engine_version

    def metadata_snapshot(self) -> StrategyMetadata:
        """Return a deterministic metadata snapshot for registry reproducibility.

        Returns:
            Immutable metadata instance with stable tag ordering semantics.
        """
        meta = self._plugin_config.metadata
        if not meta.tags:
            return meta
        ordered_tags = MappingProxyType(dict(sorted(meta.tags.items())))
        if ordered_tags == meta.tags:
            return meta
        return replace(meta, tags=ordered_tags)

    def metadata_fingerprint(self) -> str:
        """Return a deterministic SHA-256 fingerprint of plugin metadata.

        Returns:
            Hex digest suitable for registry snapshot identifiers.
        """
        meta = self.metadata_snapshot()
        payload = {
            "strategy_id": meta.strategy_id,
            "display_name": meta.display_name,
            "version": meta.version,
            "strategy_family": meta.strategy_family.value,
            "category": meta.category,
            "supported_underlyings": list(meta.supported_underlyings),
            "requires_volatility_snapshot": meta.requires_volatility_snapshot,
            "min_contracts_required": meta.min_contracts_required,
            "risk_profile_hint": meta.risk_profile_hint.value if meta.risk_profile_hint else None,
            "tags": dict(sorted(meta.tags.items())),
            "priority": self._plugin_config.priority,
            "enabled": self._plugin_config.enabled,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def validate_configuration(self) -> None:
        """Validate static plugin configuration supplied at construction.

        Raises:
            StrategyEngineConfigurationError: If configuration or metadata is invalid.
        """
        try:
            validate_strategy_plugin_config(self._plugin_config)
            validate_strategy_metadata(self._plugin_config.metadata)
        except StrategyEngineConfigurationError:
            _logger.error(
                "strategy.init.invalid_config",
                extra={"strategy_id": getattr(self._plugin_config.metadata, "strategy_id", None)},
            )
            raise

    def validate_context(self, context: StrategyContext) -> None:
        """Validate strategy context before execution.

        Args:
            context: Immutable strategy input.

        Raises:
            StrategyContextError: If the context fails validation.
        """
        if not isinstance(context, StrategyContext):
            raise StrategyContextError(
                "Context must be an instance of StrategyContext.",
                code=ERROR_CONTEXT_INVALID,
                field="context",
            )

        correlation_id = context.correlation_id.strip()
        if not correlation_id:
            raise StrategyContextError(
                "correlation_id must be a non-empty string.",
                code=ERROR_CONTEXT_INVALID,
                field="correlation_id",
            )

        if context.as_of.tzinfo is None or context.as_of.tzinfo.utcoffset(context.as_of) is None:
            raise StrategyContextError(
                "as_of must be a timezone-aware datetime.",
                code=ERROR_CONTEXT_INVALID,
                field="as_of",
            )

        if context.snapshot is None:
            raise StrategyContextError(
                "snapshot must not be None.",
                code=ERROR_CONTEXT_SNAPSHOT_MISSING,
                field="snapshot",
            )

        if not isinstance(context.snapshot, MarketSnapshot):
            raise StrategyContextError(
                "snapshot must be a MarketSnapshot instance.",
                code=ERROR_CONTEXT_SNAPSHOT_INVALID,
                field="snapshot",
            )

        self._validate_snapshot_for_context(context)
        self._validate_capability_requirements(context)

        if context.prior_signals:
            for index, prior in enumerate(context.prior_signals):
                if not isinstance(prior, TradingSignal):
                    raise StrategyContextError(
                        f"prior_signals[{index}] must be a TradingSignal.",
                        code=ERROR_CONTEXT_INVALID,
                        field=f"prior_signals[{index}]",
                    )

    def run(self, context: StrategyContext) -> TradingSignal:
        """Execute the strategy plugin and return a validated trading signal.

        Args:
            context: Immutable strategy input.

        Returns:
            Validated immutable trading signal. Never ``None``.

        Raises:
            StrategyContextError: If context validation fails.
            StrategySignalError: If the produced signal fails validation.
            EngineExecutionError: If execution fails unexpectedly.
        """
        _logger.debug(
            "strategy.run.start",
            extra={
                "strategy_id": self.engine_name,
                "correlation_id": context.correlation_id,
            },
        )
        try:
            self.validate_context(context)
            signal = self._execute(context)
            if signal is None:
                raise StrategySignalError(
                    "Strategy _execute must never return None.",
                    code=ERROR_SIGNAL_INVALID,
                )
            validated = self.validate_trading_signal(signal, context)
        except (StrategyContextError, StrategySignalError):
            raise
        except Exception as exc:
            wrapped = EngineExecutionError(
                f"Unhandled exception in strategy '{self.engine_name}': {exc}",
                engine_name=self.engine_name,
                cause=exc,
            )
            _logger.error(
                "strategy.run.failed",
                extra={
                    "strategy_id": self.engine_name,
                    "correlation_id": context.correlation_id,
                },
            )
            if self._re_raise_on_failure:
                raise wrapped from exc
            raise wrapped from exc

        _logger.info(
            "strategy.run.success",
            extra={
                "strategy_id": self.engine_name,
                "correlation_id": context.correlation_id,
                "signal_action": validated.action.value,
            },
        )
        return validated

    @abc.abstractmethod
    def _execute(self, context: StrategyContext) -> TradingSignal:
        """Evaluate the strategy and produce a trading signal.

        Args:
            context: Validated immutable strategy input.

        Returns:
            Non-null trading signal. Use :meth:`build_abstain_signal` when abstaining.
        """

    def evaluate(self, context: EngineContext) -> EngineResult:
        """Disallow generic engine entry point for strategy plugins.

        Strategy plugins must be invoked through :meth:`run` with
        :class:`StrategyContext`.

        Args:
            context: Generic engine context (unsupported).

        Raises:
            EngineExecutionError: Always raised for strategy plugins.
        """
        raise EngineExecutionError(
            "Strategy plugins must be invoked via run(StrategyContext), not evaluate(EngineContext).",
            engine_name=self.engine_name,
        )

    def validate_trading_signal(
        self,
        signal: TradingSignal,
        context: StrategyContext,
    ) -> TradingSignal:
        """Validate a trading signal against schema and context semantics.

        Args:
            signal: Candidate signal returned by :meth:`_execute`.
            context: Input context used for semantic checks.

        Returns:
            The validated signal unchanged.

        Raises:
            StrategySignalError: If validation fails.
        """
        if not isinstance(signal, TradingSignal):
            raise StrategySignalError(
                "Signal must be a TradingSignal instance.",
                code=ERROR_SIGNAL_INVALID,
                field="signal",
            )

        if not signal.signal_id.strip():
            raise StrategySignalError(
                "signal_id must be non-empty.",
                code=ERROR_SIGNAL_INVALID,
                field="signal_id",
            )

        if signal.strategy_id != self.metadata.strategy_id:
            raise StrategySignalError(
                "signal.strategy_id must match plugin metadata.strategy_id.",
                code=ERROR_SIGNAL_SEMANTIC_REJECT,
                field="strategy_id",
            )

        if signal.strategy_version != self.metadata.version:
            raise StrategySignalError(
                "signal.strategy_version must match plugin metadata.version.",
                code=ERROR_SIGNAL_SEMANTIC_REJECT,
                field="strategy_version",
            )

        if signal.strategy_family != self.metadata.strategy_family:
            raise StrategySignalError(
                "signal.strategy_family must match plugin metadata.strategy_family.",
                code=ERROR_SIGNAL_SEMANTIC_REJECT,
                field="strategy_family",
            )

        if not signal.reasons:
            raise StrategySignalError(
                "reasons must contain at least one entry.",
                code=ERROR_SIGNAL_INVALID,
                field="reasons",
            )

        if not (0.0 <= signal.confidence.score <= 100.0):
            raise StrategySignalError(
                "confidence.score must be within [0.0, 100.0].",
                code=ERROR_SIGNAL_INVALID,
                field="confidence.score",
            )

        if signal.action is SignalAction.EVALUATE and signal.strategy_family is StrategyFamily.NO_STRATEGY:
            raise StrategySignalError(
                "action=EVALUATE is incompatible with strategy_family=NO_STRATEGY.",
                code=ERROR_SIGNAL_SEMANTIC_REJECT,
                field="action",
            )

        if signal.snapshot_id != context.snapshot.provenance.snapshot_id:
            raise StrategySignalError(
                "signal.snapshot_id must match input snapshot provenance.snapshot_id.",
                code=ERROR_SIGNAL_SEMANTIC_REJECT,
                field="snapshot_id",
            )

        if signal.as_of.tzinfo is None or signal.as_of.tzinfo.utcoffset(signal.as_of) is None:
            raise StrategySignalError(
                "signal.as_of must be timezone-aware.",
                code=ERROR_SIGNAL_INVALID,
                field="as_of",
            )

        if signal.valid_until is not None:
            if signal.valid_until.tzinfo is None or signal.valid_until.tzinfo.utcoffset(signal.valid_until) is None:
                raise StrategySignalError(
                    "signal.valid_until must be timezone-aware when provided.",
                    code=ERROR_SIGNAL_INVALID,
                    field="valid_until",
                )
            if signal.valid_until < signal.as_of:
                raise StrategySignalError(
                    "signal.valid_until must not precede signal.as_of.",
                    code=ERROR_SIGNAL_INVALID,
                    field="valid_until",
                )

        underlying_symbol = _snapshot_underlying_symbol(context.snapshot)
        if signal.underlying.strip().upper() != underlying_symbol:
            raise StrategySignalError(
                "signal.underlying must match snapshot underlying symbol.",
                code=ERROR_SIGNAL_SEMANTIC_REJECT,
                field="underlying",
            )

        expected_band = confidence_band_for_score(signal.confidence.score)
        if signal.confidence.band is not expected_band:
            raise StrategySignalError(
                "signal.confidence.band must match score-derived band.",
                code=ERROR_SIGNAL_INVALID,
                field="confidence.band",
            )

        return signal

    def build_abstain_signal(
        self,
        context: StrategyContext,
        *,
        action: SignalAction = SignalAction.ABSTAIN,
        direction: SignalDirection = SignalDirection.UNKNOWN,
        reasons: tuple[str, ...] | None = None,
        score: float = 0.0,
    ) -> TradingSignal:
        """Build a deterministic abstain signal for the current context.

        Args:
            context: Strategy input context.
            action: Abstain action; defaults to ``ABSTAIN``.
            direction: Direction label; defaults to ``UNKNOWN``.
            reasons: Explainability reasons; defaults to a generic abstain reason.
            score: Confidence score; defaults to ``0.0``.

        Returns:
            Immutable abstain trading signal.
        """
        abstain_reasons = reasons or ("strategy abstained due to insufficient setup",)
        return TradingSignal(
            signal_id=_deterministic_signal_id(context, self.metadata.strategy_id, action),
            strategy_id=self.metadata.strategy_id,
            strategy_version=self.metadata.version,
            strategy_family=self.metadata.strategy_family,
            action=action,
            direction=direction,
            confidence=SignalConfidence(
                score=score,
                band=confidence_band_for_score(score),
                method="abstain",
            ),
            market=market_context_from_snapshot(context.snapshot),
            as_of=context.as_of,
            reasons=abstain_reasons,
        )

    def _validate_snapshot_for_context(self, context: StrategyContext) -> None:
        """Validate snapshot quality and provenance for the strategy context."""
        snapshot = context.snapshot
        provenance = snapshot.provenance

        if provenance.as_of.tzinfo is None or provenance.as_of.tzinfo.utcoffset(provenance.as_of) is None:
            raise StrategyContextError(
                "snapshot.provenance.as_of must be timezone-aware.",
                code=ERROR_CONTEXT_SNAPSHOT_INVALID,
                field="snapshot.provenance.as_of",
            )

        validation = validate_market_snapshot(snapshot)
        if validation.validation_status is SnapshotValidationStatus.INVALID:
            raise StrategyContextError(
                "snapshot failed market snapshot validation.",
                code=ERROR_CONTEXT_SNAPSHOT_INVALID,
                field="snapshot",
            )

        config = self._plugin_config
        if config.require_valid_snapshot and snapshot.quality.validation_status is SnapshotValidationStatus.INVALID:
            raise StrategyContextError(
                "snapshot quality validation_status is INVALID.",
                code=ERROR_CONTEXT_SNAPSHOT_INVALID,
                field="snapshot.quality.validation_status",
            )

        if (
            not config.allow_partial_snapshot
            and snapshot.quality.validation_status is SnapshotValidationStatus.PARTIAL
            and context.execution_mode is StrategyExecutionMode.LIVE
        ):
            raise StrategyContextError(
                "PARTIAL snapshot quality is not allowed for LIVE execution.",
                code=ERROR_CONTEXT_SNAPSHOT_INVALID,
                field="snapshot.quality.validation_status",
            )

        chain_underlying = snapshot.option_chain.metadata.underlying.strip().upper()
        spot_symbol = snapshot.underlying.symbol.strip().upper()
        if chain_underlying != spot_symbol:
            raise StrategyContextError(
                "underlying symbol mismatch between spot and option chain metadata.",
                code=ERROR_CONTEXT_SNAPSHOT_INVALID,
                field="snapshot.underlying",
            )

    def _validate_capability_requirements(self, context: StrategyContext) -> None:
        """Validate plugin capability requirements against the snapshot."""
        if not self._plugin_config.enforce_capability_checks:
            return

        metadata = self._plugin_config.metadata
        snapshot = context.snapshot
        underlying = _snapshot_underlying_symbol(snapshot)

        if metadata.supported_underlyings:
            allowed = {item.strip().upper() for item in metadata.supported_underlyings}
            if underlying not in allowed:
                raise StrategyContextError(
                    f"underlying '{underlying}' is not supported by strategy metadata.",
                    code=ERROR_CAPABILITY_UNDERLYING_UNSUPPORTED,
                    field="snapshot.underlying.symbol",
                )

        contract_count = len(snapshot.option_chain.contracts)
        if contract_count < metadata.min_contracts_required:
            raise StrategyContextError(
                "snapshot option chain contract count below strategy minimum.",
                code=ERROR_CAPABILITY_CONTRACTS_INSUFFICIENT,
                field="snapshot.option_chain.contracts",
            )

        if metadata.requires_volatility_snapshot and snapshot.volatility is None:
            raise StrategyContextError(
                "strategy requires volatility snapshot but snapshot.volatility is None.",
                code=ERROR_CAPABILITY_VOLATILITY_REQUIRED,
                field="snapshot.volatility",
            )


def validate_strategy_metadata(metadata: StrategyMetadata) -> None:
    """Validate immutable strategy metadata.

    Args:
        metadata: Candidate strategy metadata.

    Raises:
        StrategyEngineConfigurationError: If metadata is invalid.
    """
    strategy_id = metadata.strategy_id.strip()
    if not _STRATEGY_ID_PATTERN.match(strategy_id):
        raise StrategyEngineConfigurationError(
            "strategy_id must match ^[a-z][a-z0-9_]{1,63}$.",
            code=ERROR_CONFIG_INVALID,
        )

    display_name = metadata.display_name.strip()
    if not display_name:
        raise StrategyEngineConfigurationError(
            "display_name must be non-empty.",
            code=ERROR_CONFIG_INVALID,
        )

    version = metadata.version.strip()
    if not _SEMVER_PATTERN.match(version):
        raise StrategyEngineConfigurationError(
            "version must be a valid semantic version string.",
            code=ERROR_CONFIG_INVALID,
        )

    if metadata.min_contracts_required < 0:
        raise StrategyEngineConfigurationError(
            "min_contracts_required must be >= 0.",
            code=ERROR_CONFIG_INVALID,
        )

    if metadata.strategy_family is StrategyFamily.CUSTOM:
        custom_name = metadata.tags.get("custom_family_name", "").strip()
        if not custom_name:
            raise StrategyEngineConfigurationError(
                "strategy_family=CUSTOM requires tags['custom_family_name'].",
                code=ERROR_CONFIG_INVALID,
            )

    for underlying in metadata.supported_underlyings:
        if not underlying.strip():
            raise StrategyEngineConfigurationError(
                "supported_underlyings entries must be non-empty.",
                code=ERROR_CONFIG_INVALID,
            )


def validate_strategy_plugin_config(config: StrategyPluginConfig) -> None:
    """Validate immutable strategy plugin configuration.

    Args:
        config: Candidate plugin configuration.

    Raises:
        StrategyEngineConfigurationError: If configuration is invalid.
    """
    if not isinstance(config.metadata, StrategyMetadata):
        raise StrategyEngineConfigurationError(
            "metadata must be a StrategyMetadata instance.",
            code=ERROR_CONFIG_INVALID,
        )

    if not (_MIN_PRIORITY <= config.priority <= _MAX_PRIORITY):
        raise StrategyEngineConfigurationError(
            f"priority must be within [{_MIN_PRIORITY}, {_MAX_PRIORITY}].",
            code=ERROR_CONFIG_INVALID,
        )


def _snapshot_underlying_symbol(snapshot: MarketSnapshot) -> str:
    """Return normalized underlying symbol from a snapshot."""
    return snapshot.option_chain.metadata.underlying.strip().upper()


def _deterministic_signal_id(
    context: StrategyContext,
    strategy_id: str,
    action: SignalAction,
) -> str:
    """Build a deterministic signal identifier for abstain/default paths."""
    material = "|".join(
        (
            context.correlation_id,
            strategy_id,
            context.snapshot.provenance.snapshot_id,
            context.as_of.isoformat(),
            action.value,
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest[:32]))


__all__ = [
    "STRATEGY_VERSION",
    "ERROR_CAPABILITY_CONTRACTS_INSUFFICIENT",
    "ERROR_CAPABILITY_UNDERLYING_UNSUPPORTED",
    "ERROR_CAPABILITY_VOLATILITY_REQUIRED",
    "ERROR_CONFIG_INVALID",
    "ERROR_CONTEXT_INVALID",
    "ERROR_CONTEXT_SNAPSHOT_INVALID",
    "ERROR_CONTEXT_SNAPSHOT_MISSING",
    "ERROR_SIGNAL_INVALID",
    "ERROR_SIGNAL_SEMANTIC_REJECT",
    "BaseStrategy",
    "ConfidenceBand",
    "SignalAction",
    "SignalConfidence",
    "SignalDirection",
    "StrategyContext",
    "StrategyContextError",
    "StrategyEngineConfigurationError",
    "StrategyExecutionMode",
    "StrategyFamily",
    "StrategyMetadata",
    "StrategyPluginConfig",
    "StrategyRiskProfileHint",
    "StrategySignalError",
    "TradingSignal",
    "confidence_band_for_score",
    "validate_strategy_metadata",
    "validate_strategy_plugin_config",
]
