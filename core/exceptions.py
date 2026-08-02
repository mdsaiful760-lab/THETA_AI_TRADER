"""Exception hierarchy for the THETA AI TRADER engine foundation."""

from __future__ import annotations

import traceback
from typing import Optional

from core.constants import ERROR_CODE_CONTEXT_INVALID, ERROR_CODE_EXECUTION_UNHANDLED
from core.enums import EngineExecutionStage


class EngineError(Exception):
    """Root of all engine-layer exceptions.

    Attributes:
        engine_name: Name of the engine where the error originated.
        stage: Lifecycle stage active when the error was raised.
        cause: Original exception, when this error wraps another failure.
        traceback_str: Formatted traceback of the original failure, when available.
    """

    def __init__(
        self,
        message: str,
        *,
        engine_name: Optional[str] = None,
        stage: Optional[EngineExecutionStage] = None,
        cause: BaseException | None = None,
    ) -> None:
        """Initialize an engine error with structured context.

        Args:
            message: Human-readable error description.
            engine_name: Stable engine identifier, when known.
            stage: Lifecycle stage where the failure occurred.
            cause: Underlying exception being wrapped, if any.
        """
        super().__init__(message)
        self.engine_name = engine_name
        self.stage = stage
        self.cause = cause
        self.traceback_str: Optional[str] = None
        if cause is not None:
            self.traceback_str = "".join(
                traceback.format_exception(
                    type(cause), cause, cause.__traceback__
                )
            )


class EngineValidationError(EngineError):
    """Raised when input or context validation fails before computation."""

    def __init__(
        self,
        message: str,
        *,
        code: str = ERROR_CODE_CONTEXT_INVALID,
        field: Optional[str] = None,
        engine_name: Optional[str] = None,
        stage: EngineExecutionStage = EngineExecutionStage.VALIDATION,
        cause: BaseException | None = None,
    ) -> None:
        """Initialize a validation error with a stable error code.

        Args:
            message: Human-readable validation failure description.
            code: Machine-readable error code for structured results.
            field: Optional dotted path to the invalid field.
            engine_name: Stable engine identifier, when known.
            stage: Lifecycle stage; defaults to validation.
            cause: Underlying exception, if any.
        """
        super().__init__(
            message,
            engine_name=engine_name,
            stage=stage,
            cause=cause,
        )
        self.code = code
        self.field = field


class EngineConfigurationError(EngineError):
    """Raised when static engine configuration is invalid at construction."""

    def __init__(
        self,
        message: str,
        *,
        engine_name: Optional[str] = None,
        cause: BaseException | None = None,
    ) -> None:
        """Initialize a configuration error.

        Args:
            message: Human-readable configuration failure description.
            engine_name: Stable engine identifier, when known.
            cause: Underlying exception, if any.
        """
        super().__init__(
            message,
            engine_name=engine_name,
            stage=EngineExecutionStage.CONFIGURATION,
            cause=cause,
        )


class EngineExecutionError(EngineError):
    """Raised when computation fails after validation passes."""

    def __init__(
        self,
        message: str,
        *,
        code: str = ERROR_CODE_EXECUTION_UNHANDLED,
        engine_name: Optional[str] = None,
        stage: EngineExecutionStage = EngineExecutionStage.EXECUTE,
        cause: BaseException | None = None,
    ) -> None:
        """Initialize an execution error with a stable error code.

        Args:
            message: Human-readable execution failure description.
            code: Machine-readable error code for structured results.
            engine_name: Stable engine identifier, when known.
            stage: Lifecycle stage; defaults to execute.
            cause: Underlying exception, if any.
        """
        super().__init__(
            message,
            engine_name=engine_name,
            stage=stage,
            cause=cause,
        )
        self.code = code
