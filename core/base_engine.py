"""Foundational contract for every analytical engine in THETA AI TRADER.

This module defines immutable input/output models, structured error types, and
the abstract :class:`BaseEngine` lifecycle that all intelligence engines must
follow. Concrete engines implement domain logic in :meth:`BaseEngine.evaluate`;
orchestrators invoke engines exclusively through :meth:`BaseEngine.run`.
"""

from __future__ import annotations

import abc
import logging
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Mapping

from core.constants import (
    DEFAULT_ENGINE_VERSION,
    ERROR_CODE_CONTEXT_INVALID,
    ERROR_CODE_CONTEXT_MISSING_FIELD,
    ERROR_CODE_EXECUTION_UNHANDLED,
    ERROR_CODE_RESULT_INVALID,
)
from core.engine_context import EngineContext
from core.engine_metadata import EngineDiagnostics, EngineMetadata
from core.engine_result import EngineErrorRecord, EngineResult, EngineWarningRecord
from core.enums import EngineExecutionStage, EngineSeverity, EngineStatus
from core.exceptions import (
    EngineConfigurationError,
    EngineError,
    EngineExecutionError,
    EngineValidationError,
)
from core.validation import (
    _execution_error_to_record,
    _validate_required_fields,
    _validation_error_to_record,
)


def _utc_now() -> datetime:
    """Return the current UTC timestamp with timezone information.

    Returns:
        Timezone-aware UTC datetime.
    """
    return datetime.now(timezone.utc)


def _build_result_metadata(
    *,
    engine_name: str,
    engine_version: str,
    correlation_id: str,
    execution_id: str,
    started_at: datetime,
    completed_at: datetime,
    duration_ms: float,
) -> EngineMetadata:
    """Construct :class:`EngineMetadata` from engine instance and timing.

    Args:
        engine_name: Stable identifier of the concrete engine.
        engine_version: Semantic version of the engine implementation.
        correlation_id: Pipeline- or request-level correlation identifier.
        execution_id: Unique identifier for this engine invocation.
        started_at: UTC timestamp when execution began.
        completed_at: UTC timestamp when execution finished.
        duration_ms: Execution duration in milliseconds.

    Returns:
        Populated immutable metadata for an :class:`EngineResult`.
    """
    return EngineMetadata(
        engine_name=engine_name,
        engine_version=engine_version,
        correlation_id=correlation_id,
        execution_id=execution_id,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
    )


class BaseEngine(abc.ABC):
    """Abstract base contract for all THETA AI TRADER intelligence engines.

    Subclasses implement domain logic in :meth:`evaluate`. The public
    :meth:`run` method orchestrates the full execution lifecycle so concrete
    engines never reimplement validation, timing, logging, or error boundaries.

    Args:
        config: Optional immutable configuration mapping injected at construction.
        re_raise_on_failure: When ``True``, unhandled execution failures are
            re-raised after being logged (intended for debug and test modes).
    """

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        *,
        re_raise_on_failure: bool = False,
    ) -> None:
        """Initialize the engine and validate static configuration.

        Args:
            config: Optional configuration mapping resolved by the caller.
            re_raise_on_failure: Whether to re-raise unexpected execution errors.

        Raises:
            EngineConfigurationError: If static configuration is invalid.
        """
        self._config: Mapping[str, object] = (
            MappingProxyType(dict(config)) if config is not None else MappingProxyType({})
        )
        self._re_raise_on_failure = re_raise_on_failure
        try:
            self.validate_configuration()
        except EngineConfigurationError:
            self._log_event(
                "engine.init.invalid_config",
                logging.ERROR,
                engine_name=self.engine_name,
            )
            raise

    @property
    @abc.abstractmethod
    def engine_name(self) -> str:
        """Stable string identifier used in logs, metrics, and results.

        Returns:
            Engine identifier (e.g. ``"market_regime"``).
        """

    @property
    def engine_version(self) -> str:
        """Semantic version string for the concrete engine implementation.

        Returns:
            Engine version; defaults to ``"1.0.0"`` when not overridden.
        """
        return DEFAULT_ENGINE_VERSION

    @property
    def config(self) -> Mapping[str, object]:
        """Immutable configuration supplied at construction.

        Returns:
            Read-only mapping of engine configuration values.
        """
        return self._config

    @property
    def logger(self) -> logging.Logger:
        """Logger scoped to the concrete engine module.

        Returns:
            Standard library logger for the implementing module.
        """
        return logging.getLogger(type(self).__module__)

    def validate_configuration(self) -> None:
        """Validate static configuration supplied at construction.

        The default implementation accepts any configuration. Subclasses should
        override to enforce domain-specific constraints and raise
        :class:`EngineConfigurationError` on invalid input.

        Raises:
            EngineConfigurationError: If configuration is invalid.
        """

    def validate_context(self, context: EngineContext) -> None:
        """Validate :class:`EngineContext` before execution.

        The default implementation enforces base contract invariants. Subclasses
        should call ``super().validate_context(context)`` before adding domain rules.

        Args:
            context: Immutable input for the pending engine run.

        Raises:
            EngineValidationError: If the context fails base validation.
        """
        if not isinstance(context, EngineContext):
            raise EngineValidationError(
                "Context must be an instance of EngineContext.",
                code=ERROR_CODE_CONTEXT_INVALID,
                field="context",
                engine_name=self.engine_name,
            )

        _validate_required_fields(
            {"correlation_id": context.correlation_id},
            ("correlation_id",),
        )

        if context.as_of.tzinfo is None or context.as_of.tzinfo.utcoffset(context.as_of) is None:
            raise EngineValidationError(
                "Context 'as_of' must be a timezone-aware datetime.",
                code=ERROR_CODE_CONTEXT_INVALID,
                field="as_of",
                engine_name=self.engine_name,
            )

        if context.payload is None:
            raise EngineValidationError(
                "Context 'payload' must not be None.",
                code=ERROR_CODE_CONTEXT_MISSING_FIELD,
                field="payload",
                engine_name=self.engine_name,
            )

    def before_execute(self, context: EngineContext) -> None:
        """Hook invoked after validation and before timed execution.

        Args:
            context: Validated input for the pending engine run.
        """

    @abc.abstractmethod
    def evaluate(self, context: EngineContext) -> EngineResult:
        """Execute core engine logic for a validated context.

        Subclasses must implement this method. It must not perform I/O or call
        other engines directly.

        Args:
            context: Validated immutable input for this run.

        Returns:
            Engine result produced by the concrete engine. Metadata and timing
            fields may be partial; :meth:`run` enriches them before return.
        """

    def _execute(self, context: EngineContext) -> EngineResult:
        """Spec-compatible alias for :meth:`evaluate`.

        Args:
            context: Validated immutable input for this run.

        Returns:
            Result from :meth:`evaluate`.
        """
        return self.evaluate(context)

    def validate_result(self, result: EngineResult) -> None:
        """Validate an :class:`EngineResult` before returning to callers.

        The default implementation enforces base result invariants. Subclasses
        may override to add domain-specific output checks.

        Args:
            result: Candidate result produced by :meth:`evaluate`.

        Raises:
            EngineValidationError: If the result violates base invariants.
        """
        if result.status == EngineStatus.REJECTED:
            if result.payload is not None:
                raise EngineValidationError(
                    "Rejected results must not contain a payload.",
                    code=ERROR_CODE_RESULT_INVALID,
                    field="payload",
                    engine_name=self.engine_name,
                    stage=EngineExecutionStage.RESULT_VALIDATION,
                )
            if not result.errors:
                raise EngineValidationError(
                    "Rejected results must contain at least one error.",
                    code=ERROR_CODE_RESULT_INVALID,
                    field="errors",
                    engine_name=self.engine_name,
                    stage=EngineExecutionStage.RESULT_VALIDATION,
                )

        if result.status == EngineStatus.SUCCESS and result.errors:
            raise EngineValidationError(
                "Successful results must not contain errors.",
                code=ERROR_CODE_RESULT_INVALID,
                field="errors",
                engine_name=self.engine_name,
                stage=EngineExecutionStage.RESULT_VALIDATION,
            )

        if result.status == EngineStatus.FAILED and not result.errors:
            raise EngineValidationError(
                "Failed results must contain at least one error.",
                code=ERROR_CODE_RESULT_INVALID,
                field="errors",
                engine_name=self.engine_name,
                stage=EngineExecutionStage.RESULT_VALIDATION,
            )

    def after_execute(self, context: EngineContext, result: EngineResult) -> None:
        """Hook invoked after result validation and before returning.

        Args:
            context: Input context for the completed run.
            result: Final validated result about to be returned.
        """

    def run(self, context: EngineContext) -> EngineResult:
        """Primary entry point orchestrating the full engine lifecycle.

        Pipeline::

            validate_context()
            → before_execute()
            → start timer
            → evaluate()
            → stop timer
            → validate_result()
            → after_execute()
            → return EngineResult

        Args:
            context: Immutable input for this engine run.

        Returns:
            Immutable :class:`EngineResult` with status, metadata, and diagnostics.
        """
        execution_id = str(uuid.uuid4())
        started_at = _utc_now()
        stages_completed: list[EngineExecutionStage] = []
        validation_passed = False

        self._log_event(
            "engine.run.start",
            logging.DEBUG,
            engine_name=self.engine_name,
            correlation_id=context.correlation_id if isinstance(context, EngineContext) else None,
            execution_id=execution_id,
        )

        try:
            self.validate_context(context)
            validation_passed = True
            stages_completed.append(EngineExecutionStage.VALIDATION)
        except EngineValidationError as exc:
            exc.engine_name = exc.engine_name or self.engine_name
            self._log_event(
                "engine.run.validation_failed",
                logging.INFO,
                engine_name=self.engine_name,
                correlation_id=getattr(context, "correlation_id", None),
                execution_id=execution_id,
                error_code=exc.code,
            )
            return self._build_rejected_result(
                context=context,
                errors=(_validation_error_to_record(exc),),
                execution_id=execution_id,
                started_at=started_at,
                stages_completed=tuple(stages_completed),
                validation_passed=validation_passed,
            )

        self.before_execute(context)
        stages_completed.append(EngineExecutionStage.BEFORE_EXECUTE)

        timer_start = time.perf_counter()
        try:
            result = self.evaluate(context)
            stages_completed.append(EngineExecutionStage.EXECUTE)
        except EngineValidationError as exc:
            exc.engine_name = exc.engine_name or self.engine_name
            exc.stage = exc.stage or EngineExecutionStage.EXECUTE
            self._log_event(
                "engine.run.validation_failed",
                logging.INFO,
                engine_name=self.engine_name,
                correlation_id=context.correlation_id,
                execution_id=execution_id,
                error_code=exc.code,
            )
            return self._build_rejected_result(
                context=context,
                errors=(_validation_error_to_record(exc),),
                execution_id=execution_id,
                started_at=started_at,
                stages_completed=tuple(stages_completed),
                validation_passed=validation_passed,
            )
        except EngineExecutionError as exc:
            exc.engine_name = exc.engine_name or self.engine_name
            duration_ms = (time.perf_counter() - timer_start) * 1000.0
            completed_at = _utc_now()
            result = self._build_failed_result(
                context=context,
                errors=(_execution_error_to_record(exc),),
                execution_id=execution_id,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                stages_completed=tuple(stages_completed),
                validation_passed=validation_passed,
            )
            self._log_run_outcome(result)
            if self._re_raise_on_failure:
                raise
            return result
        except Exception as exc:
            wrapped = EngineExecutionError(
                f"Unhandled exception in engine '{self.engine_name}': {exc}",
                engine_name=self.engine_name,
                stage=EngineExecutionStage.EXECUTE,
                cause=exc,
            )
            duration_ms = (time.perf_counter() - timer_start) * 1000.0
            completed_at = _utc_now()
            result = self._build_failed_result(
                context=context,
                errors=(_execution_error_to_record(wrapped),),
                execution_id=execution_id,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                stages_completed=tuple(stages_completed),
                validation_passed=validation_passed,
            )
            self._log_run_outcome(result, exception=wrapped)
            if self._re_raise_on_failure:
                raise wrapped from exc
            return result

        duration_ms = (time.perf_counter() - timer_start) * 1000.0
        completed_at = _utc_now()
        result = self._finalize_result(
            result=result,
            context=context,
            execution_id=execution_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )

        try:
            self.validate_result(result)
            stages_completed.append(EngineExecutionStage.RESULT_VALIDATION)
        except EngineValidationError as exc:
            exc.engine_name = exc.engine_name or self.engine_name
            failed = self._build_failed_result(
                context=context,
                errors=(_validation_error_to_record(exc),),
                execution_id=execution_id,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                stages_completed=tuple(stages_completed),
                validation_passed=validation_passed,
            )
            self._log_run_outcome(failed)
            if self._re_raise_on_failure:
                raise
            return failed

        diagnostics = EngineDiagnostics(
            stages_completed=tuple(stages_completed),
            validation_passed=validation_passed,
            execution_id=execution_id,
        )
        result = replace(result, diagnostics=diagnostics)

        self.after_execute(context, result)
        stages_completed.append(EngineExecutionStage.AFTER_EXECUTE)
        result = replace(
            result,
            diagnostics=EngineDiagnostics(
                stages_completed=tuple(stages_completed),
                validation_passed=validation_passed,
                execution_id=execution_id,
                extra=result.diagnostics.extra if result.diagnostics else MappingProxyType({}),
            ),
        )

        self._log_run_outcome(result)
        return result

    def _finalize_result(
        self,
        *,
        result: EngineResult,
        context: EngineContext,
        execution_id: str,
        started_at: datetime,
        completed_at: datetime,
        duration_ms: float,
    ) -> EngineResult:
        """Merge execution timing and identity into a result from :meth:`evaluate`.

        Args:
            result: Raw result returned by the concrete engine.
            context: Input context for the run.
            execution_id: Unique identifier for this invocation.
            started_at: UTC timestamp when the run started.
            completed_at: UTC timestamp when the run ended.
            duration_ms: Measured execution duration in milliseconds.

        Returns:
            Result with populated metadata fields.
        """
        metadata = _build_result_metadata(
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            correlation_id=context.correlation_id,
            execution_id=execution_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )
        return replace(result, metadata=metadata)

    def _build_rejected_result(
        self,
        *,
        context: EngineContext,
        errors: tuple[EngineErrorRecord, ...],
        execution_id: str,
        started_at: datetime,
        stages_completed: tuple[EngineExecutionStage, ...],
        validation_passed: bool,
    ) -> EngineResult:
        """Build a rejected result for invalid input or validation failures.

        Args:
            context: Input context that failed validation.
            errors: Structured validation errors.
            execution_id: Unique identifier for this invocation.
            started_at: UTC timestamp when the run started.
            stages_completed: Lifecycle stages reached before rejection.
            validation_passed: Whether base context validation succeeded.

        Returns:
            Immutable rejected :class:`EngineResult`.
        """
        completed_at = _utc_now()
        duration_ms = (completed_at - started_at).total_seconds() * 1000.0
        correlation_id = (
            context.correlation_id
            if isinstance(context, EngineContext)
            else "unknown"
        )
        metadata = _build_result_metadata(
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            correlation_id=correlation_id,
            execution_id=execution_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )
        diagnostics = EngineDiagnostics(
            stages_completed=stages_completed,
            validation_passed=validation_passed,
            execution_id=execution_id,
        )
        return EngineResult(
            status=EngineStatus.REJECTED,
            metadata=metadata,
            payload=None,
            errors=errors,
            diagnostics=diagnostics,
        )

    def _build_failed_result(
        self,
        *,
        context: EngineContext,
        errors: tuple[EngineErrorRecord, ...],
        execution_id: str,
        started_at: datetime,
        completed_at: datetime,
        duration_ms: float,
        stages_completed: tuple[EngineExecutionStage, ...],
        validation_passed: bool,
    ) -> EngineResult:
        """Build a failed result for execution or result-validation failures.

        Args:
            context: Input context for the failed run.
            errors: Structured execution errors.
            execution_id: Unique identifier for this invocation.
            started_at: UTC timestamp when the run started.
            completed_at: UTC timestamp when the run ended.
            duration_ms: Measured execution duration in milliseconds.
            stages_completed: Lifecycle stages reached before failure.
            validation_passed: Whether base context validation succeeded.

        Returns:
            Immutable failed :class:`EngineResult`.
        """
        metadata = _build_result_metadata(
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            correlation_id=context.correlation_id,
            execution_id=execution_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )
        diagnostics = EngineDiagnostics(
            stages_completed=stages_completed,
            validation_passed=validation_passed,
            execution_id=execution_id,
        )
        return EngineResult(
            status=EngineStatus.FAILED,
            metadata=metadata,
            payload=None,
            errors=errors,
            diagnostics=diagnostics,
        )

    def _log_event(self, event: str, level: int, **fields: object) -> None:
        """Emit a structured lifecycle log record.

        Args:
            event: Stable event name (e.g. ``engine.run.start``).
            level: Standard library logging level.
            **fields: Additional structured fields attached via ``extra``.
        """
        extra = {"event": event, **fields}
        self.logger.log(level, event, extra=extra)

    def _log_run_outcome(
        self,
        result: EngineResult,
        *,
        exception: EngineExecutionError | None = None,
    ) -> None:
        """Log the final outcome of an engine run.

        Args:
            result: Final engine result being returned.
            exception: Optional wrapped exception for failed runs.
        """
        common_fields = {
            "engine_name": self.engine_name,
            "correlation_id": result.metadata.correlation_id,
            "execution_id": result.metadata.execution_id,
            "status": result.status.value,
            "duration_ms": result.metadata.duration_ms,
            "warning_count": len(result.warnings),
        }

        if result.status in (EngineStatus.SUCCESS, EngineStatus.PARTIAL):
            self._log_event("engine.run.success", logging.INFO, **common_fields)
            return

        error_codes = tuple(error.code for error in result.errors)
        self._log_event(
            "engine.run.failed",
            logging.ERROR,
            error_codes=error_codes,
            **common_fields,
        )
        if exception is not None and exception.traceback_str:
            self.logger.debug(
                "engine.run.failed.traceback",
                extra={
                    "event": "engine.run.failed.traceback",
                    "engine_name": self.engine_name,
                    "traceback": exception.traceback_str,
                },
            )


__all__ = [
    "DEFAULT_ENGINE_VERSION",
    "ERROR_CODE_CONTEXT_INVALID",
    "ERROR_CODE_CONTEXT_MISSING_FIELD",
    "ERROR_CODE_EXECUTION_UNHANDLED",
    "ERROR_CODE_RESULT_INVALID",
    "BaseEngine",
    "EngineConfigurationError",
    "EngineContext",
    "EngineDiagnostics",
    "EngineError",
    "EngineErrorRecord",
    "EngineExecutionError",
    "EngineExecutionStage",
    "EngineMetadata",
    "EngineResult",
    "EngineSeverity",
    "EngineStatus",
    "EngineValidationError",
    "EngineWarningRecord",
]
