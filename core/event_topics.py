"""Canonical event topic constants for THETA AI TRADER.

New domain topics should be added here so ``core/event_bus.py`` remains
closed for modification when the platform grows.
"""

from __future__ import annotations


class EventTopics:
    """Namespace of canonical v1 event bus topics.

    Attributes are string constants suitable for publish and subscribe calls.
    """

    MARKET_SNAPSHOT_PUBLISHED: str = "market.snapshot.published"
    MARKET_SNAPSHOT_SKIPPED: str = "market.snapshot.skipped"
    MARKET_SNAPSHOT_FAILED: str = "market.snapshot.failed"

    ENGINE_RUN_STARTED: str = "engine.run.started"
    ENGINE_RUN_COMPLETED: str = "engine.run.completed"
    ENGINE_RUN_REJECTED: str = "engine.run.rejected"
    ENGINE_RUN_FAILED: str = "engine.run.failed"

    PIPELINE_RUN_STARTED: str = "pipeline.run.started"
    PIPELINE_RUN_COMPLETED: str = "pipeline.run.completed"

    SYSTEM_HEALTH: str = "system.health"
    SYSTEM_ERROR: str = "system.error"

    SUBSCRIBER_FAILED: str = "event_bus.subscriber.failed"
