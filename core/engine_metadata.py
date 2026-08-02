"""Engine metadata and lifecycle diagnostics models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from core.enums import EngineExecutionStage


@dataclass(frozen=True)
class EngineMetadata:
    """Engine identity, timing, and traceability for a single run.

    Attributes:
        engine_name: Stable identifier of the concrete engine (e.g. ``market_regime``).
        engine_version: Semantic version of the concrete engine implementation.
        correlation_id: Pipeline- or request-level correlation identifier.
        execution_id: Unique identifier for this specific engine invocation.
        started_at: UTC timestamp when execution began.
        completed_at: UTC timestamp when execution finished.
        duration_ms: Wall-clock duration of the execution phase in milliseconds.
    """

    engine_name: str
    engine_version: str
    correlation_id: str
    execution_id: str
    started_at: datetime
    completed_at: datetime
    duration_ms: float


@dataclass(frozen=True)
class EngineDiagnostics:
    """Collected diagnostics for explainability and audit trails.

    Attributes:
        stages_completed: Ordered lifecycle stages reached during the run.
        validation_passed: Whether context validation succeeded.
        execution_id: Unique identifier matching :attr:`EngineMetadata.execution_id`.
        extra: Additional key-value diagnostic details (immutable mapping).
    """

    stages_completed: tuple[EngineExecutionStage, ...]
    validation_passed: bool
    execution_id: str
    extra: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )
