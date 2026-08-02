"""Immutable output model for a single engine run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.engine_metadata import EngineDiagnostics, EngineMetadata
from core.enums import EngineSeverity, EngineStatus


@dataclass(frozen=True)
class EngineErrorRecord:
    """Structured error attached to an :class:`EngineResult`.

    Attributes:
        code: Stable machine-readable identifier (e.g. ``ENGINE.BASE.CONTEXT.INVALID``).
        message: Human-readable explanation of the failure.
        severity: Severity classification for downstream handling.
        recoverable: Whether the caller may retry or continue with degraded input.
        field: Optional dotted path to the invalid field (e.g. ``payload.spot_price``).
    """

    code: str
    message: str
    severity: EngineSeverity = EngineSeverity.ERROR
    recoverable: bool = False
    field: Optional[str] = None


@dataclass(frozen=True)
class EngineWarningRecord:
    """Non-fatal issue surfaced to downstream consumers.

    Attributes:
        code: Stable machine-readable identifier.
        message: Human-readable explanation of the warning.
        field: Optional dotted path to the related field.
    """

    code: str
    message: str
    field: Optional[str] = None


@dataclass(frozen=True)
class EngineResult:
    """Immutable output from one engine run.

    Attributes:
        status: Overall outcome of the execution.
        metadata: Identity, timing, and correlation details.
        payload: Domain-specific output; ``None`` on hard rejection or failure.
        errors: Structured errors; empty on success.
        warnings: Non-fatal issues that downstream consumers may inspect.
        diagnostics: Optional lifecycle diagnostics for explainability.
    """

    status: EngineStatus
    metadata: EngineMetadata
    payload: object | None
    errors: tuple[EngineErrorRecord, ...] = ()
    warnings: tuple[EngineWarningRecord, ...] = ()
    diagnostics: EngineDiagnostics | None = None

    def __post_init__(self) -> None:
        """Enforce result invariants defined by the engine contract."""
        if self.status == EngineStatus.REJECTED:
            if self.payload is not None:
                raise ValueError(
                    "EngineResult with status REJECTED must have payload=None."
                )
            if not self.errors:
                raise ValueError(
                    "EngineResult with status REJECTED must contain errors."
                )
        if self.status == EngineStatus.SUCCESS and self.errors:
            raise ValueError(
                "EngineResult with status SUCCESS must not contain errors."
            )
        if self.status == EngineStatus.FAILED and not self.errors:
            raise ValueError(
                "EngineResult with status FAILED must contain errors."
            )
