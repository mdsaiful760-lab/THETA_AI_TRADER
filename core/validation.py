"""Shared validation helpers for the THETA AI TRADER engine foundation."""

from __future__ import annotations

from typing import Mapping

from core.constants import ERROR_CODE_CONTEXT_MISSING_FIELD
from core.engine_result import EngineErrorRecord
from core.enums import EngineSeverity
from core.exceptions import EngineExecutionError, EngineValidationError


def _validate_required_fields(
    values: Mapping[str, object],
    required: tuple[str, ...],
    *,
    code: str = ERROR_CODE_CONTEXT_MISSING_FIELD,
) -> None:
    """Validate presence and non-emptiness of required string fields.

    Args:
        values: Mapping of field names to values under validation.
        required: Field names that must be present and non-empty strings.
        code: Error code used when a required field is missing or empty.

    Raises:
        EngineValidationError: If any required field is missing or empty.
    """
    for field_name in required:
        if field_name not in values:
            raise EngineValidationError(
                f"Required field '{field_name}' is missing.",
                code=code,
                field=field_name,
            )
        value = values[field_name]
        if value is None:
            raise EngineValidationError(
                f"Required field '{field_name}' must not be None.",
                code=code,
                field=field_name,
            )
        if isinstance(value, str) and not value.strip():
            raise EngineValidationError(
                f"Required field '{field_name}' must not be empty.",
                code=code,
                field=field_name,
            )


def _validation_error_to_record(error: EngineValidationError) -> EngineErrorRecord:
    """Convert a validation exception into a structured error record.

    Args:
        error: Validation exception to convert.

    Returns:
        Immutable error record suitable for an :class:`EngineResult`.
    """
    return EngineErrorRecord(
        code=error.code,
        message=str(error),
        severity=EngineSeverity.ERROR,
        recoverable=False,
        field=error.field,
    )


def _execution_error_to_record(error: EngineExecutionError) -> EngineErrorRecord:
    """Convert an execution exception into a structured error record.

    Args:
        error: Execution exception to convert.

    Returns:
        Immutable error record suitable for an :class:`EngineResult`.
    """
    return EngineErrorRecord(
        code=error.code,
        message=str(error),
        severity=EngineSeverity.ERROR,
        recoverable=False,
    )
