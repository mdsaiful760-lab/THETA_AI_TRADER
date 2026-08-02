"""Immutable input model for a single engine run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Mapping, Optional


@dataclass(frozen=True)
class EngineContext:
    """Immutable input to one engine run.

    Attributes:
        correlation_id: Ties engine output to a pipeline run or request.
        as_of: Timezone-aware point-in-time for the decision (market timestamp).
        payload: Domain-specific immutable inputs for the concrete engine.
        tags: Optional key-value labels for filtering and logging.
        source: Optional caller identity (orchestrator name, test harness, etc.).
    """

    correlation_id: str
    as_of: datetime
    payload: object
    tags: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    source: Optional[str] = None
