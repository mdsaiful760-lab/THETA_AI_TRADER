"""Enumerations for engine execution outcomes and diagnostics."""

from enum import Enum


class EngineStatus(Enum):
    """Outcome of a single engine execution."""

    SUCCESS = "success"
    PARTIAL = "partial"
    REJECTED = "rejected"
    FAILED = "failed"


class EngineSeverity(Enum):
    """Severity level for structured error and warning records."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EngineExecutionStage(Enum):
    """Stage of the engine lifecycle where an event or failure occurred."""

    CONFIGURATION = "configuration"
    VALIDATION = "validation"
    BEFORE_EXECUTE = "before_execute"
    EXECUTE = "execute"
    RESULT_VALIDATION = "result_validation"
    AFTER_EXECUTE = "after_execute"
